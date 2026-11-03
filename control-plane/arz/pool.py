"""Warm pool of one: a sandbox booted ahead of time, so a job does not wait ~4 minutes for one.

Rules:
- A warm sandbox serves exactly one job and is destroyed after it, like any other sandbox.
- As soon as one is taken, the replacement starts booting in the background.
- An idle warm sandbox is recycled (destroyed, 404 verified, replaced) after RECYCLE_S, well
  inside the reaper's 60-minute limit for `arz-warm` instances; a job that takes one at the
  last moment still finishes long before the reaper would act.
- On start, any warm sandbox left by a previous process is destroyed (its SSH key lived only in
  that process), then a fresh one is booted.
- Before handing one out, the pool checks it still answers and has never run a job.

Costs one idle vc2-1c-1gb (~$0.007/h) while the control plane runs.
"""

import secrets
import threading
import time

from . import vultr
from .sandbox import TAG, WARM_TAG, Sandbox

RECYCLE_S = 40 * 60


class WarmPool:
    def __init__(self, log=print):
        self.log = log
        self.lock = threading.Condition()
        self.ready = None          # a provisioned, unused Sandbox
        self.booting = False
        self.last_error = None
        self.stopped = False

    # -- public --------------------------------------------------------------------------------
    def start(self):
        self._cleanup_leftovers()
        threading.Thread(target=self._loop, name="warm-pool", daemon=True).start()

    def status(self):
        with self.lock:
            age = time.time() - self.ready.ready_at if self.ready else None
            return {"ready": self.ready is not None, "booting": self.booting,
                    "ready_for_s": round(age) if age is not None else None, "last_error": self.last_error}

    def acquire(self, timeout=600):
        """Take the warm sandbox (waiting for one to finish booting if needed). Returns a
        provisioned Sandbox that the caller must use in a `with` block (which destroys it)."""
        deadline = time.monotonic() + timeout
        with self.lock:
            while True:
                if self.ready is not None:
                    sb, self.ready = self.ready, None
                    self.lock.notify_all()  # wake the loop to boot the replacement
                    if sb.alive():
                        return sb
                    self.log(f"warm sandbox {sb.label} failed its check; destroying it")
                    threading.Thread(target=sb.teardown, daemon=True).start()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("no warm sandbox became ready in time")
                self.lock.wait(min(remaining, 5))

    def stop(self):
        with self.lock:
            self.stopped = True
            sb, self.ready = self.ready, None
            self.lock.notify_all()
        if sb:
            sb.teardown()

    # -- internals -----------------------------------------------------------------------------
    def _cleanup_leftovers(self):
        """Delete warm sandboxes left by a previous process, in the background.

        A sandbox that was still booting when that process stopped answers DELETE with
        409 "Server is currently locked" until it is active, so keep retrying (up to ~10 min)
        instead of giving up; the reaper's 60-minute limit stays the backstop.
        """
        try:
            rows = vultr.call("GET", f"/instances?tag={WARM_TAG}&per_page=100")[1].get("instances", [])
        except Exception as e:  # noqa: BLE001
            self.log(f"leftover listing failed: {e}")
            return
        leftovers = [(i["id"], i["label"]) for i in rows
                     if TAG in i.get("tags", []) and i.get("label", "").startswith(TAG)]

        def remove(iid, label):
            for _ in range(40):
                try:
                    vultr.delete_verified(iid)
                    self.log(f"removed leftover warm sandbox {label}")
                    return
                except Exception as e:  # noqa: BLE001 - usually 409 while it is still booting
                    last = e
                    time.sleep(15)
            self.log(f"could not remove leftover {label}: {last}; the reaper will")

        for iid, label in leftovers:
            threading.Thread(target=remove, args=(iid, label), name=f"cleanup-{label}", daemon=True).start()

    def _boot(self):
        sb = Sandbox(f"warm-{time.strftime('%H%M%S')}-{secrets.token_hex(2)}", log=self.log, warm=True)
        try:
            sb.provision()
            sb.ready_at = time.time()
            return sb
        except Exception as e:  # noqa: BLE001
            sb.teardown()
            raise e

    def _loop(self):
        while True:
            with self.lock:
                if self.stopped:
                    return
                stale = self.ready is not None and time.time() - self.ready.ready_at > RECYCLE_S
                need = self.ready is None or stale
                if stale:
                    old, self.ready = self.ready, None
                    threading.Thread(target=old.teardown, daemon=True).start()
                if not need:
                    self.lock.wait(30)
                    continue
                self.booting = True
            try:
                sb = self._boot()
                with self.lock:
                    self.ready, self.booting, self.last_error = sb, False, None
                    self.lock.notify_all()
            except Exception as e:  # noqa: BLE001 - retry after a pause
                with self.lock:
                    self.booting, self.last_error = False, str(e)[:300]
                self.log(f"warm boot failed: {e}")
                time.sleep(60)
