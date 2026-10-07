"""Import of FieldTab tablet Wi-Fi exports (ported from FieldTab Desk formats.py): survey reports (JSON or CSV),
coverage maps (metre maps, older cell grids, the app's store shape) and whole session documents. Everything is
recognised by content, not file name; the result is converted to a WiFiLab survey project."""
from __future__ import annotations

import csv
import io
import json
import math

from . import analysis, plans

MAX_ROWS = 5000
MAX_CELLS = 400       # Wi-Fi map side in cells (200 m at 0.5 m; the app stops at 100 m)

def decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def parse_csv(text: str) -> dict:
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return {"columns": [], "rows": [], "truncated": False}
    return {"columns": rows[0], "rows": rows[1:MAX_ROWS + 1], "truncated": len(rows) - 1 > MAX_ROWS}

def _num(v, default=None):
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return v
    try:
        f = float(str(v).strip())
    except ValueError:
        return default
    return int(f) if f.is_integer() else f


def _bool(v) -> bool:
    return v is True or str(v).strip().lower() in ("true", "yes", "1")


def band_of(channel) -> str:
    ch = _num(channel, 0) or 0
    return "2.4" if 1 <= ch <= 14 else "5" if 32 <= ch <= 177 else "6" if ch > 177 else "?"


AP_INT = ("channel", "width", "rssi_last", "rssi_min", "rssi_avg", "rssi_max", "seen_count", "first_seen", "last_seen")


def _norm_ap(a: dict) -> dict:
    if "count" in a and "seen_count" not in a:   # the app's in-session record (document "seen")
        cnt = _num(a.get("count"), 0) or 0
        a = {**a, "seen_count": cnt, "rssi_last": a.get("rssi"), "first_seen": a.get("first"), "last_seen": a.get("last"),
             "rssi_avg": round(_num(a.get("rssi_sum"), 0) / cnt) if cnt else a.get("rssi")}
    ap = {k: a.get(k, "") or "" for k in ("bssid", "ssid", "second", "auth", "cipher", "group_cipher", "phy", "country")}
    for k in AP_INT:
        ap[k] = _num(a.get(k))
    ap["hidden"] = _bool(a.get("hidden")) or not ap["ssid"]
    ap["wps"] = _bool(a.get("wps"))
    if ap["rssi_max"] is None:
        ap["rssi_max"] = _num(a.get("rssi"), ap["rssi_last"])
    for k in ("rssi_last", "rssi_avg", "rssi_min"):
        if ap[k] is None:
            ap[k] = ap["rssi_max"]
    ap["band"] = band_of(ap["channel"])
    return ap

def wifi_survey(obj) -> dict:
    """Survey report (JSON object or CSV table) -> {meta..., aps, channels}."""
    if isinstance(obj, dict) and "columns" in obj and "rows" in obj:
        cols = [c.strip().lower() for c in obj["columns"]]
        raw = [dict(zip(cols, r, strict=False)) for r in obj["rows"]]
        meta: dict = {}
    else:
        raw = [a for a in obj.get("aps", []) if isinstance(a, dict)]
        meta = {k: obj.get(k) for k in ("band", "device", "firmware", "started", "finished", "duration_s", "scans", "note")}
    aps = [_norm_ap(a) for a in raw]
    aps.sort(key=lambda a: -(a["rssi_max"] if a["rssi_max"] is not None else -999))
    return {**meta, "ap_count": len(aps), "aps": aps, "channels": analysis.channel_stats(aps, "2.4")}


def _cell(p: dict, cols: int, cell_m: float | None = None) -> tuple[int, int] | None:
    col, row = _num(p.get("col")), _num(p.get("row"))
    if (col is None or row is None) and cell_m:
        # Metre maps: x_m / y_m are the cell centre (or any point inside the cell) from the top-left corner.
        xm, ym = _num(p.get("x_m")), _num(p.get("y_m"))
        if xm is not None and ym is not None and 0 <= xm <= MAX_CELLS and 0 <= ym <= MAX_CELLS:
            col, row = int(xm / cell_m + 1e-9) + 1, int(ym / cell_m + 1e-9) + 1
    col = _num(p.get("x")) if col is None else col
    row = _num(p.get("y")) if row is None else row
    if col is None or row is None:
        idx = _num(p.get("idx", p.get("cell", p.get("index"))))
        if idx is None or cols <= 0:
            return None
        col, row = (int(idx) - 1) % cols + 1, (int(idx) - 1) // cols + 1
    col, row = int(col), int(row)
    if not (1 <= col <= MAX_CELLS and 1 <= row <= MAX_CELLS):
        return None
    return col, row


def _point_aps(raw, seen: dict | None = None) -> list[dict]:
    seen = seen or {}
    if isinstance(raw, dict):
        # {bssid: {ssid, rssi, channel}} (app 0.3) or {bssid: rssi} with names in the survey's "seen" (app 0.4+)
        raw = [{"bssid": k, **v} if isinstance(v, dict) else {"bssid": k, "rssi": v} for k, v in raw.items()]
    out = []
    for a in raw or []:
        if not isinstance(a, dict) or _num(a.get("rssi")) is None:
            continue
        rec = seen.get(a.get("bssid")) if isinstance(seen.get(a.get("bssid")), dict) else {}
        out.append({"bssid": str(a.get("bssid", "")), "ssid": str(a.get("ssid") or rec.get("ssid") or ""),
                    "rssi": _num(a.get("rssi")), "channel": _num(a.get("channel", rec.get("channel")))})
    out.sort(key=lambda a: -a["rssi"])
    return out


def wifi_doc(d: dict) -> dict:
    """Wi-Fi session document (or the app's working copy): survey records + coverage map in one."""
    seen = d.get("seen") if isinstance(d.get("seen"), dict) else {}
    out: dict = {"note": d.get("note") or "", "scans": d.get("scans"), "started": d.get("started"), "wifi_map": None}
    m = d.get("map")
    if isinstance(m, dict) and (m.get("samples") or m.get("points")):
        # The area in metres belongs to the map; a document may also carry it next to the map.
        area = {k: d[k] for k in ("cell_m", "width_m", "length_m") if k in d and k not in m}
        out["wifi_map"] = wifi_map({**area, **m, "note": d.get("note") or "", "target": d.get("map_target") or ""}, seen)
    recs = [r for r in seen.values() if isinstance(r, dict)]
    if recs:
        out["survey"] = {**wifi_survey({"aps": recs}), "note": d.get("note") or "", "scans": d.get("scans"),
                         "started": _num(d.get("started")), "band": d.get("band", "2.4GHz")}
    return out


def _count(v) -> int:
    """A grid size: a whole number of cells, 0 when missing or absurd (Infinity / NaN never reach int())."""
    f = _num(v, 0)
    return int(f) if isinstance(f, (int, float)) and 0 < f <= 10 ** 6 else 0


def _metres(v) -> float | None:
    """A positive length in metres (the area of a metre map), else None."""
    f = _num(v)
    return float(f) if f is not None and 0 < f <= MAX_CELLS else None


def wifi_map(obj: dict, seen: dict | None = None) -> dict:
    """Coverage map in any of the shapes the Wi-Fi app wrote -> {cols, rows, note, points:[{col,row,ts,note,aps}]}.
    Columns and rows are 1-based; a point without per-AP data keeps its single "rssi".

    Metre maps (Wi-Fi app with the area in metres) also carry cell_m (side of a square cell, 0.5), width_m
    (along the columns) and length_m (along the rows); their points get x_m / y_m, the cell centre measured
    from the top-left corner. Older maps have only cols x rows: cell_m, width_m and length_m are None."""
    grid = obj.get("grid") if isinstance(obj.get("grid"), dict) else {}

    def field(key, alt=""):
        for v in (obj.get(key), grid.get(key), grid.get(alt)):
            if v is not None:
                return v
        return None

    cols = _count(field("cols", "w"))
    rows = _count(field("rows", "h"))
    width_m, length_m = _metres(field("width_m")), _metres(field("length_m"))
    cell_m = _metres(field("cell_m"))
    if cell_m is None and (width_m or length_m):
        cell_m = 0.5
    if cell_m:
        width_m = width_m or (cols * cell_m if cols else None)
        length_m = length_m or (rows * cell_m if rows else None)
        cols = cols or (math.ceil(width_m / cell_m - 1e-9) if width_m else 0)
        rows = rows or (math.ceil(length_m / cell_m - 1e-9) if length_m else 0)
    cols, rows = min(max(cols, 0), MAX_CELLS), min(max(rows, 0), MAX_CELLS)
    raw = obj.get("points", obj.get("samples", []))
    if isinstance(raw, dict):   # store shape: {"<1-based cell index>": {ts, aps}}
        raw = [{"idx": k, **v} for k, v in raw.items() if isinstance(v, dict)]
    points = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict):
            continue
        cell = _cell(p, cols, cell_m)
        if not cell:
            continue
        aps = _point_aps(p.get("aps"), seen)
        pt = {"col": cell[0], "row": cell[1], "ts": _num(p.get("ts")), "aps": aps,
              "note": str(p.get("note", p.get("label", p.get("location", ""))) or "")}
        if cell_m:
            pt["x_m"], pt["y_m"] = round((cell[0] - 0.5) * cell_m, 3), round((cell[1] - 0.5) * cell_m, 3)
        if not aps and _num(p.get("rssi")) is not None:
            pt["rssi"] = _num(p.get("rssi"))
        points.append(pt)
    cols = max([cols] + [p["col"] for p in points])
    rows = max([rows] + [p["row"] for p in points])
    if cell_m:   # a point outside the declared area widens it to the cells measured
        far_c, far_r = max([0] + [p["col"] for p in points]), max([0] + [p["row"] for p in points])
        width_m = max(width_m or 0, far_c * cell_m) or None
        length_m = max(length_m or 0, far_r * cell_m) or None
    notes = [n for n in obj.get("notes", []) if isinstance(n, dict)] if isinstance(obj.get("notes"), list) else []
    return {"cols": cols, "rows": rows, "cell_m": cell_m, "width_m": width_m, "length_m": length_m,
            "band": obj.get("band", "2.4GHz"), "device": obj.get("device", ""),
            "note": obj.get("note") or "", "target": obj.get("target") or obj.get("map_target") or "",
            "points": sorted(points, key=lambda p: (p["row"], p["col"])), "notes": notes,
            "plan": _map_plan(obj.get("plan"), cell_m)}


PLAN_KINDS = ("wall", "door", "window", "beam")


def _map_plan(raw, cell_m: float | None) -> list[dict]:
    """Floor plan lines on a map in metres: export {kind, x1_m..y2_m}, the app's working copy
    {k, x1..y2} in metres (Wi-Fi 0.9+), or {k, c1, r1, c2, r2} between centres of 0-based cells (0.7-0.8)."""
    out = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict) or len(out) >= 300:
            continue
        kind = p.get("kind", p.get("k"))
        if kind not in PLAN_KINDS:
            continue
        if all(k in p for k in ("x1_m", "y1_m", "x2_m", "y2_m")):
            xy = [_num(p[k]) for k in ("x1_m", "y1_m", "x2_m", "y2_m")]
        elif all(k in p for k in ("x1", "y1", "x2", "y2")):
            xy = [_num(p[k]) for k in ("x1", "y1", "x2", "y2")]
        elif cell_m:
            xy = [None if _num(p.get(k)) is None else (_num(p[k]) + 0.5) * cell_m for k in ("c1", "r1", "c2", "r2")]
        else:
            continue
        if any(v is None or not math.isfinite(v) or not 0 <= v <= MAX_CELLS for v in xy):
            continue
        out.append({"kind": kind, **dict(zip(("x1_m", "y1_m", "x2_m", "y2_m"), (round(v, 3) for v in xy), strict=False))})
    return out


# ---------- detection and conversion (WiFiLab) ----------

def detect(name: str, data: bytes) -> tuple[str, object]:
    """(kind, parsed) with kind one of wifi_doc, wifi_map, wifi_survey, plan; ValueError for anything else."""
    text = decode_text(data)
    if name.lower().endswith(".csv") or (text[:1] not in ("{", "[") and "," in text.split("\n", 1)[0]):
        table = parse_csv(text)
        cols = {c.strip().lower() for c in table["columns"]}
        if {"bssid", "ssid"} <= cols:
            return "wifi_survey", table
        raise ValueError("a CSV with bssid and ssid columns is expected (FieldTab wifi-survey CSV)")
    try:
        d = json.loads(text)
    except ValueError as e:
        raise ValueError(f"not JSON: {e}") from None
    if not isinstance(d, dict):
        raise ValueError("a JSON object is expected")
    kind = str(d.get("kind", "")).lower()
    keys = set(d)
    if d.get("format") == plans.FORMAT:
        return "plan", d
    if kind == "wifi" or ("seen" in keys and "map" in keys):
        return "wifi_doc", d
    if kind == "wifi_survey" or {"aps", "channels"} <= keys:
        return "wifi_survey", d
    if kind in ("wifi_map", "wifi_coverage") \
            or ("points" in keys and ({"cols", "rows"} <= keys or {"width_m", "length_m"} <= keys or "grid" in keys)) \
            or ("samples" in keys and "cols" in keys):
        return "wifi_map", d
    if kind == "wifilab_project":
        return "wifilab_project", d
    raise ValueError("not a FieldTab Wi-Fi survey, coverage map, session document or floor plan")


def _survey_ap(a: dict) -> dict:
    """A tablet survey record -> WiFiLab inventory record (2.4 GHz radio: band from the channel)."""
    return {"bssid": str(a.get("bssid", "")).lower(), "ssid": a.get("ssid") or "", "hidden": bool(a.get("hidden")),
            "channel": a.get("channel") or 0, "band": analysis.band_of(a.get("channel")), "width": a.get("width") or 20,
            "second": a.get("second") or "", "security": a.get("auth") or "", "phy": a.get("phy") or "",
            "country": a.get("country") or "", "rssi_min": a.get("rssi_min"), "rssi_avg": a.get("rssi_avg"),
            "rssi_max": a.get("rssi_max"), "rssi_last": a.get("rssi_last"), "seen_count": a.get("seen_count"),
            "first_seen": a.get("first_seen"), "last_seen": a.get("last_seen")}


def _map_to_parts(m: dict) -> tuple[dict, list[dict]]:
    """Normalized wifi_map() -> (plan, points) in metres; a cell-only map is read at 0.5 m per cell."""
    cell = m.get("cell_m") or 0.5
    width = m.get("width_m") or max(1, m.get("cols") or 1) * cell
    length = m.get("length_m") or max(1, m.get("rows") or 1) * cell
    width, length = min(max(width, plans.MIN_M), plans.MAX_M), min(max(length, plans.MIN_M), plans.MAX_M)
    items = []
    for it in m.get("plan") or []:
        xy = [min(max(it[k], 0), width if k[0] == "x" else length) for k in ("x1_m", "y1_m", "x2_m", "y2_m")]
        if (xy[0], xy[1]) != (xy[2], xy[3]):
            items.append({"kind": it["kind"], "x1": xy[0], "y1": xy[1], "x2": xy[2], "y2": xy[3]})
    plan = {"format": plans.FORMAT, "name": (m.get("note") or "")[:64], "width_m": round(width, 2),
            "length_m": round(length, 2), "items": items[:plans.MAX_ITEMS]}
    points = []
    for i, p in enumerate(m.get("points") or []):
        x = p.get("x_m", (p["col"] - 0.5) * cell)
        y = p.get("y_m", (p["row"] - 0.5) * cell)
        aps = [{"bssid": str(a.get("bssid", "")).lower(), "ssid": a.get("ssid") or "", "rssi": a["rssi"],
                "channel": a.get("channel"), "band": analysis.band_of(a.get("channel"))} for a in p.get("aps") or []]
        # A point with only a bare RSSI (oldest maps) has no AP to attribute it to: it stays a point with no APs.
        points.append({"id": f"t{i + 1}", "x_m": round(min(x, width), 3), "y_m": round(min(y, length), 3),
                       "ts": p.get("ts"), "note": p.get("note") or "", "noise": None, "aps": aps})
    return plan, points


def to_project(name: str, kind: str, payload) -> dict:
    """A detected tablet export -> a new WiFiLab project dict (not yet stored)."""
    proj: dict = {"name": name[:80] or "Imported survey", "source": "fieldtab", "plan": None, "points": [], "aps": [],
                  "survey_aps": [], "note": ""}
    if kind == "wifi_survey":
        s = wifi_survey(payload)
        proj["survey_aps"] = [_survey_ap(a) for a in s["aps"]]
        proj["note"] = s.get("note") or ""
    elif kind == "wifi_map":
        m = wifi_map(payload)
        proj["plan"], proj["points"] = _map_to_parts(m)
        proj["note"] = m.get("note") or ""
        proj["target_ssid"] = m.get("target") or ""
    elif kind == "wifi_doc":
        w = wifi_doc(payload)
        if w.get("wifi_map"):
            proj["plan"], proj["points"] = _map_to_parts(w["wifi_map"])
            proj["target_ssid"] = w["wifi_map"].get("target") or ""
        if w.get("survey"):
            proj["survey_aps"] = [_survey_ap(a) for a in w["survey"]["aps"]]
        proj["note"] = w.get("note") or ""
    elif kind == "plan":
        plan, errs = plans.validate_plan(payload)
        if errs:
            raise ValueError("; ".join(errs))
        proj["plan"] = plan
        proj["source"] = "plan"
    else:
        raise ValueError(f"cannot import {kind}")
    if proj["plan"] is None:
        proj["plan"] = {"format": plans.FORMAT, "name": "", "width_m": 20, "length_m": 12, "items": []}
    return proj
