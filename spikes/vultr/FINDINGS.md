# Vultr spike findings — 2026-10-22

Account: a Vultr organization account. Region `dfw`, plan `vc2-1c-1gb`, Ubuntu 24.04 (`os_id` 2284).
Total cost of all three runs: $0.03. Every resource was deleted and verified gone afterwards.

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

## Not yet tested

- Boot time from a custom snapshot.
- Docker + container lockdown flags inside the sandbox (test locally with Podman first).
- NAT gateway pricing and its firewall model (rules are tied to port forwards, so it does not
  look like an egress allowlist).
