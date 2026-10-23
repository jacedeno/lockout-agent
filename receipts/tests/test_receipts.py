import io
import json
import zipfile

import pytest

from arz_receipts import EventLog, generate_key, load_private_key, verify_bundle, verify_chain, write_bundle


@pytest.fixture
def key(tmp_path):
    path = tmp_path / "signing.pem"
    generate_key(str(path))
    return load_private_key(str(path))


@pytest.fixture
def bundle(tmp_path, key):
    artifacts = tmp_path / "out"
    (artifacts / "plots").mkdir(parents=True)
    (artifacts / "result.json").write_text('{"zone": "C"}')
    (artifacts / "plots" / "spectrum.png").write_bytes(b"\x89PNG fake")
    log = EventLog()
    log.append("plan", {"steps": ["fft", "iso20816"]})
    log.append("exec", {"cmd": "python3 job.py", "exit_code": 0})
    log.append("teardown", {"instance": "abc", "verified_404": True})
    path = tmp_path / "receipt.zip"
    write_bundle(str(path), log, str(artifacts), key, "job-1")
    return path


def rewrite(path, name, mutate):
    with zipfile.ZipFile(path) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    entries[name] = mutate(entries[name])
    with zipfile.ZipFile(path, "w") as z:
        for n, data in entries.items():
            z.writestr(n, data)


def failed(report):
    return [c["check"] for c in report["checks"] if not c["ok"]]


def test_valid_bundle(bundle, key):
    from arz_receipts import public_key_hex
    report = verify_bundle(str(bundle), public_key_hex(key))
    assert report["ok"], failed(report)


def test_chain_detects_edit_delete_reorder():
    log = EventLog()
    for i in range(4):
        log.append("step", {"i": i})
    assert verify_chain(log.events) == []
    edited = [dict(e) for e in log.events]
    edited[1]["data"] = {"i": 99}
    assert verify_chain(edited)
    assert verify_chain(log.events[:1] + log.events[2:])
    assert verify_chain([log.events[1], log.events[0]] + log.events[2:])


def test_tampered_artifact(bundle):
    rewrite(bundle, "artifacts/result.json", lambda b: b'{"zone": "A"}')
    assert "artifact artifacts/result.json" in failed(verify_bundle(str(bundle)))


def test_tampered_log_event(bundle):
    def mutate(b):
        lines = b.decode().splitlines()
        e = json.loads(lines[1])
        e["data"]["exit_code"] = 1
        lines[1] = json.dumps(e, sort_keys=True)
        return ("\n".join(lines) + "\n").encode()
    rewrite(bundle, "log.jsonl", mutate)
    assert {"log hash", "log chain"} <= set(failed(verify_bundle(str(bundle))))


def test_tampered_manifest(bundle):
    def mutate(b):
        r = json.loads(b)
        r["manifest"]["job_id"] = "job-2"
        return json.dumps(r).encode()
    rewrite(bundle, "receipt.json", mutate)
    assert "signature" in failed(verify_bundle(str(bundle)))


def test_resigned_by_other_key(bundle, tmp_path, key):
    from arz_receipts import public_key_hex
    other_path = tmp_path / "other.pem"
    generate_key(str(other_path))
    other = load_private_key(str(other_path))
    log = EventLog()
    log.append("plan", {"forged": True})
    forged = tmp_path / "forged.zip"
    write_bundle(str(forged), log, None, other, "job-1")
    report = verify_bundle(str(forged), public_key_hex(key))
    assert failed(report) == ["signer trusted"]


def test_unlisted_artifact(bundle):
    with zipfile.ZipFile(bundle, "a") as z:
        z.writestr("artifacts/extra.txt", "smuggled")
    assert "no unlisted artifacts" in failed(verify_bundle(str(bundle)))


def zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, data in entries.items():
            z.writestr(n, data)
    return buf.getvalue()


@pytest.mark.parametrize("source, failing_check", [
    (b"not a zip", "bundle is a zip"),
    (zip_bytes({"log.jsonl": ""}), "receipt.json readable"),
    (zip_bytes({"receipt.json": json.dumps({"manifest": {"format": "arz-receipt/1"}, "signature": ""})}),
     "manifest structure"),
])
def test_malformed_bundles_are_reported_not_raised(source, failing_check):
    report = verify_bundle(source)
    assert not report["ok"] and failed(report)[-1] == failing_check


def test_unreadable_log_is_reported(bundle):
    rewrite(bundle, "log.jsonl", lambda b: b"\xff\xfe not json\n")
    assert "log readable" in failed(verify_bundle(str(bundle)))


def test_bundle_size_cap(bundle, monkeypatch):
    import arz_receipts.bundle as b
    monkeypatch.setattr(b, "MAX_ENTRIES", 2)
    assert failed(verify_bundle(str(bundle))) == ["bundle size"]
