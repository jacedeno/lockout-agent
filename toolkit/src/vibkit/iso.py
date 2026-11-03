"""ISO 20816-3 evaluation zones for industrial machines (broadband velocity RMS, 10–1000 Hz).

Zone A: newly commissioned. B: unrestricted long-term operation. C: restricted operation,
plan corrective action. D: severe enough to cause damage.

Group 1: large machines, 300 kW–50 MW. Group 2: medium machines, 15–300 kW.
"""

BOUNDARIES_MM_S = {  # (A/B, B/C, C/D)
    (1, "rigid"): (2.3, 4.5, 7.1),
    (1, "flexible"): (3.5, 7.1, 11.0),
    (2, "rigid"): (1.4, 2.8, 4.5),
    (2, "flexible"): (2.3, 4.5, 7.1),
}

MEANING = {
    "A": "newly commissioned condition",
    "B": "acceptable for unrestricted long-term operation",
    "C": "restricted operation only; plan corrective action",
    "D": "severe; vibration may damage the machine",
}


def group_for_kw(kw):
    if kw is None:
        return None
    return 1 if kw > 300 else 2 if kw >= 15 else None


def boundaries(group=2, foundation="rigid"):
    key = (int(group), str(foundation).lower())
    if key not in BOUNDARIES_MM_S:
        raise ValueError(f"no ISO 20816-3 boundaries for group {group}, foundation {foundation!r}")
    ab, bc, cd = BOUNDARIES_MM_S[key]
    return {"A/B": ab, "B/C": bc, "C/D": cd}


def zone(v_rms_mm_s, bounds):
    """Zone letter for a velocity RMS given {"A/B", "B/C", "C/D"} boundaries."""
    v = float(v_rms_mm_s)
    if v < bounds["A/B"]:
        return "A"
    if v < bounds["B/C"]:
        return "B"
    if v < bounds["C/D"]:
        return "C"
    return "D"
