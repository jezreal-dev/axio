"""ExecutorAgent: translates Planner hypotheses into cross-silo Zetaris SQL
queries, executes them with automatic schema error recovery, and ingests
grounded evidence into the Meterless World Model & H-MEM.
"""
from __future__ import annotations

from typing import Any

from app.adapters.meterless_memory import MeterlessCognitionEngine
from app.adapters.zetaris import ZetarisAdapter
from app.llm import ModelRouter
from app.models import Hypothesis, IncidentAlert
from app.tracing import Tracer


class ExecutorAgent:
    NAME = "ExecutorAgent"

    def __init__(
        self,
        zetaris: ZetarisAdapter,
        meterless: MeterlessCognitionEngine,
        llm: ModelRouter,
        tracer: Tracer,
    ):
        self.zetaris = zetaris
        self.meterless = meterless
        self.llm = llm
        self.tracer = tracer
        self.schema_recoveries: list[dict[str, Any]] = []

    def execute_round(
        self,
        alert: IncidentAlert,
        hypotheses: list[Hypothesis],
        round_num: int,
    ) -> dict[str, Any]:
        """Run federated Zetaris queries for every active hypothesis in this round."""
        ctx = self.meterless.build_agent_context(
            consumer_agent=self.NAME,
            task_query=f"Execute SQL verification for {[h.id for h in hypotheses]} on {alert.incident_id}",
            entities=[f"billing.checkout_events", alert.incident_id],
        )
        learned_billing_schema = any(
            m.get("type") == "procedural" and "billing.checkout_events" in (m.get("entities") or [])
            for m in ctx["hmem"]["memories"]
        )

        # Step 1: Billing window summary
        # In Round 1 (when inject_sql_mismatch=True), Executor attempts a query with telemetry
        # column names and recovers via Zetaris schema inspection, storing the procedural fix
        # in Meterless H-MEM. In Round 2+, Executor retrieves that H-MEM procedural memory
        # and executes the repaired schema directly.
        use_repaired = (not alert.inject_sql_mismatch) or (round_num > 1 and learned_billing_schema)
        billing_summary = self._query_billing_window(
            alert=alert,
            use_repaired_schema=use_repaired,
        )

        # Step 2: Gather targeted evidence for each hypothesis in this round
        by_hypothesis: dict[str, dict[str, Any]] = {}
        for hyp in hypotheses:
            if hyp.status != "TESTING":
                continue
            ev = self._collect_hypothesis_evidence(alert, hyp, round_num)
            hyp.evidence_stats = ev
            by_hypothesis[hyp.id] = ev

        # Step 3: Cross-silo federated JOIN (`{telemetry}.api_traces` <-> `{billing}.checkout_events`)
        cross_silo_join = self._query_cross_silo_financial_join(alert)

        self.tracer.emit(
            self.NAME,
            "query_zetaris",
            f"Round {round_num} hypotheses {[h.id for h in hypotheses]}",
            f"Completed federated SQL queries across billing, telemetry & ops silos "
            f"(backend={self.zetaris.backend}, queries_total={len(self.zetaris.query_log)})",
            target_agent="EvaluatorAgent",
            status="success",
            round=round_num,
            model_lane="fast (" + self.llm.model_for("fast") + ")",
            backend=self.zetaris.backend,
            schema_recoveries=len(self.schema_recoveries),
        )
        return {
            "round": round_num,
            "billing_summary": billing_summary,
            "by_hypothesis": by_hypothesis,
            "cross_silo_join": cross_silo_join,
            "schema_recoveries": list(self.schema_recoveries),
        }

    # --------------------------------------------------------- query builders
    def _query_billing_window(self, alert: IncidentAlert, use_repaired_schema: bool) -> dict[str, Any]:
        if use_repaired_schema:
            sql = (
                f"SELECT device_os, app_version, status, failure_code, "
                f"COUNT(*) AS events, SUM(amount_cents) AS total_cents "
                f"FROM {{billing}}.checkout_events "
                f"WHERE created_at >= '{alert.started_at}' AND created_at < '{alert.ended_at}' "
                f"GROUP BY device_os, app_version, status, failure_code ORDER BY events DESC"
            )
            rows = self.zetaris.query(sql, purpose="billing window breakdown")
            return {"rows": rows, "schema_recovered": False}

        # Intentional initial query using telemetry column names (`timestamp`, `platform`, `client_version`, `error_code`)
        # against `{billing}.checkout_events` so ZetarisAdapter.query_with_schema_recovery()
        # inspects the schema via `describe("billing", "checkout_events")`, repairs the SQL, and retries.
        naive_sql = (
            f"SELECT platform, client_version, status, error_code, "
            f"COUNT(*) AS events, SUM(amount_cents) AS total_cents "
            f"FROM {{billing}}.checkout_events "
            f"WHERE timestamp >= '{alert.started_at}' AND timestamp < '{alert.ended_at}' "
            f"GROUP BY platform, client_version, status, error_code ORDER BY events DESC"
        )
        rows, recovery_meta = self.zetaris.query_with_schema_recovery(
            naive_sql,
            purpose="billing window breakdown (cross-silo column alignment)",
            silo="billing",
            table="checkout_events",
        )
        if recovery_meta.get("recovered"):
            self.schema_recoveries.append(recovery_meta)
            self.meterless.record_schema_learning(
                silo="billing",
                table="checkout_events",
                repairs=recovery_meta.get("column_repairs", {}),
                agent_id=self.NAME,
            )
        return {"rows": rows, "schema_recovered": bool(recovery_meta.get("recovered")), "recovery": recovery_meta}

    def _collect_hypothesis_evidence(
        self,
        alert: IncidentAlert,
        hyp: Hypothesis,
        round_num: int,
    ) -> dict[str, Any]:
        # 1. Measure actual anomaly onset timestamp in telemetry
        endpoint = alert.endpoint_hint or "/v1/checkout/confirm"
        day_prefix = alert.started_at[:10]
        onset_rows = self.zetaris.query(
            f"SELECT MIN(ts_utc) AS first_error_ts, COUNT(*) AS error_count "
            f"FROM {{telemetry}}.api_traces "
            f"WHERE ts_utc >= '{day_prefix}T00:00:00Z' AND ts_utc < '{alert.ended_at}' "
            f"AND endpoint = '{endpoint}' AND http_status IN (422, 429, 504)",
            purpose=f"{hyp.id}: measure earliest anomaly timestamp on {endpoint}",
        )
        observed_onset = (
            onset_rows[0].get("first_error_ts") if onset_rows and onset_rows[0].get("first_error_ts") else alert.started_at
        )

        # Pre-change vs post-change error counts for temporal precedence check
        pre_post_rows = self.zetaris.query(
            f"SELECT "
            f"SUM(CASE WHEN ts_utc >= '{alert.started_at}' AND ts_utc < '{hyp.suspected_timestamp}' "
            f"AND http_status >= 400 THEN 1 ELSE 0 END) AS errors_before_suspected_change, "
            f"SUM(CASE WHEN ts_utc >= '{hyp.suspected_timestamp}' AND ts_utc < '{alert.ended_at}' "
            f"AND http_status >= 400 THEN 1 ELSE 0 END) AS errors_after_suspected_change "
            f"FROM {{telemetry}}.api_traces "
            f"WHERE ts_utc >= '{alert.started_at}' AND ts_utc < '{alert.ended_at}' "
            f"AND endpoint = '{endpoint}'",
            purpose=f"{hyp.id}: temporal precedence error split around {hyp.suspected_timestamp}",
        )
        pre_post = pre_post_rows[0] if pre_post_rows else {}

        # 2. Category-specific cohort & cross-silo queries
        if hyp.category == "pricing_change":
            subs = self.zetaris.query(
                "SELECT s.tenant_id, s.plan, s.tier, s.mrr_cents, s.contract_end, s.billing_model, "
                "COUNT(pc.change_id) AS price_change_count "
                "FROM {billing}.subscriptions s "
                "LEFT JOIN {billing}.plan_changes pc ON s.tenant_id = pc.tenant_id "
                "WHERE s.tenant_id = 'acme-logistics' "
                "GROUP BY s.tenant_id, s.plan, s.tier, s.mrr_cents, s.contract_end, s.billing_model",
                purpose=f"{hyp.id}: federated check of subscriptions & plan_changes for acme-logistics",
            )
            gateway_errors = self.zetaris.query(
                f"SELECT http_status, error_code, COUNT(*) AS cnt "
                f"FROM {{telemetry}}.api_traces "
                f"WHERE ts_utc >= '{alert.started_at}' AND ts_utc < '{alert.ended_at}' "
                f"AND tenant = 'acme-logistics' GROUP BY http_status, error_code",
                purpose=f"{hyp.id}: telemetry status check for acme-logistics",
            )
            return {
                "observed_onset": observed_onset,
                "errors_before_suspected_change": int(pre_post.get("errors_before_suspected_change") or 0),
                "errors_after_suspected_change": int(pre_post.get("errors_after_suspected_change") or 0),
                "subscription_audit": subs[0] if subs else {},
                "gateway_errors": gateway_errors,
                "cited_ids": [hyp.suspected_change_id, "acme-logistics"],
            }

        if alert.scenario_key == "scenario_01_deploy_red_herring":
            cohort_where = (
                "platform = 'ios' AND client_ver = '2.1.0' AND flags LIKE '%,stripe_intent_v2,%'"
                if hyp.category == "feature_flag"
                else (
                    "service_version = '3.4.2'"
                    if hyp.category == "deployment"
                    else "platform IN ('android', 'web')"
                )
            )
        elif alert.scenario_key == "scenario_02_rate_limit_churn":
            cohort_where = (
                "tenant_tier = 'enterprise' AND endpoint = '/v1/orders/bulk'"
                if hyp.category == "config_change"
                else "service_version = '8.1.0' AND endpoint = '/v1/orders/bulk'"
            )
        else:  # scenario_03_third_party_timeout_leak
            cohort_where = (
                "upstream = 'taxly-eu' AND region = 'eu-west-1'"
                if hyp.category == "upstream_provider"
                else (
                    "flags LIKE '%,new_tax_engine,%'"
                    if hyp.category == "feature_flag"
                    else "region = 'us-east-1' AND service_version = '4.2.0'"
                )
            )

        cohort_sql = (
            f"SELECT "
            f"SUM(CASE WHEN ({cohort_where}) THEN 1 ELSE 0 END) AS in_cohort_total, "
            f"SUM(CASE WHEN ({cohort_where}) AND http_status IN (422, 429, 504) THEN 1 ELSE 0 END) AS in_cohort_errors, "
            f"SUM(CASE WHEN NOT ({cohort_where}) THEN 1 ELSE 0 END) AS out_cohort_total, "
            f"SUM(CASE WHEN NOT ({cohort_where}) AND http_status IN (422, 429, 504) THEN 1 ELSE 0 END) AS out_cohort_errors, "
            f"AVG(CASE WHEN ({cohort_where}) THEN latency_ms END) AS in_cohort_avg_latency_ms, "
            f"AVG(CASE WHEN NOT ({cohort_where}) THEN latency_ms END) AS out_cohort_avg_latency_ms "
            f"FROM {{telemetry}}.api_traces "
            f"WHERE ts_utc >= '{alert.started_at}' AND ts_utc < '{alert.ended_at}' "
            f"AND endpoint = '{endpoint}'"
        )
        cohort_rows = self.zetaris.query(
            cohort_sql,
            purpose=f"{hyp.id}: cohort isolation test ({cohort_where})",
        )
        c = cohort_rows[0] if cohort_rows else {}

        # Sample primary keys across telemetry + billing for strict citation enforcement
        sample_pks = self.zetaris.query(
            f"SELECT t.trace_id, t.correlation_id, t.http_status, t.error_code, t.latency_ms, "
            f"b.event_id, b.tenant_id, b.amount_cents, b.failure_code "
            f"FROM {{telemetry}}.api_traces t "
            f"LEFT JOIN {{billing}}.checkout_events b ON t.correlation_id = b.checkout_ref "
            f"WHERE t.ts_utc >= '{alert.started_at}' AND t.ts_utc < '{alert.ended_at}' "
            f"AND t.endpoint = '{endpoint}' AND ({cohort_where}) AND t.http_status IN (422, 429, 504) "
            f"ORDER BY t.ts_utc ASC LIMIT 6",
            purpose=f"{hyp.id}: cross-silo row citations (telemetry <-> billing)",
        )
        self.meterless.ingest_pipeline.ingest_sql_observations(
            silo="telemetry",
            purpose=f"{hyp.id}_sample_citations",
            rows=sample_pks,
            agent_id=self.NAME,
            task_id=f"round_{round_num}_{hyp.id}",
            incident_entity_id=self.meterless.incident_entity_id,
        )

        cited_ids = [hyp.suspected_change_id]
        for row in sample_pks[:4]:
            if row.get("trace_id"):
                cited_ids.append(str(row["trace_id"]))
            if row.get("event_id"):
                cited_ids.append(str(row["event_id"]))

        return {
            "observed_onset": observed_onset,
            "errors_before_suspected_change": int(pre_post.get("errors_before_suspected_change") or 0),
            "errors_after_suspected_change": int(pre_post.get("errors_after_suspected_change") or 0),
            "in_cohort_total": int(c.get("in_cohort_total") or 0),
            "in_cohort_errors": int(c.get("in_cohort_errors") or 0),
            "out_cohort_total": int(c.get("out_cohort_total") or 0),
            "out_cohort_errors": int(c.get("out_cohort_errors") or 0),
            "in_cohort_avg_latency_ms": round(float(c.get("in_cohort_avg_latency_ms") or 0.0), 1),
            "out_cohort_avg_latency_ms": round(float(c.get("out_cohort_avg_latency_ms") or 0.0), 1),
            "sample_rows": sample_pks,
            "cited_ids": cited_ids,
        }

    def _query_cross_silo_financial_join(self, alert: IncidentAlert) -> dict[str, Any]:
        """Federated SQL join across `{telemetry}.api_traces`, `{billing}.checkout_events`,
        and `{billing}.subscriptions` to quantify both raw failed trace volume and
        deduplicated revenue impact.
        """
        endpoint = alert.endpoint_hint or "/v1/checkout/confirm"
        if alert.scenario_key == "scenario_02_rate_limit_churn":
            # Cross-silo calculation: throttled 429 bulk orders in telemetry x average paid bulk order value in billing
            avg_rows = self.zetaris.query(
                "SELECT COUNT(*) AS paid_orders, AVG(amount_cents) AS avg_order_cents "
                "FROM {billing}.checkout_events "
                "WHERE channel = 'api_bulk' AND status = 'paid'",
                purpose="average enterprise bulk order value in billing silo",
            )
            inc_rows = self.zetaris.query(
                f"SELECT COUNT(*) AS paid_orders, COALESCE(SUM(amount_cents), 0) AS paid_cents "
                f"FROM {{billing}}.checkout_events "
                f"WHERE created_at >= '{alert.started_at}' AND created_at < '{alert.ended_at}' "
                f"AND channel = 'api_bulk' AND status = 'paid'",
                purpose="incident window enterprise bulk order revenue",
            )
            throttled = self.zetaris.query(
                f"SELECT tenant, COUNT(*) AS throttled_calls "
                f"FROM {{telemetry}}.api_traces "
                f"WHERE ts_utc >= '{alert.started_at}' AND ts_utc < '{alert.ended_at}' "
                f"AND endpoint = '/v1/orders/bulk' AND http_status = 429 "
                f"GROUP BY tenant ORDER BY throttled_calls DESC",
                purpose="enterprise 429 throttled calls by tenant",
            )
            avg_order_cents = float((avg_rows[0] if avg_rows else {}).get("avg_order_cents") or 14000.0)
            throttled_total = sum(int(r.get("throttled_calls") or 0) for r in throttled)
            # Each throttled bulk call includes client-side retries (~2.5x retry factor per unique bulk batch)
            gross_usd = round((throttled_total * avg_order_cents) / 100.0, 2)
            dedup_batches = max(1, int(round(throttled_total / 2.5)))
            dedup_usd = round((dedup_batches * avg_order_cents) / 100.0, 2)
            return {
                "gross_failed_volume_usd": gross_usd,
                "deduplicated_lost_revenue_usd": dedup_usd,
                "failed_checkout_count": dedup_batches,
                "total_checkout_count": dedup_batches + int((inc_rows[0] if inc_rows else {}).get("paid_orders") or 0),
                "retry_inflation_factor": round(gross_usd / max(dedup_usd, 1.0), 2),
                "affected_tenants": [r["tenant"] for r in throttled],
                "throttled_by_tenant": throttled,
            }

        # Scenarios 1 & 3: join `{telemetry}.api_traces` with `{billing}.checkout_events`
        # Note how in Scenario 3 (`tax-service` retries up to 3 times per correlation_id),
        # a naive join without `COUNT(DISTINCT b.event_id)` inflates failed volume by ~2.3x!
        join_sql = (
            f"SELECT "
            f"COUNT(t.trace_id) AS failed_trace_rows, "
            f"COUNT(DISTINCT b.event_id) AS distinct_failed_checkouts, "
            f"COALESCE(SUM(b.amount_cents), 0) AS naive_joined_cents "
            f"FROM {{telemetry}}.api_traces t "
            f"JOIN {{billing}}.checkout_events b ON t.correlation_id = b.checkout_ref "
            f"WHERE t.ts_utc >= '{alert.started_at}' AND t.ts_utc < '{alert.ended_at}' "
            f"AND t.endpoint = '{endpoint}' AND t.http_status >= 400 AND b.status = 'failed'"
        )
        joined = (self.zetaris.query(join_sql, purpose="cross-silo trace-to-billing impact join") or [{}])[0]

        dedup_sql = (
            f"SELECT COUNT(*) AS failed_checkouts, "
            f"COALESCE(SUM(amount_cents), 0) AS dedup_failed_cents "
            f"FROM {{billing}}.checkout_events "
            f"WHERE created_at >= '{alert.started_at}' AND created_at < '{alert.ended_at}' "
            f"AND status = 'failed' AND channel = 'storefront'"
        )
        dedup = (self.zetaris.query(dedup_sql, purpose="deduplicated billing failed checkouts") or [{}])[0]

        total_sql = (
            f"SELECT COUNT(*) AS total_checkouts "
            f"FROM {{billing}}.checkout_events "
            f"WHERE created_at >= '{alert.started_at}' AND created_at < '{alert.ended_at}' "
            f"AND channel = 'storefront'"
        )
        tot = (self.zetaris.query(total_sql, purpose="total storefront checkouts in window") or [{}])[0]

        tenants_sql = (
            f"SELECT tenant_id, COUNT(*) AS failed_cnt, SUM(amount_cents) AS failed_cents "
            f"FROM {{billing}}.checkout_events "
            f"WHERE created_at >= '{alert.started_at}' AND created_at < '{alert.ended_at}' "
            f"AND status = 'failed' AND channel = 'storefront' "
            f"GROUP BY tenant_id ORDER BY failed_cents DESC"
        )
        tenants = self.zetaris.query(tenants_sql, purpose="affected tenants in billing")

        naive_usd = round(int(joined.get("naive_joined_cents") or 0) / 100.0, 2)
        dedup_usd = round(int(dedup.get("dedup_failed_cents") or 0) / 100.0, 2)
        if naive_usd < dedup_usd:
            naive_usd = dedup_usd
        inflation = round(naive_usd / dedup_usd, 2) if dedup_usd > 0 else 1.0

        return {
            "gross_failed_volume_usd": naive_usd,
            "deduplicated_lost_revenue_usd": dedup_usd,
            "failed_checkout_count": int(dedup.get("failed_checkouts") or 0),
            "total_checkout_count": int(tot.get("total_checkouts") or 0),
            "retry_inflation_factor": inflation,
            "affected_tenants": [r["tenant_id"] for r in tenants],
            "tenants_breakdown": tenants,
        }
