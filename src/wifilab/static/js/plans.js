// Floor plan editor (fieldtab.plan/1: metres, origin top-left): draw walls, doors, windows and beams over an
// optional background image, import JSON (for example an LLM's answer to the prompt), save into the survey.
import {h, api, enc, toast, copyText, fmtM} from "./lib.js";
import {rulerMarks} from "./heat.js";

export const KINDS = ["wall", "door", "window", "beam"];
export const PLAN_STYLE = {
  wall: {color: "#e6edf3", width: 5, label: "Wall"},
  door: {color: "#f0a030", width: 5, label: "Door"},
  window: {color: "#4cc9f0", width: 5, label: "Window"},
  beam: {color: "#9aa7b8", width: 3, dash: true, label: "Beam"},
};
const GRID = 0.1, SNAP_END = 0.25, MIN_LEN = 0.05, UNDO_MAX = 100, MAX_ITEMS = 300;
const RL = 38, RT = 24, HIT = 7;
const TOOL_KEYS = {v: "select", w: "wall", d: "door", n: "window", b: "beam", e: "erase"};

// ---------- pure helpers (tests/js/plans_test.mjs) ----------

export const r2 = (v) => Math.round(v * 100) / 100;
export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export function segDist(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1, l2 = dx * dx + dy * dy;
  const t = l2 ? clamp(((px - x1) * dx + (py - y1) * dy) / l2, 0, 1) : 0;
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

export const segLen = (it) => Math.hypot(it.x2 - it.x1, it.y2 - it.y1);

// A point in metres -> on the 0.1 m grid, or onto an existing line end within SNAP_END; straight from the anchor.
export function snapPoint(p, {items = [], skip = null, anchor = null, ortho = false, w = 100, l = 100} = {}) {
  let x = Math.round(p.x / GRID) * GRID, y = Math.round(p.y / GRID) * GRID, best = SNAP_END;
  items.forEach((it, i) => {
    for (const end of [1, 2]) {
      if (skip && skip.i === i && skip.end === end) continue;
      const d = Math.hypot(it["x" + end] - p.x, it["y" + end] - p.y);
      if (d <= best) { best = d; x = it["x" + end]; y = it["y" + end]; }
    }
  });
  if (ortho && anchor) {
    if (Math.abs(x - anchor.x) >= Math.abs(y - anchor.y)) y = anchor.y; else x = anchor.x;
  }
  return {x: r2(clamp(x, 0, w)), y: r2(clamp(y, 0, l))};
}

export function outside(items, w, l) {
  return items.filter((it) => Math.max(it.x1, it.x2) > w + 0.01 || Math.max(it.y1, it.y2) > l + 0.01);
}

// Nearest item within `tol` metres of the point: {i, d} or null.
export function hitItem(items, x, y, tol) {
  let best = null;
  items.forEach((it, i) => {
    const d = segDist(x, y, it.x1, it.y1, it.x2, it.y2);
    if (d <= tol && (!best || d < best.d)) best = {i, d};
  });
  return best;
}

// ---------- editor ----------

export function planEditor(project, opts = {}) {
  let plan = JSON.parse(JSON.stringify(project.plan));
  let tool = "wall", sel = -1, drag = null, dirty = false, bgImg = null;
  const undo = [];
  const canvas = h("canvas.plancanvas", {tabindex: 0, role: "img", "aria-label": "Floor plan editor"});
  const status = h("span.small.muted");
  const nameIn = h("input", {type: "text", value: plan.name || "", maxlength: 64, placeholder: "Plan name", "aria-label": "Plan name"});
  const wIn = h("input", {type: "number", min: 0.5, max: 100, step: 0.5, value: plan.width_m, "aria-label": "Width, m", style: {width: "80px"}});
  const lIn = h("input", {type: "number", min: 0.5, max: 100, step: 0.5, value: plan.length_m, "aria-label": "Length, m", style: {width: "80px"}});
  const saveBtn = h("button.b.primary", {type: "button", onclick: () => save()}, "Save plan");
  const toolBtns = ["select", ...KINDS, "erase"].map((t) => h("button.b.small", {type: "button", dataset: {tool: t},
    title: `${t} (${Object.keys(TOOL_KEYS).find((k) => TOOL_KEYS[k] === t)})`, onclick: () => setTool(t)},
  t === "select" ? "Select" : t === "erase" ? "Erase" : PLAN_STYLE[t].label));

  const jsonBox = h("textarea", {rows: 8, placeholder: "Paste a fieldtab.plan/1 JSON (for example the LLM's answer) and press Load",
    style: {width: "100%"}, "aria-label": "Plan JSON"});
  const bgIn = h("input", {type: "file", accept: "image/png,image/jpeg,image/webp", "aria-label": "Background image"});

  function push() {
    undo.push(JSON.stringify(plan));
    if (undo.length > UNDO_MAX) undo.shift();
    dirty = true;
  }
  function setTool(t) {
    tool = t;
    toolBtns.forEach((b) => b.classList.toggle("primary", b.dataset.tool === t));
    canvas.style.cursor = t === "select" ? "default" : t === "erase" ? "not-allowed" : "crosshair";
    draw();
  }

  let geom = {pxm: 20, W: 600, H: 400};
  function layout() {
    const avail = Math.max(360, (canvas.parentElement ? canvas.parentElement.clientWidth : 900) - 4);
    const pxm = Math.max(4, Math.min(80, (avail - RL - 14) / plan.width_m, 620 / plan.length_m));
    geom = {pxm, W: Math.ceil(RL + plan.width_m * pxm + 14), H: Math.ceil(RT + plan.length_m * pxm + 10)};
    const dpr = window.devicePixelRatio || 1;
    canvas.width = geom.W * dpr; canvas.height = geom.H * dpr;
    canvas.style.width = geom.W + "px"; canvas.style.height = geom.H + "px";
  }
  const toM = (ev) => {
    const r = canvas.getBoundingClientRect();
    return {x: (ev.clientX - r.left - RL) / geom.pxm, y: (ev.clientY - r.top - RT) / geom.pxm};
  };

  function draw() {
    if (!canvas.isConnected) return;
    layout();
    const g = canvas.getContext("2d"), dpr = window.devicePixelRatio || 1, {pxm} = geom;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = "#0f141b"; g.fillRect(0, 0, geom.W, geom.H);
    g.fillStyle = "#aab4c0"; g.strokeStyle = "rgba(220,228,236,0.5)"; g.font = "11px -apple-system, sans-serif";
    g.beginPath();
    g.textAlign = "center"; g.textBaseline = "middle";
    for (const k of rulerMarks(plan.width_m, pxm)) {
      const x = Math.round(RL + k.m * pxm) + 0.5;
      g.moveTo(x, RT - (k.major ? 8 : 4)); g.lineTo(x, RT);
      if (k.label) g.fillText(k.label, x, 9);
    }
    g.textAlign = "right";
    for (const k of rulerMarks(plan.length_m, pxm)) {
      const y = Math.round(RT + k.m * pxm) + 0.5;
      g.moveTo(RL - (k.major ? 8 : 4), y); g.lineTo(RL, y);
      if (k.label) g.fillText(k.label, RL - 11, Math.max(RT + 6, y));
    }
    g.stroke();
    g.save();
    g.translate(RL, RT);
    g.fillStyle = "#1b2430"; g.fillRect(0, 0, plan.width_m * pxm, plan.length_m * pxm);
    if (bgImg) { g.globalAlpha = 0.6; g.drawImage(bgImg, 0, 0, plan.width_m * pxm, plan.length_m * pxm); g.globalAlpha = 1; }
    // 1 m grid
    g.strokeStyle = "rgba(255,255,255,0.06)"; g.lineWidth = 1; g.beginPath();
    for (let x = 1; x < plan.width_m; x++) { g.moveTo(x * pxm + 0.5, 0); g.lineTo(x * pxm + 0.5, plan.length_m * pxm); }
    for (let y = 1; y < plan.length_m; y++) { g.moveTo(0, y * pxm + 0.5); g.lineTo(plan.width_m * pxm, y * pxm + 0.5); }
    g.stroke();
    g.strokeStyle = "rgba(255,255,255,0.35)"; g.strokeRect(0, 0, plan.width_m * pxm, plan.length_m * pxm);
    const items = drag && drag.kind === "draw" ? [...plan.items, drag.item] : plan.items;
    items.forEach((it, i) => {
      const st = PLAN_STYLE[it.kind];
      g.strokeStyle = i === sel ? "#ffb020" : st.color;
      g.lineWidth = Math.max(2, st.width * Math.min(1, pxm / 30)) + (i === sel ? 1 : 0);
      g.setLineDash(st.dash ? [6, 4] : []);
      g.lineCap = "round";
      g.beginPath(); g.moveTo(it.x1 * pxm, it.y1 * pxm); g.lineTo(it.x2 * pxm, it.y2 * pxm); g.stroke();
      if (it.label) {
        g.fillStyle = "#ffffff"; g.font = "10px -apple-system, sans-serif"; g.textAlign = "center";
        g.fillText(it.label, (it.x1 + it.x2) / 2 * pxm, (it.y1 + it.y2) / 2 * pxm - 8);
      }
    });
    g.setLineDash([]);
    if (sel >= 0 && plan.items[sel]) {
      const it = plan.items[sel];
      for (const [x, y] of [[it.x1, it.y1], [it.x2, it.y2]]) {
        g.fillStyle = "#ffb020"; g.beginPath(); g.arc(x * pxm, y * pxm, 5, 0, 2 * Math.PI); g.fill();
      }
    }
    g.restore();
    const it = sel >= 0 ? plan.items[sel] : drag && drag.item;
    status.textContent = `${plan.items.length} / ${MAX_ITEMS} items. ` + (it ? `${PLAN_STYLE[it.kind].label} ${fmtM(segLen(it))} m `
      + `(${fmtM(it.x1)}, ${fmtM(it.y1)}) - (${fmtM(it.x2)}, ${fmtM(it.y2)}). ` : "")
      + (dirty ? "Unsaved changes." : "Saved.") + " Shift = straight lines, Del = delete selected, Cmd+Z = undo.";
    saveBtn.disabled = !dirty;
  }

  const opt = (extra = {}) => ({items: plan.items, w: plan.width_m, l: plan.length_m, ...extra});

  canvas.addEventListener("mousedown", (ev) => {
    canvas.focus();
    const p = toM(ev), tol = HIT / geom.pxm;
    if (tool === "erase") {
      const hit = hitItem(plan.items, p.x, p.y, tol);
      if (hit) { push(); plan.items.splice(hit.i, 1); sel = -1; draw(); }
      return;
    }
    if (tool === "select") {
      if (sel >= 0 && plan.items[sel]) {
        const it = plan.items[sel];
        for (const end of [1, 2]) {
          if (Math.hypot(it["x" + end] - p.x, it["y" + end] - p.y) <= tol * 1.5) {
            push(); drag = {kind: "end", end}; return;
          }
        }
      }
      const hit = hitItem(plan.items, p.x, p.y, tol);
      sel = hit ? hit.i : -1;
      if (hit) { push(); drag = {kind: "move", from: p, orig: {...plan.items[hit.i]}}; }
      draw();
      return;
    }
    if (plan.items.length >= MAX_ITEMS) { toast(`At most ${MAX_ITEMS} items in a plan`, "err"); return; }
    const a = snapPoint(p, opt());
    drag = {kind: "draw", a, item: {kind: tool, x1: a.x, y1: a.y, x2: a.x, y2: a.y}};
  });
  window.addEventListener("mousemove", (ev) => {
    if (!drag || !canvas.isConnected) return;
    const p = toM(ev);
    if (drag.kind === "draw") {
      const b = snapPoint(p, opt({anchor: drag.a, ortho: ev.shiftKey}));
      drag.item.x2 = b.x; drag.item.y2 = b.y;
    } else if (drag.kind === "end") {
      const it = plan.items[sel], other = drag.end === 1 ? 2 : 1;
      const b = snapPoint(p, opt({skip: {i: sel, end: drag.end}, anchor: {x: it["x" + other], y: it["y" + other]}, ortho: ev.shiftKey}));
      it["x" + drag.end] = b.x; it["y" + drag.end] = b.y;
    } else if (drag.kind === "move") {
      const it = plan.items[sel], o = drag.orig;
      let dx = r2(Math.round((p.x - drag.from.x) / GRID) * GRID), dy = r2(Math.round((p.y - drag.from.y) / GRID) * GRID);
      dx = clamp(dx, -Math.min(o.x1, o.x2), plan.width_m - Math.max(o.x1, o.x2));
      dy = clamp(dy, -Math.min(o.y1, o.y2), plan.length_m - Math.max(o.y1, o.y2));
      Object.assign(it, {x1: r2(o.x1 + dx), y1: r2(o.y1 + dy), x2: r2(o.x2 + dx), y2: r2(o.y2 + dy)});
    }
    draw();
  });
  window.addEventListener("mouseup", () => {
    if (!drag || !canvas.isConnected) return;
    if (drag.kind === "draw") {
      if (segLen(drag.item) >= MIN_LEN) { push(); plan.items.push(drag.item); sel = plan.items.length - 1; }
    } else if (sel >= 0 && segLen(plan.items[sel]) < MIN_LEN) {
      plan.items.splice(sel, 1); sel = -1;
    }
    drag = null;
    draw();
  });
  canvas.addEventListener("keydown", (ev) => {
    if ((ev.key === "Delete" || ev.key === "Backspace") && sel >= 0) {
      push(); plan.items.splice(sel, 1); sel = -1; draw(); ev.preventDefault();
    } else if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "z") {
      if (undo.length) { plan = JSON.parse(undo.pop()); sel = -1; dirty = true; syncInputs(); draw(); }
      ev.preventDefault();
    } else if (TOOL_KEYS[ev.key] && !ev.metaKey && !ev.ctrlKey) setTool(TOOL_KEYS[ev.key]);
  });

  function syncInputs() { nameIn.value = plan.name || ""; wIn.value = plan.width_m; lIn.value = plan.length_m; }
  nameIn.oninput = () => { plan.name = nameIn.value; dirty = true; draw(); };
  const applySize = () => {
    const w = r2(clamp(+wIn.value || plan.width_m, 0.5, 100)), l = r2(clamp(+lIn.value || plan.length_m, 0.5, 100));
    const out = outside(plan.items, w, l);
    if (out.length && !confirm(`${out.length} item(s) fall outside ${w} x ${l} m and will be removed. Continue?`)) { syncInputs(); return; }
    push();
    plan.items = plan.items.filter((it) => !out.includes(it));
    plan.width_m = w; plan.length_m = l; sel = -1;
    syncInputs(); draw();
  };
  wIn.onchange = applySize; lIn.onchange = applySize;

  async function save() {
    try {
      const r = await api(`/api/projects/${enc(project.id)}/plan`, {method: "PUT", json: plan});
      plan = JSON.parse(JSON.stringify(r.plan));
      project.plan = r.plan;
      dirty = false;
      toast("Plan saved", "ok");
      if (opts.onSaved) opts.onSaved(r);
      draw();
    } catch (e) { toast(e.message, "err", 8000); }
  }

  async function loadJson() {
    try {
      const r = await api("/api/plans/validate", {method: "POST", body: jsonBox.value});
      push();
      plan = r.plan; sel = -1;
      syncInputs(); draw();
      toast(`Loaded: ${plan.items.length} items, ${plan.width_m} x ${plan.length_m} m. Save to keep it.`, "ok");
    } catch (e) { toast(e.message, "err", 10000); }
  }

  function setBg(url) {
    if (!url) { bgImg = null; draw(); return; }
    const im = new Image();
    im.onload = () => { bgImg = im; draw(); };
    im.src = url;
  }
  bgIn.onchange = () => {
    const f = bgIn.files[0];
    if (!f) return;
    const rd = new FileReader();
    rd.onload = async () => {
      try {
        await api(`/api/projects/${enc(project.id)}/bg`, {method: "PUT", json: {data_url: rd.result}});
        project.bg = rd.result;
        setBg(rd.result);
        toast("Background saved: it is stretched to the plan's width x length", "ok");
      } catch (e) { toast(e.message, "err"); }
    };
    rd.readAsDataURL(f);
  };
  const bgClear = h("button.b.small", {type: "button", onclick: async () => {
    await api(`/api/projects/${enc(project.id)}/bg`, {method: "PUT", json: {data_url: null}});
    project.bg = null; setBg(null);
  }}, "Remove background");
  setBg(project.bg);

  const promptBtn = h("button.b.small", {type: "button", onclick: async () => {
    const r = await api("/api/plans/prompt");
    if (await copyText(r.prompt)) toast("LLM prompt copied: give it a photo of the floor plan, paste the answer below", "ok", 7000);
    else { jsonBox.value = r.prompt; toast("Clipboard blocked: the prompt is in the box below", "info"); }
  }}, "Copy LLM prompt");

  const root = h("div.stack",
    h("div.row", nameIn, h("label.row.small", "Width, m ", wIn), h("label.row.small", "Length, m ", lIn), h("span.spacer"), saveBtn),
    h("div.row", {role: "toolbar", "aria-label": "Plan tools"}, ...toolBtns, h("span.spacer"),
      h("a.b.small", {href: `/api/projects/${enc(project.id)}/plan.json`}, "Export plan JSON")),
    h("div.planwrap", canvas),
    status,
    h("details.card", h("summary", "Import a plan (JSON or LLM answer), background image"),
      h("p.small.muted", "Make a plan from a photo: Copy LLM prompt, give the prompt and the photo to any vision LLM, paste its answer "
        + "here and Load. The format is fieldtab.plan/1 (FieldTab tablet compatible)."),
      h("div.row", promptBtn, h("button.b.small", {type: "button", onclick: loadJson}, "Load JSON")),
      jsonBox,
      h("div.row", {style: {marginTop: "8px"}}, h("span.small", "Background image (stretched to the plan size): "), bgIn, bgClear)));
  setTool("wall");
  requestAnimationFrame(draw);
  window.addEventListener("resize", () => draw());
  root.isDirty = () => dirty;
  return root;
}
