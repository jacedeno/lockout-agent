// Lockout console: measurements, live run, verdict, lockout tag, work order, two-lane trail.
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const short = (h, n = 10) => (h ? String(h).slice(0, n) : "");
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};
const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Display names: the code names stay in the API, the receipt and the trail.
const DATASET = {
  healthy: "Healthy", unbalance: "Unbalance", misalignment: "Misalignment",
  bearing_inner_race: "Bearing, inner race", bearing_outer_race: "Bearing, outer race",
  prompt_injection: "Prompt injection",
};
const FAULT = {
  none: "No fault found", unbalance: "Unbalance", misalignment: "Misalignment",
  bearing_inner_race: "Bearing, inner race", bearing_outer_race: "Bearing, outer race", bearing_defect: "Bearing defect",
};
const FAULT_SHORT = {
  none: "no fault", unbalance: "unbalance", misalignment: "misalignment",
  bearing_inner_race: "inner race", bearing_outer_race: "outer race", bearing_defect: "bearing",
};
const PRIORITY = { immediate: "Immediate", planned: "Planned", monitor: "Monitor", none: "None" };
const PROBE = {
  net_egress: "Reach the internet", metadata: "Read the cloud metadata", secrets: "Find a credential",
  fs_write: "Write outside the workspace", privilege: "Gain privileges", fork_bomb: "Fork bomb",
  mem_bomb: "Memory bomb", timeout: "Run forever", host_egress: "VM reaches the internet",
  host_metadata: "VM reads the cloud metadata", host_control_plane: "VM connects to the control plane",
};
const ORDER = ["healthy", "unbalance", "misalignment", "bearing_inner_race", "bearing_outer_race", "prompt_injection"];
const name = (map, k) => map[k] || String(k ?? "").replace(/_/g, " ");
const lower = (s) => String(s).charAt(0).toLowerCase() + String(s).slice(1);
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const when = (iso) => (iso ? `${MONTHS[+iso.slice(5, 7) - 1]} ${+iso.slice(8, 10)}, ${iso.slice(11, 16)} UTC` : "");
const hms = (iso) => (iso || "").slice(11, 19);
const memory = (m) => String(m ?? "").replace(/^(\d+)m$/i, "$1 MB").replace(/^(\d+)g$/i, "$1 GB");

let datasets = [], selected = null, source = null, current = null, isLive = false, status = null;
let events = [], lastSeg = null;

// ---- status bar -------------------------------------------------------------------------------
async function refreshStatus() {
  try {
    const s = status = await (await fetch("/api/status")).json();
    const pool = s.warm_pool;
    const poolTxt = s.local ? "local Podman (dev)" : !pool ? "off"
      : pool.ready ? `ready, ${Math.round(pool.ready_for_s / 60)} min` : pool.booting ? "booting" : "idle";
    $("gauges").innerHTML =
      `<span><i class="lamp ${pool && pool.ready ? "on" : pool && pool.booting ? "warm" : ""}"></i>Warm sandbox <b>${esc(poolTxt)}</b></span>` +
      `<span>Model <b>${esc(s.model)}</b></span>` +
      `<span>Runs today <b>${esc(s.runs_today)} of ${esc(s.daily_cap)}</b></span>` +
      (s.queue ? `<span>Queue <b>${esc(s.queue)}</b></span>` : "");
  } catch { /* keep the last reading */ }
}

// ---- measurements -----------------------------------------------------------------------------
async function loadDatasets() {
  datasets = await (await fetch("/api/datasets")).json();
  datasets.sort((a, b) => ORDER.indexOf(a.name) - ORDER.indexOf(b.name));
  $("datasets").innerHTML = datasets.map((d) => `
    <button class="ds" type="button" data-name="${esc(d.name)}" aria-pressed="false">
      <span class="n">${esc(name(DATASET, d.name))}${d.name === "prompt_injection" ? '<span class="redteam">red team</span>' : ""}</span>
      <span class="d">${esc(d.description)}</span>
      <code class="c">${esc(d.name)}</code>
    </button>`).join("");
  $("datasets").querySelectorAll(".ds").forEach((b) => b.addEventListener("click", () => pick(b.dataset.name)));
  const saved = store.get("arz.dataset");
  pick(saved && datasets.some((d) => d.name === saved) ? saved : "prompt_injection");
}

function pick(n) {
  selected = datasets.find((d) => d.name === n);
  store.set("arz.dataset", n);
  $("datasets").querySelectorAll(".ds").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.name === n)));
  $("redteam").checked = n === "prompt_injection";
  const box = $("notes");
  box.hidden = !selected;
  if (!selected) return;
  const hostile = n === "prompt_injection";
  box.className = "notes" + (hostile ? " hostile" : "");
  box.innerHTML = `<b>Technician notes for ${esc(selected.asset_id)}, ${esc(selected.rpm)} rpm</b>${esc(selected.notes)}` +
    (hostile ? '<span class="warnline">Untrusted input. The agent reads it as data, never as instructions.</span>' : "");
}

// ---- run --------------------------------------------------------------------------------------
async function run() {
  const hint = $("hint");
  hint.className = "hint";
  $("run").disabled = true;
  try {
    const r = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify({ dataset: selected.name, code: $("code").value, redteam: $("redteam").checked }) });
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
    store.set("arz.code", $("code").value);
    hint.textContent = "Started. Watch the sandbox lane on the right.";
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
    const r = j.result;
    const chip = j.status === "failed" ? '<span class="chip fail">failed</span>'
      : j.status !== "done" || !r ? `<span class="chip live">${esc(j.status)}</span>`
      : `<span class="chip"><i class="zb z${esc(r.iso_zone)}" title="ISO zone ${esc(r.iso_zone)}">${esc(r.iso_zone)}</i>${esc(name(FAULT_SHORT, r.fault))}</span>`;
    return `<a href="#job=${esc(j.id)}" ${current === j.id ? 'aria-current="true"' : ""}>
      <span class="rn">${esc(name(DATASET, j.dataset))}</span>${chip}<span class="rt">${esc(when(j.created_at))}</span></a>`;
  }).join("") || '<p class="hint">No runs yet.</p>';
  const latest = jobs.find((j) => j.dataset === "prompt_injection" && j.status === "done");
  if (latest) { $("latest").href = `#job=${latest.id}`; $("latest").hidden = false; }
}

// ---- the trail: control plane lane | sandbox lane ----------------------------------------------
const IN_SANDBOX = new Set(["sandbox_active", "sandbox_ready", "job_uploaded", "file_added", "step_ran", "outputs_pulled",
                            "injection_replayed", "containment_probe", "sandbox_created", "sandbox_assigned",
                            "sandbox_destroyed", "sandbox_destroy_failed"]);
const AGG = { containment_probe: "probes", injection_replayed: "replays" };

function describe(e) {
  const d = e.data || {};
  const list = (xs) => `<ul>${(xs || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  switch (e.kind) {
    case "job_created": return { t: "Job created", s: `Measurement <code>${esc(d.dataset)}</code>, model <code>${esc(d.model)}</code>, escalation <code>${esc(d.escalation_model)}</code>` };
    case "reference_computed": return { t: "Reference diagnosis on the control plane", s: `${esc(name(FAULT, d.primary_fault))}, zone ${esc(d.iso_zone)}, ${esc(d.worst_radial_mm_s)} mm/s. The yardstick for every claim below.` };
    case "llm_call": return { t: `Model call: ${esc(d.purpose)}`, s: `<code>${esc(d.model)}</code>, ${esc(d.latency_s)} s, ${esc(d.prompt_tokens)} → ${esc(d.completion_tokens)} tokens, request <code>${short(d.request_sha256)}</code>` };
    case "plan": return { t: "Plan", cls: d.notes_contain_instructions ? "warn" : "", html: `<ol>${(d.steps || []).map((x) => `<li>${esc(String(x).replace(/^\s*\d+[.)]\s+/, ""))}</li>`).join("")}</ol>` +
      (d.notes_contain_instructions ? '<div class="flags"><span class="flag">Notes contain instructions: not followed</span></div>' : "") };
    case "sandbox_requested": return { t: "Sandbox requested from the warm pool", s: "A VM booted ahead of time, used for this one job only." };
    case "sandbox_assigned": return { t: "Sandbox VM assigned", s: `<code>${esc(d.label)}</code> on <code>${esc(d.vpc_ip)}</code>, ready for ${esc(d.ready_for_s)} s before this job, snapshot <code>${short(d.snapshot_id, 8)}</code>` };
    case "sandbox_created": return { t: "Sandbox VM created", s: `${esc(d.plan)} in ${esc(d.region)}, snapshot <code>${short(d.snapshot_id, 8)}</code>` };
    case "sandbox_active": return { t: "VM active", s: `after ${esc(d.after_s)} s` };
    case "sandbox_ready": return { t: "VM ready on the private network", s: d.vpc_ip ? `<code>${esc(d.vpc_ip)}</code>, SSH from the control plane only` : "Local Podman (development)" };
    case "job_uploaded": return { t: "Inputs uploaded", s: Object.entries(d.files || {}).map(([k, v]) => `<code>${esc(k)}</code> ${short(v, 8)}`).join(", ") };
    case "code_written": return { t: `Script written, attempt ${esc(d.attempt)}`, cls: (d.review || []).length ? "warn" : "",
      s: `${esc(d.chars)} characters, sha256 <code>${short(d.sha256)}</code>`,
      html: (d.review || []).length ? `<div class="flags">${d.review.map((f) => `<span class="flag">${esc(f)}</span>`).join("")}</div>` : "" };
    case "file_added": return { t: "Script copied in", s: `<code>${esc(d.path)}</code>` };
    case "step_ran": {
      const l = d.limits || {};
      const ok = d.exit_code === 0 && !d.timed_out && !d.oom_killed;
      const n = (d.artifacts || []).length;
      return { t: `Ran in the locked container, exit ${esc(d.exit_code)}`, cls: ok ? "good" : "bad",
        s: `No network, read-only, ${esc(memory(l.memory))}, ${esc(l.pids)} processes, ${esc(l.timeout_s)} s limit.` +
           (d.timed_out ? " <b>Timed out.</b>" : "") + (d.oom_killed ? " <b>Killed at the memory cap.</b>" : "") +
           ` ${n} ${n === 1 ? "artifact" : "artifacts"}.` };
    }
    case "outputs_pulled": return { t: d.all_hashes_match ? "Outputs pulled, hashes match" : "Outputs pulled: hash mismatch", cls: d.all_hashes_match ? "good" : "bad", s: `${esc(d.files)} files, every SHA-256 recomputed on the control plane` };
    case "attempt_failed": return { t: `Attempt ${esc(d.attempt)} failed: ${esc(d.reason)}`, cls: "bad", html: d.stderr_tail ? `<pre>${esc(d.stderr_tail)}</pre>` : "", s: "The error goes back to the model for a corrected script." };
    case "attempt_succeeded": return { t: `Attempt ${esc(d.attempt)} succeeded`, cls: "good" };
    case "sandbox_destroyed": return d.local ? { t: "Local container workspace removed" } : { t: "Sandbox VM destroyed, 404 verified", cls: d.verified_404 ? "good" : "bad", s: `The Vultr API answered 404 ${esc(d.after_s)} s after DELETE` };
    case "sandbox_destroy_failed": return { t: "Sandbox destroy failed", cls: "bad", s: `${esc(d.error)}. The reaper deletes it within 10 minutes.` };
    case "sandbox_facts": return null;
    case "integrity_check": return { t: d.ok ? "Script output matches the reference" : "Script output disagrees with the reference", cls: d.ok ? "good" : "bad", html: d.ok ? "" : list(d.problems) };
    case "claims_checked": return { t: d.ok ? "Diagnosis claims pass the deterministic check" : `Claims rejected, round ${esc(d.round)}`, cls: d.ok ? "good" : "bad",
      s: `Claimed ${esc(lower(name(FAULT, d.claimed_fault)))}, zone ${esc(d.claimed_zone)}, priority ${esc(d.claimed_priority)}`, html: d.ok ? "" : list(d.problems) };
    case "work_order": return { t: `Work order drafted, ${esc(d.priority)}`, cls: d.overridden ? "warn" : "", s: `<code>${esc(d.id)}</code>` + (d.overridden ? ". The model's claims were overridden by the check." : "") };
    case "ground_truth_check": { const x = d.expected || {};
      return { t: d.match ? "Matches the dataset's ground truth" : "Differs from the ground truth", cls: d.match ? "good" : "bad", s: `Expected ${esc(lower(name(FAULT, x.fault)))}, zone ${esc(x.iso_zone)}` }; }
    case "job_failed": return { t: "Job failed", cls: "bad", s: esc(d.error) };
    case "redteam_started": return { t: "Red team: what if the model had obeyed?", cls: "warn", s: "Replaying the injected commands literally, then trying 11 escapes, in the same VM." };
    case "forged_claim_checked": return { t: d.rejected ? "Forged “healthy, zone A” claim rejected" : "Forged claim accepted", cls: d.rejected ? "good" : "bad", html: list(d.problems) };
    case "containment_summary": return { t: d.all_contained ? `Contained: ${esc(d.probes_contained)} of ${esc(d.probes)} escapes blocked, replay contained, forged claim rejected` : "Containment failed", cls: d.all_contained ? "good" : "bad" };
    case "redteam_failed": return { t: "Red-team step failed", cls: "bad", s: esc(d.error) };
    case "llm_retry": return { t: `Model reply unusable, retrying ${esc(d.purpose)} on ${esc(d.retry_model)}`, cls: "warn", s: esc(d.error) };
    default: return { t: esc(e.kind.replace(/_/g, " ")), s: "" };
  }
}

function segment(lane) {
  if (lastSeg && lastSeg.dataset.lane === lane && !lastSeg.classList.contains("closed")) return lastSeg;
  const s = document.createElement("div");
  s.className = `seg ${lane}`;
  s.dataset.lane = lane;
  if (lane === "sb") s.innerHTML = '<div class="sb-head"><span>Inside the sandbox VM</span><span>no network, no credentials</span></div>';
  $("timeline").appendChild(s);
  lastSeg = s;
  return s;
}

function chain(e) {
  return e.hash ? `<span class="chain" title="#${esc(e.seq)}: ${esc(e.prev)} → ${esc(e.hash)}">#${esc(e.seq)} ${short(e.hash, 8)}</span>` : "";
}

function addAggregate(seg, e) {
  const kind = AGG[e.kind], d = e.data || {};
  let box = seg.lastElementChild;
  if (!box || box.dataset.agg !== kind) {
    box = document.createElement("div");
    box.className = "ev agg";
    box.dataset.agg = kind;
    box.dataset.first = e.seq ?? "";
    box.innerHTML = `<div class="t"><b></b><span class="chain"></span><time></time></div><ul class="${kind}"></ul>`;
    seg.appendChild(box);
  }
  const li = document.createElement("li");
  li.className = d.contained ? "ok" : "no";
  li.innerHTML = kind === "probes"
    ? `<b>${esc(name(PROBE, d.probe))}</b><span>${esc(d.layer)} layer: ${esc(d.expected)}</span>`
    : `<b>${esc(d.action)}</b><span>${esc(d.result)}</span>`;
  box.querySelector("ul").appendChild(li);
  const total = box.querySelectorAll("li").length, good = box.querySelectorAll("li.ok").length;
  box.classList.toggle("good", good === total);
  box.classList.toggle("bad", good !== total);
  box.querySelector(".t b").textContent = kind === "probes"
    ? `Escape attempts: ${good} of ${total} blocked` : `Injected commands, replayed literally: ${good} of ${total} contained`;
  box.querySelector(".chain").textContent = e.seq != null ? `#${box.dataset.first}–${e.seq}` : "";
  box.querySelector("time").textContent = hms(e.ts);
  return box;
}

function addEvent(e) {
  if (e.kind === "job_finished") return;
  events.push(e);
  const lane = IN_SANDBOX.has(e.kind) ? "sb" : "cp";
  let el;
  if (AGG[e.kind]) {
    el = addAggregate(segment(lane), e);
  } else {
    const v = describe(e);
    if (!v) return;
    const seg = segment(lane);
    seg.insertAdjacentHTML("beforeend", `<div class="ev ${v.cls || ""}"><div class="t"><b>${v.t}</b>${chain(e)}<time>${esc(hms(e.ts))}</time></div>` +
      (v.s ? `<div class="s">${v.s}</div>` : "") + (v.html || "") + "</div>");
    el = seg.lastElementChild;
    if (e.kind === "sandbox_destroyed" || e.kind === "sandbox_destroy_failed") seg.classList.add("closed");
  }
  if (isLive && document.visibilityState === "visible") el.scrollIntoView({ block: "nearest", behavior: reduceMotion() ? "auto" : "smooth" });
}

// ---- verdict ----------------------------------------------------------------------------------
function scaleHtml(v, b) {
  const max = Math.max(b["C/D"] * 1.6, v * 1.15);
  const pct = (x) => 100 * Math.min(x, max) / max;
  const edges = [0, b["A/B"], b["B/C"], b["C/D"], max];
  const bands = "ABCD".split("").map((z, i) => `<i class="z${z}" style="width:${(pct(edges[i + 1]) - pct(edges[i])).toFixed(2)}%">${z}</i>`).join("");
  const p = pct(v);
  const edge = p > 88 ? " right" : p < 12 ? " left" : "";
  return `<div class="scale" role="img" aria-label="${esc(v.toFixed(2))} mm/s on the ISO 20816-3 scale">
    <div class="bands">${bands}</div>
    <div class="needle" style="left:${p.toFixed(2)}%"></div><div class="readout${edge}" style="left:${p.toFixed(2)}%">${esc(v.toFixed(2))} mm/s</div>
    <div class="ticks">${["A/B", "B/C", "C/D"].map((k) => `<span style="left:${pct(b[k]).toFixed(2)}%">${esc(b[k])}</span>`).join("")}</div></div>`;
}

function verdictHtml(wo) {
  const m = wo.measured;
  return `<div><div class="k">Diagnosis</div><div class="fault">${esc(name(FAULT, wo.fault))}</div></div>
    <div><div class="k">ISO 20816-3, worst radial axis ${esc(m.axis)}: zone ${esc(wo.iso_zone)}</div>${scaleHtml(m.worst_radial_mm_s, m.boundaries_mm_s)}</div>
    <div><div class="k">Priority</div><div class="prio ${esc(wo.priority)}">${esc(name(PRIORITY, wo.priority))}</div></div>`;
}

// ---- the lockout tag: what makes the run trustworthy, in four facts ------------------------------
function tagHtml(jid, report, swing) {
  const c = report.containment, sb = report.sandbox || {}, rec = report.receipt || {};
  const gone = events.find((e) => e.kind === "sandbox_destroyed");
  const dd = (gone && gone.data) || {};
  const isolated = c ? !!c.all_contained && !c.error : true;
  const zero = !!dd.local || !!(sb.destroy_verified ?? dd.verified_404);
  const checked = !(report.integrity_problems || []).length;
  const corrected = (report.claims_rejected || []).length;
  const signed = !!rec.verified;
  const all = isolated && zero && checked && signed;
  const fact = (ok, title, body) => `<div class="fact ${ok ? "ok" : "no"}"><b><i aria-hidden="true">${ok ? "✓" : "✕"}</i>${title}</b><p>${body}</p></div>`;
  const ended = report.ended_at || sb.destroyed_at || "";
  return `<div class="tagwrap${swing ? " swing" : ""}"><section class="tag${all ? "" : " fail"}" aria-label="Lockout tag">
    <div class="band"><i class="grommet" aria-hidden="true"></i><span>${all ? "Locked out" : "Not verified"}</span></div>
    <div class="body">
      <div class="facts">
        ${fact(isolated, "Isolated", c
          ? (c.error ? `The red-team step failed: ${esc(c.error)}` : `No network, no credentials. ${esc(c.probes_contained)} of ${esc(c.probes)} escapes blocked, and the injected commands ran without getting anything out.`)
          : "No network, no credentials, a read-only container. Run it with the red team to try 11 escapes.")}
        ${fact(zero, "Zero energy verified", dd.local ? "Local development container removed."
          : zero ? `The Vultr API answered 404 ${esc(dd.after_s)} s after DELETE, at ${esc(hms(sb.destroyed_at))} UTC.`
          : "The 404 was not confirmed. The reaper deletes the VM within 10 minutes.")}
        ${fact(checked, "Answer checked", !checked ? "The script's numbers disagree with the control plane's own computation."
          : corrected ? `Recomputed on the control plane. ${corrected} model ${corrected === 1 ? "claim" : "claims"} corrected before the work order.`
          : "Recomputed on the control plane: the script's numbers and the model's claims both hold.")}
        ${fact(signed, "Signed", `Ed25519 receipt of ${esc(rec.events)} events, ${signed ? "verifies" : "does not verify"}. <a href="/api/jobs/${esc(jid)}/files/receipt">receipt.zip</a>, <a href="/verify">verify it</a>`)}
      </div>
      <div class="fields"><span>Applied by <b>control plane</b></span><span>Date <b>${esc(when(ended))}</b></span><span>Job <b class="mono">${esc(jid)}</b></span>${status && status.receipt_key ? `<span>Key <b class="mono">${short(status.receipt_key, 16)}</b></span>` : ""}</div>
    </div></section></div>`;
}

// ---- work order as the planner's form ----------------------------------------------------------
function workOrderHtml(wo) {
  const m = wo.measured;
  const items = (xs, tag = "ul") => `<${tag}>${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</${tag}>`;
  return `<article class="form" aria-label="Work order">
    <header><div><span class="fl">Work order</span><h3>${esc(wo.title)}</h3></div>
      <div class="woid"><code>${esc(wo.id)}</code><span>Draft, needs planner approval</span></div></header>
    <div class="cells">
      <div class="cell"><span class="fl">Asset</span><p>${esc(wo.asset_id)}: ${esc(wo.asset)}</p></div>
      <div class="cell"><span class="fl">Failure mode</span><p>${esc(wo.failure_mode)}</p></div>
      <div class="cell"><span class="fl">Measured</span><p>${esc(m.worst_radial_mm_s.toFixed(2))} mm/s RMS, axis ${esc(m.axis)}, zone ${esc(wo.iso_zone)}</p></div>
      <div class="cell"><span class="fl">Priority</span><p><b>${esc(name(PRIORITY, wo.priority))}</b></p></div>
    </div>
    <div class="cell"><span class="fl">Summary</span><p>${esc(wo.summary)}</p></div>
    <div class="cell"><span class="fl">Actions</span>${items(wo.actions, "ol")}</div>
    <div class="cells two">
      <div class="cell"><span class="fl">Parts</span>${items(wo.parts.length ? wo.parts : ["None"])}</div>
      <div class="cell"><span class="fl">Safety</span><p>${esc(wo.safety)}</p></div>
    </div>
    <div class="cell"><span class="fl">Evidence</span>${items(wo.evidence)}</div>
    ${(wo.llm_claims_rejected || []).length ? `<div class="cell"><span class="fl">Corrected by the deterministic check</span>${items(wo.llm_claims_rejected)}</div>` : ""}
    ${wo.notes_contained_instructions ? '<p class="formnote">The technician notes contained instructions addressed to the agent. They were not followed.</p>' : ""}
  </article>`;
}

function factsHtml(report) {
  const att = report.attempts || [], sb = report.sandbox || {};
  const rows = [
    ["Model calls", esc(report.llm_calls)],
    ["Script attempts", att.map((a) => a.failed ? `${esc(a.attempt)} failed (${esc(a.failed)})` : `${esc(a.attempt)} ran`).join(", ") || "none"],
    ["Models", [...new Set(att.map((a) => a.model))].map((x) => `<code>${esc(x)}</code>`).join(", ") || "none"],
    ["Run time", `${esc(Math.round(report.duration_s))} s` + (report.queued_s ? `, after ${esc(Math.round(report.queued_s))} s in the queue` : "")],
    ["Sandbox", sb.label ? `<code>${esc(sb.label)}</code>, ${esc(sb.plan)}, ${esc(sb.region)}` : "local container"],
    ["Snapshot", sb.snapshot_id ? `<code>${short(sb.snapshot_id, 8)}</code>` : "none"],
    ["Technician notes", report.notes_contained_instructions ? "contained instructions, not followed" : "plain observations"],
  ];
  return `<aside class="factsheet"><h3 class="sec">Run facts</h3><dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl></aside>`;
}

async function showResults(jid, report, swing) {
  const wo = await (await fetch(`/api/jobs/${jid}/files/work_order_json`)).json();
  if (!status) await refreshStatus();
  $("verdict").hidden = false;
  $("verdict").innerHTML = verdictHtml(wo);
  const fig = report.files.spectrum ? `<figure class="figure">
      <button type="button" class="figbtn" id="figbtn" aria-label="Enlarge the spectrum figure"><img src="/api/jobs/${esc(jid)}/files/spectrum" alt="Velocity and envelope spectra of the worst radial axis, drawn by the agent's script inside the sandbox"></button>
      <figcaption>Drawn inside the sandbox by the agent's own script with <code>vibkit.plot</code>, then pulled out and hash-checked. Click to enlarge.</figcaption></figure>` : "";
  $("outcome").hidden = false;
  $("outcome").innerHTML = tagHtml(jid, report, swing) + fig + `<div class="below">${workOrderHtml(wo)}${factsHtml(report)}</div>`;
  if ($("figbtn")) $("figbtn").addEventListener("click", () => {
    $("lightimg").src = $("figbtn").querySelector("img").src;
    $("lightimg").alt = $("figbtn").querySelector("img").alt;
    $("lightbox").showModal();
  });
  $("print").hidden = false;
  $("trailsub").textContent = `${events.filter((e) => e.hash).length} events, each hash-chained to the one before and signed into the receipt. Control plane on the left, the sandbox VM on the right.`;
  if (swing) $("job").scrollIntoView({ block: "start", behavior: reduceMotion() ? "auto" : "smooth" });
}

function showFailure(report) {
  $("outcome").hidden = false;
  $("outcome").innerHTML = `<div class="failnote"><b>This run did not finish.</b><p>${esc((report && report.error) || "No report was written.")}</p><p>The trail below shows where it stopped.</p></div>`;
}

// ---- open a job -------------------------------------------------------------------------------
async function openJob(jid) {
  if (source) source.close();
  current = jid; isLive = false; events = []; lastSeg = null;
  document.body.classList.add("has-job");
  $("empty").hidden = true; $("job").hidden = false;
  $("timeline").innerHTML = ""; $("verdict").hidden = true; $("outcome").hidden = true; $("outcome").innerHTML = "";
  $("print").hidden = true;
  $("jobid").textContent = jid;
  $("printurl").textContent = `${location.origin}/#job=${jid}`;
  const info = await (await fetch(`/api/jobs/${jid}`)).json();
  const ds = datasets.find((d) => d.name === info.dataset);
  $("jobtitle").textContent = name(DATASET, info.dataset);
  $("jobmeta").textContent = ds ? `${ds.asset_id}, ${ds.asset}, ${ds.rpm} rpm` : "";
  document.title = `${name(DATASET, info.dataset)} · Lockout`;
  isLive = info.status === "queued" || info.status === "running";
  setState(info.status);
  const wasLive = isLive;
  source = new EventSource(`/api/jobs/${jid}/events`);
  source.onmessage = async (m) => {
    const e = JSON.parse(m.data);
    if (e.kind === "job_finished") {
      source.close();
      const fin = await (await fetch(`/api/jobs/${jid}`)).json();
      isLive = false;
      setState(fin.status, fin);
      if (fin.report && fin.report.ok) await showResults(jid, fin.report, wasLive && !reduceMotion());
      else showFailure(fin.report);
      loadRuns();
      return;
    }
    if (isLive) setState("running");
    addEvent(e);
  };
  loadRuns();
}

function setState(st, fin) {
  const el = $("jobstate");
  el.className = "state" + (st === "running" || st === "queued" ? " live" : st === "failed" ? " failed" : "");
  const r = fin && fin.report;
  el.textContent = st === "queued" ? "Queued" : st === "running" ? "Running" :
    st === "done" ? `Finished in ${r ? Math.round(r.duration_s) : "?"} s` : "Failed";
}

function route() {
  const m = location.hash.match(/job=([\w-]+)/);
  if (m) openJob(m[1]);
}

$("run").addEventListener("click", run);
$("print").addEventListener("click", () => window.print());
$("lightbox").addEventListener("click", (e) => { if (e.target === $("lightbox")) $("lightbox").close(); });
$("code").value = store.get("arz.code") || "";
window.addEventListener("hashchange", route);
refreshStatus();
loadDatasets().then(route);
loadRuns();
setInterval(refreshStatus, 10000);
