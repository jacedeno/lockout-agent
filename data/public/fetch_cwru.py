# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1", "scipy>=1.14"]
# ///
"""Download a few Case Western Reserve University bearing files and convert them to CSV.

The CWRU Bearing Data Center publishes these files for download but states no license, so the
raw files are never committed: this script fetches them, checks their SHA-256 and writes
CSV + JSON next to them under data/raw/cwru/ (git-ignored).

    uv run data/public/fetch_cwru.py

Test rig bearing at the drive end: SKF 6205-2RS, the same geometry as the synthetic datasets.
Source: https://engineering.case.edu/bearingdatacenter
"""

import hashlib
import json
import os
import urllib.request

import numpy as np
import scipy.io

BASE = "https://engineering.case.edu/sites/default/files"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "raw", "cwru")

# file id: (label, fault, fs_hz, sha256). Fault files come from the "12k Drive End Bearing
# Fault Data" set at 0 hp load; fs for the normal baseline must be confirmed from CWRU's docs.
FILES = {
    "97": ("normal_0hp", "none", None,
            "16bf48babcf1c7ac224bc1a81cd9eafdb27e42d5cf559761907e067e8eeadf3c"),
    "105": ("inner_race_007_0hp", "bearing_inner_race", 12000,
            "f80b0ea04fd06b372a0eaec7c056543ea37e4bb4727a5b173d2a5bacd2aa9cab"),
    "118": ("ball_007_0hp", "bearing_ball", 12000,
            "b00628f8dd8d1d930af77fa465d1e5cdb385fe259489053f91f3680bda7f640e"),
    "130": ("outer_race_007_6oclock_0hp", "bearing_outer_race", 12000,
            "35a095307d0971477049b343a1b5981dde465a58fb7f233ad89b035068c1717d"),
}


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    os.makedirs(OUT, exist_ok=True)
    for fid, (label, fault, fs, expected) in FILES.items():
        mat = os.path.join(OUT, f"{fid}.mat")
        if not os.path.exists(mat):
            urllib.request.urlretrieve(f"{BASE}/{fid}.mat", mat)
        digest = sha256(mat)
        if expected and digest != expected:
            raise SystemExit(f"{fid}.mat: SHA-256 mismatch ({digest})")
        data = scipy.io.loadmat(mat)
        de = next(v for k, v in data.items() if k.endswith("_DE_time")).ravel()
        fe = next((v for k, v in data.items() if k.endswith("_FE_time")), None)
        rpm = next((float(v.ravel()[0]) for k, v in data.items() if k.endswith("RPM")), None)
        cols, header = [de], "accel_de_g"
        if fe is not None:
            cols.append(fe.ravel()[: len(de)])
            header += ",accel_fe_g"
        csv = os.path.join(OUT, f"cwru_{fid}_{label}.csv")
        np.savetxt(csv, np.column_stack(cols), delimiter=",", fmt="%.6f", header=header, comments="")
        meta = {"source": f"{BASE}/{fid}.mat", "source_sha256": digest, "label": label, "fault": fault,
                "fs_hz": fs, "fs_note": None if fs else "confirm from the CWRU normal baseline page",
                "rpm": rpm, "samples": int(len(de)), "units": "g",
                "bearing": {"model": "6205-2RS", "location": "drive end"}}
        with open(csv[:-4] + ".json", "w") as f:
            json.dump(meta, f, indent=2)
        print(f"{fid}: {label:28s} rpm={rpm} samples={len(de)} sha256={digest[:16]}…")


if __name__ == "__main__":
    main()
