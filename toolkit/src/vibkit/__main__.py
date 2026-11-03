"""python -m vibkit <data.csv> <metadata.json> [--fs HZ]  →  diagnosis as JSON on stdout."""

import argparse
import json
import sys

from .diagnose import analyze


def main(argv=None):
    p = argparse.ArgumentParser(prog="vibkit", description=__doc__)
    p.add_argument("csv")
    p.add_argument("metadata")
    p.add_argument("--fs", type=float, default=None)
    args = p.parse_args(argv)
    json.dump(analyze(args.csv, args.metadata, args.fs), sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
