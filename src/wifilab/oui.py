"""Vendor by MAC prefix from the IEEE registries (MA-L 24-bit, MA-M 28-bit, MA-S 36-bit prefixes).

Sources, in order of precedence:
1. <data dir>/oui.csv - user overrides, "prefix,vendor" lines (6, 7 or 9 hex digits).
2. Locally administered addresses (bit 0x02 of the first octet) - "Private (randomized)".
3. <data dir>/oui.tsv.gz - downloaded from Settings > Update vendor database (or scripts/update_oui.py --out).
4. The bundled wifilab/data/oui.tsv.gz (built by scripts/update_oui.py).

The .tsv.gz format: a "# wifilab-oui ..." header line with key=value metadata, then "PREFIX<TAB>Vendor" lines,
PREFIX being 6, 7 or 9 upper-case hex digits. Lookup is longest-prefix.
"""
from __future__ import annotations

import csv
import gzip
import io
import os
import re
import tempfile
import threading
import time
import unicodedata
import urllib.request
from functools import lru_cache
from importlib import resources
from pathlib import Path

SOURCES = {
    "MA-L": "https://standards-oui.ieee.org/oui/oui.csv",
    "MA-M": "https://standards-oui.ieee.org/oui28/mam.csv",
    "MA-S": "https://standards-oui.ieee.org/oui36/oui36.csv",
}
PREFIX_LENS = (9, 7, 6)
DB_NAME = "oui.tsv.gz"
USER_CSV = "oui.csv"
PRIVATE = "Private (randomized)"
MAX_NAME = 40

_HEX = re.compile(r"[^0-9a-f]")
_lock = threading.Lock()

# Short names for vendors common in Wi-Fi surveys whose registry names are long or inconsistent.
_ALIASES = [
    (r"^aruba", "Aruba (HPE)"),
    (r"^hewlett packard enterprise", "HPE"),
    (r"^cisco meraki", "Cisco Meraki"),
    (r"^cisco", "Cisco"),
    (r"^apple", "Apple"),
    (r"^huawei", "Huawei"),
    (r"^tp-?link", "TP-Link"),
    (r"^ubiquiti", "Ubiquiti"),
    (r"^routerboard", "MikroTik"),
    (r"^mikrotik", "MikroTik"),
    (r"^ruckus", "Ruckus"),
    (r"^netgear", "Netgear"),
    (r"^asustek", "ASUS"),
    (r"^d-link", "D-Link"),
    (r"^zyxel", "Zyxel"),
    (r"^juniper", "Juniper"),
    (r"^mist systems", "Juniper Mist"),
    (r"^new h3c|^h3c", "H3C"),
    (r"^extreme networks", "Extreme Networks"),
    (r"^fortinet", "Fortinet"),
    (r"^samsung", "Samsung"),
    (r"^xiaomi|^beijing xiaomi", "Xiaomi"),
    (r"^intel corporat", "Intel"),
    (r"^espressif", "Espressif"),
    (r"^raspberry pi", "Raspberry Pi"),
    (r"^google", "Google"),
    (r"^microsoft", "Microsoft"),
    (r"^broadcom", "Broadcom"),
    (r"^amazon technologies", "Amazon"),
    (r"^sagemcom", "Sagemcom"),
    (r"^zte corporation", "ZTE"),
    (r"^keenetic", "Keenetic"),
]
_ALIAS_RE = [(re.compile(p, re.I), n) for p, n in _ALIASES]
_SUFFIX = re.compile(
    r"[\s,.]*\b(co|company|corp|corporation|inc|incorporated|ltd|limited|llc|l\.l\.c|gmbh|ag|sa|s\.a|s\.a\.s|sas|"
    r"srl|s\.r\.l|spa|s\.p\.a|bv|b\.v|nv|n\.v|oy|ab|as|a/s|aps|kg|pty|plc|pte|kk|k\.k|sdn bhd|bhd|ooo|zao|jsc|"
    r"group|holdings?|technology|technologies|tech|electronics?|communications?|systems)\b[\s,.]*$", re.I)


def short_name(name: str) -> str:
    """Compact ASCII vendor name: transliterated, legal-form suffixes dropped, well-known vendors aliased."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s.replace("\t", " ")).strip(" ,.;\"'")
    if not s:
        return ""
    for rx, alias in _ALIAS_RE:
        if rx.search(s):
            return alias
    if s.lower() == "private":
        return "Private registration"
    prev = None
    while prev != s:
        prev = s
        cut = _SUFFIX.sub("", s).strip(" ,.;-&")
        if cut:
            s = cut
    return s[:MAX_NAME].strip()


def parse_ieee_csv(text: str) -> dict[str, str]:
    """IEEE registry CSV (Registry,Assignment,Organization Name,...) -> {PREFIX: short vendor}."""
    out = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3 or row[0].strip() == "Registry":
            continue
        p = row[1].strip().upper()
        if len(p) in PREFIX_LENS and re.fullmatch(r"[0-9A-F]+", p):
            n = short_name(row[2])
            if n:
                out[p] = n
    return out


def write_db(path: Path, entries: dict[str, str], meta: dict[str, str] | None = None) -> None:
    meta = {"generated": time.strftime("%Y-%m-%d", time.gmtime()), "entries": str(len(entries)),
            "source": "IEEE MA-L/MA-M/MA-S", **(meta or {})}
    head = "# wifilab-oui " + " ".join(f"{k}={v}" for k, v in meta.items())
    body = "\n".join([head] + [f"{p}\t{entries[p]}" for p in sorted(entries)]) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".oui-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as gz:
            gz.write(body.encode("ascii"))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_db(data: bytes) -> tuple[dict[str, str], dict[str, str]]:
    entries, meta = {}, {}
    for line in gzip.decompress(data).decode("ascii", "replace").splitlines():
        if line.startswith("#"):
            meta.update(kv.split("=", 1) for kv in line.split()[2:] if "=" in kv)
            continue
        p, _, n = line.partition("\t")
        if len(p) in PREFIX_LENS and n:
            entries[p] = n
    return entries, meta


def fetch(url: str, timeout: float = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (WiFiLab OUI updater)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def download(dest: Path, fetcher=None) -> dict:
    """Download the three IEEE registries and write dest (.tsv.gz). Returns the new DB info."""
    fetcher = fetcher or fetch
    entries: dict[str, str] = {}
    counts = {}
    for reg, url in SOURCES.items():
        try:
            part = parse_ieee_csv(fetcher(url))
        except OSError as e:
            raise RuntimeError(f"cannot download {reg} registry from {url}: {getattr(e, 'reason', e)}") from e
        if not part:
            raise RuntimeError(f"{reg} registry from {url} has no entries (format changed?)")
        counts[reg] = len(part)
        entries.update(part)
    if counts.get("MA-L", 0) < 10000:
        raise RuntimeError(f"MA-L registry looks truncated ({counts.get('MA-L', 0)} entries)")
    write_db(dest, entries, {k.replace("-", "").lower(): str(v) for k, v in counts.items()})
    return {"entries": len(entries), **counts}


# ---------- lookup ----------

def _user_csv(path: Path) -> dict[str, str]:
    out = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for row in csv.reader(text.splitlines()):
        if len(row) >= 2 and row[0].strip().lower() != "prefix":
            h = _HEX.sub("", row[0].lower()).upper()
            name = short_name(row[1]) if not row[1].isascii() else row[1].strip()[:MAX_NAME]
            if len(h) >= 6 and name:
                out[h[:9] if len(h) >= 9 else h[:7] if len(h) == 7 else h[:6]] = name
    return out


def _bundled() -> bytes:
    return resources.files("wifilab").joinpath("data/" + DB_NAME).read_bytes()


@lru_cache(maxsize=4)
def _load(data_dir: Path | None) -> tuple[dict[str, str], dict[str, str], dict]:
    """(registry entries, user overrides, info) for a data dir; cached until reload()."""
    info = {"source": "bundled", "path": "", "entries": 0, "generated": "", "user_entries": 0}
    local = data_dir / DB_NAME if data_dir else None
    entries, meta = {}, {}
    if local and local.is_file():
        try:
            entries, meta = read_db(local.read_bytes())
            info.update(source="downloaded", path=str(local))
        except (OSError, EOFError, gzip.BadGzipFile):
            entries = {}
    if not entries:
        try:
            entries, meta = read_db(_bundled())
            info.update(source="bundled", path="")
        except (OSError, EOFError, gzip.BadGzipFile, FileNotFoundError):
            entries, meta = {}, {}
    user = _user_csv(data_dir / USER_CSV) if data_dir else {}
    info.update(entries=len(entries), generated=meta.get("generated", ""), user_entries=len(user))
    return entries, user, info


def _dir(extra: Path | None) -> Path | None:
    """Accepts the data dir or (legacy) the path of <data dir>/oui.csv."""
    if extra is None:
        return None
    extra = Path(extra)
    return extra.parent if extra.name == USER_CSV else extra


def reload() -> None:
    with _lock:
        _load.cache_clear()


def info(extra: Path | None = None) -> dict:
    return dict(_load(_dir(extra))[2])


def is_local(mac: str) -> bool:
    """Locally administered address (bit 1 of the first byte): randomised or virtual BSSIDs."""
    h = _HEX.sub("", str(mac).lower())
    return len(h) >= 2 and bool(int(h[:2], 16) & 0x02)


def _match(table: dict[str, str], h: str) -> str:
    for n in PREFIX_LENS:
        if len(h) >= n and h[:n] in table:
            return table[h[:n]]
    return ""


def vendor(mac: str, extra: Path | None = None) -> str:
    h = _HEX.sub("", str(mac or "").lower()).upper()
    if len(h) < 6:
        return ""
    entries, user, _ = _load(_dir(extra))
    v = _match(user, h)
    if v:
        return v
    if is_local(h):
        return PRIVATE
    return _match(entries, h)


def update(data_dir: Path, fetcher=None) -> dict:
    """Download into <data dir>/oui.tsv.gz (the bundled DB stays as the fallback) and reload."""
    with _lock:
        res = download(Path(data_dir) / DB_NAME, fetcher)
        _load.cache_clear()
    return {**info(data_dir), "counts": res}
