"""Multi-Agent State-Machine Orchestrator for IncidentWhy.

Coordinates the cyclic loop:
    PlannerAgent  <->  ExecutorAgent  <->  EvaluatorAgent
                           |
                           v  (once root cause is verified)
                   SynthesizerAgent  <->  FinalReviewerAgent

Backed by:
  - ZetarisAdapter (federated SQL across `billing`, `telemetry`, and `ops` silos)
  - MeterlessCognitionEngine (World Model Engine + H-MEM Engine)
  - ModelRouter (NVIDIA Nemotron `reasoning` and `fast` lanes)
  - Tracer (JSONL traces written to `logs/trace-<run_id>.jsonl` and `logs/agent_trace.jsonl`)
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from app.adapters.meterless_memory import MeterlessCognitionEngine
from app.adapters.zetaris import ZetarisAdapter
from app.agents.evaluator import EvaluatorAgent
from app.agents.executor import ExecutorAgent
from app.agents.planner import PlannerAgent
from app.agents.synthesizer import SynthesizerAgent
from app.config import Settings, get_settings
from app.llm import ModelRouter
from app.models import Hypothesis, IncidentAlert
from app.sample_data import generate as ensure_silos_seeded
from app.tracing import Tracer

ACTIVE_RUNS: dict[str, dict[str, Any]] = {}


def run_investigation(
    payload: dict[str, Any],
    settings: Settings | None = None,
    tracer: Tracer | None = None,
    echo_logs: bool = True,
) -> dict[str, Any]:
    """Execute a complete multi-agent root-cause investigation and return a
    structured JSON response suitable for both programmatic judges (`POST /run`)
    and the interactive War-Room UI (`POST /api/investigate`).
    """
    cfg = settings or get_settings()
    ensure_silos_seeded(cfg.silo_dir, force=False)

    alert = IncidentAlert.from_payload(payload)
    run_id = str(
        payload.get("run_id")
        or payload.get("request_id")
        or payload.get("incident_id")
        or f"run-{uuid.uuid4().hex[:8]}"
    )
    t0 = time.monotonic()
    tr = tracer or Tracer(run_id=run_id, log_dir=cfg.log_dir, echo=echo_logs)
    ACTIVE_RUNS[run_id] = {"status": "running", "tracer": tr, "alert": alert.to_dict()}

    try:
        llm = ModelRouter(settings=cfg, tracer=tr)
        zetaris = ZetarisAdapter(settings=cfg, tracer=tr, agent="ExecutorAgent")

        # Injected generator function passed to Meterless H-MEM (Meterless portability rule)
        def injected_generator(prompt: str, system_prompt: str) -> str | None:
            return llm.generate("fast", system_prompt, prompt, max_tokens=160)

        meterless = MeterlessCognitionEngine(
            run_id=run_id,
            state_dir=cfg.state_dir,
            log_dir=cfg.log_dir,
            tracer=tr,
            generator=injected_generator if llm.available else None,
        )

        tr.emit(
            "Orchestrator",
            "investigation_started",
            f"incident_id={alert.incident_id} scenario={alert.scenario_key}",
            f"Hydrated Meterless H-MEM ({len(meterless.hmem_store.memories)} warm memories) & opened World Model",
            target_agent="PlannerAgent",
            status="success",
            sample_mode=cfg.sample_mode,
            zetaris_backend=zetaris.backend,
            nvidia_llm_available=llm.available,
        )
        meterless.register_incident(
            incident_id=alert.incident_id,
            title=alert.title,
            started_at=alert.started_at,
            symptom_summary=alert.symptom_summary,
        )

        planner = PlannerAgent(zetaris=zetaris, meterless=meterless, llm=llm, tracer=tr)
        executor = ExecutorAgent(zetaris=zetaris, meterless=meterless, llm=llm, tracer=tr)
        evaluator = EvaluatorAgent(zetaris=zetaris, meterless=meterless, llm=llm, tracer=tr)
        synthesizer = SynthesizerAgent(meterless=meterless, llm=llm, tracer=tr)

        all_hypotheses: list[Hypothesis] = []
        rounds_history: list[dict[str, Any]] = []

        # --- Round 1: Initial plan -> SQL execution -> Adversarial evaluation
        r1_hyps, ops_surface = planner.create_initial_plan(alert)
        all_hypotheses.extend(r1_hyps)

        exec_bundle = executor.execute_round(alert, all_hypotheses, round_num=1)
        eval_bundle = evaluator.evaluate_round(alert, all_hypotheses, exec_bundle, round_num=1)
        rounds_history.append({"round": 1, "execution": exec_bundle, "evaluation": eval_bundle})

        # --- Feedback / Critique Loop (Round 2 .. max_rounds)
        round_num = 1
        while eval_bundle["needs_revision"] and round_num < cfg.max_rounds:
            round_num += 1
            new_hyps = planner.revise_plan(
                alert=alert,
                prior_hypotheses=all_hypotheses,
                evaluator_feedback=eval_bundle,
                ops_surface=ops_surface,
                round_num=round_num,
            )
            all_hypotheses.extend(new_hyps)
            exec_bundle = executor.execute_round(alert, all_hypotheses, round_num=round_num)
            eval_bundle = evaluator.evaluate_round(alert, all_hypotheses, exec_bundle, round_num=round_num)
            rounds_history.append({"round": round_num, "execution": exec_bundle, "evaluation": eval_bundle})

        # --- Synthesis & Reviewer Critique Loop
        report_brief = synthesizer.synthesize_and_review(
            alert=alert,
            hypotheses=all_hypotheses,
            financial_impact=eval_bundle["financial_impact"],
        )

        elapsed_s = round(time.monotonic() - t0, 3)
        tr.emit(
            "Orchestrator",
            "investigation_completed",
            f"Completed {round_num} investigative round(s) + reviewer loop",
            f"Root cause verified: {report_brief['verified_root_cause']['suspected_change_id']} in {elapsed_s}s",
            status="success",
            rounds=round_num,
            execution_time_seconds=elapsed_s,
        )
        tr.close()

        result = {
            "run_id": run_id,
            "request_id": run_id,
            "incident_id": alert.incident_id,
            "trace_id": f"trace-{run_id}",
            "status": "success",
            "track": "The Agent That Can Explain Why",
            "execution_time_seconds": elapsed_s,
            "log_file": f"logs/trace-{run_id}.jsonl",
            "log_path": f"logs/trace-{run_id}.jsonl",
            "agents_used": [
                "PlannerAgent",
                "ExecutorAgent",
                "EvaluatorAgent",
                "SynthesizerAgent",
                "FinalReviewerAgent",
            ],
            "agents": [
                {"name": "PlannerAgent", "role": "Decomposes incident into hypotheses and revises plan when Evaluator falsifies red herrings"},
                {"name": "ExecutorAgent", "role": "Executes cross-silo Zetaris SQL queries with live schema error recovery"},
                {"name": "EvaluatorAgent", "role": "Adversarial SRE critic running temporal precedence, contract ledger, cohort z-test & citation falsification"},
                {"name": "SynthesizerAgent", "role": "Drafts the cited Root-Cause & Financial Impact Brief"},
                {"name": "FinalReviewerAgent", "role": "Audits brief for correlation-vs-causation caveats and post-remediation verification SQL"},
            ],
            "collaboration": {
                "feedback_loop_completed": True,
                "investigative_rounds": round_num,
                "hypotheses_tested": len(all_hypotheses),
                "hypotheses_falsified": sum(1 for h in all_hypotheses if h.status == "FALSIFIED"),
                "hypotheses_verified": sum(1 for h in all_hypotheses if h.status == "VERIFIED"),
                "sql_schema_recoveries": len(executor.schema_recoveries),
                "reviewer_revision_count": report_brief.get("reviewer_revisions", 1),
                "evaluator_findings": [
                    h.falsification_reason for h in all_hypotheses if h.status == "FALSIFIED" and h.falsification_reason
                ],
            },
            "sponsor_technology": {
                "zetaris": {
                    "backend": zetaris.backend,
                    "catalogs": zetaris.catalogs,
                    "queries_executed": len(zetaris.query_log),
                    "schema_recoveries": executor.schema_recoveries,
                    "query_log": zetaris.query_log,
                },
                "meterless": meterless.snapshot_summary(),
                "nvidia_nemotron": {
                    "reasoning_lane_model": cfg.model_reasoning,
                    "fast_lane_model": cfg.model_fast,
                    "live_api_configured": llm.available,
                    "calls": llm.calls,
                },
                "cursor": {
                    "role": "Primary AI development environment used for multi-agent architecture, SQL federation adapter, and test suite",
                },
            },
            "alert": alert.to_dict(),
            "rounds_completed": round_num,
            "hypotheses": [h.to_dict() for h in all_hypotheses],
            "verified_root_cause": report_brief["verified_root_cause"],
            "falsified_hypotheses": report_brief["falsified_red_herrings"],
            "financial_impact": report_brief["financial_impact"],
            "executive_summary": report_brief["executive_summary"],
            "remediation_plan": report_brief["remediation_plan"],
            "post_remediation_verification_sql": report_brief.get("post_remediation_verification_sql"),
            "limitations_and_caveats": report_brief.get("limitations_and_caveats", []),
            "output": {
                "summary": report_brief["executive_summary"],
                "verified_root_cause": report_brief["verified_root_cause"],
                "falsified_red_herrings": report_brief["falsified_red_herrings"],
                "financial_impact": report_brief["financial_impact"],
                "recommendations": report_brief["remediation_plan"],
                "post_remediation_verification_sql": report_brief.get("post_remediation_verification_sql"),
                "limitations_and_caveats": report_brief.get("limitations_and_caveats", []),
            },
            "result": report_brief,
            "trace_events": tr.events,
        }
        ACTIVE_RUNS[run_id] = {"status": "completed", "tracer": tr, "result": result}
        return result
    except Exception as exc:
        tr.emit("Orchestrator", "investigation_failed", str(alert.incident_id), str(exc), status="error")
        tr.close()
        err_payload = {
            "run_id": run_id,
            "request_id": run_id,
            "status": "error",
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "recoverable": True,
            },
            "trace_id": f"trace-{run_id}",
            "log_file": f"logs/trace-{run_id}.jsonl",
            "execution_time_seconds": round(time.monotonic() - t0, 3),
        }
        ACTIVE_RUNS[run_id] = {"status": "error", "tracer": tr, "result": err_payload}
        return err_payload
