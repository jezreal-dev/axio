"""Read-only live Zetaris check. Never falls back to sample SQLite databases.

Run: python scripts/check_zetaris.py [--catalogs]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.adapters.zetaris import ZetarisAdapter, QueryError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogs", action="store_true", help="Also verify one required Axio table per source")
    args = parser.parse_args()
    settings = get_settings()
    settings.sample_mode = False
    if not settings.zetaris_configured or "api.zetaris.com" in settings.zetaris_rest_url:
        print("NOT CONFIGURED: set the provisioned instance's REST URL, organisation ID and API key in .env. The account portal and template URL are not query endpoints.")
        return 1
    adapter = ZetarisAdapter(settings)
    checks = [("REST query", "SELECT 1 AS ok")]
    if args.catalogs:
        checks += [("billing", "SELECT * FROM {billing}.checkout_events LIMIT 1"), ("telemetry", "SELECT * FROM {telemetry}.api_traces LIMIT 1"), ("ops", "SELECT * FROM {ops}.deployments LIMIT 1")]
    failed = False
    for label, sql in checks:
        try:
            rows = adapter.query(sql, purpose="connection check", limit=1, retries=0)
            if label == "REST query" and (not rows or str(next(iter(rows[0].values()))) != "1"):
                raise ValueError("Unexpected SELECT 1 response")
            print(json.dumps({"check": label, "status": "pass", "backend": adapter.backend, "rows": len(rows)}))
        except (QueryError, ValueError):
            failed = True
            print(json.dumps({"check": label, "status": "fail", "message": "Check instance availability, credentials, organisation ID and catalog mapping; server response omitted to avoid exposing data."}))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
