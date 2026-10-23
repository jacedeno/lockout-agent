#!/usr/bin/env python3
"""Bake the sandbox snapshot on Vultr, then boot-test it.

Subcommands:
  build       builder instance → upload sandbox/ + redteam/ → build.sh → snapshot → delete builder
  boot-test   jump host + sandbox from the snapshot in a VPC; time the boot; run the escape
              probes (Docker and gVisor, plus VM-level probes) through the jump host; tear down

Reads VULTR_API_KEY from the environment. Reuses the helpers in spike.py.
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spike  # noqa: E402
from spike import TAG, call, create_instance, delete_verified, log, must, run_ssh, ssh_base, teardown, wait_active, wait_ssh  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def new_key(workdir):
    work = tempfile.mkdtemp(prefix="arz-bake-", dir=workdir)
    key = os.path.join(work, "id_ed25519")
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", TAG, "-f", key], check=True)
    return key, open(key + ".pub").read().strip()


def ssh_firewall(created, allow):
    fw = must(*call("POST", "/firewalls", {"description": TAG}))["firewall_group"]["id"]
    created["firewall"] = fw
    for ip in allow:
        must(*call("POST", f"/firewalls/{fw}/rules", {"ip_type": "v4", "protocol": "tcp", "port": "22",
                                                      "subnet": ip, "subnet_size": 32, "notes": TAG}))
    return fw


def source_tarball():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for d in ("sandbox", "redteam"):
            tar.add(os.path.join(REPO, d), arcname=f"arz-src/{d}",
                    filter=lambda t: None if "__pycache__" in t.name else t)
    return buf.getvalue()


def cmd_build(args):
    report, created = {}, {"instances": [], "vpc": None, "firewall": None, "sshkey": None}
    key, pub = new_key(args.workdir)
    try:
        created["sshkey"] = must(*call("POST", "/ssh-keys", {"name": TAG, "ssh_key": pub}))["ssh_key"]["id"]
        fw = ssh_firewall(created, args.allow)
        builder = create_instance(f"{TAG}-builder", firewall_group_id=fw, sshkey_id=[created["sshkey"]])
        created["instances"].append(builder["id"])
        builder, _ = wait_active(builder["id"])
        ssh = ssh_base(key) + [f"root@{builder['main_ip']}"]
        if wait_ssh(ssh, "builder") is None:
            raise RuntimeError("builder never accepted SSH")
        run_ssh(ssh, "cloud-init status --wait >/dev/null 2>&1 || true", timeout=600)

        p = subprocess.run(ssh + ["tar", "xzf", "-", "-C", "/root"], input=source_tarball(),
                           capture_output=True, timeout=120)
        if p.returncode:
            raise RuntimeError(f"upload failed: {p.stderr.decode()}")
        log("source uploaded; running build.sh")
        start = time.monotonic()
        p = subprocess.run(ssh + ["bash", "/root/arz-src/sandbox/image/build.sh"],
                           capture_output=True, text=True, timeout=1800)
        report["build_s"] = round(time.monotonic() - start)
        with open(os.path.join(os.path.dirname(key), "build.log"), "w") as f:
            f.write(p.stdout + p.stderr)
        if "BUILD_OK" not in p.stdout:
            raise RuntimeError(f"build.sh failed (rc={p.returncode}):\n{(p.stdout + p.stderr)[-3000:]}")
        report["image"] = json.loads(p.stdout.split("== record the build", 1)[1].split("== scrub", 1)[0])
        log(f"build OK in {report['build_s']}s")

        snap = must(*call("POST", "/snapshots", {"instance_id": builder["id"],
                                                 "description": f"arz-sandbox {time.strftime('%Y-%m-%d %H:%M')}"}))
        snap_id = snap["snapshot"]["id"]
        start = time.monotonic()
        while True:
            s = must(*call("GET", f"/snapshots/{snap_id}"))["snapshot"]
            if s["status"] == "complete":
                break
            if time.monotonic() - start > 3600:
                raise TimeoutError("snapshot not complete after 1 h")
            time.sleep(15)
        report["snapshot"] = {"id": snap_id, "size_gb": round(s["size"] / 1e9, 2),
                              "compressed_gb": round(s.get("compressed_size", 0) / 1e9, 2),
                              "minutes": round((time.monotonic() - start) / 60, 1)}
        log(f"snapshot {snap_id} complete: {report['snapshot']}")
    finally:
        report["teardown"] = teardown(created)
    return report


def cmd_boot_test(args):
    report = {"snapshot": args.snapshot}
    created = {"instances": [], "vpc": None, "firewall": None, "sandbox_firewall": None, "sshkey": None}
    key, pub = new_key(args.workdir)
    try:
        created["sshkey"] = must(*call("POST", "/ssh-keys", {"name": TAG, "ssh_key": pub}))["ssh_key"]["id"]
        fw = ssh_firewall(created, args.allow)
        created["sandbox_firewall"] = must(*call("POST", "/firewalls", {"description": TAG}))["firewall_group"]["id"]
        created["vpc"] = must(*call("POST", "/vpcs", {"region": spike.REGION, "description": TAG,
                                                      "v4_subnet": "10.50.0.0", "v4_subnet_mask": 24}))["vpc"]["id"]
        jump = create_instance(f"{TAG}-jump", firewall_group_id=fw, attach_vpc=[created["vpc"]],
                               sshkey_id=[created["sshkey"]])
        created["instances"].append(jump["id"])
        jump, _ = wait_active(jump["id"])
        jump_ssh = ssh_base(key) + [f"root@{jump['main_ip']}"]
        wait_ssh(jump_ssh, "jump")

        # Time the sandbox on its own, the way a job would see it.
        t0 = time.monotonic()
        status, body = call("POST", "/instances", {
            "region": spike.REGION, "plan": spike.PLAN, "snapshot_id": args.snapshot,
            "label": f"{TAG}-sandbox", "hostname": f"{TAG}-sandbox", "tags": [TAG],
            "enable_ipv6": False, "activation_email": False, "attach_vpc": [created["vpc"]],
            "firewall_group_id": created["sandbox_firewall"], "sshkey_id": [created["sshkey"]]})
        sandbox = must(status, body)["instance"]
        created["instances"].append(sandbox["id"])
        sandbox, _ = wait_active(sandbox["id"])
        report["sandbox_create_to_active_s"] = round(time.monotonic() - t0)
        sb_vpc = must(*call("GET", f"/instances/{sandbox['id']}/vpcs"))["vpcs"][0]["ip_address"]
        proxy = " ".join(ssh_base(key) + ["-W", "%h:%p", f"root@{jump['main_ip']}"])
        sb_ssh = ssh_base(key) + ["-o", f"ProxyCommand={proxy}", f"root@{sb_vpc}"]
        if wait_ssh(sb_ssh, "sandbox via jump", timeout=600) is None:
            raise RuntimeError("sandbox never accepted SSH over the VPC")
        report["sandbox_create_to_ssh_s"] = round(time.monotonic() - t0)
        _, out, _ = run_ssh(sb_ssh, "systemctl is-active arz-lockdown nftables docker; "
                                    "until systemctl is-active -q arz-lockdown; do sleep 1; done; "
                                    "nft list chain inet arz output | grep -c 169.254 || true; cat /etc/arz/image.json")
        report["sandbox_state"] = out
        report["sandbox_ready_s"] = round(time.monotonic() - t0)
        log(f"sandbox ready {report['sandbox_ready_s']}s after create")

        for runtime in ("default", "runsc"):
            flag = "" if runtime == "default" else "--runtime runsc"
            _, out, err = run_ssh(sb_ssh, f"/opt/arz/redteam/run.py --runner /opt/arz/runner/arz-run "
                                          f"--timeout 10 {flag} --host-probes", timeout=600)
            try:
                report[f"redteam_{runtime}"] = json.loads(out)
            except ValueError:
                report[f"redteam_{runtime}"] = {"error": (out + err)[-2000:]}
            r = report[f"redteam_{runtime}"]
            log(f"redteam [{runtime}]: all_contained={r.get('all_contained')} "
                + " ".join(f"{x['probe']}={'ok' if x['contained'] else 'FAIL'}" for x in r.get("results", [])))
    finally:
        report["teardown"] = teardown(created)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    t = sub.add_parser("boot-test")
    t.add_argument("--snapshot", required=True)
    for s in (b, t):
        s.add_argument("--allow", nargs="+", required=True, help="IPv4 addresses allowed to SSH in")
        s.add_argument("--workdir", default=None, help="where the throwaway SSH key and logs go")
    args = p.parse_args()
    report = {"build": cmd_build, "boot-test": cmd_boot_test}[args.cmd](args)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    sys.exit(main())
