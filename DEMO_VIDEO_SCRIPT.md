# Axio — 3-Minute Demo Video Script (`DEMO_VIDEO_SCRIPT.md`)

> **Target Length**: 2 minutes 50 seconds (strictly under the 3:00 limit)  
> **Format**: Direct live product demonstration (no slide fluff; open straight into the War-Room UI + live terminal).

---

## 0:00 – 0:25 | The 15-Second Hook & The Murder Mystery (`25s`)
- **[Screen]**: Split screen — Left 75%: **Axio 3-Panel War-Room Dashboard** (`http://localhost:8000`); Right 25%: terminal tailing `logs/agent_trace.jsonl`.
- **[Narration (Jezreal)]**:
  > "War rooms waste hours debating false leads. **Axio** uses mathematical falsification to eliminate the decoys, proving the real root cause and exact dollar leak in seconds. War rooms argue over theories; **Axio** leaves them with axioms.
  > Take `INC-8891`: at 14:05 UTC, storefront checkout conversion drops 38%, burning nearly ten thousand dollars an hour. In a normal war room, engineers immediately blame the latest deployment—`checkout-service v3.4.2`—and waste an hour rolling it back. Today we're showing **Axio**, an adversarial 5-agent investigator built on **Zetaris**, **Meterless**, and **NVIDIA Nemotron** that disproves war-room decoys across Billing, Telemetry, and Ops silos."

---

## 0:25 – 1:15 | Live Run: Round 1 Falsification & SQL Schema Recovery (`50s`)
- **[Action]**: Click **`INC-8891: Silent Checkout Conversion Drop`** on the Left Panel, keep **"Inject Round-1 Cross-Silo SQL Schema Mismatch"** checked, and click **▶ Run Adversarial Investigation**.
- **[Screen]**: Watch the Right Panel SSE trace stream light up (`PlannerAgent` → `ExecutorAgent` → `RECOVERED` → `EvaluatorAgent` → `NEEDS_REVISION`) and the Center Panel Hypothesis Falsification cards appear.
- **[Narration (Victor)]**:
  > "Watch the live trace stream on the right. First, `PlannerAgent` on **NVIDIA Nemotron Super-49B** proposes two Round-1 suspects from the `{ops}` silo: deployment `dep-1005` (`v3.4.2`) and timeout config `cfg-301`.
  > Next, `ExecutorAgent` on **Nemotron Nano-9B** queries our **Zetaris** federated catalogs. Notice the amber `TOOL_ERROR_RECOVERY` event: `ExecutorAgent` initially queries `{billing}.checkout_events` using telemetry column names (`timestamp` and `platform`), catches the SQL error, inspects the table schema via Zetaris, repairs the columns to `created_at` and `device_os`, and saves a **procedural memory** into **Meterless H-MEM** so Round 2 never repeats that mistake.
  > Now look at the two red cards in the center: `EvaluatorAgent` **falsifies** deployment `dep-1005` for a **Temporal Precedence Violation**—it deployed at 14:20 UTC, 15 minutes *after* 31 `HTTP 422` errors had already fired! And it **falsifies** `cfg-301` because Android and Web checkouts sharing that timeout had a `0.00%` failure rate."

---

## 1:15 – 2:00 | Round 2 Verification, Reviewer Critique Loop & Zetaris SQL (`45s`)
- **[Action]**: Scroll to the green **`H3 · VERIFIED ROOT CAUSE`** card (`flg-502`), then click the **Root-Cause & Dollar-Impact Brief** tab and the **Zetaris SQL & Schema Recovery** tab.
- **[Narration (Jezreal)]**:
  > "Forced back to the drawing board by `EvaluatorAgent`'s rejection, `PlannerAgent` queries uninspected feature flags in Round 2 and tests `flg-502`—`stripe_intent_v2` rolled out at 14:04 UTC—one minute before the crash.
  > `EvaluatorAgent` runs a two-proportion z-test across Zetaris telemetry and billing rows: in-cohort failure rate on iOS v2.1.0 is **92.4% (`121/131`) versus 0.00% (`0/361`) outside the cohort**—a risk ratio of **1,847x** with $z = 21.03$ and $p = 3.61 \times 10^{-98}$, confirmed by an independent Evaluator SQL check and **$10,732.46** in deduplicated lost revenue.
  > Before publishing, `FinalReviewerAgent` critiques `SynthesizerAgent`'s first draft (`NEEDS_REVISION`), enforcing observational-vs-causal caveats and a post-remediation Zetaris verification SQL query."

---

## 2:00 – 2:50 | Scenario 2 & 3 Financial Audit + Meterless Inspection (`50s`)
- **[Action]**: Click **`INC-8892 (Enterprise Tenant Order-Sync Collapse)`**, run it (`~1s`), point out the `Contract & Ledger Contradiction` on `tkt-7003`, then click **`INC-8893 (EU Checkout Latency & Retry Storm Leak)`**, run it, and click the **Meterless World Model & H-MEM** tab.
- **[Narration (Victor)]**:
  > "This same adversarial loop generalizes across incidents. In `INC-8892`, Customer Success blames a pricing complaint ticket (`tkt-7003`) for an 80% order drop. `EvaluatorAgent` joins `{billing}.subscriptions` and `{billing}.plan_changes`, proves there were zero price changes, and isolates `cfg-302`—an automated gateway burst-limit drop from 500 to 50 requests per minute causing `HTTP 429` throttling.
  > And in `INC-8893`, during an EU tax-provider `504` timeout storm, a naive trace-to-billing join over-reports lost revenue at **$24,415.46** because each checkout retried 3 times. `EvaluatorAgent` deduplicates by `billing.checkout_events.event_id` to report the true net loss of **$9,084.40**—catching a **2.69x retry inflation**.
  > Finally, in the **Meterless** tab and `/api/world-model` and `/api/memory` endpoints, judges can inspect the persisted World Model entities, H-MEM procedural memories, Trust Ledger, and `wired_vs_helper_manifest`. Everything runs out-of-the-box with `SAMPLE_MODE=true` via `python3 run.py` or `POST /run`. Thank you!"
