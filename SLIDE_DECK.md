# Axio — 8-Slide Hackathon Pitch Deck (`SLIDE_DECK.md`)

> **Track 2**: *The Agent That Can Explain Why*  
> **Team Agentic Army**: Jezreal Momoh & Victor Okpe  
> **Sponsors Integrated**: Zetaris · Meterless · NVIDIA Nemotron · Cursor  
> **Interactive PDF-Exportable Deck**: Start `python3 run.py` and open `http://localhost:8000/slides` (press `Ctrl+P` → **Save as PDF**).

---

## Slide 1 — Title & Hook
- **Title**: **Axio** — The Adversarial Multi-Agent Investigator That Falsifies War-Room Red Herrings
- **Subtitle**: Cross-Silo Root-Cause & Silent Revenue Leak Investigator (*War rooms argue over theories; Axio leaves them with axioms*)
- **15-Second Hook**: *"War rooms waste hours debating false leads. **Axio** uses mathematical falsification to eliminate the decoys, proving the real root cause and exact dollar leak in seconds."*
- **Team**: Agentic Army — Jezreal Momoh (Backend/Full-Stack/Scrum/Tech Writer) & Victor Okpe (Full-Stack/ML-AI Engineer)

---

## Slide 2 — The Problem: War-Room Red Herrings & Siloed Blind Spots
- **Silo Fragmentation**:
  - **Billing Silo** (`Stripe / ERP`) knows *how many dollars* are failing and what contracts tenants are on.
  - **Telemetry Silo** (`APM / API Gateway`) knows *which endpoints, regions, and client versions* are throwing HTTP `422` / `429` / `504`.
  - **Ops Silo** (`GitHub / LaunchDarkly / Terraform / Zendesk`) knows *what shipped, what flags flipped, and what customers complained about*.
- **Why Existing AI Copilots Fail**:
  1. **Recency Bias**: They blame the latest deployment (`dep-1005` at `14:20Z`) even when errors started at `14:05Z` (15 minutes earlier).
  2. **Narrative Bias**: They believe an angry support ticket (`tkt-7003` "pricing hike!") without checking the billing contract ledger (`0` price changes) or API gateway (`2,768` HTTP `429` rate-limit errors).
  3. **Retry-Storm Financial Inflation**: Naive joins across retried telemetry spans inflate reported revenue loss by **2.69x**.

---

## Slide 3 — Our Solution: Adversarial Hypothesis Falsification
- **IncidentWhy** replaces single-pass RAG summarization with a **5-Agent Cyclic Falsification Loop**:
  1. **`PlannerAgent` (Nemotron Super-49B)**: Inspects the `{ops}` change surface and proposes competing Round-1 hypotheses.
  2. **`ExecutorAgent` (Nemotron Nano-9B)**: Runs federated SQL across `{billing}`, `{telemetry}`, and `{ops}` via **Zetaris**, automatically recovering from cross-silo schema mismatches.
  3. **`EvaluatorAgent` (Nemotron Super-49B)**: Adversarial SRE & FinOps critic that tries to **disprove** every hypothesis using 4 deterministic rules:
     - **Temporal Precedence** ($\text{cause\_ts} \le \text{onset\_ts}$)
     - **Contract & Ledger Contradiction**
     - **Cohort Isolation Two-Proportion Z-Test** ($z > 3.29, p < 0.001$)
     - **Cross-Silo Primary-Key Citation & Retry Deduplication**
  4. **`SynthesizerAgent` ↔ `FinalReviewerAgent`**: Compiles the cited brief and enforces causal caveats + post-remediation verification SQL.

---

## Slide 4 — Load-Bearing Sponsor Architecture
- **Zetaris (Networked Data Platform for AI)**:
  - Executes federated cross-silo SQL across `{billing}`, `{telemetry}`, and `{ops}` via `/sql-editor/sqls/run` (`X-Org-ID`, `X-Request-ID`) with full offline parity across 58,000+ rows in `SAMPLE_MODE=true`.
- **Meterless (World Model + H-MEM Agent Engines)**:
  - **World Model Engine**: Persists entity continuity (`incident:INC-8891@2026-10-22T14:05:00Z`) and hypothesis state transitions (`TESTING` → `FALSIFIED` → `VERIFIED`) with explicit `wired_vs_helper_manifest`.
  - **H-MEM Engine**: Stores procedural schema-repair memories (`timestamp` → `created_at`) so Round 2 never repeats a Round 1 SQL error, backed by an append-only `TrustLedgerService`.
- **NVIDIA Nemotron (Dual-Lane Model Router)**:
  - Pairs `llama-3.3-nemotron-super-49b-v1` (reasoning lane) with `nvidia-nemotron-nano-9b-v2` (fast lane).
- **Cursor**:
  - AI-native IDE used to build and verify the 5-agent cyclic state machine and test suite.

---

## Slide 5 — Live Murder-Mystery Walkthrough (`INC-8891`)
- **The Alert**: Storefront checkout conversion drops at `14:05 UTC`. On-call suspects `checkout-service v3.4.2` (`dep-1005`).
- **Round 1 (Red Herrings Falsified)**:
  - `ExecutorAgent` hits a column mismatch on `{billing}.checkout_events`, inspects schema via Zetaris `describe()`, repairs the SQL (`TOOL_ERROR_RECOVERY`), and saves a procedural memory in Meterless H-MEM.
  - `EvaluatorAgent` **falsifies `dep-1005`** (deployed at `14:20Z` — 15 minutes *after* 31 `HTTP 422` errors already occurred) and **falsifies `cfg-301`** (`0.00%` failure rate on Android/Web).
- **Round 2 (True Root Cause Verified)**:
  - `PlannerAgent` pivots to uninspected feature flags and proposes `flg-502` (`stripe_intent_v2=true` at `14:04Z` on `ios v2.1.0`).
  - `EvaluatorAgent` verifies `flg-502`: **92.4% failure rate in-cohort (`121/131`) vs 0.00% (`0/361`) out-of-cohort** ($z = 21.03, p = 3.61 \times 10^{-98}, \text{risk\_ratio} = 1847.33\times$).
  - Exact deduplicated revenue loss: **`$10,732.46`** (`$9,906.89/hr`).

---

## Slide 6 — Three Verified Scenarios & Financial Accuracy
| Incident | Falsified Red Herrings | Verified Root Cause | Financial Audit Outcome |
|----------|------------------------|---------------------|-------------------------|
| **`INC-8891`**: Checkout Drop | `dep-1005` (Temporal), `cfg-301` (Cohort) | `flg-502` (`stripe_intent_v2` on iOS v2.1.0) | **`$10,732.46`** net loss across 121 failed checkouts |
| **`INC-8892`**: Enterprise Sync Collapse | `tkt-7003` (Contract Ledger), `dep-1007` (Temporal) | `cfg-302` (`rate_limit` lowered `500 -> 50/min`) | **`$177,236.46`** net unfulfilled bulk order GMV |
| **`INC-8893`**: EU Tax Timeout Leak | `flg-505` (Temporal), `dep-1011` (Cohort) | `upstream-taxly-eu-504` (`eu-west-1` gateway 504s) | Caught **2.69x retry inflation** (`$24,415.46` gross → **`$9,084.40`** net) |

---

## Slide 7 — Production Engineering & Traceability
- **Zero-Friction Judge Execution**:
  - `SAMPLE_MODE=true` by default; `python3 run.py` boots on `0.0.0.0:8000` in `< 2 seconds` with zero external API keys required.
  - Exposes `POST /run`, `POST /api/investigate`, `GET /api/stream/{run_id}` (SSE), `GET /api/world-model`, `GET /api/memory`, and `GET /health`.
- **Full JSONL Observability**:
  - Every agent handoff, SQL query, schema recovery, falsification check, and reviewer critique is logged to `logs/trace-<run_id>.jsonl` and `logs/agent_trace.jsonl`.

---

## Slide 8 — Real-World Impact & Commercial Roadmap
- **Why Engineering & FinOps Teams Adopt IncidentWhy**:
  - Cuts Mean-Time-To-Resolution (MTTR) by eliminating wasted rollbacks on innocent deployments.
  - Prevents false customer-churn diagnoses by reconciling support tickets against live billing contracts and gateway telemetry.
  - Gives CFOs and VPs of Engineering a single, deduplicated dollar-impact number backed by primary-key SQL citations.
