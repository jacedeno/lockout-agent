"""Spawn processes until the kernel refuses. Contained if the pids limit stops it early."""
import json
import os
import signal
import sys
import time

children = []
error = None
try:
    for i in range(2000):
        try:
            pid = os.fork()
        except OSError as e:
            error = e.strerror
            break
        if pid == 0:
            time.sleep(30)
            os._exit(0)
        children.append(pid)
        if i % 50 == 0:
            print(f"spawned {i + 1}", file=sys.stderr, flush=True)
except BaseException as e:  # report rather than die silently
    error = f"{type(e).__name__}: {e}"
finally:
    for pid in children:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except OSError:
            pass
contained = error is not None and len(children) < 500
print(json.dumps({"probe": "fork_bomb", "contained": contained,
                  "detail": {"spawned": len(children), "stopped_by": error}}), flush=True)
