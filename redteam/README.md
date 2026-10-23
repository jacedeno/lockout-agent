# redteam

Deterministic escape probes, each run as an ordinary job step through `sandbox/runner/arz-run`
under the same limits as real jobs. `run.py` reports expected vs. observed per probe.

| Probe | Contained when |
|---|---|
| `net_egress` | no DNS, no TCP to the internet |
| `metadata` | 169.254.169.254 unreachable |
| `secrets` | no credential-like env vars, no readable secret paths |
| `fs_write` | every write outside `/work/out` and `/tmp` refused |
| `privilege` | uid 65534, zero capabilities, `setuid(0)` refused |
| `fork_bomb` | stopped by the pids limit |
| `mem_bomb` | OOM-killed at the memory cap |
| `timeout` | killed at the wall-clock limit |
| `host_egress`, `host_metadata` (`--host-probes`) | the sandbox VM itself cannot reach the internet or metadata |

```bash
redteam/run.py --engine podman --timeout 10        # local; rootless Podman here cannot enforce memory/pids
/opt/arz/redteam/run.py --host-probes [--runtime runsc]   # on the sandbox VM
```
