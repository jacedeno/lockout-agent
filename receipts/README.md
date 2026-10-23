# receipts (`arz-receipts`)

Hash-chained JSONL event log, artifact hashing, Ed25519-signed manifest and an offline
verifier. Pre-event tooling.

A bundle is a zip with `log.jsonl`, `artifacts/…` and `receipt.json`
(`{"manifest": …, "signature": …}`). The manifest pins the log's SHA-256, event count and
head hash, every artifact's SHA-256 and the signer's public key. Each event's hash is
`sha256(prev_hash ‖ canonical_json(event))`, so editing, dropping or reordering an event
breaks the chain.

```bash
uv sync
uv run pytest -q                                   # tamper, forgery, smuggling and malformed-input tests
uv run arz-receipt keygen signing.pem              # prints the public key (hex)
uv run arz-receipt verify receipt.zip --pubkey <hex>
```

The signing key lives only on the control plane; the sandbox never sees it.

`verify_bundle` treats its input as untrusted (the `/verify` page accepts uploads): a file
that is not a zip, a missing or malformed manifest, an unreadable log, or a bundle over
1,000 entries or 64 MB uncompressed is reported as a failed check, never raised.
