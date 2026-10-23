# Vultr spike findings — 2026-10-22

Account: a Vultr organization account. Region `dfw`, plan `vc2-1c-1gb`, Ubuntu 24.04 (`os_id` 2284).
Total cost of all runs so far: $0.13. Every resource was deleted and verified gone afterwards.

Run with the key from `$ARZ_SECRETS_DIR/vultr.env` loaded into the environment:

```bash
python3 -I spikes/vultr/spike.py lifecycle
python3 -I spikes/vultr/spike.py vpc-only --allow <wan-ip-1> <wan-ip-2> [--mode no-public-ip]
python3 -I spikes/vultr/spike.py cleanup
```

## Results

| Question | Answer |
|---|---|
| Create → `active` | 58–90 s |
| `active` → SSH ready (cloud-init done) | ~41 s |
| `DELETE` → `GET` returns 404 | < 1 s |
| VPC deletion after its instances are gone | succeeds after ~10–120 s of retries |
| VPC-only instance (`disable_public_ipv4: true`) | **Refused**: "The selected VPC does not have a NAT gateway. Deploy a NAT gateway on this VPC before creating a VPC-only instance." |
| cloud-init user-data on Vultr | Works (`DataSourceVultr`); `runcmd` and `write_files` ran |
| VPC NIC configured automatically | Yes, `enp8s0` with the VPC address, no extra config |
| Does a firewall group filter VPC traffic? | **No.** An empty firewall group (drop all inbound) still allowed SSH over the VPC |
| Empty firewall group blocks public inbound | Yes, SSH to the sandbox's public IP from the internet failed |
| nftables default-drop (in + out) applied by cloud-init | Works: DNS, HTTPS, raw IP egress and the metadata endpoint all blocked; SSH from the VPC still allowed |
| Sandbox create → `active` when created right after another | 0 s extra (provisioned in parallel) |

## Consequences for the design

1. **"No public IP" is not available without a NAT gateway**, and a NAT gateway is an egress path,
   the opposite of what the sandbox needs. Primary design is now **locked-public**: public IP,
   empty firewall group, nftables default-drop, container `--network none` inside.
2. **The firewall group protects only the public interface.** The VPC side is protected by the
   sandbox's own nftables input chain (SSH only from the control plane).
3. **Egress is open until nftables loads.** In this spike the rules came last in cloud-init (after
   an `apt install`). In the real snapshot, `nftables.service` is enabled with the policy baked
   in, so the drop policy is active from early boot and nothing is installed at job time.
4. **Metadata is reachable before the lockdown** (cloud-init needs it). User-data must never
   carry a secret; it only carries the per-job public SSH key.
5. Per-job latency budget: ~2–2.5 min to a ready sandbox from a stock image. A snapshot boot
   still needs measuring.

## Snapshot bake and boot test — 2026-10-22/23

`spikes/vultr/bake.py build` then `boot-test --snapshot <id>`. Current snapshot:
`a48eff26-96f8-4df1-84d0-47e78959aa94` (2.3 GB compressed; the first bake was deleted).

| Measure | Result |
|---|---|
| `build.sh` on a fresh instance | 3.5 min |
| Snapshot creation | 11–21 min |
| Snapshot boot: create → `active` | **186–196 s** (stock image: 37–90 s) |
| `active` → SSH over the VPC | ~41–49 s |
| **Create → ready sandbox** | **~4 min** |
| `arz-lockdown` after cloud-init | runs ~1 s after `cloud-final`; metadata rule gone |

Escape probes on the booted sandbox (`redteam/run.py --host-probes`, 10 s timeout):

| Probe | Docker (runc) | gVisor (runsc) |
|---|---|---|
| net_egress, metadata, secrets, fs_write, privilege | contained | contained |
| fork_bomb | contained (stopped at 127 processes) | **probe crashed after its first fork (exit 2, no output)** |
| mem_bomb | OOM-killed | OOM-killed |
| timeout | killed at 10 s | killed at 10 s |
| host_egress, host_metadata (VM layer) | contained | contained |

**10/10 contained under Docker (runc).**

### Bugs found and fixed

- `arz-lockdown.service` first shipped with `WantedBy=multi-user.target`. Ubuntu's
  `cloud-final.service` is ordered *after* `multi-user.target`, so that was an ordering cycle and
  systemd silently dropped the unit: the metadata endpoint stayed reachable from the VM. Now
  `WantedBy=cloud-init.target`; verified.
- The secrets probe flagged the base image's `GPG_KEY` (a public key ID) and the empty `/home`;
  the image now blanks `GPG_KEY` and the probe ignores empty directories.

### Consequences for the design

1. **Runtime: Docker (runc) is primary.** gVisor breaks `fork()` in this setup, so any job that
   forks (including Python `multiprocessing`) would fail under it. It stays optional and
   unproven.
2. **Four minutes from create to ready is too slow for a live demo per job.** Options:
   a **warm pool of one** pre-booted sandbox, destroyed after its job and replaced in the
   background (user-perceived wait ~0, cost ~$0.007/h), or stock image + no snapshot (faster
   boot, but packages would need egress at job time — rejected).
3. Snapshot storage is a small ongoing cost; keep one current snapshot and delete old ones.

## Not yet tested

- Why `fork()` fails under runsc here.
- Vultr account limits (instance count) for a warm pool plus concurrent jobs.
- NAT gateway pricing and its firewall model (rules are tied to port forwards, so it does not
  look like an egress allowlist).
