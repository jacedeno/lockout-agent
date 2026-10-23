"""Verifiable receipts for sandboxed jobs.

A receipt bundle is a zip holding:
  log.jsonl       hash-chained events (each carries the previous event's hash)
  artifacts/...   every file the job produced
  receipt.json    {"manifest": {...}, "signature": base64}; the manifest pins the log head,
                  the event count and every artifact's SHA-256, and is signed with Ed25519
"""

from .chain import GENESIS, EventLog, canonical, verify_chain
from .bundle import generate_key, load_private_key, public_key_hex, sha256_file, verify_bundle, write_bundle

__all__ = ["GENESIS", "EventLog", "canonical", "verify_chain", "generate_key", "load_private_key",
           "public_key_hex", "sha256_file", "verify_bundle", "write_bundle"]
