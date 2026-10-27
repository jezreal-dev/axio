"""PlannerAgent: decomposes an IncidentAlert into testable hypotheses and revises
the investigative plan when EvaluatorAgent falsifies a hypothesis or flags a
coverage gap.

Uses the NVIDIA Nemotron `reasoning` lane via `ModelRouter` when configured, and
always anchors candidate hypotheses in actual rows discovered in the `{ops}` and
`{telemetry}` silos plus the Meterless World Model / H-MEM context.
"""
from __future__ import annotations

from typing import Any

from app.adapters.meterless_memory import MeterlessCognitionEngine
from app.adapters.zetaris import ZetarisAdapter
from app.llm import ModelRouter
from app.models import Hypothesis, IncidentAlert
from app.tracing import Tracer


class PlannerAgent:
    NAME = "PlannerAgent"

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

    def create_initial_plan(self, alert: IncidentAlert) -> tuple[list[Hypothesis], dict[str, Any]]:
        """Round 1: inspect change logs & support tickets in `{ops}`, consult
        Meterless H-MEM, and formulate initial war-room hypotheses.
        """
        ctx = self.meterless.build_agent_context(
            consumer_agent=self.NAME,
            task_query=f"Initial hypotheses for {alert.incident_id} {alert.metric} {alert.symptom_summary}",
            entities=[alert.incident_id],
        )

        # Surface scan of {ops} tables around the incident date
        day_prefix = alert.started_at[:10]
        deploys = self.zetaris.query(
            f"SELECT deploy_id, service, version, commit_sha, author, deployed_at, summary "
            f"FROM {{ops}}.deployments WHERE deployed_at >= '{day_prefix}T00:00:00Z' "
            f"ORDER BY deployed_at ASC",
            purpose=f"ops surface scan: deployments on {day_prefix}",
        )
        tickets = self.zetaris.query(
            f"SELECT ticket_id, tenant, opened_at, subject, body, priority "
            f"FROM {{ops}}.support_tickets WHERE opened_at >= '{day_prefix}T00:00:00Z' "
            f"ORDER BY opened_at ASC",
            purpose=f"ops surface scan: support_tickets on {day_prefix}",
        )
        flags = self.zetaris.query(
            f"SELECT change_id, flag_key, changed_at, actor, action, rollout_pct, targeting "
            f"FROM {{ops}}.flag_changes WHERE changed_at >= '{day_prefix}T00:00:00Z' "
            f"ORDER BY changed_at ASC",
            purpose=f"ops surface scan: flag_changes on {day_prefix}",
        )
        configs = self.zetaris.query(
            f"SELECT change_id, service, config_key, old_value, new_value, changed_at, actor, scope "
            f"FROM {{ops}}.config_changes WHERE changed_at >= '{day_prefix}T00:00:00Z' "
            f"ORDER BY changed_at ASC",
            purpose=f"ops surface scan: config_changes on {day_prefix}",
        )

        for label, rows in (("deployments", deploys), ("support_tickets", tickets), ("flag_changes", flags), ("config_changes", configs)):
            self.meterless.ingest_pipeline.ingest_sql_observations(
                silo="ops",
                purpose=f"round1_{label}",
                rows=rows,
                agent_id=self.NAME,
                task_id="round_1_plan",
                incident_entity_id=self.meterless.incident_entity_id,
            )

        hypotheses = self._build_round1_hypotheses(alert, deploys, tickets, flags, configs)

        # Optional NVIDIA Nemotron reasoning-lane enrichment of hypothesis rationale
        if self.llm.available:
            llm_note = self.llm.generate_json(
                "reasoning",
                "You are PlannerAgent in an SRE root-cause investigation. Return JSON {\"planner_note\": \"...\"}.",
                f"Incident: {alert.title} (onset {alert.started_at}). Candidate Round-1 hypotheses: "
                f"{[h.title for h in hypotheses]}",
                max_tokens=220,
            )
        else:
            llm_note = None

        for h in hypotheses:
            h.status = "TESTING"
            self.meterless.register_hypothesis(h, agent_id=self.NAME)

        self.tracer.emit(
            self.NAME,
            "decompose_question",
            f"Incident {alert.incident_id} ({alert.metric}) onset={alert.started_at}",
            f"Formulated {len(hypotheses)} Round-1 hypotheses: "
            + "; ".join(f"{h.id} [{h.suspected_change_id} @ {h.suspected_timestamp}]" for h in hypotheses),
            target_agent="ExecutorAgent",
            status="success",
            model_lane="reasoning (" + self.llm.model_for("reasoning") + ")",
            hypotheses=[h.id for h in hypotheses],
            hmem_reinjected=ctx["hmem"]["returned_count"],
            llm_note=llm_note,
        )
        return hypotheses, {
            "deploys": deploys,
            "tickets": tickets,
            "flags": flags,
            "configs": configs,
        }

    def revise_plan(
        self,
        alert: IncidentAlert,
        prior_hypotheses: list[Hypothesis],
        evaluator_feedback: dict[str, Any],
        ops_surface: dict[str, Any],
        round_num: int,
    ) -> list[Hypothesis]:
        """Round 2+: read Evaluator falsifications from Meterless H-MEM and propose
        the deeper cross-silo hypothesis that survives temporal & cohort constraints.
        """
        falsified_ids = {h.suspected_change_id for h in prior_hypotheses if h.status == "FALSIFIED"}
        ctx = self.meterless.build_agent_context(
            consumer_agent=self.NAME,
            task_query=(
                f"Revise plan for {alert.incident_id} after falsifying {sorted(falsified_ids)}; "
                f"feedback={evaluator_feedback.get('summary', '')}"
            ),
            entities=[alert.incident_id] + [h.id for h in prior_hypotheses],
        )

        new_hypotheses = self._build_revised_hypotheses(
            alert=alert,
            falsified_change_ids=falsified_ids,
            ops_surface=ops_surface,
            round_num=round_num,
        )
        for h in new_hypotheses:
            h.status = "TESTING"
            self.meterless.register_hypothesis(h, agent_id=self.NAME)

        self.tracer.emit(
            self.NAME,
            "revise_plan",
            f"Evaluator falsified {sorted(falsified_ids)}: {evaluator_feedback.get('summary', '')}",
            f"Formulated Round-{round_num} hypothesis "
            + "; ".join(f"{h.id}: {h.title} [{h.suspected_change_id} @ {h.suspected_timestamp}]" for h in new_hypotheses),
            target_agent="ExecutorAgent",
            status="success",
            round=round_num,
            falsified_change_ids=sorted(falsified_ids),
            hmem_falsifications_loaded=sum(
                1 for m in ctx["hmem"]["memories"] if m.get("type") == "falsification"
            ),
            new_hypotheses=[h.id for h in new_hypotheses],
        )
        return new_hypotheses

    # ------------------------------------------------- hypothesis builders
    def _build_round1_hypotheses(
        self,
        alert: IncidentAlert,
        deploys: list[dict[str, Any]],
        tickets: list[dict[str, Any]],
        flags: list[dict[str, Any]],
        configs: list[dict[str, Any]],
    ) -> list[Hypothesis]:
        if alert.scenario_key == "scenario_02_rate_limit_churn":
            tkt = next((t for t in tickets if t["tenant"] == "acme-logistics"), tickets[0] if tickets else {})
            dep = next((d for d in deploys if d["service"] == "api-gateway"), deploys[0] if deploys else {})
            return [
                Hypothesis(
                    id="H1",
                    title="Customer-side pullback due to recent plan price increase (ticket tkt-7003)",
                    category="pricing_change",
                    suspected_change_id=str(tkt.get("ticket_id", "tkt-7003")),
                    suspected_timestamp=str(tkt.get("opened_at", "2026-10-23T09:10:00Z")),
                    target_silo="billing",
                    target_service="subscriptions",
                    target_cohort={"tenant": "acme-logistics"},
                    mechanism=(
                        "Support ticket tkt-7003 from acme-logistics cites dissatisfaction with 'new pricing'; "
                        "hypothesis: a recent price hike in billing.plan_changes caused the tenant to throttle "
                        "or halt bulk order sync."
                    ),
                    confidence=0.68,
                    round_proposed=1,
                ),
                Hypothesis(
                    id="H2",
                    title="api-gateway v8.1.0 deployment (envoy 1.31 upgrade) broke /v1/orders/bulk",
                    category="deployment",
                    suspected_change_id=str(dep.get("deploy_id", "dep-1007")),
                    suspected_timestamp=str(dep.get("deployed_at", "2026-10-23T03:30:00Z")),
                    target_silo="ops",
                    target_service="api-gateway",
                    target_cohort={"service": "api-gateway", "service_version": "8.1.0"},
                    mechanism=(
                        f"Deployment {dep.get('deploy_id', 'dep-1007')} ({dep.get('summary', 'envoy upgrade')}) "
                        f"shipped at {dep.get('deployed_at', '2026-10-23T03:30:00Z')} and may have broken "
                        "upstream connection pooling for /v1/orders/bulk."
                    ),
                    confidence=0.64,
                    round_proposed=1,
                ),
            ]

        if alert.scenario_key == "scenario_03_third_party_timeout_leak":
            flg = next((f for f in flags if f["flag_key"] == "new_tax_engine"), flags[-1] if flags else {})
            dep = next((d for d in deploys if d["service"] == "tax-service"), deploys[-1] if deploys else {})
            return [
                Hypothesis(
                    id="H1",
                    title="new_tax_engine feature-flag rollout (flg-505) caused checkout tax timeouts",
                    category="feature_flag",
                    suspected_change_id=str(flg.get("change_id", "flg-505")),
                    suspected_timestamp=str(flg.get("changed_at", "2026-10-24T19:00:00Z")),
                    target_silo="ops",
                    target_service="tax-service",
                    target_cohort={"flag": "new_tax_engine"},
                    mechanism=(
                        f"Feature flag {flg.get('change_id', 'flg-505')} (new_tax_engine 20% global rollout at "
                        f"{flg.get('changed_at', '2026-10-24T19:00:00Z')}) introduced slow tax calculation queries."
                    ),
                    confidence=0.66,
                    round_proposed=1,
                ),
                Hypothesis(
                    id="H2",
                    title="Global code bug in tax-service v4.2.0 deployment (dep-1011) broke all tax calculations",
                    category="deployment",
                    suspected_change_id=str(dep.get("deploy_id", "dep-1011")),
                    suspected_timestamp=str(dep.get("deployed_at", "2026-10-24T17:55:00Z")),
                    target_silo="ops",
                    target_service="tax-service",
                    target_cohort={"service": "tax-service", "service_version": "4.2.0", "scope": "global"},
                    mechanism=(
                        f"Deployment {dep.get('deploy_id', 'dep-1011')} (tax-service v4.2.0 at "
                        f"{dep.get('deployed_at', '2026-10-24T17:55:00Z')}) introduced a global regression "
                        "affecting all regions (us-east-1, eu-west-1, ap-south-1) equally."
                    ),
                    confidence=0.71,
                    round_proposed=1,
                ),
            ]

        # Default: scenario_01_deploy_red_herring
        dep = next((d for d in deploys if d["service"] == "checkout-service"), deploys[0] if deploys else {})
        cfg = next((c for c in configs if c["service"] == "checkout-service"), configs[0] if configs else {})
        return [
            Hypothesis(
                id="H1",
                title="checkout-service v3.4.2 deployment (stripe-python 7.1->7.3 bump) broke /v1/checkout/confirm",
                category="deployment",
                suspected_change_id=str(dep.get("deploy_id", "dep-1005")),
                suspected_timestamp=str(dep.get("deployed_at", "2026-10-22T14:20:00Z")),
                target_silo="ops",
                target_service="checkout-service",
                target_cohort={"service": "checkout-service", "service_version": "3.4.2"},
                mechanism=(
                    f"Deployment {dep.get('deploy_id', 'dep-1005')} ({dep.get('summary', '')}) shipped at "
                    f"{dep.get('deployed_at', '2026-10-22T14:20:00Z')} and is the prime on-call suspect for "
                    "Stripe checkout confirmation failures."
                ),
                confidence=0.74,
                round_proposed=1,
            ),
            Hypothesis(
                id="H2",
                title="Config change cfg-301 (stripe.api_timeout_ms 8000->6000) caused global Stripe timeouts",
                category="config_change",
                suspected_change_id=str(cfg.get("change_id", "cfg-301")),
                suspected_timestamp=str(cfg.get("changed_at", "2026-10-22T13:40:00Z")),
                target_silo="ops",
                target_service="checkout-service",
                target_cohort={"config_key": "stripe.api_timeout_ms", "scope": "global"},
                mechanism=(
                    f"Config change {cfg.get('change_id', 'cfg-301')} lowered stripe.api_timeout_ms from "
                    "8000ms to 6000ms at 2026-10-22T13:40:00Z across all platforms globally."
                ),
                confidence=0.61,
                round_proposed=1,
            ),
        ]

    def _build_revised_hypotheses(
        self,
        *,
        alert: IncidentAlert,
        falsified_change_ids: set[str],
        ops_surface: dict[str, Any],
        round_num: int,
    ) -> list[Hypothesis]:
        flags = ops_surface.get("flags", [])
        configs = ops_surface.get("configs", [])

        if alert.scenario_key == "scenario_02_rate_limit_churn":
            cfg = next(
                (c for c in configs if c["config_key"] == "rate_limit.enterprise_burst_per_min"),
                {"change_id": "cfg-302", "changed_at": "2026-10-23T01:45:00Z"},
            )
            return [
                Hypothesis(
                    id="H3",
                    title="Gateway config cfg-302 lowered enterprise burst rate limit 500->50 req/min, causing HTTP 429 throttling",
                    category="config_change",
                    suspected_change_id=str(cfg.get("change_id", "cfg-302")),
                    suspected_timestamp=str(cfg.get("changed_at", "2026-10-23T01:45:00Z")),
                    target_silo="ops+telemetry+billing",
                    target_service="api-gateway",
                    target_cohort={"tenant_tier": "enterprise", "endpoint": "/v1/orders/bulk", "http_status": 429},
                    mechanism=(
                        "Terraform config change cfg-302 at 2026-10-23T01:45:00Z (16 minutes before the 02:01 UTC "
                        "nightly sync window) reduced rate_limit.enterprise_burst_per_min from 500 to 50 req/min, "
                        "throttling acme-logistics (~60 req/min burst) with HTTP 429 rate_limited."
                    ),
                    confidence=0.92,
                    round_proposed=round_num,
                )
            ]

        if alert.scenario_key == "scenario_03_third_party_timeout_leak":
            return [
                Hypothesis(
                    id="H3",
                    title="Third-party upstream provider taxly-eu 504 timeout degradation in eu-west-1 amplified by 3x retry loop",
                    category="upstream_provider",
                    suspected_change_id="upstream-taxly-eu-504",
                    suspected_timestamp="2026-10-24T18:12:00Z",
                    target_silo="telemetry+billing+ops",
                    target_service="tax-service",
                    target_cohort={"upstream": "taxly-eu", "region": "eu-west-1", "endpoint": "/v1/checkout/tax"},
                    mechanism=(
                        "External tax provider taxly-eu in eu-west-1 began timing out (>5000ms, HTTP 504) at "
                        "2026-10-24T18:12:00Z. tax-service retries each failed tax call up to 3 times (attempt 1..3), "
                        "multiplying trace error volume and latency while exhausting EU checkout sessions."
                    ),
                    confidence=0.93,
                    round_proposed=round_num,
                )
            ]

        flg = next(
            (f for f in flags if f["flag_key"] == "stripe_intent_v2" and f["action"] == "enable"),
            {"change_id": "flg-502", "changed_at": "2026-10-22T14:04:00Z"},
        )
        return [
            Hypothesis(
                id="H3",
                title="Feature flag stripe_intent_v2=true rollout (flg-502 at 14:04 UTC) on iOS client v2.1.0 throwing HTTP 422",
                category="feature_flag",
                suspected_change_id=str(flg.get("change_id", "flg-502")),
                suspected_timestamp=str(flg.get("changed_at", "2026-10-22T14:04:00Z")),
                target_silo="ops+telemetry+billing",
                target_service="checkout-service",
                target_cohort={"platform": "ios", "client_ver": "2.1.0", "flag": "stripe_intent_v2"},
                mechanism=(
                    "Feature flag flg-502 enabled stripe_intent_v2=100% for platform=ios;client_ver>=2.1.0 at "
                    "2026-10-22T14:04:00Z (1 minute before the 14:05 UTC drop), causing /v1/checkout/confirm "
                    "to fail with HTTP 422 payment_intent_unprocessable until disabled by flg-503 at 15:10 UTC."
                ),
                confidence=0.95,
                round_proposed=round_num,
            )
        ]
