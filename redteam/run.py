#!/usr/bin/env python3
"""Run every escape probe through arz-run and report expected vs. observed.

Container probes run as ordinary job steps under the same limits as real jobs.
With --host-probes, egress and metadata are also tried from the sandbox VM itself
(outside the container), which checks the nftables layer on its own.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = {
    # probe: (expected, how containment is judged)
    "net_egress": ("no DNS, no TCP to the internet", "probe"),
    "metadata": ("169.254.169.254 unreachable", "probe"),
    "secrets": ("no credential env vars or readable secret paths", "probe"),
    "fs_write": ("every write outside /work/out and /tmp refused", "probe"),
    "privilege": ("uid 65534, no capabilities, setuid(0) refused", "probe"),
    "fork_bomb": ("stopped by the pids limit", "probe"),
    "mem_bomb": ("OOM-killed at the memory cap", "oom"),
    "timeout": ("killed at the wall-clock limit", "timeout"),
}


def run_probe(name, runner, workroot, engine, runtime, timeout):
    job = os.path.join(workroot, name)
    os.makedirs(os.path.join(job, "input"))
    os.makedirs(os.path.join(job, "code"))
    shutil.copy(os.path.join(HERE, "probes", f"{name}.py"), os.path.join(job, "code", f"{name}.py"))
    os.chmod(job, 0o755)
    cmd = [sys.executable, "-I", runner, "--job", job, "--script", f"{name}.py",
           "--engine", engine, "--timeout", str(timeout)]
    if runtime:
        cmd += ["--runtime", runtime]
    summary = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
    with open(os.path.join(job, summary["stdout"]["path"])) as f:
        lines = [l for l in f.read().splitlines() if l.startswith("{")]
    observed = json.loads(lines[-1]) if lines else None
    with open(os.path.join(job, summary["stderr"]["path"])) as f:
        stderr_tail = f.read()[-300:]

    expected, judge = PROBES[name]
    if judge == "oom":
        contained = summary["oom_killed"] or summary["exit_code"] == 137
    elif judge == "timeout":
        contained = summary["timed_out"]
    else:
        contained = bool(observed and observed["contained"])
    return {"probe": name, "layer": "container", "expected": expected, "contained": contained,
            "observed": observed["detail"] if observed else stderr_tail,
            "exit_code": summary["exit_code"], "oom_killed": summary["oom_killed"],
            "timed_out": summary["timed_out"], "script_sha256": summary["script"]["sha256"],
            "stdout_sha256": summary["stdout"]["sha256"]}


def host_probes():
    results = []
    for name, url, expected in (("host_egress", "https://example.com", "VM cannot reach the internet"),
                                ("host_metadata", "http://169.254.169.254/v1/hostname",
                                 "VM cannot reach metadata after lockdown")):
        p = subprocess.run(["curl", "-sS", "-m", "6", "-o", "/dev/null", "-w", "%{http_code}", url],
                           capture_output=True, text=True)
        contained = p.returncode != 0
        results.append({"probe": name, "layer": "vm", "expected": expected, "contained": contained,
                        "observed": (p.stderr.strip() or f"HTTP {p.stdout}")})
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runner", default=os.path.join(HERE, "..", "sandbox", "runner", "arz-run"))
    p.add_argument("--engine", default="docker", choices=("docker", "podman"))
    p.add_argument("--runtime", default=None)
    p.add_argument("--timeout", type=int, default=15)
    p.add_argument("--host-probes", action="store_true")
    p.add_argument("--only", nargs="*", default=None)
    args = p.parse_args()

    workroot = tempfile.mkdtemp(prefix="arz-redteam-")
    os.chmod(workroot, 0o755)
    results = [run_probe(n, os.path.abspath(args.runner), workroot, args.engine, args.runtime, args.timeout)
               for n in (args.only or PROBES)]
    if args.host_probes:
        results += host_probes()
    shutil.rmtree(workroot, ignore_errors=True)
    report = {"all_contained": all(r["contained"] for r in results), "results": results}
    print(json.dumps(report, indent=2))
    return 0 if report["all_contained"] else 1


if __name__ == "__main__":
    sys.exit(main())
