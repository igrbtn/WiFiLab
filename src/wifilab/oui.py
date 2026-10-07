"""Vendor by MAC prefix: a small bundled OUI subset (common enterprise and consumer Wi-Fi vendors), extended by
an optional user file <data dir>/oui.csv with the same "prefix,vendor" columns (e.g. cut from the IEEE registry)."""
from __future__ import annotations

import csv
import re
from functools import lru_cache
from importlib import resources
from pathlib import Path

_HEX = re.compile(r"[^0-9a-f]")


def _norm(prefix: str) -> str:
    h = _HEX.sub("", str(prefix).lower())
    return h[:6] if len(h) >= 6 else ""


def _read(text: str) -> dict[str, str]:
    out = {}
    for row in csv.reader(text.splitlines()):
        if len(row) >= 2 and row[0].strip().lower() != "prefix":
            k = _norm(row[0])
            if k:
                out[k] = row[1].strip()[:48]
    return out


@lru_cache(maxsize=4)
def table(extra: Path | None = None) -> dict[str, str]:
    t = _read(resources.files("wifilab").joinpath("data/oui.csv").read_text(encoding="utf-8"))
    if extra and extra.is_file():
        try:
            t.update(_read(extra.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            pass
    return t


def is_local(mac: str) -> bool:
    """Locally administered address (bit 1 of the first byte): randomised or virtual BSSIDs."""
    h = _HEX.sub("", str(mac).lower())
    return len(h) >= 2 and bool(int(h[:2], 16) & 0x02)


def vendor(mac: str, extra: Path | None = None) -> str:
    if not mac:
        return ""
    v = table(extra).get(_norm(mac), "")
    if v:
        return v
    return "(locally administered)" if is_local(mac) else ""
