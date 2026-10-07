"""Access points placed on a survey floor plan: position in metres plus the BSSIDs bound to them (so a heatmap can
show one physical AP, a group of them, or which placed AP serves each spot). Same shape as FieldTab Desk."""
from __future__ import annotations

import math
import re

AP_ID = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
BSSID = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
MAX_APS, MAX_BSSIDS, MAX_NAME, MAX_M = 64, 32, 48, 1000.0


def _coord(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= MAX_M:
        raise ValueError(f"{what} must be a number 0..{MAX_M:g}")
    return round(float(v), 3)


def validate_aps(raw) -> list[dict]:
    """[{id, name, x_m, y_m, bssids}] -> normalized list; ValueError with a readable reason."""
    if not isinstance(raw, list):
        raise ValueError("aps must be a list")
    if len(raw) > MAX_APS:
        raise ValueError(f"{len(raw)} access points, at most {MAX_APS}")
    out, ids = [], set()
    for i, a in enumerate(raw, 1):
        if not isinstance(a, dict):
            raise ValueError(f"AP {i}: not an object")
        ap_id = a.get("id")
        if not isinstance(ap_id, str) or not AP_ID.match(ap_id):
            raise ValueError(f"AP {i}: bad id")
        if ap_id in ids:
            raise ValueError(f"AP {i}: duplicate id {ap_id}")
        ids.add(ap_id)
        name = a.get("name", "")
        if not isinstance(name, str):
            raise ValueError(f"AP {i}: name must be text")
        name = "".join(ch for ch in name if ch.isprintable()).strip()[:MAX_NAME]
        bssids = a.get("bssids", [])
        if not isinstance(bssids, list) or len(bssids) > MAX_BSSIDS:
            raise ValueError(f"AP {i}: bssids must be a list of at most {MAX_BSSIDS}")
        clean = []
        for b in bssids:
            b = b.lower() if isinstance(b, str) else ""
            if not BSSID.match(b):
                raise ValueError(f"AP {i}: bad BSSID {b!r}")
            if b not in clean:
                clean.append(b)
        out.append({"id": ap_id, "name": name, "x_m": _coord(a.get("x_m"), f"AP {i}: x_m"),
                    "y_m": _coord(a.get("y_m"), f"AP {i}: y_m"), "bssids": clean})
    return out
