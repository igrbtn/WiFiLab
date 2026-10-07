"""Background scanning: one scan at a time (the radio does one anyway), periodic while running, results stored."""
from __future__ import annotations

import logging
import threading
import time

from . import analysis, oui
from .scanner import Scanner
from .store import Store

log = logging.getLogger("wifilab.scan")
MIN_INTERVAL, MAX_INTERVAL = 5, 3600


def enrich(ap: dict, oui_extra=None) -> dict:
    lo, hi, c = analysis.span(ap)
    rssi = ap.get("rssi", ap.get("rssi_max", -100))
    noise = ap.get("noise")
    return {**ap, "vendor": oui.vendor(ap.get("bssid", ""), oui_extra), "span_lo": lo, "span_hi": hi, "center": c,
            "sec_class": analysis.sec_class(ap.get("security", "")), "quality": analysis.quality(rssi),
            "snr": rssi - noise if isinstance(noise, (int, float)) and noise < 0 else None,
            "freq": analysis.freq_mhz(int(ap.get("channel") or 0), ap.get("band") or "2.4")}


class ScanService:
    def __init__(self, scanner: Scanner, store: Store, interval: int = 15, running: bool = True,
                 retention_days: int = 14, oui_extra=None) -> None:
        self.scanner = scanner
        self.store = store
        self.interval = min(max(int(interval), MIN_INTERVAL), MAX_INTERVAL)
        self.running = running
        self.retention_days = retention_days
        self.oui_extra = oui_extra
        self.last: dict | None = None
        self.last_ok: dict | None = None
        self.scanning = False
        self.fails = 0
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_prune = 0.0

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="wifilab-scan", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def set_interval(self, seconds: int) -> None:
        self.interval = min(max(int(seconds), MIN_INTERVAL), MAX_INTERVAL)
        self._wake.set()

    def set_running(self, on: bool) -> None:
        self.running = bool(on)
        self._wake.set()

    def scan_now(self, position=None, store: bool = True) -> dict:
        """One scan, now (waits for one in flight to finish first)."""
        with self._lock:
            self.scanning = True
            try:
                res = self.scanner.scan(position)
            except Exception as e:  # noqa: BLE001 - a backend crash must not kill the loop
                log.exception("scan failed")
                res = Scanner.failure("scan_failed", f"Scan failed: {e}", self.scanner.backend)
            finally:
                self.scanning = False
        res["aps"] = [enrich(a, self.oui_extra) for a in res.get("aps", [])]
        self.last = res
        if res["ok"]:
            self.last_ok = res
            self.fails = 0
            if store:
                self.store.add_scan(res)
        else:
            self.fails += 1
        return res

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.running:
                self.scan_now()
                if time.time() - self._last_prune > 3600:
                    self._last_prune = time.time()
                    try:
                        self.store.prune(self.retention_days)
                    except Exception:  # noqa: BLE001
                        log.exception("prune failed")
            self._wake.clear()
            self._wake.wait(self.interval if self.running else 3600)

    def state(self) -> dict:
        last = self.last or {}
        return {"running": self.running, "interval": self.interval, "scanning": self.scanning, "fails": self.fails,
                "backend": self.scanner.backend, "last_ts": last.get("ts"), "last_ok": last.get("ok"),
                "error_code": last.get("error_code", ""), "error": last.get("error", ""),
                "anonymous": last.get("anonymous", 0), "ap_count": len(last.get("aps", []))}
