"""`hello` job: the day-1 end-to-end check, run on the control plane.

provision a sandbox → upload a job (known input + script) → run it in the locked container →
pull the outputs → recompute every SHA-256 on the control plane → delete the VM → verify 404.

    cd /opt/arz/app && set -a && . /etc/arz/control-plane.env && set +a && /opt/arz/venv/bin/python -m arz.hello

Prints the report as JSON and writes it to /var/lib/arz/jobs/<job-id>/report.json.
Exit code 0 only if the job ran, the answer is right, every hash matches and the 404 is verified.
"""

import json
import math
import os
import secrets
import sys
import time

from .sandbox import Sandbox, now

JOBS = "/var/lib/arz/jobs"
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


def main():
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    local = os.path.join(JOBS, job_id)
    os.makedirs(local, mode=0o700)
    report = {"job_id": job_id, "kind": "hello", "started_at": now()}
    ok = False
    sb = Sandbox(job_id, log=lambda m: print(m, file=sys.stderr, flush=True))
    try:
        with sb:
            sb.upload({"input/sample.csv": sample_csv(), "code/hello.py": HELLO.encode()})
            summary = sb.run("hello.py")
            verification = sb.pull(summary, local)
        result = json.load(open(os.path.join(local, "out", "result.json")))
        answer_ok = abs(result["accel_rms_g"] - EXPECTED_RMS) < 1e-3
        report.update(run=summary, verification=verification, result=result, checks={
            "exit_code_0": summary["exit_code"] == 0 and not summary["timed_out"],
            "answer": {"expected_rms_g": round(EXPECTED_RMS, 6), "got": result["accel_rms_g"], "ok": answer_ok},
            "hashes_match": all(v["match"] for v in verification),
            "destroy_verified": sb.facts.get("destroy_verified", False)})
        ok = all(v if isinstance(v, bool) else v["ok"] for v in report["checks"].values())
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
