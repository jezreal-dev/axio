"""Structured JSONL tracing.

Every agent decision, handoff, SQL query, critique, retry and fallback is written
as one JSON object per line to `logs/trace-<run_id>.jsonl`, echoed to stdout in a
readable form, and pushed to live subscribers (the SSE stream used by the UI).
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Tracer:
    def __init__(self, run_id: str, log_dir: Path, echo: bool = True):
        self.run_id = run_id
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / f"trace-{run_id}.jsonl"
        self.aggregate_log_file = self.log_dir / "agent_trace.jsonl"
        self.echo = echo
        self.events: list[dict[str, Any]] = []
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._seq = 0
        self._t0 = time.monotonic()
        self.closed = False
        # Truncate any previous trace with the same run id.
        self.log_file.write_text("", encoding="utf-8")

    # ------------------------------------------------------------------ emit
    def emit(
        self,
        agent: str,
        action: str,
        input_summary: str = "",
        output_summary: str = "",
        target_agent: str | None = None,
        status: str = "success",
        **meta: Any,
    ) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "timestamp": utc_now(),
                "elapsed_ms": int((time.monotonic() - self._t0) * 1000),
                "run_id": self.run_id,
                "agent_name": agent,
                "action": action,
                "input_summary": input_summary,
                "output_summary": output_summary,
                "target_agent": target_agent,
                "status": status,
            }
            if meta:
                event["meta"] = _jsonable(meta)
            self.events.append(event)
            line_json = json.dumps(event, ensure_ascii=False) + "\n"
            with self.log_file.open("a", encoding="utf-8") as fh:
                fh.write(line_json)
            with self.aggregate_log_file.open("a", encoding="utf-8") as fh:
                fh.write(line_json)
            for q in list(self._subscribers):
                q.put(event)
        if self.echo:
            arrow = f" -> {target_agent}" if target_agent else ""
            line = f"[{status.upper():>14}] run={self.run_id} {agent}{arrow} :: {action} :: {output_summary}"
            print(line[:400], file=sys.stdout, flush=True)
        return event

    # ----------------------------------------------------------- subscribers
    def subscribe(self) -> queue.Queue:
        """Return a queue pre-filled with past events, then live ones."""
        q: queue.Queue = queue.Queue()
        with self._lock:
            for ev in self.events:
                q.put(ev)
            if self.closed:
                q.put(None)
            else:
                self._subscribers.append(q)
        return q

    def close(self) -> None:
        with self._lock:
            self.closed = True
            for q in self._subscribers:
                q.put(None)
            self._subscribers.clear()


def _jsonable(obj: Any) -> Any:
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        if isinstance(obj, dict):
            return {str(k): _jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [_jsonable(v) for v in obj]
        return str(obj)
