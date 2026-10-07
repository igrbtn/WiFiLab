"""Survey report data, CSV export and the Word (docx) report. The printable HTML report is rendered in the browser
(static/js/report.js) from report_data(); the browser also renders the heatmaps and sends them as PNGs for docx."""
from __future__ import annotations

import base64
import csv
import io
import time

from . import __version__, analysis, projects

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
    return report_data(inv, title=proj.get("name") or "Survey", source="project", points=proj.get("points"),
                       ssid=ssid or proj.get("target_ssid") or None,
                       meta={"project": proj.get("id"), "note": proj.get("note", ""), "width_m": plan.get("width_m"),
                             "length_m": plan.get("length_m"), "created": proj.get("created"),
                             "updated": proj.get("updated"), "imported_from": proj.get("source", "")})


def inventory_csv(inventory: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLS)
    for a in inventory:
        w.writerow(["" if a.get(c) is None else a.get(c) for c in CSV_COLS])
    return buf.getvalue()


def _fmt_ts(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if isinstance(ts, (int, float)) and ts > 0 else ""


def docx_report(data: dict, images: list[dict]) -> bytes:
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

    if images:
        doc.add_heading("Coverage", 1)
        for im in images:
            png = str(im.get("png", ""))
            if "," in png[:64]:
                png = png.split(",", 1)[1]
            try:
                raw = base64.b64decode(png, validate=True)
            except ValueError:
                continue
            if not raw.startswith(b"\x89PNG"):
                continue
            doc.add_paragraph(str(im.get("title", ""))[:120]).runs[0].bold = True
            doc.add_picture(io.BytesIO(raw), width=Cm(17))

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
