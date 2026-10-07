// Survey report: printable page (browser print -> PDF), Word export (heatmaps sent as PNGs), CSV and JSON.
import {h, api, enc, toast, table, fmtTime, fmtM, download, badge, empty} from "./lib.js";
import {VIEWS, buildScene, engGeom, renderEng, walkOrder} from "./heat.js";
import {spectrumSvg, loadBars, legend, BAND_NAME} from "./spectrum.js";
import {apColumns} from "./live.js";

const SEV = {high: "err", medium: "warn", low: "", info: ""};

// Heatmaps a project report shows: [{kind, target, title}]
export function reportViews(proj, ssid) {
  const out = [{kind: "rssi", target: {type: "best"}, title: "Signal: strongest AP at each point"}];
  if (ssid) out.push({kind: "rssi", target: {type: "ssid", value: ssid}, title: `Signal: SSID ${ssid}`});
  const t = ssid ? {type: "ssid", value: ssid} : {type: "best"}, who = ssid ? `SSID ${ssid}` : "strongest AP";
  out.push({kind: "snr", target: t, title: `SNR: ${who}`}, {kind: "count", target: t, title: `AP count (radios at -75 dBm or better): ${ssid ? who : "all networks"}`},
    {kind: "overlap", target: t, title: `Channel overlap (co-channel radios at -82 dBm or better): ${who}`});
  if ((proj.aps || []).length) out.push({kind: "serving", target: t, title: "Serving AP zones (placed APs)"});
  // Same list as heatmap.report_views() on the server.
  for (const ap of (proj.aps || []).filter((a) => (a.bssids || []).length).slice(0, MAX_AP_VIEWS)) {
    out.push({kind: "rssi", target: {type: "set", bssids: new Set(ap.bssids.map((b) => b.toLowerCase()))}, title: `Signal: placed AP ${ap.name || ap.id}`});
  }
  return out;
}

const MAX_AP_VIEWS = 6;

// Area of the heatmap: the floor plan, or (no plan) a box around the points.
export function reportArea(proj) {
  const plan = proj.plan || {};
  if (Number.isFinite(plan.width_m) && Number.isFinite(plan.length_m)) return {wM: plan.width_m, lM: plan.length_m, items: plan.items || []};
  const pts = proj.points || [];
  const w = Math.max(0, ...pts.map((p) => p.x_m || 0)) + 2, l = Math.max(0, ...pts.map((p) => p.y_m || 0)) + 2;
  return {wM: Math.max(2, Math.ceil(w)), lM: Math.max(2, Math.ceil(l)), items: []};
}

function heatCanvas(proj, v, bg) {
  const {wM, lM, items} = reportArea(proj);
  const geom = engGeom(wM, lM, 860, 560);
  const sc = buildScene(proj.points || [], proj.aps || [], v, wM, lM);
  const byP = new Map(sc.pts.map((q) => [q.p, q]));
  const scene = {geom, wM, lM, img: sc.img, bg, plan: items, pts: sc.pts, path: walkOrder(proj.points || []).map((p) => byP.get(p)),
    sel: null, title: v.title, unit: sc.unit, stops: sc.stops, zones: sc.zones, labels: false,
    aps: (proj.aps || []).map((ap) => ({...ap, color: null}))};
  const c = document.createElement("canvas");
  c.width = geom.W * 2; c.height = geom.H * 2;
  c.style.width = geom.W + "px"; c.style.maxWidth = "100%";
  const g = c.getContext("2d");
  g.scale(2, 2);
  renderEng(g, scene);
  c.dataset.title = v.title;
  return c;
}

function loadImage(url) {
  return new Promise((res) => {
    if (!url) { res(null); return; }
    const im = new Image();
    im.onload = () => res(im);
    im.onerror = () => res(null);
    im.src = url;
  });
}

export async function reportPage(id) {
  const live = id === "live";
  let minutes = 60;
  const proj = live ? null : await api(`/api/projects/${enc(id)}`);
  let ssid = proj ? proj.target_ssid || "" : "";
  const out = h("div.report");
  const ssidSel = h("select", {"aria-label": "Coverage SSID"});
  const winSel = h("select", {"aria-label": "Window"}, [[60, "Last hour"], [360, "Last 6 hours"], [1440, "Last 24 hours"], [10080, "Last 7 days"]]
    .map(([v, l]) => h("option", {value: v}, l)));
  winSel.onchange = () => { minutes = +winSel.value; build(); };
  ssidSel.onchange = async () => {
    ssid = ssidSel.value;
    await api(`/api/projects/${enc(id)}`, {method: "PATCH", json: {target_ssid: ssid}});
    build();
  };
  const q = () => (live ? `minutes=${minutes}` : `project=${enc(id)}&ssid=${enc(ssid)}`);
  const docxBtn = h("button.b", {type: "button", onclick: async () => {
    docxBtn.disabled = true;
    try {
      const images = [...out.querySelectorAll("canvas")].map((c) => ({title: c.dataset.title || "", png: c.toDataURL("image/png")}));
      const r = await api("/api/report.docx", {json: {project: live ? null : id, ssid, minutes, images}, raw: true, timeout: 120000});
      download(`${(proj ? proj.name : "wifi-live")}-report.docx`.replace(/[^A-Za-z0-9._-]+/g, "_"), await r.blob());
    } catch (e) { toast(e.message, "err"); }
    docxBtn.disabled = false;
  }}, "Word (.docx)");
  const csvA = h("a.b", {href: "#"}, "AP CSV");
  const htmlA = h("a.b", {href: "#", title: "Standalone HTML report with the heatmaps embedded"}, "HTML");
  const jsonBtn = h("button.b", {type: "button", onclick: async () => {
    const d = await api(`/api/report?${q()}`);
    download(`${(proj ? proj.name : "wifi-live")}-report.json`.replace(/[^A-Za-z0-9._-]+/g, "_"), JSON.stringify(d, null, 1), "application/json");
  }}, "JSON");
  const toolbar = h("div.row.noprint", h("h2", "Report"), live ? winSel : h("label.row.small", "Coverage SSID ", ssidSel), h("span.spacer"),
    h("button.b.primary", {type: "button", onclick: () => window.print()}, "Print / PDF"), docxBtn, htmlA, csvA, jsonBtn,
    proj ? h("a.b", {href: `#/survey/${enc(id)}`}, "Back to survey") : null);

  async function build() {
    out.replaceChildren(h("div.empty", "Building the report..."));
    const d = await api(`/api/report?${q()}`);
    csvA.href = `/api/report.csv?${live ? `minutes=${minutes}` : `project=${enc(id)}`}`;
    htmlA.href = `/api/report.html?${q()}`;
    if (proj) {
      const inv = await api(`/api/projects/${enc(id)}/inventory`);
      ssidSel.replaceChildren(h("option", {value: ""}, "(strongest AP)"), ...inv.ssids.map((s) => h("option", {value: s}, s)));
      ssidSel.value = ssid;
    }
    const s = d.summary, m = d.meta || {};
    const kpi = (v, l) => h("div.kpi", h("b", String(v)), h("span", l));
    const parts = [
      h("header", h("h1", `Wi-Fi survey report: ${d.title}`),
        h("div.small.muted", `Generated ${fmtTime(d.generated)} by WiFiLab ${d.version}. `
          + (live ? `Source: live scan history, last ${m.minutes} min (${m.scans} scans).` : `Source: survey project${m.imported_from === "fieldtab" ? " (imported from FieldTab)" : ""}`
            + (m.width_m ? `, area ${fmtM(m.width_m)} x ${fmtM(m.length_m)} m` : "") + ".")
          + (d.ssid ? ` Coverage target: ${d.ssid}.` : "")),
        m.note ? h("p", m.note) : null),
      h("div.kpis", kpi(s.bssids, "BSSIDs"), kpi(s.radios, "radios"), kpi(s.ssids, "SSIDs"), kpi(s.bands["2.4"], "on 2.4 GHz"), kpi(s.bands["5"], "on 5 GHz"),
        kpi(s.bands["6"], "on 6 GHz"), s.points ? kpi(s.points, "points") : null, kpi(s.issues.high, "high issues"), kpi(s.issues.medium, "medium issues")),
      h("section", h("h2", "Issues found"), d.issues.length ? h("ul.issues", d.issues.map((i) => h("li", badge(i.severity, SEV[i.severity]), " ", h("b", i.title), h("div.small", i.detail))))
        : h("p", "No issues found.")),
    ];
    // The live report shows the heatmaps of the latest survey with measured points (d.coverage).
    const cproj = proj || (d.coverage ? await api(`/api/projects/${enc(d.coverage.project)}`) : null);
    if (cproj && (cproj.points || []).length) {
      const bg = await loadImage(cproj.bg);
      const views = reportViews(cproj, proj ? d.ssid : (d.coverage_ssid || ""));
      const area = reportArea(cproj);
      parts.push(h("section", h("h2", "Coverage"),
        h("p.small.muted", (proj ? "" : `Survey "${cproj.name}" (latest survey with measured points). `)
          + `${cproj.points.length} measured points, ${(cproj.aps || []).length} placed APs, `
          + (cproj.plan && cproj.plan.width_m ? `floor plan ${fmtM(area.wM)} x ${fmtM(area.lM)} m. ` : `no floor plan: drawn on a ${fmtM(area.wM)} x ${fmtM(area.lM)} m area around the points. `)
          + "Heatmaps interpolate between points and fade out 3-5 m away from them; dark points heard nothing of the selected network.",
          proj ? null : [" ", h("a.noprint", {href: `#/report/${enc(cproj.id)}`}, "Full survey report")]),
        ...views.map((v) => h("figure", heatCanvas(cproj, v, bg), h("figcaption", v.title)))));
    } else {
      parts.push(h("section", h("h2", "Coverage"), empty(proj ? "No measured points in this survey yet: the heatmaps appear here once points are measured (Measure & heatmaps)."
        : "No survey with measured points yet: create a survey and measure points to get coverage heatmaps in the report.")));
    }
    const aps = d.inventory.map((a) => ({...a, rssi: a.rssi_max}));
    const r = d.recommend;
    const recs = [];
    if (r["2.4"].channel) recs.push(`2.4 GHz: least loaded of 1/6/11 is channel ${r["2.4"].channel}.`);
    if (r["5"].channel) recs.push(`5 GHz: least loaded non-DFS channel ${r["5"].channel}` + (r["5"].block80 ? `, 80 MHz block ${r["5"].block80.channels[0]}-${r["5"].block80.channels[3]}` : "")
      + (r["5"].channel_dfs ? `; including DFS: ${r["5"].channel_dfs}` : "") + ".");
    if (r["6"].channel && s.bands["6"]) recs.push(`6 GHz: least loaded PSC channel ${r["6"].channel}.`);
    parts.push(h("section", h("h2", "Channel plan"), h("ul", recs.map((x) => h("li", x))),
      ...["2.4", "5", "6"].filter((b) => aps.some((a) => a.band === b)).map((b) => h("div.band", h("h3", BAND_NAME[b]),
        spectrumSvg(aps, b, {height: 240}), legend(aps.filter((a) => a.band === b)),
        loadBars(d.channels[b], {all: b === "2.4", recommend: b === "5" && r["5"].block80 ? r["5"].block80.channels : [r[b].channel]}))),
      d.cochannel.length ? h("div", h("h3", "Shared channels"), table([{key: "band", label: "Band"}, {key: "channel", label: "Channel", num: true},
        {key: "radios", label: "Radios", num: true}, {key: "ssids", label: "SSIDs", render: (e) => e.ssids.join(", ") || "(hidden)"}], d.cochannel)) : null));
    parts.push(h("section", h("h2", "Access point inventory"),
      table([...apColumns({field: "rssi_max", label: "Max dBm"}).filter((c) => c.key !== "snr"), {key: "rssi_avg", label: "Avg", num: true},
        {key: "seen_count", label: live ? "Scans" : "Points", num: true}], d.inventory, {sort: {key: "rssi_max", dir: -1}})));
    out.replaceChildren(...parts);
  }
  await build();
  return {el: h("div.stack", toolbar, out)};
}
