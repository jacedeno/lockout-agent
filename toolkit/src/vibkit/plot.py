"""Diagnostic figure: the velocity spectrum an analyst would look at, with the evidence marked.

    import vibkit, vibkit.plot
    d = vibkit.analyze(csv, meta)
    signals, fs = vibkit.load_csv(csv)
    vibkit.plot.diagnostic_figure(signals, fs, d, "/work/out/spectrum.png", title="P-101A drive end")

Top: velocity spectrum (mm/s RMS) of the worst radial axis to 1 kHz, running-speed orders and
any detected bearing defect lines marked, the overall level and its ISO 20816-3 zone in the
corner. Bottom: the envelope spectrum with the bearing defect frequencies, where early bearing
damage shows long before the overall level moves. Matplotlib (Agg) only; no network, no fonts
to fetch.
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .spectral import envelope_spectrum, velocity_power_spectrum  # noqa: E402

INK, INK_2, INK_3, RULE = "#1d1c19", "#4a4843", "#8a877f", "#e2ded6"
PAPER, FILL = "#fbfaf7", "#e9e4d8"
AMBER = "#b86e00"
ZONE = {"A": "#2c8a54", "B": "#7d8f1c", "C": "#c06f12", "D": "#c0352b"}
DEFECT = {"BPFO": "#c0352b", "BPFI": "#7a3fb0", "BSF": "#1f6fb2", "2xBSF": "#1f6fb2", "FTF": "#5b7a1f"}


def _style(ax):
    ax.set_facecolor(PAPER)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(RULE)
    ax.tick_params(colors=INK_3, labelsize=8.5, length=3)
    ax.grid(axis="y", color=RULE, linewidth=0.6)
    ax.set_axisbelow(True)


def _amplitude(ps):
    """Per-bin RMS amplitude that reads as the tone's RMS at its peak."""
    return np.sqrt(np.maximum(ps, 0.0))


def diagnostic_figure(signals, fs, diagnosis, path, title=None, fmax=None):
    """Write the figure to path (PNG). fmax defaults to ~20× running speed, within 400–1000 Hz."""
    iso = diagnosis["iso_20816_3"]
    axis = iso["worst_radial_axis"]
    x = signals[axis]
    rpm = diagnosis["rpm"]
    fr = rpm / 60.0
    zone = iso["zone"]
    fmax = fmax or min(1000.0, max(400.0, 20 * fr))

    fig = plt.figure(figsize=(10, 6.2), dpi=160, facecolor=PAPER)
    gs = fig.add_gridspec(2, 1, height_ratios=[2.3, 1], hspace=0.42, left=0.075, right=0.975, top=0.80, bottom=0.09)

    # ---- velocity spectrum ---------------------------------------------------------------------
    ax = fig.add_subplot(gs[0])
    _style(ax)
    f, ps, enbw = velocity_power_spectrum(x, fs)
    m = (f >= 8.0) & (f <= fmax)  # ISO 20816 starts at 10 Hz; below that is integration noise
    amp = _amplitude(ps[m])
    ax.fill_between(f[m], amp, color=FILL, linewidth=0)
    ax.plot(f[m], amp, color=INK, linewidth=0.7)
    top = float(amp.max()) if len(amp) else 1.0
    ax.set_xlim(0, fmax)
    ax.set_ylim(0, top * 1.28)
    ax.set_ylabel("velocity, mm/s RMS", color=INK_2, fontsize=9)

    orders = diagnosis["axes"][axis]["orders"]
    for k in range(1, 11):
        fk = k * fr
        if fk > fmax:
            break
        key = f"{k}x"
        strong = key in orders and orders[key]["mm_s"] >= 0.08 * top
        ax.axvline(fk, color=AMBER, linewidth=0.9 if k <= 3 else 0.6, alpha=0.9 if k <= 3 else 0.45,
                   linestyle="-" if k <= 3 else ":", zorder=0)
        if k <= 3 or strong:
            ax.text(fk, top * 1.2, f"{k}×", color=AMBER, fontsize=8.5, ha="center", va="bottom", fontweight="bold")
    dom = diagnosis["axes"][axis]["dominant_order"]
    if dom in orders:
        fd, vd = orders[dom]["freq_hz"], orders[dom]["mm_s"]
        ax.annotate(f"{dom}  {vd:.2f} mm/s", xy=(fd, vd), xytext=(fd + fmax * 0.05, vd * 0.92),
                    color=INK, fontsize=8.5, arrowprops={"arrowstyle": "-", "color": INK_3, "linewidth": 0.7})

    hits = {}
    for name, d in (diagnosis.get("bearing") or {}).get(axis, {}).items():
        if name != "_band_hz" and d.get("detected"):
            hits[name] = d
    ax.set_xlabel("frequency, Hz", color=INK_2, fontsize=9)
    ax.set_title(f"Velocity spectrum · axis {axis} · {rpm:g} rpm (1× = {fr:.2f} Hz)", loc="left",
                 color=INK_2, fontsize=9.5, pad=8)

    # ---- envelope spectrum ---------------------------------------------------------------------
    ax2 = fig.add_subplot(gs[1])
    _style(ax2)
    defects = {n: d["defect_hz"] for n, d in (diagnosis.get("bearing") or {}).get(axis, {}).items() if n != "_band_hz"}
    try:
        fe, ae, _, band = envelope_spectrum(x, fs)
        emax = max([4.2 * v for v in defects.values()] + [12 * fr]) if defects else 12 * fr
        me = (fe >= 2.0) & (fe <= emax)
        ax2.fill_between(fe[me], ae[me], color=FILL, linewidth=0)
        ax2.plot(fe[me], ae[me], color=INK, linewidth=0.6)
        etop = float(ae[me].max()) if me.any() else 1.0
        ax2.set_xlim(0, emax)
        ax2.set_ylim(0, max(etop * 1.35, 0.02))  # no bearing lines: a flat trace, not magnified noise
        etop = ax2.get_ylim()[1] / 1.35
        for name, f0 in defects.items():
            if name == "FTF":
                continue
            color = DEFECT.get(name, INK_2)
            detected = name in hits
            for h in range(1, 5):
                if h * f0 > emax:
                    break
                ax2.axvline(h * f0, color=color, linewidth=1.0 if detected else 0.6,
                            alpha=0.9 if detected else 0.3, linestyle="-" if detected else "--", zorder=0)
            ax2.text(f0, etop * 1.24, name + (" ✓" if detected else ""), color=color, fontsize=8,
                     ha="center", va="bottom", fontweight="bold" if detected else "normal")
        ax2.set_title(f"Envelope spectrum · {band[0]:.0f}–{band[1]:.0f} Hz band · bearing defect frequencies",
                      loc="left", color=INK_2, fontsize=9.5, pad=8)
        ax2.set_ylabel("envelope, g", color=INK_2, fontsize=9)
    except ValueError as e:
        ax2.text(0.5, 0.5, f"envelope analysis unavailable: {e}", transform=ax2.transAxes, ha="center", color=INK_3)
    ax2.set_xlabel("frequency, Hz", color=INK_2, fontsize=9)

    # ---- header: verdict + ISO zone ------------------------------------------------------------
    fault = diagnosis["primary_fault"].replace("_", " ")
    fig.text(0.075, 0.95, title or "Vibration diagnosis", color=INK, fontsize=13, fontweight="bold", va="center")
    priority = diagnosis["priority"].split(" (")[0]
    fig.text(0.075, 0.905, f"{fault}  ·  priority {priority}", color=INK_2, fontsize=10, va="center")
    b = iso["boundaries_mm_s"]
    level = iso["worst_radial_mm_s"]
    x0, w, y0, h = 0.60, 0.3, 0.915, 0.022
    vmax = max(b["C/D"] * 1.6, level * 1.15)
    edges = [0, b["A/B"], b["B/C"], b["C/D"], vmax]
    for i, z in enumerate("ABCD"):
        fig.patches.append(matplotlib.patches.Rectangle((x0 + w * edges[i] / vmax, y0), w * (edges[i + 1] - edges[i]) / vmax,
                                                        h, transform=fig.transFigure, color=ZONE[z], alpha=0.85, zorder=1))
    xn = x0 + w * min(level, vmax) / vmax
    fig.lines.append(matplotlib.lines.Line2D([xn, xn], [y0 - 0.012, y0 + h + 0.012], transform=fig.transFigure,
                                             color=INK, linewidth=2, zorder=2))
    fig.text(xn, y0 + h + 0.018, f"{level:.2f} mm/s · zone {zone}", color=ZONE[zone], fontsize=9.5,
             fontweight="bold", ha="center", va="bottom")
    fig.text(x0, y0 - 0.02, f"ISO 20816-3 group {iso.get('group', '?')}, {iso.get('foundation', '?')}",
             color=INK_3, fontsize=7.5, va="top")
    fig.savefig(path, facecolor=PAPER)
    plt.close(fig)
    return path


__all__ = ["diagnostic_figure"]
