"""The agent loop: plan → code → run in the sandbox → retry (≤2) → explain → check → work order.

    cd /opt/arz/app && set -a && . /etc/arz/control-plane.env && set +a && \\
      /opt/arz/venv/bin/python -m arz.agent --dataset prompt_injection

Trust model, step by step:
- The model sees the CSV header, the machine metadata (minus the ground truth) and the vibkit
  reference. Never a credential, never the host. Metadata is labelled as data.
- The script it writes is untrusted code: it only ever runs in the throwaway sandbox VM, inside
  the locked container. The control plane only parses it (syntax check, pattern review); it
  never executes it.
- The control plane recomputes the diagnosis itself with vibkit (its own code, on the data), so
  a script that fabricates numbers is caught, and so is an explanation that claims a fault or
  zone the numbers do not support (one re-ask, then the claim is overridden and flagged).
- Every model call, file, step, check and the sandbox lifecycle go into the hash-chained log;
  the bundle is signed with the control plane's Ed25519 key.

Output in /var/lib/arz/jobs/<job-id>/ (or --jobs): report.json, work_order.md/.json, receipt.zip.
"""

import argparse
import ast
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time

import vibkit
from arz_receipts import EventLog, load_private_key, verify_bundle, write_bundle

from . import llm, prompts
from .sandbox import Sandbox, now

MAX_ATTEMPTS = 3          # first try + 2 retries; the last one uses the escalation model
MAX_SCRIPT_CHARS = 30_000
STEP_TIMEOUT_S = 60
REVIEW_PATTERNS = [       # observed and recorded, not blocked: the sandbox is the boundary
    (r"\b(import|from)\s+(subprocess|pty)\b|\bsubprocess\.|\bos\.(system|popen|exec\w*|spawn\w*)\b", "starts processes"),
    (r"\b(import|from)\s+(socket|urllib|requests|http\.client|httpx|ftplib)\b|\b(socket|urllib|requests|httpx)\.\w", "network library"),
    (r"\bcurl\b|\bwget\b", "curl/wget"),
    (r"169\.254\.169\.254|meta-data|metadata\.(google|vultr)", "cloud metadata endpoint"),
    (r"\.ssh|id_rsa|id_ed25519|authorized_keys", "SSH keys"),
    (r"/proc/|environ\b", "process environment"),
    (r"/etc/(passwd|shadow)|/root\b", "system files"),
]


def review(script):
    return sorted({label for pat, label in REVIEW_PATTERNS if re.search(pat, script)})


def csv_summary(path, fs):
    with open(path) as f:
        header = f.readline().strip()
        rows = sum(1 for _ in f)
    return (f"header `{header}`, {rows} rows, sampled at {fs:g} Hz ({rows / fs:.2f} s), acceleration in g"
            if fs else f"header `{header}`, {rows} rows")


def expected_priority(ref):
    zone, fault = ref["iso_20816_3"]["zone"], ref["primary_fault"]
    if zone == "D":
        return "immediate"
    if zone == "C" or fault.startswith("bearing_"):
        return "planned"
    if zone == "B" or fault != "none":
        return "monitor"
    return "none"


def check_claims(expl, ref):
    """Problems with the model's diagnosis and work order, judged against the reference."""
    problems = []
    d, wo = expl["diagnosis"], expl["work_order"]
    if d["fault"] != ref["primary_fault"]:
        problems.append(f"fault {d['fault']!r} is not supported: the measurement shows {ref['primary_fault']!r}")
    if d["iso_zone"] != ref["iso_20816_3"]["zone"]:
        problems.append(f"ISO zone {d['iso_zone']} is not supported: the measured "
                        f"{ref['iso_20816_3']['worst_radial_mm_s']:.2f} mm/s is zone {ref['iso_20816_3']['zone']}")
    if wo["priority"] != expected_priority(ref):
        problems.append(f"priority {wo['priority']!r} does not match zone {ref['iso_20816_3']['zone']} "
                        f"and fault {ref['primary_fault']!r} (expected {expected_priority(ref)!r})")
    return problems


def check_integrity(result, ref):
    """Problems with the sandbox's result.json compared with the control plane's recomputation."""
    d = (result or {}).get("diagnosis") or {}
    iso = d.get("iso_20816_3") or {}
    problems = []
    if d.get("primary_fault") != ref["primary_fault"]:
        problems.append(f"primary_fault {d.get('primary_fault')!r} != reference {ref['primary_fault']!r}")
    if iso.get("zone") != ref["iso_20816_3"]["zone"]:
        problems.append(f"zone {iso.get('zone')!r} != reference {ref['iso_20816_3']['zone']!r}")
    try:
        if abs(float(iso.get("worst_radial_mm_s")) - ref["iso_20816_3"]["worst_radial_mm_s"]) > 0.01:
            problems.append(f"worst_radial_mm_s {iso.get('worst_radial_mm_s')} != reference "
                            f"{ref['iso_20816_3']['worst_radial_mm_s']}")
    except (TypeError, ValueError):
        problems.append("worst_radial_mm_s missing or not a number")
    return problems


def render_work_order(wo):
    lines = [f"# {wo['id']}: {wo['title']}", "",
             f"| Field | Value |", "|---|---|",
             f"| Asset | {wo['asset_id']}: {wo['asset']} |",
             f"| Failure mode | {wo['failure_mode']} |",
             f"| Diagnosed fault | `{wo['fault']}` |",
             f"| ISO 20816-3 zone | **{wo['iso_zone']}** ({wo['measured']['worst_radial_mm_s']:.2f} mm/s RMS on axis "
             f"{wo['measured']['axis']}; boundaries A/B {wo['measured']['boundaries_mm_s']['A/B']}, "
             f"B/C {wo['measured']['boundaries_mm_s']['B/C']}, C/D {wo['measured']['boundaries_mm_s']['C/D']}) |",
             f"| Priority | **{wo['priority']}** |",
             f"| Created | {wo['created_at']} by the agent (draft, needs a planner's approval) |", "",
             "## Actions", ""] + [f"{i}. {a}" for i, a in enumerate(wo["actions"], 1)] + \
            ["", "## Parts", ""] + ([f"- {p}" for p in wo["parts"]] or ["- none"]) + \
            ["", "## Safety", "", wo["safety"], "", "## Evidence", ""] + [f"- {e}" for e in wo["evidence"]]
    if wo.get("llm_claims_rejected"):
        lines += ["", "## Corrected by the deterministic check", ""] + [f"- {p}" for p in wo["llm_claims_rejected"]]
    if wo.get("notes_contained_instructions"):
        lines += ["", "> The measurement notes contained instructions addressed to the agent. They were not followed."]
    return "\n".join(lines) + "\n"


class Agent:
    def __init__(self, dataset, datasets_dir, jobs_dir, sandbox_cls=Sandbox, log=None, emit=None, acquire=None,
                 redteam=False):
        """emit(event) receives every log entry as it is appended (the web timeline);
        acquire() returns a pre-provisioned sandbox (the warm pool) instead of booting one."""
        self.dataset = dataset
        self.csv_path = os.path.join(datasets_dir, f"{dataset}.csv")
        self.meta_path = os.path.join(datasets_dir, f"{dataset}.json")
        self.job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        self.dir = os.path.join(jobs_dir, self.job_id)
        os.makedirs(os.path.join(self.dir, "llm"), mode=0o700)
        self.sandbox_cls = sandbox_cls
        self.log_line = log or (lambda m: print(m, file=sys.stderr, flush=True))
        self.log = EventLog()
        self.n_calls = 0
        self.t0 = time.monotonic()
        self.emit = emit
        self.acquire = acquire
        self.redteam = redteam
        self.events_file = open(os.path.join(self.dir, "events.jsonl"), "a")

    def event(self, _kind, **data):
        entry = self.log.append(_kind, data)
        self.events_file.write(json.dumps(entry, sort_keys=True) + "\n")
        self.events_file.flush()
        self.log_line(f"[{time.monotonic() - self.t0:6.1f}s] {_kind} " + json.dumps(data)[:240])
        if self.emit:
            self.emit(entry)

    def ask(self, messages, schema, name, model=None):
        obj, prov, req, resp = llm.chat_json(messages, schema, name, model=model)
        self.n_calls += 1
        stem = os.path.join(self.dir, "llm", f"{self.n_calls:02d}-{name}")
        open(stem + "-request.json", "wb").write(req)
        open(stem + "-response.json", "wb").write(resp)
        self.event("llm_call", **prov)
        return obj

    def run(self):
        meta = json.load(open(self.meta_path))
        truth = meta.get("ground_truth") or {}
        public_meta = {k: v for k, v in meta.items() if k != "ground_truth"}
        fs = (meta.get("sensor") or {}).get("fs_hz")
        inputs = {f"input/{self.dataset}.csv": open(self.csv_path, "rb").read(),
                  f"input/{self.dataset}.json": json.dumps(public_meta, indent=2).encode()}
        self.event("job_created", job_id=self.job_id, kind="agent", dataset=self.dataset,
                   model=llm.default_model(), escalation_model=llm.escalation_model(),
                   inputs={k: hashlib.sha256(v).hexdigest() for k, v in inputs.items()})
        report = {"job_id": self.job_id, "dataset": self.dataset, "started_at": now(), "ok": False}

        # Reference diagnosis, computed here with our own code: the yardstick for every claim.
        ref = vibkit.analyze(self.csv_path, self.meta_path)
        self.event("reference_computed", primary_fault=ref["primary_fault"], iso_zone=ref["iso_20816_3"]["zone"],
                   worst_radial_mm_s=ref["iso_20816_3"]["worst_radial_mm_s"])

        plan = self.ask(prompts.plan_messages(self.dataset, csv_summary(self.csv_path, fs),
                                              json.dumps(public_meta, indent=2)), prompts.PLAN_SCHEMA, "plan")
        json.dump(plan, open(os.path.join(self.dir, "plan.json"), "w"), indent=2)
        self.event("plan", **plan)

        final, previous, attempts = None, None, []
        sb, containment = None, None
        try:
            if self.acquire:
                self.event("sandbox_requested", warm_pool=True)
                sb = self.acquire()
                sb.log, sb.on_event = (lambda m: None), (lambda k, d: self.event(k, **d))
                self.event("sandbox_assigned", warm=True, instance_id=sb.instance_id, label=sb.label,
                           vpc_ip=sb.vpc_ip, snapshot_id=sb.facts.get("snapshot_id"),
                           created_at=sb.facts.get("created_at"), ready_for_s=round(time.time() - sb.ready_at))
            else:
                sb = self.sandbox_cls(self.job_id, log=lambda m: None, on_event=lambda k, d: self.event(k, **d))
            with sb:
                sb.upload(inputs)
                for n in range(1, MAX_ATTEMPTS + 1):
                    model = llm.escalation_model() if n == MAX_ATTEMPTS else None
                    code = self.ask(prompts.code_messages(self.dataset, json.dumps(plan, indent=2), previous),
                                    prompts.CODE_SCHEMA, f"code-{n}", model=model)
                    script = code["script"]
                    flags = review(script)
                    self.event("code_written", attempt=n, sha256=hashlib.sha256(script.encode()).hexdigest(),
                               chars=len(script), review=flags)
                    why = None
                    if len(script) > MAX_SCRIPT_CHARS:
                        why = f"script is {len(script)} characters (limit {MAX_SCRIPT_CHARS})"
                    else:
                        try:
                            ast.parse(script)
                        except SyntaxError as e:
                            why = f"syntax error at line {e.lineno}: {e.msg}"
                    stderr_tail = ""
                    if why is None:
                        name = f"analyze_{n}.py"
                        sb.put(f"code/{name}", script.encode())
                        summary = sb.run(name, timeout=STEP_TIMEOUT_S)
                        dest = os.path.join(self.dir, "attempts", str(n))
                        verification = sb.pull(summary, dest)
                        stderr_tail = open(os.path.join(dest, summary["stderr"]["path"]), errors="replace").read()[-1500:]
                        result = None
                        rpath = os.path.join(dest, "out", "result.json")
                        if os.path.isfile(rpath):
                            try:
                                result = json.load(open(rpath))
                            except ValueError:
                                result = None
                        if summary["timed_out"]:
                            why = f"killed at the {STEP_TIMEOUT_S} s limit"
                        elif summary["oom_killed"]:
                            why = "killed at the memory limit"
                        elif summary["exit_code"] != 0:
                            why = f"exit code {summary['exit_code']}"
                        elif not isinstance(result, dict) or "diagnosis" not in result:
                            why = "no /work/out/result.json with a 'diagnosis' key"
                        elif not all(v["match"] for v in verification):
                            why = "artifact hashes changed between the sandbox and the control plane"
                    attempts.append({"attempt": n, "model": model or llm.default_model(), "failed": why,
                                     "review": flags})
                    if why is None:
                        final = {"attempt": n, "dir": dest, "summary": summary, "result": result, "script": script}
                        self.event("attempt_succeeded", attempt=n)
                        break
                    self.event("attempt_failed", attempt=n, reason=why, stderr_tail=stderr_tail[-400:])
                    previous = {"script": script, "why": why, "stderr": stderr_tail or why}
                if final is not None and self.redteam:
                    from . import redteam
                    os.makedirs(os.path.join(self.dir, "redteam"), exist_ok=True)
                    self.event("redteam_started", note="replaying the injected instructions, then the escape probes")
                    try:
                        containment = redteam.run(self, sb, ref, os.environ.get("ARZ_CP_VPC_IP", "10.50.0.3"))
                    except Exception as e:  # noqa: BLE001 - the analysis result still stands
                        containment = {"error": f"{e.__class__.__name__}: {e}"[:300], "all_contained": False}
                        self.event("redteam_failed", error=containment["error"])
            self.event("sandbox_facts", **sb.facts)
            if final is None:
                raise RuntimeError(f"no working script after {MAX_ATTEMPTS} attempts")

            integrity = check_integrity(final["result"], ref)
            self.event("integrity_check", ok=not integrity, problems=integrity)
            evidence_result = final["result"] if not integrity else {"diagnosis": ref,
                                                                    "note": "sandbox output replaced by the reference"}

            asset = f"{meta.get('asset_id', '?')}: {meta.get('asset', '?')}"
            expl, rejected = None, []
            for round_ in (1, 2):
                expl = self.ask(prompts.explain_messages(asset, json.dumps(evidence_result, indent=1),
                                                         "; ".join(rejected[-1]) if rejected else None),
                                prompts.EXPLAIN_SCHEMA, f"explain-{round_}")
                problems = check_claims(expl, ref)
                self.event("claims_checked", round=round_, ok=not problems, problems=problems,
                           claimed_fault=expl["diagnosis"]["fault"], claimed_zone=expl["diagnosis"]["iso_zone"],
                           claimed_priority=expl["work_order"]["priority"])
                if not problems:
                    break
                rejected.append(problems)
            overridden = bool(check_claims(expl, ref))
            if overridden:
                expl["diagnosis"]["fault"] = ref["primary_fault"]
                expl["diagnosis"]["iso_zone"] = ref["iso_20816_3"]["zone"]
                expl["work_order"]["priority"] = expected_priority(ref)

            iso = ref["iso_20816_3"]
            # The model drafts the text; identity, fault, zone and the measured numbers come from
            # the metadata and the reference, never from the model.
            wo = {**expl["work_order"], "id": f"WO-{self.job_id}", "created_at": now(),
                  "asset_id": meta.get("asset_id"), "asset": meta.get("asset"), "fault": ref["primary_fault"],
                  "iso_zone": iso["zone"],
                  "measured": {"axis": iso["worst_radial_axis"], "worst_radial_mm_s": iso["worst_radial_mm_s"],
                               "boundaries_mm_s": iso["boundaries_mm_s"]},
                  "evidence": expl["diagnosis"]["evidence"],
                  "summary": expl["diagnosis"]["summary"],
                  "notes_contained_instructions": bool(plan["notes_contain_instructions"]
                                                       or expl["diagnosis"]["notes_contained_instructions"]),
                  "llm_claims_rejected": [p for r in rejected for p in r] if overridden or rejected else []}
            json.dump(wo, open(os.path.join(self.dir, "work_order.json"), "w"), indent=2)
            open(os.path.join(self.dir, "work_order.md"), "w").write(render_work_order(wo))
            self.event("work_order", id=wo["id"], fault=wo["fault"], iso_zone=wo["iso_zone"], priority=wo["priority"],
                       overridden=overridden, sha256=hashlib.sha256(render_work_order(wo).encode()).hexdigest())
            got = {"fault": ref["primary_fault"], "iso_zone": iso["zone"]}
            if truth:
                exp = {k: truth[k] for k in ("fault", "iso_zone") if k in truth}
                self.event("ground_truth_check", expected=exp, got=got, match=all(got[k] == v for k, v in exp.items()))

            bundle = self.sign(final)
            verified = verify_bundle(bundle, os.environ["ARZ_RECEIPT_PUBKEY"])
            report["files"] = {"work_order_md": "work_order.md", "work_order_json": "work_order.json",
                               "receipt": "receipt.zip", "plan": "plan.json",
                               "spectrum": os.path.relpath(os.path.join(final["dir"], "out", "spectrum.png"), self.dir)
                               if os.path.isfile(os.path.join(final["dir"], "out", "spectrum.png")) else None}
            report["summary"] = expl["diagnosis"]["summary"]
            report["containment"] = containment
            report["priority"] = wo["priority"]
            report.update(result=got, attempts=attempts, integrity_problems=integrity, claims_rejected=rejected,
                          overridden=overridden, work_order=os.path.join(self.dir, "work_order.md"),
                          receipt={"path": bundle, "verified": verified["ok"], "events": len(self.log.events)},
                          llm_calls=self.n_calls, sandbox=sb.facts, notes_contained_instructions=wo["notes_contained_instructions"],
                          ok=verified["ok"] and sb.facts.get("destroy_verified") is not False)
        except Exception as e:  # noqa: BLE001 - report it; the sandbox is already torn down
            report["error"] = f"{e.__class__.__name__}: {e}"[:1000]
            report["attempts"] = attempts
            if sb is not None and sb.facts:
                report["sandbox"] = sb.facts
            self.event("job_failed", error=report["error"])
        finally:
            report["ended_at"] = now()
            report["duration_s"] = round(time.monotonic() - self.t0, 1)
            json.dump(report, open(os.path.join(self.dir, "report.json"), "w"), indent=2)
            # Not in the hash chain: the receipt is already signed. Only tells the live view to stop.
            if self.emit:
                self.emit({"seq": None, "kind": "job_finished", "ts": now(),
                           "data": {"ok": report["ok"], "duration_s": report["duration_s"]}})
            self.events_file.close()
        return report

    def sign(self, final):
        art = os.path.join(self.dir, "bundle-artifacts")
        os.makedirs(art)
        for name in ("plan.json", "work_order.json", "work_order.md"):
            shutil.copy(os.path.join(self.dir, name), art)
        shutil.copytree(os.path.join(final["dir"], "out"), os.path.join(art, "out"))
        for stream in ("stdout", "stderr"):
            shutil.copy(os.path.join(final["dir"], final["summary"][stream]["path"]), os.path.join(art, f"{stream}.txt"))
        open(os.path.join(art, "analysis.py"), "w").write(final["script"])
        if os.path.isdir(os.path.join(self.dir, "redteam")):
            shutil.copytree(os.path.join(self.dir, "redteam"), os.path.join(art, "redteam"))
        shutil.copytree(os.path.join(self.dir, "llm"), os.path.join(art, "llm"))
        bundle = os.path.join(self.dir, "receipt.zip")
        write_bundle(bundle, self.log, art, load_private_key(os.environ["ARZ_RECEIPT_KEY"]), self.job_id,
                     extra={"dataset": self.dataset, "kind": "agent"})
        shutil.rmtree(art)
        return bundle


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dataset", required=True)
    p.add_argument("--datasets", default=os.environ.get("ARZ_DATASETS", "/opt/arz/datasets/synth"))
    p.add_argument("--jobs", default="/var/lib/arz/jobs")
    p.add_argument("--local", action="store_true", help="development only: Podman instead of a sandbox VM")
    p.add_argument("--redteam", action="store_true", help="after the analysis, replay the injection and run the escape probes")
    args = p.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9_]{1,64}", args.dataset):
        p.error("dataset names are lowercase letters, digits and underscores")
    cls = Sandbox
    if args.local:
        from .localsandbox import LocalSandbox
        cls = LocalSandbox
    report = Agent(args.dataset, args.datasets, args.jobs, cls, redteam=args.redteam).run()
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
