"""Run from the repository root: python scripts/check_nightshift.py [--detect] [--chat]."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.llm import ModelRouter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--detect", action="store_true")
    parser.add_argument("--chat", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    settings.model_provider = "meterless"
    router = ModelRouter(settings)
    result = router.connection_status()
    if args.detect and result["status"] == "offline":
        for host in ("127.0.0.1", "localhost", "[::1]"):
            for port in (8088, 8090, 8787, 11434):
                result = router.connection_status(f"http://{host}:{port}/v1")
                if result["status"] in ("online", "unauthorized"):
                    break
            else:
                continue
            break
        if result["status"] in ("online", "unauthorized"):
            settings.meterless_base_url = result["base_url"]
            path = Path(__file__).resolve().parents[1] / ".env"
            lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
            lines = [line for line in lines if not line.startswith("METERLESS_BASE_URL=")]
            path.write_text("\n".join(lines + ["METERLESS_BASE_URL=" + result["base_url"]]) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    if result["status"] != "online":
        return 1
    if args.chat:
        settings.use_llm = True
        router = ModelRouter(settings)
        answer = router.generate("fast", "You are testing a connection.", "Reply with: Axio connection works.", max_tokens=100)
        print(json.dumps({"text_received": bool(answer), "calls": router.calls}, indent=2))
        return 0 if answer else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
