"""Deterministic synthetic data for three fragmented silos of a fictional SaaS
commerce company ("ShopLane").

The silos intentionally disagree on naming and units, like real systems do:

  billing.db    (payments ledger)   created_at, amount_cents, device_os='iOS', checkout_ref
  telemetry.db  (APM / traces)      ts_utc, platform='ios', client_ver, correlation_id
  ops.db        (CI/CD, flags, config, support)  deployed_at / changed_at / opened_at

Four days of traffic (2026-10-21 .. 2026-10-24 UTC) are generated with a fixed
seed. Three incidents are embedded, each with plausible red herrings. The
investigation agents are never told the answer: they only see these tables.

This module only produces INPUT data. No investigation output is stored here.
"""
from __future__ import annotations

import math
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20261022
START = datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)
SCHEMA_VERSION = 3


def T(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- change log
DEPLOYS = [
    ("checkout-service", "3.4.0", "2026-10-21T09:12", "a.okafor", "Add idempotency keys to order creation"),
    ("search-service", "5.0.2", "2026-10-21T15:40", "m.ruiz", "Ranking feature store client upgrade"),
    ("notification-service", "2.8.1", "2026-10-22T11:30", "s.patel", "Batch push notifications"),
    ("catalog-service", "7.2.0", "2026-10-22T13:10", "l.nguyen", "Variant pricing cache"),
    ("checkout-service", "3.4.2", "2026-10-22T14:20", "a.okafor",
     "Refactor order summary serializer; bump stripe-python 7.1 -> 7.3"),
    ("search-service", "5.0.3", "2026-10-22T16:45", "m.ruiz", "Fix typo-tolerance regression"),
    ("api-gateway", "8.1.0", "2026-10-23T03:30", "infra-bot", "Upgrade envoy 1.31; tune upstream connection pool"),
    ("checkout-service", "3.4.3", "2026-10-23T10:00", "a.okafor", "Order summary currency formatting"),
    ("catalog-service", "7.2.1", "2026-10-23T15:20", "l.nguyen", "Cache TTL tweak"),
    ("notification-service", "2.8.2", "2026-10-24T11:05", "s.patel", "Email template fixes"),
    ("tax-service", "4.2.0", "2026-10-24T17:55", "k.weber", "Add EU VAT rounding rules (OSS scheme)"),
    ("search-service", "5.0.4", "2026-10-24T21:00", "m.ruiz", "Synonym dictionary refresh"),
]
INITIAL_VERSIONS = {
    "checkout-service": "3.3.9",
    "tax-service": "4.1.3",
    "api-gateway": "8.0.6",
}

FLAG_CHANGES = [
    ("search_ranking_v5", "2026-10-22T10:15", "m.ruiz", "rollout", 50, "global"),
    ("stripe_intent_v2", "2026-10-22T14:04", "j.chen", "enable", 100, "platform=ios;client_ver>=2.1.0"),
    ("stripe_intent_v2", "2026-10-22T15:10", "j.chen", "disable", 0, "platform=ios;client_ver>=2.1.0"),
    ("android_checkout_redesign", "2026-10-23T00:30", "p.ade", "rollout", 50, "platform=android"),
    ("new_tax_engine", "2026-10-24T19:00", "k.weber", "rollout", 20, "global"),
]

CONFIG_CHANGES = [
    ("checkout-service", "stripe.api_timeout_ms", "8000", "6000", "2026-10-22T13:40", "a.okafor", "global"),
    ("api-gateway", "rate_limit.enterprise_burst_per_min", "500", "50", "2026-10-23T01:45", "infra-terraform",
     "tier=enterprise"),
    ("tax-service", "provider.retry_backoff_ms", "200", "100", "2026-10-24T16:00", "k.weber", "region=us-east-1"),
]

TICKETS = [
    ("brightmart", "2026-10-22T14:18", "Customers can't pay on iPhone",
     "Several customers say payment fails right at the confirm step on iPhone since lunch.", "high"),
    ("kairo-shop", "2026-10-22T14:26", "Checkout broken after your update?",
     "Did you ship an update today? Our payments are failing.", "high"),
    ("acme-logistics", "2026-10-23T09:10", "Order sync failing - reconsidering plan",
     "Our nightly order sync has been failing since last night. Frankly we are also evaluating whether "
     "the new pricing is worth it for us.", "urgent"),
    ("nordic-goods", "2026-10-24T18:30", "Checkout extremely slow in Germany",
     "German customers wait 10+ seconds at checkout and many give up.", "high"),
    ("peak-outfitters", "2026-10-21T10:02", "Invoice PDF question", "Where can I download last month's invoice?", "low"),
]

# ----------------------------------------------------------------- tenants
B2C_TENANTS = [  # (tenant, tier, billing region code, weight)
    ("brightmart", "growth", 3), ("nordic-goods", "pro", 2), ("kairo-shop", "growth", 2),
    ("peak-outfitters", "pro", 2), ("luma-home", "growth", 1), ("verde-market", "starter", 1),
]
ENTERPRISE = {  # tenant -> (region, calls/min during sync)
    "acme-logistics": ("us-east-1", 60),
    "orbit-freight": ("eu-west-1", 30),
    "helio-health": ("us-east-1", 12),
}
SUBSCRIPTIONS = [
    ("brightmart", "growth-monthly", "growth", 49900, "2026-11-30", "seat"),
    ("nordic-goods", "pro-monthly", "pro", 129900, "2026-11-30", "seat"),
    ("kairo-shop", "growth-monthly", "growth", 49900, "2026-11-30", "seat"),
    ("peak-outfitters", "pro-annual", "pro", 119900, "2027-02-28", "seat"),
    ("luma-home", "growth-monthly", "growth", 49900, "2026-11-30", "seat"),
    ("verde-market", "starter-monthly", "starter", 9900, "2026-11-30", "seat"),
    ("acme-logistics", "enterprise-annual", "enterprise", 2400000, "2027-06-30", "usage"),
    ("orbit-freight", "enterprise-annual", "enterprise", 1500000, "2027-03-31", "usage"),
    ("helio-health", "enterprise-annual", "enterprise", 900000, "2027-09-30", "usage"),
]
PLAN_CHANGES = [  # none for acme-logistics: the "pricing" claim is a red herring
    ("brightmart", "starter-monthly", "growth-monthly", 9900, 49900, "2026-10-14T08:00"),
    ("luma-home", "starter-monthly", "growth-monthly", 9900, 49900, "2026-10-19T12:30"),
]

REGION_BILLING_CODE = {"us-east-1": "US-E", "eu-west-1": "EU-W", "ap-south-1": "AP-S"}
OS_BILLING = {"ios": "iOS", "android": "Android", "web": "Web", "server": "API"}


def _version_at(service: str, ts: datetime) -> str:
    ver = INITIAL_VERSIONS.get(service, "1.0.0")
    for svc, v, at, *_ in DEPLOYS:
        if svc == service and T(at) <= ts:
            ver = v
    return ver


def _flag_on(flag: str, ts: datetime) -> bool:
    state = False
    for key, at, _actor, action, _pct, _tgt in FLAG_CHANGES:
        if key == flag and T(at) <= ts:
            state = action != "disable"
    return state


def _config_value(service: str, key: str, ts: datetime, default: str) -> str:
    val = default
    for svc, k, _old, new, at, *_ in CONFIG_CHANGES:
        if svc == service and k == key and T(at) <= ts:
            val = new
    return val


# ---------------------------------------------------------------- generator
def generate(silo_dir: Path, force: bool = False) -> dict:
    silo_dir = Path(silo_dir)
    silo_dir.mkdir(parents=True, exist_ok=True)
    marker = silo_dir / f".schema_v{SCHEMA_VERSION}"
    if marker.exists() and not force:
        return {"status": "exists", "silo_dir": str(silo_dir)}
    for name in ("billing.db", "telemetry.db", "ops.db"):
        p = silo_dir / name
        if p.exists():
            p.unlink()

    rng = random.Random(SEED)
    billing = sqlite3.connect(silo_dir / "billing.db")
    telem = sqlite3.connect(silo_dir / "telemetry.db")
    ops = sqlite3.connect(silo_dir / "ops.db")

    billing.executescript(
        """
        CREATE TABLE checkout_events (
            event_id TEXT PRIMARY KEY, checkout_ref TEXT, tenant_id TEXT, created_at TEXT,
            amount_cents INTEGER, currency TEXT, status TEXT, failure_code TEXT,
            device_os TEXT, app_version TEXT, region_code TEXT, channel TEXT);
        CREATE INDEX ix_ce_created ON checkout_events(created_at);
        CREATE INDEX ix_ce_ref ON checkout_events(checkout_ref);
        CREATE TABLE subscriptions (
            tenant_id TEXT PRIMARY KEY, plan TEXT, tier TEXT, mrr_cents INTEGER,
            contract_end TEXT, billing_model TEXT);
        CREATE TABLE plan_changes (
            change_id TEXT PRIMARY KEY, tenant_id TEXT, old_plan TEXT, new_plan TEXT,
            old_price_cents INTEGER, new_price_cents INTEGER, effective_at TEXT);
        """
    )
    telem.executescript(
        """
        CREATE TABLE api_traces (
            trace_id TEXT PRIMARY KEY, ts_utc TEXT, tenant TEXT, tenant_tier TEXT,
            service TEXT, service_version TEXT, endpoint TEXT, http_status INTEGER,
            latency_ms INTEGER, platform TEXT, client_ver TEXT, region TEXT,
            flags TEXT, upstream TEXT, correlation_id TEXT, error_code TEXT, attempt INTEGER);
        CREATE INDEX ix_tr_ts ON api_traces(ts_utc);
        CREATE INDEX ix_tr_corr ON api_traces(correlation_id);
        """
    )
    ops.executescript(
        """
        CREATE TABLE deployments (deploy_id TEXT PRIMARY KEY, service TEXT, version TEXT,
            commit_sha TEXT, author TEXT, deployed_at TEXT, summary TEXT);
        CREATE TABLE flag_changes (change_id TEXT PRIMARY KEY, flag_key TEXT, changed_at TEXT,
            actor TEXT, action TEXT, rollout_pct INTEGER, targeting TEXT);
        CREATE TABLE config_changes (change_id TEXT PRIMARY KEY, service TEXT, config_key TEXT,
            old_value TEXT, new_value TEXT, changed_at TEXT, actor TEXT, scope TEXT);
        CREATE TABLE support_tickets (ticket_id TEXT PRIMARY KEY, tenant TEXT, opened_at TEXT,
            subject TEXT, body TEXT, priority TEXT);
        """
    )

    # ---- ops silo
    for i, (svc, ver, at, author, summary) in enumerate(DEPLOYS, 1):
        sha = "%07x" % rng.getrandbits(28)
        ops.execute("INSERT INTO deployments VALUES (?,?,?,?,?,?,?)",
                    (f"dep-{1000 + i}", svc, ver, sha, author, iso(T(at)), summary))
    for i, (key, at, actor, action, pct, tgt) in enumerate(FLAG_CHANGES, 1):
        ops.execute("INSERT INTO flag_changes VALUES (?,?,?,?,?,?,?)",
                    (f"flg-{500 + i}", key, iso(T(at)), actor, action, pct, tgt))
    for i, (svc, key, old, new, at, actor, scope) in enumerate(CONFIG_CHANGES, 1):
        ops.execute("INSERT INTO config_changes VALUES (?,?,?,?,?,?,?,?)",
                    (f"cfg-{300 + i}", svc, key, old, new, iso(T(at)), actor, scope))
    for i, (tenant, at, subject, body, prio) in enumerate(TICKETS, 1):
        ops.execute("INSERT INTO support_tickets VALUES (?,?,?,?,?,?)",
                    (f"tkt-{7000 + i}", tenant, iso(T(at)), subject, body, prio))

    # ---- billing reference tables
    billing.executemany("INSERT INTO subscriptions VALUES (?,?,?,?,?,?)", SUBSCRIPTIONS)
    for i, (tenant, old, new, op, np_, at) in enumerate(PLAN_CHANGES, 1):
        billing.execute("INSERT INTO plan_changes VALUES (?,?,?,?,?,?,?)",
                        (f"pc-{200 + i}", tenant, old, new, op, np_, iso(T(at))))

    traces: list[tuple] = []
    events: list[tuple] = []
    seq = {"t": 0, "c": 0}

    def trace(ts, tenant, tier, service, endpoint, status, latency, platform, client_ver, region,
              flags, upstream, corr, err, attempt=1):
        seq["t"] += 1
        traces.append((f"tr-{seq['t']:07d}", iso(ts), tenant, tier, service, _version_at(service, ts),
                       endpoint, status, latency, platform, client_ver, region,
                       "," + ",".join(flags) + "," if flags else "", upstream, corr, err, attempt))

    tenants_w = [t for t, _tier, w in B2C_TENANTS for _ in range(w)]
    tier_of = {t: tier for t, tier, _w in B2C_TENANTS}

    minute = START
    while minute < END:
        in_incident_window = (
            (T("2026-10-22T13:00") <= minute < T("2026-10-22T16:00"))
            or (T("2026-10-23T01:30") <= minute < T("2026-10-23T10:00"))
            or (T("2026-10-24T17:30") <= minute < T("2026-10-24T20:30"))
        )
        step_minutes = 1 if in_incident_window else 10
        hour = minute.hour + minute.minute / 60
        # diurnal B2C demand peaking ~15:00 UTC
        lam = 4.5 + 3.5 * math.exp(-((hour - 15.0) ** 2) / 18.0)
        n = _poisson(rng, lam)
        for _ in range(n):
            ts = minute + timedelta(seconds=rng.randint(0, 59))
            seq["c"] += 1
            corr = f"chk-{seq['c']:07d}"
            tenant = rng.choice(tenants_w)
            tier = tier_of[tenant]
            r = rng.random()
            platform = "ios" if r < 0.42 else ("android" if r < 0.75 else "web")
            if platform == "ios":
                adoption = 0.0 if ts < T("2026-10-21T12:00") else (0.35 if ts < T("2026-10-22T00:00") else 0.55)
                client_ver = "2.1.0" if rng.random() < adoption else "2.0.9"
            elif platform == "android":
                client_ver = rng.choice(["5.3.2", "5.3.4", "5.3.4"])
            else:
                client_ver = "web-12.0" if ts >= T("2026-10-24T17:55") else "web-11.8"
            rr = rng.random()
            region = "us-east-1" if rr < 0.5 else ("eu-west-1" if rr < 0.8 else "ap-south-1")
            amount = int(max(800, rng.lognormvariate(math.log(6500), 0.6)))

            flags = []
            if _flag_on("stripe_intent_v2", ts) and platform == "ios" and client_ver >= "2.1.0":
                flags.append("stripe_intent_v2")
            if _flag_on("android_checkout_redesign", ts) and platform == "android" and rng.random() < 0.5:
                flags.append("android_checkout_redesign")
            if _flag_on("new_tax_engine", ts) and rng.random() < 0.2:
                flags.append("new_tax_engine")

            # --- 1) tax calculation (tax-service -> external tax provider)
            upstream = "taxly-eu" if region == "eu-west-1" else "taxly-us"
            taxly_eu_degraded = T("2026-10-24T18:12") <= ts < T("2026-10-24T20:05")
            tax_ok = False
            t_cursor = ts
            for attempt in range(1, 4):  # tax-service retries the provider up to 3 times
                if upstream == "taxly-eu" and taxly_eu_degraded and rng.random() < 0.78:
                    lat = rng.randint(5100, 8900)
                    trace(t_cursor, tenant, tier, "tax-service", "/v1/checkout/tax", 504, lat, platform,
                          client_ver, region, flags, upstream, corr, "tax_provider_timeout", attempt)
                    t_cursor += timedelta(milliseconds=lat)
                    continue
                lat = int(rng.lognormvariate(math.log(110), 0.35))
                status, err = (200, "")
                if rng.random() < 0.002:
                    status, err = (500, "internal_error")
                trace(t_cursor, tenant, tier, "tax-service", "/v1/checkout/tax", status, lat, platform,
                      client_ver, region, flags, upstream, corr, err, attempt)
                tax_ok = status == 200
                break

            # --- 2) payment confirmation (checkout-service -> stripe)
            status_b, fail_code = "paid", ""
            if not tax_ok:
                status_b, fail_code = "failed", "tax_calculation_failed"
            else:
                c_ts = t_cursor + timedelta(seconds=rng.randint(2, 20))
                lat = int(rng.lognormvariate(math.log(240), 0.3))
                if "stripe_intent_v2" in flags and rng.random() < 0.94:
                    st, err = 422, "payment_intent_unprocessable"
                elif rng.random() < 0.022:
                    st, err = 402, "card_declined"
                elif rng.random() < 0.003:
                    st, err = 502, "upstream_error"
                else:
                    st, err = 200, ""
                trace(c_ts, tenant, tier, "checkout-service", "/v1/checkout/confirm", st, lat, platform,
                      client_ver, region, flags, "stripe", corr, err)
                if st != 200:
                    status_b, fail_code = "failed", err
            events.append((f"evt-{seq['c']:07d}", corr, tenant, iso(t_cursor + timedelta(seconds=25)), amount,
                           "USD", status_b, fail_code, OS_BILLING[platform], client_ver,
                           REGION_BILLING_CODE[region], "storefront"))

        # --- enterprise bulk order sync through the api-gateway
        for tenant, (region, rate) in ENTERPRISE.items():
            if not _sync_active(tenant, minute):
                continue
            limit = int(_config_value("api-gateway", "rate_limit.enterprise_burst_per_min", minute, "500"))
            calls = _poisson(rng, rate)
            for k in range(calls):
                ts = minute + timedelta(seconds=rng.randint(0, 59))
                seq["c"] += 1
                corr = f"ord-{seq['c']:07d}"
                over = k >= limit or (calls > limit and rng.random() < (calls - limit) / calls)
                lat = int(rng.lognormvariate(math.log(180), 0.3))
                if over:
                    trace(ts, tenant, "enterprise", "api-gateway", "/v1/orders/bulk", 429, 3, "server", "",
                          region, [], "", corr, "rate_limited")
                    continue
                st, err = (200, "") if rng.random() > 0.002 else (503, "unavailable")
                trace(ts, tenant, "enterprise", "api-gateway", "/v1/orders/bulk", st, lat, "server", "",
                      region, [], "", corr, err)
                if st == 200:
                    amount = int(max(2500, rng.lognormvariate(math.log(14000), 0.5)))
                    events.append((f"evt-{seq['c']:07d}", corr, tenant, iso(ts + timedelta(seconds=3)), amount,
                                   "USD", "paid", "", "API", "", REGION_BILLING_CODE[region], "api_bulk"))
        minute += timedelta(minutes=step_minutes)

    telem.executemany("INSERT INTO api_traces VALUES (" + ",".join("?" * 17) + ")", traces)
    billing.executemany("INSERT INTO checkout_events VALUES (" + ",".join("?" * 12) + ")", events)
    for con in (billing, telem, ops):
        con.commit()
        con.close()
    marker.write_text(f"seed={SEED}\n", encoding="utf-8")
    return {"status": "generated", "silo_dir": str(silo_dir), "traces": len(traces), "billing_events": len(events)}


def _sync_active(tenant: str, minute: datetime) -> bool:
    h, m = minute.hour, minute.minute
    if tenant == "acme-logistics":
        return (2 <= h < 4) or (8 <= h <= 23 and m < 5)  # nightly bulk + hourly incremental
    if tenant == "orbit-freight":
        return (h == 3 and m < 30) or (h % 2 == 0 and 6 <= h <= 22 and m < 4)
    if tenant == "helio-health":
        return 7 <= h <= 21 and m < 6
    return False


def _poisson(rng: random.Random, lam: float) -> int:
    # Knuth's algorithm; lam is small here.
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data" / "silos"
    print(generate(target, force=True))
