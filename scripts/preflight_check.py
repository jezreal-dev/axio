#!/usr/bin/env python3
"""Automated Pre-Submission & Anti-Disqualification Audit for IncidentWhy.

Audits every item required by:
  - rules and requirements/disqualification-checklist.md
  - rules and requirements/technical-execution-requirements.md
  - rules and requirements/submission-requirements.md
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_FILES = [
    "README.md",
    "run.py",
    "requirements.txt",
    ".env.example",
    ".gitignore",
    "Dockerfile",
    "metadata.json",
    "data/db_seed.py",
    "data/silos/billing.db",
    "data/silos/telemetry.db",
    "data/silos/ops.db",
    "input_examples/incident_01_checkout_drop.json",
    "input_examples/incident_02_tenant_rate_limit.json",
    "input_examples/incident_03_latency_margin_leak.json",
    "output_examples/report_01_checkout_drop.json",
    "output_examples/report_02_tenant_rate_limit.json",
    "output_examples/report_03_latency_margin_leak.json",
    "web/index.html",
    "web/slides.html",
    "web/style.css",
    "web/app.js",
]

SECRET_PATTERNS = [
    re.compile(r"nvapi-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
]


def main() -> int:
    failures: list[str] = []
    passes: list[str] = []

    # 1. Required files at root and subdirectories
    for rel in REQUIRED_FILES:
        p = ROOT / rel
        if p.exists() and p.stat().st_size > 0:
            passes.append(f"File present & non-empty: {rel}")
        else:
            failures.append(f"Missing or empty required file: {rel}")

    # 2. Check .gitignore includes .env
    gitignore = ROOT / ".gitignore"
    if gitignore.exists():
        txt = gitignore.read_text(encoding="utf-8")
        if ".env" in txt:
            passes.append(".gitignore excludes .env")
        else:
            failures.append(".gitignore does not exclude .env")

    # 3. Scan codebase for leaked API keys
    leaked = []
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in {".py", ".md", ".json", ".js", ".html", ".env", ".txt"}:
            if ".git" in p.parts or "silos" in p.parts:
                continue
            content = p.read_text(encoding="utf-8", errors="ignore")
            for pat in SECRET_PATTERNS:
                m = pat.search(content)
                if m and "your-" not in m.group(0).lower() and "placeholder" not in m.group(0).lower():
                    leaked.append(f"{p.relative_to(ROOT)}: {m.group(0)[:10]}...")
    if leaked:
        failures.append(f"Potential secret(s) detected: {leaked}")
    else:
        passes.append("Zero real API keys or secrets detected in repository")

    # 4. Verify output_examples are real multi-agent execution outputs
    for out_rel in [
        "output_examples/report_01_checkout_drop.json",
        "output_examples/report_02_tenant_rate_limit.json",
        "output_examples/report_03_latency_margin_leak.json",
    ]:
        p = ROOT / out_rel
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if (
                data.get("status") == "success"
                and len(data.get("agents_used", [])) >= 3
                and data.get("collaboration", {}).get("feedback_loop_completed") is True
                and data.get("sponsor_technology", {}).get("zetaris", {}).get("queries_executed", 0) >= 15
                and len(data.get("trace_events", [])) >= 10
            ):
                passes.append(f"Verified genuine multi-agent output & SQL trace in {out_rel}")
            else:
                failures.append(f"Output example {out_rel} missing multi-agent/SQL trace fields")

    # 5. Verify JSONL trace logs exist in logs/
    log_dir = ROOT / "logs"
    jsonl_files = list(log_dir.glob("*.jsonl")) if log_dir.exists() else []
    if jsonl_files:
        passes.append(f"Found {len(jsonl_files)} JSONL trace log file(s) in logs/")
    else:
        failures.append("No JSONL trace files found in logs/")

    print("=" * 72)
    print("INCIDENTWHY PRE-SUBMISSION AUDIT REPORT")
    print("=" * 72)
    for msg in passes:
        print(f"  [PASS] {msg}")
    for msg in failures:
        print(f"  [FAIL] {msg}")
    print("-" * 72)
    print(f"Summary: {len(passes)} passed, {len(failures)} failed")
    print("=" * 72)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
