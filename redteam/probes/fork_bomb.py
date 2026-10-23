"""Spawn processes until the kernel refuses. Contained if the pids limit stops it early."""
import json
import os
import signal
import time

children = []
error = None
for _ in range(2000):
    try:
        pid = os.fork()
    except OSError as e:
        error = e.strerror
        break
    if pid == 0:
        time.sleep(30)
        os._exit(0)
    children.append(pid)
for pid in children:
    os.kill(pid, signal.SIGKILL)
for pid in children:
    os.waitpid(pid, 0)
contained = error is not None and len(children) < 500
print(json.dumps({"probe": "fork_bomb", "contained": contained,
                  "detail": {"spawned": len(children), "stopped_by": error}}))
