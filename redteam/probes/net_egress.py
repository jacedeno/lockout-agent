"""Try to reach the internet by name and by raw IP."""
import json
import socket

detail = {}
try:
    socket.getaddrinfo("example.com", 443)
    detail["dns"] = "resolved"
except OSError as e:
    detail["dns"] = f"blocked: {e}"
for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
    try:
        socket.create_connection((host, port), timeout=3).close()
        detail[f"{host}:{port}"] = "connected"
    except OSError as e:
        detail[f"{host}:{port}"] = f"blocked: {e}"
contained = all(v.startswith("blocked") for v in detail.values())
print(json.dumps({"probe": "net_egress", "contained": contained, "detail": detail}))
