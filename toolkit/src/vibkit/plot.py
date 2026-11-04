"""Diagnostic figure: what an analyst looks at, with the evidence marked.

    import vibkit, vibkit.plot
    d = vibkit.analyze(csv, meta)
    signals, fs = vibkit.load_csv(csv)
    vibkit.plot.diagnostic_figure(signals, fs, d, "/work/out/spectrum.png", title="P-101A · Unbalance")

Three panels for the worst radial axis:
- velocity spectrum to 10× running speed, running-speed orders marked, the dominant one labelled;
- envelope spectrum with the bearing defect frequencies (detected ones solid and ticked), where
  early bearing damage shows long before the overall level moves;
- a few shaft revolutions of the time waveform, where impacts and modulation are visible.
The header carries the finding and the overall level on the ISO 20816-3 scale.

units="us" (default) shows velocity in in/s peak (derived: RMS × √2 / 25.4) and frequency in
CPM; units="iso" shows mm/s RMS and Hz. The ISO zone boundaries are converted, never changed.
Matplotlib (Agg) only; no network, no fonts to fetch.
"""

import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .spectral import envelope_spectrum, velocity_power_spectrum  # noqa: E402

INK, INK_2, INK_3, RULE = "#1c1f22", "#454b50", "#80878b", "#dcdedb"
PAPER, FILL = "#fafbf8", "#e6e8e3"
ORDER = "#9a6a00"          # running-speed orders
ZONE = {"A": "#2c8a54", "B": "#7d8f1c", "C": "#c06f12", "D": "#c0352b"}
# Defect lines: the console's validated categorical slots (colour-blind safe, labelled anyway).
DEFECT = {"BPFO": "#2a78d6", "BPFI": "#eb6834", "BSF": "#1baf7a", "2xBSF": "#1baf7a", "FTF": "#6b7176"}
TITLE_MAX = 40

UNITS = {
    "us": {"v": "in/s pk", "f": "CPM", "fk": 60.0, "vk": math.sqrt(2) / 25.4, "vfmt": "{:.3f}"},
    "iso": {"v": "mm/s RMS", "f": "Hz", "fk": 1.0, "vk": 1.0, "vfmt": "{:.2f}"},
}


def _style(ax):
    ax.set_facecolor(PAPER)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(RULE)
    ax.tick_params(colors=INK_3, labelsize=8.5, length=3)
    ax.grid(axis="y", color=RULE, linewidth=0.6)
    ax.set_axisbelow(True)


def _label(ax, text):
    ax.set_title(text, loc="left", color=INK_2, fontsize=9.5, pad=7)


def _freq_axis(ax, u):
    ax.set_xlabel(f"frequency, {u['f']}", color=INK_2, fontsize=9)
    if u["f"] == "CPM":
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))


def diagnostic_figure(signals, fs, diagnosis, path, title=None, fmax=None, units="us", revolutions=4):
    """Write the figure to path (PNG). fmax (Hz) defaults to 10.5× running speed."""
    u = UNITS["us" if units not in UNITS else units]
    iso = diagnosis["iso_20816_3"]
    axis = iso["worst_radial_axis"]
    x = signals[axis]
    rpm = diagnosis["rpm"]
    fr = rpm / 60.0
    zone = iso["zone"]
    fmax = fmax or 10.5 * fr

    fig = plt.figure(figsize=(10, 8.2), dpi=160, facecolor=PAPER)
    gs = fig.add_gridspec(3, 1, height_ratios=[2.2, 1.15, 1.0], hspace=0.62, left=0.085, right=0.975, top=0.84, bottom=0.07)

    # ---- velocity spectrum ---------------------------------------------------------------------
    ax = fig.add_subplot(gs[0])
    _style(ax)
    f, ps, _ = velocity_power_spectrum(x, fs)
    m = (f >= 8.0) & (f <= fmax)  # ISO 20816 starts at 10 Hz; below that is integration noise
    fx = f[m] * u["fk"]
    amp = np.sqrt(np.maximum(ps[m], 0.0)) * u["vk"]   # per-bin amplitude that reads as the tone's level
    ax.fill_between(fx, amp, color=FILL, linewidth=0)
    ax.plot(fx, amp, color=INK, linewidth=1.15, solid_joinstyle="round")
    top = float(amp.max()) if len(amp) else 1.0
    ax.set_xlim(0, fmax * u["fk"])
    ax.set_ylim(0, top * 1.3)
    ax.set_ylabel(f"velocity, {u['v']}", color=INK_2, fontsize=9)
    orders = diagnosis["axes"][axis]["orders"]
    for k in range(1, 11):
        fk = k * fr
        if fk > fmax:
            break
        key = f"{k}x"
        strong = key in orders and orders[key]["mm_s"] * u["vk"] >= 0.08 * top
        ax.axvline(fk * u["fk"], color=ORDER, linewidth=0.9 if k <= 3 else 0.6, alpha=0.85 if k <= 3 else 0.4, zorder=0)
        if k <= 3 or strong or k == 6:
            ax.text(fk * u["fk"], top * 1.22, f"{k}×", color=ORDER, fontsize=8.5, ha="center", va="bottom", fontweight="bold")
    dom = diagnosis["axes"][axis]["dominant_order"]
    if dom in orders:
        fd, vd = orders[dom]["freq_hz"] * u["fk"], orders[dom]["mm_s"] * u["vk"]
        ax.annotate(f"{dom.replace('x', '×')}  {u['vfmt'].format(vd)} {u['v']}", xy=(fd, vd),
                    xytext=(fd + fmax * u["fk"] * 0.06, vd * 0.9), color=INK, fontsize=9,
                    arrowprops={"arrowstyle": "-", "color": INK_3, "linewidth": 0.7})
    _freq_axis(ax, u)
    _label(ax, f"Velocity spectrum, axis {axis}, {rpm:g} rpm (1× = {fr * u['fk']:,.{0 if u['f'] == 'CPM' else 2}f} {u['f']})")

    # ---- envelope spectrum ---------------------------------------------------------------------
    ax2 = fig.add_subplot(gs[1])
    _style(ax2)
    bearing = (diagnosis.get("bearing") or {}).get(axis, {})
    defects = {n: d["defect_hz"] for n, d in bearing.items() if n != "_band_hz"}
    hits = {n for n, d in bearing.items() if n != "_band_hz" and d.get("detected")}
    try:
        fe, ae, _, band = envelope_spectrum(x, fs)
        emax = max([4.2 * v for v in defects.values()] + [12 * fr]) if defects else 12 * fr
        me = (fe >= 2.0) & (fe <= emax)
        ax2.fill_between(fe[me] * u["fk"], ae[me], color=FILL, linewidth=0)
        ax2.plot(fe[me] * u["fk"], ae[me], color=INK, linewidth=0.9)
        etop = float(ae[me].max()) if me.any() else 1.0
        ax2.set_xlim(0, emax * u["fk"])
        ax2.set_ylim(0, max(etop * 1.4, 0.02))  # no bearing lines: a flat trace, not magnified noise
        ytxt = ax2.get_ylim()[1] / 1.4 * 1.27
        for name, f0 in defects.items():
            if name == "FTF":
                continue
            color = DEFECT.get(name, INK_2)
            hit = name in hits
            for h in range(1, 5):
                if h * f0 > emax:
                    break
                ax2.axvline(h * f0 * u["fk"], color=color, linewidth=1.1 if hit else 0.6,
                            alpha=0.9 if hit else 0.35, linestyle="-" if hit else "--", zorder=0)
            ax2.text(f0 * u["fk"], ytxt, name + (" ✓" if hit else ""), color=color, fontsize=8,
                     ha="center", va="bottom", fontweight="bold" if hit else "normal")
        _label(ax2, f"Envelope spectrum, {band[0]:.0f}–{band[1]:.0f} Hz band, bearing defect frequencies")
        ax2.set_ylabel("envelope, g", color=INK_2, fontsize=9)
    except ValueError as e:
        ax2.text(0.5, 0.5, f"envelope analysis unavailable: {e}", transform=ax2.transAxes, ha="center", color=INK_3)
    _freq_axis(ax2, u)

    # ---- time waveform -------------------------------------------------------------------------
    ax3 = fig.add_subplot(gs[2])
    _style(ax3)
    n = min(len(x), int(round(revolutions / fr * fs)))
    t_ms = np.arange(n) / fs * 1000
    w = np.asarray(x[:n], dtype=float)
    w = w - w.mean()
    ax3.plot(t_ms, w, color=INK, linewidth=0.6)
    for r in range(1, revolutions):
        ax3.axvline(r * 1000 / fr, color=ORDER, linewidth=0.6, alpha=0.5, zorder=0)
    lim = float(np.max(np.abs(w))) if n else 1.0
    ax3.set_ylim(-lim * 1.15, lim * 1.15)
    ax3.set_xlim(0, t_ms[-1] if n else 1)
    ax3.set_xlabel("time, ms", color=INK_2, fontsize=9)
    ax3.set_ylabel("accel, g", color=INK_2, fontsize=9)
    _label(ax3, f"Time waveform, {revolutions} shaft revolutions (lines mark each turn), {lim:.2f} g peak")

    # ---- header: finding + ISO zone ------------------------------------------------------------
    head = title or "Vibration diagnosis"
    if len(head) > TITLE_MAX:
        head = head[:TITLE_MAX - 1].rstrip() + "…"
    fault = diagnosis["primary_fault"].replace("_", " ")
    fig.text(0.085, 0.955, head, color=INK, fontsize=13.5, fontweight="bold", va="center")
    priority = diagnosis["priority"].split(" (")[0]
    fig.text(0.085, 0.918, f"{fault}, priority {priority}", color=INK_2, fontsize=10, va="center")
    b = {k: v * u["vk"] for k, v in iso["boundaries_mm_s"].items()}
    level = iso["worst_radial_mm_s"] * u["vk"]
    x0, wd, y0, h = 0.62, 0.30, 0.925, 0.02
    vmax = max(b["C/D"] * 1.6, level * 1.15)
    edges = [0, b["A/B"], b["B/C"], b["C/D"], vmax]
    for i, z in enumerate("ABCD"):
        fig.patches.append(matplotlib.patches.Rectangle((x0 + wd * edges[i] / vmax, y0), wd * (edges[i + 1] - edges[i]) / vmax,
                                                        h, transform=fig.transFigure, color=ZONE[z], alpha=0.85, zorder=1))
    xn = x0 + wd * min(level, vmax) / vmax
    fig.lines.append(matplotlib.lines.Line2D([xn, xn], [y0 - 0.011, y0 + h + 0.011], transform=fig.transFigure,
                                             color=INK, linewidth=2, zorder=2))
    fig.text(min(xn, x0 + wd - 0.07), y0 + h + 0.016, f"{u['vfmt'].format(level)} {u['v']}, zone {zone}",
             color=ZONE[zone], fontsize=9.5, fontweight="bold", ha="center", va="bottom")
    fig.text(x0, y0 - 0.018, f"ISO 20816-3 group {iso.get('group', '?')}, {iso.get('foundation', '?')}"
             + ("" if u["vk"] == 1.0 else f" ({iso['worst_radial_mm_s']:.2f} mm/s RMS)"),
             color=INK_3, fontsize=7.5, va="top")
    fig.savefig(path, facecolor=PAPER)
    plt.close(fig)
    return path


__all__ = ["diagnostic_figure"]
