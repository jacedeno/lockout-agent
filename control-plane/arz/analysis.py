"""Analysis job with a signed receipt: vibkit on one dataset, in a throwaway sandbox.

The day-2 exit criterion: a CLI job on synthetic data gives the right ISO zone and fault, and
its receipt bundle verifies. The analysis script here is fixed; the agent loop will have the
LLM write it instead, and the receipt will carry the prompt and response hashes too.

    cd /opt/arz/app && set -a && . /etc/arz/control-plane.env && set +a && \\
      /opt/arz/venv/bin/python -m arz.analysis --dataset misalignment

Datasets live in ARZ_DATASETS (default /opt/arz/datasets/synth): <name>.csv + <name>.json.
Output in /var/lib/arz/jobs/<job-id>/: the pulled files, report.json and receipt.zip.
Exit code 0 only if the run succeeded, every hash matched, the sandbox's 404 was verified,
the receipt verifies against ARZ_RECEIPT_PUBKEY and the result matches any ground truth.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time

from arz_receipts import EventLog, load_private_key, verify_bundle, write_bundle

from .sandbox import Sandbox, now

JOBS = "/var/lib/arz/jobs"

SCRIPT = '''\
import json, sys
import vibkit
name = sys.argv[1] if len(sys.argv) > 1 else "{name}"
d = vibkit.analyze(f"/work/input/{{name}}.csv", f"/work/input/{{name}}.json")
json.dump(d, open("/work/out/result.json", "w"), indent=2)
print(json.dumps({{"primary_fault": d["primary_fault"], "zone": d["iso_20816_3"]["zone"],
                  "worst_radial_mm_s": d["iso_20816_3"]["worst_radial_mm_s"]}}))
'''


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dataset", required=True)
    args = p.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9_]{1,64}", args.dataset):
        p.error("dataset names are lowercase letters, digits and underscores")

    root = os.environ.get("ARZ_DATASETS", "/opt/arz/datasets/synth")
    csv_path, meta_path = (os.path.join(root, f"{args.dataset}.{ext}") for ext in ("csv", "json"))
    meta = json.load(open(meta_path))
    truth = meta.get("ground_truth") or {}
    # The sandbox gets the measurement and the machine data, never the answer key.
    sandbox_meta = {k: v for k, v in meta.items() if k != "ground_truth"}

    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    local = os.path.join(JOBS, job_id)
    os.makedirs(local, mode=0o700)
    files = {f"input/{args.dataset}.csv": open(csv_path, "rb").read(),
             f"input/{args.dataset}.json": json.dumps(sandbox_meta, indent=2).encode(),
             "code/analyze.py": SCRIPT.format(name=args.dataset).encode()}

    log = EventLog()
    log.append("job_created", {"job_id": job_id, "kind": "analysis", "dataset": args.dataset,
                               "inputs": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}})
    report = {"job_id": job_id, "kind": "analysis", "dataset": args.dataset, "started_at": now()}
    ok = False
    sb = Sandbox(job_id, log=lambda m: print(m, file=sys.stderr, flush=True), on_event=log.append)
    try:
        with sb:
            sb.upload(files)
            summary = sb.run("analyze.py", timeout=120)
            verification = sb.pull(summary, local)
        log.append("sandbox_facts", sb.facts)

        result = json.load(open(os.path.join(local, "out", "result.json")))
        got = {"fault": result["primary_fault"], "iso_zone": result["iso_20816_3"]["zone"]}
        expected = {k: truth[k] for k in ("fault", "iso_zone") if k in truth}
        log.append("result", {"primary_fault": got["fault"], "iso_zone": got["iso_zone"],
                              "worst_radial_mm_s": result["iso_20816_3"]["worst_radial_mm_s"],
                              "findings": result["findings"]})
        if expected:
            log.append("ground_truth_check", {"expected": expected, "got": got,
                                              "match": all(got[k] == v for k, v in expected.items())})

        # Sign: log + every pulled file. The key never leaves the control plane.
        art = os.path.join(local, "bundle-artifacts")
        shutil.copytree(os.path.join(local, "out"), os.path.join(art, "out"))
        for stream in ("stdout", "stderr"):
            shutil.copy(os.path.join(local, summary[stream]["path"]), os.path.join(art, f"{stream}.txt"))
        bundle = os.path.join(local, "receipt.zip")
        manifest = write_bundle(bundle, log, art, load_private_key(os.environ["ARZ_RECEIPT_KEY"]), job_id,
                                extra={"dataset": args.dataset, "result": got})
        shutil.rmtree(art)
        verified = verify_bundle(bundle, os.environ["ARZ_RECEIPT_PUBKEY"])

        report.update(result=got, expected=expected, verification=verification,
                      receipt={"path": bundle, "log_head": manifest["log"]["head"], "events": manifest["log"]["events"],
                               "key_id": manifest["signer"]["key_id"], "verified": verified["ok"],
                               "failed_checks": [c for c in verified["checks"] if not c["ok"]]})
        report["checks"] = {
            "exit_code_0": summary["exit_code"] == 0 and not summary["timed_out"],
            "hashes_match": all(v["match"] for v in verification),
            "destroy_verified": sb.facts.get("destroy_verified", False),
            "receipt_verifies": verified["ok"],
            "matches_ground_truth": (all(got[k] == v for k, v in expected.items()) if expected else None),
        }
        ok = all(v is not False for v in report["checks"].values())
    except Exception as e:  # noqa: BLE001 - report it; the sandbox is already torn down
        report["error"] = f"{e.__class__.__name__}: {e}"[:1000]
    finally:
        report.update(sandbox=sb.facts, ended_at=now(), ok=ok)
        with open(os.path.join(local, "report.json"), "w") as f:
            json.dump(report, f, indent=2)
        print(json.dumps(report, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
