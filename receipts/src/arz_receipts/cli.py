"""arz-receipt: keygen and verify."""

import argparse
import json
import sys

from .bundle import generate_key, verify_bundle


def main(argv=None):
    p = argparse.ArgumentParser(prog="arz-receipt")
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen", help="write a new Ed25519 signing key")
    k.add_argument("path")
    v = sub.add_parser("verify", help="verify a receipt bundle")
    v.add_argument("bundle")
    v.add_argument("--pubkey", help="expected signer public key (hex)")
    v.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = p.parse_args(argv)

    if args.cmd == "keygen":
        print(generate_key(args.path))
        return 0
    report = verify_bundle(args.bundle, args.pubkey)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for c in report["checks"]:
            print(f"{'PASS' if c['ok'] else 'FAIL'}  {c['check']}  {c['detail']}")
        print("VALID" if report["ok"] else "INVALID")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
