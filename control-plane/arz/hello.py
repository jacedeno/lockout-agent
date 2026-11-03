"""`hello` job: the day-1 end-to-end check, run on the control plane.

provision a sandbox → upload a job (known input + script) → run it in the locked container →
pull the outputs → recompute every SHA-256 on the control plane → delete the VM → verify 404.

    cd /opt/arz/app && set -a && . /etc/arz/control-plane.env && set +a && python3 -s -m arz.hello

Prints the report as JSON and writes it to /var/lib/arz/jobs/<job-id>/report.json.
Exit code 0 only if the job ran, the answer is right, every hash matches and the 404 is verified.
"""

import hashlib
import io
import json
import math
import os
import secrets
import sys
import tarfile
import time

from .sandbox import Sandbox, now

JOBS = "/var/lib/arz/jobs"
REMOTE = "/root/job"
EXPECTED_RMS = 1 / math.sqrt(2)  # 1 g sine over whole periods

HELLO = '''\
import json, platform, socket
import numpy as np
x = np.loadtxt("/work/input/sample.csv", delimiter=",", skiprows=1)
out = {"hello": "from the sandbox", "python": platform.python_version(), "numpy": np.__version__,
       "hostname": socket.gethostname(), "rows": int(x.shape[0]),
       "accel_rms_g": round(float(np.sqrt(np.mean(x[:, 1] ** 2))), 6)}
json.dump(out, open("/work/out/result.json", "w"), indent=2)
print(json.dumps(out))
'''


def sample_csv(fs=1000, freq=50, seconds=1):
    rows = ["t,accel_g"] + [f"{i / fs:.6f},{math.sin(2 * math.pi * freq * i / fs):.9f}" for i in range(fs * seconds)]
    return ("\n".join(rows) + "\n").encode()


def job_tar(files):
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
    return buf.getvalue()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    local = os.path.join(JOBS, job_id)
    os.makedirs(local, mode=0o700)
    files = {"input/sample.csv": sample_csv(), "code/hello.py": HELLO.encode()}
    report = {"job_id": job_id, "kind": "hello", "started_at": now(),
              "inputs": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}
    ok = False
    sb = Sandbox(job_id, log=lambda m: print(m, file=sys.stderr, flush=True))
    try:
        with sb:
            sb.ssh(f"rm -rf {REMOTE} && mkdir -p {REMOTE} && chmod 755 {REMOTE} && tar -xf - -C {REMOTE}",
                   input=job_tar(files))
            sb.event("job_uploaded")
            p = sb.ssh(f"/opt/arz/runner/arz-run --job {REMOTE} --script hello.py --timeout 60", timeout=180)
            summary = json.loads(p.stdout)
            report["run"] = summary
            sb.event("job_ran", exit_code=summary["exit_code"], timed_out=summary["timed_out"],
                     oom_killed=summary["oom_killed"])

            wanted = [summary["stdout"]["path"], summary["stderr"]["path"]] + [a["path"] for a in summary["artifacts"]]
            p = sb.ssh(f"tar -C {REMOTE} --null -T - -cf -", input="\0".join(wanted).encode() + b"\0", timeout=120)
            with tarfile.open(fileobj=io.BytesIO(p.stdout)) as tar:
                tar.extractall(local, filter="data")  # no links, no absolute paths, no traversal
            sb.event("outputs_pulled", files=len(wanted), bytes=len(p.stdout))

        checks = [("stdout", summary["stdout"]), ("stderr", summary["stderr"])] + \
                 [(a["path"], a) for a in summary["artifacts"]]
        verification = []
        for name, entry in checks:
            path = os.path.join(local, entry["path"])
            mine = sha256(path) if os.path.isfile(path) else None
            verification.append({"path": entry["path"], "sandbox_sha256": entry["sha256"],
                                 "control_plane_sha256": mine, "match": mine == entry["sha256"]})
        report["verification"] = verification
        result = json.load(open(os.path.join(local, "out", "result.json")))
        report["result"] = result
        answer_ok = abs(result["accel_rms_g"] - EXPECTED_RMS) < 1e-3
        report["checks"] = {
            "exit_code_0": summary["exit_code"] == 0 and not summary["timed_out"],
            "answer": {"expected_rms_g": round(EXPECTED_RMS, 6), "got": result["accel_rms_g"], "ok": answer_ok},
            "hashes_match": all(v["match"] for v in verification),
            "destroy_verified": sb.facts.get("destroy_verified", False),
        }
        ok = (report["checks"]["exit_code_0"] and answer_ok and report["checks"]["hashes_match"]
              and report["checks"]["destroy_verified"])
    except Exception as e:  # noqa: BLE001 - report it; the sandbox is already torn down
        report["error"] = f"{e.__class__.__name__}: {e}"[:1000]
    finally:
        report.update(sandbox=sb.facts, events=sb.events, ended_at=now(), ok=ok)
        with open(os.path.join(local, "report.json"), "w") as f:
            json.dump(report, f, indent=2)
        print(json.dumps(report, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
