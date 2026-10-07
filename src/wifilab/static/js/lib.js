// Shared helpers: DOM builder, API calls with readable errors, formatting, toasts, downloads, sortable tables.

// h("div.card#x", {onclick, style: {...}}, child, "text", [children]) -> element; null/false children are skipped.
export function h(tag, attrs, ...kids) {
  const m = tag.match(/^([a-z0-9]+)?((?:[.#][\w-]+)*)$/i);
  const el = document.createElement((m && m[1]) || "div");
  if (m && m[2]) for (const part of m[2].match(/[.#][\w-]+/g)) {
    if (part[0] === ".") el.classList.add(part.slice(1)); else el.id = part.slice(1);
  }
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) { kids.unshift(attrs); attrs = null; }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  const add = (k) => {
    if (k === null || k === undefined || k === false) return;
    if (Array.isArray(k)) k.forEach(add);
    else el.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
  };
  kids.forEach(add);
  return el;
}

export const $ = (s, root = document) => root.querySelector(s);

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

// api(path, {method, json, body, raw}) -> parsed JSON (or Response when raw); errors carry the server's detail.
export async function api(path, opts = {}) {
  const init = {method: opts.method || (opts.json !== undefined || opts.body !== undefined ? "POST" : "GET"), headers: {}};
  if (opts.json !== undefined) { init.body = JSON.stringify(opts.json); init.headers["Content-Type"] = "application/json"; }
  else if (opts.body !== undefined) init.body = opts.body;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), opts.timeout || 60000);
  init.signal = ctl.signal;
  let r;
  try {
    r = await fetch(path, init);
  } catch (e) {
    throw new ApiError(0, e.name === "AbortError" ? "request timed out" : "WiFiLab is not reachable: " + e.message);
  } finally { clearTimeout(timer); }
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j); } catch { /* not JSON */ }
    throw new ApiError(r.status, msg);
  }
  if (opts.raw) return r;
  return r.json();
}

export const enc = encodeURIComponent;

export function toast(msg, kind = "info", ms = 4000) {
  let box = $("#toasts");
  if (!box) { box = h("div#toasts"); document.body.append(box); }
  const t = h("div.toast." + kind, msg);
  box.append(t);
  setTimeout(() => t.remove(), ms);
}

export function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleString("en-GB", {year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"});
}

export function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

export function fmtM(v) { return String(Math.round(v * 100) / 100); }

export function download(name, data, type = "application/octet-stream") {
  const blob = data instanceof Blob ? data : new Blob([data], {type});
  const a = h("a", {href: URL.createObjectURL(blob), download: name});
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

export function badge(text, kind = "") { return h("span.badge" + (kind ? "." + kind : ""), text); }

export function empty(text) { return h("div.empty", text); }

export function errBox(e) { return h("div.notice.err", String(e && e.message ? e.message : e)); }

// Sort helper: compares numbers numerically, everything else as lower-case text; null/undefined last.
export function cmp(a, b) {
  const na = a === null || a === undefined || a === "", nb = b === null || b === undefined || b === "";
  if (na || nb) return na === nb ? 0 : na ? 1 : -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).toLowerCase().localeCompare(String(b).toLowerCase());
}

const UP = " " + String.fromCharCode(0x25b4), DOWN = " " + String.fromCharCode(0x25be);

// table(cols, rows, {sort, onRow, rowClass}) with clickable headers. cols: [{key, label, render?, num?}]
export function table(cols, rows, opts = {}) {
  let sort = opts.sort || null;   // {key, dir}
  const wrap = h("div.tablewrap");
  const draw = () => {
    const data = [...rows];
    if (sort) {
      const col = cols.find((c) => c.key === sort.key);
      const val = col && col.sortValue ? col.sortValue : (r) => r[sort.key];
      data.sort((a, b) => cmp(val(a), val(b)) * sort.dir);
    }
    const head = h("tr", cols.map((c) => h("th" + (c.num ? ".num" : ""), {
      onclick: () => {
        sort = sort && sort.key === c.key ? {key: c.key, dir: -sort.dir} : {key: c.key, dir: c.num ? -1 : 1};
        if (opts.onSort) opts.onSort(sort);
        draw();
      }, title: "Sort"}, c.label, sort && sort.key === c.key ? (sort.dir > 0 ? UP : DOWN) : "")));
    const body = data.map((r) => h("tr" + (opts.rowClass ? opts.rowClass(r) : ""), {onclick: opts.onRow ? () => opts.onRow(r) : null},
      cols.map((c) => h("td" + (c.num ? ".num" : ""), c.render ? c.render(r) : (r[c.key] ?? "")))));
    wrap.replaceChildren(h("table", h("thead", head), h("tbody", body)));
  };
  draw();
  return wrap;
}

export function debounce(fn, ms = 250) {
  let t = 0;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}
