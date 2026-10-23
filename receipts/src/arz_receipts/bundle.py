"""Signed receipt bundles."""

import base64
import datetime
import hashlib
import io
import json
import os
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .chain import EventLog, canonical, verify_chain

FORMAT = "arz-receipt/1"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def generate_key(path):
    """Write a new Ed25519 private key (PEM, mode 0600) and return its public key hex."""
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return public_key_hex(key)


def load_private_key(path):
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def public_key_hex(private_key):
    return private_key.public_key().public_bytes(serialization.Encoding.Raw,
                                                 serialization.PublicFormat.Raw).hex()


def write_bundle(out_path, log, artifacts_root, private_key, job_id, extra=None):
    """Zip the log, every file under artifacts_root and a signed manifest into out_path."""
    artifacts = []
    files = []
    if artifacts_root and os.path.isdir(artifacts_root):
        for root, _, names in os.walk(artifacts_root):
            for name in sorted(names):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, artifacts_root).replace(os.sep, "/")
                artifacts.append({"path": f"artifacts/{rel}", "bytes": os.path.getsize(full),
                                  "sha256": sha256_file(full)})
                files.append((full, f"artifacts/{rel}"))
    log_bytes = log.to_jsonl().encode()
    pub = public_key_hex(private_key)
    manifest = {
        "format": FORMAT,
        "job_id": job_id,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "log": {"path": "log.jsonl", "sha256": sha256_bytes(log_bytes), "events": len(log.events),
                "head": log.head},
        "artifacts": sorted(artifacts, key=lambda a: a["path"]),
        "signer": {"alg": "Ed25519", "public_key": pub, "key_id": sha256_bytes(bytes.fromhex(pub))[:16]},
        **({"extra": extra} if extra else {}),
    }
    signature = base64.b64encode(private_key.sign(canonical(manifest))).decode()
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("log.jsonl", log_bytes)
        for full, arc in files:
            z.write(full, arc)
        z.writestr("receipt.json", json.dumps({"manifest": manifest, "signature": signature},
                                              indent=2, sort_keys=True))
    return manifest


def verify_bundle(source, trusted_public_key=None):
    """Check signature, signer, log chain and every artifact hash.

    source is a path or bytes of a bundle zip. Returns {"ok": bool, "checks": [...]}.
    """
    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return ok

    z = zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source)
    names = set(z.namelist())
    try:
        receipt = json.loads(z.read("receipt.json"))
        manifest, signature = receipt["manifest"], base64.b64decode(receipt["signature"])
    except (KeyError, ValueError) as e:
        check("receipt.json readable", False, str(e))
        return {"ok": False, "checks": checks}
    check("format", manifest.get("format") == FORMAT, manifest.get("format", ""))

    pub_hex = manifest.get("signer", {}).get("public_key", "")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex)).verify(signature, canonical(manifest))
        check("signature", True, f"key_id {manifest['signer'].get('key_id')}")
    except (InvalidSignature, ValueError) as e:
        check("signature", False, type(e).__name__)
    if trusted_public_key is not None:
        check("signer trusted", pub_hex == trusted_public_key.lower(),
              "matches" if pub_hex == trusted_public_key.lower() else f"signed by {pub_hex[:16]}…")

    log_bytes = z.read("log.jsonl") if "log.jsonl" in names else b""
    check("log hash", sha256_bytes(log_bytes) == manifest["log"]["sha256"])
    log = EventLog.from_jsonl(log_bytes.decode())
    problems = verify_chain(log.events)
    check("log chain", not problems, "; ".join(problems))
    check("log head", log.head == manifest["log"]["head"] and len(log.events) == manifest["log"]["events"])

    listed = {a["path"] for a in manifest["artifacts"]}
    for a in manifest["artifacts"]:
        ok = a["path"] in names and sha256_bytes(z.read(a["path"])) == a["sha256"]
        check(f"artifact {a['path']}", ok, a["sha256"][:16] if ok else "missing or modified")
    unlisted = sorted(n for n in names if n.startswith("artifacts/") and n not in listed)
    check("no unlisted artifacts", not unlisted, ", ".join(unlisted))
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
