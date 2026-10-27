#!/usr/bin/env python3
"""Programmatic & Interactive Entry Point for IncidentWhy.

Supports:
  1. HTTP API & War-Room UI Server (default):
       python3 run.py
     Binds to 0.0.0.0:8000 (configurable via HOST / PORT) and exposes:
       - POST /run                      (Judge programmatic execution endpoint)
       - POST /api/investigate          (Synchronous investigation endpoint)
       - POST /api/investigate/async    (Asynchronous investigation with live SSE stream)
       - GET  /api/stream/<run_id>      (Server-Sent Events live JSONL trace stream)
       - GET  /api/runs/<run_id>        (Poll status/result of an async run)
       - GET  /api/scenarios            (List the 3 seeded red-herring scenarios)
       - GET  /api/world-model          (Inspect Meterless World Model state)
       - GET  /api/memory               (Inspect Meterless H-MEM & Trust Ledger state)
       - GET  /health                   (Health check across Zetaris, Meterless & Nemotron)
       - GET  /                         (3-Panel War-Room Investigation Dashboard)

  2. CLI Investigation Runner:
       python3 run.py --input input_examples/incident_01_checkout_drop.json
       python3 run.py --scenario scenario_01_deploy_red_herring

  3. Deterministic Database Seeder:
       python3 run.py seed
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import queue
import sys
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.adapters.zetaris import ZetarisAdapter
from app.agents.orchestrator import ACTIVE_RUNS, run_investigation
from app.config import Settings, get_settings
from app.llm import ModelRouter
from app.models import SCENARIO_PRESETS
from app.sample_data import generate as seed_silos
from app.tracing import Tracer

ROOT_DIR = Path(__file__).resolve().parent
WEB_DIR = ROOT_DIR / "web"
SCENARIOS_DIR = ROOT_DIR / "data" / "scenarios"


def _load_scenarios() -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    if SCENARIOS_DIR.exists():
        for p in sorted(SCENARIOS_DIR.glob("*.json")):
            try:
                scenarios.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
    if not scenarios:
        for key, preset in SCENARIO_PRESETS.items():
            scenarios.append({"scenario_id": key, **preset})
    return scenarios


def _read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def build_health_payload(settings: Settings) -> dict[str, Any]:
    seed_silos(settings.silo_dir, force=False)
    zetaris = ZetarisAdapter(settings=settings)
    wm = _read_json_file(settings.state_dir / "meterless_world_model.json", {})
    hmem = _read_json_file(settings.state_dir / "meterless_hmem.json", {})
    return {
        "status": "ok",
        "service": "Axio (IncidentWhy Engine) — Cross-Silo Root-Cause & Silent Revenue Leak Investigator",
        "track": "Track 2: The Agent That Can Explain Why",
        "team": "Agentic Army (Jezreal Momoh & Victor Okpe)",
        "config": settings.masked_summary(),
        "nightshift": ModelRouter(settings).connection_status() if settings.model_provider == "meterless" else {"status": "not_selected"},
        "zetaris": zetaris.health(),
        "meterless": {
            "world_model_entities": len(wm.get("entities", {})),
            "world_model_events": len(wm.get("events", [])),
            "hmem_memories": len(hmem.get("memories", [])),
            "hmem_trust_profiles": len(hmem.get("trust_profiles", {})),
        },
        "scenarios_available": [s.get("scenario_id") for s in _load_scenarios()],
    }


class IncidentWhyHTTPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep stdout clean for structured JSONL agent trace logs
        pass

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Org-ID, X-Request-ID")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, rel_path: str) -> None:
        clean = rel_path.lstrip("/") or "index.html"
        target = (WEB_DIR / clean).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            self._send_json({"error": "Not found", "path": rel_path}, status=HTTPStatus.NOT_FOUND)
            return
        ctype, _ = mimetypes.guess_type(str(target))
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", (ctype or "text/html") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _read_body_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace").strip()
        if not raw:
            return {}
        return json.loads(raw)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Org-ID, X-Request-ID")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        settings = get_settings()

        if path in ("/health", "/api/health"):
            self._send_json(build_health_payload(settings))
            return

        if path == "/api/scenarios":
            self._send_json({"scenarios": _load_scenarios()})
            return

        if path == "/api/world-model":
            wm = _read_json_file(settings.state_dir / "meterless_world_model.json", {"entities": {}, "relations": [], "events": []})
            self._send_json({"engine": "Meterless World Model Agent Engine", "state": wm})
            return

        if path == "/api/memory":
            hmem = _read_json_file(settings.state_dir / "meterless_hmem.json", {"memories": [], "trust_profiles": {}})
            self._send_json({"engine": "Meterless H-MEM Agent Engine", "state": hmem})
            return

        if path.startswith("/api/runs/"):
            run_id = path.split("/api/runs/", 1)[1].strip("/")
            info = ACTIVE_RUNS.get(run_id)
            if not info:
                self._send_json({"error": f"Unknown run_id '{run_id}'"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({
                "run_id": run_id,
                "status": info.get("status"),
                "result": info.get("result"),
            })
            return

        if path.startswith("/api/stream/"):
            run_id = path.split("/api/stream/", 1)[1].strip("/")
            self._handle_sse_stream(run_id)
            return

        if path in ("/slides", "/slides.html"):
            self._serve_static("slides.html")
            return

        if path in ("/", "/index.html", "/style.css", "/app.js", "/favicon.ico"):
            if path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._serve_static("index.html" if path == "/" else path)
            return

        self._send_json({"error": "Endpoint not found", "path": path}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        settings = get_settings()

        try:
            payload = self._read_body_json()
        except Exception as exc:
            self._send_json(
                {
                    "status": "error",
                    "error": {"type": "InvalidJSON", "message": f"Malformed JSON request body: {exc}", "recoverable": True},
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        if path in ("/run", "/api/investigate"):
            result = run_investigation(payload=payload, settings=settings, echo_logs=True)
            status_code = HTTPStatus.OK if result.get("status") == "success" else HTTPStatus.INTERNAL_SERVER_ERROR
            self._send_json(result, status=int(status_code))
            return

        if path == "/api/investigate/async":
            run_id = str(
                payload.get("run_id")
                or payload.get("request_id")
                or f"run-{uuid.uuid4().hex[:8]}"
            )
            payload["run_id"] = run_id
            tracer = Tracer(run_id=run_id, log_dir=settings.log_dir, echo=True)
            ACTIVE_RUNS[run_id] = {"status": "running", "tracer": tracer, "result": None}

            def _worker() -> None:
                run_investigation(payload=payload, settings=settings, tracer=tracer, echo_logs=True)

            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            self._send_json(
                {
                    "run_id": run_id,
                    "status": "running",
                    "stream_url": f"/api/stream/{run_id}",
                    "result_url": f"/api/runs/{run_id}",
                },
                status=HTTPStatus.ACCEPTED,
            )
            return

        self._send_json({"error": "Endpoint not found", "path": path}, status=HTTPStatus.NOT_FOUND)

    def _handle_sse_stream(self, run_id: str) -> None:
        info = ACTIVE_RUNS.get(run_id)
        if not info or not info.get("tracer"):
            self._send_json({"error": f"Unknown run_id '{run_id}'"}, status=HTTPStatus.NOT_FOUND)
            return

        tracer: Tracer = info["tracer"]
        q = tracer.subscribe()

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            while True:
                try:
                    ev = q.get(timeout=15.0)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    if ACTIVE_RUNS.get(run_id, {}).get("status") in ("completed", "error"):
                        break
                    continue

                if ev is None:
                    final_res = ACTIVE_RUNS.get(run_id, {}).get("result")
                    done_data = json.dumps({"run_id": run_id, "status": ACTIVE_RUNS.get(run_id, {}).get("status"), "result": final_res})
                    self.wfile.write(f"event: done\ndata: {done_data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    break

                data_str = json.dumps(ev, ensure_ascii=False)
                self.wfile.write(f"event: trace\ndata: {data_str}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def start_server(host: str | None = None, port: int | None = None) -> None:
    settings = get_settings()
    bind_host = host or settings.host
    bind_port = port or settings.port
    seed_silos(settings.silo_dir, force=False)
    server = ThreadingHTTPServer((bind_host, bind_port), IncidentWhyHTTPHandler)
    print(
        f"[IncidentWhy] Server listening on http://{bind_host}:{bind_port} "
        f"(SAMPLE_MODE={settings.sample_mode}, reasoning_model={settings.model_reasoning})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[IncidentWhy] Shutting down server.", flush=True)
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "seed":
        settings = get_settings()
        summary = seed_silos(settings.silo_dir, force="--force" in args_list)
        print(json.dumps(summary, indent=2))
        return 0

    parser = argparse.ArgumentParser(description="IncidentWhy Multi-Agent Root-Cause Investigator")
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "run", "seed"], help="Execution mode")
    parser.add_argument("--input", "-i", type=str, help="Path to JSON input payload file")
    parser.add_argument("--scenario", "-s", type=str, help="Scenario ID to execute directly")
    parser.add_argument("--output", "-o", type=str, help="Optional file path to write JSON output")
    parser.add_argument("--host", type=str, default=None, help="HTTP server bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="HTTP server bind port (default: 8000)")
    parser.add_argument("--quiet", action="store_true", help="Suppress stdout trace echoing during CLI run")
    parsed = parser.parse_args(args_list)

    settings = get_settings()

    if parsed.command == "seed":
        summary = seed_silos(settings.silo_dir, force=True)
        print(json.dumps(summary, indent=2))
        return 0

    if parsed.input or parsed.scenario or parsed.command == "run":
        payload: dict[str, Any] = {}
        if parsed.input:
            payload = json.loads(Path(parsed.input).read_text(encoding="utf-8"))
        if parsed.scenario:
            payload["scenario_id"] = parsed.scenario
        result = run_investigation(payload=payload, settings=settings, echo_logs=not parsed.quiet)
        if parsed.output:
            out_path = Path(parsed.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        else:
            print(json.dumps(result, indent=2))
        return 0 if result.get("status") == "success" else 1

    start_server(host=parsed.host, port=parsed.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
