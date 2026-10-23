"""Hash-chained event log."""

import datetime
import hashlib
import json

GENESIS = "0" * 64


def canonical(obj):
    """Deterministic JSON bytes: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def event_hash(event):
    body = {k: v for k, v in event.items() if k != "hash"}
    return hashlib.sha256(event["prev"].encode() + canonical(body)).hexdigest()


class EventLog:
    def __init__(self, events=None):
        self.events = list(events or [])

    @property
    def head(self):
        return self.events[-1]["hash"] if self.events else GENESIS

    def append(self, kind, data, ts=None):
        event = {
            "seq": len(self.events),
            "ts": ts or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
            "kind": kind,
            "data": data,
            "prev": self.head,
        }
        event["hash"] = event_hash(event)
        self.events.append(event)
        return event

    def to_jsonl(self):
        return "".join(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n" for e in self.events)

    @classmethod
    def from_jsonl(cls, text):
        return cls(json.loads(line) for line in text.splitlines() if line.strip())


def verify_chain(events):
    """Return a list of problems; empty means the chain is intact."""
    problems, prev = [], GENESIS
    for i, e in enumerate(events):
        if e.get("seq") != i:
            problems.append(f"event {i}: seq is {e.get('seq')}")
        if e.get("prev") != prev:
            problems.append(f"event {i}: prev does not match the previous event's hash")
        if e.get("hash") != event_hash(e):
            problems.append(f"event {i}: hash does not match its content")
        prev = e.get("hash", "")
    return problems
