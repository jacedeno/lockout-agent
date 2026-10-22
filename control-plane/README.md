# control-plane

FastAPI app on a Vultr VM: job API, live SSE timeline, web UI, `/verify` page and the
sandbox reaper. Holds the only credentials in the system (Vultr API key, Serverless
Inference key, receipt-signing key), loaded from a root-only env file at deploy time.

Built during the event window (Nov 3–8). See the plan (kept private) §3.
