// Engineering heatmaps (ported from FieldTab Desk wifi.js): values measured at survey points are interpolated by
// inverse-distance weighting over the floor plan and fade out away from the points. Views: RSSI (strongest AP,
// one SSID, one AP, an AP group), serving AP zones, SNR, AP count and channel overlap.
// Pure helpers are exported for tests/js/heat_test.mjs; drawing needs a canvas.

export const NOT_HEARD = -95;

// Strong -> weak is blue, cyan, green, yellow, orange, red; values in between blend, beyond the ends clamp.
export const ENG_STOPS = [[-85, [214, 40, 40]], [-75, [245, 128, 32]], [-67, [242, 212, 46]], [-60, [62, 186, 82]],
  [-50, [36, 196, 220]], [-35, [32, 92, 218]]];
export const SNR_STOPS = [[5, [214, 40, 40]], [15, [245, 128, 32]], [20, [242, 212, 46]], [25, [62, 186, 82]],
  [35, [36, 196, 220]], [45, [32, 92, 218]]];
export const COUNT_STOPS = [[0, [214, 40, 40]], [1, [245, 128, 32]], [2, [62, 186, 82]], [3, [36, 196, 220]], [5, [32, 92, 218]]];
export const OVERLAP_STOPS = [[0, [62, 186, 82]], [1, [242, 212, 46]], [2, [245, 128, 32]], [4, [214, 40, 40]]];
export const ENG_COVER = {full: 3, zero: 5};
export const COUNT_MIN = -75;     // an AP counts for "AP count" at this RSSI or better
export const OVERLAP_MIN = -82;   // a co-channel radio counts as overlap at this RSSI or better
export const DEFAULT_NOISE = -95;

export function ramp(stops, v) {
  if (v <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    const [v1, c1] = stops[i];
    if (v <= v1) {
      const [v0, c0] = stops[i - 1], t = (v - v0) / (v1 - v0);
      return c0.map((x, k) => Math.round(x + (c1[k] - x) * t));
    }
  }
  return stops[stops.length - 1][1];
}

export function engColor(rssi) { return ramp(ENG_STOPS, rssi); }

// ---------- targets and per-point values ----------

// target: {type: "best"} | {type: "ssid", value} | {type: "set", bssids: Set}
export function targetHit(a, target) {
  return target.type === "best" || (target.type === "ssid" && a.ssid === target.value)
    || (target.type === "set" && target.bssids.has(String(a.bssid).toLowerCase()));
}

export function pointValue(p, target) {
  let best = null;
  for (const a of p.aps || []) if (targetHit(a, target) && (!best || a.rssi > best.rssi)) best = a;
  if (!best) return {v: NOT_HEARD, missing: true};
  return {v: best.rssi, ap: best};
}

export function radioKey(bssid) { return String(bssid || "").toLowerCase().split(":").slice(0, 5).join(":"); }
export function shortMac(bssid) { return String(bssid || "").toLowerCase().split(":").slice(-2).join(":"); }

// Value of one view at a point: {v, missing}. ctx: {target, noise}
export function viewValue(kind, p, target) {
  if (kind === "snr") {
    const pv = pointValue(p, target);
    if (pv.missing) return {v: 0, missing: true};
    const noise = typeof p.noise === "number" && p.noise < 0 ? p.noise : DEFAULT_NOISE;
    return {v: pv.v - noise};
  }
  if (kind === "count") {
    const radios = new Set();
    for (const a of p.aps || []) if (targetHit(a, target) && a.rssi >= COUNT_MIN) radios.add(radioKey(a.bssid));
    return {v: radios.size};
  }
  if (kind === "overlap") {
    const pv = pointValue(p, target);
    if (pv.missing) return {v: 0, missing: true};
    const mine = radioKey(pv.ap.bssid), others = new Set();
    for (const a of p.aps || []) {
      if (a.channel === pv.ap.channel && (a.band || "") === (pv.ap.band || "") && a.rssi >= OVERLAP_MIN && radioKey(a.bssid) !== mine) {
        others.add(radioKey(a.bssid));
      }
    }
    return {v: others.size};
  }
  return pointValue(p, target);
}

export const VIEWS = {
  rssi: {label: "Signal (RSSI)", unit: "dBm", stops: ENG_STOPS, floor: NOT_HEARD},
  snr: {label: "SNR", unit: "dB", stops: SNR_STOPS, floor: 0},
  count: {label: "AP count", unit: "radios", stops: COUNT_STOPS, floor: 0},
  overlap: {label: "Channel overlap", unit: "co-channel radios", stops: OVERLAP_STOPS, floor: 0},
  serving: {label: "Serving AP", unit: "", stops: ENG_STOPS, floor: NOT_HEARD},
};

// ---------- interpolation ----------

export function coverAlpha(d, full = ENG_COVER.full, zero = ENG_COVER.zero) {
  if (d <= full) return 1;
  if (d >= zero) return 0;
  const t = (d - full) / (zero - full);
  return 1 - t * t * (3 - 2 * t);
}

// Inverse-distance weighting over samples [{x, y, v, missing}] (metres); dmin = distance to the nearest sample.
// A missing sample pulls toward the floor value only within the fade radius.
export function idw(samples, x, y, power = 2, floor = NOT_HEARD) {
  let num = 0, den = 0, dmin = Infinity;
  for (const s of samples) {
    const d2 = (s.x - x) ** 2 + (s.y - y) ** 2;
    if (d2 < 1e-12) return {v: s.v, dmin: 0};
    dmin = Math.min(dmin, d2);
    let w = power === 2 ? 1 / d2 : d2 ** (-power / 2);
    if (s.missing) w *= coverAlpha(Math.sqrt(d2));
    num += s.v * w; den += w;
  }
  if (!samples.length) return null;
  return {v: den ? num / den : floor, dmin: Math.sqrt(dmin)};
}

export function heatStep(wM, lM) { return Math.max(0.1, Math.round(Math.sqrt(wM * lM / 40000) * 100) / 100); }

export function heatField(samples, wM, lM, step = heatStep(wM, lM), floor = NOT_HEARD) {
  const nx = Math.max(1, Math.ceil(wM / step - 1e-9)), ny = Math.max(1, Math.ceil(lM / step - 1e-9));
  const v = new Float32Array(nx * ny), a = new Float32Array(nx * ny);
  if (samples.length) {
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const e = idw(samples, (i + 0.5) * wM / nx, (j + 0.5) * lM / ny, 2, floor);
      v[j * nx + i] = e.v; a[j * nx + i] = coverAlpha(e.dmin);
    }
  }
  return {nx, ny, v, a};
}

// Serving zones: perAp[k] = samples of placed AP k; idx = AP with the strongest interpolated signal (-1: none).
export function servingField(perAp, wM, lM, step = heatStep(wM, lM)) {
  const nx = Math.max(1, Math.ceil(wM / step - 1e-9)), ny = Math.max(1, Math.ceil(lM / step - 1e-9));
  const idx = new Int16Array(nx * ny).fill(-1), v = new Float32Array(nx * ny).fill(NOT_HEARD), a = new Float32Array(nx * ny);
  if (perAp.length && perAp[0].length) {
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const x = (i + 0.5) * wM / nx, y = (j + 0.5) * lM / ny, k = j * nx + i;
      for (let n = 0; n < perAp.length; n++) {
        const e = idw(perAp[n], x, y);
        a[k] = coverAlpha(e.dmin);
        if (e.v > v[k] + 1e-6 && e.v > NOT_HEARD + 0.5) { v[k] = e.v; idx[k] = n; }
      }
    }
  }
  return {nx, ny, idx, v, a};
}

export const AP_PALETTE = [[47, 128, 237], [242, 120, 32], [46, 179, 92], [174, 72, 210], [240, 196, 30], [16, 170, 160],
  [230, 73, 128], [120, 130, 250]];
export function apColor(i) { return AP_PALETTE[((i % AP_PALETTE.length) + AP_PALETTE.length) % AP_PALETTE.length]; }

// Physical APs heard at the points: BSSIDs sharing the first 5 MAC bytes are one radio.
export function apGroups(points) {
  const by = new Map();
  for (const p of points) {
    for (const a of p.aps || []) {
      if (!a.bssid) continue;
      const b = a.bssid.toLowerCase(), k = radioKey(b);
      const g = by.get(k) || {bssids: new Map(), ssids: new Map(), channels: new Set(), best: -999};
      const x = g.bssids.get(b) || {bssid: b, ssid: a.ssid || "", channel: a.channel, best: -999, n: 0};
      x.best = Math.max(x.best, a.rssi); x.n++;
      g.bssids.set(b, x);
      if (a.ssid) {
        const s = g.ssids.get(a.ssid) || {n: 0, best: -999};
        s.n++; s.best = Math.max(s.best, a.rssi);
        g.ssids.set(a.ssid, s);
      }
      if (a.channel !== undefined && a.channel !== null) g.channels.add(a.channel);
      g.best = Math.max(g.best, a.rssi);
      by.set(k, g);
    }
  }
  return [...by.values()].map((g) => {
    const bssids = [...g.bssids.keys()].sort();
    return {id: bssids[0], bssids, info: bssids.map((b) => g.bssids.get(b)),
      ssids: [...g.ssids.entries()].sort((a, b) => b[1].n - a[1].n || b[1].best - a[1].best).map((e) => e[0]),
      channels: [...g.channels].sort((a, b) => a - b), best: g.best};
  }).sort((a, b) => b.best - a.best || (a.id < b.id ? -1 : 1));
}

export function groupLabel(g) { return `${g.ssids[0] || "(hidden)"} ${shortMac(g.id)}`; }

export function walkOrder(points) {
  return points.filter((p) => Number.isFinite(p.ts)).sort((a, b) => a.ts - b.ts);
}

// ---------- geometry ----------

const ENG = {rl: 30, rt: 22, pad: 14, r: 14, legend: 72, minW: 520, maxH: 680};

export function engGeom(wM, lM, avail, maxH = ENG.maxH) {
  const ox = ENG.rl + ENG.pad, oy = ENG.rt + ENG.pad;
  const pxm = Math.max(6, Math.min(90, (Math.max(320, avail) - ox - ENG.pad - ENG.r) / wM, maxH / lM));
  const rw = wM * pxm, rh = lM * pxm;
  const W = Math.max(Math.min(ENG.minW, Math.max(320, avail)), Math.ceil(ox + rw + ENG.pad + ENG.r));
  return {pxm, ox, oy, rw, rh, W, H: Math.ceil(oy + rh + ENG.pad + ENG.legend)};
}

export function scaleBarM(pxm) { return [1, 2, 5, 10, 20, 50, 100].find((m) => m * pxm >= 60) || 200; }
export function labelStep(pxm) { return [1, 2, 5, 10, 20, 50, 100].find((x) => x * pxm >= 26) || 200; }

export function rulerMarks(lenM, pxm) {
  const minor = 0.5 * pxm >= 5 ? 0.5 : 1;
  const every = labelStep(pxm);
  const out = [];
  for (let i = 0; i * minor <= lenM + 1e-9; i++) {
    const m = Math.round(i * minor * 100) / 100;
    const whole = Number.isInteger(m);
    out.push({m, major: whole, label: whole && m % every === 0 ? String(m) : ""});
  }
  return out;
}

// Canvas px -> metres on the plan (null outside the room).
export function toMetres(e, px, py, wM, lM) {
  const x = (px - e.ox) / e.pxm, y = (py - e.oy) / e.pxm;
  if (x < -0.05 || y < -0.05 || x > wM + 0.05 || y > lM + 0.05) return null;
  return {x: Math.min(wM, Math.max(0, x)), y: Math.min(lM, Math.max(0, y))};
}

// ---------- drawing ----------

const ENG_BG = "#6b6f75", ENG_ROOM = "#868a91", ENG_ALPHA = 0.9;
const ENG_PLAN = {wall: {color: "#2b2f36"}, door: {color: "#f08a24", cut: true}, window: {color: "#a8dcf7", cut: true},
  beam: {color: "#a0a6ae", dash: true}};

export function fieldImage(f, stops = ENG_STOPS) {
  const off = document.createElement("canvas");
  off.width = f.nx; off.height = f.ny;
  const og = off.getContext("2d");
  const img = og.createImageData(f.nx, f.ny);
  for (let k = 0; k < f.v.length; k++) {
    if (!f.a[k]) continue;
    const [R, G, B] = !f.idx ? ramp(stops, f.v[k]) : f.idx[k] < 0 ? ENG_STOPS[0][1] : apColor(f.idx[k]);
    const weak = f.idx && f.idx[k] >= 0 && f.v[k] < -80 ? 0.45 : 1;
    img.data.set([R, G, B, Math.round(255 * ENG_ALPHA * f.a[k] * weak)], 4 * k);
  }
  og.putImageData(img, 0, 0);
  return off;
}

function line(g, it, pxm, color, w, dash) {
  g.strokeStyle = color; g.lineWidth = w;
  g.setLineDash(dash ? [w * 3, w * 2] : []);
  g.beginPath(); g.moveTo(it.x1 * pxm, it.y1 * pxm); g.lineTo(it.x2 * pxm, it.y2 * pxm); g.stroke();
}

// Plan items in metres ({kind, x1, y1, x2, y2}); doors and windows cut a gap into the walls they sit on.
export function drawPlan(g, items, pxm) {
  const wallW = Math.max(2.5, Math.min(9, 0.25 * pxm));
  const cuts = items.filter((it) => ENG_PLAN[it.kind] && ENG_PLAN[it.kind].cut);
  g.save();
  if (cuts.length) {
    const c = g.canvas, hw = wallW / 2 + 1;
    g.beginPath();
    g.rect(-1e4, -1e4, 2e4 + c.width, 2e4 + c.height);
    for (const it of cuts) {
      const x1 = it.x1 * pxm, y1 = it.y1 * pxm, x2 = it.x2 * pxm, y2 = it.y2 * pxm;
      const len = Math.hypot(x2 - x1, y2 - y1) || 1, nx = -(y2 - y1) / len * hw, ny = (x2 - x1) / len * hw;
      g.moveTo(x1 + nx, y1 + ny); g.lineTo(x2 + nx, y2 + ny); g.lineTo(x2 - nx, y2 - ny); g.lineTo(x1 - nx, y1 - ny); g.closePath();
    }
    g.clip("evenodd");
  }
  g.lineCap = "square";
  for (const it of items) if (it.kind === "wall") line(g, it, pxm, ENG_PLAN.wall.color, wallW);
  g.restore();
  g.save();
  g.lineCap = "butt";
  for (const it of items) {
    if (it.kind === "door") line(g, it, pxm, ENG_PLAN.door.color, Math.max(1.5, wallW * 0.4));
    else if (it.kind === "window") {
      line(g, it, pxm, ENG_PLAN.wall.color, Math.max(2, wallW * 0.7));
      line(g, it, pxm, ENG_PLAN.window.color, Math.max(1, wallW * 0.7 - 2));
    } else if (it.kind === "beam") line(g, it, pxm, ENG_PLAN.beam.color, Math.max(1.5, wallW * 0.35), true);
  }
  g.restore();
}

function rulers(g, e, wM, lM) {
  g.save();
  g.strokeStyle = "rgba(255,255,255,0.45)";
  g.fillStyle = "rgba(255,255,255,0.85)";
  g.lineWidth = 1;
  g.font = "10px -apple-system, sans-serif";
  g.beginPath();
  const yb = ENG.rt - 2, xb = ENG.rl - 2;
  g.moveTo(e.ox, yb + 0.5); g.lineTo(e.ox + e.rw, yb + 0.5);
  g.moveTo(xb + 0.5, e.oy); g.lineTo(xb + 0.5, e.oy + e.rh);
  g.textAlign = "center"; g.textBaseline = "alphabetic";
  for (const k of rulerMarks(wM, e.pxm)) {
    const x = Math.round(e.ox + k.m * e.pxm) + 0.5;
    g.moveTo(x, yb - (k.major ? 5 : 2.5)); g.lineTo(x, yb);
    if (k.label) g.fillText(k.label, x, yb - 7);
  }
  g.textAlign = "right"; g.textBaseline = "middle";
  for (const k of rulerMarks(lM, e.pxm)) {
    const y = Math.round(e.oy + k.m * e.pxm) + 0.5;
    g.moveTo(xb - (k.major ? 5 : 2.5), y); g.lineTo(xb, y);
    if (k.label) g.fillText(k.label, xb - 7, y);
  }
  g.stroke();
  g.textAlign = "left"; g.textBaseline = "alphabetic";
  g.fillText("m", 4, yb - 7);
  g.restore();
}

function legendBand(g, e, s) {
  const stops = s.stops || ENG_STOPS;
  const lo = stops[0][0], hi = stops[stops.length - 1][0];
  const yt = e.oy + e.rh + ENG.pad + 12, y = yt + 20, x0 = ENG.rl + 30, bh = 9;
  let bw = Math.min(280, e.W - x0 - ENG.r - 12);
  g.save();
  g.fillStyle = "#ffffff";
  g.font = "600 12px -apple-system, sans-serif";
  g.textBaseline = "middle"; g.textAlign = "left";
  g.fillText(s.title || "", ENG.rl - 6, yt);
  g.font = "11px -apple-system, sans-serif";
  g.fillStyle = "rgba(255,255,255,0.9)";
  if (s.zones) {
    let x = ENG.rl - 6;
    for (const z of [...s.zones, {name: "none heard", color: ENG_STOPS[0][1]}]) {
      const tw = g.measureText(z.name).width;
      if (x + tw + 22 > e.W - ENG.r) { g.fillText("...", x, y + bh / 2); x = e.W; break; }
      g.fillStyle = `rgb(${z.color})`;
      g.fillRect(x, y, 12, bh + 1);
      g.fillStyle = "rgba(255,255,255,0.9)";
      g.fillText(z.name, x + 16, y + bh / 2);
      x += tw + 30;
    }
    bw = x - x0;
  } else {
    g.fillText(s.unit === "dBm" ? "dBm" : s.unit === "dB" ? "dB" : "n", ENG.rl - 6, y + bh / 2);
    const grad = g.createLinearGradient(x0, 0, x0 + bw, 0);
    for (const [v, c] of stops) grad.addColorStop((v - lo) / (hi - lo), `rgb(${c})`);
    g.fillStyle = grad;
    g.fillRect(x0, y, bw, bh);
    g.strokeStyle = "rgba(255,255,255,0.8)"; g.lineWidth = 1;
    g.fillStyle = "rgba(255,255,255,0.85)";
    g.font = "10px -apple-system, sans-serif";
    g.textAlign = "center"; g.textBaseline = "top";
    g.beginPath();
    for (const [v] of stops) {
      const x = Math.round(x0 + bw * (v - lo) / (hi - lo)) + 0.5;
      g.moveTo(x, y + bh); g.lineTo(x, y + bh + 4);
      g.fillText(v === lo && s.unit === "dBm" ? `${v} or less` : v === hi && s.unit !== "dBm" ? `${v}+` : String(v), x, y + bh + 6);
    }
    g.stroke();
  }
  g.strokeStyle = "rgba(255,255,255,0.8)"; g.lineWidth = 1;
  g.fillStyle = "rgba(255,255,255,0.85)";
  g.font = "10px -apple-system, sans-serif";
  g.textAlign = "center"; g.textBaseline = "top";
  const m = scaleBarM(e.pxm), len = m * e.pxm, xr = e.W - ENG.r, xs = xr - len, ys = y + 3;
  if (xs > x0 + bw + 50) {
    g.beginPath();
    g.moveTo(xs + 0.5, ys - 4); g.lineTo(xs + 0.5, ys + 4); g.moveTo(xs, ys + 0.5); g.lineTo(xr, ys + 0.5);
    g.moveTo(xr - 0.5, ys - 4); g.lineTo(xr - 0.5, ys + 4);
    g.stroke();
    g.fillText(`${m} m`, xs + len / 2, ys + 7);
  }
  g.restore();
}

export const AP_R = 10;

function drawApIcon(g, x, y, ap) {
  g.save();
  if (ap.on) {
    g.beginPath(); g.arc(x, y, AP_R + 4, 0, 2 * Math.PI);
    g.strokeStyle = "#ffb020"; g.lineWidth = 3; g.stroke();
  }
  g.beginPath(); g.arc(x, y, AP_R, 0, 2 * Math.PI);
  g.fillStyle = ap.color ? `rgb(${ap.color})` : "#1d2026"; g.fill();
  g.strokeStyle = "#ffffff"; g.lineWidth = 2; g.stroke();
  g.lineCap = "round"; g.lineWidth = 1.6;
  const cy = y + 3.5;
  g.beginPath(); g.arc(x, cy, 1.6, 0, 2 * Math.PI); g.fillStyle = "#ffffff"; g.fill();
  for (const r of [4, 7]) { g.beginPath(); g.arc(x, cy, r, -Math.PI * 0.78, -Math.PI * 0.22); g.stroke(); }
  if (ap.name) {
    g.font = "600 11px -apple-system, sans-serif";
    g.textAlign = "center"; g.textBaseline = "top";
    g.lineJoin = "round"; g.lineWidth = 3; g.strokeStyle = "rgba(20,22,26,0.85)";
    g.strokeText(ap.name, x, y + AP_R + 3);
    g.fillStyle = "#ffffff";
    g.fillText(ap.name, x, y + AP_R + 3);
  }
  g.restore();
}

// s: {geom, wM, lM, img, bg (HTMLImageElement), plan, pts: [{x, y, v, missing, p}], path, sel, aps, title, unit,
//     stops, zones, pending: {x, y}, labels (bool: values next to points)}
export function renderEng(g, s) {
  const {geom: e, wM, lM, plan, pts, sel} = s;
  const {pxm, rw, rh} = e;
  g.fillStyle = ENG_BG;
  g.fillRect(0, 0, e.W, e.H);
  rulers(g, e, wM, lM);
  g.save();
  g.translate(e.ox, e.oy);
  g.fillStyle = ENG_ROOM;
  g.fillRect(0, 0, rw, rh);
  if (s.bg) {
    g.save();
    g.globalAlpha = 0.55;
    g.drawImage(s.bg, 0, 0, rw, rh);
    g.restore();
  }
  if (s.img) {
    g.save();
    g.beginPath(); g.rect(0, 0, rw, rh); g.clip();
    g.imageSmoothingEnabled = true;
    g.imageSmoothingQuality = "high";
    g.drawImage(s.img, 0, 0, rw, rh);
    g.restore();
  }
  g.strokeStyle = ENG_PLAN.wall.color;
  g.lineWidth = plan.length ? 1 : Math.max(2.5, Math.min(9, 0.25 * pxm));
  g.strokeRect(0, 0, rw, rh);
  if (plan.length) drawPlan(g, plan, pxm);
  if (s.path && s.path.length > 1) {
    g.strokeStyle = "rgba(24,27,32,0.75)";
    g.lineWidth = 1.25;
    g.lineJoin = "round";
    g.beginPath();
    s.path.forEach((q, i) => (i ? g.lineTo(q.x * pxm, q.y * pxm) : g.moveTo(q.x * pxm, q.y * pxm)));
    g.stroke();
  }
  const r = Math.max(3.5, Math.min(5.5, pxm * 0.09));
  g.font = "600 10px -apple-system, sans-serif";
  for (const q of pts) {
    const x = q.x * pxm, y = q.y * pxm, on = sel && q.p === sel;
    if (on) {
      g.beginPath(); g.arc(x, y, r + 5, 0, 2 * Math.PI);
      g.fillStyle = "rgba(255,255,255,0.45)"; g.fill();
    }
    g.beginPath(); g.arc(x, y, r, 0, 2 * Math.PI);
    g.fillStyle = q.missing ? "#1d2026" : "#1f6feb"; g.fill();
    g.lineWidth = on ? 2.5 : 1.75; g.strokeStyle = "#ffffff"; g.stroke();
    if (s.labels && !q.missing) {
      g.fillStyle = "#ffffff"; g.textAlign = "left"; g.textBaseline = "middle";
      g.lineWidth = 3; g.strokeStyle = "rgba(20,22,26,0.8)";
      const t = String(Math.round(q.v));
      g.strokeText(t, x + r + 3, y); g.fillText(t, x + r + 3, y);
    }
  }
  if (s.pending) {
    g.beginPath(); g.arc(s.pending.x * pxm, s.pending.y * pxm, r + 6, 0, 2 * Math.PI);
    g.strokeStyle = "#ffb020"; g.lineWidth = 3; g.setLineDash([4, 3]); g.stroke(); g.setLineDash([]);
  }
  if (!pts.length && !s.pending) {
    g.font = "600 13px -apple-system, sans-serif";
    g.textAlign = "center"; g.textBaseline = "middle";
    g.fillStyle = "rgba(255,255,255,0.92)";
    g.fillText(s.emptyText || "No measured points", rw / 2, rh / 2);
  }
  for (const ap of s.aps || []) drawApIcon(g, ap.x_m * pxm, ap.y_m * pxm, ap);
  g.restore();
  legendBand(g, e, s);
}

// A full scene for one view (used by the survey page and the report): returns {img, pts, title, stops, unit, zones}.
export function buildScene(points, placed, view, wM, lM) {
  const kind = view.kind || "rssi";
  const def = VIEWS[kind] || VIEWS.rssi;
  const target = view.target || {type: "best"};
  const pts = points.map((p) => ({x: p.x_m, y: p.y_m, ...viewValue(kind === "serving" ? "rssi" : kind, p, target), p}));
  let img = null, zones = null;
  if (pts.length) {
    if (kind === "serving") {
      if (placed.length) {
        const perAp = placed.map((ap) => {
          const t = {type: "set", bssids: new Set(ap.bssids)};
          return points.map((p) => ({x: p.x_m, y: p.y_m, ...pointValue(p, t)}));
        });
        img = fieldImage(servingField(perAp, wM, lM));
        zones = placed.map((ap, i) => ({name: ap.name || "(unnamed)", color: apColor(i)}));
      }
    } else {
      img = fieldImage(heatField(pts, wM, lM, heatStep(wM, lM), def.floor), def.stops);
    }
  }
  return {img, pts, stops: def.stops, unit: def.unit, zones};
}
