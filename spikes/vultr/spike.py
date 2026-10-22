#!/usr/bin/env python3
"""Pre-event Vultr API spikes (stdlib only).

Subcommands:
  lifecycle   create the smallest instance, wait for it, delete it, verify 404
  vpc-only    jump host (public + VPC) and sandbox (VPC only, no public IP);
              probe cloud-init, SSH over the VPC, egress and metadata from the sandbox
  cleanup     delete every resource carrying the spike tag/label

Reads VULTR_API_KEY from the environment. Everything created is tagged or labelled
"arz-spike" and torn down in a finally block.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

API = os.environ.get("VULTR_API_URL", "https://api.vultr.com/v2")
TAG = "arz-spike"
REGION, PLAN, OS_ID = "dfw", "vc2-1c-1gb", 2284  # Dallas, $5/mo, Ubuntu 24.04
T0 = time.monotonic()


def log(msg):
    print(f"[{time.monotonic() - T0:7.1f}s] {msg}", flush=True)


def call(method, path, body=None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['VULTR_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"error": raw.decode(errors="replace")}


def must(status, body, ok=(200, 201, 202, 204)):
    if status not in ok:
        raise RuntimeError(f"API {status}: {body}")
    return body


def create_instance(label, **extra):
    body = {"region": REGION, "plan": PLAN, "os_id": OS_ID, "label": label,
            "hostname": label, "tags": [TAG], "enable_ipv6": False,
            "activation_email": False, **extra}
    inst = must(*call("POST", "/instances", body))["instance"]
    log(f"created {label} id={inst['id']}")
    return inst


def wait_active(iid, timeout=600):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        inst = must(*call("GET", f"/instances/{iid}"))["instance"]
        if inst["status"] == "active" and inst["power_status"] == "running":
            log(f"{inst['label']} active after {time.monotonic() - start:.0f}s "
                f"(server_status={inst['server_status']}, main_ip={inst['main_ip']}, v6={inst['v6_main_ip']})")
            return inst, time.monotonic() - start
        time.sleep(5)
    raise TimeoutError(f"instance {iid} not active after {timeout}s")


def delete_verified(iid, timeout=300):
    start = time.monotonic()
    status, body = call("DELETE", f"/instances/{iid}")
    if status not in (204, 404):
        raise RuntimeError(f"delete {iid}: {status} {body}")
    while time.monotonic() - start < timeout:
        status, _ = call("GET", f"/instances/{iid}")
        if status == 404:
            log(f"instance {iid} gone (404) after {time.monotonic() - start:.0f}s")
            return time.monotonic() - start
        time.sleep(3)
    raise TimeoutError(f"instance {iid} still present after {timeout}s")


def cmd_lifecycle(_args):
    report = {}
    inst = create_instance(f"{TAG}-lifecycle")
    try:
        _, report["create_to_active_s"] = wait_active(inst["id"])
    finally:
        report["delete_to_404_s"] = delete_verified(inst["id"])
    return report


def ssh_base(key):
    return ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=10"]


def run_ssh(argv, script, timeout=60):
    p = subprocess.run(argv + ["bash", "-s"], input=script, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def wait_ssh(argv, label, timeout=420):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            rc, _, _ = run_ssh(argv, "true", timeout=30)
        except subprocess.TimeoutExpired:
            rc = 255
        if rc == 0:
            log(f"ssh to {label} OK after {time.monotonic() - start:.0f}s")
            return time.monotonic() - start
        time.sleep(10)
    log(f"ssh to {label} never came up in {timeout}s")
    return None


SANDBOX_PROBES = r"""
echo "## cloud-init";   cloud-init status --long 2>&1 | head -5
echo "## marker";       cat /root/cloud-init-marker 2>&1
echo "## addresses";    ip -4 -brief addr
echo "## routes";       ip route
echo "## dns";          getent hosts example.com && echo RESOLVED || echo NO_DNS
echo "## egress https"; curl -sS -m 8 -o /dev/null -w '%{http_code}\n' https://example.com 2>&1 || echo EGRESS_BLOCKED
echo "## egress ip";    curl -sS -m 8 -o /dev/null -w '%{http_code}\n' http://1.1.1.1 2>&1 || echo EGRESS_IP_BLOCKED
echo "## metadata";     curl -sS -m 5 http://169.254.169.254/v1/hostname 2>&1 || echo METADATA_UNREACHABLE
"""


LOCKED_USER_DATA = """#cloud-config
packages: [nftables]
write_files:
  - path: /etc/arz-egress.nft
    content: |
      table inet arz {
        chain input {
          type filter hook input priority 0; policy drop;
          iif "lo" accept
          ct state established,related accept
          ip saddr 10.50.0.0/24 tcp dport 22 accept
        }
        chain output {
          type filter hook output priority 0; policy drop;
          oif "lo" accept
          ct state established,related accept
          ip daddr 10.50.0.0/24 accept
        }
      }
runcmd:
  - echo cloud-init-ran > /root/cloud-init-marker
  - nft -f /etc/arz-egress.nft && echo nft-applied >> /root/cloud-init-marker
"""


def cmd_vpc_only(args):
    report = {"mode": args.mode}
    created = {"instances": [], "vpc": None, "firewall": None, "sandbox_firewall": None, "sshkey": None}
    work = tempfile.mkdtemp(prefix="arz-spike-", dir=args.workdir)
    key = os.path.join(work, "id_ed25519")
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", TAG, "-f", key], check=True)
    pub = open(key + ".pub").read().strip()
    try:
        created["sshkey"] = must(*call("POST", "/ssh-keys", {"name": TAG, "ssh_key": pub}))["ssh_key"]["id"]

        fw = must(*call("POST", "/firewalls", {"description": TAG}))["firewall_group"]["id"]
        created["firewall"] = fw
        for ip in args.allow:
            must(*call("POST", f"/firewalls/{fw}/rules", {"ip_type": "v4", "protocol": "tcp", "port": "22",
                                                          "subnet": ip, "subnet_size": 32, "notes": TAG}))
        log(f"firewall group {fw}: tcp/22 from {args.allow}")

        vpc = must(*call("POST", "/vpcs", {"region": REGION, "description": TAG,
                                          "v4_subnet": "10.50.0.0", "v4_subnet_mask": 24}))["vpc"]["id"]
        created["vpc"] = vpc
        log(f"vpc {vpc} 10.50.0.0/24")

        user_data = base64.b64encode(
            "#cloud-config\nruncmd:\n  - echo cloud-init-ran > /root/cloud-init-marker\n".encode()).decode()
        common = {"attach_vpc": [vpc], "sshkey_id": [created["sshkey"]], "user_data": user_data}

        jump = create_instance(f"{TAG}-jump", firewall_group_id=fw, **common)
        created["instances"].append(jump["id"])
        if args.mode == "no-public-ip":
            # Refused by Vultr on 2026-10-22: a VPC-only instance needs a NAT gateway on the VPC.
            sandbox_req = {"disable_public_ipv4": True, **common}
        else:
            # Public IP, but a firewall group with no rules (drops all inbound) and an
            # nftables default-drop policy applied by cloud-init as its last step.
            sb_fw = must(*call("POST", "/firewalls", {"description": TAG}))["firewall_group"]["id"]
            created["sandbox_firewall"] = sb_fw
            sandbox_req = {**common, "firewall_group_id": sb_fw,
                           "user_data": base64.b64encode(LOCKED_USER_DATA.encode()).decode()}
        sb_status, sb_body = call("POST", "/instances", {
            "region": REGION, "plan": PLAN, "os_id": OS_ID, "label": f"{TAG}-sandbox",
            "hostname": f"{TAG}-sandbox", "tags": [TAG], "enable_ipv6": False,
            "activation_email": False, **sandbox_req})
        report["sandbox_create"] = {"status": sb_status, "request_flags": sorted(sandbox_req)}
        sandbox = must(sb_status, sb_body)["instance"]
        created["instances"].append(sandbox["id"])
        log(f"created sandbox id={sandbox['id']}")

        jump, report["jump_create_to_active_s"] = wait_active(jump["id"])
        sandbox, report["sandbox_create_to_active_s"] = wait_active(sandbox["id"])
        report["sandbox_api_view"] = {k: sandbox.get(k) for k in
                                      ("main_ip", "v6_main_ip", "internal_ip", "netmask_v4", "features")}

        def vpc_ip(iid):
            vpcs = must(*call("GET", f"/instances/{iid}/vpcs"))["vpcs"]
            return vpcs[0]["ip_address"] if vpcs else None

        jump_vpc, sb_vpc = vpc_ip(jump["id"]), vpc_ip(sandbox["id"])
        report["vpc_ips"] = {"jump": jump_vpc, "sandbox": sb_vpc}
        log(f"vpc ips: jump={jump_vpc} sandbox={sb_vpc}")

        jump_ssh = ssh_base(key) + [f"root@{jump['main_ip']}"]
        report["jump_ssh_ready_s"] = wait_ssh(jump_ssh, "jump")
        if report["jump_ssh_ready_s"] is not None:
            _, out, _ = run_ssh(jump_ssh, "cloud-init status; cat /root/cloud-init-marker; ip -4 -brief addr")
            report["jump_probe"] = out
            proxy = " ".join(ssh_base(key) + ["-W", "%h:%p", f"root@{jump['main_ip']}"])
            sb_ssh = ssh_base(key) + ["-o", f"ProxyCommand={proxy}", f"root@{sb_vpc}"]
            report["sandbox_ssh_ready_s"] = wait_ssh(sb_ssh, "sandbox via jump")
            if report["sandbox_ssh_ready_s"] is not None:
                _, out, err = run_ssh(sb_ssh, SANDBOX_PROBES, timeout=120)
                report["sandbox_probe"] = out + err
                log("sandbox probes:\n" + out + err)
            if sandbox["main_ip"] not in ("", "0.0.0.0"):
                try:
                    rc, _, _ = run_ssh(ssh_base(key) + [f"root@{sandbox['main_ip']}"], "true", timeout=30)
                except subprocess.TimeoutExpired:
                    rc = "timeout"
                report["sandbox_public_ssh_from_internet"] = "REACHABLE" if rc == 0 else f"blocked (rc={rc})"
                log(f"public SSH to sandbox: {report['sandbox_public_ssh_from_internet']}")
    finally:
        report["teardown"] = teardown(created)
    return report


def teardown(created):
    result = {}
    for iid in created["instances"]:
        try:
            result[f"instance {iid}"] = f"404 after {delete_verified(iid):.0f}s"
        except Exception as e:  # keep tearing down the rest
            result[f"instance {iid}"] = f"FAILED: {e}"
    for kind, path in (("vpc", "/vpcs/"), ("firewall", "/firewalls/"),
                       ("sandbox_firewall", "/firewalls/"), ("sshkey", "/ssh-keys/")):
        rid = created.get(kind)
        if not rid:
            continue
        for _ in range(20):  # a VPC refuses deletion until its instances are fully gone
            status, body = call("DELETE", path + rid)
            if status in (204, 404):
                result[f"{kind} {rid}"] = "deleted"
                break
            time.sleep(6)
        else:
            result[f"{kind} {rid}"] = f"FAILED: {status} {body}"
        log(f"{kind} {rid}: {result[f'{kind} {rid}']}")
    return result


def cmd_cleanup(_args):
    created = {"instances": [], "vpc": None, "firewall": None, "sshkey": None}
    found = {"instances": [], "vpcs": [], "firewalls": [], "sshkeys": []}
    for i in must(*call("GET", f"/instances?tag={TAG}&per_page=500"))["instances"]:
        created["instances"].append(i["id"])
        found["instances"].append(i["id"])
    result = teardown(created)
    for kind, path, key, field in (("vpcs", "/vpcs", "vpcs", "description"),
                                   ("firewalls", "/firewalls", "firewall_groups", "description"),
                                   ("sshkeys", "/ssh-keys", "ssh_keys", "name")):
        for r in must(*call("GET", f"{path}?per_page=500"))[key]:
            if r.get(field) == TAG:
                found[kind].append(r["id"])
                status, _ = call("DELETE", f"{path}/{r['id']}")
                result[f"{kind} {r['id']}"] = status
    return {"found": found, "result": result}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lifecycle")
    v = sub.add_parser("vpc-only")
    v.add_argument("--allow", nargs="+", required=True, help="IPv4 addresses allowed to SSH to the jump host")
    v.add_argument("--workdir", default=None, help="where the throwaway SSH key is written")
    v.add_argument("--mode", choices=("locked-public", "no-public-ip"), default="locked-public",
                   help="sandbox network mode (no-public-ip needs a NAT gateway on the VPC)")
    sub.add_parser("cleanup")
    args = p.parse_args()
    report = {"lifecycle": cmd_lifecycle, "vpc-only": cmd_vpc_only, "cleanup": cmd_cleanup}[args.cmd](args)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    sys.exit(main())
