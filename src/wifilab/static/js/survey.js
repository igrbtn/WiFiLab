// Surveys: project list and import; the survey workspace (measure on the plan, heatmaps, placed APs, plan editor,
// AP inventory).
import {h, api, enc, toast, table, empty, fmtTime, fmtM, download, badge, copyText} from "./lib.js";
import {VIEWS, buildScene, engGeom, renderEng, toMetres, apGroups, groupLabel, shortMac, walkOrder, AP_R, apColor} from "./heat.js";
import {planEditor} from "./plans.js";
import {apColumns} from "./live.js";

// ---------- list ----------

export async function surveysPage() {
  const list = await api("/api/projects");
  const name = h("input", {type: "text", placeholder: "New survey name, e.g. Floor 2 east", maxlength: 80, "aria-label": "Survey name"});
  const create = h("button.b.primary", {type: "button", onclick: async () => {
    const p = await api("/api/projects", {json: {name: name.value || "Untitled survey"}});
    location.hash = `#/survey/${enc(p.id)}/plan`;
  }}, "New survey");
  const file = h("input", {type: "file", accept: ".json,.csv", multiple: true, "aria-label": "Import files"});
  file.onchange = async () => {
    for (const f of file.files) {
      try {
        const r = await api(`/api/import?name=${enc(f.name)}`, {body: await f.arrayBuffer()});
        toast(`Imported ${f.name} as ${r.kind}: ${r.project.points} points, ${r.project.aps} APs`, "ok", 6000);
      } catch (e) { toast(e.message, "err", 9000); }
    }
    location.hash = "#/surveys";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  };
  const cols = [
    {key: "name", label: "Survey", render: (p) => h("a", {href: `#/survey/${enc(p.id)}`}, p.name)},
    {key: "points", label: "Points", num: true},
    {key: "width_m", label: "Area", render: (p) => (p.width_m ? `${fmtM(p.width_m)} x ${fmtM(p.length_m)} m` : "")},
    {key: "source", label: "Source", render: (p) => (p.source === "fieldtab" ? badge("FieldTab import") : p.source || "")},
    {key: "updated", label: "Updated", render: (p) => fmtTime(p.updated)},
    {key: "x", label: "", render: (p) => h("span.row",
      h("a.b.small", {href: `#/report/${enc(p.id)}`}, "Report"),
      h("button.b.small.danger", {type: "button", onclick: async (ev) => {
        ev.stopPropagation();
        if (!confirm(`Delete survey "${p.name}" with its ${p.points} points?`)) return;
        await api(`/api/projects/${enc(p.id)}`, {method: "DELETE"});
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      }}, "Delete"))},
  ];
  return {el: h("div.stack", h("h2", "Surveys"),
    h("div.card", h("div.row", name, create), h("div.row", {style: {marginTop: "8px"}}, h("span.small", "Import: "), file),
      h("p.small.muted", "Import accepts WiFiLab project exports, FieldTab tablet Wi-Fi exports (wifi-survey JSON/CSV, wifi-map JSON, "
        + "session documents) and fieldtab.plan/1 floor plans.")),
    photoPlanCard(name, !list.length),
    list.length ? table(cols, list, {sort: {key: "updated", dir: -1}}) : empty("No surveys yet: create one, draw or import the floor plan, then click on the plan where you stand to measure."))};
}

// ---------- floor plan from a photo, through any vision LLM ----------

const PHOTO_EXAMPLE = "Main corridor along the long side: 31 m\nRoom 204 (top right): 6.2 m wide\nDoors are 0.9 m";

export function photoPromptQuery(f) {
  const q = new URLSearchParams();
  for (const k of ["known", "notes", "name"]) if ((f[k] || "").trim()) q.set(k, f[k].trim());
  const w = parseFloat(f.width_m), l = parseFloat(f.length_m);
  if (w > 0 && l > 0) { q.set("width_m", String(w)); q.set("length_m", String(l)); }
  return q.toString();
}

function photoPlanCard(nameIn, open = false) {
  const known = h("textarea", {rows: 3, style: {width: "100%", boxSizing: "border-box"}, placeholder: PHOTO_EXAMPLE, "aria-label": "Known real sizes"});
  const wIn = h("input", {type: "number", min: 0.5, max: 100, step: 0.1, placeholder: "optional", style: {width: "90px"}, "aria-label": "Overall width, m"});
  const lIn = h("input", {type: "number", min: 0.5, max: 100, step: 0.1, placeholder: "optional", style: {width: "90px"}, "aria-label": "Overall length, m"});
  const notes = h("input", {type: "text", maxlength: 300, placeholder: "e.g. evacuation plan, ignore the left wing", style: {flex: "1"}, "aria-label": "Notes"});
  const preview = h("pre.small", {style: {whiteSpace: "pre-wrap", maxHeight: "220px", overflow: "auto"}});
  const answer = h("textarea", {rows: 6, style: {width: "100%", boxSizing: "border-box", fontFamily: "monospace"}, placeholder: "Paste the LLM answer here (JSON, with or without ``` fences or text around it)", "aria-label": "LLM answer"});
  const status = h("div.small");
  const form = () => ({known: known.value, width_m: wIn.value, length_m: lIn.value, notes: notes.value, name: nameIn.value});
  const prompt = async () => (await api(`/api/plans/prompt?${photoPromptQuery(form())}`)).prompt;
  let timer = null;
  const refresh = () => { clearTimeout(timer); timer = setTimeout(async () => { preview.textContent = await prompt(); }, 250); };
  for (const el of [known, wIn, lIn, notes, nameIn]) el.addEventListener("input", refresh);
  refresh();
  const copy = h("button.b.primary", {type: "button", onclick: async () => {
    const text = await prompt();
    preview.textContent = text;
    if (await copyText(text)) toast("Prompt copied: paste it into the LLM chat together with the photo", "ok", 6000);
    else toast("Clipboard blocked: select the prompt in the preview and copy it", "info", 6000);
  }}, "Copy prompt");
  const createBtn = h("button.b.primary", {type: "button", onclick: async () => {
    status.textContent = "";
    if (!answer.value.trim()) { status.textContent = "Paste the LLM answer first."; status.className = "small warn"; return; }
    let plan;
    try {
      plan = (await api("/api/plans/validate", {body: answer.value})).plan;
    } catch (e) {
      status.textContent = "The answer is not a usable plan: " + e.message + "\nAsk the LLM to fix exactly these points and paste the new answer.";
      status.className = "small err";
      status.style.whiteSpace = "pre-wrap";
      return;
    }
    const p = await api("/api/projects", {json: {name: nameIn.value || plan.name || "Survey from a photo", plan}});
    toast(`Survey created: ${fmtM(plan.width_m)} x ${fmtM(plan.length_m)} m, ${plan.items.length} lines. Check it against the photo.`, "ok", 7000);
    location.hash = `#/survey/${enc(p.id)}/plan`;
  }}, "Create survey from this plan");
  return h("details.card", {open},
    h("summary", h("b", "Floor plan from a photo (Claude, ChatGPT, Gemini or any vision LLM)")),
    h("ol.small",
      h("li", "Take a photo of the floor plan (evacuation plan on the wall, a drawing, a scan). Shoot straight on, the whole plan in the frame, no glare."),
      h("li", "Write the real sizes you know below: at least one long one (a corridor, the building side). One measured length sets the scale for everything; two in different directions are better. A laser meter or the dimension lines on the drawing are fine."),
      h("li", "Copy prompt, open a new chat in the LLM, attach the photo and paste the prompt."),
      h("li", "Copy the whole answer, paste it below and Create survey. Then compare the lines with the photo in Floor plan and fix walls by hand if needed (you can also set the photo as the plan background).")),
    h("div.small", {style: {marginTop: "8px"}}, "Known real sizes, one per line"), known,
    h("div.row", {style: {marginTop: "6px"}}, h("label.row.small", "Whole plan width, m ", wIn), h("label.row.small", "length, m ", lIn), notes),
    h("p.small.muted", "The survey name above is used as the plan name."),
    h("div.row", copy),
    h("details", h("summary.small", "Prompt preview"), preview),
    h("div.small", {style: {marginTop: "8px"}}, "LLM answer"), answer,
    h("div.row", createBtn), status);
}

// ---------- workspace ----------

const VIEW_KEY = "wifilab.survey.view.";

export async function surveyPage(pid, tab = "measure") {
  const proj = await api(`/api/projects/${enc(pid)}`);
  const tabs = [["measure", "Measure & heatmaps"], ["plan", "Floor plan"], ["aps", "Access points"]];
  const title = h("input.titlein", {type: "text", value: proj.name, maxlength: 80, "aria-label": "Survey name"});
  title.onchange = async () => { await api(`/api/projects/${enc(pid)}`, {method: "PATCH", json: {name: title.value}}); toast("Renamed", "ok"); };
  const head = h("div.row", title, h("span.spacer"),
    h("a.b.small", {href: `#/report/${enc(pid)}`}, "Report"),
    h("a.b.small", {href: `/api/projects/${enc(pid)}/export.json`}, "Export JSON"),
    h("a.b.small", {href: `/api/projects/${enc(pid)}/fieldtab-map.json`, title: "FieldTab wifi_map format"}, "Export FieldTab map"));
  const tabBar = h("div.tabs", tabs.map(([k, l]) => h("a" + (k === tab ? ".on" : ""), {href: `#/survey/${enc(pid)}/${k}`}, l)));
  let body;
  if (tab === "plan") body = planEditor(proj, {onSaved: () => {}});
  else if (tab === "aps") body = await apsTab(proj);
  else body = measureTab(proj);
  return {el: h("div.stack", head, tabBar, body.el || body), cleanup: body.cleanup, mounted: body.mounted};
}

async function apsTab(proj) {
  const inv = await api(`/api/projects/${enc(proj.id)}/inventory`);
  const cols = [...apColumns({field: "rssi_max", label: "Max"}).filter((c) => c.key !== "snr"),
    {key: "rssi_avg", label: "Avg", num: true}, {key: "rssi_min", label: "Min", num: true}, {key: "seen_count", label: "Points", num: true}];
  return h("div.stack", h("p.small.muted", `${inv.aps.length} BSSIDs heard at the survey points` + (proj.survey_aps && proj.survey_aps.length ? " plus the imported AP list" : "") + "."),
    inv.aps.length ? table(cols, inv.aps, {sort: {key: "rssi_max", dir: -1}}) : empty("No access points yet: measure some points."));
}

function measureTab(proj) {
  const plan = proj.plan;
  const wM = plan.width_m, lM = plan.length_m;
  let points = proj.points || [];
  let placed = proj.aps || [];
  let groups = apGroups(points);
  let mode = "measure", sel = null, selAp = null, pending = null, drag = null, showVals = false, bgImg = null;
  let view = {kind: "rssi", target: "best", group: []};
  try { view = {...view, ...JSON.parse(localStorage.getItem(VIEW_KEY + proj.id) || "{}")}; } catch { /* nothing saved */ }

  const canvas = h("canvas.heat", {tabindex: 0, role: "img", "aria-label": "Survey heatmap"});
  const wrap = h("div.heatwrap", canvas);
  const pop = h("div.pop", {role: "dialog", style: {display: "none"}});
  wrap.append(pop);
  const info = h("div.small.muted");
  const side = h("div");
  const modeBtns = [["measure", "Measure"], ["select", "Select / move"], ["place", "Place AP"]].map(([m, l]) =>
    h("button.b.small", {type: "button", onclick: () => setMode(m)}, l));
  const viewSel = h("select", {"aria-label": "Heatmap"}, Object.entries(VIEWS).map(([k, v]) => h("option", {value: k}, v.label)));
  const targetSel = h("select", {"aria-label": "Network or AP"});
  const groupBox = h("span.row.small.wrap");
  const valsCb = h("input", {type: "checkbox", id: "vals"});
  const pngBtn = h("button.b.small", {type: "button", onclick: () => {
    const sc = scene(), out = document.createElement("canvas");
    out.width = sc.geom.W * 2; out.height = sc.geom.H * 2;
    const g = out.getContext("2d"); g.scale(2, 2); renderEng(g, sc);
    out.toBlob((b) => download(`${proj.name}-${view.kind}.png`.replace(/[^A-Za-z0-9._-]+/g, "_"), b, "image/png"));
  }}, "Save PNG");
  if (proj.bg) { const im = new Image(); im.onload = () => { bgImg = im; draw(); }; im.src = proj.bg; }

  function saveView() { try { localStorage.setItem(VIEW_KEY + proj.id, JSON.stringify(view)); } catch { /* storage blocked */ } }

  function ssidList() {
    const best = new Map();
    for (const p of points) for (const a of p.aps || []) if (a.ssid) best.set(a.ssid, Math.max(best.get(a.ssid) ?? -999, a.rssi));
    return [...best.entries()].sort((a, b) => b[1] - a[1]);
  }
  function rebuildTargets() {
    groups = apGroups(points);
    targetSel.replaceChildren(h("option", {value: "best"}, "Strongest AP at each point"),
      h("optgroup", {label: "Network (SSID)"}, ssidList().map(([s, b]) => h("option", {value: "ssid:" + s}, `${s}  (best ${b} dBm)`))),
      placed.length ? h("optgroup", {label: "Placed APs"}, placed.map((ap) => h("option", {value: "p:" + ap.id}, ap.name || "(unnamed)"))) : null,
      placed.length ? h("option", {value: "group"}, "AP group (tick placed APs)") : null,
      groups.length ? h("optgroup", {label: "Radios heard (by MAC)"}, groups.map((g) => h("option", {value: "g:" + g.id}, `${groupLabel(g)}  (${g.bssids.length} BSSID, best ${g.best})`))) : null);
    if (![...targetSel.options].some((o) => o.value === view.target)) view.target = "best";
    targetSel.value = view.target;
    view.group = view.group.filter((id) => placed.some((a) => a.id === id));
    groupBox.replaceChildren(...placed.map((ap) => {
      const cb = h("input", {type: "checkbox", checked: view.group.includes(ap.id)});
      cb.onchange = () => { view.group = cb.checked ? [...view.group, ap.id] : view.group.filter((x) => x !== ap.id); saveView(); draw(); };
      return h("label.row", cb, ap.name || "(unnamed)");
    }));
  }
  function target() {
    const t = view.target;
    if (t.startsWith("ssid:")) return {type: "ssid", value: t.slice(5)};
    if (t.startsWith("p:")) return {type: "set", bssids: new Set((placed.find((a) => a.id === t.slice(2)) || {bssids: []}).bssids)};
    if (t.startsWith("g:")) return {type: "set", bssids: new Set((groups.find((g) => g.id === t.slice(2)) || {bssids: []}).bssids)};
    if (t === "group") return {type: "set", bssids: new Set(view.group.flatMap((id) => (placed.find((a) => a.id === id) || {bssids: []}).bssids))};
    return {type: "best"};
  }
  function viewTitle() {
    const v = VIEWS[view.kind];
    if (view.kind === "serving") return placed.length ? "Serving AP (strongest placed AP)" : "Serving AP: place access points first";
    return `${v.label}: ${targetSel.selectedOptions[0] ? targetSel.selectedOptions[0].textContent.replace(/\s+\(.*$/, "") : "strongest AP"}`;
  }
  function setMode(m) {
    mode = m; pop.style.display = "none";
    modeBtns.forEach((b, i) => b.classList.toggle("primary", ["measure", "select", "place"][i] === m));
    canvas.style.cursor = m === "measure" ? "crosshair" : m === "place" ? "copy" : "default";
    info.textContent = m === "measure" ? "Stand at a spot, click it on the plan: the Mac scans there and stores the point (a few seconds). Click a point to inspect it."
      : m === "place" ? "Click where an access point hangs, then choose which measured radio it is." : "Click a point or AP to inspect it; drag to move.";
  }
  viewSel.value = view.kind;
  viewSel.onchange = () => { view.kind = viewSel.value; saveView(); draw(); };
  targetSel.onchange = () => { view.target = targetSel.value; saveView(); draw(); };
  valsCb.onchange = () => { showVals = valsCb.checked; draw(); };

  let geom = null, cache = {key: "", sc: null};
  function scene() {
    const avail = Math.max(360, (wrap.clientWidth || 900) - 4);
    geom = engGeom(wM, lM, avail, 620);
    const t = target();
    const key = JSON.stringify([view.kind, view.target, view.group, points.length, points.map((p) => [p.x_m, p.y_m]), placed.map((a) => [a.bssids])]);
    if (key !== cache.key) cache = {key, sc: buildScene(points, placed, {kind: view.kind, target: t}, wM, lM)};
    const sc = cache.sc;
    const byP = new Map(sc.pts.map((q) => [q.p, q]));
    return {geom, wM, lM, img: sc.img, bg: bgImg, plan: plan.items || [], pts: sc.pts, path: walkOrder(points).map((p) => byP.get(p)), sel,
      title: viewTitle(), unit: sc.unit, stops: sc.stops, zones: sc.zones, pending, labels: showVals,
      emptyText: "No measured points: click on the plan where you stand",
      aps: placed.map((ap, i) => ({...ap, color: view.kind === "serving" ? apColor(i) : null, on: ap.id === selAp}))};
  }
  function draw() {
    if (!canvas.isConnected) return;
    groupBox.style.display = view.target === "group" ? "" : "none";
    targetSel.style.display = view.kind === "serving" ? "none" : "";
    const sc = scene(), dpr = window.devicePixelRatio || 1;
    canvas.width = geom.W * dpr; canvas.height = geom.H * dpr;
    canvas.style.width = geom.W + "px"; canvas.style.height = geom.H + "px";
    const g = canvas.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    renderEng(g, sc);
  }

  const evXY = (ev) => { const r = canvas.getBoundingClientRect(); return {px: ev.clientX - r.left, py: ev.clientY - r.top}; };
  function hitPoint(px, py) {
    let best = null;
    for (const p of points) {
      const d = Math.hypot(geom.ox + p.x_m * geom.pxm - px, geom.oy + p.y_m * geom.pxm - py);
      if (d <= 9 && (!best || d < best.d)) best = {p, d};
    }
    return best && best.p;
  }
  function hitAp(px, py) {
    return placed.find((a) => Math.hypot(geom.ox + a.x_m * geom.pxm - px, geom.oy + a.y_m * geom.pxm - py) <= AP_R + 3) || null;
  }

  async function measure(m) {
    if (pending) { toast("A measurement is running", "info"); return; }
    pending = {x: m.x, y: m.y};
    info.textContent = `Measuring at ${fmtM(m.x)}, ${fmtM(m.y)} m...`;
    draw();
    try {
      const r = await api(`/api/projects/${enc(proj.id)}/points`, {json: {x_m: Math.round(m.x * 100) / 100, y_m: Math.round(m.y * 100) / 100}, timeout: 90000});
      points = [...points, r.point];
      sel = r.point;
      const best = r.point.aps[0];
      info.textContent = `Point ${points.length} saved at ${fmtM(m.x)}, ${fmtM(m.y)} m: ${r.point.aps.length} BSSIDs, strongest ${best ? best.rssi + " dBm (" + (best.ssid || "hidden") + ")" : "none"}.`;
      if (r.warning) toast(r.warning, "info", 12000);
      rebuildTargets();
      showSide();
    } catch (e) { toast(e.message, "err", 9000); info.textContent = e.message; }
    pending = null;
    draw();
  }

  let saveTimer = 0;
  function apsChanged() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try { await api(`/api/projects/${enc(proj.id)}/aps`, {method: "PUT", json: {aps: placed}}); }
      catch (e) { toast("APs not saved: " + e.message, "err"); }
    }, 500);
    cache.key = "";
    rebuildTargets();
  }

  function openPlace(m, px, py) {
    let near = null;
    for (const p of points) { const d = Math.hypot(p.x_m - m.x, p.y_m - m.y); if (!near || d < near.d) near = {p, d}; }
    const rows = groups.map((g) => {
      let v = null;
      if (near) for (const a of near.p.aps) if (g.bssids.includes(String(a.bssid).toLowerCase()) && (v === null || a.rssi > v)) v = a.rssi;
      return {g, v};
    }).sort((a, b) => (b.v ?? -999) - (a.v ?? -999) || b.g.best - a.g.best).slice(0, 30);
    const add = (g) => {
      const ap = {id: "ap" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        name: g ? `${g.ssids[0] || "AP"} ${shortMac(g.id)}` : `AP ${placed.length + 1}`,
        x_m: Math.round(m.x * 100) / 100, y_m: Math.round(m.y * 100) / 100, bssids: g ? [...g.bssids] : []};
      placed = [...placed, ap];
      selAp = ap.id; sel = null;
      pop.style.display = "none";
      apsChanged(); showSide(); draw();
    };
    pop.replaceChildren(h("div.row.small", h("b", "Which access point is this?"), h("span.spacer"),
      h("button.b.small", {type: "button", onclick: () => { pop.style.display = "none"; }}, "x")),
    h("div.small.faint", near ? `Signal at the nearest point, ${fmtM(Math.round(near.d * 10) / 10)} m away` : "No measured points yet"),
    ...rows.map(({g, v}) => h("button.b.small.item", {type: "button", onclick: () => add(g)}, h("b", groupLabel(g)),
      h("div.small.muted", `ch ${g.channels.join("/") || "?"}, ${v === null ? "not heard there" : v + " dBm"}, ${g.bssids.length} BSSID`))),
    h("button.b.small.item", {type: "button", onclick: () => add(null)}, h("b", "Custom AP"), h("div.small.muted", "not bound to a measured radio")));
    pop.style.display = "";
    pop.style.left = Math.max(0, Math.min(px + 10, canvas.clientWidth - 300)) + "px";
    pop.style.top = Math.max(0, Math.min(py + 10, canvas.clientHeight - 200)) + "px";
  }

  canvas.addEventListener("mousedown", (ev) => {
    const {px, py} = evXY(ev);
    const ap = hitAp(px, py);
    if (ap) { selAp = ap.id; sel = null; drag = {kind: "ap", ap, moved: false}; showSide(); draw(); return; }
    const p = hitPoint(px, py);
    if (p) { sel = p; selAp = null; drag = mode === "select" ? {kind: "point", p, moved: false} : null; showSide(); draw(); return; }
    const m = toMetres(geom, px, py, wM, lM);
    if (!m) return;
    if (mode === "measure") measure(m);
    else if (mode === "place") openPlace(m, px, py);
    else { sel = null; selAp = null; showSide(); draw(); }
  });
  canvas.addEventListener("mousemove", (ev) => {
    if (!drag) return;
    const {px, py} = evXY(ev);
    const m = toMetres(geom, px, py, wM, lM);
    if (!m) return;
    drag.moved = true;
    const x = Math.round(m.x * 100) / 100, y = Math.round(m.y * 100) / 100;
    if (drag.kind === "ap") { drag.ap.x_m = x; drag.ap.y_m = y; } else { drag.p.x_m = x; drag.p.y_m = y; cache.key = ""; }
    draw();
  });
  window.addEventListener("mouseup", async () => {
    if (!drag || !canvas.isConnected) { drag = null; return; }
    const d = drag;
    drag = null;
    if (!d.moved) return;
    if (d.kind === "ap") apsChanged();
    else {
      try { await api(`/api/projects/${enc(proj.id)}/points/${enc(d.p.id)}`, {method: "PATCH", json: {x_m: d.p.x_m, y_m: d.p.y_m}}); }
      catch (e) { toast(e.message, "err"); }
    }
    showSide(); draw();
  });

  function showSide() {
    side.replaceChildren();
    if (sel) {
      const p = sel;
      const note = h("input", {type: "text", value: p.note || "", maxlength: 120, placeholder: "Note (room, height...)", "aria-label": "Point note"});
      note.onchange = () => api(`/api/projects/${enc(proj.id)}/points/${enc(p.id)}`, {method: "PATCH", json: {note: note.value}}).then(() => { p.note = note.value; });
      side.append(h("div.card", h("div.row", h("b", `Point at ${fmtM(p.x_m)}, ${fmtM(p.y_m)} m`), h("span.small.muted", fmtTime(p.ts)), note, h("span.spacer"),
        h("button.b.small.danger", {type: "button", onclick: async () => {
          await api(`/api/projects/${enc(proj.id)}/points/${enc(p.id)}`, {method: "DELETE"});
          points = points.filter((x) => x !== p); sel = null; cache.key = ""; rebuildTargets(); showSide(); draw();
        }}, "Delete point"),
        h("button.b.small", {type: "button", onclick: () => { sel = null; showSide(); draw(); }}, "Close")),
      p.aps.length ? table([{key: "ssid", label: "SSID", render: (a) => a.ssid || h("span.faint", "(hidden)")}, {key: "bssid", label: "BSSID", render: (a) => h("span.mono", a.bssid)},
        {key: "channel", label: "Ch", num: true}, {key: "band", label: "Band"}, {key: "rssi", label: "dBm", num: true}], p.aps, {sort: {key: "rssi", dir: -1}})
        : h("p.muted", "Nothing heard at this point (dead spot).")));
    } else if (selAp) {
      const ap = placed.find((a) => a.id === selAp);
      if (!ap) return;
      const name = h("input", {type: "text", value: ap.name, maxlength: 48, "aria-label": "AP name"});
      name.oninput = () => { ap.name = name.value; apsChanged(); draw(); };
      const mine = new Set(ap.bssids);
      const rowsFor = (g) => h("div", h("div.small", h("b", groupLabel(g)), `  ch ${g.channels.join("/") || "?"}, best ${g.best} dBm`),
        g.info.map((x) => {
          const cb = h("input", {type: "checkbox", checked: mine.has(x.bssid)});
          cb.onchange = () => { ap.bssids = cb.checked ? [...new Set([...ap.bssids, x.bssid])] : ap.bssids.filter((b) => b !== x.bssid); apsChanged(); draw(); };
          return h("label.row.small", {style: {marginLeft: "14px"}}, cb, h("span.mono", x.bssid), x.ssid || "(hidden)", h("span.faint", `ch ${x.channel}, best ${x.best}`));
        }));
      const own = groups.filter((g) => g.bssids.some((b) => mine.has(b)));
      side.append(h("div.card", h("div.row", h("b", "Access point"), name, h("span.small.muted", `at ${fmtM(ap.x_m)}, ${fmtM(ap.y_m)} m`), h("span.spacer"),
        h("button.b.small.danger", {type: "button", onclick: () => { placed = placed.filter((a) => a !== ap); selAp = null; apsChanged(); showSide(); draw(); }}, "Delete AP"),
        h("button.b.small", {type: "button", onclick: () => { selAp = null; showSide(); draw(); }}, "Close")),
      h("div.small.muted", "Bound BSSIDs (its signal on the map is the strongest of them):"),
      own.length ? own.map(rowsFor) : h("p.small.faint", "Not bound to a measured BSSID."),
      h("details", h("summary.small", `Other radios (${groups.length - own.length})`), groups.filter((g) => !own.includes(g)).map(rowsFor))));
    }
  }

  rebuildTargets();
  setMode("measure");
  const ro = new ResizeObserver(() => draw());
  const el = h("div.stack",
    h("div.row.wrap", h("span.row", {role: "group", "aria-label": "Mode"}, ...modeBtns), viewSel, targetSel, groupBox,
      h("label.row.small", {for: "vals"}, valsCb, "Values"), h("span.spacer"), pngBtn),
    info, wrap, side,
    h("p.small.faint", "Heatmaps interpolate the measured points (inverse-distance weighting): full strength up to 3 m from the nearest point, "
      + "faded out by 5 m. Dark points heard nothing of the selected network. SNR uses the noise floor the Mac reported at each point; "
      + "AP count = radios at -75 dBm or better; channel overlap = other radios at -82 dBm or better on the serving AP's channel."));
  return {el, mounted: () => { ro.observe(wrap); draw(); }, cleanup: () => ro.disconnect()};
}
