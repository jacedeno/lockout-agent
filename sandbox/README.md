# sandbox

- `image/build.sh` turns a fresh Ubuntu 24.04 Vultr instance into the sandbox snapshot: Docker
  with no bridge and no iptables, gVisor (`runsc`) as an optional runtime, the `arz-runner`
  analysis image, SSH keys only, and an nftables default-drop policy loaded at early boot.
  Inbound, only SSH from the control plane's VPC address (`CONTROL_PLANE_IP`, baked in:
  **re-bake if that address changes**); outbound, nothing new at all, not even to the VPC.
  The boot policy lets cloud-init reach the metadata endpoint; `arz-lockdown.service` removes
  that once cloud-init finishes. The script ends by removing the build SSH key and resetting
  cloud-init.
- `image/runner/Dockerfile`: the analysis runtime (numpy, scipy, pandas, matplotlib). Nothing
  is installed at job time.
- `runner/arz-run`: runs one job step in a locked container (`--network none`, read-only root,
  memory/pids/CPU caps, no capabilities, uid 65534, wall-clock timeout) and prints a JSON
  summary with flags, image ID, timings, exit/OOM/timeout status and SHA-256 of all outputs.
  `out/` is container-controlled, so only regular files are listed (lstat, `O_NOFOLLOW`;
  links, FIFOs, sockets and devices are skipped and reported), at most 64 artifacts / 50 MB,
  and the first 1 MiB of stdout and stderr each; Docker's own log copy is capped at 1 MB.
  Every cut is recorded in the summary (`truncated`, `artifacts_skipped`).

Baked and boot-tested with `spikes/vultr/bake.py` (the boot test reaches the sandbox through
the real control plane). The sandbox holds no credentials.
