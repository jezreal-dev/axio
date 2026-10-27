"""NVIDIA Nemotron dual-lane model router (OpenAI-compatible, stdlib only).

Following the Meterless "injected generator" rule, agents never call a provider
directly: they ask this router for a lane (`reasoning` or `fast`) and get back
parsed JSON or `None`. `None` means "no model available / call failed", and every
agent has a deterministic code path for that case. Switching providers is a
change to MODEL_BASE_URL / MODEL_* env vars only.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from app.config import Settings
from app.tracing import Tracer


class ModelRouter:
    LANES = ("reasoning", "fast")

    def __init__(self, settings: Settings, tracer: Tracer | None = None):
        self.settings = settings
        self.tracer = tracer
        self.calls: list[dict[str, Any]] = []
        self._disabled_reason: str | None = None if settings.llm_configured else "no MODEL_API_KEY (deterministic mode)"

    @property
    def available(self) -> bool:
        return self._disabled_reason is None

    def model_for(self, lane: str) -> str:
        return self.settings.model_reasoning if lane == "reasoning" else self.settings.model_fast

    # ------------------------------------------------------------------ core
    def generate(self, lane: str, system: str, user: str, max_tokens: int = 1200, temperature: float = 0.2) -> str | None:
        if not self.available:
            return None
        model = self.model_for(lane)
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        url = self.settings.model_base_url.rstrip("/") + "/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.model_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        t0 = time.monotonic()
        last_err = ""
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=self.settings.model_timeout_s) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                text = payload["choices"][0]["message"].get("content") or ""
                usage = payload.get("usage", {})
                rec = {
                    "lane": lane,
                    "model": model,
                    "latency_ms": int((time.monotonic() - t0) * 1000),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "attempt": attempt + 1,
                }
                self.calls.append(rec)
                return text
            except urllib.error.HTTPError as exc:  # 4xx are not worth retrying except 429
                last_err = f"HTTP {exc.code}"
                if exc.code in (401, 403, 404):
                    self._disabled_reason = f"model provider rejected request ({last_err})"
                    break
            except Exception as exc:  # timeouts, DNS, malformed payloads
                last_err = type(exc).__name__ + ": " + str(exc)[:120]
            time.sleep(1.5 * (attempt + 1))
        self.calls.append({"lane": lane, "model": model, "error": last_err})
        if self.tracer:
            self.tracer.emit(
                "ModelRouter", "model_call_failed", f"lane={lane} model={model}",
                f"{last_err}; agent falls back to deterministic logic", status="fallback",
            )
        return None

    def generate_json(self, lane: str, system: str, user: str, **kw: Any) -> Any | None:
        text = self.generate(lane, system + "\nRespond with a single valid JSON value and nothing else.", user, **kw)
        if text is None:
            return None
        return parse_json_loose(text)


def parse_json_loose(text: str) -> Any | None:
    """Parse JSON from a model reply, tolerating code fences, <think> blocks and noise."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fenced:
        text = fenced.group(1).strip()
    for candidate in (text, _outermost(text, "{", "}"), _outermost(text, "[", "]")):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _outermost(text: str, open_ch: str, close_ch: str) -> str | None:
    start, end = text.find(open_ch), text.rfind(close_ch)
    if start == -1 or end <= start:
        return None
    return text[start : end + 1]
