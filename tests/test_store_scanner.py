import sys
import time

import pytest

from wifilab import oui
from wifilab.scanloop import ScanService
from wifilab.scanner.fake import FLOOR, FakeScanner, rssi_at
from wifilab.store import Store


def test_fake_scanner_is_position_dependent():
    sc = FakeScanner()
    near = {a["bssid"]: a["rssi"] for a in sc.scan((5, 4))["aps"]}
    far = {a["bssid"]: a["rssi"] for a in sc.scan((FLOOR[0], FLOOR[1]))["aps"]}
    b = "24:de:c6:5a:01:00"
    assert near[b] > far.get(b, -100) + 10
    assert rssi_at(-32, (0, 0), (1, 0), "2.4", 0) > rssi_at(-32, (0, 0), (10, 0), "2.4", 0)
    res = sc.scan()
    assert res["ok"] and res["connection"]["associated"]


def test_store_inventory_and_prune():
    s = Store(":memory:")
    sc = FakeScanner()
    for _ in range(2):
        s.add_scan(sc.scan())
    inv = s.inventory()
    assert inv and inv[0]["seen_count"] == 2 and inv == sorted(inv, key=lambda a: -a["rssi_max"])
    assert s.scan_count() == 2
    assert len(s.history(inv[0]["bssid"], 0)) == 2
    old = sc.scan()
    old["ts"] = time.time() - 40 * 86400
    s.add_scan(old)
    assert s.prune(14) == len(old["aps"])
    assert s.scan_count() == 2


def test_projects_round_trip():
    s = Store(":memory:")
    p = s.save_project({"name": "A", "plan": {"width_m": 5, "length_m": 4}, "points": []})
    assert s.project(p["id"])["name"] == "A"
    assert s.projects()[0]["width_m"] == 5
    s.save_project({**p, "name": "B"})
    assert s.project(p["id"])["name"] == "B" and s.project(p["id"])["created"] == p["created"]
    assert s.delete_project(p["id"]) and s.project(p["id"]) is None


def test_scan_service_counts_failures():
    class Broken(FakeScanner):
        def scan(self, position=None):
            raise RuntimeError("radio exploded")

    svc = ScanService(Broken(), Store(":memory:"), interval=1)
    assert svc.interval == 5
    res = svc.scan_now()
    assert not res["ok"] and "radio exploded" in res["error"] and svc.fails == 1


def test_oui_vendor():
    assert oui.vendor("24:DE:C6:00:00:01") == "Aruba (HPE)"
    assert oui.vendor("02:11:22:33:44:55") == "(locally administered)"
    assert oui.vendor("") == ""


@pytest.mark.skipif(sys.platform != "darwin", reason="CoreWLAN is macOS only")
def test_corewlan_security_labels():
    pytest.importorskip("CoreWLAN")
    from wifilab.scanner.corewlan import SEC, security_label

    def sup(*names):
        vals = {SEC[n] for n in names}
        return lambda v: v in vals

    assert security_label(sup("WPA2Personal", "WPAPersonalMixed", "WPA3Personal", "WPA3Transition")) == "WPA2/WPA3"
    assert security_label(sup("WPAPersonal", "WPA2Personal")) == "WPA/WPA2"
    assert security_label(sup("WPA2Enterprise")) == "WPA2-ENT"
    assert security_label(sup("None")) == "OPEN"
    assert security_label(sup("WEP")) == "WEP"
    assert security_label(sup("OWE")) == "OWE"
    assert security_label(sup()) == "UNKNOWN"
