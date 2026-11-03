#!/usr/bin/env python3
"""Deploy the control plane VM (idempotent: re-run `up` to re-provision).

    python3 -I control-plane/deploy/deploy.py up --allow 203.0.113.10/32 203.0.113.11/32 [--snapshot ID]
    python3 -I control-plane/deploy/deploy.py status
    python3 -I control-plane/deploy/deploy.py destroy [--vpc]

What it touches:
- Vultr: instance `arz-control-plane` (vc2-1c-2gb, Ubuntu 24.04, dfw) attached to VPC `arz-vpc`
  (10.50.0.0/24, shared with the sandboxes), firewall group `arz-control-plane` (80/443 from
  anywhere, 22 from --allow) and SSH key `arz-control-plane`; plus the empty firewall group
  `arz-sandbox` every sandbox gets (no rules: no public inbound at all).
- Cloudflare: a DNS-only A record for HOST, so Caddy serves its own Let's Encrypt certificate.
- the secrets directory: vultr-arz-control-plane.env (resource IDs, IPs) and vultr-arz-control-plane.key.
- The VM: provision.sh (swap, SSH keys-only, nftables, Caddy, sandbox reaper, the `arz` package
  in /opt/arz/app) and /etc/arz/control-plane.env (root-only: the Vultr API key plus the
  sandbox settings: snapshot, VPC, firewall group, region, plan).

Credentials come from the secrets directory: vultr.env and cloudflare.env.
`destroy` keeps the VPC unless --vpc is given (sandboxes attach to it).
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.expanduser(os.environ.get("ARZ_SECRETS_DIR", "~/.config/lockout"))
ENV_FILE = os.path.join(SECRETS, "vultr-arz-control-plane.env")
KEY_FILE = os.path.join(SECRETS, "vultr-arz-control-plane.key")
RECEIPT_KEY = os.path.join(SECRETS, "arz-receipt-signing.pem")
RECEIPT_PUB = os.path.join(SECRETS, "arz-receipt-signing.pub")
ACCESS_CODE = os.path.join(SECRETS, "arz-access-code.env")
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
LABEL = "arz-control-plane"
VPC_LABEL = "arz-vpc"
SANDBOX_FW_LABEL = "arz-sandbox"
SANDBOX_PLAN = "vc2-1c-1gb"
VPC_SUBNET, VPC_MASK = "10.50.0.0", 24
REGION = "dfw"
PLAN = "vc2-1c-2gb"
UBUNTU_2404 = 2284
ZONE = "geekendzone.net"
HOST = "agentrush.geekendzone.net"
HEADER = ("# Agent Rush control plane on Vultr (vultr-agent-rush/control-plane/deploy/deploy.py).\n"
          "# Resource IDs and addresses; the SSH key is vultr-arz-control-plane.key.\n")
T0 = time.monotonic()


def log(msg):
    print(f"[{time.monotonic() - T0:7.1f}s] {msg}", file=sys.stderr, flush=True)


def read_env(path):
    out = {}
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
    return out


def write_env(path, values):
    old = os.umask(0o077)
    try:
        with open(path, "w") as f:
            f.write(HEADER + "".join(f"{k}={v}\n" for k, v in values.items()))
    finally:
        os.umask(old)


VULTR = read_env(os.path.join(SECRETS, "vultr.env"))
API = VULTR.get("VULTR_API_URL", "https://api.vultr.com/v2").rstrip("/")


def vultr(method, path, body=None, ok=(200, 201, 202, 204)):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {VULTR['VULTR_API_KEY']}",
                                          "Content-Type": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                status, data = r.status, json.loads(raw) if raw else {}
                break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                time.sleep(2 * (attempt + 1))
                continue
            raw = e.read()
            status, data = e.code, {"error": raw.decode(errors="replace")[:300]}
            break
    if status not in ok:
        raise RuntimeError(f"{method} {path}: HTTP {status} {data}")
    return status, data


def cloudflare(method, path, body=None):
    token = read_env(os.path.join(SECRETS, "cloudflare.env"))["CF_API_TOKEN"]
    req = urllib.request.Request(f"https://api.cloudflare.com/client/v4{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if not data.get("success"):
        raise RuntimeError(f"Cloudflare {method} {path}: {data.get('errors')}")
    return data["result"]


def zone_id():
    return cloudflare("GET", f"/zones?name={ZONE}")[0]["id"]


def upsert_dns(ip):
    zone = zone_id()
    found = cloudflare("GET", f"/zones/{zone}/dns_records?type=A&name={HOST}")
    record = {"type": "A", "name": HOST, "content": ip, "ttl": 300, "proxied": False,
              "comment": "Agent Rush control plane (Vultr), DNS-only for Caddy TLS"}
    if found:
        return cloudflare("PUT", f"/zones/{zone}/dns_records/{found[0]['id']}", record)["id"]
    return cloudflare("POST", f"/zones/{zone}/dns_records", record)["id"]


def ssh_argv(ip):
    return ["ssh", "-i", KEY_FILE, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10", f"root@{ip}"]


def ssh(ip, cmd, input=None, timeout=900):
    return subprocess.run(ssh_argv(ip) + [cmd], input=input, capture_output=True, timeout=timeout)


def wait_ssh(ip, timeout=420):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if ssh(ip, "true", timeout=30).returncode == 0:
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(5)
    raise RuntimeError(f"SSH to {ip} never came up")


def find(path, key, field, value):
    rows = vultr("GET", f"{path}?per_page=500")[1][key]
    return next((r for r in rows if r.get(field) == value), None)


def ensure_ssh_key():
    if not os.path.exists(KEY_FILE):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", LABEL, "-f", KEY_FILE], check=True)
    found = find("/ssh-keys", "ssh_keys", "name", LABEL)
    if found:
        return found["id"]
    pub = open(KEY_FILE + ".pub").read().strip()
    return vultr("POST", "/ssh-keys", {"name": LABEL, "ssh_key": pub})[1]["ssh_key"]["id"]


def ensure_firewall(allow):
    found = find("/firewalls", "firewall_groups", "description", LABEL)
    if found:
        return found["id"]
    fw = vultr("POST", "/firewalls", {"description": LABEL})[1]["firewall_group"]["id"]
    for port, cidr in [(80, "0.0.0.0/0"), (443, "0.0.0.0/0")] + [(22, c) for c in allow]:
        subnet, size = cidr.split("/")
        vultr("POST", f"/firewalls/{fw}/rules", {"ip_type": "v4", "protocol": "tcp", "port": str(port),
                                                 "subnet": subnet, "subnet_size": int(size), "notes": LABEL})
    log(f"firewall group {fw}: 80/443 open, 22 from {allow}")
    return fw


def ensure_sandbox_firewall():
    found = find("/firewalls", "firewall_groups", "description", SANDBOX_FW_LABEL)
    if found:
        return found["id"]
    fw = vultr("POST", "/firewalls", {"description": SANDBOX_FW_LABEL})[1]["firewall_group"]["id"]
    log(f"sandbox firewall group {fw}: no rules (drops all public inbound)")
    return fw


def ensure_vpc():
    found = find("/vpcs", "vpcs", "description", VPC_LABEL)
    if found:
        return found["id"]
    vpc = vultr("POST", "/vpcs", {"region": REGION, "description": VPC_LABEL, "v4_subnet": VPC_SUBNET,
                                  "v4_subnet_mask": VPC_MASK})[1]["vpc"]["id"]
    log(f"vpc {vpc} {VPC_SUBNET}/{VPC_MASK}")
    return vpc


def wait_active(iid, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        inst = vultr("GET", f"/instances/{iid}")[1]["instance"]
        if inst["status"] == "active" and inst["power_status"] == "running" and inst["main_ip"] != "0.0.0.0":
            return inst
        time.sleep(5)
    raise RuntimeError(f"instance {iid} not active after {timeout}s")


def bundle():
    """Render the templates and return provision files as a tar archive (bytes)."""
    subs = {"@HOST@": HOST, "@VPC_CIDR@": f"{VPC_SUBNET}/{VPC_MASK}",
            "@RECEIPT_PUBKEY@": open(RECEIPT_PUB).read().strip()}
    skip = ("__pycache__", ".venv", ".pytest_cache", "tests", "uv.lock")
    trees = [(os.path.join(HERE, "..", "arz"), "app/arz"),
             (os.path.join(REPO, "receipts", "pyproject.toml"), "pkg/receipts/pyproject.toml"),
             (os.path.join(REPO, "receipts", "src"), "pkg/receipts/src"),
             (os.path.join(REPO, "toolkit", "pyproject.toml"), "pkg/vibkit/pyproject.toml"),
             (os.path.join(REPO, "toolkit", "src"), "pkg/vibkit/src"),
             (os.path.join(REPO, "data", "synth", "generate.py"), "synth/generate.py"),
             (os.path.join(REPO, "data", "synth", "SHA256SUMS.expected"), "synth/SHA256SUMS.expected")]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for src, arc in trees:
            tar.add(src, arcname=arc, filter=lambda t: None if any(s in t.name.split("/") for s in skip) else t)
        for name in sorted(os.listdir(os.path.join(HERE, "files"))):
            path = os.path.join(HERE, "files", name)
            if not os.path.isfile(path) or name.endswith(".pyc"):
                continue
            data = open(path).read()
            for k, v in subs.items():
                data = data.replace(k, v)
            info = tarfile.TarInfo(name[:-3] if name.endswith(".in") else name)
            raw = data.encode()
            info.size = len(raw)
            info.mode = 0o755 if name.endswith((".sh", ".py")) else 0o644
            tar.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


def check_https(timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"https://{HOST}/healthz", timeout=10) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - DNS or ACME still settling
            pass
        time.sleep(10)
    return False


def cmd_up(args):
    env = read_env(ENV_FILE)
    snapshot = args.snapshot or env.get("ARZ_SNAPSHOT_ID")
    if not snapshot:
        raise SystemExit("--snapshot is required the first time (the sandbox snapshot ID)")
    vultr("GET", f"/snapshots/{snapshot}")  # fail early on a wrong or deleted snapshot
    key_id = ensure_ssh_key()
    fw = ensure_firewall(args.allow)
    sandbox_fw = ensure_sandbox_firewall()
    vpc = ensure_vpc()
    inst = find("/instances", "instances", "label", LABEL)
    if inst is None:
        inst = vultr("POST", "/instances", {
            "region": REGION, "plan": PLAN, "os_id": UBUNTU_2404, "label": LABEL, "hostname": LABEL,
            "tags": [LABEL], "enable_ipv6": False, "backups": "disabled", "activation_email": False,
            "sshkey_id": [key_id], "firewall_group_id": fw, "attach_vpc": [vpc]})[1]["instance"]
        log(f"created instance {inst['id']} ({PLAN}, {REGION})")
    env.update({"HOST": HOST, "VULTR_INSTANCE_ID": inst["id"], "VULTR_FIREWALL_ID": fw,
                "VULTR_SSHKEY_ID": key_id, "VULTR_VPC_ID": vpc, "ARZ_SANDBOX_FIREWALL_ID": sandbox_fw,
                "ARZ_SNAPSHOT_ID": snapshot})
    write_env(ENV_FILE, env)

    inst = wait_active(inst["id"])
    ip = inst["main_ip"]
    vpcs = vultr("GET", f"/instances/{inst['id']}/vpcs")[1]["vpcs"]
    env.update({"VULTR_INSTANCE_IP": ip, "VULTR_VPC_IP": vpcs[0]["ip_address"] if vpcs else "",
                "CLOUDFLARE_RECORD_ID": upsert_dns(ip)})
    write_env(ENV_FILE, env)
    log(f"{HOST} -> {ip} (vpc {env['VULTR_VPC_IP']})")

    wait_ssh(ip)
    p = ssh(ip, "rm -rf /root/arz-deploy && mkdir -p /root/arz-deploy && tar -xf - -C /root/arz-deploy",
            input=bundle(), timeout=120)
    if p.returncode:
        raise RuntimeError(f"upload failed: {p.stderr.decode()[-500:]}")
    cp_env = "".join(f"{k}={v}\n" for k, v in {
        "VULTR_API_KEY": VULTR["VULTR_API_KEY"], "VULTR_API_URL": API,
        "ARZ_REGION": REGION, "ARZ_SANDBOX_PLAN": SANDBOX_PLAN, "ARZ_SNAPSHOT_ID": snapshot,
        "ARZ_VPC_ID": vpc, "ARZ_SANDBOX_FIREWALL_ID": sandbox_fw,
        "ARZ_RECEIPT_KEY": "/etc/arz/receipt-signing.pem",
        "ARZ_RECEIPT_PUBKEY": open(RECEIPT_PUB).read().strip(),
        "ARZ_DATASETS": "/opt/arz/datasets/synth",
        "VULTR_INFERENCE_API_KEY": VULTR["VULTR_INFERENCE_API_KEY"],
        "VULTR_INFERENCE_URL": VULTR.get("VULTR_INFERENCE_URL", "https://api.vultrinference.com/v1"),
        "ARZ_MODEL": "qwen3.8-flash-next", "ARZ_MODEL_ESCALATION": "glm-5.3",
        "ARZ_ACCESS_CODE": read_env(ACCESS_CODE)["ARZ_ACCESS_CODE"], "ARZ_JOBS": "/var/lib/arz/jobs"}.items()).encode()
    p = ssh(ip, "install -d -m 0700 /etc/arz && umask 077 && cat > /etc/arz/control-plane.env", input=cp_env, timeout=60)
    if p.returncode:
        raise RuntimeError(f"env upload failed: {p.stderr.decode()[-500:]}")
    p = ssh(ip, "umask 077 && cat > /etc/arz/receipt-signing.pem", input=open(RECEIPT_KEY, "rb").read(), timeout=60)
    if p.returncode:
        raise RuntimeError(f"signing key upload failed: {p.stderr.decode()[-500:]}")
    log("provisioning (first run installs packages)")
    p = ssh(ip, "bash /root/arz-deploy/provision.sh 2>&1 | tail -30", timeout=1200)
    print(p.stdout.decode()[-3000:])
    if p.returncode or b"provision: ok" not in p.stdout:
        raise RuntimeError("provision failed")
    if check_https():
        log(f"https://{HOST}/healthz is live")
        with urllib.request.urlopen(f"https://{HOST}/api/status", timeout=15) as r:
            log(f"app: {r.read().decode()[:200]}")
    else:
        raise RuntimeError(f"https://{HOST}/healthz did not come up")


def cmd_status(_args):
    env = read_env(ENV_FILE)
    inst = find("/instances", "instances", "label", LABEL)
    if not inst:
        print("no instance")
        return
    print(json.dumps({k: inst[k] for k in ("id", "status", "power_status", "main_ip", "plan", "region",
                                           "date_created", "tags")}, indent=2))
    print("vpc ip:", env.get("VULTR_VPC_IP"))
    print("https:", "up" if check_https(timeout=1) else "down")
    p = ssh(inst["main_ip"], "systemctl list-timers arz-reaper.timer --no-pager | head -3; "
                             "journalctl -u arz-reaper.service -n 3 --no-pager -o cat", timeout=60)
    print(p.stdout.decode())


def cmd_destroy(args):
    env = read_env(ENV_FILE)
    inst = find("/instances", "instances", "label", LABEL)
    if inst:
        vultr("DELETE", f"/instances/{inst['id']}")
        for _ in range(60):
            try:
                vultr("GET", f"/instances/{inst['id']}")
            except RuntimeError as e:
                if "HTTP 404" in str(e):
                    log(f"instance {inst['id']} deleted, 404 verified")
                    break
                raise
            time.sleep(2)
        else:
            raise RuntimeError("instance delete not verified")
    if env.get("CLOUDFLARE_RECORD_ID"):
        cloudflare("DELETE", f"/zones/{zone_id()}/dns_records/{env['CLOUDFLARE_RECORD_ID']}")
        log(f"DNS record for {HOST} deleted")
    for path, key, field, value in (("/firewalls", "firewall_groups", "description", LABEL),
                                    ("/firewalls", "firewall_groups", "description", SANDBOX_FW_LABEL),
                                    ("/ssh-keys", "ssh_keys", "name", LABEL)):
        found = find(path, key, field, value)
        if found:
            for _ in range(30):  # a firewall group stays attached for a few seconds after delete
                try:
                    vultr("DELETE", f"{path}/{found['id']}")
                    break
                except RuntimeError:
                    time.sleep(5)
            log(f"{path} {found['id']} deleted")
    if args.vpc:
        found = find("/vpcs", "vpcs", "description", VPC_LABEL)
        if found:
            for _ in range(30):
                try:
                    vultr("DELETE", f"/vpcs/{found['id']}")
                    log(f"vpc {found['id']} deleted")
                    break
                except RuntimeError:
                    time.sleep(5)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("up")
    up.add_argument("--allow", nargs="+", required=True, help="CIDRs allowed to SSH (the operator's addresses)")
    up.add_argument("--snapshot", default=None, help="sandbox snapshot ID (kept from the last run if omitted)")
    sub.add_parser("status")
    d = sub.add_parser("destroy")
    d.add_argument("--vpc", action="store_true", help="also delete the VPC")
    args = ap.parse_args()
    {"up": cmd_up, "status": cmd_status, "destroy": cmd_destroy}[args.cmd](args)


if __name__ == "__main__":
    main()
