#!/usr/bin/env python3
"""Bake the sandbox snapshot on Vultr, then boot-test it.

Subcommands:
  build       builder instance → upload sandbox/ + redteam/ → build.sh → snapshot → delete builder
  boot-test   sandbox from the snapshot on the control plane's VPC (`arz-vpc`); time the boot;
              run the escape probes (Docker and gVisor, plus VM-level probes) through the
              control plane as the jump host; check both firewall layers; tear down

Reads VULTR_API_KEY from the environment and the control plane's IDs, addresses and SSH key
from the secrets directory (vultr-arz-control-plane.env / .key). The sandbox only accepts SSH from
the control plane's VPC address, which build.sh bakes into the image. Reuses spike.py.
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
SECRETS = os.path.expanduser(os.environ.get("ARZ_SECRETS_DIR", "~/.config/lockout"))
CP_KEY = os.path.join(SECRETS, "vultr-arz-control-plane.key")


def control_plane():
    env = {}
    for line in open(os.path.join(SECRETS, "vultr-arz-control-plane.env")):
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k] = v
    return env


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
        skip = ("__pycache__", ".venv", ".pytest_cache")
        for d in ("sandbox", "redteam", "toolkit"):
            tar.add(os.path.join(REPO, d), arcname=f"arz-src/{d}",
                    filter=lambda t: None if any(s in t.name.split("/") for s in skip) else t)
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
        cp_ip = control_plane()["VULTR_VPC_IP"]
        report["control_plane_ip"] = cp_ip
        p = subprocess.run(ssh + ["env", f"CONTROL_PLANE_IP={cp_ip}", "bash", "/root/arz-src/sandbox/image/build.sh"],
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
        cp = control_plane()
        report["control_plane"] = {"public_ip": cp["VULTR_INSTANCE_IP"], "vpc_ip": cp["VULTR_VPC_IP"]}
        created["sshkey"] = must(*call("POST", "/ssh-keys", {"name": TAG, "ssh_key": pub}))["ssh_key"]["id"]
        created["sandbox_firewall"] = must(*call("POST", "/firewalls", {"description": TAG}))["firewall_group"]["id"]
        jump_ssh = ssh_base(CP_KEY) + [f"root@{cp['VULTR_INSTANCE_IP']}"]
        wait_ssh(jump_ssh, "control plane")

        # Time the sandbox on its own, the way a job would see it.
        t0 = time.monotonic()
        status, body = call("POST", "/instances", {
            "region": spike.REGION, "plan": spike.PLAN, "snapshot_id": args.snapshot,
            "label": f"{TAG}-sandbox", "hostname": f"{TAG}-sandbox", "tags": [TAG],
            "enable_ipv6": False, "activation_email": False, "attach_vpc": [cp["VULTR_VPC_ID"]],
            "firewall_group_id": created["sandbox_firewall"], "sshkey_id": [created["sshkey"]]})
        sandbox = must(status, body)["instance"]
        created["instances"].append(sandbox["id"])
        sandbox, _ = wait_active(sandbox["id"])
        report["sandbox_create_to_active_s"] = round(time.monotonic() - t0)
        sb_vpc = must(*call("GET", f"/instances/{sandbox['id']}/vpcs"))["vpcs"][0]["ip_address"]
        report["sandbox_vpc_ip"] = sb_vpc
        proxy = " ".join(ssh_base(CP_KEY) + ["-W", "%h:%p", f"root@{cp['VULTR_INSTANCE_IP']}"])
        sb_ssh = ssh_base(key) + ["-o", f"ProxyCommand={proxy}", f"root@{sb_vpc}"]
        if wait_ssh(sb_ssh, "sandbox via jump", timeout=600) is None:
            raise RuntimeError("sandbox never accepted SSH over the VPC from the control plane")
        report["sandbox_create_to_ssh_s"] = round(time.monotonic() - t0)
        _, out, _ = run_ssh(sb_ssh, r"""
for i in $(seq 1 120); do systemctl is-active -q arz-lockdown && break; sleep 2; done
echo "## units";        systemctl is-active arz-lockdown nftables docker cloud-final
echo "## cloud-init";   cloud-init status --long 2>&1 | head -8
echo "## metadata rule present"; nft list chain inet arz output | grep -c 169.254 || true
echo "## lockdown";     journalctl -b -u arz-lockdown --no-pager | tail -8
echo "## cloud-final";  journalctl -b -u cloud-final --no-pager | tail -15
echo "## slowest units"; systemd-analyze blame 2>/dev/null | head -12
echo "## failed";       systemctl --failed --no-legend
echo "## sshd";         sshd -T 2>/dev/null | grep -E '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin) '
echo "## nft input";    nft list chain inet arz input | grep -E 'dport 22|saddr'
echo "## nft output";   nft list chain inet arz output | grep -vE '^\s*(chain|type|\}|table)'
cat /etc/arz/image.json
""", timeout=420)
        report["sandbox_state"] = out
        log("sandbox state:\n" + out)
        report["sandbox_ready_s"] = round(time.monotonic() - t0)
        log(f"sandbox ready {report['sandbox_ready_s']}s after create")

        for runtime in ("default", "runsc"):
            flag = "" if runtime == "default" else "--runtime runsc"
            _, out, err = run_ssh(sb_ssh, f"/opt/arz/redteam/run.py --runner /opt/arz/runner/arz-run "
                                          f"--timeout 10 {flag} --host-probes --control-plane {cp['VULTR_VPC_IP']}", timeout=600)
            try:
                report[f"redteam_{runtime}"] = json.loads(out)
            except ValueError:
                report[f"redteam_{runtime}"] = {"error": (out + err)[-2000:]}
            r = report[f"redteam_{runtime}"]
            log(f"redteam [{runtime}]: all_contained={r.get('all_contained')} "
                + " ".join(f"{x['probe']}={'ok' if x['contained'] else 'FAIL'}" for x in r.get("results", [])))

        # Second layer on its own: with the sandbox's firewall flushed, the control plane's
        # nftables must still drop every new connection from the VPC. Last step; the sandbox
        # is destroyed right after.
        _, out, _ = run_ssh(sb_ssh, f"""nft flush ruleset && python3 -I - <<'PY'
import json, socket
res = {{}}
for port in (22, 80, 443, 8000):
    try:
        socket.create_connection(('{cp['VULTR_VPC_IP']}', port), timeout=4).close(); res[port] = 'connected'
    except ConnectionRefusedError:
        res[port] = 'refused'
    except OSError as e:
        res[port] = 'no answer'
print(json.dumps(res))
PY""", timeout=120)
        try:
            seen = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            seen = {"error": out[-300:]}
        report["control_plane_layer"] = {"contained": all(v == "no answer" for v in seen.values()), "observed": seen}
        log(f"control plane layer (sandbox firewall flushed): {report['control_plane_layer']}")
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
