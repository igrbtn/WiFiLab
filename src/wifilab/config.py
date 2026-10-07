"""Runtime configuration from the environment (see .env.example); everything has a working default."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "WiFiLab"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "wifilab"


def _int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.environ.get(name, default))
    except ValueError:
        return default
    return min(max(v, lo), hi)


def _port(raw) -> int:
    """0 (random free port) unless a valid unprivileged port is given."""
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 0
    return v if 1024 <= v <= 65535 else 0


@dataclass
class Config:
    host: str
    port: int             # 0 = a random free port (default); the actual one is in <data dir>/server.json
    data_dir: Path
    scanner: str          # auto | corewlan | fake
    scan_interval: int    # seconds between background scans
    retention_days: int   # scan history kept in SQLite
    autoscan: bool

    @property
    def db_path(self) -> Path:
        return self.data_dir / "wifilab.sqlite3"


def load() -> Config:
    data = os.environ.get("WIFILAB_DATA_DIR")
    return Config(
        host="127.0.0.1",
        port=_port(os.environ.get("WIFILAB_PORT", "")),
        data_dir=Path(data).expanduser() if data else _default_data_dir(),
        scanner=os.environ.get("WIFILAB_SCANNER", "auto").strip().lower() or "auto",
        scan_interval=_int("WIFILAB_SCAN_INTERVAL", 15, 5, 3600),
        retention_days=_int("WIFILAB_RETENTION_DAYS", 14, 1, 3650),
        autoscan=os.environ.get("WIFILAB_AUTOSCAN", "1").strip() not in ("0", "false", "no"),
    )
