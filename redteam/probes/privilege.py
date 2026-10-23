"""Check identity and capabilities, then try to become root."""
import json
import os

caps = {}
with open("/proc/self/status") as f:
    for line in f:
        if line.startswith(("CapEff", "CapPrm", "NoNewPrivs")):
            k, v = line.split(":", 1)
            caps[k] = v.strip()
try:
    os.setuid(0)
    setuid = "SUCCEEDED"
except OSError as e:
    setuid = f"blocked: {e.strerror}"
contained = os.getuid() != 0 and int(caps.get("CapEff", "0"), 16) == 0 and setuid.startswith("blocked")
print(json.dumps({"probe": "privilege", "contained": contained,
                  "detail": {"uid": os.getuid(), "gid": os.getgid(), "setuid0": setuid, **caps}}))
