"""Web front for the agent: start a job, watch it live, read the work order, verify a receipt.

    uvicorn arz.web:app --host 127.0.0.1 --port 8000      # behind Caddy (TLS) on the control plane

Guardrails (the plan (kept private) §6): one job at a time behind a small queue; live runs need the access
code shared with judges; a per-IP hourly limit and a global daily cap; anyone can watch past
runs (the replay) and verify receipts without a code.
"""

import collections
import datetime as dt
import hashlib
import hmac
import json
import os
import queue
import re
import threading
import time

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from arz_receipts import verify_bundle

from . import llm
from .agent import Agent
from .sandbox import Sandbox

JOBS = os.environ.get("ARZ_JOBS", "/var/lib/arz/jobs")
DATASETS = os.environ.get("ARZ_DATASETS", "/opt/arz/datasets/synth")
ACCESS_CODE = os.environ.get("ARZ_ACCESS_CODE", "")
DAILY_CAP = int(os.environ.get("ARZ_DAILY_CAP", "40"))
PER_IP_HOURLY = int(os.environ.get("ARZ_PER_IP_HOURLY", "6"))
QUEUE_MAX = 4
LOCAL = os.environ.get("ARZ_LOCAL") == "1"      # development only: Podman, no VM, no warm pool
USE_POOL = not LOCAL and os.environ.get("ARZ_WARM_POOL", "1") == "1"
PUBKEY = os.environ.get("ARZ_RECEIPT_PUBKEY", "")
JOB_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MAX_UPLOAD = 10 * 1024 * 1024

DESCRIPTIONS = {
    "healthy": "Routine reading, nothing wrong",
    "unbalance": "Strong once-per-revolution vibration",
    "misalignment": "Coupling problem after a motor re-install",
    "bearing_outer_race": "Early bearing damage, overall level still low",
    "prompt_injection": "Red team: the technician notes try to hijack the agent",
}


class Job:
    def __init__(self, agent, dataset, ip_hash):
        self.id, self.agent, self.dataset, self.ip_hash = agent.job_id, agent, dataset, ip_hash
        self.status, self.events, self.report = "queued", [], None
        self.created_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    def summary(self):
        r = self.report or {}
        return {"id": self.id, "dataset": self.dataset, "status": self.status, "created_at": self.created_at,
                "result": r.get("result"), "priority": r.get("priority"), "ok": r.get("ok"),
                "duration_s": r.get("duration_s"), "events": len(self.events)}


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = collections.OrderedDict()
        self.queue = queue.Queue(maxsize=QUEUE_MAX)
        self.hits = collections.defaultdict(collections.deque)
        self.day, self.day_count = None, 0

    def load_history(self, limit=30):
        """Past jobs from disk, so the replay survives a restart."""
        if not os.path.isdir(JOBS):
            return
        for jid in sorted(os.listdir(JOBS))[-limit:]:
            d = os.path.join(JOBS, jid)
            rp, ep = os.path.join(d, "report.json"), os.path.join(d, "events.jsonl")
            if not (JOB_ID.match(jid) and os.path.isfile(rp) and os.path.isfile(ep)):
                continue
            try:
                report = json.load(open(rp))
                events = [json.loads(line) for line in open(ep) if line.strip()]
            except (OSError, ValueError):  # e.g. a CLI run owned by root
                continue
            job = Job.__new__(Job)
            job.id, job.agent, job.dataset, job.ip_hash = jid, None, report.get("dataset"), None
            job.report, job.status = report, "done" if report.get("ok") else "failed"
            job.events = events
            job.events.append({"seq": None, "kind": "job_finished", "data": {"ok": report.get("ok")}})
            job.created_at = report.get("started_at", "")
            self.jobs[jid] = job

    def admit(self, ip):
        """Raise HTTPException if this client or the day is over its limit; else count it."""
        now = time.time()
        today = dt.date.today().isoformat()
        with self.lock:
            if self.day != today:
                self.day, self.day_count = today, 0
            if self.day_count >= DAILY_CAP:
                raise HTTPException(429, f"daily cap of {DAILY_CAP} live runs reached; past runs stay viewable")
            hits = self.hits[ip]
            while hits and now - hits[0] > 3600:
                hits.popleft()
            if len(hits) >= PER_IP_HOURLY:
                raise HTTPException(429, f"limit of {PER_IP_HOURLY} runs per hour from one address")
            hits.append(now)
            self.day_count += 1


store = Store()
pool = None


def worker():
    while True:
        job = store.queue.get()
        job.status = "running"
        try:
            job.report = job.agent.run()
            job.status = "done" if job.report.get("ok") else "failed"
        except Exception as e:  # noqa: BLE001
            job.report = {"ok": False, "error": f"{e.__class__.__name__}: {e}"[:500]}
            job.status = "failed"
            job.events.append({"seq": None, "kind": "job_finished", "data": {"ok": False}})


app = FastAPI(title="Agent Rush control plane", docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
def startup():
    global pool
    os.makedirs(JOBS, exist_ok=True)
    store.load_history()
    if USE_POOL:
        from .pool import WarmPool
        pool = WarmPool(log=lambda m: print(f"[pool] {m}", flush=True))
        pool.start()
    threading.Thread(target=worker, name="job-worker", daemon=True).start()


@app.on_event("shutdown")
def shutdown():
    if pool:
        pool.stop()


def client_ip(request):
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else request.client.host) or "?"


def datasets():
    out = []
    for name in sorted(os.listdir(DATASETS)):
        if name.endswith(".json") and os.path.isfile(os.path.join(DATASETS, name[:-5] + ".csv")):
            meta = json.load(open(os.path.join(DATASETS, name)))
            out.append({"name": name[:-5], "description": DESCRIPTIONS.get(name[:-5], ""),
                        "asset": meta.get("asset"), "asset_id": meta.get("asset_id"),
                        "rpm": (meta.get("machine") or {}).get("rpm"), "notes": meta.get("notes", "")})
    return out


def get_job(jid):
    if not JOB_ID.match(jid) or jid not in store.jobs:
        raise HTTPException(404, "no such job")
    return store.jobs[jid]


@app.get("/api/status")
def status():
    return {"queue": store.queue.qsize(), "running": any(j.status == "running" for j in store.jobs.values()),
            "warm_pool": pool.status() if pool else None, "local": LOCAL, "model": llm.default_model(),
            "escalation_model": llm.escalation_model(), "live_runs": bool(ACCESS_CODE) or LOCAL,
            "daily_cap": DAILY_CAP, "runs_today": store.day_count if store.day == dt.date.today().isoformat() else 0,
            "receipt_key": PUBKEY}


@app.get("/api/datasets")
def list_datasets():
    return datasets()


@app.get("/api/jobs")
def list_jobs():
    return [j.summary() for j in reversed(list(store.jobs.values()))][:30]


@app.post("/api/jobs")
async def create_job(request: Request):
    body = await request.json()
    dataset, code = str(body.get("dataset", "")), str(body.get("code", ""))
    if dataset not in {d["name"] for d in datasets()}:
        raise HTTPException(400, "unknown dataset")
    if not LOCAL:
        if not ACCESS_CODE:
            raise HTTPException(403, "live runs are switched off; past runs are viewable")
        if not hmac.compare_digest(code.encode(), ACCESS_CODE.encode()):
            raise HTTPException(403, "wrong access code")
    if store.queue.full():
        raise HTTPException(429, "the queue is full; try again in a few minutes")
    ip = client_ip(request)
    store.admit(ip)
    sandbox_cls = Sandbox
    if LOCAL:
        from .localsandbox import LocalSandbox
        sandbox_cls = LocalSandbox
    redteam = bool(body.get("redteam", dataset == "prompt_injection"))
    agent = Agent(dataset, DATASETS, JOBS, log=lambda m: print(m, flush=True),
                  acquire=pool.acquire if pool else None, sandbox_cls=sandbox_cls, redteam=redteam)
    job = Job(agent, dataset, hashlib.sha256(ip.encode()).hexdigest()[:12])
    agent.emit = job.events.append
    store.jobs[job.id] = job
    store.queue.put_nowait(job)
    return {"id": job.id, "position": store.queue.qsize()}


@app.get("/api/jobs/{jid}")
def job_detail(jid: str):
    job = get_job(jid)
    out = job.summary()
    out["report"] = job.report
    return out


@app.get("/api/jobs/{jid}/events")
async def job_events(jid: str, request: Request):
    import asyncio
    job = get_job(jid)
    start = int(request.headers.get("last-event-id") or request.query_params.get("from") or 0)

    async def stream():
        i, idle = start, 0
        while True:
            while i < len(job.events):
                e = job.events[i]
                i += 1
                yield f"id: {i}\ndata: {json.dumps(e)}\n\n"
                if e.get("kind") == "job_finished":
                    return
            if await request.is_disconnected():
                return
            idle += 1
            if idle % 30 == 0:  # ~12 s
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/jobs/{jid}/files/{name}")
def job_file(jid: str, name: str):
    job = get_job(jid)
    files = (job.report or {}).get("files") or {}
    if name not in files or not files[name]:
        raise HTTPException(404, "no such file")
    path = os.path.realpath(os.path.join(JOBS, jid, files[name]))
    if not path.startswith(os.path.realpath(os.path.join(JOBS, jid)) + os.sep) or not os.path.isfile(path):
        raise HTTPException(404, "no such file")
    media = {"spectrum": "image/png", "receipt": "application/zip", "work_order_md": "text/markdown; charset=utf-8"}
    return FileResponse(path, media_type=media.get(name, "application/json"),
                        filename=f"{jid}-{os.path.basename(path)}" if name == "receipt" else None)


@app.post("/api/verify")
async def verify(file: UploadFile = File(...)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "receipt bundles are under 10 MB")
    report = verify_bundle(data, PUBKEY or None)
    try:
        import io
        import zipfile
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("receipt.json"))["manifest"]
        report["job_id"], report["created_at"] = manifest.get("job_id"), manifest.get("created_at")
        report["extra"] = manifest.get("extra")
    except Exception:  # noqa: BLE001 - verify_bundle already reported why
        pass
    return JSONResponse(report)


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/verify")
def verify_page():
    return FileResponse(os.path.join(STATIC, "verify.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
