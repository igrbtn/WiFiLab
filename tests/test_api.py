import base64
import io
import struct
import zipfile
import zlib

from conftest import FIXTURES

from wifilab import projects
from wifilab.scanner import Scanner, get_scanner
from wifilab.scanner.fake import FakeScanner


def test_info_and_live(client):
    r = client.get("/api/info").json()
    assert r["backend"] == "fake" and r["location"]["needed"] is False
    assert client.get("/api/live").json()["scan"]["aps"] == []
    live = client.post("/api/scan").json()
    aps = live["scan"]["aps"]
    assert aps and {"vendor", "span_lo", "span_hi", "sec_class", "quality", "snr"} <= set(aps[0])
    assert {c["code"] for c in live["issues"]} >= {"open_network", "hidden_ssid"}
    assert set(live["channels"]) == {"2.4", "5", "6"}
    assert client.get("/api/live").json()["scan"]["ts"] == live["scan"]["ts"]


def test_index_and_static(client):
    assert "WiFiLab" in client.get("/").text
    assert client.get("/static/js/main.js").status_code == 200


def test_settings_persist(client, store):
    r = client.put("/api/settings", json={"interval": 2, "running": False}).json()
    assert r["interval"] == 5 and r["running"] is False
    assert store.settings() == {"interval": 5, "running": False}
    assert client.put("/api/settings", json={"interval": "x"}).status_code == 422


def test_history_and_inventory(client):
    for _ in range(3):
        client.post("/api/scan")
    inv = client.get("/api/inventory?minutes=10").json()
    assert inv["scans"] == 3
    a = inv["aps"][0]
    assert a["seen_count"] == 3 and a["rssi_min"] <= a["rssi_avg"] <= a["rssi_max"]
    h = client.get(f"/api/history?bssids={a['bssid']},bogus&minutes=10").json()
    assert list(h["series"]) == [a["bssid"]] and len(h["series"][a["bssid"]]) == 3
    assert client.delete("/api/history").json()["ok"]
    assert client.get("/api/inventory").json()["aps"] == []


def test_survey_flow(client):
    plan = (FIXTURES / "plan-example.json").read_text()
    p = client.post("/api/projects", json={"name": "Test floor"}).json()
    pid = p["id"]
    assert client.put(f"/api/projects/{pid}/plan", content=plan).json()["plan"]["width_m"] == 8
    bad = client.put(f"/api/projects/{pid}/plan", content='{"format": "x"}')
    assert bad.status_code == 422 and bad.json()["errors"]
    for x, y in [(1, 1), (4, 2.5), (7, 4)]:
        r = client.post(f"/api/projects/{pid}/points", json={"x_m": x, "y_m": y})
        assert r.status_code == 200, r.text
    assert client.post(f"/api/projects/{pid}/points", json={"x_m": 50, "y_m": 1}).status_code == 422
    proj = client.get(f"/api/projects/{pid}").json()
    assert len(proj["points"]) == 3 and proj["points"][0]["noise"] == -92
    pt = proj["points"][0]
    moved = client.patch(f"/api/projects/{pid}/points/{pt['id']}", json={"x_m": 2, "note": "desk"}).json()["point"]
    assert moved["x_m"] == 2 and moved["note"] == "desk"
    aps = [{"id": "ap1", "name": "AP 1", "x_m": 3, "y_m": 2, "bssids": [pt["aps"][0]["bssid"]]}]
    assert client.put(f"/api/projects/{pid}/aps", json={"aps": aps}).json()["aps"][0]["id"] == "ap1"
    assert client.put(f"/api/projects/{pid}/aps", json={"aps": [{"id": "x"}]}).status_code == 422
    inv = client.get(f"/api/projects/{pid}/inventory").json()
    assert inv["aps"] and "WiFiLab-Corp" in inv["ssids"]
    rep = client.get(f"/api/report?project={pid}&ssid=WiFiLab-Corp").json()
    assert rep["summary"]["points"] == 3 and rep["ssid"] == "WiFiLab-Corp"
    csv = client.get(f"/api/report.csv?project={pid}").text
    assert csv.startswith("bssid,ssid,hidden,vendor")
    exp = client.get(f"/api/projects/{pid}/export.json").json()
    assert exp["kind"] == "wifilab_project" and len(exp["points"]) == 3
    ft = client.get(f"/api/projects/{pid}/fieldtab-map.json").json()
    assert ft["kind"] == "wifi_map" and ft["plan"][0]["x2_m"] == 8
    assert client.delete(f"/api/projects/{pid}/points/{pt['id']}").json()["points"] == 2
    assert client.delete(f"/api/projects/{pid}").json()["ok"]
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_measure_refuses_anonymous_scans(cfg, store):
    from fastapi.testclient import TestClient

    from wifilab.web import create_app

    class Anon(Scanner):
        backend = "anon"

        def scan(self, position=None):
            return self.result([], self.backend, None, anonymous=12)

    with TestClient(create_app(cfg, scanner=Anon(), store=store, start_loop=False)) as c:
        pid = c.post("/api/projects", json={"name": "x"}).json()["id"]
        r = c.post(f"/api/projects/{pid}/points", json={"x_m": 1, "y_m": 1})
        assert r.status_code == 409 and "Location" in r.json()["detail"]
        assert c.get("/api/info").json()["location"]["hidden_results"] == 12


def test_wifi_off_is_reported(cfg, store):
    from fastapi.testclient import TestClient

    from wifilab.web import create_app

    class Off(Scanner):
        backend = "off"

        def scan(self, position=None):
            return self.failure("wifi_off", "Wi-Fi is turned off.", self.backend)

    with TestClient(create_app(cfg, scanner=Off(), store=store, start_loop=False)) as c:
        live = c.post("/api/scan").json()
        assert live["scan"]["error_code"] == "wifi_off" and live["state"]["fails"] == 1
        pid = c.post("/api/projects", json={"name": "x"}).json()["id"]
        assert c.post(f"/api/projects/{pid}/points", json={"x_m": 1, "y_m": 1}).status_code == 409


def test_import_endpoint(client):
    for name, points in [("fieldtab-wifi-map.json", 3), ("fieldtab-wifi-doc.json", 2), ("fieldtab-wifi-survey.csv", 0),
                         ("plan-example.json", 0)]:
        r = client.post(f"/api/import?name={name}", content=(FIXTURES / name).read_bytes())
        assert r.status_code == 200, r.text
        assert r.json()["project"]["points"] == points
    assert client.post("/api/import?name=x.json", content=b'{"a": 1}').status_code == 422
    # A WiFiLab export comes back as a new project
    pid = client.get("/api/projects").json()[0]["id"]
    exp = client.get(f"/api/projects/{pid}/export.json").content
    r = client.post("/api/import?name=again.json", content=exp).json()
    assert r["kind"] == "wifilab_project"
    assert len(client.get("/api/projects").json()) == 5


def _png(w: int = 4, h: int = 3) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    raw = b"".join(b"\x00" + b"\x30\x80\xc0" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_docx_report(client):
    client.post("/api/scan")
    png = "data:image/png;base64," + base64.b64encode(_png()).decode()
    r = client.post("/api/report.docx", json={"minutes": 10, "images": [{"title": "t", "png": png}, {"png": "bogus"}]})
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    xml = z.read("word/document.xml").decode()
    assert "Live Wi-Fi environment" in xml and "Cafe-Free" in xml
    assert any(n.startswith("word/media/") for n in z.namelist())


def test_location_request_without_app(client):
    r = client.post("/api/location/request").json()
    assert "WiFiLab.app" in r["message"]


def test_project_helpers():
    p = projects.new_project("x")
    scan = FakeScanner().scan((3, 3))
    pt = projects.point_from_scan(3, 3, scan)
    assert pt["aps"] == sorted(pt["aps"], key=lambda a: -a["rssi"])
    projects.add_point(p, pt)
    s = projects.summary(p)
    assert s["points"] == 1 and s["radios"] >= 3
    try:
        projects.set_bg(p, "data:text/html,hi")
        raise AssertionError("accepted a non-image background")
    except ValueError:
        pass


def test_get_scanner_fallback():
    assert get_scanner("fake").backend == "fake"
