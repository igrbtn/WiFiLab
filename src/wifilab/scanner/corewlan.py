"""macOS scanner on CoreWLAN (pyobjc). Read-only: scans and reads the interface state, never associates.

macOS 14+ returns SSID and BSSID only to an app the user allowed Location Services; without it every network
comes back anonymous (counted, not listed). The bundled WiFiLab.app asks for the permission (location.py).
"""
from __future__ import annotations

import json
import subprocess
import threading
import time

import CoreWLAN

from ..analysis import band_of
from . import Scanner

# CWSecurity values (CoreWLAN.h); constants are read from the module when present.
SEC = {name: getattr(CoreWLAN, "kCWSecurity" + name, val) for name, val in (
    ("None", 0), ("WEP", 1), ("WPAPersonal", 2), ("WPAPersonalMixed", 3), ("WPA2Personal", 4), ("Personal", 5),
    ("DynamicWEP", 6), ("WPAEnterprise", 7), ("WPAEnterpriseMixed", 8), ("WPA2Enterprise", 9), ("Enterprise", 10),
    ("WPA3Personal", 11), ("WPA3Enterprise", 12), ("WPA3Transition", 13), ("OWE", 14), ("OWETransition", 15))}
PHY = [("be", getattr(CoreWLAN, "kCWPHYMode11be", 7)), ("ax", getattr(CoreWLAN, "kCWPHYMode11ax", 6)),
       ("ac", getattr(CoreWLAN, "kCWPHYMode11ac", 5)), ("n", getattr(CoreWLAN, "kCWPHYMode11n", 4)),
       ("g", getattr(CoreWLAN, "kCWPHYMode11g", 3)), ("b", getattr(CoreWLAN, "kCWPHYMode11b", 2)),
       ("a", getattr(CoreWLAN, "kCWPHYMode11a", 1))]
PHY_BY_VALUE = {v: k for k, v in PHY}
BANDS = {1: "2.4", 2: "5", 3: "6"}
WIDTHS = {1: 20, 2: 40, 3: 80, 4: 160}
SECURITY_LABEL = {v: k for k, v in SEC.items()}


def security_label(supports) -> str:
    """CWNetwork.supportsSecurity_ answers -> one label ("WPA2/WPA3", "WPA2-ENT", "OPEN", ...)."""
    def has(name: str) -> bool:
        try:
            return bool(supports(SEC[name]))
        except Exception:  # noqa: BLE001 - an unknown enum value on an older macOS
            return False

    personal = [p for p, keys in (("WPA", ("WPAPersonal",)), ("WPA2", ("WPA2Personal", "WPAPersonalMixed")),
                                  ("WPA3", ("WPA3Personal", "WPA3Transition"))) if any(has(k) for k in keys)]
    enterprise = [p for p, keys in (("WPA", ("WPAEnterprise",)), ("WPA2", ("WPA2Enterprise", "WPAEnterpriseMixed")),
                                    ("WPA3", ("WPA3Enterprise",))) if any(has(k) for k in keys)]
    if enterprise:
        return "/".join(enterprise) + "-ENT"
    if personal:
        return "/".join(personal)
    if has("WEP") or has("DynamicWEP"):
        return "WEP"
    if has("OWETransition"):
        return "OWE-TRANS"
    if has("OWE"):
        return "OWE"
    if has("None"):
        return "OPEN"
    return "UNKNOWN"


def _channel(ch) -> tuple[int, str, int]:
    if ch is None:
        return 0, "?", 20
    num = int(ch.channelNumber())
    band = BANDS.get(int(ch.channelBand()), band_of(num))
    return num, band, WIDTHS.get(int(ch.channelWidth()), 20)


def _str(v) -> str:
    return "" if v is None else str(v)


class CoreWLANScanner(Scanner):
    backend = "corewlan"
    may_repeat = True

    def __init__(self) -> None:
        self._client = CoreWLAN.CWWiFiClient.sharedWiFiClient()
        self._mcs_cache: tuple[float, dict] = (0.0, {})
        self._lock = threading.Lock()

    def _iface(self):
        return self._client.interface()

    def _ap(self, n, noise: int | None) -> dict | None:
        bssid = _str(n.bssid()).lower()
        ssid = _str(n.ssid())
        num, band, width = _channel(n.wlanChannel())
        if not bssid or not num:
            return None
        modes = [name for name, val in PHY if _safe(lambda v=val: n.supportsPHYMode_(v))]
        nn = int(n.noiseMeasurement() or 0)
        return {"bssid": bssid, "ssid": ssid, "hidden": not ssid, "rssi": int(n.rssiValue()),
                "noise": nn if nn < 0 else noise, "channel": num, "band": band, "width": width, "second": "",
                "security": security_label(n.supportsSecurity_), "phy": modes[0] if modes else "",
                "phy_modes": modes, "country": _str(n.countryCode()), "beacon_ms": int(n.beaconInterval() or 0),
                "ibss": bool(n.ibss())}

    def scan(self, position=None) -> dict:
        with self._lock:
            iface = self._iface()
            if iface is None:
                return self.failure("no_interface", "No Wi-Fi interface found on this Mac.", self.backend)
            if not iface.powerOn():
                return self.failure("wifi_off", "Wi-Fi is turned off. Turn it on to scan.", self.backend)
            nets, err = iface.scanForNetworksWithName_error_(None, None)
            cached = False
            if err is not None or nets is None:
                msg = str(err.localizedDescription()) if err is not None else "no result"
                # macOS throttles active scans ("Resource busy"): the system's own last scan is the next best thing.
                nets = iface.cachedScanResults()
                if not nets:
                    return self.failure("scan_failed", f"Scan failed: {msg}", self.backend)
                cached = True
            noise = int(iface.noiseMeasurement() or 0) or None
            by, anonymous = {}, 0
            for n in nets:
                ap = self._ap(n, noise)
                if ap is None:
                    anonymous += 1
                elif ap["bssid"] not in by or ap["rssi"] > by[ap["bssid"]]["rssi"]:
                    by[ap["bssid"]] = ap
            aps = list(by.values())
            res = self.result(aps, self.backend, self.connection(iface), anonymous)
            res["cached"] = cached
            return res

    def connection(self, iface=None) -> dict | None:
        iface = iface or self._iface()
        if iface is None or not iface.powerOn():
            return None
        num, band, width = _channel(iface.wlanChannel())
        if not num:
            return {"associated": False, "interface": _str(iface.interfaceName())}
        rssi, noise = int(iface.rssiValue() or 0), int(iface.noiseMeasurement() or 0)
        out = {"associated": True, "interface": _str(iface.interfaceName()), "ssid": _str(iface.ssid()),
               "bssid": _str(iface.bssid()).lower(), "rssi": rssi, "noise": noise,
               "snr": rssi - noise if rssi and noise else None, "channel": num, "band": band, "width": width,
               "tx_rate": float(iface.transmitRate() or 0), "tx_power_mw": int(iface.transmitPower() or 0),
               "phy": PHY_BY_VALUE.get(int(iface.activePHYMode()), ""),
               "security": SECURITY_LABEL.get(int(iface.security()), ""), "country": _str(iface.countryCode())}
        out.update(self._mcs())
        return out

    def _mcs(self) -> dict:
        """MCS / spatial streams are not in CoreWLAN: system_profiler has them (slow, so cached for 60 s)."""
        ts, data = self._mcs_cache
        if time.time() - ts < 60:
            return data
        data = {}
        try:
            r = subprocess.run(["/usr/sbin/system_profiler", "SPAirPortDataType", "-json"], capture_output=True,
                               text=True, timeout=20)
            info = json.loads(r.stdout or "{}")
            for block in info.get("SPAirPortDataType", []):
                for itf in block.get("spairport_airport_interfaces", []):
                    cur = itf.get("spairport_current_network_information")
                    if isinstance(cur, dict):
                        if "spairport_network_mcs" in cur:
                            data["mcs"] = cur.get("spairport_network_mcs")
                        if "spairport_network_rate" in cur:
                            data["rate_mbps"] = cur.get("spairport_network_rate")
        except (OSError, ValueError, subprocess.SubprocessError):
            data = {}
        self._mcs_cache = (time.time(), data)
        return data

    def location_status(self) -> dict:
        from ..location import status
        return status()


def _safe(fn) -> bool:
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001 - enum value unknown to this macOS
        return False
