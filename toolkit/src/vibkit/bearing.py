"""Rolling-element bearing defect frequencies and envelope-spectrum indicators."""

import math

import numpy as np

from .spectral import envelope_spectrum

FAULT_FOR = {"BPFO": "bearing_outer_race", "BPFI": "bearing_inner_race", "BSF": "bearing_ball",
             "2xBSF": "bearing_ball", "FTF": "bearing_cage"}

# Detection rule, judged per defect frequency on the envelope spectrum: the fundamental stands
# SNR_FUNDAMENTAL_DB above the local floor and at least one harmonic stands SNR_HARMONIC_DB above.
SNR_FUNDAMENTAL_DB = 10.0
SNR_HARMONIC_DB = 6.0


def defect_frequencies(rpm, balls, ball_diameter, pitch_diameter, contact_angle_deg=0.0):
    """BPFO, BPFI, BSF and FTF in Hz from bearing geometry (diameters in the same unit)."""
    fr = rpm / 60.0
    r = ball_diameter / pitch_diameter * math.cos(math.radians(contact_angle_deg))
    return {"BPFO": balls / 2 * fr * (1 - r), "BPFI": balls / 2 * fr * (1 + r),
            "BSF": pitch_diameter / (2 * ball_diameter) * fr * (1 - r * r), "FTF": fr / 2 * (1 - r)}


def indicators(accel_g, fs, defects_hz, band=None, harmonics=3, skip=("FTF",)):
    """Envelope-spectrum evidence for each defect frequency.

    Returns {name: {"fault", "detected", "harmonics": [{harmonic, freq_hz, amp_g, snr_db}]}}
    plus "_band_hz". FTF (cage) is reported but never used for detection by default: it sits
    in the low-frequency end of the envelope, where it is easily confused with modulation.
    """
    f, amp, _, band = envelope_spectrum(accel_g, fs, band)
    df = f[1] - f[0]
    out = {"_band_hz": [round(band[0]), round(band[1])]}
    for name, f0 in defects_hz.items():
        rows = []
        for h in range(1, harmonics + 1):
            fh = h * f0
            if fh > 0.9 * f[-1]:
                break
            tol = max(0.01 * fh, 2 * df)
            near = np.flatnonzero(np.abs(f - fh) <= tol)
            k = int(near[np.argmax(amp[near])])
            ring = (np.abs(f - fh) <= max(10.0, 0.1 * fh)) & (np.abs(f - fh) > 2 * tol)
            floor = float(np.median(amp[ring])) if ring.any() else float("nan")
            snr = 20 * math.log10(amp[k] / floor) if floor > 0 else float("nan")
            rows.append({"harmonic": h, "freq_hz": round(float(f[k]), 2), "amp_g": round(float(amp[k]), 5),
                         "snr_db": round(snr, 1)})
        detected = (name not in skip and bool(rows) and rows[0]["snr_db"] >= SNR_FUNDAMENTAL_DB
                    and any(r["snr_db"] >= SNR_HARMONIC_DB for r in rows[1:]))
        out[name] = {"fault": FAULT_FOR.get(name, f"bearing_{name.lower()}"), "defect_hz": round(f0, 2),
                     "detected": detected, "harmonics": rows}
    return out
