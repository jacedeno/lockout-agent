"""Development stand-in for Sandbox: the same job steps through arz-run and rootless Podman on
the developer's machine, so prompts can be iterated in seconds instead of a 4-minute VM boot.

Never used on the control plane and never for a submitted run: it has no VM boundary, no VPC
firewall and no 404 to verify. Its facts say so, and its receipts carry "local": true.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time

from .sandbox import _sha256, now

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUNNER = os.path.join(REPO, "sandbox", "runner", "arz-run")


class LocalSandbox:
    def __init__(self, job_id, log=print, on_event=None, image="localhost/arz-runner:latest"):
        self.job_id, self.log, self.on_event, self.image = job_id, log, on_event, image
        self.events = []
        self.facts = {"local": True, "engine": "podman", "image": image}
        self.root = None
        self._t0 = time.monotonic()

    def event(self, name, **fields):
        e = {"at": now(), "t_s": round(time.monotonic() - self._t0, 1), "event": name, **fields}
        self.events.append(e)
        self.log(f"[{e['t_s']:6.1f}s] {name} {fields if fields else ''}".rstrip())
        if self.on_event:
            self.on_event(name, fields)
        return e

    def __enter__(self):
        self.root = tempfile.mkdtemp(prefix=f"arz-local-{self.job_id}-")
        os.chmod(self.root, 0o755)
        self.event("sandbox_ready", local=True)
        return self

    def __exit__(self, *exc):
        subprocess.run(["podman", "unshare", "rm", "-rf", self.root], capture_output=True)
        shutil.rmtree(self.root, ignore_errors=True)
        self.facts.update(destroyed_at=now(), destroy_verified=None)
        self.event("sandbox_destroyed", local=True)
        return False

    def upload(self, files):
        for d in ("input", "code"):
            os.makedirs(os.path.join(self.root, d), mode=0o755, exist_ok=True)
        for name, data in files.items():
            self._write(name, data)
        self.event("job_uploaded", files={k: hashlib.sha256(v).hexdigest() for k, v in files.items()})

    def put(self, name, data):
        self._write(name, data)
        self.event("file_added", path=name, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))

    def _write(self, name, data):
        path = os.path.join(self.root, name)
        with open(path, "wb") as f:
            f.write(data)
        os.chmod(path, 0o644)

    def run(self, script, timeout=60, extra_args=""):
        subprocess.run(["podman", "unshare", "rm", "-rf", os.path.join(self.root, "out")], capture_output=True)
        p = subprocess.run(["python3", "-I", RUNNER, "--job", self.root, "--script", script, "--engine", "podman",
                            "--image", self.image, "--timeout", str(int(timeout))],
                           capture_output=True, text=True, timeout=timeout + 120, check=True)
        summary = json.loads(p.stdout)
        self.event("step_ran", script=script, script_sha256=summary["script"]["sha256"], exit_code=summary["exit_code"],
                   timed_out=summary["timed_out"], oom_killed=summary["oom_killed"], stdout=summary["stdout"],
                   stderr=summary["stderr"], artifacts=summary["artifacts"])
        return summary

    def pull(self, summary, dest):
        out = []
        for e in [summary["stdout"], summary["stderr"]] + summary["artifacts"]:
            src, dst = os.path.join(self.root, e["path"]), os.path.join(dest, e["path"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            subprocess.run(["podman", "unshare", "cat", src], stdout=open(dst, "wb"), check=True)
            mine = _sha256(dst)
            out.append({"path": e["path"], "sandbox_sha256": e["sha256"], "control_plane_sha256": mine,
                        "match": mine == e["sha256"]})
        self.event("outputs_pulled", files=len(out), all_hashes_match=all(v["match"] for v in out))
        return out
