# sandbox

- `image/build.sh` turns a fresh Ubuntu 24.04 Vultr instance into the sandbox snapshot: Docker
  with no bridge and no iptables, gVisor (`runsc`) as an optional runtime, the `arz-runner`
  analysis image, and an nftables default-drop policy loaded at early boot. The boot policy
  lets cloud-init reach the metadata endpoint; `arz-lockdown.service` removes that once
  cloud-init finishes. The script ends by removing the build SSH key and resetting cloud-init.
- `image/runner/Dockerfile`: the analysis runtime (numpy, scipy, pandas, matplotlib). Nothing
  is installed at job time.
- `runner/arz-run`: runs one job step in a locked container (`--network none`, read-only root,
  memory/pids/CPU caps, no capabilities, uid 65534, wall-clock timeout) and prints a JSON
  summary with flags, image ID, timings, exit/OOM/timeout status and SHA-256 of all outputs.

Baked and boot-tested with `spikes/vultr/bake.py`. The sandbox holds no credentials.
