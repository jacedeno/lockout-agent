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

// Units: US by default (in/s peak derived from RMS, CPM), ISO on request (mm/s RMS, Hz).
// ISO 20816-3 zones are defined in mm/s RMS; they are converted, never changed.
const UNITS = {
  us: { v: "in/s pk", f: "CPM", fk: 60, vk: Math.SQRT2 / 25.4, vd: 3, name: "in/s, CPM" },
  iso: { v: "mm/s RMS", f: "Hz", fk: 1, vk: 1, vd: 2, name: "mm/s, Hz" },
};
let unitSys = store.get("arz.units") === "iso" ? "iso" : "us";
const U = () => UNITS[unitSys];
const vel = (mm) => mm * U().vk;
const fq = (hz) => hz * U().fk;
const fmtV = (mm) => `${vel(mm).toFixed(U().vd)} ${U().v}`;
const fmtF = (hz) => (unitSys === "us" ? `${Math.round(hz * 60).toLocaleString("en-US")} CPM` : `${hz.toFixed(hz < 100 ? 2 : 1)} Hz`);
const num = (v) => String(+v.toFixed(4));
const DEFECT_NAME = { BPFO: "outer race", BPFI: "inner race", BSF: "ball spin", FTF: "cage" };

let datasets = [], selected = null, source = null, current = null, isLive = false, status = null;
let view = null, lastWO = null, specZoom = "10x", specLog = false;
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
function scaleHtml(vmm, bmm) {
  const v = vel(vmm), b = Object.fromEntries(Object.entries(bmm).map(([k, x]) => [k, vel(x)]));
  const max = Math.max(b["C/D"] * 1.6, v * 1.15);
  const pct = (x) => 100 * Math.min(x, max) / max;
  const edges = [0, b["A/B"], b["B/C"], b["C/D"], max];
  const bands = "ABCD".split("").map((z, i) => `<i class="z${z}" style="width:${(pct(edges[i + 1]) - pct(edges[i])).toFixed(2)}%">${z}</i>`).join("");
  const p = pct(v);
  const edge = p > 88 ? " right" : p < 12 ? " left" : "";
  return `<div class="scale" role="img" aria-label="${esc(fmtV(vmm))} on the ISO 20816-3 scale">
    <div class="bands">${bands}</div>
    <div class="needle" style="left:${p.toFixed(2)}%"></div><div class="readout${edge}" style="left:${p.toFixed(2)}%">${esc(fmtV(vmm))}</div>
    <div class="ticks">${["A/B", "B/C", "C/D"].map((k) => `<span style="left:${pct(b[k]).toFixed(2)}%">${esc(b[k].toFixed(U().vd))}</span>`).join("")}</div></div>`;
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
function measuredText(wo) {
  const m = wo.measured, v = m.worst_radial_mm_s;
  return esc(unitSys === "us" ? `${fmtV(v)} (${v.toFixed(2)} mm/s RMS), axis ${m.axis}, zone ${wo.iso_zone}`
    : `${v.toFixed(2)} mm/s RMS, axis ${m.axis}, zone ${wo.iso_zone}`);
}

function workOrderHtml(wo) {
  const m = wo.measured;
  const items = (xs, tag = "ul") => `<${tag}>${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</${tag}>`;
  return `<article class="form" aria-label="Work order">
    <header><div><span class="fl">Work order</span><h3>${esc(wo.title)}</h3></div>
      <div class="woid"><code>${esc(wo.id)}</code><span>Draft, needs planner approval</span></div></header>
    <div class="cells">
      <div class="cell"><span class="fl">Asset</span><p>${esc(wo.asset_id)}: ${esc(wo.asset)}</p></div>
      <div class="cell"><span class="fl">Failure mode</span><p>${esc(wo.failure_mode)}</p></div>
      <div class="cell"><span class="fl">Measured</span><p id="wo-measured">${measuredText(wo)}</p></div>
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
  const [wo, v] = await Promise.all([(await fetch(`/api/jobs/${jid}/files/work_order_json`)).json(), loadView(datasetOf(jid))]);
  if (jid !== current) return;
  lastWO = wo; view = v;
  if (!status) await refreshStatus();
  $("verdict").hidden = false;
  $("verdict").innerHTML = verdictHtml(wo);
  const fig = report.files.spectrum ? `<figure class="figure">
      <button type="button" class="figbtn" id="figbtn" aria-label="Enlarge the agent's figure"><img src="/api/jobs/${esc(jid)}/files/spectrum" alt="The agent's diagnostic figure: velocity and envelope spectra and the time waveform of the worst radial axis"></button>
      <figcaption>The agent's own figure: drawn inside the sandbox by its script with <code>vibkit.plot</code>, pulled out and hash-checked. Click to enlarge.</figcaption></figure>` : "";
  $("outcome").hidden = false;
  $("outcome").innerHTML = tagHtml(jid, report, swing) + (view ? vibrationHtml() : "") +
    `<div class="below">${workOrderHtml(wo)}<div class="aside">${factsHtml(report)}${fig}</div></div>`;
  wireVibration();
  drawVibration();
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
  jobDataset[jid] = info.dataset;
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

const jobDataset = {};
const datasetOf = (jid) => jobDataset[jid];
const viewCache = {};
async function loadView(name) {
  if (!name) return null;
  if (!viewCache[name]) viewCache[name] = fetch(`/api/datasets/${encodeURIComponent(name)}/view`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  return viewCache[name];
}

// ---- vibration: trend, spectrum, envelope, waveform (control plane reference) ----------------------
function vibrationHtml() {
  return `<section class="vib" aria-label="Vibration">
    <div class="vib-head">
      <div><h2 class="sec">Vibration, axis ${esc(view.axis)}</h2>
        <p class="hint">The control plane's own analysis of this measurement with <code>vibkit</code>, the yardstick every claim was checked against. Hover or use the arrow keys to read values.</p></div>
      <div class="segctl" role="group" aria-label="Units">${Object.entries(UNITS).map(([k, u]) => `<button type="button" data-units="${k}" aria-pressed="${k === unitSys}">${u.name}</button>`).join("")}</div>
    </div>
    <div class="panel" id="p-trend"></div>
    <div class="panel" id="p-spec"></div>
    <div class="pair"><div class="panel" id="p-env"></div><div class="panel" id="p-wave"></div></div>
  </section>`;
}

function wireVibration() {
  document.querySelectorAll(".segctl [data-units]").forEach((b) => b.addEventListener("click", () => {
    unitSys = b.dataset.units;
    store.set("arz.units", unitSys);
    document.querySelectorAll(".segctl [data-units]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.units === unitSys)));
    if (lastWO) { $("verdict").innerHTML = verdictHtml(lastWO); if ($("wo-measured")) $("wo-measured").innerHTML = measuredText(lastWO); }
    drawVibration();
  }));
}

function drawVibration() {
  if (!view) return;
  drawTrend(); drawSpectrum(); drawEnvelope(); drawWaveform();
}

const ago = (wk) => (wk === 0 ? "this week" : -wk === 1 ? "1 week ago" : `${-wk} weeks ago`);
const tickFmt = (v) => num(v);
const table = (head, rows) => `<table><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${
  rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;

function trendStory(weeks, key, env, det, pIdx) {
  const now = weeks[weeks.length - 1];
  if (pIdx >= 0) {
    return `The envelope picked up ${key} (${DEFECT_NAME[key]}) ${ago(weeks[pIdx].week)}, and the line has grown to ${env[env.length - 1].toFixed(3)} g. ` +
      `The overall level is still zone ${now.zone} (${fmtV(now.overall_mm_s)}), so a route that only trends overall velocity would not flag it yet. ` +
      "That gap is the P-F interval: time to plan the bearing change before it fails.";
  }
  const out = weeks.findIndex((w) => w.zone !== "A");
  if (out >= 0) {
    let jump = 0, ji = 1;
    for (let i = 1; i < weeks.length; i++) { const d = weeks[i].overall_mm_s - weeks[i - 1].overall_mm_s; if (d > jump) { jump = d; ji = i; } }
    const total = now.overall_mm_s - weeks[0].overall_mm_s;
    if (jump > 0.6 * total) {
      return `The overall level jumped from zone ${weeks[ji - 1].zone} to zone ${weeks[ji].zone} ${ago(weeks[ji].week)} and has kept rising, to ${fmtV(now.overall_mm_s)} (zone ${now.zone}). No bearing defect lines in the envelope.`;
    }
    return `The overall level left zone A ${ago(weeks[out].week)} and has climbed every week since, to ${fmtV(now.overall_mm_s)} (zone ${now.zone}). No bearing defect lines in the envelope.`;
  }
  const vs = weeks.map((w) => w.overall_mm_s);
  return `Steady in zone ${now.zone} for ${weeks.length} weeks (${fmtV(Math.min(...vs))} to ${fmtV(Math.max(...vs))}), and no bearing defect lines in the envelope.`;
}

function drawTrend() {
  const p = $("p-trend"), t = view.trend;
  if (!p) return;
  if (!t) { p.hidden = true; return; }
  const weeks = t.weeks, b = view.boundaries_mm_s, n = weeks.length;
  const last = weeks[n - 1].envelope || {};
  const names = Object.keys(last);
  const key = names.find((k) => last[k].detected) || names.sort((a, c) => (last[c].amp_g || 0) - (last[a].amp_g || 0))[0] || "BPFO";
  const xs = weeks.map((w) => w.week);
  const ov = weeks.map((w) => vel(w.overall_mm_s));
  const env = weeks.map((w) => ((w.envelope || {})[key] || { amp_g: 0 }).amp_g);
  const det = weeks.map((w) => !!((w.envelope || {})[key] || {}).detected);
  const pIdx = det.indexOf(true);
  p.innerHTML = `<div class="panel-head"><h3>Trend, the last ${n} weeks</h3><span class="synthetic" title="${esc(t.note)}">Synthetic history</span></div>
    <p class="story">${esc(trendStory(weeks, key, env, det, pIdx))}</p>
    <p class="chart-title">Overall velocity, ${esc(U().v)}, on the ISO 20816-3 zones</p><div id="ch-tv"></div>
    <p class="chart-title"><i class="key d-${esc(key)}"></i>${pIdx >= 0 ? `Envelope at ${esc(key)} (${esc(DEFECT_NAME[key] || key)}), g. Filled points: detected`
      : `Bearing envelope, the strongest defect line (${esc(key)}), g. Nothing detected in ${n} weeks`}</p><div id="ch-te"></div>
    <details class="tableview"><summary>Show the numbers</summary>${table(["Week", `Overall, ${U().v}`, "Zone", `${key} envelope, g`, "Detected"],
      weeks.map((w, i) => [w.week === 0 ? "this reading" : `${w.week}`, ov[i].toFixed(U().vd), w.zone, env[i].toFixed(4), det[i] ? "yes" : "no"]))}</details>`;
  const xspec = { domain: [-n + 0.6, 0.45], ticks: xs.filter((w) => w % 2 === 0), fmt: (v) => (v === 0 ? "now" : `${v} wk`), label: "weeks" };
  const nearest = (xv) => Math.max(0, Math.min(n - 1, Math.round(xv) + n - 1));
  const ymax = Math.max(vel(b["C/D"]) * 1.3, Math.max(...ov) * 1.15);
  const pv = pIdx >= 0 ? [{ x: xs[pIdx], cls: "pmark", label: "P", strong: true }] : [];
  Charts.chart($("ch-tv"), {
    height: 200, label: "Overall velocity trend", group: "trend", keyStep: 1, x: xspec, margin: { r: 60 },
    y: { domain: [0, ymax], fmt: tickFmt, label: U().v },
    bands: [["A", 0, b["A/B"]], ["B", b["A/B"], b["B/C"]], ["C", b["B/C"], b["C/D"]], ["D", b["C/D"], 1e9]]
      .map(([z, a, c]) => ({ y0: vel(a), y1: Math.min(vel(c), ymax), cls: `z${z}`, label: `zone ${z}` })),
    series: { x: xs, y: ov, points: true, cls: "ink" },
    markers: weeks.map((w, i) => ({ x: w.week, y: ov[i], r: i === n - 1 ? 6 : 4, cls: `z${w.zone}${i === n - 1 ? " now" : ""}`,
      label: i === n - 1 ? "this reading" : null, anchor: "end", dx: -8 })),
    vlines: pv,
    hover: (xv) => { const i = nearest(xv); return { x: xs[i], y: ov[i], value: fmtV(weeks[i].overall_mm_s), lines: [i === n - 1 ? "this reading" : ago(xs[i]), `zone ${weeks[i].zone}`] }; },
  });
  Charts.chart($("ch-te"), {
    height: 160, label: "Envelope trend", group: "trend", keyStep: 1, x: xspec, margin: { r: 60 },
    y: { domain: [0, Math.max(0.02, Math.max(...env) * 1.25)], fmt: tickFmt, label: "g" },
    series: { x: xs, y: env, points: true, cls: `d-${key}` },
    markers: env.map((v, i) => ({ x: xs[i], y: v, r: i === n - 1 ? 5.5 : 4, cls: `d-${key} ${det[i] ? "filled" : "hollow"}` })),
    vlines: pIdx >= 0 ? [{ x: xs[pIdx], cls: "pmark", label: "P, first detected", strong: true }] : [],
    hover: (xv) => {
      const i = nearest(xv), e = (weeks[i].envelope || {})[key] || {};
      return { x: xs[i], y: env[i], value: `${env[i].toFixed(3)} g`, lines: [i === n - 1 ? "this reading" : ago(xs[i]), det[i] ? `${key} detected, ${e.snr_db} dB over the floor` : "below the detection threshold"] };
    },
  });
}

function peaks(xs, ys, x1, count) {
  const top = Math.max(...ys.filter((_, i) => xs[i] <= x1));
  const out = [];
  for (let i = 3; i < xs.length - 3 && xs[i] <= x1; i++) {
    if (ys[i] < 0.04 * top) continue;
    let ok = true;
    for (let k = -3; k <= 3 && ok; k++) if (k && ys[i + k] > ys[i]) ok = false;
    if (ok) out.push(i);
  }
  return out.sort((a, c) => ys[c] - ys[a]).slice(0, count);
}

function drawSpectrum() {
  const p = $("p-spec"), sp = view.spectrum, fr = view.rpm / 60;
  if (!p) return;
  const fmaxHz = specZoom === "10x" ? 10.5 * fr : 1000;
  const fHz = sp.f_hz, xs = fHz.map(fq), ys = sp.v_mm_s.map(vel);
  const x1 = fq(fmaxHz);
  const vis = ys.filter((_, i) => xs[i] <= x1);
  const top = Math.max(...vis);
  const orders = [];
  const dom = view.dominant_order;
  for (let k = 1; k * fr <= fmaxHz; k++) {
    const o = view.orders[`${k}x`];
    const strong = k <= 3 || `${k}x` === dom || k === 6 || (o && vel(o.mm_s) >= 0.08 * top);
    orders.push({ x: fq(k * fr), cls: k <= 3 ? "order" : "order faint", label: strong && (k <= 12 || k % 5 === 0) ? `${k}×` : null });
  }
  const pk = peaks(fHz, sp.v_mm_s, fmaxHz, 8);
  p.innerHTML = `<div class="panel-head"><h3>Velocity spectrum</h3>
      <div class="ctls"><div class="segctl small" role="group" aria-label="Frequency range">
        <button type="button" data-zoom="10x" aria-pressed="${specZoom === "10x"}">To 10×</button>
        <button type="button" data-zoom="full" aria-pressed="${specZoom === "full"}">To ${unitSys === "us" ? "60,000 CPM" : "1,000 Hz"}</button></div>
      <div class="segctl small" role="group" aria-label="Amplitude scale">
        <button type="button" data-log="0" aria-pressed="${!specLog}">Linear</button><button type="button" data-log="1" aria-pressed="${specLog}">Log</button></div></div></div>
    <p class="chart-title">${esc(U().v)} per line, running-speed orders marked (1× = ${esc(fmtF(fr))})</p><div id="ch-spec"></div>
    <details class="tableview"><summary>Largest peaks</summary>${table(["Frequency", "Order", `Amplitude, ${U().v}`],
      pk.map((i) => [fmtF(fHz[i]), `${(fHz[i] / fr).toFixed(2)}×`, ys[i].toFixed(U().vd + 1)]))}</details>`;
  p.querySelectorAll("[data-zoom]").forEach((b) => b.addEventListener("click", () => { specZoom = b.dataset.zoom; drawSpectrum(); }));
  p.querySelectorAll("[data-log]").forEach((b) => b.addEventListener("click", () => { specLog = b.dataset.log === "1"; drawSpectrum(); }));
  Charts.chart($("ch-spec"), {
    height: 260, label: "Velocity spectrum",
    x: { domain: [0, x1], fmt: (v) => (unitSys === "us" ? v.toLocaleString("en-US") : num(v)), label: `frequency, ${U().f}` },
    y: specLog ? { domain: [top / 3000, top * 2], log: true, fmt: (v) => num(v), label: U().v } : { domain: [0, top * 1.2], fmt: tickFmt, label: U().v },
    series: { x: xs, y: ys, area: true, cls: "ink" },
    vlines: orders,
    hover: (xv) => {
      const i = Charts.snap(xs, ys, xv, x1 * 0.006, true);
      return { x: xs[i], y: ys[i], value: `${ys[i].toFixed(U().vd + 1)} ${U().v}`, lines: [fmtF(fHz[i]), `${(fHz[i] / fr).toFixed(2)}× running speed`] };
    },
  });
}

function drawEnvelope() {
  const p = $("p-env"), e = view.envelope;
  if (!p) return;
  const fHz = e.f_hz, xs = fHz.map(fq), ys = e.g;
  const x1 = xs[xs.length - 1];
  const top = Math.max(...ys);
  const defs = Object.entries(view.defects).filter(([k]) => k !== "FTF");
  const lines = [];
  for (const [k, d] of defs) for (let h = 1; h <= 4 && fq(h * d.hz) <= x1; h++) {
    lines.push({ x: fq(h * d.hz), cls: `d-${k} ${d.detected ? "hit" : "miss"}`, label: h === 1 ? `${k}${d.detected ? " ✓" : ""}` : null, strong: d.detected });
  }
  const near = (hz) => { for (const [k, d] of defs) for (let h = 1; h <= 4; h++) if (Math.abs(hz - h * d.hz) <= 0.015 * h * d.hz) return `${k} × ${h} (${DEFECT_NAME[k]})`; return "no defect frequency here"; };
  p.innerHTML = `<div class="panel-head"><h3>Envelope spectrum</h3></div>
    <p class="chart-title">g, ${esc(e.band_hz[0])}–${esc(e.band_hz[1])} Hz band, bearing defect frequencies and their harmonics</p>
    <div class="legend">${defs.map(([k, d]) => `<span><i class="key d-${esc(k)}${d.detected ? "" : " miss"}"></i>${esc(k)} ${esc(DEFECT_NAME[k])}${d.detected ? `, detected (${esc(d.snr_db)} dB)` : ""}</span>`).join("")}</div>
    <div id="ch-env"></div>
    <details class="tableview"><summary>Show the numbers</summary>${table(["Line", "Frequency", "Amplitude, g", "Over the floor", "Detected"],
      defs.map(([k, d]) => [`${k} (${DEFECT_NAME[k]})`, fmtF(d.hz), (d.amp_g ?? 0).toFixed(4), `${d.snr_db} dB`, d.detected ? "yes" : "no"]))}</details>`;
  Charts.chart($("ch-env"), {
    height: 210, label: "Envelope spectrum",
    x: { domain: [0, x1], fmt: (v) => (unitSys === "us" ? v.toLocaleString("en-US") : num(v)), label: `frequency, ${U().f}` },
    y: { domain: [0, Math.max(0.02, top * 1.25)], fmt: tickFmt, label: "g" },
    series: { x: xs, y: ys, area: true, cls: "ink" },
    vlines: lines,
    hover: (xv) => { const i = Charts.snap(xs, ys, xv, x1 * 0.008, true); return { x: xs[i], y: ys[i], value: `${ys[i].toFixed(4)} g`, lines: [fmtF(fHz[i]), near(fHz[i])] }; },
  });
}

function drawWaveform() {
  const p = $("p-wave"), w = view.waveform;
  if (!p) return;
  const ys = w.g, xs = ys.map((_, i) => i * w.dt_ms);
  const lim = Math.max(...ys.map(Math.abs)) * 1.15 || 1;
  const revs = Math.round(xs[xs.length - 1] / w.rev_ms);
  p.innerHTML = `<div class="panel-head"><h3>Time waveform</h3></div>
    <p class="chart-title">Acceleration, g, ${revs} shaft revolutions, a line at each turn (${w.rev_ms.toFixed(1)} ms)</p><div id="ch-wave"></div>`;
  Charts.chart($("ch-wave"), {
    height: 210, label: "Time waveform",
    x: { domain: [0, xs[xs.length - 1]], fmt: num, label: "time, ms" },
    y: { domain: [-lim, lim], fmt: tickFmt, label: "g", ticks: 4 },
    series: { x: xs, y: ys, cls: "ink thin" },
    vlines: Array.from({ length: revs - 1 }, (_, r) => ({ x: (r + 1) * w.rev_ms, cls: "order" })),
    hover: (xv) => { const i = Charts.snap(xs, ys, xv, 0, false); return { x: xs[i], y: ys[i], value: `${ys[i].toFixed(3)} g`, lines: [`${xs[i].toFixed(1)} ms`, `revolution ${Math.floor(xs[i] / w.rev_ms) + 1}`] }; },
  });
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
