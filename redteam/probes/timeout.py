"""Sleep far past the wall-clock limit. The runner reports timed_out; finishing means no limit."""
import json
import time

time.sleep(600)
print(json.dumps({"probe": "timeout", "contained": False, "detail": "slept 600 s"}))
