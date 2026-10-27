(() => {
  const state = {
    scenarios: [],
    selectedScenario: null,
    activeStream: null,
    traceCount: 0,
    lastResult: null,
  };

  const els = {
    scenarioList: document.getElementById("scenarioList"),
    customQueryInput: document.getElementById("customQueryInput"),
    injectMismatchToggle: document.getElementById("injectMismatchToggle"),
    runInvestigationBtn: document.getElementById("runInvestigationBtn"),
    runBtnLabel: document.getElementById("runBtnLabel"),
    kpiRootCause: document.getElementById("kpiRootCause"),
    kpiRootSub: document.getElementById("kpiRootSub"),
    kpiFalsified: document.getElementById("kpiFalsified"),
    kpiFalsifiedSub: document.getElementById("kpiFalsifiedSub"),
    kpiNetLoss: document.getElementById("kpiNetLoss"),
    kpiLossSub: document.getElementById("kpiLossSub"),
    kpiSqlCount: document.getElementById("kpiSqlCount"),
    kpiRecoverySub: document.getElementById("kpiRecoverySub"),
    hypothesesContainer: document.getElementById("hypothesesContainer"),
    briefContainer: document.getElementById("briefContainer"),
    sqlContainer: document.getElementById("sqlContainer"),
    meterlessContainer: document.getElementById("meterlessContainer"),
    traceStream: document.getElementById("traceStream"),
    traceCountBadge: document.getElementById("traceCountBadge"),
    traceFileLabel: document.getElementById("traceFileLabel"),
    pillMeterless: document.getElementById("pillMeterless"),
    pillNvidia: document.getElementById("pillNvidia"),
    pillSampleMode: document.getElementById("pillSampleMode"),
  };

  function escapeHtml(val) {
    return String(val ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatUsd(num) {
    const n = Number(num || 0);
    return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // Tab switching
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      const target = document.getElementById(btn.dataset.tab);
      if (target) target.classList.add("active");
    });
  });

  async function init() {
    try {
      const [healthRes, scenRes] = await Promise.all([
        fetch("/health").then((r) => r.json()),
        fetch("/api/scenarios").then((r) => r.json()),
      ]);
      if (healthRes && healthRes.config) {
        els.pillSampleMode.textContent = `SAMPLE_MODE=${healthRes.config.sample_mode}`;
      }
      if (healthRes && healthRes.meterless) {
        els.pillMeterless.innerHTML = `<strong>Meterless</strong> · ${healthRes.meterless.world_model_entities} entities · ${healthRes.meterless.hmem_memories} H-MEM`;
      }
      state.scenarios = scenRes.scenarios || [];
      renderScenarioList();
      if (state.scenarios.length > 0) {
        selectScenario(state.scenarios[0]);
        // Automatically run Scenario 1 on initial load so the dashboard is immediately populated
        runInvestigation();
      }
    } catch (err) {
      console.error("Initialization error:", err);
    }
  }

  function renderScenarioList() {
    els.scenarioList.innerHTML = state.scenarios
      .map((s) => {
        const isSel = state.selectedScenario && state.selectedScenario.scenario_id === s.scenario_id;
        return `
          <div class="scenario-card ${isSel ? "active" : ""}" data-id="${escapeHtml(s.scenario_id)}">
            <div class="scenario-top">
              <span class="scenario-id-badge">${escapeHtml(s.incident_id)}</span>
              <span class="scenario-window">${escapeHtml((s.started_at || "").slice(5, 16).replace("T", " "))} UTC</span>
            </div>
            <div class="scenario-title">${escapeHtml(s.title)}</div>
            <div class="scenario-desc">${escapeHtml(s.symptom_summary)}</div>
          </div>
        `;
      })
      .join("");

    els.scenarioList.querySelectorAll(".scenario-card").forEach((card) => {
      card.addEventListener("click", () => {
        const found = state.scenarios.find((s) => s.scenario_id === card.dataset.id);
        if (found) selectScenario(found);
      });
    });
  }

  function selectScenario(scen) {
    state.selectedScenario = scen;
    els.customQueryInput.value = scen.symptom_summary || "";
    renderScenarioList();
  }

  function resetStepper() {
    document.querySelectorAll(".agent-step").forEach((el) => {
      el.classList.remove("active", "done", "revision");
    });
  }

  function updateStepperFromTrace(ev) {
    const agent = ev.agent;
    const stepEl = document.querySelector(`.agent-step[data-agent="${agent}"]`);
    if (stepEl) {
      document.querySelectorAll(".agent-step").forEach((el) => el.classList.remove("active"));
      if (ev.status === "needs_revision" || ev.status === "recovered") {
        stepEl.classList.add("revision");
      } else {
        stepEl.classList.add("active", "done");
      }
      const desc = document.getElementById(`status-${agent}`);
      if (desc && ev.action) {
        desc.textContent = `${ev.action} (${ev.status})`;
      }
    }
  }

  function appendTraceEvent(ev) {
    if (state.traceCount === 0) {
      els.traceStream.innerHTML = "";
    }
    state.traceCount += 1;
    els.traceCountBadge.textContent = `${state.traceCount} events`;

    const item = document.createElement("div");
    const statusCls = (ev.status || "success").toLowerCase();
    item.className = `trace-item ${statusCls}`;
    const target = ev.target_agent ? ` → ${ev.target_agent}` : "";
    item.innerHTML = `
      <div class="trace-top">
        <span class="trace-agents">${escapeHtml(ev.agent)}${escapeHtml(target)}</span>
        <span class="trace-status ${escapeHtml(statusCls)}">${escapeHtml(ev.status)}</span>
      </div>
      <div class="trace-action">${escapeHtml(ev.action)}</div>
      <div class="trace-summary">${escapeHtml(ev.output_summary)}</div>
    `;
    els.traceStream.appendChild(item);
    els.traceStream.scrollTop = els.traceStream.scrollHeight;
    updateStepperFromTrace(ev);
  }

  async function runInvestigation() {
    if (state.activeStream) {
      state.activeStream.close();
      state.activeStream = null;
    }

    const scen = state.selectedScenario || {};
    const payload = {
      incident_id: scen.incident_id || "INC-8891",
      scenario_id: scen.scenario_id || "scenario_01_deploy_red_herring",
      user_query: els.customQueryInput.value.trim() || scen.symptom_summary || "",
      options: {
        inject_sql_mismatch: els.injectMismatchToggle.checked,
      },
    };

    els.runInvestigationBtn.disabled = true;
    els.runBtnLabel.textContent = "Investigating Across 3 Silos...";
    state.traceCount = 0;
    els.traceStream.innerHTML = "";
    resetStepper();

    try {
      const startRes = await fetch("/api/investigate/async", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then((r) => r.json());

      if (startRes && startRes.run_id) {
        els.traceFileLabel.textContent = `logs/trace-${startRes.run_id}.jsonl`;
        const es = new EventSource(`/api/stream/${startRes.run_id}`);
        state.activeStream = es;

        es.addEventListener("trace", (msg) => {
          try {
            const ev = JSON.parse(msg.data);
            appendTraceEvent(ev);
          } catch (e) {
            console.error(e);
          }
        });

        es.addEventListener("done", (msg) => {
          es.close();
          state.activeStream = null;
          try {
            const d = JSON.parse(msg.data);
            if (d.result) {
              renderFullResult(d.result);
            }
          } catch (e) {
            console.error(e);
          }
          els.runInvestigationBtn.disabled = false;
          els.runBtnLabel.textContent = "Run Adversarial Investigation";
        });

        es.onerror = async () => {
          es.close();
          state.activeStream = null;
          const poll = await fetch(`/api/runs/${startRes.run_id}`).then((r) => r.json());
          if (poll && poll.result) renderFullResult(poll.result);
          els.runInvestigationBtn.disabled = false;
          els.runBtnLabel.textContent = "Run Adversarial Investigation";
        };
      } else {
        // Fallback synchronous execution
        const syncRes = await fetch("/api/investigate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }).then((r) => r.json());
        (syncRes.trace_events || []).forEach(appendTraceEvent);
        renderFullResult(syncRes);
        els.runInvestigationBtn.disabled = false;
        els.runBtnLabel.textContent = "Run Adversarial Investigation";
      }
    } catch (err) {
      console.error("Investigation error:", err);
      els.runInvestigationBtn.disabled = false;
      els.runBtnLabel.textContent = "Run Adversarial Investigation";
    }
  }

  function renderFullResult(res) {
    state.lastResult = res;
    const root = res.verified_root_cause || (res.output && res.output.verified_root_cause) || {};
    const falsified = res.falsified_hypotheses || (res.output && res.output.falsified_red_herrings) || [];
    const fin = res.financial_impact || (res.output && res.output.financial_impact) || {};
    const zet = (res.sponsor_technology && res.sponsor_technology.zetaris) || {};
    const met = (res.sponsor_technology && res.sponsor_technology.meterless) || {};

    // KPIs
    els.kpiRootCause.textContent = root.suspected_change_id || root.id || "Verified";
    els.kpiRootSub.textContent = root.title || "Confirmed across 3 silos";

    els.kpiFalsified.textContent = `${falsified.length} rejected`;
    els.kpiFalsifiedSub.textContent = falsified.map((f) => f.suspected_change_id).join(", ") || "Adversarial checks";

    els.kpiNetLoss.textContent = formatUsd(fin.deduplicated_lost_revenue_usd);
    if (fin.retry_inflation_factor && fin.retry_inflation_factor > 1.05) {
      els.kpiLossSub.textContent = `Corrected from ${formatUsd(fin.gross_failed_volume_usd)} (${fin.retry_inflation_factor}x retry inflation)`;
    } else {
      els.kpiLossSub.textContent = `${formatUsd(fin.hourly_burn_rate_usd)}/hr burn · ${fin.failed_checkout_count || 0} failed`;
    }

    els.kpiSqlCount.textContent = `${zet.queries_executed || 0} queries`;
    els.kpiRecoverySub.textContent = `${(zet.schema_recoveries || []).length} live schema recovery · ${res.execution_time_seconds || 0}s`;

    if (met.world_model && met.hmem) {
      els.pillMeterless.innerHTML = `<strong>Meterless</strong> · ${met.world_model.entity_count} entities · ${met.hmem.memory_count} H-MEM`;
    }

    renderHypothesesTab(res.hypotheses || []);
    renderBriefTab(res);
    renderSqlTab(zet);
    renderMeterlessTab(met);
  }

  function renderHypothesesTab(hypotheses) {
    if (!hypotheses.length) {
      els.hypothesesContainer.innerHTML = `<div class="empty-state">No hypotheses recorded.</div>`;
      return;
    }

    els.hypothesesContainer.innerHTML = hypotheses
      .map((h) => {
        const checksHtml = (h.checks || [])
          .map((c) => {
            const icon = c.passed ? "✓ PASS" : "✗ FAIL";
            const cls = c.passed ? "pass" : "fail";
            return `
              <div class="check-row">
                <span class="check-icon ${cls}">${icon}</span>
                <div>
                  <strong>${escapeHtml(c.check_name)}:</strong> ${escapeHtml(c.summary)}
                </div>
              </div>
            `;
          })
          .join("");

        const citesHtml = (h.citations || [])
          .map((cid) => `<span class="cite-chip">${escapeHtml(cid)}</span>`)
          .join("");

        const verdictLabel =
          h.status === "VERIFIED"
            ? `VERIFIED ROOT CAUSE (Round ${h.round_resolved || h.round_proposed} · Confidence ${(h.confidence * 100).toFixed(0)}%)`
            : `FALSIFIED BY EVALUATOR (Round ${h.round_resolved || h.round_proposed})`;

        return `
          <div class="hyp-card ${escapeHtml(h.status)}">
            <div class="hyp-header">
              <div class="hyp-title-group">
                <span class="hyp-id">${escapeHtml(h.id)} · Round ${escapeHtml(h.round_proposed)}</span>
                <span class="hyp-change-chip">${escapeHtml(h.suspected_change_id)} @ ${escapeHtml(h.suspected_timestamp)}</span>
                <span class="hyp-change-chip">${escapeHtml(h.category)}</span>
              </div>
              <span class="status-pill ${escapeHtml(h.status)}">${escapeHtml(verdictLabel)}</span>
            </div>
            <div class="hyp-title">${escapeHtml(h.title)}</div>
            <div class="hyp-mechanism"><strong>Proposed Mechanism:</strong> ${escapeHtml(h.mechanism)}</div>
            <div class="hyp-verdict-box">
              <strong>Evaluator Verdict:</strong> ${escapeHtml(h.falsification_reason || "")}
            </div>
            <div class="hyp-Checks">${checksHtml}</div>
            <div class="citation-row">${citesHtml}</div>
          </div>
        `;
      })
      .join("");
  }

  function renderBriefTab(res) {
    const root = res.verified_root_cause || {};
    const fin = res.financial_impact || {};
    const falsified = res.falsified_hypotheses || [];
    const recs = res.remediation_plan || [];
    const caveats = res.limitations_and_caveats || [];
    const verSql = res.post_remediation_verification_sql || "";

    els.briefContainer.innerHTML = `
      <div class="brief-section">
        <h3>Executive Summary</h3>
        <p>${escapeHtml(res.executive_summary || "")}</p>
      </div>

      <div class="brief-section">
        <h3>Verified Root Cause (${escapeHtml(root.suspected_change_id || "")})</h3>
        <p><strong>${escapeHtml(root.title || "")}</strong></p>
        <p style="margin-top:4px;color:var(--text-muted);">${escapeHtml(root.mechanism || "")}</p>
        <p style="margin-top:6px;"><strong>Statistical Proof:</strong> ${escapeHtml(root.statistical_proof || "")}</p>
      </div>

      <div class="brief-section">
        <h3>Cross-Silo Financial Impact Audit</h3>
        <p><strong>Net Lost Revenue (Deduplicated):</strong> ${formatUsd(fin.deduplicated_lost_revenue_usd)} (${formatUsd(fin.hourly_burn_rate_usd)}/hr burn rate)</p>
        <p><strong>Gross Failed Trace Volume:</strong> ${formatUsd(fin.gross_failed_volume_usd)} (Retry Inflation Factor: ${escapeHtml(fin.retry_inflation_factor)}x)</p>
        <p style="margin-top:4px;color:var(--text-muted);">${escapeHtml(fin.methodology_note || "")}</p>
      </div>

      <div class="brief-section">
        <h3>Why Red Herrings Were Falsified</h3>
        <ul class="brief-list">
          ${falsified
            .map(
              (f) =>
                `<li><strong>${escapeHtml(f.id)} (${escapeHtml(f.suspected_change_id)}):</strong> ${escapeHtml(f.why_falsified)}</li>`
            )
            .join("")}
        </ul>
      </div>

      <div class="brief-section">
        <h3>Prioritized Remediation Plan</h3>
        <ol class="brief-list">
          ${recs.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}
        </ol>
        <div style="margin-top:8px;font-size:11.5px;color:var(--text-muted);">Post-Remediation Zetaris Verification Query:</div>
        <div class="code-block">${escapeHtml(verSql)}</div>
      </div>

      <div class="brief-section">
        <h3>FinalReviewerAgent Causal Caveats &amp; Confidence Boundaries</h3>
        <ul class="brief-list">
          ${caveats.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}
        </ul>
      </div>
    `;
  }

  function renderSqlTab(zet) {
    const recoveries = zet.schema_recoveries || [];
    const queries = zet.query_log || [];

    const recHtml = recoveries
      .map(
        (r) => `
        <div class="recovery-card">
          <div class="recovery-title">⚡ Live Cross-Silo SQL Schema Error Recovery (TOOL_ERROR_RECOVERY)</div>
          <div style="font-size:12px;margin-bottom:4px;"><strong>Initial Error:</strong> <code>${escapeHtml(r.initial_error)}</code></div>
          <div style="font-size:12px;margin-bottom:4px;"><strong>Inspected Schema (<code>{${escapeHtml(r.silo)}}.${escapeHtml(r.table)}</code>):</strong> <code>${escapeHtml((r.inspected_schema || []).join(", "))}</code></div>
          <div style="font-size:12px;margin-bottom:4px;"><strong>Column Repairs Applied &amp; Stored in Meterless H-MEM:</strong> <code>${escapeHtml(JSON.stringify(r.column_repairs || {}))}</code></div>
          <div class="code-block">Repaired SQL:\n${escapeHtml(r.repaired_sql)}</div>
        </div>
      `
      )
      .join("");

    const qHtml = queries
      .map(
        (q, idx) => `
        <div class="brief-section">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
            <strong>#${idx + 1} · ${escapeHtml(q.purpose || "Federated SQL Query")}</strong>
            <span class="cite-chip">${escapeHtml(q.row_count)} rows · ${escapeHtml(q.elapsed_ms)}ms · ${escapeHtml(q.status)}</span>
          </div>
          <div class="code-block">${escapeHtml(q.federated_sql)}</div>
        </div>
      `
      )
      .join("");

    els.sqlContainer.innerHTML = recHtml + qHtml;
  }

  function renderMeterlessTab(met) {
    const wm = met.world_model || {};
    const hmem = met.hmem || {};
    const manifest = met.context_builder_manifest || {};

    els.meterlessContainer.innerHTML = `
      <div class="brief-section">
        <h3>Meterless World Model Agent Engine</h3>
        <p><strong>Persisted File:</strong> <code>${escapeHtml(wm.persisted_path || "state/meterless_world_model.json")}</code></p>
        <p><strong>Entities:</strong> ${escapeHtml(wm.entity_count)} · <strong>Relations:</strong> ${escapeHtml(wm.relation_count)} · <strong>Events:</strong> ${escapeHtml(wm.event_count)}</p>
        <div class="code-block">${escapeHtml(JSON.stringify(wm.entities || [], null, 2))}</div>
      </div>

      <div class="brief-section">
        <h3>Meterless H-MEM Agent Engine &amp; Trust Ledger</h3>
        <p><strong>Persisted File:</strong> <code>${escapeHtml(hmem.persisted_path || "state/meterless_hmem.json")}</code></p>
        <p><strong>Total Memories:</strong> ${escapeHtml(hmem.memory_count)} · <strong>By Type:</strong> <code>${escapeHtml(JSON.stringify(hmem.by_type || {}))}</code></p>
        <div style="margin-top:6px;font-weight:600;">Agent Trust Ledger Profiles:</div>
        <div class="code-block">${escapeHtml(JSON.stringify(hmem.trust_profiles || {}, null, 2))}</div>
        <div style="margin-top:6px;font-weight:600;">Active H-MEM Memories (Episodic, Semantic, Procedural):</div>
        <div class="code-block">${escapeHtml(JSON.stringify(hmem.memories || [], null, 2))}</div>
      </div>

      <div class="brief-section">
        <h3>Meterless ContextBuilder Manifest (Wired vs. Helper Services)</h3>
        <div class="code-block">${escapeHtml(JSON.stringify(manifest, null, 2))}</div>
      </div>
    `;
  }

  els.runInvestigationBtn.addEventListener("click", runInvestigation);
  init();
})();
