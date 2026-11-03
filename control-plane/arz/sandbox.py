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
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time

from . import vultr

TAG = "arz-sandbox"  # the reaper deletes anything with this tag and label prefix after 10 min
REMOTE = "/root/job"
RUNNER = "/opt/arz/runner/arz-run"


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


class Sandbox:
    def __init__(self, job_id, log=print, on_event=None):
        self.job_id = job_id
        self.label = f"{TAG}-{job_id}"
        self.log = log
        self.on_event = on_event  # e.g. a receipt EventLog's append(kind, data)
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
        if self.on_event:
            self.on_event(name, fields)
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
        self.event("sandbox_created", instance_id=inst["id"], region=self.facts["region"],
                   plan=self.facts["plan"], snapshot_id=self.facts["snapshot_id"])
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

    # -- job steps -----------------------------------------------------------------------------
    def upload(self, files):
        """Write {"input/x.csv": bytes, "code/s.py": bytes} into a fresh job directory."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for d in ("input", "code"):
                info = tarfile.TarInfo(d)
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                tar.addfile(info)
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o644
                tar.addfile(info, io.BytesIO(data))
        self.ssh(f"rm -rf {REMOTE} && mkdir -p {REMOTE} && chmod 755 {REMOTE} && tar -xf - -C {REMOTE}",
                 input=buf.getvalue())
        self.event("job_uploaded", files={k: hashlib.sha256(v).hexdigest() for k, v in files.items()})

    def run(self, script, timeout=60, extra_args=""):
        """Run code/<script> in the locked container; returns arz-run's JSON summary."""
        p = self.ssh(f"{RUNNER} --job {REMOTE} --script {script} --timeout {int(timeout)} {extra_args}",
                     timeout=timeout + 120)
        summary = json.loads(p.stdout)
        self.event("step_ran", script=script, script_sha256=summary["script"]["sha256"],
                   command=summary["command"], limits=summary["limits"], image_id=summary["image_id"],
                   exit_code=summary["exit_code"], timed_out=summary["timed_out"],
                   oom_killed=summary["oom_killed"], stdout=summary["stdout"], stderr=summary["stderr"],
                   artifacts=summary["artifacts"], artifacts_skipped=summary["artifacts_skipped"])
        return summary

    def pull(self, summary, dest):
        """Copy stdout, stderr and listed artifacts into dest; recompute every SHA-256 here.

        Returns [{path, sandbox_sha256, control_plane_sha256, match}]. Extraction refuses links,
        absolute paths and traversal (tarfile's 'data' filter).
        """
        entries = [summary["stdout"], summary["stderr"]] + summary["artifacts"]
        names = "\0".join(e["path"] for e in entries).encode() + b"\0"
        p = self.ssh(f"tar -C {REMOTE} --null -T - -cf -", input=names, timeout=180)
        with tarfile.open(fileobj=io.BytesIO(p.stdout)) as tar:
            tar.extractall(dest, filter="data")
        out = []
        for e in entries:
            path = os.path.join(dest, e["path"])
            mine = _sha256(path) if os.path.isfile(path) else None
            out.append({"path": e["path"], "sandbox_sha256": e["sha256"], "control_plane_sha256": mine,
                        "match": mine == e["sha256"]})
        self.event("outputs_pulled", bytes=len(p.stdout), files=len(out),
                   all_hashes_match=all(v["match"] for v in out), verification=out)
        return out

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


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
