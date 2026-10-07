// Channel graphs: every AP as an arc over the channels it occupies (span from the server), coloured per SSID,
// plus channel load bars. Pure helpers are exported for tests/js/spectrum_test.mjs.
import {h} from "./lib.js";

// Dark-surface categorical palette (from FieldTab Desk spectrum.js).
export const SSID_PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767",
  "#1c9fb8", "#9a9a1e", "#b45fd6", "#c2702a"];
export const HIDDEN_COLOR = "#7d8796";
export const BAND_NAME = {"2.4": "2.4 GHz", "5": "5 GHz", "6": "6 GHz"};

export function ssidHash(str) {
  let x = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) { x ^= str.charCodeAt(i); x = Math.imul(x, 0x01000193) >>> 0; }
  return x >>> 0;
}

// SSID -> {i, color, dash}: hash picks the slot, a taken slot probes the next free one; once all are used the
// hash slot is reused with a dashed outline. Input order does not matter.
export function ssidColors(ssids) {
  const n = SSID_PALETTE.length, used = new Set(), out = new Map();
  const names = [...new Set(ssids.filter(Boolean))].sort((a, b) => ssidHash(a) - ssidHash(b) || (a < b ? -1 : a > b ? 1 : 0));
  for (const name of names) {
    const want = ssidHash(name) % n;
    let i = -1;
    for (let k = 0; k < n && i < 0; k++) if (!used.has((want + k) % n)) i = (want + k) % n;
    if (i >= 0) { used.add(i); out.set(name, {i, color: SSID_PALETTE[i], dash: false}); }
    else out.set(name, {i: want, color: SSID_PALETTE[want], dash: true});
  }
  return out;
}

export const sig = (a) => (a.rssi ?? a.rssi_max ?? -100);
export const ssidKey = (a) => (a.hidden || !a.ssid ? "" : a.ssid);
export function colorOf(colors, key) { return (key && colors.get(key)) || {i: -1, color: HIDDEN_COLOR, dash: false}; }

// Plot range in channel numbers for a band: all of 2.4 GHz, else the channels in use with a margin.
export function axisFor(band, aps) {
  if (band === "2.4") {
    const ch14 = aps.some((a) => a.channel === 14);
    return {chans: Array.from({length: ch14 ? 14 : 13}, (_, i) => i + 1), lo: -1, hi: ch14 ? 16 : 15};
  }
  const lo = Math.min(...aps.map((a) => a.span_lo), band === "6" ? 1 : 36);
  const hi = Math.max(...aps.map((a) => a.span_hi), band === "6" ? 33 : 64);
  const all = band === "5" ? CH5 : Array.from({length: 59}, (_, i) => 1 + 4 * i);
  return {chans: all.filter((c) => c >= lo && c <= hi), lo: lo - 2, hi: hi + 2};
}

export const CH5 = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144,
  149, 153, 157, 161, 165, 169, 173, 177];

// Arc outline: rounded hump, at the peak in the middle and down to the floor at the span edges.
export function shape(u) { return u <= 0 || u >= 1 ? 0 : 1 - Math.abs(2 * u - 1) ** 3; }

// The strongest arc per SSID per channel group gets a label; labels that would overlap are skipped.
export function labelCandidates(aps) {
  const by = new Map();
  for (const a of aps) {
    const k = ssidKey(a) + "|" + Math.round(a.center);
    if (!by.has(k) || sig(by.get(k)) < sig(a)) by.set(k, a);
  }
  return [...by.values()].sort((a, b) => sig(b) - sig(a));
}

export function boxesOverlap(a, b, pad = 2) {
  return a.x < b.x + b.w + pad && b.x < a.x + a.w + pad && a.y < b.y + b.h + pad && b.y < a.y + a.h + pad;
}

export function placeLabels(items, bounds = {x0: -Infinity, x1: Infinity, y0: -Infinity}) {
  const placed = [];
  for (const it of items) {
    const x = Math.max(bounds.x0, Math.min(bounds.x1 - it.w, it.x - it.w / 2));
    for (const dy of [0, it.h + 4]) {
      const box = {id: it.id, x, y: it.y - it.h - dy, w: it.w, h: it.h};
      if (box.y < bounds.y0 || placed.some((p) => boxesOverlap(p, box))) continue;
      placed.push(box);
      break;
    }
  }
  return placed;
}

export const textW = (s, px = 11) => Math.ceil(String(s).length * px * 0.6) + 4;

// Rows of the filtered AP list: {band, q, sec, minRssi, noHidden}
export function filterAps(aps, f = {}) {
  const q = (f.q || "").trim().toLowerCase();
  return aps.filter((a) => (!f.band || f.band === "all" || a.band === f.band)
    && (!f.sec || f.sec === "all" || a.sec_class === f.sec)
    && sig(a) >= (f.minRssi ?? -100)
    && !(f.noHidden && !ssidKey(a))
    && (!q || (a.ssid || "").toLowerCase().includes(q) || String(a.bssid || "").includes(q)
      || String(a.vendor || "").toLowerCase().includes(q) || String(a.channel) === q));
}

// ---------- drawing ----------

const NS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) el.setAttribute(k, v);
  for (const k of kids.flat()) if (k !== null && k !== undefined) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}

// Spectrum chart of one band: arcs over the channels, height = signal (-100..-20 dBm).
export function spectrumSvg(aps, band, opts = {}) {
  const W = opts.width || 900, H = opts.height || 300, pad = {l: 44, r: 12, t: 14, b: 30};
  const list = aps.filter((a) => a.band === band && a.channel);
  const ax = axisFor(band, list);
  const colors = opts.colors || ssidColors(list.map(ssidKey));
  const xOf = (c) => pad.l + (c - ax.lo) / (ax.hi - ax.lo) * (W - pad.l - pad.r);
  const yOf = (v) => pad.t + (1 - (Math.max(-100, Math.min(-20, v)) + 100) / 80) * (H - pad.t - pad.b);
  const svg = s("svg", {viewBox: `0 0 ${W} ${H}`, class: "spectrum", role: "img", "aria-label": `${BAND_NAME[band]} channel graph`});
  const grid = s("g", {class: "grid"});
  for (let v = -90; v <= -30; v += 10) {
    grid.append(s("line", {x1: pad.l, x2: W - pad.r, y1: yOf(v), y2: yOf(v)}), s("text", {x: pad.l - 6, y: yOf(v) + 4, "text-anchor": "end"}, v));
  }
  for (const c of ax.chans) {
    const x = xOf(c);
    grid.append(s("line", {x1: x, x2: x, y1: pad.t, y2: H - pad.b, class: band === "2.4" && [1, 6, 11].includes(c) ? "pref" : ""}),
      s("text", {x, y: H - pad.b + 16, "text-anchor": "middle"}, c));
  }
  grid.append(s("text", {x: 4, y: pad.t + 4}, "dBm"));
  svg.append(grid);
  const arcs = s("g", {class: "arcs"});
  const sorted = [...list].sort((a, b) => sig(a) - sig(b));
  for (const a of sorted) {
    const x0 = xOf(a.span_lo), x1 = xOf(a.span_hi), yb = yOf(-100), yt = yOf(sig(a));
    const pts = [];
    for (let i = 0; i <= 24; i++) {
      const u = i / 24;
      pts.push(`${(x0 + (x1 - x0) * u).toFixed(1)},${(yb - (yb - yt) * shape(u)).toFixed(1)}`);
    }
    const col = colorOf(colors, ssidKey(a));
    const on = opts.highlight && opts.highlight === a.bssid;
    const path = s("polyline", {points: pts.join(" "), fill: col.color, "fill-opacity": on ? 0.45 : 0.14, stroke: col.color,
      "stroke-width": on ? 3 : 1.6, "stroke-dasharray": col.dash ? "5 3" : null},
    s("title", `${a.ssid || "(hidden)"}  ${a.bssid}\nch ${a.channel} / ${a.width} MHz, ${sig(a)} dBm, ${a.security || ""}`));
    if (opts.onPick) { path.style.cursor = "pointer"; path.addEventListener("click", () => opts.onPick(a)); }
    arcs.append(path);
  }
  svg.append(arcs);
  const labels = labelCandidates(list).map((a) => ({id: a.bssid, a, x: xOf(a.center), y: yOf(sig(a)) - 2,
    w: textW(ssidKey(a) || "(hidden)"), h: 13}));
  const placed = placeLabels(labels, {x0: pad.l, x1: W - pad.r, y0: 0});
  const lg = s("g", {class: "labels"});
  for (const b of placed) {
    const a = labels.find((l) => l.id === b.id).a;
    lg.append(s("text", {x: b.x + 2, y: b.y + b.h - 2, fill: colorOf(colors, ssidKey(a)).color}, ssidKey(a) || "(hidden)"));
  }
  svg.append(lg);
  if (!list.length) svg.append(s("text", {x: W / 2, y: H / 2, "text-anchor": "middle", class: "nodata"}, `No networks on ${BAND_NAME[band]}`));
  return svg;
}

// Channel load bars (stats from the server: {channel, aps, load}); the recommended channel is marked.
export function loadBars(stats, opts = {}) {
  const used = stats.filter((c) => opts.all || c.aps || c.load);
  const W = opts.width || 900, H = 150, pad = {l: 44, r: 12, t: 10, b: 30};
  const max = Math.max(1, ...used.map((c) => c.load));
  const bw = (W - pad.l - pad.r) / Math.max(1, used.length);
  const svg = s("svg", {viewBox: `0 0 ${W} ${H}`, class: "loadbars", role: "img", "aria-label": "Channel load"});
  svg.append(s("text", {x: 4, y: pad.t + 8, class: "axis"}, "load"));
  used.forEach((c, i) => {
    const hh = (H - pad.t - pad.b) * c.load / max, x = pad.l + i * bw;
    const rec = opts.recommend && opts.recommend.includes(c.channel);
    svg.append(s("rect", {x: x + 2, y: H - pad.b - hh, width: Math.max(2, bw - 4), height: hh, class: rec ? "rec" : c.aps ? "used" : "near"},
      s("title", `ch ${c.channel}: ${c.aps} AP(s) on it, load ${c.load}`)),
    s("text", {x: x + bw / 2, y: H - pad.b + 14, "text-anchor": "middle", class: "axis"}, c.channel),
    c.aps ? s("text", {x: x + bw / 2, y: H - pad.b - hh - 3, "text-anchor": "middle", class: "count"}, c.aps) : null);
  });
  if (!used.length) svg.append(s("text", {x: W / 2, y: H / 2, "text-anchor": "middle", class: "nodata"}, "No load"));
  return svg;
}

export function legend(aps) {
  const colors = ssidColors(aps.map(ssidKey));
  const by = new Map();
  for (const a of aps) {
    const k = ssidKey(a), e = by.get(k) || {k, n: 0, best: -999};
    e.n++; e.best = Math.max(e.best, sig(a));
    by.set(k, e);
  }
  return h("div.legend", [...by.values()].sort((a, b) => b.best - a.best).map((e) => {
    const c = colorOf(colors, e.k);
    return h("span.sw", h("i", {style: {background: c.color}}), `${e.k || "(hidden)"} (${e.n})`);
  }));
}
