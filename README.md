# vultr-agent-rush

Development repo for **Lockout**, the lablab.ai **Vultr: Agent Rush** hackathon entry
(Nov 3–8, 2026), Challenge 1 — Blast Radius Zero. Live at https://agentrush.geekendzone.net.

The name is the maintenance practice: before working on a machine you isolate every energy
source and **verify zero energy**. Lockout does the same with an AI agent: it isolates a
throwaway VM, does the work inside it, then destroys it and verifies the 404.

**What it is:** a sandboxed reliability-diagnostics agent. Give it a vibration dataset; it
plans the job, writes the analysis code, runs it inside a throwaway Vultr VM that has no
route to the internet, and returns a diagnosis, a draft work order and signed **receipts**
proving exactly what ran — then destroys the sandbox and proves that too.

```
Browser → control plane (Vultr VM) ⇄ Vultr Serverless Inference
                 │  Vultr API + SSH over private VPC
                 ▼
          ephemeral sandbox VM (no public IP, default-drop egress, no credentials)
            └─ locked container: no network, read-only, memory/pids/time limits
```

Full design, risks and schedule: the plan (kept private). Pre-event and kickoff tasks:
the checklist (kept private).

## Layout

| Path | Purpose |
|---|---|
| `control-plane/` | Web app, agent loop, Vultr API client, reaper |
| `sandbox/` | Sandbox snapshot build: container runtime, analysis image, egress policy |
| `toolkit/` | `vibkit` vibration analysis library (FFT, ISO 20816, fault signatures) |
| `receipts/` | Hash-chained log, artifact hashing, Ed25519 signing and verification |
| `redteam/` | Deterministic sandbox escape probes |
| `data/` | Synthetic generator with ground truth, public dataset download scripts |
| `spikes/` | Throwaway pre-event experiments |
| `docs/` | Architecture, receipt format, deploy runbook |

## Pre-event work (disclosure)

The event page encourages a head start before kickoff and sets no rule against pre-existing
code. To keep it transparent, work done before **Nov 3, 2026 15:00 UTC** is limited to
planning, account setup, datasets, infrastructure scripts and generic tooling spikes
(Vultr API client, receipts library, sandbox image builder, escape-probe suite). The agent
loop, web app, analysis toolkit and work-order generation are built during the event
window. Commit history shows which is which.

## Secrets

No credential is ever committed, baked into the sandbox image, passed in cloud-init
user-data or present inside a sandbox. Keys are kept in a private secrets store and
deployed to the control plane as a root-only env file.

## License

[MIT](LICENSE)
