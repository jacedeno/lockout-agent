"""One ephemeral sandbox VM per job: create from the snapshot, reach it over the VPC, destroy it.

    with Sandbox(job_id) as sb:
        sb.ssh("uname -a")
    # here the instance is deleted and its 404 verified, whatever happened inside the block

The sandbox gets a per-job SSH key (registered with Vultr for the create, deleted at teardown)
and nothing else: no credentials, no user-data. It only accepts SSH from the control plane's
VPC address (baked into the snapshot) and its firewall group has no rules, so its public IP
takes no inbound traffic at all.

Configuration comes from the environment (/etc/arz/control-plane.env): ARZ_SNAPSHOT_ID,
ARZ_VPC_ID, ARZ_SANDBOX_FIREWALL_ID, ARZ_REGION, ARZ_SANDBOX_PLAN.
"""

import datetime as dt
import os
import shutil
import subprocess
import tempfile
import time

from . import vultr

TAG = "arz-sandbox"  # the reaper deletes anything with this tag and label prefix after 10 min


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


class Sandbox:
    def __init__(self, job_id, log=print):
        self.job_id = job_id
        self.label = f"{TAG}-{job_id}"
        self.log = log
        self.events = []
        self.facts = {"label": self.label, "region": os.environ.get("ARZ_REGION", "dfw"),
                      "plan": os.environ.get("ARZ_SANDBOX_PLAN", "vc2-1c-1gb"),
                      "snapshot_id": os.environ["ARZ_SNAPSHOT_ID"]}
        self.instance_id = self.sshkey_id = self.vpc_ip = None
        self.keydir = None
        self._t0 = time.monotonic()

    # -- bookkeeping ---------------------------------------------------------------------------
    def event(self, name, **fields):
        e = {"at": now(), "t_s": round(time.monotonic() - self._t0, 1), "event": name, **fields}
        self.events.append(e)
        self.log(f"[{e['t_s']:6.1f}s] {name} {fields if fields else ''}".rstrip())
        return e

    # -- lifecycle -----------------------------------------------------------------------------
    def __enter__(self):
        try:
            self.provision()
        except BaseException:
            self.teardown()
            raise
        return self

    def __exit__(self, *exc):
        self.teardown()
        return False

    def provision(self):
        self.keydir = tempfile.mkdtemp(prefix=f"arz-{self.job_id}-")
        key = os.path.join(self.keydir, "id_ed25519")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", self.label, "-f", key], check=True)
        self.sshkey_id = vultr.add_ssh_key(self.label, open(key + ".pub").read().strip())
        inst = vultr.create_instance({
            "region": self.facts["region"], "plan": self.facts["plan"], "snapshot_id": self.facts["snapshot_id"],
            "label": self.label, "hostname": self.label, "tags": [TAG], "enable_ipv6": False,
            "backups": "disabled", "activation_email": False, "sshkey_id": [self.sshkey_id],
            "attach_vpc": [os.environ["ARZ_VPC_ID"]], "firewall_group_id": os.environ["ARZ_SANDBOX_FIREWALL_ID"]})
        self.instance_id = inst["id"]
        self.facts["instance_id"] = inst["id"]
        self.facts["created_at"] = now()
        self.event("sandbox_created", instance_id=inst["id"])
        inst, secs = vultr.wait_active(inst["id"])
        self.event("sandbox_active", after_s=round(secs))
        self.vpc_ip = vultr.vpc_ip(inst["id"], os.environ["ARZ_VPC_ID"])
        self.facts["vpc_ip"] = self.vpc_ip
        self.wait_ssh()
        self.event("sandbox_ready", vpc_ip=self.vpc_ip)

    def ssh_argv(self):
        # Ephemeral hosts on our own VPC: there is no stable host key to pin.
        return ["ssh", "-i", os.path.join(self.keydir, "id_ed25519"), "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                "-o", "ConnectTimeout=10", f"root@{self.vpc_ip}"]

    def ssh(self, cmd, input=None, timeout=300, check=True):
        p = subprocess.run(self.ssh_argv() + [cmd], input=input, capture_output=True, timeout=timeout)
        if check and p.returncode:
            raise RuntimeError(f"sandbox command failed (rc={p.returncode}): {cmd}\n{p.stderr.decode()[-500:]}")
        return p

    def wait_ssh(self, timeout=480):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            try:
                if self.ssh("true", timeout=30, check=False).returncode == 0:
                    return
            except subprocess.TimeoutExpired:
                pass
            time.sleep(5)
        raise RuntimeError(f"sandbox {self.instance_id} never accepted SSH on {self.vpc_ip}")

    def teardown(self):
        if self.instance_id:
            try:
                secs = vultr.delete_verified(self.instance_id)
                self.facts.update(destroyed_at=now(), destroy_verified=True)
                self.event("sandbox_destroyed", verified_404=True, after_s=round(secs, 1))
            except Exception as e:  # noqa: BLE001 - the reaper is the backstop; record and go on
                self.facts.update(destroy_verified=False, destroy_error=str(e)[:300])
                self.event("sandbox_destroy_failed", error=str(e)[:300])
            self.instance_id = None
        if self.sshkey_id:
            try:
                vultr.delete_ssh_key(self.sshkey_id)
            except Exception as e:  # noqa: BLE001
                self.event("sshkey_delete_failed", error=str(e)[:200])
            self.sshkey_id = None
        if self.keydir:
            shutil.rmtree(self.keydir, ignore_errors=True)
            self.keydir = None
