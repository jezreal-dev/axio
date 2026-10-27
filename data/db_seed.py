#!/usr/bin/env python3
"""Seed the three fragmented SQLite databases (`billing.db`, `telemetry.db`, `ops.db`)
used when SAMPLE_MODE=true or when Zetaris credentials are not configured.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.sample_data import generate  # noqa: E402


def seed_all(force: bool = False) -> dict:
    settings = get_settings()
    result = generate(settings.silo_dir, force=force)
    return result


if __name__ == "__main__":
    force_flag = "--force" in sys.argv
    info = seed_all(force=force_flag)
    print(json.dumps(info, indent=2))
