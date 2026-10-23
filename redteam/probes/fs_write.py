"""Try to write outside the job's output directory."""
import json

detail = {}
for path in ("/etc/arz-probe", "/usr/arz-probe", "/work/input/arz-probe", "/work/code/arz-probe", "/arz-probe"):
    try:
        with open(path, "w") as f:
            f.write("x")
        detail[path] = "WRITTEN"
    except OSError as e:
        detail[path] = f"blocked: {e.strerror}"
contained = all(v.startswith("blocked") for v in detail.values())
print(json.dumps({"probe": "fs_write", "contained": contained, "detail": detail}))
