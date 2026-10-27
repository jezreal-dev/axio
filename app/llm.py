"""Nightshift-primary model router (OpenAI-compatible, stdlib only).

Following the Meterless "injected generator" rule, agents never call a provider
directly: they ask this router for a lane (`reasoning` or `fast`) and get back
parsed JSON or `None`. `None` means "no model available / call failed", and every
agent has a deterministic code path for that case. Switching providers is a
change to MODEL_PROVIDER and its environment settings. Nightshift chooses the
concrete model behind its configured routing mode.
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
        self._disabled_reason: str | None = None if settings.llm_configured else "provider token missing or LLM disabled (deterministic mode)"

    @property
    def available(self) -> bool:
        return self._disabled_reason is None

    def model_for(self, lane: str) -> str:
        if self.settings.model_provider == "meterless":
            return self.settings.meterless_model
        return self.settings.model_reasoning if lane == "reasoning" else self.settings.model_fast

    def headers(self, chat: bool = False) -> dict:
        headers = {"Authorization": f"Bearer {self.settings.provider_token}", "Content-Type": "application/json", "Accept": "application/json"}
        if self.settings.model_provider == "meterless":
            headers["meterless-app"] = "axio"
            if chat:
                headers["meterless-scope"] = "user"
        return headers

    def connection_status(self, base_url: str | None = None) -> dict:
        """Discovery verifies authentication, but does not claim a model has answered."""
        url = base_url or self.settings.provider_url
        try:
            req = urllib.request.Request(url + "/models", headers=self.headers())
            with urllib.request.urlopen(req, timeout=2) as response:
                payload = json.load(response)
            models = [item["id"] for item in payload.get("data", []) if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].startswith("meterless/")]
            return {"status": "online" if models else "offline", "base_url": url, "models": models}
        except urllib.error.HTTPError as exc:
            return {"status": "unauthorized" if exc.code in (401, 403) else "offline", "base_url": url, "models": []}
        except Exception:
            return {"status": "offline", "base_url": url, "models": []}

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
        url = self.settings.provider_url + "/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=self.headers(chat=True),
            method="POST",
        )
        t0 = time.monotonic()
        last_err = ""
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=self.settings.meterless_timeout_s if self.settings.model_provider == "meterless" else self.settings.model_timeout_s) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                text = payload["choices"][0]["message"].get("content") or ""
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("Provider returned no text")
                usage = payload.get("usage", {})
                rec = {
                    "lane": lane,
                    "model": payload.get("model", model),
                    "requested_model": model,
                    "provider": self.settings.model_provider,
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
                last_err = type(exc).__name__
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
