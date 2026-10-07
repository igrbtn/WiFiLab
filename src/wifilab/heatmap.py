"""Server-side engineering heatmaps (Pillow) for the HTML and Word reports.

A port of the value and interpolation logic of static/js/heat.js (keep them in step): per-point values for a view,
inverse-distance weighting with fade-out away from the points, serving AP zones. The browser draws the interactive
maps; this module renders the same views as PNG so a report has heatmaps without a browser.
"""
from __future__ import annotations

import io
import math

NOT_HEARD = -95
ENG_STOPS = [(-85, (214, 40, 40)), (-75, (245, 128, 32)), (-67, (242, 212, 46)), (-60, (62, 186, 82)),
             (-50, (36, 196, 220)), (-35, (32, 92, 218))]
SNR_STOPS = [(5, (214, 40, 40)), (15, (245, 128, 32)), (20, (242, 212, 46)), (25, (62, 186, 82)),
             (35, (36, 196, 220)), (45, (32, 92, 218))]
COUNT_STOPS = [(0, (214, 40, 40)), (1, (245, 128, 32)), (2, (62, 186, 82)), (3, (36, 196, 220)), (5, (32, 92, 218))]
OVERLAP_STOPS = [(0, (62, 186, 82)), (1, (242, 212, 46)), (2, (245, 128, 32)), (4, (214, 40, 40))]
COVER_FULL, COVER_ZERO = 3.0, 5.0
COUNT_MIN, OVERLAP_MIN, DEFAULT_NOISE = -75, -82, -95
VIEWS = {
    "rssi": {"unit": "dBm", "stops": ENG_STOPS, "floor": NOT_HEARD},
    "snr": {"unit": "dB", "stops": SNR_STOPS, "floor": 0},
    "count": {"unit": "radios", "stops": COUNT_STOPS, "floor": 0},
    "overlap": {"unit": "co-channel radios", "stops": OVERLAP_STOPS, "floor": 0},
    "serving": {"unit": "", "stops": ENG_STOPS, "floor": NOT_HEARD},
}
AP_PALETTE = [(47, 128, 237), (242, 120, 32), (46, 179, 92), (174, 72, 210), (240, 196, 30), (16, 170, 160),
              (230, 73, 128), (120, 130, 250)]
MAX_AP_VIEWS = 6
SERVER_CELLS = 6000   # interpolation grid size; the image is smoothed up from it


def ramp(stops, v: float) -> tuple[int, int, int]:
    if v <= stops[0][0]:
        return stops[0][1]
    for (v0, c0), (v1, c1) in zip(stops, stops[1:], strict=False):
        if v <= v1:
            t = (v - v0) / (v1 - v0)
            return tuple(round(a + (b - a) * t) for a, b in zip(c0, c1, strict=True))
    return stops[-1][1]


def radio_key(bssid: str) -> str:
    return ":".join(str(bssid or "").lower().split(":")[:5])


def _hit(a: dict, target: dict) -> bool:
    t = target.get("type", "best")
    return (t == "best" or (t == "ssid" and a.get("ssid") == target.get("value"))
            or (t == "set" and str(a.get("bssid", "")).lower() in target.get("bssids", ())))


def point_value(p: dict, target: dict) -> dict:
    best = None
    for a in p.get("aps") or []:
        if isinstance(a.get("rssi"), (int, float)) and _hit(a, target) and (best is None or a["rssi"] > best["rssi"]):
            best = a
    return {"v": NOT_HEARD, "missing": True} if best is None else {"v": best["rssi"], "ap": best}


def view_value(kind: str, p: dict, target: dict) -> dict:
    if kind == "snr":
        pv = point_value(p, target)
        if pv.get("missing"):
            return {"v": 0, "missing": True}
        noise = p.get("noise") if isinstance(p.get("noise"), (int, float)) and p["noise"] < 0 else DEFAULT_NOISE
        return {"v": pv["v"] - noise}
    if kind == "count":
        return {"v": len({radio_key(a.get("bssid")) for a in p.get("aps") or []
                          if _hit(a, target) and isinstance(a.get("rssi"), (int, float)) and a["rssi"] >= COUNT_MIN})}
    if kind == "overlap":
        pv = point_value(p, target)
        if pv.get("missing"):
            return {"v": 0, "missing": True}
        ap, mine = pv["ap"], radio_key(pv["ap"].get("bssid"))
        return {"v": len({radio_key(a.get("bssid")) for a in p.get("aps") or []
                          if a.get("channel") == ap.get("channel") and (a.get("band") or "") == (ap.get("band") or "")
                          and isinstance(a.get("rssi"), (int, float)) and a["rssi"] >= OVERLAP_MIN
                          and radio_key(a.get("bssid")) != mine})}
    return point_value(p, target)


def cover_alpha(d: float) -> float:
    if d <= COVER_FULL:
        return 1.0
    if d >= COVER_ZERO:
        return 0.0
    t = (d - COVER_FULL) / (COVER_ZERO - COVER_FULL)
    return 1 - t * t * (3 - 2 * t)


def idw(samples: list[dict], x: float, y: float, floor: float = NOT_HEARD) -> tuple[float, float]:
    num = den = 0.0
    dmin = math.inf
    for s in samples:
        d2 = (s["x"] - x) ** 2 + (s["y"] - y) ** 2
        if d2 < 1e-12:
            return s["v"], 0.0
        dmin = min(dmin, d2)
        w = 1 / d2
        if s.get("missing"):
            w *= cover_alpha(math.sqrt(d2))
        num += s["v"] * w
        den += w
    return (num / den if den else floor), math.sqrt(dmin)


def area(proj: dict) -> tuple[float, float, list[dict], bool]:
    """(width_m, length_m, plan items, has_plan); without a plan, a box around the points."""
    plan = proj.get("plan") or {}
    if isinstance(plan.get("width_m"), (int, float)) and isinstance(plan.get("length_m"), (int, float)):
        return float(plan["width_m"]), float(plan["length_m"]), list(plan.get("items") or []), True
    pts = proj.get("points") or []
    w = max([p.get("x_m", 0) for p in pts] + [0]) + 2
    ln = max([p.get("y_m", 0) for p in pts] + [0]) + 2
    return max(2.0, math.ceil(w)), max(2.0, math.ceil(ln)), [], False


def report_views(proj: dict, ssid: str = "") -> list[dict]:
    """The heatmaps a report shows (same list as reportViews() in static/js/report.js)."""
    out = [{"kind": "rssi", "target": {"type": "best"}, "title": "Signal: strongest AP at each point"}]
    if ssid:
        out.append({"kind": "rssi", "target": {"type": "ssid", "value": ssid}, "title": f"Signal: SSID {ssid}"})
    t = {"type": "ssid", "value": ssid} if ssid else {"type": "best"}
    who = f"SSID {ssid}" if ssid else "strongest AP"
    out += [{"kind": "snr", "target": t, "title": f"SNR: {who}"},
            {"kind": "count", "target": t,
             "title": f"AP count (radios at -75 dBm or better): {who if ssid else 'all networks'}"},
            {"kind": "overlap", "target": t,
             "title": f"Channel overlap (co-channel radios at -82 dBm or better): {who}"}]
    placed = [a for a in proj.get("aps") or [] if a.get("bssids")]
    if proj.get("aps"):
        out.append({"kind": "serving", "target": t, "title": "Serving AP zones (placed APs)"})
    for ap in placed[:MAX_AP_VIEWS]:
        out.append({"kind": "rssi", "target": {"type": "set", "bssids": [b.lower() for b in ap["bssids"]]},
                    "title": f"Signal: placed AP {ap.get('name') or ap.get('id')}"})
    return out


def _grid(w: float, ln: float) -> tuple[int, int]:
    step = max(0.1, math.sqrt(w * ln / SERVER_CELLS))
    return max(1, math.ceil(w / step - 1e-9)), max(1, math.ceil(ln / step - 1e-9))


def field_rgba(proj: dict, view: dict) -> tuple[int, int, bytes, list[dict], list[dict] | None]:
    """Interpolated RGBA grid (nx, ny, bytes), point samples and serving zones for one view."""
    w, ln, _, _ = area(proj)
    points = [p for p in proj.get("points") or []
              if isinstance(p.get("x_m"), (int, float)) and isinstance(p.get("y_m"), (int, float))]
    kind = view.get("kind", "rssi")
    d = VIEWS.get(kind, VIEWS["rssi"])
    target = dict(view.get("target") or {"type": "best"})
    if target.get("type") == "set":
        target["bssids"] = {str(b).lower() for b in target.get("bssids") or ()}
    pts = [{"x": p["x_m"], "y": p["y_m"], **view_value("rssi" if kind == "serving" else kind, p, target)}
           for p in points]
    nx, ny = _grid(w, ln)
    buf = bytearray(nx * ny * 4)
    zones = None
    if not pts:
        return nx, ny, bytes(buf), pts, zones
    if kind == "serving":
        placed = proj.get("aps") or []
        if not placed:
            return nx, ny, bytes(buf), pts, zones
        per_ap = [[{"x": p["x_m"], "y": p["y_m"], **point_value(p, {"type": "set", "bssids": {
            b.lower() for b in ap.get("bssids") or ()}})} for p in points] for ap in placed]
        zones = [{"name": ap.get("name") or "(unnamed)", "color": AP_PALETTE[i % len(AP_PALETTE)]}
                 for i, ap in enumerate(placed)]
    for j in range(ny):
        y = (j + 0.5) * ln / ny
        for i in range(nx):
            x = (i + 0.5) * w / nx
            k = 4 * (j * nx + i)
            if kind == "serving":
                best_v, best_n, dmin = NOT_HEARD, -1, math.inf
                for n, smp in enumerate(per_ap):
                    v, dm = idw(smp, x, y)
                    dmin = min(dmin, dm)
                    if v > best_v + 1e-6 and v > NOT_HEARD + 0.5:
                        best_v, best_n = v, n
                a = cover_alpha(dmin)
                rgb = ENG_STOPS[0][1] if best_n < 0 else AP_PALETTE[best_n % len(AP_PALETTE)]
                weak = 0.45 if best_n >= 0 and best_v < -80 else 1
            else:
                v, dm = idw(pts, x, y, d["floor"])
                a, rgb, weak = cover_alpha(dm), ramp(d["stops"], v), 1
            buf[k:k + 4] = bytes((*rgb, round(255 * 0.9 * a * weak)))
    return nx, ny, bytes(buf), pts, zones


# ---------- drawing ----------

BG, ROOM, WALL, DOOR, WINDOW, BEAM = (107, 111, 117), (134, 138, 145), (43, 47, 54), (240, 138, 36), (168, 220, 247), \
    (160, 166, 174)
WHITE = (255, 255, 255)


def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:   # Pillow without FreeType
        return ImageFont.load_default()


def _nice(px_per_m: float, minimum: float) -> int:
    return next((m for m in (1, 2, 5, 10, 20, 50, 100) if m * px_per_m >= minimum), 200)


def render_png(proj: dict, view: dict, width: int = 1400) -> bytes:
    """One heatmap view as PNG: rulers, plan (or a metre grid without a plan), heat, points, placed APs,
    legend and scale bar."""
    from PIL import Image, ImageDraw

    w, ln, items, has_plan = area(proj)
    s = width / 860                      # scale relative to the browser's report canvas
    rl, rt, pad, rr, legend_h = 34 * s, 26 * s, 14 * s, 14 * s, 80 * s
    pxm = min((width - rl - pad - pad - rr) / w, 680 * s / ln)
    ox, oy = rl + pad, rt + pad
    rw, rh = w * pxm, ln * pxm
    W, H = width, math.ceil(oy + rh + pad + legend_h)
    img = Image.new("RGB", (W, H), BG)
    g = ImageDraw.Draw(img)
    f_small, f_mid, f_title = _font(round(11 * s)), _font(round(12 * s)), _font(round(13 * s))

    # rulers
    every = _nice(pxm, 26 * s)
    for m in range(0, math.floor(w) + 1):
        x = ox + m * pxm
        g.line([(x, rt - 2 * s - (5 if m % every == 0 else 2.5) * s), (x, rt - 2 * s)], fill=(200, 202, 206), width=1)
        if m % every == 0:
            g.text((x, rt - 9 * s), str(m), fill=WHITE, font=f_small, anchor="ms")
    for m in range(0, math.floor(ln) + 1):
        y = oy + m * pxm
        g.line([(rl - 2 * s - (5 if m % every == 0 else 2.5) * s, y), (rl - 2 * s, y)], fill=(200, 202, 206), width=1)
        if m % every == 0:
            g.text((rl - 9 * s, y), str(m), fill=WHITE, font=f_small, anchor="rm")
    g.text((4 * s, rt - 9 * s), "m", fill=WHITE, font=f_small, anchor="ls")

    g.rectangle([ox, oy, ox + rw, oy + rh], fill=ROOM)

    nx, ny, rgba, pts, zones = field_rgba(proj, view)
    if pts:
        heat = Image.frombytes("RGBA", (nx, ny), rgba).resize((max(1, round(rw)), max(1, round(rh))),
                                                              Image.BILINEAR)
        img.paste(heat, (round(ox), round(oy)), heat)
    if not has_plan:   # no floor plan: a metre grid gives the scale
        for m in range(0, math.floor(w) + 1):
            g.line([(ox + m * pxm, oy), (ox + m * pxm, oy + rh)], fill=(96, 100, 106), width=1)
        for m in range(0, math.floor(ln) + 1):
            g.line([(ox, oy + m * pxm), (ox + rw, oy + m * pxm)], fill=(96, 100, 106), width=1)

    wall_w = max(2, round(min(9 * s, 0.25 * pxm)))
    g.rectangle([ox, oy, ox + rw, oy + rh], outline=WALL, width=1 if items else wall_w)

    def seg(it, color, width):
        g.line([(ox + it["x1"] * pxm, oy + it["y1"] * pxm), (ox + it["x2"] * pxm, oy + it["y2"] * pxm)],
               fill=color, width=max(1, round(width)))

    for it in items:
        if it.get("kind") == "wall":
            seg(it, WALL, wall_w)
    for it in items:
        k = it.get("kind")
        if k == "door":
            seg(it, ROOM, wall_w + 2)
            seg(it, DOOR, max(2, wall_w * 0.4))
        elif k == "window":
            seg(it, WALL, max(2, wall_w * 0.7))
            seg(it, WINDOW, max(1, wall_w * 0.7 - 2))
        elif k == "beam":
            seg(it, BEAM, max(2, wall_w * 0.35))

    ordered = sorted((p for p in proj.get("points") or [] if isinstance(p.get("ts"), (int, float))),
                     key=lambda p: p["ts"])
    if len(ordered) > 1:
        g.line([(ox + p["x_m"] * pxm, oy + p["y_m"] * pxm) for p in ordered], fill=(24, 27, 32), width=max(1, round(s)))
    r = max(3.5, min(5.5, pxm / s * 0.09)) * s
    for q in pts:
        x, y = ox + q["x"] * pxm, oy + q["y"] * pxm
        g.ellipse([x - r, y - r, x + r, y + r], fill=(29, 32, 38) if q.get("missing") else (31, 111, 235),
                  outline=WHITE, width=max(1, round(1.75 * s)))
    if not pts:
        g.text((ox + rw / 2, oy + rh / 2), "No measured points", fill=WHITE, font=f_title, anchor="mm")

    apr = 10 * s
    for i, ap in enumerate(proj.get("aps") or []):
        x, y = ox + ap["x_m"] * pxm, oy + ap["y_m"] * pxm
        col = AP_PALETTE[i % len(AP_PALETTE)] if view.get("kind") == "serving" else (29, 32, 38)
        g.ellipse([x - apr, y - apr, x + apr, y + apr], fill=col, outline=WHITE, width=max(1, round(2 * s)))
        for rad in (4 * s, 7 * s):
            g.arc([x - rad, y + 3.5 * s - rad, x + rad, y + 3.5 * s + rad], 220, 320, fill=WHITE,
                  width=max(1, round(1.6 * s)))
        if ap.get("name"):
            g.text((x, y + apr + 3 * s), ap["name"], fill=WHITE, font=f_mid, anchor="ma",
                   stroke_width=max(1, round(2 * s)), stroke_fill=(20, 22, 26))

    # legend
    d = VIEWS.get(view.get("kind", "rssi"), VIEWS["rssi"])
    yt = oy + rh + pad + 12 * s
    yb = yt + 20 * s
    bh = 9 * s
    g.text((rl - 6 * s, yt), str(view.get("title", ""))[:110], fill=WHITE, font=f_title, anchor="lm")
    x0 = rl + 30 * s
    if zones is not None:
        x = rl - 6 * s
        for z in zones + [{"name": "none heard", "color": ENG_STOPS[0][1]}]:
            tw = g.textlength(z["name"], font=f_small)
            if x + tw + 22 * s > W - rr - 120 * s:
                g.text((x, yb + bh / 2), "...", fill=WHITE, font=f_small, anchor="lm")
                break
            g.rectangle([x, yb, x + 12 * s, yb + bh], fill=z["color"])
            g.text((x + 16 * s, yb + bh / 2), z["name"], fill=WHITE, font=f_small, anchor="lm")
            x += tw + 30 * s
        bar_end = x
    else:
        stops = d["stops"]
        lo, hi = stops[0][0], stops[-1][0]
        bw = min(300 * s, W - x0 - rr - 140 * s)
        unit = "dBm" if d["unit"] == "dBm" else "dB" if d["unit"] == "dB" else "n"
        g.text((rl - 6 * s, yb + bh / 2), unit, fill=WHITE, font=f_small, anchor="lm")
        for i in range(round(bw)):
            g.line([(x0 + i, yb), (x0 + i, yb + bh)], fill=ramp(stops, lo + (hi - lo) * i / max(1, bw - 1)))
        for v, _ in stops:
            x = x0 + bw * (v - lo) / (hi - lo)
            g.line([(x, yb + bh), (x, yb + bh + 4 * s)], fill=WHITE)
            label = f"{v} or less" if v == lo and d["unit"] == "dBm" else f"{v}+" if v == hi and d["unit"] != "dBm" \
                else str(v)
            g.text((x, yb + bh + 6 * s), label, fill=WHITE, font=f_small, anchor="ma")
        bar_end = x0 + bw
    m = _nice(pxm, 60 * s)
    xr = W - rr
    xs = xr - m * pxm
    ys = yb + 3 * s
    if xs > bar_end + 40 * s:
        g.line([(xs, ys), (xr, ys)], fill=WHITE, width=max(1, round(s)))
        g.line([(xs, ys - 4 * s), (xs, ys + 4 * s)], fill=WHITE, width=max(1, round(s)))
        g.line([(xr, ys - 4 * s), (xr, ys + 4 * s)], fill=WHITE, width=max(1, round(s)))
        g.text(((xs + xr) / 2, ys + 7 * s), f"{m} m", fill=WHITE, font=f_small, anchor="ma")

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def report_images(proj: dict, ssid: str = "", width: int = 1400) -> list[dict]:
    """[{title, png bytes}] for every report view; empty when the project has no measured points."""
    if not proj or not proj.get("points"):
        return []
    return [{"title": v["title"], "png": render_png(proj, v, width)} for v in report_views(proj, ssid)]
