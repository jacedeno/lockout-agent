// Agent Rush console: dataset picker, live run, two-lane timeline, verdict, work order, receipt.
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const short = (h) => (h ? String(h).slice(0, 10) : "");
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

let datasets = [], selected = null, source = null, current = null;

// Events that physically happen inside the sandbox VM (the right lane).
const IN_SANDBOX = new Set(["sandbox_active", "sandbox_ready", "job_uploaded", "file_added", "step_ran", "outputs_pulled"]);
const OPENS = new Set(["sandbox_created", "sandbox_assigned"]);

// ---- status bar -------------------------------------------------------------------------------
async function refreshStatus() {
  try {
    const s = await (await fetch("/api/status")).json();
    const pool = s.warm_pool;
    const poolTxt = s.local ? "local Podman (dev)" : !pool ? "off" : pool.ready ? `ready ${Math.round(pool.ready_for_s / 60)} min` : pool.booting ? "booting" : "idle";
    $("gauges").innerHTML =
      `<span><i class="lamp ${pool && pool.ready ? "on" : pool && pool.booting ? "warm" : ""}"></i>warm sandbox <b>${esc(poolTxt)}</b></span>` +
      `<span>model <b>${esc(s.model)}</b></span>` +
      `<span>runs today <b class="num">${s.runs_today}/${s.daily_cap}</b></span>` +
      (s.queue ? `<span>queue <b class="num">${s.queue}</b></span>` : "");
  } catch { /* keep the last reading */ }
}

// ---- datasets ---------------------------------------------------------------------------------
async function loadDatasets() {
  datasets = await (await fetch("/api/datasets")).json();
  $("datasets").innerHTML = datasets.map((d) => `
    <button class="ds" type="button" data-name="${esc(d.name)}" aria-pressed="false">
      <span class="n">${esc(d.name)}</span>${d.name === "prompt_injection" ? '<span class="tag">red team</span>' : "<span></span>"}
      <span class="d">${esc(d.description)}</span>
    </button>`).join("");
  $("datasets").querySelectorAll(".ds").forEach((b) => b.addEventListener("click", () => pick(b.dataset.name)));
  pick(store.get("arz.dataset") && datasets.some((d) => d.name === store.get("arz.dataset")) ? store.get("arz.dataset") : "prompt_injection");
}

function pick(name) {
  selected = datasets.find((d) => d.name === name);
  store.set("arz.dataset", name);
  $("datasets").querySelectorAll(".ds").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.name === name)));
  const n = $("notes");
  n.hidden = !selected;
  if (selected) {
    n.className = "notes" + (name === "prompt_injection" ? " hostile" : "");
    n.innerHTML = `<b>${esc(selected.asset_id)} · ${esc(selected.rpm)} rpm · technician notes</b>${esc(selected.notes)}`;
  }
}

// ---- run --------------------------------------------------------------------------------------
async function run() {
  const hint = $("hint");
  hint.className = "hint";
  $("run").disabled = true;
  try {
    const r = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify({ dataset: selected.name, code: $("code").value }) });
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
    store.set("arz.code", $("code").value);
    hint.textContent = "Started. The sandbox is network-less; watch the right lane.";
    location.hash = `job=${body.id}`;
  } catch (e) {
    hint.className = "hint err";
    hint.textContent = e.message;
  } finally {
    $("run").disabled = false;
    loadRuns();
  }
}

// ---- past runs --------------------------------------------------------------------------------
async function loadRuns() {
  const jobs = await (await fetch("/api/jobs")).json();
  $("runs").innerHTML = jobs.map((j) => {
    const z = j.result ? j.result.iso_zone : null;
    const chip = j.status === "failed" ? '<span class="chip fail">failed</span>'
      : j.status !== "done" ? `<span class="chip">${esc(j.status)}</span>`
      : `<span class="chip z${esc(z)}">${esc(j.result.fault)} · ${esc(z)}</span>`;
    return `<a href="#job=${esc(j.id)}" ${current === j.id ? 'aria-current="true"' : ""}>
      <span class="rn">${esc(j.dataset)}</span>${chip}<span class="rt">${esc((j.created_at || "").replace("T", " ").slice(0, 16))} UTC</span></a>`;
  }).join("") || '<p class="hint">No runs yet.</p>';
}

// ---- timeline ---------------------------------------------------------------------------------
function describe(e) {
  const d = e.data || {};
  const kv = (o) => Object.entries(o).map(([k, v]) => `${k} <code>${esc(v)}</code>`).join(" · ");
  switch (e.kind) {
    case "job_created": return { t: "Job created", s: `dataset <code>${esc(d.dataset)}</code> · model <code>${esc(d.model)}</code>, escalation <code>${esc(d.escalation_model)}</code>` };
    case "reference_computed": return { t: "Reference diagnosis on the control plane", s: `${esc(d.primary_fault)} · zone ${esc(d.iso_zone)} · ${esc(d.worst_radial_mm_s)} mm/s. The yardstick for every claim below.` };
    case "llm_call": return { t: `Model call · ${esc(d.purpose)}`, s: `<code>${esc(d.model)}</code> · ${esc(d.latency_s)} s · ${esc(d.prompt_tokens)}→${esc(d.completion_tokens)} tokens · request <code>${short(d.request_sha256)}</code>` };
    case "plan": return { t: "Plan", cls: d.notes_contain_instructions ? "warn" : "", html: `<ul>${(d.steps || []).map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` +
      (d.notes_contain_instructions ? '<div class="flags"><span class="flag">notes contain instructions: not followed</span></div>' : "") };
    case "sandbox_requested": return { t: "Sandbox requested from the warm pool", s: "A VM booted ahead of time, used for this one job only." };
    case "sandbox_assigned": return { t: "Sandbox VM assigned", s: `${esc(d.label)} · VPC <code>${esc(d.vpc_ip)}</code> · warm for ${esc(d.ready_for_s)} s · snapshot <code>${short(d.snapshot_id)}</code>` };
    case "sandbox_created": return { t: "Sandbox VM created", s: `${esc(d.plan)} · ${esc(d.region)} · snapshot <code>${short(d.snapshot_id)}</code>` };
    case "sandbox_active": return { t: "VM active", s: `after ${esc(d.after_s)} s` };
    case "sandbox_ready": return { t: "VM ready over the private VPC", s: d.vpc_ip ? `<code>${esc(d.vpc_ip)}</code>, SSH from the control plane only` : "local Podman (development)" };
    case "job_uploaded": return { t: "Inputs uploaded", s: Object.entries(d.files || {}).map(([k, v]) => `<code>${esc(k)}</code> ${short(v)}`).join(" · ") };
    case "code_written": return { t: `Script written · attempt ${esc(d.attempt)}`, cls: (d.review || []).length ? "warn" : "",
      s: `${esc(d.chars)} characters · sha256 <code>${short(d.sha256)}</code>`,
      html: (d.review || []).length ? `<div class="flags">${d.review.map((f) => `<span class="flag">${esc(f)}</span>`).join("")}</div>` : "" };
    case "file_added": return { t: "Script copied in", s: `<code>${esc(d.path)}</code>` };
    case "step_ran": {
      const l = d.limits || {};
      const ok = d.exit_code === 0 && !d.timed_out && !d.oom_killed;
      return { t: `Ran in the locked container · exit ${esc(d.exit_code)}`, cls: ok ? "good" : "bad",
        s: `no network · read-only · ${esc(l.memory)} · ${esc(l.pids)} pids · ${esc(l.timeout_s)} s` + (d.timed_out ? " · <b>timed out</b>" : "") + (d.oom_killed ? " · <b>OOM-killed</b>" : "") +
           ` · ${(d.artifacts || []).length} artifact(s)` };
    }
    case "outputs_pulled": return { t: d.all_hashes_match ? "Outputs pulled, hashes match" : "Outputs pulled: HASH MISMATCH", cls: d.all_hashes_match ? "good" : "bad", s: `${esc(d.files)} files, every SHA-256 recomputed on the control plane` };
    case "attempt_failed": return { t: `Attempt ${esc(d.attempt)} failed: ${esc(d.reason)}`, cls: "bad", html: d.stderr_tail ? `<pre>${esc(d.stderr_tail)}</pre>` : "", s: "The error goes back to the model for a corrected script." };
    case "attempt_succeeded": return { t: `Attempt ${esc(d.attempt)} succeeded`, cls: "good" };
    case "sandbox_destroyed": return d.local ? { t: "Local container workspace removed" } : { t: "Sandbox VM destroyed · 404 verified", cls: d.verified_404 ? "good" : "bad", s: `the API answered 404 ${esc(d.after_s)} s after DELETE` };
    case "sandbox_destroy_failed": return { t: "Sandbox destroy failed", cls: "bad", s: `${esc(d.error)} · the reaper deletes it within 10 minutes` };
    case "sandbox_facts": return null;
    case "integrity_check": return { t: d.ok ? "Script output matches the reference" : "Script output disagrees with the reference", cls: d.ok ? "good" : "bad", html: d.ok ? "" : `<ul>${d.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` };
    case "claims_checked": return { t: d.ok ? "Diagnosis claims pass the deterministic check" : `Claims rejected (round ${esc(d.round)})`, cls: d.ok ? "good" : "bad",
      s: `claimed ${esc(d.claimed_fault)} · zone ${esc(d.claimed_zone)} · priority ${esc(d.claimed_priority)}`, html: d.ok ? "" : `<ul>${d.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` };
    case "work_order": return { t: `Work order drafted · ${esc(d.priority)}`, cls: d.overridden ? "warn" : "", s: `${esc(d.id)}` + (d.overridden ? " · model claims overridden by the check" : "") };
    case "ground_truth_check": return { t: d.match ? "Matches the dataset's ground truth" : "Differs from the ground truth", cls: d.match ? "good" : "bad", s: kv(d.expected || {}) };
    case "job_failed": return { t: "Job failed", cls: "bad", s: esc(d.error) };
    default: return { t: esc(e.kind.replace(/_/g, " ")), s: "" };
  }
}

let alive = false, lastRow = null;
function addEvent(e) {
  if (e.kind === "job_finished") return;
  const v = describe(e);
  if (!v) return;
  if (OPENS.has(e.kind)) alive = true;
  const closing = e.kind === "sandbox_destroyed" || e.kind === "sandbox_destroy_failed";
  const inSb = IN_SANDBOX.has(e.kind) || OPENS.has(e.kind) || closing;
  const row = document.createElement("div");
  row.className = "row" + (alive ? " alive" : "") + (OPENS.has(e.kind) ? " open" : "") + (closing && alive ? " close" : "");
  const time = (e.ts || "").slice(11, 19);
  const card = `<div class="ev ${v.cls || ""}"><div class="t"><b>${v.t}</b><time>${esc(time)}</time></div>` +
    (v.s ? `<div class="s">${v.s}</div>` : "") + (v.html || "") +
    (e.hash ? `<div class="hash"><i>#${esc(e.seq)}</i> ${short(e.prev)} → ${short(e.hash)}</div>` : "") + "</div>";
  row.innerHTML = inSb ? `<div class="cell cp"></div><div class="cell sb">${card}</div>` : `<div class="cell cp">${card}</div><div class="cell sb"></div>`;
  $("timeline").appendChild(row);
  lastRow = row;
  if (closing) alive = false;
  if (document.visibilityState === "visible" && current && isLive) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// ---- verdict + results ------------------------------------------------------------------------
function scaleHtml(v, b) {
  const max = Math.max(b["C/D"] * 1.6, v * 1.15);
  const pos = (x) => `${(100 * Math.min(x, max) / max).toFixed(2)}%`;
  const w = [b["A/B"], b["B/C"] - b["A/B"], b["C/D"] - b["B/C"], max - b["C/D"]].map((x) => `${(100 * x / max).toFixed(2)}%`);
  return `<div class="scale" role="img" aria-label="${v} mm/s on the ISO 20816-3 scale">
    <div class="bands">${w.map((x) => `<i style="width:${x}"></i>`).join("")}</div>
    <div class="needle" style="left:${pos(v)}"></div><div class="readout num" style="left:${pos(v)}">${v.toFixed(2)} mm/s</div>
    <div class="ticks">${["A/B", "B/C", "C/D"].map((k) => `<span style="left:${pos(b[k])}">${b[k]}</span>`).join("")}</div></div>`;
}

async function showResults(jid, report) {
  if (!report || !report.files) return;
  const wo = await (await fetch(`/api/jobs/${jid}/files/work_order_json`)).json();
  const m = wo.measured;
  $("verdict").hidden = false;
  $("verdict").innerHTML = `
    <div><div class="k">Diagnosis</div><div class="fault">${esc(wo.fault.replace(/_/g, " "))}</div></div>
    <div><div class="k">ISO 20816-3 · worst radial axis ${esc(m.axis)} · zone ${esc(wo.iso_zone)}</div>${scaleHtml(m.worst_radial_mm_s, m.boundaries_mm_s)}</div>
    <div><div class="k">Priority</div><div class="prio ${esc(wo.priority)}">${esc(wo.priority)}</div></div>`;
  const rec = report.receipt || {};
  const st = await (await fetch("/api/status")).json();
  $("results").hidden = false;
  $("results").innerHTML = `
    <article class="paper">
      <div class="wo-id">${esc(wo.id)} · draft for planner approval</div>
      <h3>${esc(wo.title)}</h3>
      <dl><dt>Asset</dt><dd>${esc(wo.asset_id)}: ${esc(wo.asset)}</dd>
          <dt>Failure mode</dt><dd>${esc(wo.failure_mode)}</dd>
          <dt>Measured</dt><dd>${esc(m.worst_radial_mm_s.toFixed(2))} mm/s RMS, axis ${esc(m.axis)}, zone ${esc(wo.iso_zone)}</dd>
          <dt>Priority</dt><dd><b>${esc(wo.priority)}</b></dd></dl>
      <p>${esc(wo.summary)}</p>
      <h4>Actions</h4><ol>${wo.actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ol>
      <h4>Parts</h4><ul>${(wo.parts.length ? wo.parts : ["none"]).map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
      <h4>Safety</h4><p>${esc(wo.safety)}</p>
      <h4>Evidence</h4><ul>${wo.evidence.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      ${wo.llm_claims_rejected && wo.llm_claims_rejected.length ? `<h4>Corrected by the deterministic check</h4><ul>${wo.llm_claims_rejected.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      ${wo.notes_contained_instructions ? '<div class="stamp">The technician notes contained instructions addressed to the agent. They were not followed.</div>' : ""}
    </article>
    <div class="aside">
      ${report.files.spectrum ? `<figure class="figure" style="margin:0"><img src="/api/jobs/${jid}/files/spectrum" alt="Velocity spectrum of the worst radial axis, drawn by the agent's script in the sandbox"></figure>` : ""}
      <div class="receipt">
        <p class="label">Signed receipt</p>
        <div class="rk"><span>Events in the hash chain</span><span>${esc(rec.events)}</span></div>
        <div class="rk"><span>Signature</span><span>${rec.verified ? "verifies" : "does not verify"}</span></div>
        <div class="rk"><span>Signing key (Ed25519)</span><span>${short(st.receipt_key)}…</span></div>
        <div class="rk"><span>Model calls</span><span>${esc(report.llm_calls)}</span></div>
        <div class="actions"><a class="ghost" href="/api/jobs/${jid}/files/receipt">Download receipt.zip</a><a class="ghost" href="/verify">Verify it</a></div>
      </div>
    </div>`;
}

// ---- open a job -------------------------------------------------------------------------------
let isLive = false;
async function openJob(jid) {
  if (source) source.close();
  current = jid; alive = false; isLive = false;
  $("empty").hidden = true; $("job").hidden = false;
  $("timeline").innerHTML = ""; $("verdict").hidden = true; $("results").hidden = true;
  $("jobid").textContent = jid;
  const info = await (await fetch(`/api/jobs/${jid}`)).json();
  isLive = info.status === "queued" || info.status === "running";
  setState(info.status);
  source = new EventSource(`/api/jobs/${jid}/events`);
  source.onmessage = async (m) => {
    const e = JSON.parse(m.data);
    if (e.kind === "job_finished") {
      source.close();
      const fin = await (await fetch(`/api/jobs/${jid}`)).json();
      setState(fin.status, fin);
      if (fin.report && fin.report.ok) await showResults(jid, fin.report);
      loadRuns();
      return;
    }
    if (isLive) setState("running");
    addEvent(e);
  };
  loadRuns();
}

function setState(status, fin) {
  const el = $("jobstate");
  el.className = "state" + (status === "running" || status === "queued" ? " live" : "");
  const r = fin && fin.report;
  el.textContent = status === "queued" ? "queued" : status === "running" ? "running" :
    status === "done" ? `finished in ${r ? r.duration_s : "?"} s` : `failed${r && r.error ? `: ${r.error}` : ""}`;
}

function route() {
  const m = location.hash.match(/job=([\w-]+)/);
  if (m) openJob(m[1]);
}

$("run").addEventListener("click", run);
$("code").value = store.get("arz.code") || "";
window.addEventListener("hashchange", route);
loadDatasets().then(route);
loadRuns();
refreshStatus();
setInterval(refreshStatus, 10000);
