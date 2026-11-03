# control-plane

FastAPI app on a Vultr VM: job API, live SSE timeline, web UI, `/verify` page and the
sandbox reaper. Holds the only credentials in the system (Vultr API key, Serverless
Inference key, receipt-signing key), loaded from a root-only env file at deploy time.

Built during the event window (Nov 3–8). See the plan (kept private) §3.

## Deploy (`deploy/`)

```bash
python3 -I -B control-plane/deploy/deploy.py up --allow 203.0.113.10/32 203.0.113.11/32
python3 -I -B control-plane/deploy/deploy.py status
python3 -I -B control-plane/deploy/deploy.py destroy          # keeps the VPC; --vpc drops it too
```

`up` is idempotent: it reuses whatever already exists and re-runs `provision.sh`, so it is also
how a config change ships. Credentials are read from `the secrets directory` (`vultr.env`,
`cloudflare.env`); resource IDs and the VM's SSH key are written back there
(`vultr-arz-control-plane.env`, `vultr-arz-control-plane.key`).

| Piece | Value |
|---|---|
| Instance | `arz-control-plane`, `vc2-1c-2gb`, Ubuntu 24.04, `dfw`, no IPv6, tag `arz-control-plane` |
| VPC | `arz-vpc`, `10.50.0.0/24`, shared with the sandboxes |
| Firewall group | `arz-control-plane`: 80/443 from anywhere, 22 from the operator's addresses |
| Host firewall | nftables (ufw disabled): drops every new connection from the VPC, so a sandbox can never open one to the control plane |
| URL | `https://agentrush.geekendzone.net` — Cloudflare A record, **DNS-only**; Caddy gets its own Let's Encrypt certificate |
| SSH | keys only (`sshd_config.d/00-arz.conf`) |
| Secrets on the VM | `/etc/arz/control-plane.env` (root, 0600): `VULTR_API_KEY` |

### Sandbox reaper

`arz-reaper.timer` runs `/opt/arz/arz-reaper.py --max-age 600` every 2 minutes as a
`DynamicUser`. It deletes instances that carry the `arz-sandbox` tag **and** a label starting
with `arz-sandbox`, then polls until the API returns 404. Anything else is skipped and logged.
A failed delete or API error fails the unit.

```bash
ssh -i $ARZ_SECRETS_DIR/vultr-arz-control-plane.key root@<ip> \
  'journalctl -u arz-reaper -n 20 -o cat; /usr/bin/python3 -I /opt/arz/arz-reaper.py --dry-run --max-age 0'
```

The Vultr API key has an IP allowlist (Access Control), so the control plane's public IP must
be on it or every reaper run fails with `Unauthorized IP address`.
