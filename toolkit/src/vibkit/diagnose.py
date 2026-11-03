"""Deterministic diagnosis: levels, ISO zone and fault signatures, with the evidence for each.

The rules are deliberately simple and explainable; every finding carries the numbers that
triggered it. Free-text metadata (notes, asset descriptions) is never read.

Signatures, judged on the worst radial axis unless stated:
- unbalance: 1× carries ≥ 60 % of the band energy, 2× < 0.5·1×, axial 1× < 0.5·radial 1×,
  and the level is above the A/B boundary (residual unbalance in a healthy machine is normal).
- misalignment: 2× ≥ 0.75·1× radially, or axial 1× ≥ 0.5·radial 1×, with the level above A/B.
- bearing defect: envelope-spectrum lines at a defect frequency and a harmonic (see bearing.py),
  on any radial axis. Independent of the overall level: early bearing damage barely moves it.
  Indications more than 15 dB below the strongest go to `weaker_indications`; if the two
  strongest defect types are within 3 dB, the finding is `bearing_defect` with no type.
"""

from . import bearing, iso
from .spectral import order_amplitudes, velocity_rms

PRIORITY = {"D": "immediate", "C": "planned (weeks)", "B": "monitor", "A": "none"}
RANK = {"bearing_outer_race": 0, "bearing_inner_race": 0, "bearing_ball": 0, "bearing_cage": 0,
        "bearing_defect": 0, "misalignment": 1, "unbalance": 2}
SECONDARY_DB = 15.0  # bearing indications this far below the strongest are reported as weaker only
AMBIGUOUS_DB = 3.0   # two bearing types this close: report a bearing defect without a type


def _rpm(meta):
    rpm = (meta.get("machine") or {}).get("rpm", meta.get("rpm"))
    if not rpm:
        raise ValueError("metadata has no rpm (machine.rpm or rpm)")
    return float(rpm)


def _roles(signals, meta):
    sensor_axes = (meta.get("sensor") or {}).get("axes") or {}
    channels = meta.get("channels") or {}
    roles = {}
    for a in signals:
        desc = str(sensor_axes.get(a) or channels.get(f"accel_{a}_g") or "")
        roles[a] = "axial" if "axial" in desc.lower() else "radial"
    return roles


def _iso_bounds(meta, assumptions):
    spec = (meta.get("machine") or {}).get("iso_20816_3") or {}
    if spec.get("boundaries_mm_s"):
        return spec.get("group"), spec.get("foundation"), spec["boundaries_mm_s"]
    group = spec.get("group") or iso.group_for_kw((meta.get("machine") or {}).get("rated_kw"))
    foundation = spec.get("foundation")
    if group is None:
        group = 2
        assumptions.append("machine size unknown: ISO 20816-3 group 2 (15–300 kW) assumed")
    if foundation is None:
        foundation = "rigid"
        assumptions.append("foundation unknown: rigid assumed")
    return group, foundation, iso.boundaries(group, foundation)


def _defects(meta, rpm, assumptions):
    b = meta.get("bearing") or {}
    if b.get("defect_frequencies_hz"):
        return dict(b["defect_frequencies_hz"])
    if b.get("defect_orders"):
        return {k: v * rpm / 60.0 for k, v in b["defect_orders"].items()}
    if all(k in b for k in ("balls", "ball_diameter_mm", "pitch_diameter_mm")):
        return bearing.defect_frequencies(rpm, b["balls"], b["ball_diameter_mm"], b["pitch_diameter_mm"],
                                          b.get("contact_angle_deg", 0.0))
    assumptions.append("no bearing geometry or defect frequencies: bearing analysis skipped")
    return {}


def diagnose(signals, fs, meta):
    """Full analysis of one measurement. `signals` is {axis: acceleration in g}."""
    assumptions = []
    rpm = _rpm(meta)
    roles = _roles(signals, meta)
    group, foundation, bounds = _iso_bounds(meta, assumptions)
    defects = _defects(meta, rpm, assumptions)

    axes = {}
    for a, x in signals.items():
        orders = order_amplitudes(x, fs, rpm)
        v = velocity_rms(x, fs)
        axes[a] = {"role": roles[a], "velocity_rms_mm_s": round(v, 3), "orders": orders,
                   "dominant_order": max(orders, key=lambda k: orders[k]["mm_s"])}

    radial = [a for a in axes if axes[a]["role"] == "radial"] or list(axes)
    axial = [a for a in axes if axes[a]["role"] == "axial"]
    worst = max(radial, key=lambda a: axes[a]["velocity_rms_mm_s"])
    level = axes[worst]["velocity_rms_mm_s"]
    zone = iso.zone(level, bounds)
    o = axes[worst]["orders"]
    r1, r2 = o["1x"]["mm_s"], o["2x"]["mm_s"]
    ax1 = max((axes[a]["orders"]["1x"]["mm_s"] for a in axial), default=None)
    above_ab = level >= bounds["A/B"]

    findings = []
    energy_1x = (r1 / level) ** 2 if level > 0 else 0.0
    if above_ab and energy_1x >= 0.6 and r2 < 0.5 * r1 and (ax1 is None or ax1 < 0.5 * r1):
        findings.append({"fault": "unbalance", "axis": worst,
                         "evidence": f"1× {r1:.2f} mm/s carries {energy_1x:.0%} of the {level:.2f} mm/s band level; "
                                     f"2× {r2:.2f} mm/s" + (f"; axial 1× {ax1:.2f} mm/s" if ax1 is not None else "")})
    mis_2x = r1 > 0 and r2 >= 0.75 * r1
    mis_ax = ax1 is not None and r1 > 0 and ax1 >= 0.5 * r1
    if above_ab and (mis_2x or mis_ax):
        why = []
        if mis_2x:
            why.append(f"radial 2× {r2:.2f} mm/s ≥ 0.75 × 1× {r1:.2f} mm/s")
        if mis_ax:
            why.append(f"axial 1× {ax1:.2f} mm/s ≥ 0.5 × radial 1× {r1:.2f} mm/s")
        findings.append({"fault": "misalignment", "axis": worst, "evidence": "; ".join(why)})

    bearing_ev, weaker = {}, []
    if defects:
        for a in radial:
            try:
                bearing_ev[a] = bearing.indicators(signals[a], fs, defects)
            except ValueError as e:  # fs too low for an envelope band
                assumptions.append(f"axis {a}: bearing analysis skipped ({e})")
                break
        hits = {}
        for a, ev in bearing_ev.items():
            for name, d in ev.items():
                if name != "_band_hz" and d["detected"]:
                    best = hits.get(d["fault"])
                    snr = d["harmonics"][0]["snr_db"]
                    if best is None or snr > best[2]:
                        hits[d["fault"]] = (a, name, snr, d)
        # Defect lines share harmonics (3×BPFO ≈ 2×BPFI on a 6205), so a real defect lights up
        # its neighbours too. Keep only indications within SECONDARY_DB of the strongest; when
        # the two strongest types are within AMBIGUOUS_DB of each other, do not pick one.
        ranked = sorted(hits.items(), key=lambda kv: -kv[1][2])
        top_snr = ranked[0][1][2] if ranked else None
        weaker = []
        for fault, (a, name, snr, d) in ranked:
            hs = ", ".join(f"{h['harmonic']}× {h['snr_db']:.0f} dB" for h in d["harmonics"])
            ev_text = (f"envelope lines at {name} {d['defect_hz']:.1f} Hz ({hs}) "
                       f"in the {bearing_ev[a]['_band_hz'][0]}–{bearing_ev[a]['_band_hz'][1]} Hz band")
            if top_snr - snr > SECONDARY_DB:
                weaker.append({"fault": fault, "axis": a, "snr_db": snr, "evidence": ev_text})
                continue
            findings.append({"fault": fault, "axis": a, "snr_db": snr, "evidence": ev_text})
        bearing_found = [f for f in findings if f["fault"].startswith("bearing_")]
        if len(bearing_found) >= 2 and bearing_found[0]["snr_db"] - bearing_found[1]["snr_db"] <= AMBIGUOUS_DB:
            types = " / ".join(f["fault"].removeprefix("bearing_") for f in bearing_found)
            findings = [f for f in findings if not f["fault"].startswith("bearing_")]
            findings.insert(0, {"fault": "bearing_defect", "axis": bearing_found[0]["axis"],
                                "snr_db": bearing_found[0]["snr_db"],
                                "evidence": f"bearing defect lines, type ambiguous ({types} within "
                                            f"{AMBIGUOUS_DB:.0f} dB): " + " | ".join(f["evidence"] for f in bearing_found)})

    findings.sort(key=lambda x: (RANK.get(x["fault"], 9), -x.get("snr_db", 0)))
    primary = findings[0]["fault"] if findings else "none"
    priority = PRIORITY[zone]
    if primary.startswith("bearing_") and zone in ("A", "B"):
        priority = "planned (early bearing defect: overall level still low)"

    return {
        "rpm": rpm, "fs_hz": fs, "running_speed_hz": round(rpm / 60.0, 3),
        "axes": axes,
        "iso_20816_3": {"group": group, "foundation": foundation, "boundaries_mm_s": bounds,
                        "worst_radial_axis": worst, "worst_radial_mm_s": level, "zone": zone,
                        "meaning": iso.MEANING[zone]},
        "bearing": bearing_ev,
        "findings": findings,
        "weaker_indications": weaker,
        "primary_fault": primary,
        "priority": priority,
        "assumptions": assumptions,
    }


def analyze(csv_path, meta_path, fs=None):
    """Load a measurement (CSV + JSON metadata) and diagnose it."""
    from .io import load_csv, load_metadata
    meta = load_metadata(meta_path)
    fs = fs or (meta.get("sensor") or {}).get("fs_hz") or meta.get("fs_hz")
    signals, fs = load_csv(csv_path, fs)
    return diagnose(signals, fs, meta)
