"use strict";

const state = {
  config: null,
  disputes: [],
  selected: null,
  run: null,
  poll: null,
  els: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const money = (currency, value) =>
  `${currency === "USD" ? "$" : currency + " "}${Number(value || 0).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `${res.status} ${res.statusText}`);
  return body;
}

/* ---------------- header ---------------- */

function renderBadges(config) {
  const live = config.paypal_mode === "sandbox";
  const badges = [
    live
      ? { cls: "live", text: "PayPal sandbox (live API)" }
      : { cls: "mock", text: "PayPal mock mode" },
    config.agent_engine === "claude"
      ? { cls: "live", text: `Agent: ${config.model}` }
      : { cls: "mock", text: "Agent: offline rules engine" },
  ];
  $("#badges").innerHTML = badges
    .map((b) => `<span class="badge ${b.cls}"><span class="badge-dot"></span>${b.text}</span>`)
    .join("");
}

function renderStats(stats) {
  $("#stat-open").textContent = stats.open_disputes;
  $("#stat-risk").textContent = money(stats.currency, stats.at_risk);
  $("#stat-protected").textContent = money(stats.currency, stats.protected);
  $("#stat-hours").textContent = stats.hours_saved;
}

/* ---------------- inbox ---------------- */

function renderInbox() {
  const list = $("#dispute-list");
  list.innerHTML = "";
  for (const d of state.disputes) {
    const li = document.createElement("li");
    const tags = [];
    if (d.submitted) tags.push(`<span class="tag tag-done">Submitted</span>`);
    else if (d.run && d.run.status === "complete")
      tags.push(`<span class="tag tag-action">${d.run.action} ready</span>`);
    else if (d.run && d.run.status === "running")
      tags.push(`<span class="tag tag-action">Agent running</span>`);
    if (d.actionable && d.due_in_days !== null) {
      tags.push(
        `<span class="tag ${d.due_in_days <= 5 ? "tag-due-soon" : ""}">Due in ${d.due_in_days}d</span>`
      );
    }
    li.innerHTML = `
      <button class="dispute-btn" data-id="${d.dispute_id}" aria-current="${d.dispute_id === state.selected}">
        <span class="dispute-row">
          <span class="dispute-item">${escapeHtml(d.item_name || d.dispute_id)}</span>
          <span class="dispute-amount">${money(d.currency, d.amount)}</span>
        </span>
        <span class="dispute-sub">
          <span class="tag">${escapeHtml(d.reason_label)}</span>${tags.join("")}
        </span>
      </button>`;
    list.appendChild(li);
  }
  list.querySelectorAll(".dispute-btn").forEach((btn) =>
    btn.addEventListener("click", () => selectDispute(btn.dataset.id))
  );
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

/* ---------------- detail ---------------- */

async function loadDisputes() {
  const data = await api("/api/disputes");
  state.disputes = data.disputes;
  renderStats(data.stats);
  renderInbox();
}

async function selectDispute(id) {
  stopPolling();
  state.selected = id;
  renderInbox();
  const data = await api(`/api/disputes/${id}`);
  state.detail = data;
  state.run = data.run;

  $("#empty-state").hidden = true;
  const body = $("#detail-body");
  body.hidden = false;
  body.innerHTML = "";
  const frag = $("#tpl-detail").content.cloneNode(true);
  body.appendChild(frag);
  state.els = {};
  body.querySelectorAll("[data-el]").forEach((el) => (state.els[el.dataset.el] = el));

  const s = data.summary;
  const fill = (name, value) =>
    body.querySelectorAll(`[data-f="${name}"]`).forEach((el) => (el.textContent = value));
  fill("dispute_id", s.dispute_id);
  fill("stage", s.stage);
  fill("item_name", s.item_name || "Dispute");
  fill("buyer_name", s.buyer_name || "Unknown buyer");
  fill("order_id", s.order_id || "—");
  fill("amount", money(s.currency, s.amount));
  fill("reason_label", s.reason_label);
  fill("status", s.status);
  fill("buyer_message", s.buyer_message || "No message from the buyer.");

  const dueEl = body.querySelector('[data-f="due"]');
  if (s.due_in_days === null) dueEl.textContent = "No response deadline";
  else {
    dueEl.innerHTML = `Respond within <b>${s.due_in_days} day${s.due_in_days === 1 ? "" : "s"}</b>`;
    if (s.due_in_days <= 5) dueEl.classList.add("urgent");
  }

  state.els.run.addEventListener("click", runAgent);
  state.els.approve.addEventListener("click", approve);

  if (!s.actionable) {
    state.els.run.disabled = true;
    state.els["run-hint"].textContent = `PayPal status is ${s.status}; no seller response can be sent.`;
  }

  if (data.submissions.length) renderSubmissions(data.submissions);
  if (state.run) renderRun(state.run);
}

async function runAgent() {
  state.els.run.disabled = true;
  state.els.run.textContent = "Running…";
  state.els["run-hint"].textContent = "";
  state.els["trace-panel"].hidden = false;
  state.els.timeline.innerHTML =
    `<li class="spinner-row"><span class="spinner"></span>Starting the agent…</li>`;
  try {
    const { run_id } = await api(`/api/disputes/${state.selected}/agent`, { method: "POST" });
    startPolling(run_id);
  } catch (err) {
    state.els.timeline.innerHTML = `<li class="kind-error"><span class="step-label">${escapeHtml(err.message)}</span></li>`;
    state.els.run.disabled = false;
    state.els.run.textContent = "Run agent";
  }
}

function startPolling(runId) {
  stopPolling();
  const tick = async () => {
    try {
      const run = await api(`/api/runs/${runId}`);
      state.run = run;
      renderRun(run);
      if (run.status !== "running") {
        stopPolling();
        loadDisputes();
      }
    } catch (err) {
      stopPolling();
    }
  };
  tick();
  state.poll = setInterval(tick, 900);
}

function stopPolling() {
  if (state.poll) clearInterval(state.poll);
  state.poll = null;
}

const TOOL_LABELS = {
  get_dispute_details: "Read the PayPal dispute",
  get_order_record: "Read the order record",
  get_shipment_tracking: "Read carrier tracking",
  get_customer_messages: "Read buyer messages",
  get_store_policies: "Read store policies",
  get_buyer_history: "Read buyer history",
  submit_resolution_plan: "Submit resolution plan",
};

function renderRun(run) {
  const els = state.els;
  if (!els) return;
  els["trace-panel"].hidden = false;
  const items = run.steps.map((step) => {
    let label = escapeHtml(step.label || step.kind);
    let note = "";
    let pre = "";
    if (step.kind === "tool_call") {
      label = `${escapeHtml(TOOL_LABELS[step.label] || step.label)} <code>${escapeHtml(step.label)}</code>`;
    } else if (step.kind === "tool_result") {
      const p = step.payload || {};
      label = `Result from <code>${escapeHtml(step.label)}</code>`;
      note = `${p.chars} characters${p.truncated ? " (preview)" : ""}`;
      pre = p.preview || "";
    } else if (step.kind === "thinking") {
      label = "Agent note";
      note = (step.payload && step.payload.text) || "";
    } else if (step.kind === "plan") {
      label = escapeHtml(step.label);
    } else if (step.kind === "error") {
      label = escapeHtml(step.label);
      note = (step.payload && step.payload.error) || "";
    } else if (step.kind === "status" && step.label === "Tokens this turn") {
      const p = step.payload || {};
      label = "Tokens this turn";
      note = `${p.input_tokens} in / ${p.output_tokens} out`;
    }
    return `<li class="kind-${step.kind}">
      <span class="step-label">${label}</span>
      ${note ? `<p class="step-note">${escapeHtml(note)}</p>` : ""}
      ${pre ? `<details class="step-detail"><summary>View what the agent read</summary><pre class="step-pre">${escapeHtml(pre)}</pre></details>` : ""}
    </li>`;
  });
  if (run.status === "running") {
    items.push(`<li class="spinner-row"><span class="spinner"></span>Thinking…</li>`);
  }
  els.timeline.innerHTML = items.join("");

  if (run.status === "error") {
    els.run.disabled = false;
    els.run.textContent = "Run agent again";
    els["run-hint"].textContent = run.error || "The run failed.";
    return;
  }
  if (run.status === "complete" && run.plan) {
    els.run.disabled = false;
    els.run.textContent = "Run agent again";
    renderPlan(run.plan, run);
  }
}

function renderPlan(plan, run) {
  const els = state.els;
  const s = state.detail.summary;
  els["rec-panel"].hidden = false;

  els["action-badge"].textContent = plan.action;
  els["action-badge"].className = `action-badge action-${plan.action}`;
  els["rec-summary"].textContent = plan.summary;

  const pct = Math.round((plan.confidence || 0) * 100);
  els["conf-value"].textContent = `${pct}%`;
  els["conf-fill"].style.width = `${pct}%`;
  els["engine-note"].textContent =
    plan.engine === "claude" ? `${plan.model || "claude"} · ${run.steps.length} steps` : "offline rules engine";

  els.reasoning.innerHTML = (plan.reasoning || [])
    .map((r) => `<li>${escapeHtml(r)}</li>`)
    .join("");

  els.evidence.innerHTML = (plan.evidence || []).length
    ? plan.evidence
        .map(
          (e) => `<li><span class="check">✓</span><span>
            <span class="evidence-type">${escapeHtml(e.evidence_type)}</span>
            <span class="evidence-desc">${escapeHtml(e.description)}</span>
            ${e.tracking_info ? `<span class="evidence-desc"><br>Tracking ${escapeHtml(e.tracking_info.tracking_number)} (${escapeHtml(e.tracking_info.carrier_name)})</span>` : ""}
            ${e.refund_ids ? `<span class="evidence-desc"><br>Refund ${escapeHtml(e.refund_ids.join(", "))}</span>` : ""}
          </span></li>`
        )
        .join("")
    : `<li><span class="evidence-desc">No evidence filed with this action.</span></li>`;

  const risks = plan.risk_flags || [];
  els["risk-wrap"].hidden = risks.length === 0;
  els.risks.innerHTML = risks.map((r) => `<li>${escapeHtml(r)}</li>`).join("");

  if (plan.action === "OFFER" && plan.offer) {
    els["offer-wrap"].hidden = false;
    const allowed = s.allowed_offer_types.length ? s.allowed_offer_types : [plan.offer.offer_type];
    els["offer-type"].innerHTML = allowed
      .map(
        (t) =>
          `<option value="${t}" ${t === plan.offer.offer_type ? "selected" : ""}>${t.replaceAll("_", " ").toLowerCase()}</option>`
      )
      .join("");
    els["offer-currency"].textContent = s.currency;
    els["offer-amount"].value = plan.offer.amount;
    els["offer-cap"].textContent = `Cannot exceed the disputed amount of ${money(s.currency, s.amount)}. The server rejects anything higher.`;
  } else {
    els["offer-wrap"].hidden = true;
  }

  els.note.value = plan.response_note || "";
  els["buyer-message"].value = plan.buyer_message || "";
  const count = () => {
    const n = els.note.value.length;
    els["note-count"].textContent = `${n}/2000`;
    els["note-count"].classList.toggle("over", n > 2000);
  };
  count();
  els.note.addEventListener("input", count);

  const submitted = (state.detail.submissions || []).some((x) => x.ok);
  if (!s.actionable || submitted) {
    els.approve.disabled = true;
    els.approve.textContent = submitted ? "Already submitted" : "Not open for a response";
  }
}

/* ---------------- approve ---------------- */

async function approve() {
  const els = state.els;
  els.approve.disabled = true;
  els.approve.textContent = "Submitting to PayPal…";
  const payload = {
    run_id: state.run ? state.run.id : null,
    action: state.run.plan.action,
    response_note: els.note.value,
    buyer_message: els["buyer-message"].value,
    send_buyer_message: els["send-message"].checked,
  };
  if (state.run.plan.action === "OFFER") {
    payload.offer_type = els["offer-type"].value;
    payload.offer_amount = els["offer-amount"].value;
  }
  try {
    const result = await api(`/api/disputes/${state.selected}/approve`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderResult(result);
    els.approve.textContent = result.ok ? "Submitted to PayPal" : "Submission failed";
    if (!result.ok) els.approve.disabled = false;
    await loadDisputes();
  } catch (err) {
    renderError(err.message);
    els.approve.disabled = false;
    els.approve.textContent = "Approve & submit to PayPal";
  }
}

function renderResult(result) {
  const panel = state.els["result-panel"];
  const body = state.els["result-body"];
  panel.hidden = false;
  const banner = result.ok
    ? `<div class="banner ok">Sent to PayPal. ${escapeHtml(result.dispute.status)} is the new dispute status.</div>`
    : `<div class="banner">PayPal rejected part of this submission. Nothing else was sent.</div>`;
  body.innerHTML =
    banner +
    result.results
      .map(
        (r) => `<div class="result-block">
          <div class="result-line">
            <span class="result-pill ${r.ok ? "ok" : "fail"}">${r.ok ? "200 OK" : r.status_code || "ERROR"}</span>
            <strong>${escapeHtml(r.action)}</strong>
          </div>
          ${r.endpoint ? `<p>POST <code>${escapeHtml(r.endpoint)}</code></p>` : ""}
          ${r.request ? `<p>Request</p><pre class="step-pre">${escapeHtml(JSON.stringify(r.request, null, 2))}</pre>` : ""}
          <p>Response</p>
          <pre class="step-pre">${escapeHtml(JSON.stringify(r.response || r.error, null, 2))}</pre>
        </div>`
      )
      .join("");
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderSubmissions(submissions) {
  state.els["result-panel"].hidden = false;
  state.els["result-body"].innerHTML =
    `<div class="banner ok">This dispute already has a response on file.</div>` +
    submissions
      .map(
        (s) => `<div class="result-block">
          <div class="result-line">
            <span class="result-pill ${s.ok ? "ok" : "fail"}">${s.status_code || "ERROR"}</span>
            <strong>${escapeHtml(s.action)}</strong>
          </div>
          ${s.endpoint ? `<p>POST <code>${escapeHtml(s.endpoint)}</code></p>` : ""}
          <p>Response</p>
          <pre class="step-pre">${escapeHtml(JSON.stringify(s.response, null, 2))}</pre>
        </div>`
      )
      .join("");
}

function renderError(message) {
  const panel = state.els["result-panel"];
  panel.hidden = false;
  state.els["result-body"].innerHTML = `<div class="banner">${escapeHtml(message)}</div>`;
}

/* ---------------- boot ---------------- */

$("#btn-reset").addEventListener("click", async () => {
  if (!confirm("Restore the demo fixtures and clear all agent runs?")) return;
  await api("/api/reset", { method: "POST" });
  state.selected = null;
  state.run = null;
  $("#detail-body").hidden = true;
  $("#empty-state").hidden = false;
  await loadDisputes();
});

(async function boot() {
  try {
    state.config = await api("/api/config");
    renderBadges(state.config);
    await loadDisputes();
    if (state.disputes.length) selectDispute(state.disputes[0].dispute_id);
  } catch (err) {
    document.body.insertAdjacentHTML(
      "afterbegin",
      `<div class="banner" style="margin:16px 24px">${escapeHtml(err.message)}</div>`
    );
  }
})();
