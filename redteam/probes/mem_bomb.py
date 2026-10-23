"""Allocate memory until killed. The runner reports oom_killed; reaching the end means no cap."""
import json

blocks = []
for i in range(64):  # up to 4 GB in 64 MB steps
    blocks.append(bytearray(64 * 1024 * 1024))
print(json.dumps({"probe": "mem_bomb", "contained": False, "detail": {"allocated_mb": 64 * len(blocks)}}))
