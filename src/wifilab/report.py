"""Survey report data, CSV export, the Word (docx) report and a standalone HTML report.

The interactive printable report is rendered in the browser (static/js/report.js) from report_data() and sends its
heatmap canvases to docx. Without them (API calls, the live report) the heatmaps are rendered server-side by
heatmap.py, so every report of a survey with measured points carries its heatmaps."""
from __future__ import annotations

import base64
import csv
import html
import io
import time

from . import __version__, analysis, heatmap, projects

CSV_COLS = ["bssid", "ssid", "hidden", "vendor", "band", "channel", "width", "center", "security", "sec_class", "phy",
            "country", "rssi_last", "rssi_min", "rssi_avg", "rssi_max", "seen_count", "first_seen", "last_seen"]
SEV_LABEL = {"high": "High", "medium": "Medium", "low": "Low", "info": "Info"}


def report_data(inventory: list[dict], *, title: str, source: str, points: list[dict] | None = None,
                ssid: str | None = None, meta: dict | None = None) -> dict:
    points = points or []
    issues = analysis.environment_issues(inventory) + analysis.coverage_issues(points, ssid or None)
    issues.sort(key=lambda i: analysis.SEVERITY_ORDER[i["severity"]])
    return {
        "title": title, "source": source, "generated": time.time(), "version": __version__, "ssid": ssid or "",
        "meta": meta or {},
        "summary": {"bssids": len(inventory), "ssids": len({a["ssid"] for a in inventory if a.get("ssid")}),
                    "radios": len({analysis.radio_key(a["bssid"]) for a in inventory}),
                    "bands": {b: sum(1 for a in inventory if a.get("band") == b) for b in analysis.BANDS},
                    "points": len(points),
                    "issues": {s: sum(1 for i in issues if i["severity"] == s) for s in SEV_LABEL}},
        "inventory": inventory,
        "channels": {b: analysis.channel_stats(inventory, b) for b in analysis.BANDS},
        "recommend": analysis.recommend(inventory),
        "cochannel": analysis.cochannel(inventory),
        "issues": issues,
    }


def project_report(proj: dict, ssid: str | None = None, oui_extra=None) -> dict:
    inv = projects.inventory(proj, oui_extra)
    plan = proj.get("plan") or {}
    data = report_data(inv, title=proj.get("name") or "Survey", source="project", points=proj.get("points"),
                       ssid=ssid or proj.get("target_ssid") or None,
                       meta={"project": proj.get("id"), "note": proj.get("note", ""), "width_m": plan.get("width_m"),
                             "length_m": plan.get("length_m"), "created": proj.get("created"),
                             "updated": proj.get("updated"), "imported_from": proj.get("source", "")})
    data["coverage"] = coverage_info(proj) if proj.get("points") else None
    return data


def inventory_csv(inventory: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLS)
    for a in inventory:
        w.writerow(["" if a.get(c) is None else a.get(c) for c in CSV_COLS])
    return buf.getvalue()


def _fmt_ts(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if isinstance(ts, (int, float)) and ts > 0 else ""


def coverage_info(proj: dict | None) -> dict | None:
    """What the report says about heatmaps: the survey they come from, or None when nothing was measured."""
    if not proj:
        return None
    w, ln, _, has_plan = heatmap.area(proj)
    return {"project": proj.get("id"), "name": proj.get("name", ""), "points": len(proj.get("points") or []),
            "placed_aps": len(proj.get("aps") or []), "has_plan": has_plan, "width_m": w, "length_m": ln}


NO_POINTS = ("No measured points yet: the coverage heatmaps appear once points are measured on the survey's "
             "floor plan (Surveys > Measure & heatmaps).")


def _decode_png(png) -> bytes | None:
    if isinstance(png, bytes):
        return png if png.startswith(b"\x89PNG") else None
    png = str(png or "")
    if "," in png[:64]:
        png = png.split(",", 1)[1]
    try:
        raw = base64.b64decode(png, validate=True)
    except ValueError:
        return None
    return raw if raw.startswith(b"\x89PNG") else None


def coverage_images(proj: dict | None, ssid: str = "", images: list[dict] | None = None) -> list[dict]:
    """[{title, png bytes}]: the browser's canvases when sent, else server-rendered views of proj."""
    out = []
    for im in images or []:
        raw = _decode_png(im.get("png"))
        if raw:
            out.append({"title": str(im.get("title", ""))[:120], "png": raw})
    if out:
        return out
    return heatmap.report_images(proj, ssid) if proj and proj.get("points") else []


def docx_report(data: dict, images: list[dict], proj: dict | None = None) -> bytes:
    """Word report: summary, heatmap images [{title, png (base64 or data URL)}], channel plan, issues, inventory."""
    from docx import Document
    from docx.shared import Cm, Pt

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10)
    for s in doc.sections:
        s.left_margin = s.right_margin = Cm(1.8)
        s.top_margin = s.bottom_margin = Cm(1.6)

    doc.add_heading(f"Wi-Fi survey report: {data['title']}", 0)
    meta = data.get("meta") or {}
    rows = [("Generated", _fmt_ts(data["generated"])), ("Tool", f"WiFiLab {data['version']}"),
            ("Source", "survey project" if data["source"] == "project" else "live scan history")]
    if meta.get("width_m"):
        rows.append(("Area", f"{meta['width_m']:g} x {meta['length_m']:g} m"))
    if data.get("ssid"):
        rows.append(("Coverage target SSID", data["ssid"]))
    if meta.get("note"):
        rows.append(("Note", meta["note"]))
    if meta.get("minutes"):
        rows.append(("Window", f"last {meta['minutes']} min, {meta.get('scans', 0)} scans"))
    _table(doc, ["Field", "Value"], rows)

    s = data["summary"]
    doc.add_heading("Summary", 1)
    doc.add_paragraph(
        f"{s['bssids']} BSSIDs ({s['radios']} radios, {s['ssids']} SSIDs): "
        f"{s['bands']['2.4']} on 2.4 GHz, {s['bands']['5']} on 5 GHz, {s['bands']['6']} on 6 GHz. "
        + (f"{s['points']} measured points. " if s["points"] else "")
        + "Issues: " + ", ".join(f"{n} {SEV_LABEL[k].lower()}" for k, n in s["issues"].items() if n) + ".")

    cov = data.get("coverage")
    pics = coverage_images(proj, data.get("ssid") or data.get("coverage_ssid") or "", images)
    doc.add_heading("Coverage", 1)
    if pics:
        doc.add_paragraph(_coverage_note(cov, data["source"]))
        for im in pics:
            doc.add_paragraph(im["title"]).runs[0].bold = True
            doc.add_picture(io.BytesIO(im["png"]), width=Cm(17))
    else:
        doc.add_paragraph(NO_POINTS)

    doc.add_heading("Issues", 1)
    if data["issues"]:
        _table(doc, ["Severity", "Issue", "Details"],
               [(SEV_LABEL[i["severity"]], i["title"], i["detail"]) for i in data["issues"]])
    else:
        doc.add_paragraph("No issues found.")

    doc.add_heading("Channel plan", 1)
    rec = data["recommend"]
    lines = []
    if rec["2.4"]["channel"]:
        lines.append(f"2.4 GHz: least loaded of 1/6/11 is channel {rec['2.4']['channel']}.")
    if rec["5"]["channel"]:
        b80 = rec["5"].get("block80")
        lines.append(f"5 GHz: least loaded non-DFS channel {rec['5']['channel']}"
                     + (f", 80 MHz block {b80['channels'][0]}-{b80['channels'][-1]}" if b80 else "")
                     + (f"; with DFS: {rec['5']['channel_dfs']}" if rec["5"].get("channel_dfs") else "") + ".")
    if rec["6"]["channel"] and s["bands"]["6"]:
        lines.append(f"6 GHz: least loaded PSC channel {rec['6']['channel']}.")
    for ln in lines:
        doc.add_paragraph(ln, style="List Bullet")
    for band in analysis.BANDS:
        used = [c for c in data["channels"][band] if c["aps"] or (band == "2.4" and c["load"])]
        if not used:
            continue
        doc.add_heading(f"{band} GHz channels", 2)
        _table(doc, ["Channel", "APs", "Strongest, dBm", "Load"],
               [(c["channel"], c["aps"], c["max_rssi"] if c["aps"] else "", c["load"]) for c in used])

    doc.add_heading("Access point inventory", 1)
    _table(doc, ["SSID", "BSSID", "Vendor", "Band", "Ch", "Width", "Security", "Max dBm"],
           [(a.get("ssid") or "(hidden)", a["bssid"], a.get("vendor", ""), a.get("band", ""), a.get("channel", ""),
             a.get("width", ""), a.get("security", ""), a.get("rssi_max", "")) for a in data["inventory"]],
           small=True)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _coverage_note(cov: dict | None, source: str) -> str:
    if not cov:
        return ""
    where = f"survey \"{cov['name']}\"" if source == "live" else "this survey"
    plan = (f"floor plan {cov['width_m']:g} x {cov['length_m']:g} m" if cov["has_plan"]
            else f"no floor plan, drawn on a {cov['width_m']:g} x {cov['length_m']:g} m grid around the points")
    return (f"Heatmaps of {where}: {cov['points']} measured points, {cov['placed_aps']} placed APs, {plan}. "
            "Values are interpolated between the points (inverse-distance weighting) and fade out 3-5 m away "
            "from them; dark points heard nothing of the selected network.")


def html_report(data: dict, proj: dict | None = None, images: list[dict] | None = None) -> str:
    """Standalone printable HTML report with the heatmaps embedded as PNG."""
    e = html.escape
    s, m = data["summary"], data.get("meta") or {}
    cov = data.get("coverage")
    pics = coverage_images(proj, data.get("ssid") or data.get("coverage_ssid") or "", images)
    src = (f"live scan history, last {m.get('minutes')} min ({m.get('scans', 0)} scans)" if data["source"] == "live"
           else "survey project" + (f", area {m['width_m']:g} x {m['length_m']:g} m" if m.get("width_m") else ""))
    parts = [f"<header><h1>Wi-Fi survey report: {e(data['title'])}</h1><p class=muted>Generated "
             f"{e(_fmt_ts(data['generated']))} by WiFiLab {e(data['version'])}. Source: {e(src)}."
             + (f" Coverage target: {e(data['ssid'])}." if data.get("ssid") else "") + "</p>"
             + (f"<p>{e(m['note'])}</p>" if m.get("note") else "") + "</header>"]
    parts.append("<section class=kpis>" + "".join(
        f"<div><b>{v}</b><span>{e(label)}</span></div>" for v, label in (
            (s["bssids"], "BSSIDs"), (s["radios"], "radios"), (s["ssids"], "SSIDs"), (s["bands"]["2.4"], "on 2.4 GHz"),
            (s["bands"]["5"], "on 5 GHz"), (s["bands"]["6"], "on 6 GHz"), (s["points"], "points"),
            (s["issues"]["high"], "high issues"), (s["issues"]["medium"], "medium issues"))) + "</section>")
    parts.append("<section><h2>Issues found</h2>" + ("<ul>" + "".join(
        f"<li><b class=sev-{e(i['severity'])}>{e(SEV_LABEL[i['severity']])}</b> {e(i['title'])}"
        f"<div class=muted>{e(i['detail'])}</div></li>" for i in data["issues"]) + "</ul>"
        if data["issues"] else "<p>No issues found.</p>") + "</section>")
    parts.append("<section><h2>Coverage</h2>")
    if pics:
        parts.append(f"<p class=muted>{e(_coverage_note(cov, data['source']))}</p>")
        for im in pics:
            b64 = base64.b64encode(im["png"]).decode("ascii")
            parts.append(f"<figure><img alt=\"{e(im['title'])}\" src=\"data:image/png;base64,{b64}\">"
                         f"<figcaption>{e(im['title'])}</figcaption></figure>")
    else:
        parts.append(f"<p>{e(NO_POINTS)}</p>")
    parts.append("</section>")
    rec = data["recommend"]
    lines = []
    if rec["2.4"]["channel"]:
        lines.append(f"2.4 GHz: least loaded of 1/6/11 is channel {rec['2.4']['channel']}.")
    if rec["5"]["channel"]:
        lines.append(f"5 GHz: least loaded non-DFS channel {rec['5']['channel']}.")
    if rec["6"]["channel"] and s["bands"]["6"]:
        lines.append(f"6 GHz: least loaded PSC channel {rec['6']['channel']}.")
    parts.append("<section><h2>Channel plan</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in lines) + "</ul>")
    for band in analysis.BANDS:
        used = [c for c in data["channels"][band] if c["aps"]]
        if used:
            parts.append(f"<h3>{band} GHz</h3><table><tr><th>Channel</th><th>APs</th><th>Strongest, dBm</th>"
                         "<th>Load</th></tr>" + "".join(
                             f"<tr><td>{c['channel']}</td><td>{c['aps']}</td><td>{c['max_rssi']}</td>"
                             f"<td>{c['load']}</td></tr>" for c in used) + "</table>")
    parts.append("</section><section><h2>Access point inventory</h2><table class=small><tr><th>SSID</th><th>BSSID</th>"
                 "<th>Vendor</th><th>Band</th><th>Ch</th><th>Width</th><th>Security</th><th>Max dBm</th></tr>" + "".join(
                     "<tr>" + "".join(f"<td>{e(str(v))}</td>" for v in (
                         a.get("ssid") or "(hidden)", a["bssid"], a.get("vendor", ""), a.get("band", ""),
                         a.get("channel", ""), a.get("width", ""), a.get("security", ""), a.get("rssi_max", "")))
                     + "</tr>" for a in data["inventory"]) + "</table></section>")
    css = ("body{font:14px -apple-system,Segoe UI,sans-serif;color:#1d2026;max-width:1100px;margin:24px auto;"
           "padding:0 16px}h1{font-size:22px}h2{border-bottom:1px solid #ccd;padding-bottom:4px;margin-top:28px}"
           ".muted{color:#667}.kpis{display:flex;flex-wrap:wrap;gap:10px}.kpis div{border:1px solid #dde;"
           "border-radius:6px;padding:6px 12px}.kpis b{display:block;font-size:20px}.kpis span{font-size:12px;"
           "color:#667}figure{margin:16px 0;break-inside:avoid}figure img{max-width:100%;border-radius:4px}"
           "figcaption{font-size:12px;color:#667}table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid"
           " #dde;padding:3px 8px;text-align:left}table.small{font-size:12px}.sev-high{color:#c62828}"
           ".sev-medium{color:#e65100}@media print{body{margin:0}}")
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><title>{e(data['title'])} - WiFiLab report"
            f"</title><style>{css}</style></head><body>" + "".join(parts) + "</body></html>")


def _table(doc, head: list[str], rows, small: bool = False) -> None:
    from docx.shared import Pt

    t = doc.add_table(rows=1, cols=len(head))
    t.style = "Light Grid Accent 1"
    for i, h in enumerate(head):
        t.rows[0].cells[i].text = h
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = "" if v is None else str(v)
    if small:
        for row in t.rows:
            for c in row.cells:
                for p in c.paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(8)
