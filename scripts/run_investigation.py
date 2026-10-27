#!/usr/bin/env python3
"""CLI helper to execute an IncidentWhy investigation from a scenario ID or JSON input file.

Examples:
    python3 scripts/run_investigation.py --scenario scenario_01_deploy_red_herring
    python3 scripts/run_investigation.py --input input_examples/incident_01_checkout_drop.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.orchestrator import run_investigation


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an IncidentWhy multi-agent root-cause investigation")
    parser.add_argument("--scenario", "-s", type=str, default=None, help="Scenario ID (e.g. scenario_01_deploy_red_herring)")
    parser.add_argument("--input", "-i", type=str, default=None, help="Path to JSON input payload file")
    parser.add_argument("--output", "-o", type=str, default=None, help="Optional file path to save the JSON report")
    parser.add_argument("--quiet", action="store_true", help="Suppress live trace stdout printing")
    args = parser.parse_args()

    payload = {}
    if args.input:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if args.scenario:
        payload["scenario_id"] = args.scenario
    if not payload:
        payload["scenario_id"] = "scenario_01_deploy_red_herring"

    result = run_investigation(payload=payload, echo_logs=not args.quiet)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Saved report to {out_path} (root_cause={result['verified_root_cause']['suspected_change_id']})")
    else:
        print(
            json.dumps(
                {
                    "run_id": result["run_id"],
                    "incident_id": result["incident_id"],
                    "status": result["status"],
                    "verified_root_cause": result["verified_root_cause"]["suspected_change_id"],
                    "falsified_hypotheses": [h["suspected_change_id"] for h in result["falsified_hypotheses"]],
                    "deduplicated_lost_revenue_usd": result["financial_impact"]["deduplicated_lost_revenue_usd"],
                    "gross_failed_volume_usd": result["financial_impact"]["gross_failed_volume_usd"],
                    "sql_schema_recoveries": result["collaboration"]["sql_schema_recoveries"],
                    "zetaris_queries_executed": result["sponsor_technology"]["zetaris"]["queries_executed"],
                    "log_file": result["log_file"],
                },
                indent=2,
            )
        )
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
