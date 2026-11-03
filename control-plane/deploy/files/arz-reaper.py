#!/usr/bin/env python3
"""Sandbox reaper: delete every sandbox instance older than --max-age seconds, then verify 404.

Runs from arz-reaper.timer every 2 minutes. It only touches instances that carry the
`arz-sandbox` tag AND whose label starts with `arz-sandbox`; anything else is left alone.
Warm-pool sandboxes (also tagged `arz-warm`) get --warm-max-age instead: they wait idle for a
job, and the pool recycles them well before that limit.
Exits non-zero when the API fails or a delete is not verified, so systemd marks the run failed.

    arz-reaper.py [--max-age SECONDS] [--warm-max-age SECONDS] [--dry-run]

Reads VULTR_API_KEY (and optionally VULTR_API_URL) from the environment.
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("VULTR_API_URL", "https://api.vultr.com/v2").rstrip("/")
TAG = "arz-sandbox"
WARM_TAG = "arz-warm"


def call(method, path):
    req = urllib.request.Request(API + path, method=method,
                                 headers={"Authorization": f"Bearer {os.environ['VULTR_API_KEY']}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"error": raw.decode(errors="replace")[:200]}


def log(**fields):
    print(json.dumps(fields, sort_keys=True), flush=True)


def list_sandboxes():
    rows, cursor = [], ""
    while True:
        path = f"/instances?tag={TAG}&per_page=100" + (f"&cursor={cursor}" if cursor else "")
        status, body = call("GET", path)
        if status != 200:
            raise RuntimeError(f"GET /instances: HTTP {status} {body}")
        rows += body.get("instances", [])
        cursor = body.get("meta", {}).get("links", {}).get("next", "")
        if not cursor:
            return rows


def delete_verified(iid, timeout=60):
    status, body = call("DELETE", f"/instances/{iid}")
    if status not in (204, 404):
        return False, f"DELETE: HTTP {status} {body}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, _ = call("GET", f"/instances/{iid}")
        if status == 404:
            return True, None
        time.sleep(2)
    return False, f"GET still {status} after {timeout}s"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-age", type=int, default=600, help="seconds (default 600)")
    ap.add_argument("--warm-max-age", type=int, default=3600, help="seconds for arz-warm sandboxes (default 3600)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    try:
        rows = list_sandboxes()
    except Exception as e:  # noqa: BLE001 - report and fail the unit
        log(event="error", error=str(e)[:300])
        return 2

    failed = deleted = 0
    for inst in rows:
        label = inst.get("label", "")
        if TAG not in inst.get("tags", []) or not label.startswith(TAG):
            log(event="skip", id=inst["id"], label=label, reason="tag or label mismatch")
            continue
        age = (now - dt.datetime.fromisoformat(inst["date_created"])).total_seconds()
        limit = args.warm_max_age if WARM_TAG in inst.get("tags", []) else args.max_age
        if age < limit:
            continue
        if args.dry_run:
            log(event="would_delete", id=inst["id"], label=label, age_s=int(age))
            continue
        ok, err = delete_verified(inst["id"])
        deleted += ok
        failed += not ok
        log(event="deleted" if ok else "delete_failed", id=inst["id"], label=label,
            age_s=int(age), verified_404=ok, error=err)

    log(event="pass", seen=len(rows), deleted=deleted, failed=failed, max_age_s=args.max_age,
        dry_run=args.dry_run)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
