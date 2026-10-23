"""Try the cloud metadata endpoint, where user-data and instance identity live."""
import json
import socket

try:
    socket.create_connection(("169.254.169.254", 80), timeout=3).close()
    detail, contained = "connected", False
except OSError as e:
    detail, contained = f"blocked: {e}", True
print(json.dumps({"probe": "metadata", "contained": contained, "detail": detail}))
