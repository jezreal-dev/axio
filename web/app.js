(() => {
  const state = {
    scenarios: [],
    selectedScenario: null,
    activeStream: null,
    traceCount: 0,
    lastResult: null,
    running: false,
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
      document.querySelectorAll(".tab-btn").forEach((b) => { b.classList.remove("active"); b.setAttribute("aria-pressed", "false"); });
      document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      btn.setAttribute("aria-pressed", "true");
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
        const localData = healthRes.zetaris?.backend !== "zetaris-rest";
        els.pillSampleMode.textContent = localData ? "Prepared incidents · synthetic data" : "Incident data from Zetaris";
        document.getElementById("pillZetaris").textContent = localData ? "Zetaris adapter · local SQLite backend" : "Zetaris · REST backend";
        els.pillNvidia.textContent = healthRes.config.model_provider === "meterless"
          ? `Nightshift · ${healthRes.nightshift?.status || "unchecked"}${healthRes.nightshift?.status === "unauthorized" ? "; set Runtime token in .env" : "; model run not yet verified"}`
          : (healthRes.config.llm_configured ? "NVIDIA · configured, awaiting run" : "NVIDIA · not configured; deterministic reasoning");
      }
      if (healthRes && healthRes.meterless) {
        els.pillMeterless.innerHTML = `<strong>Meterless</strong> · ${healthRes.meterless.world_model_entities} entities · ${healthRes.meterless.hmem_memories} H-MEM`;
      }
      state.scenarios = scenRes.scenarios || [];
      renderScenarioList();
      if (state.scenarios.length > 0) {
        selectScenario(state.scenarios[0]);
      }
    } catch (err) {
      showError("Could not connect to Axio. Check the server and reload.");
    }
  }

  function renderScenarioList() {
    els.scenarioList.innerHTML = state.scenarios
      .map((s) => {
        const isSel = state.selectedScenario && state.selectedScenario.scenario_id === s.scenario_id;
        return `
          <button type="button" class="scenario-card ${isSel ? "active" : ""}" aria-pressed="${!!isSel}" ${state.running ? "disabled" : ""} data-id="${escapeHtml(s.scenario_id)}">
            <div class="scenario-top">
              <span class="scenario-id-badge">${escapeHtml(s.incident_id)}</span>
              <span class="scenario-window">${escapeHtml((s.started_at || "").slice(5, 16).replace("T", " "))} UTC</span>
            </div>
            <div class="scenario-title">${escapeHtml(shortTitle(s))}</div>
            <div class="scenario-desc">${escapeHtml(shortDescription(s))}</div>
          </button>
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
    if (state.running) return;
    state.selectedScenario = scen;
    document.getElementById("selectedIncident").textContent = `${scen.incident_id} / INCIDENT BRIEF`;
    document.getElementById("selectedTitle").textContent = shortTitle(scen);
    clearResult();
    els.customQueryInput.value = scen.symptom_summary || "";
    renderScenarioList();
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
        <span class="trace-agents">${escapeHtml(ev.agent_name || ev.agent)}${escapeHtml(target)}</span>
        <span class="trace-status ${escapeHtml(statusCls)}">${escapeHtml(ev.status)}</span>
      </div>
      <div class="trace-action">${escapeHtml(ev.action)}</div>
      <div class="trace-summary">${escapeHtml(ev.output_summary)}</div>
    `;
    els.traceStream.appendChild(item);
    els.traceStream.scrollTop = els.traceStream.scrollHeight;
  }

  function shortTitle(s) {
    return ({"INC-8891":"Checkout conversion drop", "INC-8892":"Enterprise order-sync collapse", "INC-8893":"EU checkout timeout storm"})[s.incident_id] || s.title;
  }
  function shortDescription(s) {
    return ({"INC-8891":"iPhone payments are failing.", "INC-8892":"Bulk order volume fell over 80%.", "INC-8893":"Timeouts and retries obscure the loss."})[s.incident_id] || s.symptom_summary;
  }
  function showError(message) {
    const banner = document.getElementById("errorBanner");
    banner.hidden = false;
    banner.textContent = message;
    document.getElementById("runStatus").textContent = "Needs attention";
  }
  function clearResult() {
    state.lastResult = null;
    document.getElementById("errorBanner").hidden = true;
    document.getElementById("runStatus").textContent = "Ready to investigate";
    [els.kpiRootCause, els.kpiFalsified, els.kpiNetLoss].forEach(el => el.textContent = "—");
    els.kpiRootSub.textContent = "Awaiting evidence";
    els.kpiFalsifiedSub.textContent = "Tested against the data";
    els.kpiLossSub.textContent = "Deduplicated failed transactions";
    els.kpiSqlCount.textContent = "0 queries";
    els.kpiRecoverySub.textContent = "No recovery events";
    els.briefContainer.innerHTML = '<div class="empty-state"><span class="empty-mark">↗</span><h3>A clear answer starts with a question.</h3><p>Start an investigation to see the finding, financial impact and next steps.</p></div>';
    [els.hypothesesContainer, els.sqlContainer, els.meterlessContainer].forEach(el => el.innerHTML = '<p class="muted">Run an investigation to inspect its evidence.</p>');
    els.traceStream.innerHTML = "";
    state.traceCount = 0;
    els.traceCountBadge.textContent = "0 events";
    els.traceFileLabel.textContent = "Waiting for a run";
  }
  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || `Request failed (${response.status})`);
    return data;
  }
  async function runInvestigation() {
    if (state.running || !state.selectedScenario) return;
    clearResult();
    state.running = true;
    renderScenarioList();
    els.runInvestigationBtn.disabled = true;
    els.customQueryInput.disabled = true;
    els.injectMismatchToggle.disabled = true;
    els.runBtnLabel.textContent = "Investigating…";
    document.getElementById("runStatus").textContent = "Testing explanations…";
    els.briefContainer.innerHTML = '<div class="empty-state"><span class="empty-mark">↗</span><h3>Following the evidence.</h3><p>Open Agent activity to follow the investigation as it runs.</p></div>';
    const scen = state.selectedScenario;
    let pollTimer, finished = false, polling = false, stream;
    const finish = (result, error) => {
      if (finished) return;
      finished = true;
      clearInterval(pollTimer);
      stream?.close();
      state.activeStream = null;
      state.running = false;
      els.runInvestigationBtn.disabled = false;
      els.customQueryInput.disabled = false;
      els.injectMismatchToggle.disabled = false;
      els.runBtnLabel.textContent = "Investigate again";
      renderScenarioList();
      if (error || result?.status !== "success") {
        showError(error || result?.error?.message || "The investigation could not complete. Try again.");
        els.briefContainer.innerHTML = '<div class="empty-state"><h3>Investigation incomplete.</h3><p>No finding has been confirmed. Check the error above and try again.</p></div>';
      } else {
        try { renderFullResult(result); }
        catch (err) { showError(`Could not display the report: ${err.message}`); }
      }
    };
    try {
      const start = await requestJson("/api/investigate/async", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({incident_id:scen.incident_id,scenario_id:scen.scenario_id,user_query:els.customQueryInput.value.trim(),options:{inject_sql_mismatch:els.injectMismatchToggle.checked}})
      });
      if (!start.run_id) throw new Error("Server did not return a run ID.");
      els.traceFileLabel.textContent = `logs/trace-${start.run_id}.jsonl`;
      const poll = async () => {
        if (finished || polling) return;
        polling = true;
        try {
          const data = await requestJson(`/api/runs/${start.run_id}`);
          if (!finished && data.result) {
            if (state.traceCount < (data.result.trace_events || []).length) {
              els.traceStream.innerHTML = ""; state.traceCount = 0;
              data.result.trace_events.forEach(appendTraceEvent);
            }
            finish(data.result);
          }
        } catch (err) { finish(null, `Connection interrupted: ${err.message}. You can retry.`); }
        finally { polling = false; }
      };
      stream = new EventSource(`/api/stream/${start.run_id}`);
      state.activeStream = stream;
      stream.addEventListener("trace", msg => {
        if (finished) return;
        try { appendTraceEvent(JSON.parse(msg.data)); } catch (err) { console.error(err); }
      });
      stream.addEventListener("done", msg => {
        try { const data = JSON.parse(msg.data); if (data.result) finish(data.result); else poll(); }
        catch (err) { poll(); }
      });
      stream.onerror = () => { stream.close(); poll(); };
      pollTimer = setInterval(poll, 2000);
    } catch (err) { finish(null, err.message); }
  }


  function renderFullResult(res) {
    state.lastResult = res;
    document.getElementById("runStatus").textContent = `Investigation complete · ${res.execution_time_seconds || 0}s`;
    const calls = res.sponsor_technology?.nvidia_nemotron?.calls || [];
    const successes = calls.filter(call => !call.error).length;
    const providerLabel = res.sponsor_technology?.nvidia_nemotron?.provider === "meterless" ? "Nightshift" : "NVIDIA";
    const returnedModels = [...new Set(calls.filter(call => !call.error).map(call => call.model))].join(", ");
    els.pillNvidia.textContent = successes ? `${providerLabel} · ${successes} successful API calls · ${returnedModels}${calls.some(call => call.error) ? " · some calls fell back" : ""}` : `${providerLabel} · deterministic fallback used`;
    const root = res.verified_root_cause || (res.output && res.output.verified_root_cause) || {};
    const falsified = res.falsified_hypotheses || (res.output && res.output.falsified_red_herrings) || [];
    const fin = res.financial_impact || (res.output && res.output.financial_impact) || {};
    const zet = (res.sponsor_technology && res.sponsor_technology.zetaris) || {};
    const met = (res.sponsor_technology && res.sponsor_technology.meterless) || {};

    // KPIs
    els.kpiRootCause.textContent = root.suspected_change_id || root.id || "Verified";
    els.kpiRootSub.textContent = "Supported by cross-silo evidence";

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
    const proof = root.statistical_proof;
    const proofText = proof && typeof proof === "object"
      ? Object.entries(proof).map(([key, value]) => `${key.replace(/_/g, " ")}: ${value}`).join(" · ")
      : proof || "";
    const fin = res.financial_impact || {};
    const falsified = res.falsified_hypotheses || [];
    const recs = res.remediation_plan || [];
    const caveats = res.limitations_and_caveats || [];
    const verSql = res.post_remediation_verification_sql || "";
    const pct = value => `${(Number(value || 0) * 100).toFixed(1)}%`;
    const width = value => Math.max(0, Math.min(100, Number(value || 0) * 100));

    els.briefContainer.innerHTML = `
      <div class="finding"><div class="eyebrow">EVIDENCE-BACKED FINDING · ${escapeHtml(root.suspected_change_id || "")}</div><h3>${escapeHtml(root.title || "")}</h3><p>${escapeHtml(root.mechanism || "")}</p></div>
      <div class="brief-section"><h3>Recommended next steps</h3><ol class="brief-list">${recs.map(r => `<li>${escapeHtml(r)}</li>`).join("")}</ol></div>
      <div class="brief-section"><h3>Why this explanation holds</h3>
      ${proof && typeof proof === "object" ? `<div class="evidence-bars"><div class="bar-row"><span>Affected cohort</span><div class="bar-track"><div class="bar-fill" style="width:${width(proof.in_cohort_error_rate)}%"></div></div><strong>${pct(proof.in_cohort_error_rate)}</strong></div><div class="bar-row"><span>Outside cohort</span><div class="bar-track"><div class="bar-fill" style="width:${width(proof.out_cohort_error_rate)}%"></div></div><strong>${pct(proof.out_cohort_error_rate)}</strong></div></div><p class="small muted">Observed failure rates. Confirm the effect after remediation.</p>` : ""}
      <details style="margin-top:16px"><summary>Statistical details</summary><p>${escapeHtml(proofText)}</p></details></div>

      <div class="brief-section">
        <h3>Financial impact</h3>
        <p><strong>Net Lost Revenue (Deduplicated):</strong> ${formatUsd(fin.deduplicated_lost_revenue_usd)} (${formatUsd(fin.hourly_burn_rate_usd)}/hr burn rate)</p>
        <p><strong>Gross Failed Trace Volume:</strong> ${formatUsd(fin.gross_failed_volume_usd)} (Retry Inflation Factor: ${escapeHtml(fin.retry_inflation_factor)}x)</p>
        <p style="margin-top:4px;color:var(--text-muted);">${escapeHtml(fin.methodology_note || "")}</p>
      </div>

      <div class="brief-section">
        <h3>What we ruled out</h3>
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
        <h3>Verify after remediation</h3>
        <div style="margin-top:8px;font-size:11.5px;color:var(--text-muted);">Post-Remediation Zetaris Verification Query:</div>
        <div class="code-block">${escapeHtml(verSql)}</div>
      </div>

      <div class="brief-section">
        <h3>Confidence and limitations</h3>
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
            <span class="cite-chip">${escapeHtml(q.rows ?? q.row_count ?? 0)} rows · ${escapeHtml(q.latency_ms ?? q.elapsed_ms ?? 0)}ms · ${escapeHtml(q.status)}</span>
          </div>
          <div class="code-block">${escapeHtml(q.federated_sql || q.sql)}</div>
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
