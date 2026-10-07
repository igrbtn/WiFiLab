"""Synthetic scanner for tests, demos and non-macOS hosts: a simulated 30 x 18 m floor with three dual-band
corporate APs and a few neighbours with typical problems (open, WEP, WPA1, hidden, 40 MHz on 2.4 GHz, channel 3).
All names and MAC addresses are made up. RSSI follows a log-distance path loss model from the scan position."""
from __future__ import annotations

import hashlib
import math
import time

from . import Scanner

FLOOR = (30.0, 18.0)
DEFAULT_POS = (12.0, 8.0)
NOISE = -92

# (radio MAC prefix, x, y, tx dBm at 1 m, [(band, channel, width)], [(ssid, security)])
CORP_SSIDS = [("WiFiLab-Corp", "WPA2-ENT"), ("WiFiLab-Guest", "WPA2/WPA3"), ("", "WPA2")]
RADIOS = [
    ("24:de:c6:5a:01", 5.0, 4.0, -32, [("2.4", 1, 20), ("5", 36, 80)], CORP_SSIDS),
    ("24:de:c6:5a:02", 16.0, 12.0, -32, [("2.4", 6, 20), ("5", 52, 80)], CORP_SSIDS),
    ("24:de:c6:5a:03", 26.0, 5.0, -32, [("2.4", 6, 20), ("5", 149, 80)], CORP_SSIDS),
    ("f0:9f:c2:7b:10", 29.0, 15.0, -30, [("2.4", 3, 40)], [("Neighbor-Home", "WPA/WPA2")]),
    ("00:0b:86:c1:20", -6.0, 14.0, -36, [("2.4", 11, 20)], [("Cafe-Free", "OPEN")]),
    ("14:cc:20:3d:30", 8.0, 20.0, -36, [("2.4", 9, 20)], [("OldPrinter", "WEP")]),
    ("4c:5e:0c:9e:40", 20.0, 1.0, -30, [("5", 36, 40), ("6", 37, 160)], [("Neighbor-6E", "WPA3")]),
]


def _jitter(*parts) -> float:
    """Deterministic pseudo-noise in -3..3 dB from the inputs (stable within a 5 s time slot)."""
    h = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return (h[0] / 255.0) * 6 - 3


def rssi_at(tx: float, ap_xy: tuple[float, float], pos: tuple[float, float], band: str, slot: int) -> int:
    d = max(1.0, math.dist(ap_xy, pos))
    exp = 3.0 if band == "2.4" else 3.4 if band == "5" else 3.7
    walls = int(d / 7)   # roughly one wall every 7 m
    loss = 10 * exp * math.log10(d) + walls * (3 if band == "2.4" else 5) + (0 if band == "2.4" else 4)
    return round(tx - loss + _jitter(ap_xy, pos, band, slot))


class FakeScanner(Scanner):
    backend = "fake"

    def __init__(self) -> None:
        self.position = DEFAULT_POS

    def aps_at(self, pos: tuple[float, float], slot: int) -> list[dict]:
        out = []
        for prefix, x, y, tx, radios, ssids in RADIOS:
            for r_i, (band, ch, width) in enumerate(radios):
                rssi = rssi_at(tx, (x, y), pos, band, slot)
                if rssi < -92:
                    continue
                for s_i, (ssid, sec) in enumerate(ssids):
                    bssid = f"{prefix}:{(r_i << 4) | s_i:02x}"
                    phy = ["ax", "ac", "n", "a"] if band == "5" else ["ax"] if band == "6" else ["ax", "n", "g", "b"]
                    out.append({"bssid": bssid, "ssid": ssid, "hidden": not ssid, "rssi": rssi - s_i % 2,
                                "noise": NOISE, "channel": ch, "band": band, "width": width, "second": "",
                                "security": sec, "phy": phy[0], "phy_modes": phy, "country": "XX", "beacon_ms": 100,
                                "ibss": False})
        return out

    def scan(self, position=None) -> dict:
        pos = tuple(position) if position else self.position
        slot = int(time.time() // 5)
        aps = self.aps_at(pos, slot)
        return self.result(aps, self.backend, self.connection(aps))

    def connection(self, aps=None) -> dict | None:
        aps = aps if aps is not None else self.aps_at(self.position, int(time.time() // 5))
        corp = [a for a in aps if a["ssid"] == "WiFiLab-Corp"]
        if not corp:
            return {"associated": False, "interface": "en0"}
        a = max(corp, key=lambda x: x["rssi"])
        return {"associated": True, "interface": "en0", "ssid": a["ssid"], "bssid": a["bssid"], "rssi": a["rssi"],
                "noise": NOISE, "snr": a["rssi"] - NOISE, "channel": a["channel"], "band": a["band"],
                "width": a["width"], "tx_rate": 866.0, "tx_power_mw": 0, "phy": a["phy"], "security": a["security"],
                "country": "XX", "mcs": 9}
