"""Minimal Vultr API v2 client for the control plane (stdlib only).

Reads VULTR_API_KEY (and optionally VULTR_API_URL) from the environment. The key never leaves
the control plane: nothing here sends it to a sandbox.
"""

import json
import os
import time
import urllib.error
import urllib.request


class VultrError(RuntimeError):
    pass


def _api():
    return os.environ.get("VULTR_API_URL", "https://api.vultr.com/v2").rstrip("/")


def call(method, path, body=None, ok=(200, 201, 202, 204)):
    """Return (status, data). Retries 429 and 5xx; raises VultrError unless status is in ok.

    Pass ok=None to get any status back without raising (used for 404 checks).
    """
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(5):
        req = urllib.request.Request(_api() + path, method=method, data=data,
                                     headers={"Authorization": f"Bearer {os.environ['VULTR_API_KEY']}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                status, out = r.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                status, out = e.code, json.loads(raw)
            except ValueError:
                status, out = e.code, {"error": raw.decode(errors="replace")[:300]}
            if (e.code == 429 or e.code >= 500) and attempt < 4:
                time.sleep(2 * (attempt + 1))
                continue
        break
    if ok is not None and status not in ok:
        raise VultrError(f"{method} {path}: HTTP {status} {out}")
    return status, out


def add_ssh_key(name, public_key):
    return call("POST", "/ssh-keys", {"name": name, "ssh_key": public_key})[1]["ssh_key"]["id"]


def delete_ssh_key(key_id):
    call("DELETE", f"/ssh-keys/{key_id}", ok=(204, 404))


def create_instance(body):
    return call("POST", "/instances", body)[1]["instance"]


def wait_active(iid, timeout=600):
    """Wait for active + running + a public IP. Returns (instance, seconds)."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        inst = call("GET", f"/instances/{iid}")[1]["instance"]
        if inst["status"] == "active" and inst["power_status"] == "running" and inst["main_ip"] != "0.0.0.0":
            return inst, time.monotonic() - start
        time.sleep(5)
    raise VultrError(f"instance {iid} not active after {timeout}s")


def vpc_ip(iid, vpc_id, timeout=120):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        for v in call("GET", f"/instances/{iid}/vpcs")[1].get("vpcs", []):
            if v.get("id") == vpc_id and v.get("ip_address"):
                return v["ip_address"]
        time.sleep(3)
    raise VultrError(f"instance {iid} has no address on VPC {vpc_id} after {timeout}s")


def delete_verified(iid, timeout=120):
    """DELETE the instance, then poll until GET returns 404. Returns seconds to 404."""
    start = time.monotonic()
    status, body = call("DELETE", f"/instances/{iid}", ok=None)
    if status not in (204, 404):
        raise VultrError(f"DELETE /instances/{iid}: HTTP {status} {body}")
    while time.monotonic() - start < timeout:
        if call("GET", f"/instances/{iid}", ok=None)[0] == 404:
            return time.monotonic() - start
        time.sleep(2)
    raise VultrError(f"instance {iid} still present {timeout}s after DELETE")
