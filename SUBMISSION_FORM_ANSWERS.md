# HackOS Submission Portal — Copy-Paste Answers (`SUBMISSION_FORM_ANSWERS.md`)

> Prepared against `demo and submission resources/submission-form-preview.md` and `demo and submission resources/strong-submission-examples.md`.

---

## 1. Project Title
**Axio — Cross-Silo Root-Cause & Silent Revenue Leak Investigator**

## 2. Track Selection
**Track 2: The Agent That Can Explain Why**

## 3. Short Tagline (1–2 sentences)
War rooms waste hours debating false leads; **Axio** uses mathematical falsification across Billing, Telemetry, and Ops silos (**Zetaris Federated SQL** + **Meterless World Model & H-MEM** + **NVIDIA Nemotron**) to eliminate the decoys, proving the real root cause and exact deduplicated dollar leak in seconds. War rooms argue over theories; Axio leaves them with axioms.

## 4. Problem & Use Case Description
When production systems experience silent revenue or conversion drops, engineering and revenue war rooms routinely waste hours chasing plausible red herrings: rolling back recent deployments that actually shipped *after* the anomaly started, escalating customer "pricing" complaints when an API gateway rate-limit change is silently returning `HTTP 429`, or over-reporting lost revenue by 2.69x because naive telemetry-to-billing joins multiply retried `504` spans.

**Axio** solves this by treating root-cause analysis as **adversarial hypothesis falsification** rather than log summarization. Across three seeded multi-silo incidents (`INC-8891`, `INC-8892`, `INC-8893`), Axio queries 58,000+ rows across `{billing}`, `{telemetry}`, and `{ops}` catalogs, rejects 6 red-herring hypotheses using deterministic temporal precedence, contract ledger checks, and two-proportion z-tests ($p < 10^{-6}$), recovers automatically from cross-silo SQL schema mismatches, and produces a primary-key-cited Root-Cause & Dollar-Impact Brief with post-remediation verification SQL.

## 5. Multi-Agent Architecture & Collaboration Loop
**Axio** orchestrates **5 specialized agents** across two active feedback/critique loops:
1. **`PlannerAgent` (`nvidia/llama-3.3-nemotron-super-49b-v1` reasoning lane)**: Inspects the `{ops}` change surface (`deployments`, `flag_changes`, `config_changes`, `support_tickets`), formulates Round-1 hypotheses, and revises the investigative plan in Round 2 after `EvaluatorAgent` rejects Round-1 red herrings (`NEEDS_REVISION`).
2. **`ExecutorAgent` (`nvidia/nvidia-nemotron-nano-9b-v2` fast lane)**: Executes federated SQL across `{billing}`, `{telemetry}`, and `{ops}` via `ZetarisAdapter`. In Round 1, when encountering a cross-silo column mismatch on `{billing}.checkout_events` (`timestamp`/`platform` vs `created_at`/`device_os`), it catches the error, inspects table schema via `describe()`, repairs the SQL (`TOOL_ERROR_RECOVERY`), and stores a procedural memory in **Meterless H-MEM** so Round 2 executes cleanly without repeating the error.
3. **`EvaluatorAgent` (`nvidia/llama-3.3-nemotron-super-49b-v1` reasoning lane)**: Adversarial SRE & FinOps critic. Runs independent Zetaris verification SQL and enforces 4 deterministic falsification checks: (a) Temporal Precedence ($\text{cause\_ts} \le \text{onset\_ts}$), (b) Contract & Ledger Contradiction, (c) Cohort Isolation Two-Proportion Z-Test & Risk Ratio ($z > 3.29, p < 0.001$), and (d) Cross-Silo Primary-Key Citation & Retry-Inflation Financial Deduplication (`COUNT(DISTINCT billing.checkout_events.event_id)`).
4. **`SynthesizerAgent` (`nvidia/llama-3.3-nemotron-super-49b-v1` reasoning lane)**: Compiles the verified root-cause mechanism, falsified red-herring proofs, deduplicated financial loss, and prioritized remediation runbook.
5. **`FinalReviewerAgent` (`nvidia/nvidia-nemotron-nano-9b-v2` fast lane)**: Audits `SynthesizerAgent`'s initial draft, triggers a `NEEDS_REVISION` critique loop when observational-vs-causal caveats or post-remediation verification SQL are absent, and approves the final brief.

## 6. Sponsor Technologies Used & How They Were Integrated
- **Zetaris (`app/adapters/zetaris.py`)**: Implements the Zetaris REST SQL execution contract (`POST /sql-editor/sqls/run` with `X-Org-ID`, `X-Request-ID`, and `Authorization` headers) across `{billing}`, `{telemetry}`, and `{ops}` catalogs, paired with an offline `SAMPLE_MODE=true` SQLite federation backend (`billing.db`, `telemetry.db`, `ops.db`) and live schema introspection/recovery (`query_with_schema_recovery()`).
- **Meterless (`app/adapters/meterless_memory.py`)**: Implements both headless Meterless engines:
  - **World Model Agent Engine**: `WorldStoreService`, `IngestPipelineService`, `ContextBuilderService` (with explicit `wired_vs_helper_manifest`), and `ControlPlaneService` (`GET /api/world-model`), tracking continuity anchors (`incident:INC-8891@2026-10-22T14:05:00Z`) and hypothesis transitions (`TESTING` → `FALSIFIED` / `VERIFIED`).
  - **H-MEM Agent Engine**: `MemoryStoreService` (`short_term`, `working`, `long_term` layers across `episodic`, `semantic`, `procedural`, and `factual` types), `MemoryMiningService` (accepting an injected generator function with no-model fallback), `MemoryRetrievalService`, and `TrustLedgerService` (`GET /api/memory`).
- **NVIDIA Nemotron (`app/llm.py`)**: Dual-lane OpenAI-compatible router (`https://integrate.api.nvidia.com/v1`) pairing `nvidia/llama-3.3-nemotron-super-49b-v1` (reasoning lane) and `nvidia/nvidia-nemotron-nano-9b-v2` (fast lane) with deterministic offline fallback.
- **Cursor**: AI-native IDE used to design, implement, and verify the 5-agent cyclic state machine, Zetaris SQL federation adapter, and automated test suite.

## 7. Programmatic Execution & Verification Instructions for Judges
```bash
# 1. Start server (SAMPLE_MODE=true by default; zero external API keys required)
cp .env.example .env
python3 run.py

# 2. Health check
curl -s http://localhost:8000/health | python3 -m json.tool

# 3. Execute investigation via POST /run
curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d @input_examples/incident_01_checkout_drop.json | python3 -m json.tool

# 4. Run automated unit tests & pre-submission audit
python3 -m unittest discover -s tests -v
python3 scripts/preflight_check.py
```

## 8. GitHub Repository URL (Public)
`https://github.com/jezreal-dev/axio`

## 9. Team Members (Team Agentic Army)
- **Jezreal Momoh** (`jezreal-dev`) — Backend Developer, Full Stack Developer, Scrum Master, Technical Writer *(Primary Contact)*
- **Victor Okpe** — Full Stack Developer, ML/AI Engineer, Mobile Developer

## 10. Pre-Existing Components Declaration
None — all product code, multi-agent orchestration, Zetaris and Meterless adapters, deterministic data generators, and War-Room UI were built from scratch during the Open Agent Hackathon 2026 build window (22–27 October 2026).
