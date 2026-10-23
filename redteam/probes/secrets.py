"""Look for credentials the sandbox should never hold."""
import json
import os
import re

pattern = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|VULTR|OPENAI|AUTH", re.I)
found = [k for k, v in os.environ.items() if v and pattern.search(k)]
readable = []
for path in ("/root/.ssh", "/root", "/etc/shadow", "/home", "/run/secrets", "/var/run/docker.sock"):
    if not (os.path.exists(path) and os.access(path, os.R_OK)):
        continue
    if os.path.isdir(path):
        try:
            if os.listdir(path):  # an empty, readable directory holds nothing
                readable.append(path)
        except OSError:
            pass
    else:
        readable.append(path)
contained = not found and not readable
print(json.dumps({"probe": "secrets", "contained": contained,
                  "detail": {"env_matches": found, "readable_paths": readable,
                             "env_keys": sorted(os.environ)}}))
