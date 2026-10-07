// Live pages: AP table, channel graphs, find-AP meter, scan history, settings.
import {h, api, enc, table, badge, empty, ago, fmtTime, toast, debounce} from "./lib.js";
import {spectrumSvg, loadBars, legend, filterAps, sig, BAND_NAME} from "./spectrum.js";

export const SEC_KIND = {open: "err", wep: "err", wpa1: "warn", wpa2: "", wpa3: "ok", ent: "ok", owe: "", unknown: "warn"};
export const QUALITY_KIND = {excellent: "ok", good: "ok", fair: "", weak: "warn", poor: "err"};

export function secBadge(a) { return badge(a.security || "?", SEC_KIND[a.sec_class] || ""); }

export function signalCell(v) {
  if (v === null || v === undefined) return "";
  const pct = Math.max(0, Math.min(100, (v + 95) * 100 / 65));
  const cls = v >= -60 ? "ok" : v >= -67 ? "good" : v >= -75 ? "fair" : v >= -82 ? "warn" : "err";
  return h("span.sig", h("span.sigbar", h("i." + cls, {style: {width: pct + "%"}})), h("span.num", `${v}`));
}

// ---------- signal-over-time chart (SVG) ----------

const NS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) el.setAttribute(k, v);
  for (const k of kids.flat()) if (k !== null && k !== undefined) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}
const LINE_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9", "#1c9fb8", "#e66767"];

// Gaps longer than 2.5 scan intervals break the line (the AP was not heard in between).
export function splitSeries(points, gap) {
  const out = [];
  let cur = [];
  for (const p of points) {
    if (cur.length && p.ts - cur[cur.length - 1].ts > gap) { out.push(cur); cur = []; }
    cur.push(p);
  }
  if (cur.length) out.push(cur);
  return out;
}

export function signalChart(series, opts = {}) {
  const W = opts.width || 900, H = opts.height || 220, pad = {l: 44, r: 12, t: 10, b: 26};
  const all = Object.values(series).flat();
  const t1 = opts.t1 || Math.max(Date.now() / 1000, ...all.map((p) => p.ts));
  const t0 = opts.t0 || Math.min(t1 - 60, ...all.map((p) => p.ts));
  const xOf = (t) => pad.l + (t - t0) / Math.max(1, t1 - t0) * (W - pad.l - pad.r);
  const yOf = (v) => pad.t + (1 - (Math.max(-100, Math.min(-20, v)) + 100) / 80) * (H - pad.t - pad.b);
  const svg = s("svg", {viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": "Signal over time"});
  const g = s("g", {class: "grid"});
  for (let v = -90; v <= -30; v += 10) g.append(s("line", {x1: pad.l, x2: W - pad.r, y1: yOf(v), y2: yOf(v)}), s("text", {x: pad.l - 6, y: yOf(v) + 4, "text-anchor": "end"}, v));
  for (let i = 0; i <= 4; i++) {
    const t = t0 + (t1 - t0) * i / 4;
    g.append(s("text", {x: xOf(t), y: H - 8, "text-anchor": "middle"}, new Date(t * 1000).toLocaleTimeString("en-GB", {hour: "2-digit", minute: "2-digit"})));
  }
  svg.append(g);
  const gap = Math.max(60, (opts.interval || 15) * 2.5);
  Object.entries(series).forEach(([key, pts], i) => {
    const col = LINE_COLORS[i % LINE_COLORS.length];
    for (const seg of splitSeries(pts, gap)) {
      if (seg.length === 1) svg.append(s("circle", {cx: xOf(seg[0].ts), cy: yOf(seg[0].rssi), r: 2.5, fill: col}));
      else svg.append(s("polyline", {points: seg.map((p) => `${xOf(p.ts).toFixed(1)},${yOf(p.rssi).toFixed(1)}`).join(" "),
        fill: "none", stroke: col, "stroke-width": 1.8}, s("title", key)));
    }
  });
  if (!all.length) svg.append(s("text", {x: W / 2, y: H / 2, "text-anchor": "middle", class: "nodata"}, "No samples in this window"));
  return svg;
}

export function chartLegend(keys, names = {}) {
  return h("div.legend", keys.map((k, i) => h("span.sw", h("i", {style: {background: LINE_COLORS[i % LINE_COLORS.length]}}), names[k] || k)));
}

// ---------- Live ----------

const F_KEY = "wifilab.live.filters";
function loadFilters() {
  try { return {q: "", band: "all", sec: "all", minRssi: -100, noHidden: false, ...JSON.parse(localStorage.getItem(F_KEY) || "{}")}; }
  catch { return {q: "", band: "all", sec: "all", minRssi: -100, noHidden: false}; }
}

export function apColumns(opts = {}) {
  const v = opts.field || "rssi";
  return [
    {key: "ssid", label: "SSID", render: (a) => a.ssid || h("span.faint", "(hidden)")},
    {key: "bssid", label: "BSSID", render: (a) => h("span.mono", a.bssid)},
    {key: "vendor", label: "Vendor"},
    {key: "band", label: "Band", sortValue: (a) => parseFloat(a.band)},
    {key: "channel", label: "Ch", num: true},
    {key: "width", label: "MHz", num: true},
    {key: v, label: opts.label || "Signal", num: true, render: (a) => signalCell(a[v])},
    {key: "snr", label: "SNR", num: true},
    {key: "security", label: "Security", render: secBadge},
    {key: "phy", label: "PHY", render: (a) => (a.phy ? "11" + a.phy : "")},
  ];
}

function connCard(c) {
  if (!c) return h("div.card", h("b", "Connection: "), h("span.muted", "Wi-Fi interface unavailable"));
  if (!c.associated) return h("div.card", h("b", "Connection: "), h("span.muted", `${c.interface || "Wi-Fi"} not associated`));
  const kv = (k, v) => (v === undefined || v === null || v === "" ? null : h("span.kv", h("span.muted", k + " "), String(v)));
  return h("div.card.conn", h("b", "Connected: "), c.ssid || h("span.faint", "(SSID hidden by macOS)"), " ",
    kv("BSSID", c.bssid), kv("RSSI", `${c.rssi} dBm`), kv("noise", `${c.noise} dBm`), kv("SNR", c.snr !== null ? `${c.snr} dB` : null),
    kv("ch", `${c.channel} (${BAND_NAME[c.band] || c.band}, ${c.width} MHz)`), kv("Tx rate", `${c.tx_rate} Mbps`),
    kv("MCS", c.mcs), kv("PHY", c.phy ? "11" + c.phy : ""), kv("security", c.security), kv("country", c.country));
}

export async function livePage() {
  let f = loadFilters(), sort = {key: "rssi", dir: -1}, sel = null, timer = 0, last = null;
  const conn = h("div"), issuesBox = h("div"), tbl = h("div"), detail = h("div"), count = h("span.small.muted");
  const q = h("input", {type: "search", placeholder: "Filter: SSID, BSSID, vendor, channel", value: f.q, "aria-label": "Filter"});
  const band = h("select", {"aria-label": "Band"}, [["all", "All bands"], ["2.4", "2.4 GHz"], ["5", "5 GHz"], ["6", "6 GHz"]].map(([v, l]) => h("option", {value: v}, l)));
  const sec = h("select", {"aria-label": "Security"}, [["all", "Any security"], ["open", "Open"], ["wep", "WEP"], ["wpa1", "WPA (TKIP)"],
    ["wpa2", "WPA2"], ["wpa3", "WPA3"], ["ent", "Enterprise"], ["owe", "OWE"]].map(([v, l]) => h("option", {value: v}, l)));
  const minR = h("input", {type: "range", min: -100, max: -40, step: 1, value: f.minRssi, "aria-label": "Minimum signal"});
  const minL = h("span.small", `>= ${f.minRssi} dBm`);
  const noHid = h("input", {type: "checkbox", checked: f.noHidden, id: "nohid"});
  band.value = f.band; sec.value = f.sec;
  const save = () => { try { localStorage.setItem(F_KEY, JSON.stringify(f)); } catch { /* storage blocked */ } };
  const changed = () => {
    f = {q: q.value, band: band.value, sec: sec.value, minRssi: +minR.value, noHidden: noHid.checked};
    minL.textContent = `>= ${f.minRssi} dBm`;
    save(); render();
  };
  q.oninput = debounce(changed, 150); band.onchange = changed; sec.onchange = changed; minR.oninput = changed; noHid.onchange = changed;
  const scanBtn = h("button.b", {type: "button", onclick: async () => {
    scanBtn.disabled = true; scanBtn.textContent = "Scanning...";
    try { last = await api("/api/scan", {method: "POST"}); render(); } catch (e) { toast(e.message, "err"); }
    scanBtn.disabled = false; scanBtn.textContent = "Scan now";
  }}, "Scan now");
  const runBtn = h("button.b", {type: "button", onclick: async () => {
    const r = await api("/api/settings", {method: "PUT", json: {running: !(last && last.state.running)}});
    if (last) last.state.running = r.running;
    runBtn.textContent = r.running ? "Pause" : "Resume";
  }}, "Pause");

  function render() {
    if (!last) return;
    const sc = last.scan;
    conn.replaceChildren(connCard(sc.connection));
    runBtn.textContent = last.state.running ? "Pause" : "Resume";
    if (!sc.ok && sc.error) { tbl.replaceChildren(h("div.notice.err", sc.error)); count.textContent = ""; return; }
    const rows = filterAps(sc.aps, f);
    count.textContent = `${rows.length} of ${sc.aps.length} BSSIDs shown` + (sc.anonymous ? `, ${sc.anonymous} without SSID/BSSID (Location Services)` : "")
      + `, scanned ${ago(sc.ts)}`;
    tbl.replaceChildren(rows.length ? table(apColumns(), rows, {sort, onSort: (x) => { sort = x; }, onRow: (a) => { sel = a.bssid; showDetail(); },
      rowClass: (a) => (a.bssid === sel ? ".sel" : "")}) : empty(sc.aps.length ? "No network matches the filter." : "No networks in the last scan."));
    const iss = last.issues;
    issuesBox.replaceChildren(iss.length ? h("div.row.wrap", h("span.small.muted", "In range: "),
      iss.map((i) => badge(i.title, i.severity === "high" ? "err" : i.severity === "medium" ? "warn" : ""))) : null);
  }

  async function showDetail() {
    render();
    const a = last && last.scan.aps.find((x) => x.bssid === sel);
    if (!sel) { detail.replaceChildren(); return; }
    const hist = await api(`/api/history?bssids=${enc(sel)}&minutes=30`);
    const kv = (k, v) => h("tr", h("th", k), h("td", v ?? ""));
    detail.replaceChildren(h("div.card",
      h("div.row", h("h3", a ? (a.ssid || "(hidden)") : sel), h("span.spacer"),
        h("a.b.small", {href: `#/find/${enc(sel)}`}, "Find this AP"), h("button.b.small", {type: "button", onclick: () => { sel = null; showDetail(); }}, "Close")),
      a ? h("table.kvt", kv("BSSID", a.bssid), kv("Vendor", a.vendor || "unknown"), kv("Channel", `${a.channel} (${BAND_NAME[a.band]}, ${a.width} MHz, centre ${a.center}, ${a.freq} MHz)`),
        kv("Signal", `${a.rssi} dBm (${a.quality})`), kv("Noise / SNR", a.noise ? `${a.noise} dBm / ${a.snr} dB` : ""),
        kv("Security", a.security), kv("PHY modes", (a.phy_modes || []).map((m) => "11" + m).join(", ")),
        kv("Country", a.country), kv("Beacon interval", a.beacon_ms ? `${a.beacon_ms} TU` : ""), kv("Ad hoc (IBSS)", a.ibss ? "yes" : "no"))
        : h("p.muted", "Not in the last scan."),
      h("h4", "Signal, last 30 min"), signalChart({[sel]: hist.series[sel] || []}, {interval: last.state.interval})));
  }

  async function refresh() {
    try { last = await api("/api/live"); render(); } catch (e) { tbl.replaceChildren(h("div.notice.err", e.message)); }
  }
  await refresh();
  timer = setInterval(refresh, 3000);
  const el = h("div.stack", h("div.row", h("h2", "Live scan"), h("span.spacer"), scanBtn, runBtn), conn, issuesBox,
    h("div.row.wrap.filters", q, band, sec, h("label.row.small", minR, minL), h("label.row.small", {for: "nohid"}, noHid, "Hide hidden"), h("span.spacer"), count),
    tbl, detail);
  return {el, cleanup: () => clearInterval(timer)};
}

// ---------- Channels ----------

export function channelSection(aps, channels, rec, band, opts = {}) {
  const list = aps.filter((a) => a.band === band);
  if (!list.length && band !== "2.4") return null;
  const r = rec[band] || {};
  let recText = "";
  if (band === "2.4" && r.channel) recText = `Least loaded of 1/6/11: channel ${r.channel}`;
  if (band === "5" && r.channel) {
    recText = `Least loaded non-DFS: channel ${r.channel}` + (r.block80 ? `; 80 MHz block ${r.block80.channels[0]}-${r.block80.channels[3]}` : "")
      + (r.block40 ? `; 40 MHz ${r.block40.channels.join("+")}` : "") + (r.channel_dfs ? `; with DFS: ${r.channel_dfs}` : "");
  }
  if (band === "6" && r.channel) recText = `Least loaded PSC channel: ${r.channel}`;
  const recCh = band === "5" && r.block80 ? r.block80.channels : r.channel ? [r.channel] : [];
  return h("section.card",
    h("div.row", h("h3", BAND_NAME[band]), h("span.small.muted", `${list.length} BSSIDs`), h("span.spacer"), recText ? badge(recText, "ok") : null),
    spectrumSvg(aps, band, {onPick: opts.onPick, height: opts.height}),
    legend(list),
    h("div.small.muted", "Channel load: every AP adds max(1, RSSI + 100) to each channel it overlaps (green bar = recommended)."),
    loadBars(channels[band], {recommend: recCh, all: band === "2.4"}));
}

export async function channelsPage() {
  let src = "live", minutes = 60;
  const body = h("div.stack");
  const srcSel = h("select", {"aria-label": "Source"}, h("option", {value: "live"}, "Last scan"), h("option", {value: "60"}, "Last 60 min (strongest seen)"),
    h("option", {value: "1440"}, "Last 24 h (strongest seen)"));
  srcSel.onchange = () => { src = srcSel.value === "live" ? "live" : "hist"; minutes = +srcSel.value || 60; load(); };
  let timer = 0;
  async function load() {
    try {
      let aps, channels, rec, coch, adj;
      if (src === "live") {
        const r = await api("/api/live");
        ({channels, recommend: rec, cochannel: coch, adjacent: adj} = r);
        aps = r.scan.aps;
      } else {
        const r = await api(`/api/report?minutes=${minutes}`);
        aps = r.inventory.map((a) => ({...a, rssi: a.rssi_max}));
        ({channels, recommend: rec, cochannel: coch} = r);
        adj = [];
      }
      const sections = ["2.4", "5", "6"].map((b) => channelSection(aps, channels, rec, b, {onPick: (a) => { location.hash = `#/find/${enc(a.bssid)}`; }}));
      body.replaceChildren(...sections.filter(Boolean),
        h("section.card", h("h3", "Interference"),
          coch.length ? table([{key: "band", label: "Band"}, {key: "channel", label: "Channel", num: true}, {key: "radios", label: "Radios", num: true},
            {key: "ssids", label: "SSIDs", render: (e) => e.ssids.join(", ") || "(hidden)"}], coch, {sort: {key: "radios", dir: -1}})
            : h("p.muted", "No channel is shared by two or more strong radios."),
          adj.length ? h("div", h("h4", "Partially overlapping pairs"), table([{key: "band", label: "Band"},
            {key: "a_ssid", label: "AP A", render: (x) => `${x.a_ssid || "(hidden)"} ch ${x.a_channel}`},
            {key: "b_ssid", label: "AP B", render: (x) => `${x.b_ssid || "(hidden)"} ch ${x.b_channel}`}], adj)) : null));
    } catch (e) { body.replaceChildren(h("div.notice.err", e.message)); }
  }
  await load();
  timer = setInterval(() => { if (src === "live") load(); }, 5000);
  return {el: h("div.stack", h("div.row", h("h2", "Channels"), h("span.spacer"), srcSel), body), cleanup: () => clearInterval(timer)};
}

// ---------- Find AP ----------

export function trend(vals) {
  if (vals.length < 4) return 0;
  const n = Math.min(6, Math.floor(vals.length / 2));
  const a = vals.slice(-n), b = vals.slice(-2 * n, -n);
  const avg = (x) => x.reduce((s0, v) => s0 + v, 0) / x.length;
  const d = avg(a) - avg(b);
  return Math.abs(d) < 1.5 ? 0 : d > 0 ? 1 : -1;
}

export async function findPage(bssid) {
  let stop = false;
  const vals = [];
  const live = await api("/api/live");
  const pick = h("select", {"aria-label": "Access point"}, h("option", {value: ""}, "Choose an access point..."),
    [...live.scan.aps].sort((a, b) => sig(b) - sig(a)).map((a) => h("option", {value: a.bssid}, `${a.ssid || "(hidden)"}  ${a.bssid}  ch ${a.channel}  ${a.rssi} dBm`)));
  pick.value = bssid || "";
  pick.onchange = () => { location.hash = `#/find/${enc(pick.value)}`; };
  const big = h("div.bignum", "--"), word = h("div.bigword", ""), meter = h("div.meter", h("i")), info = h("div.small.muted"), arrow = h("div.trend", "");
  const spark = h("div");
  const el = h("div.stack", h("div.row", h("h2", "Find AP"), pick),
    bssid ? h("div.card.find", big, word, arrow, meter, info, spark) : h("p.muted", "Pick an access point, then walk: the number climbs as you get closer. Scans run back to back while this page is open."));
  async function loop() {
    while (!stop && bssid) {
      const t0 = Date.now();
      try {
        const r = await api("/api/scan", {method: "POST"});
        if (stop) break;
        const a = r.scan.aps.find((x) => x.bssid === bssid);
        if (a) {
          vals.push(a.rssi);
          if (vals.length > 60) vals.shift();
          big.textContent = `${a.rssi} dBm`;
          big.className = "bignum " + (QUALITY_KIND[a.quality] || "");
          word.textContent = a.quality;
          meter.firstChild.style.width = Math.max(2, Math.min(100, (a.rssi + 95) * 100 / 65)) + "%";
          const t = trend(vals);
          arrow.textContent = t > 0 ? "getting stronger" : t < 0 ? "getting weaker" : "steady";
          info.textContent = `${a.ssid || "(hidden)"}  ${a.bssid}  ch ${a.channel} (${BAND_NAME[a.band]})  ${a.security}  `
            + `min ${Math.min(...vals)} / avg ${Math.round(vals.reduce((x, y) => x + y, 0) / vals.length)} / max ${Math.max(...vals)} dBm over ${vals.length} scans`;
        } else {
          big.textContent = "--"; big.className = "bignum err"; word.textContent = "not heard in the last scan";
        }
        const now = Date.now() / 1000;
        spark.replaceChildren(signalChart({[bssid]: vals.map((v, i) => ({ts: now - (vals.length - 1 - i) * 4, rssi: v}))}, {height: 160, interval: 4}));
      } catch (e) {
        info.textContent = e.message;
        await new Promise((res) => setTimeout(res, 3000));
      }
      // A real scan takes seconds anyway; this only keeps a fast backend from spinning.
      await new Promise((res) => setTimeout(res, Math.max(0, 1500 - (Date.now() - t0))));
    }
  }
  return {el, mounted: () => loop(), cleanup: () => { stop = true; }};
}

// ---------- History ----------

export async function historyPage() {
  let minutes = 60;
  const chosen = new Set();
  const win = h("select", {"aria-label": "Window"}, [[15, "15 min"], [60, "1 hour"], [360, "6 hours"], [1440, "24 hours"], [10080, "7 days"]].map(([v, l]) => h("option", {value: v}, l)));
  win.value = "60";
  win.onchange = () => { minutes = +win.value; load(); };
  const tbl = h("div"), chart = h("div"), info = h("span.small.muted");
  const csv = h("a.b.small", {href: "/api/report.csv?minutes=60"}, "Export CSV");
  const clear = h("button.b.small.danger", {type: "button", onclick: async () => {
    if (!confirm("Delete the whole scan history? Survey projects are kept.")) return;
    await api("/api/history", {method: "DELETE"});
    load();
  }}, "Clear history");
  async function drawChart(interval) {
    if (!chosen.size) { chart.replaceChildren(h("p.muted", "Tick up to 8 access points to plot their signal over the window.")); return; }
    const r = await api(`/api/history?bssids=${[...chosen].map(enc).join(",")}&minutes=${minutes}`);
    chart.replaceChildren(signalChart(r.series, {interval, t0: Date.now() / 1000 - minutes * 60}), chartLegend([...chosen]));
  }
  async function load() {
    csv.href = `/api/report.csv?minutes=${minutes}`;
    const [inv, st] = await Promise.all([api(`/api/inventory?minutes=${minutes}`), api("/api/settings")]);
    info.textContent = `${inv.aps.length} BSSIDs in ${inv.scans} scans`;
    const cols = [{key: "pick", label: "", render: (a) => {
      const cb = h("input", {type: "checkbox", checked: chosen.has(a.bssid), "aria-label": "Plot " + a.bssid});
      cb.onclick = (ev) => ev.stopPropagation();
      cb.onchange = () => {
        if (cb.checked && chosen.size >= 8) { cb.checked = false; toast("At most 8 lines", "info"); return; }
        if (cb.checked) chosen.add(a.bssid); else chosen.delete(a.bssid);
        drawChart(st.interval);
      };
      return cb;
    }}, ...apColumns({field: "rssi_max", label: "Max"}).filter((c) => c.key !== "snr"),
    {key: "rssi_avg", label: "Avg", num: true}, {key: "rssi_min", label: "Min", num: true},
    {key: "seen_count", label: "Seen", num: true}, {key: "last_seen", label: "Last seen", render: (a) => ago(a.last_seen)}];
    tbl.replaceChildren(inv.aps.length ? table(cols, inv.aps, {sort: {key: "rssi_max", dir: -1}}) : empty("No scans in this window."));
    drawChart(st.interval);
  }
  await load();
  return {el: h("div.stack", h("div.row", h("h2", "History"), win, info, h("span.spacer"), csv, clear), chart, tbl)};
}

// ---------- Settings ----------

export async function settingsPage() {
  const [st, info] = await Promise.all([api("/api/settings"), api("/api/info")]);
  const iv = h("input", {type: "number", min: 5, max: 3600, value: st.interval, style: {width: "90px"}, "aria-label": "Scan interval, s"});
  const run = h("input", {type: "checkbox", checked: st.running, id: "run"});
  const save = h("button.b.primary", {type: "button", onclick: async () => {
    try {
      await api("/api/settings", {method: "PUT", json: {interval: +iv.value, running: run.checked}});
      toast("Saved", "ok");
    } catch (e) { toast(e.message, "err"); }
  }}, "Save");
  const loc = info.location;
  const locBtn = h("button.b.small", {type: "button", onclick: async () => {
    const r = await api("/api/location/request", {method: "POST"});
    toast(r.message || r.status, "info", 10000);
  }}, "Request Location Services");
  const kv = (k, v) => h("tr", h("th", k), h("td", v));
  return {el: h("div.stack", h("h2", "Settings"),
    h("div.card", h("h3", "Background scanning"),
      h("div.row", h("label.row", "Scan every ", iv, " s"), h("label.row", {for: "run"}, run, "Scan in the background"), save),
      h("p.small.muted", "Each scan takes a few seconds of radio time; very short intervals can slow the Mac's own Wi-Fi. "
        + `History is kept ${st.retention_days} days (WIFILAB_RETENTION_DAYS).`)),
    h("div.card", h("h3", "About"), h("table.kvt",
      kv("Version", info.version), kv("Scanner", info.backend), kv("Data", info.data_dir),
      kv("Location Services", `${loc.status}${loc.authorized ? "" : " (SSIDs and BSSIDs are hidden by macOS)"}`),
      kv("Last scan", fmtTime(info.state.last_ts))), loc.needed ? locBtn : null,
    h("p.small.muted", "Vendor names come from a bundled subset of the IEEE OUI list. Put more as \"prefix,vendor\" lines into "
      + "oui.csv in the data folder."))),
  };
}
