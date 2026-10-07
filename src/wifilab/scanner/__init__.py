"""Scanner backends. `get_scanner(kind)` returns CoreWLAN on macOS ("auto" falls back to the fake one elsewhere).

A scan result is a dict:
  {ts, ok, error_code, error, backend, aps: [AP], connection: {...} | None, anonymous: int}
AP: {bssid, ssid, hidden, rssi, noise, channel, band, width, second, security, phy, phy_modes, country,
     beacon_ms, ibss}
error_code: "" | "wifi_off" | "no_interface" | "scan_failed" | "unavailable".
`anonymous` counts networks the OS returned without SSID and BSSID (macOS without Location Services permission).
"""
from __future__ import annotations

import sys
import time


class ScannerError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Scanner:
    backend = "none"

    def scan(self, position: tuple[float, float] | None = None) -> dict:
        """One active scan. position (metres) only steers the fake backend's simulated floor."""
        raise NotImplementedError

    def connection(self) -> dict | None:
        return None

    def location_status(self) -> dict:
        return {"needed": False, "status": "not_needed", "authorized": True}

    @staticmethod
    def result(aps: list[dict], backend: str, connection: dict | None = None, anonymous: int = 0) -> dict:
        return {"ts": time.time(), "ok": True, "error_code": "", "error": "", "backend": backend, "aps": aps,
                "connection": connection, "anonymous": anonymous}

    @staticmethod
    def failure(code: str, message: str, backend: str) -> dict:
        return {"ts": time.time(), "ok": False, "error_code": code, "error": message, "backend": backend, "aps": [],
                "connection": None, "anonymous": 0}


def get_scanner(kind: str = "auto") -> Scanner:
    if kind == "fake":
        from .fake import FakeScanner
        return FakeScanner()
    if kind in ("auto", "corewlan") and sys.platform == "darwin":
        try:
            from .corewlan import CoreWLANScanner
            return CoreWLANScanner()
        except ImportError:
            if kind == "corewlan":
                raise
    if kind == "corewlan":
        raise ScannerError("unavailable", "CoreWLAN is only available on macOS with pyobjc-framework-CoreWLAN")
    from .fake import FakeScanner
    return FakeScanner()
