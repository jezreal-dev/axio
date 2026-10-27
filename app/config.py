"""Runtime configuration.

All credentials come from environment variables (optionally loaded from a local
`.env` file, which is git-ignored). Nothing secret is ever hardcoded or logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (stdlib only). Existing env vars always win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(ROOT_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _is_placeholder(value: str) -> bool:
    v = (value or "").strip().lower()
    return (not v) or v.startswith("your-") or v.endswith("-here") or v in ("changeme", "xxx")


@dataclass
class Settings:
    model_provider: str = field(default_factory=lambda: os.getenv("MODEL_PROVIDER", "meterless"))
    meterless_base_url: str = field(default_factory=lambda: os.getenv("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1"))
    meterless_runtime_token: str = field(default_factory=lambda: os.getenv("METERLESS_RUNTIME_TOKEN", ""))
    meterless_model: str = field(default_factory=lambda: os.getenv("METERLESS_MODEL", "meterless/auto"))
    meterless_timeout_s: float = 180
    # --- Model provider: NVIDIA Nemotron via the OpenAI-compatible API catalog ---
    model_api_key: str = field(default_factory=lambda: os.getenv("MODEL_API_KEY", ""))
    model_base_url: str = field(
        default_factory=lambda: os.getenv("MODEL_BASE_URL", "https://integrate.api.nvidia.com/v1")
    )
    # Dual-lane routing: a deep reasoning lane and a high-throughput lane.
    model_reasoning: str = field(
        default_factory=lambda: os.getenv("MODEL_REASONING", "nvidia/llama-3.3-nemotron-super-49b-v1")
    )
    model_fast: str = field(
        default_factory=lambda: os.getenv("MODEL_FAST", "nvidia/nvidia-nemotron-nano-9b-v2")
    )
    model_timeout_s: float = field(default_factory=lambda: float(os.getenv("MODEL_TIMEOUT_S", "60")))

    # --- Zetaris federated data harness ---
    zetaris_rest_url: str = field(default_factory=lambda: os.getenv("ZETARIS_REST_URL", ""))
    zetaris_org_id: str = field(default_factory=lambda: os.getenv("ZETARIS_ORG_ID", "1"))
    zetaris_api_key: str = field(default_factory=lambda: os.getenv("ZETARIS_API_KEY", ""))
    zetaris_timeout_s: float = field(default_factory=lambda: float(os.getenv("ZETARIS_TIMEOUT_S", "30")))
    # Fully-qualified names of the three silos as registered in Zetaris
    # (e.g. "pg_billing.public"). Used to resolve {billing}/{telemetry}/{ops}.
    zetaris_catalog_billing: str = field(default_factory=lambda: os.getenv("ZETARIS_CATALOG_BILLING", "billing"))
    zetaris_catalog_telemetry: str = field(
        default_factory=lambda: os.getenv("ZETARIS_CATALOG_TELEMETRY", "telemetry")
    )
    zetaris_catalog_ops: str = field(default_factory=lambda: os.getenv("ZETARIS_CATALOG_OPS", "ops"))

    # --- Runtime ---
    sample_mode: bool = field(default_factory=lambda: _bool("SAMPLE_MODE", True))
    use_llm: bool = field(default_factory=lambda: _bool("USE_LLM", True))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    max_rounds: int = field(default_factory=lambda: int(os.getenv("MAX_ROUNDS", "4")))
    batch_size: int = field(default_factory=lambda: int(os.getenv("HYPOTHESIS_BATCH_SIZE", "2")))

    # --- Paths ---
    silo_dir: Path = field(default_factory=lambda: Path(os.getenv("SILO_DIR", str(ROOT_DIR / "data" / "silos"))))
    state_dir: Path = field(default_factory=lambda: Path(os.getenv("STATE_DIR", str(ROOT_DIR / "state"))))
    log_dir: Path = field(default_factory=lambda: Path(os.getenv("LOG_DIR", str(ROOT_DIR / "logs"))))

    @property
    def llm_configured(self) -> bool:
        return self.use_llm and not _is_placeholder(self.provider_token) and self.provider_token != "rt-local"

    @property
    def provider_url(self) -> str:
        return normalize_runtime_url(self.meterless_base_url)[0] if self.model_provider == "meterless" else self.model_base_url.rstrip("/")

    @property
    def provider_token(self) -> str:
        if self.model_provider == "meterless":
            return normalize_runtime_url(self.meterless_base_url)[1] or self.meterless_runtime_token
        return self.model_api_key

    @property
    def zetaris_configured(self) -> bool:
        return not _is_placeholder(self.zetaris_api_key) and bool(self.zetaris_rest_url.strip())

    def public_view(self) -> dict:
        """Configuration safe to print or return from /health (secrets masked)."""
        return {
            "sample_mode": self.sample_mode,
            "llm_configured": self.llm_configured,
            "model_provider": self.model_provider,
            "model_base_url": self.provider_url,
            "meterless_model": self.meterless_model,
            "model_reasoning": self.model_reasoning,
            "model_fast": self.model_fast,
            "model_api_key": "configured" if self.llm_configured else None,
            "zetaris_configured": self.zetaris_configured,
            "zetaris_rest_url": self.zetaris_rest_url or None,
            "zetaris_api_key": mask(self.zetaris_api_key),
            "max_rounds": self.max_rounds,
            "batch_size": self.batch_size,
        }

    def masked_summary(self) -> dict:
        return self.public_view()


def mask(secret: str) -> str | None:
    if _is_placeholder(secret):
        return None
    return secret[:4] + "...redacted"


def get_settings() -> Settings:
    return Settings()


def normalize_runtime_url(value: str) -> tuple[str, str]:
    """Accept console URLs/connect links without exposing fragments or query secrets."""
    value = value.strip()
    parsed = urlsplit(value if "://" in value else "http://" + value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Invalid Meterless Runtime URL")
    token = parse_qs(parsed.fragment).get("k", [""])[0]
    return f"{parsed.scheme}://{parsed.netloc}/v1", token
