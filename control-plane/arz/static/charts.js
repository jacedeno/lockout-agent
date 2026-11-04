// Lockout charts: small SVG line charts with a crosshair tooltip, no dependencies.
// One series per chart (two measures never share a y-axis); reference lines, zone bands and
// markers are drawn from the spec. Colours come from CSS classes, so both themes work.
"use strict";

const Charts = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const BASE = { l: 58, r: 18, t: 24, b: 36 };
  const groups = new Map();   // linked crosshairs (the two trend charts)

  const el = (tag, attrs = {}, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
    if (parent) parent.appendChild(n);
    return n;
  };

  function niceTicks(lo, hi, count = 5) {
    const span = hi - lo || 1;
    const step0 = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) || 10 * mag;
    const out = [];
    for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + 1e-9 * span; v += step) out.push(+v.toPrecision(12));
    return out;
  }

  function logTicks(lo, hi) {
    const out = [];
    for (let e = Math.ceil(Math.log10(lo) - 1e-9); e <= Math.floor(Math.log10(hi) + 1e-9); e++) out.push(Math.pow(10, e));
    return out;
  }

  // Keep every peak: per pixel column, the min and max of the samples that fall in it.
  function decimate(xs, ys, sx, x0, x1) {
    const pts = [];
    let col = null, lo = 0, hi = 0, xlo = 0, xhi = 0;
    const flush = () => { if (col !== null) { if (xlo <= xhi) { pts.push([xlo, lo], [xhi, hi]); } else { pts.push([xhi, hi], [xlo, lo]); } } };
    for (let i = 0; i < xs.length; i++) {
      const x = xs[i];
      if (x < x0 || x > x1) continue;
      const c = Math.round(sx(x));
      const y = ys[i];
      if (c !== col) { flush(); col = c; lo = hi = y; xlo = xhi = x; continue; }
      if (y < lo) { lo = y; xlo = x; }
      if (y > hi) { hi = y; xhi = x; }
    }
    flush();
    return pts;
  }

  function render(host, spec) {
    host.classList.add("chart");
    host.innerHTML = "";
    const M = { ...BASE, ...(spec.margin || {}) };
    const W = Math.max(280, host.clientWidth);
    const H = spec.height || 200;
    const iw = W - M.l - M.r, ih = H - M.t - M.b;
    const [x0, x1] = spec.x.domain;
    const sx = (v) => M.l + (v - x0) / (x1 - x0) * iw;
    let y0, y1, sy, yt;
    if (spec.y.log) {
      [y0, y1] = spec.y.domain;
      const l0 = Math.log10(y0), l1 = Math.log10(y1);
      sy = (v) => M.t + ih - (Math.log10(Math.max(v, y0)) - l0) / (l1 - l0) * ih;
      yt = logTicks(y0, y1);
    } else {
      [y0, y1] = spec.y.domain;
      sy = (v) => M.t + ih - (Math.min(Math.max(v, y0), y1) - y0) / (y1 - y0) * ih;
      yt = niceTicks(y0, y1, spec.y.ticks || 4);
    }
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": spec.label || "" }, host);

    // zone bands (trend), behind everything
    for (const b of spec.bands || []) {
      const top = sy(Math.min(b.y1, y1)), bot = sy(Math.max(b.y0, y0));
      if (bot - top <= 0) continue;
      el("rect", { x: M.l, y: top, width: iw, height: bot - top, class: `band ${b.cls}` }, svg);
      if (b.label) el("text", { x: M.l + iw + 8, y: (top + bot) / 2 + 4, class: "band-label" }, svg).textContent = b.label;
    }
    // grid + y axis
    for (const v of yt) {
      if (v < y0 - 1e-12 || v > y1 + 1e-12) continue;
      const y = sy(v);
      el("line", { x1: M.l, x2: M.l + iw, y1: y, y2: y, class: "grid" }, svg);
      el("text", { x: M.l - 8, y: y + 4, class: "tick", "text-anchor": "end" }, svg).textContent = spec.y.fmt(v);
    }
    el("text", { x: 14, y: M.t + ih / 2, class: "axis-label", transform: `rotate(-90 14 ${M.t + ih / 2})`, "text-anchor": "middle" }, svg).textContent = spec.y.label;
    // x axis
    el("line", { x1: M.l, x2: M.l + iw, y1: M.t + ih, y2: M.t + ih, class: "axis" }, svg);
    const xt = spec.x.ticks || niceTicks(x0, x1, Math.max(3, Math.round(iw / 110)));
    for (const v of xt) {
      if (v < x0 - 1e-9 || v > x1 + 1e-9) continue;
      el("text", { x: sx(v), y: M.t + ih + 16, class: "tick", "text-anchor": "middle" }, svg).textContent = spec.x.fmt(v);
    }
    el("text", { x: M.l + iw, y: H - 4, class: "axis-label", "text-anchor": "end" }, svg).textContent = spec.x.label;

    // reference lines (orders, defect frequencies, revolutions, P marker). Labels never overlap:
    // strong ones (detected lines, P) are placed first, the rest only where they still fit.
    const placed = [];
    const vis = (spec.vlines || []).filter((v) => v.x >= x0 && v.x <= x1);
    for (const v of vis) el("line", { x1: sx(v.x), x2: sx(v.x), y1: M.t, y2: M.t + ih, class: `vline ${v.cls || ""}` }, svg);
    for (const v of vis.filter((v) => v.label).sort((a, b) => (b.strong ? 1 : 0) - (a.strong ? 1 : 0) || a.x - b.x)) {
      const x = sx(v.x), half = v.label.length * 3.4 + 5;
      if (placed.some(([a, b]) => x + half > a && x - half < b)) continue;
      placed.push([x - half, x + half]);
      el("text", { x, y: M.t - 8, class: `vlabel ${v.strong ? "strong" : ""}`, "text-anchor": "middle" }, svg).textContent = v.label;
    }
    // the series: wash + line
    const s = spec.series;
    const pts = s.points ? s.x.map((x, i) => [x, s.y[i]]) : decimate(s.x, s.y, sx, x0, x1);
    if (pts.length) {
      const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join("");
      if (s.area) el("path", { d: `${d}L${sx(pts[pts.length - 1][0]).toFixed(1)},${sy(y0)}L${sx(pts[0][0]).toFixed(1)},${sy(y0)}Z`, class: `wash ${s.cls || ""}` }, svg);
      el("path", { d, class: `line ${s.cls || ""}` }, svg);
    }
    for (const p of spec.markers || []) {
      el("circle", { cx: sx(p.x), cy: sy(p.y), r: p.r || 4, class: `marker ${p.cls || ""}` }, svg);
      if (p.label) el("text", { x: sx(p.x) + (p.dx ?? 0), y: sy(p.y) - 10, class: "mlabel", "text-anchor": p.anchor || "middle" }, svg).textContent = p.label;
    }

    // hover layer: crosshair + one tooltip; arrow keys move it too
    const cross = el("line", { y1: M.t, y2: M.t + ih, class: "cross", visibility: "hidden" }, svg);
    const dot = el("circle", { r: 4.5, class: "cross-dot", visibility: "hidden" }, svg);
    const hit = el("rect", { x: M.l, y: M.t, width: iw, height: ih, class: "hit", tabindex: 0, "aria-label": `${spec.label}: use the arrow keys to read values` }, svg);
    const tip = document.createElement("div");
    tip.className = "tip";
    tip.hidden = true;
    host.appendChild(tip);
    let cur = null;
    const show = (xv, fromGroup) => {
      const r = spec.hover(xv);
      if (!r) return;
      cur = r.x;
      const x = sx(r.x);
      cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
      if (r.y != null) { dot.setAttribute("cx", x); dot.setAttribute("cy", sy(r.y)); dot.setAttribute("visibility", "visible"); }
      tip.replaceChildren();
      const v = document.createElement("b"); v.textContent = r.value; tip.appendChild(v);
      for (const line of r.lines || []) { const sp = document.createElement("span"); sp.textContent = line; tip.appendChild(sp); }
      tip.hidden = false;
      const tw = tip.offsetWidth;
      tip.style.left = `${Math.max(0, x + 12 + tw > W ? x - 12 - tw : x + 12)}px`;
      tip.style.top = `${M.t}px`;
      if (spec.group && !fromGroup) for (const other of groups.get(spec.group) || []) if (other.host !== host) other.show(r.x, true);
    };
    const hide = (fromGroup) => {
      cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.hidden = true;
      if (spec.group && !fromGroup) for (const other of groups.get(spec.group) || []) if (other.host !== host) other.hide(true);
    };
    const toX = (ev) => {
      const r = svg.getBoundingClientRect();
      return x0 + ((ev.clientX - r.left) * (W / r.width) - M.l) / iw * (x1 - x0);
    };
    hit.addEventListener("pointermove", (ev) => show(toX(ev)));
    hit.addEventListener("pointerleave", () => hide());
    hit.addEventListener("blur", () => hide());
    hit.addEventListener("keydown", (ev) => {
      if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft") return;
      ev.preventDefault();
      const step = spec.keyStep || (x1 - x0) / 100;
      show(Math.min(x1, Math.max(x0, (cur ?? x0) + (ev.key === "ArrowRight" ? step : -step))));
    });
    if (spec.group) {
      const list = (groups.get(spec.group) || []).filter((g) => g.host !== host && document.body.contains(g.host));
      list.push({ host, show, hide });
      groups.set(spec.group, list);
    }
  }

  function chart(host, spec) {
    render(host, spec);
    if (host._ro) host._ro.disconnect();
    let w = host.clientWidth;
    host._ro = new ResizeObserver(() => { if (Math.abs(host.clientWidth - w) > 4) { w = host.clientWidth; render(host, spec); } });
    host._ro.observe(host);
  }

  // nearest sample, or the highest sample within ±px pixels (readers aim near a peak, not at it)
  function snap(xs, ys, xv, span, peak) {
    let lo = 0, hi = xs.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] < xv) lo = mid; else hi = mid; }
    const near = Math.abs(xs[lo] - xv) < Math.abs(xs[hi] - xv) ? lo : hi;
    let best = near;
    if (peak && span > 0) {
      for (let i = near; i >= 0 && xs[i] >= xv - span; i--) if (ys[i] > ys[best]) best = i;
      for (let i = near; i < xs.length && xs[i] <= xv + span; i++) if (ys[i] > ys[best]) best = i;
    }
    return best;
  }

  return { chart, snap, niceTicks };
})();
