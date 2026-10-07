"""Survey projects: a floor plan, measured points (each holds the scan made there), placed APs and an optional
imported AP inventory. Pure functions over the project dict; the store keeps it as one JSON document."""
from __future__ import annotations

import time
import uuid

from . import analysis, apmarks, oui, plans
from .scanloop import enrich

POINT_AP_KEYS = ("bssid", "ssid", "rssi", "noise", "channel", "band", "width", "security", "phy")
MAX_POINTS = 2000
MAX_BG = 6 * 1024 * 1024   # background image data URL


def new_project(name: str, plan: dict | None = None) -> dict:
    if plan is None:
        plan = {"format": plans.FORMAT, "name": name[:64], "width_m": 20, "length_m": 12, "items": []}
    return {"name": (name or "Untitled survey")[:80], "source": "wifilab", "plan": plan, "points": [], "aps": [],
            "survey_aps": [], "note": "", "target_ssid": ""}


def point_from_scan(x_m: float, y_m: float, scan: dict, note: str = "") -> dict:
    noise = None
    conn = scan.get("connection") or {}
    if isinstance(conn.get("noise"), (int, float)) and conn["noise"] < 0:
        noise = conn["noise"]
    aps = []
    for a in scan.get("aps", []):
        rec = {k: a.get(k) for k in POINT_AP_KEYS if a.get(k) not in (None, "")}
        rec["ssid"] = a.get("ssid") or ""
        aps.append(rec)
        if noise is None and isinstance(a.get("noise"), (int, float)) and a["noise"] < 0:
            noise = a["noise"]
    aps.sort(key=lambda a: -a["rssi"])
    return {"id": uuid.uuid4().hex[:10], "x_m": round(float(x_m), 3), "y_m": round(float(y_m), 3),
            "ts": scan.get("ts") or time.time(), "note": str(note or "")[:120], "noise": noise, "aps": aps,
            "anonymous": scan.get("anonymous", 0)}


def check_xy(proj: dict, x_m, y_m) -> tuple[float, float]:
    plan = proj.get("plan") or {}
    w, ln = float(plan.get("width_m") or 100), float(plan.get("length_m") or 100)
    try:
        x, y = float(x_m), float(y_m)
    except (TypeError, ValueError):
        raise ValueError("x_m and y_m must be numbers") from None
    if not (0 <= x <= w + 0.01 and 0 <= y <= ln + 0.01):
        raise ValueError(f"point ({x:g}, {y:g}) is outside the plan ({w:g} x {ln:g} m)")
    return min(x, w), min(y, ln)


def add_point(proj: dict, point: dict) -> dict:
    if len(proj.setdefault("points", [])) >= MAX_POINTS:
        raise ValueError(f"at most {MAX_POINTS} points per survey")
    proj["points"].append(point)
    return proj


def set_plan(proj: dict, raw) -> list[str]:
    plan, errs = plans.validate_plan(raw)
    if errs:
        return errs
    proj["plan"] = plan
    return []


def set_aps(proj: dict, raw) -> None:
    proj["aps"] = apmarks.validate_aps(raw)


def set_bg(proj: dict, data_url: str | None) -> None:
    if data_url in (None, ""):
        proj.pop("bg", None)
        return
    if not isinstance(data_url, str) or not data_url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,",
                                                                 "data:image/webp;base64,")):
        raise ValueError("background must be a PNG, JPEG or WebP data URL")
    if len(data_url) > MAX_BG:
        raise ValueError(f"background image is larger than {MAX_BG // (1024 * 1024)} MB")
    proj["bg"] = data_url


def inventory(proj: dict, oui_extra=None) -> list[dict]:
    """Every BSSID of the project: aggregated over the measured points, merged with an imported AP list."""
    by: dict[str, dict] = {}
    for p in proj.get("points") or []:
        for a in p.get("aps") or []:
            b = str(a.get("bssid", "")).lower()
            if not b or not isinstance(a.get("rssi"), (int, float)):
                continue
            r = by.get(b)
            if r is None:
                r = by[b] = {"bssid": b, "rssi_min": a["rssi"], "rssi_max": a["rssi"], "_sum": 0, "seen_count": 0,
                             "first_seen": p.get("ts"), "last_seen": p.get("ts")}
            for k in ("ssid", "channel", "band", "width", "security", "phy", "noise"):
                if a.get(k) not in (None, ""):
                    r[k] = a[k]
            r["rssi_min"] = min(r["rssi_min"], a["rssi"])
            r["rssi_max"] = max(r["rssi_max"], a["rssi"])
            r["_sum"] += a["rssi"]
            r["seen_count"] += 1
            r["rssi_last"] = a["rssi"]
            if p.get("ts"):
                r["first_seen"] = min(x for x in (r.get("first_seen"), p["ts"]) if x)
                r["last_seen"] = max(x for x in (r.get("last_seen"), p["ts"]) if x)
    for r in by.values():
        r["rssi_avg"] = round(r.pop("_sum") / r["seen_count"]) if r["seen_count"] else None
    for a in proj.get("survey_aps") or []:
        b = str(a.get("bssid", "")).lower()
        if not b:
            continue
        r = by.setdefault(b, {"bssid": b})
        for k, v in a.items():
            if v not in (None, "") and (k not in r or k in ("security", "width", "second", "phy", "country")):
                r[k] = v
        for k, fn in (("rssi_max", max), ("rssi_min", min)):
            if isinstance(a.get(k), (int, float)):
                r[k] = fn(r[k], a[k]) if isinstance(r.get(k), (int, float)) else a[k]
    out = []
    for r in by.values():
        r.setdefault("ssid", "")
        r["hidden"] = not r["ssid"]
        r.setdefault("band", analysis.band_of(r.get("channel")))
        r.setdefault("width", 20)
        r.setdefault("security", "")
        r["rssi"] = r.get("rssi_max", -100)
        if not r.get("channel"):
            continue
        out.append(enrich(r, oui_extra))
    return sorted(out, key=lambda a: -(a.get("rssi_max") if a.get("rssi_max") is not None else -999))


def ssids(proj: dict) -> list[str]:
    seen: dict[str, int] = {}
    for p in proj.get("points") or []:
        for a in p.get("aps") or []:
            if a.get("ssid"):
                seen[a["ssid"]] = max(seen.get(a["ssid"], -999), a.get("rssi", -999))
    return sorted(seen, key=lambda s: -seen[s])


def issues(proj: dict, ssid: str | None = None, oui_extra=None) -> list[dict]:
    inv = inventory(proj, oui_extra)
    out = analysis.environment_issues(inv) + analysis.coverage_issues(proj.get("points") or [], ssid or None)
    return sorted(out, key=lambda i: analysis.SEVERITY_ORDER[i["severity"]])


def summary(proj: dict, ssid: str | None = None) -> dict:
    inv = inventory(proj)
    pts = proj.get("points") or []
    vals = [analysis.point_best(p, ssid or None) for p in pts]
    heard = [v for v in vals if v is not None]
    return {"points": len(pts), "bssids": len(inv), "ssids": len({a["ssid"] for a in inv if a["ssid"]}),
            "radios": len({analysis.radio_key(a["bssid"]) for a in inv}),
            "bands": {b: sum(1 for a in inv if a["band"] == b) for b in analysis.BANDS},
            "best_avg": round(sum(heard) / len(heard)) if heard else None,
            "weak_points": sum(1 for v in vals if v is None or v < analysis.FAIR),
            "vendor_counts": _count(oui.vendor(a["bssid"]) or "unknown" for a in inv)}


def _count(it) -> dict:
    out: dict[str, int] = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def export_fieldtab_map(proj: dict) -> dict:
    """The project as a FieldTab `wifi_map` export (metre map) so the tablet tooling can read it back."""
    plan = proj.get("plan") or {}
    pts = [{"x_m": p["x_m"], "y_m": p["y_m"], "ts": p.get("ts"), "note": p.get("note", ""),
            "aps": [{"bssid": a["bssid"], "ssid": a.get("ssid", ""), "rssi": a["rssi"], "channel": a.get("channel")}
                    for a in p.get("aps") or []]} for p in proj.get("points") or []]
    return {"kind": "wifi_map", "device": "wifilab", "note": proj.get("name", ""), "cell_m": 0.5,
            "width_m": plan.get("width_m"), "length_m": plan.get("length_m"), "points": pts,
            "plan": [{"kind": it["kind"], "x1_m": it["x1"], "y1_m": it["y1"], "x2_m": it["x2"], "y2_m": it["y2"]}
                     for it in plan.get("items") or []]}
