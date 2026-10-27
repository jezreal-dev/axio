"""Domain schemas for IncidentWhy (stdlib dataclasses with `.to_dict()`, plus
optional Pydantic compatibility if installed).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class IncidentAlert:
    incident_id: str
    title: str
    metric: str
    started_at: str
    ended_at: str
    baseline_start: str = "2026-10-22T12:00:00Z"
    baseline_end: str = "2026-10-22T14:00:00Z"
    symptom_summary: str = ""
    scenario_key: str = ""
    tenant_hint: str | None = None
    region_hint: str | None = None
    endpoint_hint: str | None = None
    inject_sql_mismatch: bool = True

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "IncidentAlert":
        """Normalize either `/api/investigate` or `/run` request payloads into an IncidentAlert."""
        if "input" in data and isinstance(data["input"], dict):
            merged = {**data, **data["input"]}
        else:
            merged = dict(data)
        inc_id = (
            merged.get("incident_id")
            or merged.get("run_id")
            or merged.get("request_id")
            or "INC-8891"
        )
        query_text = str(merged.get("user_query") or merged.get("query") or merged.get("symptom_summary") or "")
        metric = str(merged.get("metric") or "")
        scenario_key = str(
            merged.get("scenario_key")
            or merged.get("scenario_id")
            or merged.get("scenario")
            or ""
        )

        # Infer which of the three seeded incidents matches the alert or free-text query
        q_low = (query_text + " " + metric + " " + scenario_key + " " + str(inc_id)).lower()
        if not scenario_key:
            if any(k in q_low for k in ("rate_limit", "429", "acme", "churn", "pricing", "bulk", "inc-8892", "scenario_02", "incident_02")):
                scenario_key = "scenario_02_rate_limit_churn"
            elif any(k in q_low for k in ("tax", "latency", "504", "timeout", "germany", "eu-west", "margin", "inc-8893", "scenario_03", "incident_03")):
                scenario_key = "scenario_03_third_party_timeout_leak"
            else:
                scenario_key = "scenario_01_deploy_red_herring"

        presets = SCENARIO_PRESETS.get(scenario_key, SCENARIO_PRESETS["scenario_01_deploy_red_herring"])
        opts = merged.get("options") if isinstance(merged.get("options"), dict) else {}
        return cls(
            incident_id=str(inc_id),
            title=str(merged.get("title") or presets["title"]),
            metric=str(merged.get("metric") or presets["metric"]),
            started_at=str(merged.get("started_at") or presets["started_at"]),
            ended_at=str(merged.get("ended_at") or presets["ended_at"]),
            baseline_start=str(merged.get("baseline_start") or presets["baseline_start"]),
            baseline_end=str(merged.get("baseline_end") or presets["baseline_end"]),
            symptom_summary=str(query_text or presets["symptom_summary"]),
            scenario_key=scenario_key,
            tenant_hint=merged.get("tenant_hint") or presets.get("tenant_hint"),
            region_hint=merged.get("region_hint") or presets.get("region_hint"),
            endpoint_hint=merged.get("endpoint_hint") or presets.get("endpoint_hint"),
            inject_sql_mismatch=bool(opts.get("inject_sql_mismatch", merged.get("inject_sql_mismatch", True))),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FalsificationCheck:
    check_name: str
    passed: bool
    summary: str
    observed: dict[str, Any] = field(default_factory=dict)
    p_value: float | None = None
    z_score: float | None = None
    risk_ratio: float | None = None
    cited_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Hypothesis:
    id: str
    title: str
    category: str  # deployment | feature_flag | config_change | pricing_change | upstream_provider
    suspected_change_id: str
    suspected_timestamp: str
    target_silo: str
    target_service: str
    target_cohort: dict[str, Any]
    mechanism: str
    status: str = "PENDING"  # PENDING | TESTING | FALSIFIED | VERIFIED
    confidence: float = 0.50
    proposed_by: str = "PlannerAgent"
    round_proposed: int = 1
    round_resolved: int | None = None
    falsification_reason: str | None = None
    checks: list[FalsificationCheck] = field(default_factory=list)
    evidence_stats: dict[str, Any] = field(default_factory=dict)
    citations: list[str] = field(default_factory=list)
    world_entity_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FinancialImpact:
    gross_failed_volume_usd: float
    deduplicated_lost_revenue_usd: float
    hourly_burn_rate_usd: float
    failed_checkout_count: int
    total_checkout_count: int
    retry_inflation_factor: float
    affected_tenants: list[str]
    currency: str = "USD"
    methodology_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["net_revenue_at_risk_usd"] = self.deduplicated_lost_revenue_usd
        return d


SCENARIO_PRESETS: dict[str, dict[str, Any]] = {
    "scenario_01_deploy_red_herring": {
        "incident_id": "INC-8891",
        "title": "Silent Checkout Conversion Drop ($18k/hr Loss) vs. Red-Herring Deploy v3.4.2",
        "metric": "checkout_conversion_drop",
        "started_at": "2026-10-22T14:05:00Z",
        "ended_at": "2026-10-22T15:10:00Z",
        "baseline_start": "2026-10-22T12:00:00Z",
        "baseline_end": "2026-10-22T14:00:00Z",
        "symptom_summary": (
            "Storefront checkout conversion dropped sharply starting at 2026-10-22T14:05:00Z. "
            "On-call suspects checkout-service deployment v3.4.2 shipped today, and merchants "
            "brightmart and kairo-shop opened urgent tickets about iPhone payment failures."
        ),
        "endpoint_hint": "/v1/checkout/confirm",
    },
    "scenario_02_rate_limit_churn": {
        "incident_id": "INC-8892",
        "title": "Enterprise Tenant Order-Sync Collapse (80% Drop) vs. Pricing Complaint Red Herring",
        "metric": "enterprise_order_sync_drop",
        "started_at": "2026-10-23T02:01:00Z",
        "ended_at": "2026-10-23T09:30:00Z",
        "baseline_start": "2026-10-22T02:00:00Z",
        "baseline_end": "2026-10-22T09:30:00Z",
        "symptom_summary": (
            "Enterprise tenant acme-logistics bulk order volume dropped over 80% starting "
            "2026-10-23T02:01:00Z. Support ticket tkt-7003 complains about 'new pricing' and "
            "threatens churn; api-gateway v8.1.0 also deployed earlier that morning."
        ),
        "tenant_hint": "acme-logistics",
        "endpoint_hint": "/v1/orders/bulk",
    },
    "scenario_03_third_party_timeout_leak": {
        "incident_id": "INC-8893",
        "title": "EU Checkout Latency & Retry Storm Leak vs. tax-service v4.2.0 Deploy Red Herring",
        "metric": "checkout_latency_and_timeout_spike",
        "started_at": "2026-10-24T18:12:00Z",
        "ended_at": "2026-10-24T20:05:00Z",
        "baseline_start": "2026-10-24T16:00:00Z",
        "baseline_end": "2026-10-24T18:00:00Z",
        "symptom_summary": (
            "EU checkout latency spiked above 5000ms with high failure rates starting "
            "2026-10-24T18:12:00Z. Engineers suspect tax-service v4.2.0 (deployed at 17:55 UTC) "
            "or the new_tax_engine feature flag rollout."
        ),
        "region_hint": "eu-west-1",
        "endpoint_hint": "/v1/checkout/tax",
    },
}
