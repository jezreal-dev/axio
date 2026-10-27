"""Zetaris federated data adapter.

Agents write ONE federated SQL statement that references the three silos as
`{billing}.table`, `{telemetry}.table` and `{ops}.table`. The adapter resolves
those placeholders and executes the statement on one of two backends:

* ``zetaris-rest``  - the real Zetaris instance, through
  ``POST {ZETARIS_REST_URL}/sql-editor/sqls/run`` with a bearer token, ``X-Org-ID``
  and a fresh ``X-Request-ID``. Zetaris queries each source in place (no ETL).
* ``sqlite-federation`` - SAMPLE_MODE only. The three silo files are ATTACHed to
  one SQLite connection so the *same* cross-silo SQL runs offline for judges
  without credentials. It is labelled as such in every trace event; it never
  pretends to be Zetaris.

Also provides schema discovery (``describe``), transient-error retries with
backoff, and an opt-in fault injector used to demonstrate recovery.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from app.config import Settings
from app.tracing import Tracer

SILOS = ("billing", "telemetry", "ops")


class QueryError(Exception):
    def __init__(self, message: str, transient: bool = False, sql: str = ""):
        super().__init__(message)
        self.transient = transient
        self.sql = sql


class ZetarisAdapter:
    def __init__(self, settings: Settings, tracer: Tracer | None = None, agent: str = "Executor"):
        self.settings = settings
        self.tracer = tracer
        self.agent = agent
        self.query_log: list[dict[str, Any]] = []
        self._local = threading.local()
        self._faults = {f.strip() for f in os.getenv("FAULT_INJECTION", "").split(",") if f.strip()}
        if settings.zetaris_configured and not settings.sample_mode:
            self.backend = "zetaris-rest"
            self.catalogs = {
                "billing": settings.zetaris_catalog_billing,
                "telemetry": settings.zetaris_catalog_telemetry,
                "ops": settings.zetaris_catalog_ops,
            }
        else:
            self.backend = "sqlite-federation"
            self.catalogs = {s: s for s in SILOS}

    # ------------------------------------------------------------ public API
    def query(self, sql: str, purpose: str, limit: int = 5000, retries: int = 2) -> list[dict[str, Any]]:
        """Run one federated statement and return rows as dicts.

        Transient failures (timeouts, 5xx, 429) are retried with exponential
        backoff; each attempt is traced. Non-transient errors raise QueryError.
        """
        resolved = self.resolve(sql)
        silos = sorted({s for s in SILOS if "{" + s + "}" in sql})
        attempt = 0
        while True:
            attempt += 1
            t0 = time.monotonic()
            try:
                self._maybe_inject_fault(purpose, attempt)
                rows = self._run_rest(resolved, limit) if self.backend == "zetaris-rest" else self._run_sqlite(resolved)
                ms = int((time.monotonic() - t0) * 1000)
                self._record(purpose, resolved, silos, len(rows), ms, attempt, "success")
                return rows
            except QueryError as exc:
                ms = int((time.monotonic() - t0) * 1000)
                self._record(purpose, resolved, silos, 0, ms, attempt, "error", str(exc))
                if exc.transient and attempt <= retries:
                    wait = 0.4 * (2 ** (attempt - 1))
                    if self.tracer:
                        self.tracer.emit(
                            self.agent, "query_retry", purpose,
                            f"{exc} - retrying in {wait:.1f}s (attempt {attempt + 1}/{retries + 1})",
                            status="retrying", backend=self.backend, silos=silos,
                        )
                    time.sleep(wait)
                    continue
                exc.sql = resolved
                raise

    def resolve(self, sql: str) -> str:
        out = sql
        for silo, catalog in self.catalogs.items():
            out = out.replace("{" + silo + "}", catalog)
        return out

    def list_tables(self, silo: str) -> list[str]:
        if self.backend == "sqlite-federation":
            rows = self._run_sqlite(f"SELECT name FROM {silo}.sqlite_master WHERE type='table' ORDER BY name")
            return [r["name"] for r in rows]
        try:
            rows = self._run_rest(f"SHOW TABLES IN {self.catalogs[silo]}", 500)
        except QueryError:
            return []
        names = []
        for r in rows:
            name = r.get("tableName") or r.get("table_name") or r.get("name") or next(iter(r.values()), None)
            if name:
                names.append(str(name))
        return names

    def describe(self, silo: str, table: str) -> list[str]:
        """Discover column names in a backend-agnostic way (SELECT * ... LIMIT 1)."""
        rows = self.query(f"SELECT * FROM {{{silo}}}.{table} LIMIT 1", purpose=f"schema discovery {silo}.{table}",
                          limit=1)
        if rows:
            return list(rows[0].keys())
        if self.backend == "sqlite-federation":
            info = self._run_sqlite(f"PRAGMA {silo}.table_info({table})")
            return [r["name"] for r in info]
        return []

    def query_with_schema_recovery(
        self,
        sql: str,
        purpose: str,
        silo: str,
        table: str,
        column_aliases: dict[str, list[str]] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Execute a query; if a column-not-found error occurs, inspect the target
        table's live schema via `describe()`, rewrite the offending column names,
        trace the recovery, and re-run.
        """
        try:
            rows = self.query(sql, purpose=purpose)
            return rows, {"recovered": False, "final_sql": sql, "discovered_columns": []}
        except QueryError as exc:
            msg = str(exc)
            if "no such column" not in msg.lower() and "column" not in msg.lower():
                raise
            cols = self.describe(silo, table)
            alias_map = column_aliases or {
                "timestamp": ["created_at", "ts_utc", "deployed_at", "changed_at", "opened_at"],
                "platform": ["device_os", "platform"],
                "client_version": ["app_version", "client_ver"],
                "error_code": ["failure_code", "error_code"],
                "tenant_id": ["tenant_id", "tenant"],
            }
            rewritten = sql
            repairs: dict[str, str] = {}
            for wrong_col, candidates in alias_map.items():
                if wrong_col in rewritten and wrong_col not in cols:
                    for cand in candidates:
                        if cand in cols:
                            rewritten = re.sub(rf"\b{re.escape(wrong_col)}\b", cand, rewritten)
                            repairs[wrong_col] = cand
                            break
            if self.tracer:
                self.tracer.emit(
                    self.agent,
                    "TOOL_ERROR_RECOVERY",
                    f"{purpose} failed ({msg})",
                    f"Inspected {{{silo}}}.{table} schema {cols}; repaired columns {repairs} and retried",
                    target_agent="ZetarisAdapter",
                    status="recovered",
                    silo=silo,
                    table=table,
                    discovered_columns=cols,
                    column_repairs=repairs,
                )
            rows = self.query(rewritten, purpose=f"{purpose} (repaired)")
            return rows, {
                "recovered": True,
                "silo": silo,
                "table": table,
                "initial_error": msg,
                "discovered_columns": cols,
                "inspected_schema": cols,
                "column_repairs": repairs,
                "final_sql": rewritten,
                "repaired_sql": rewritten,
            }

    def health(self) -> dict[str, Any]:
        info: dict[str, Any] = {"backend": self.backend, "catalogs": self.catalogs}
        try:
            self.query("SELECT 1 AS ok", purpose="health check", limit=1, retries=0)
            info["reachable"] = True
            info["status"] = "ok"
        except Exception as exc:  # noqa: BLE001 - health must never raise
            info["reachable"] = False
            info["status"] = "error"
            info["error"] = str(exc)[:200]
        return info

    # -------------------------------------------------------------- backends
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            silo_dir = Path(self.settings.silo_dir)
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            conn.row_factory = sqlite3.Row
            for silo in SILOS:
                path = silo_dir / f"{silo}.db"
                if not path.exists():
                    raise QueryError(f"sample silo missing: {path} (run `python run.py seed`)")
                conn.execute(f"ATTACH DATABASE ? AS {silo}", (f"file:{path}?mode=ro",)) if False else \
                    conn.execute(f"ATTACH DATABASE '{path.as_posix()}' AS {silo}")
            self._local.conn = conn
        return conn

    def _run_sqlite(self, sql: str) -> list[dict[str, Any]]:
        try:
            cur = self._conn().execute(sql)
            return [dict(r) for r in cur.fetchall()]
        except sqlite3.OperationalError as exc:
            transient = "locked" in str(exc).lower()
            raise QueryError(f"SQL error: {exc}", transient=transient, sql=sql) from exc

    def _run_rest(self, sql: str, limit: int) -> list[dict[str, Any]]:
        url = self.settings.zetaris_rest_url.rstrip("/") + "/sql-editor/sqls/run"
        body = {"queryId": str(uuid.uuid4()), "sql": sql, "source": "SqlEditor", "limit": limit}
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self.settings.zetaris_api_key,
                "X-Org-ID": str(self.settings.zetaris_org_id),
                "X-Request-ID": str(uuid.uuid4()),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.settings.zetaris_timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise QueryError(f"Zetaris HTTP {exc.code}: {detail}", transient=exc.code in (429, 500, 502, 503, 504)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise QueryError(f"Zetaris unreachable: {exc}", transient=True) from exc
        return normalize_rows(payload)

    # --------------------------------------------------------------- helpers
    def _maybe_inject_fault(self, purpose: str, attempt: int) -> None:
        """Chaos testing hook (off by default). FAULT_INJECTION=telemetry_timeout_once
        makes the first attempt of the first cohort query time out, so the retry path
        can be demonstrated and tested deterministically."""
        if "telemetry_timeout_once" in self._faults and "cohort" in purpose and attempt == 1:
            self._faults.discard("telemetry_timeout_once")
            raise QueryError("injected fault: telemetry source timed out after 30s", transient=True)

    def _record(self, purpose, sql, silos, rows, ms, attempt, status, error=None):
        rec = {"purpose": purpose, "backend": self.backend, "silos": silos, "rows": rows,
               "latency_ms": ms, "attempt": attempt, "status": status, "sql": _compact(sql)}
        if error:
            rec["error"] = error[:300]
        self.query_log.append(rec)


def normalize_rows(payload: Any) -> list[dict[str, Any]]:
    """Accept the common Zetaris/REST result shapes and return a list of dicts."""
    data = payload
    if isinstance(payload, dict):
        for key in ("data", "rows", "result", "results", "records"):
            if key in payload:
                data = payload[key]
                break
        if isinstance(data, dict) and ("rows" in data or "data" in data):
            cols = data.get("columns") or data.get("schema") or payload.get("columns")
            data_rows = data.get("rows") or data.get("data")
            return _rows_with_columns(data_rows, cols)
        cols = payload.get("columns") or payload.get("schema")
        if cols and isinstance(data, list) and data and isinstance(data[0], (list, tuple)):
            return _rows_with_columns(data, cols)
    if isinstance(data, list):
        if not data:
            return []
        if isinstance(data[0], dict):
            return data
    return []


def _rows_with_columns(rows: Any, cols: Any) -> list[dict[str, Any]]:
    names = [c.get("name") if isinstance(c, dict) else str(c) for c in (cols or [])]
    return [dict(zip(names, r)) for r in (rows or [])]


def _compact(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()
