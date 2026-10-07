"""Server-rendered heatmaps: every report of a survey with measured points embeds them (HTML and docx)."""
import io
import re
import zipfile

from wifilab import heatmap, projects
from wifilab.scanner.fake import FakeScanner

PNG = b"\x89PNG"


def survey(plan=True, placed=True) -> dict:
    sc = FakeScanner()
    p = projects.new_project("Heat test")
    if not plan:
        p["plan"] = None
    for i, (x, y) in enumerate([(2, 2), (8, 3), (14, 6), (5, 9), (11, 10)]):
        pt = projects.point_from_scan(x, y, sc.scan((x, y)))
        pt["ts"] = 1000 + i
        p["points"].append(pt)
    if placed:
        b = projects.inventory(p)[0]["bssid"]
        p["aps"] = [{"id": "ap1", "name": "AP 1", "x_m": 3, "y_m": 2, "bssids": [b]}]
    return p


def _media(docx: bytes) -> tuple[str, list[str]]:
    z = zipfile.ZipFile(io.BytesIO(docx))
    return z.read("word/document.xml").decode(), [n for n in z.namelist() if n.startswith("word/media/")]


def test_views_and_values():
    p = survey()
    kinds = [v["kind"] for v in heatmap.report_views(p, "WiFiLab-Corp")]
    assert kinds == ["rssi", "rssi", "snr", "count", "overlap", "serving", "rssi"]
    pt = {"noise": -90, "aps": [{"bssid": "02:00:00:00:00:01", "rssi": -50, "channel": 6, "band": "2.4", "ssid": "A"},
                                {"bssid": "02:00:00:00:01:01", "rssi": -70, "channel": 6, "band": "2.4", "ssid": "B"},
                                {"bssid": "02:00:00:00:00:02", "rssi": -60, "channel": 6, "band": "2.4", "ssid": "C"}]}
    best = {"type": "best"}
    assert heatmap.view_value("rssi", pt, best)["v"] == -50
    assert heatmap.view_value("snr", pt, best)["v"] == 40
    assert heatmap.view_value("count", pt, best)["v"] == 2       # two radios (first 5 bytes) at -75 or better
    assert heatmap.view_value("overlap", pt, best)["v"] == 1     # the other radio on channel 6
    assert heatmap.view_value("rssi", pt, {"type": "ssid", "value": "Z"}).get("missing")
    assert heatmap.ramp(heatmap.ENG_STOPS, -100) == heatmap.ENG_STOPS[0][1]
    assert heatmap.cover_alpha(1) == 1 and heatmap.cover_alpha(6) == 0


def test_render_with_and_without_plan():
    for plan in (True, False):
        p = survey(plan=plan)
        imgs = heatmap.report_images(p)
        assert len(imgs) == len(heatmap.report_views(p)) and all(i["png"].startswith(PNG) for i in imgs)
    w, ln, items, has_plan = heatmap.area(survey(plan=False))
    assert not has_plan and items == [] and w >= 16 and ln >= 12
    assert heatmap.report_images(projects.new_project("empty")) == []


def _upload(client, p: dict) -> str:
    created = client.post("/api/projects", json={"name": p["name"], "plan": p["plan"]}).json()
    pid = created["id"]
    store = client.app.state.store
    full = store.project(pid)
    full["points"], full["aps"], full["plan"] = p["points"], p["aps"], p["plan"]
    store.save_project(full)
    return pid


def test_project_reports_embed_heatmaps(client):
    pid = _upload(client, survey())
    html = client.get(f"/api/report.html?project={pid}").text
    assert html.count("data:image/png;base64,") == len(heatmap.report_views(survey()))
    assert "Signal: strongest AP at each point" in html and "5 measured points" in html
    r = client.post("/api/report.docx", json={"project": pid})
    xml, media = _media(r.content)
    assert r.status_code == 200 and "Coverage" in xml and len(media) == len(heatmap.report_views(survey()))
    views = client.get(f"/api/projects/{pid}/heatmaps").json()["views"]
    png = client.get(f"/api/projects/{pid}/heatmap.png?view={len(views) - 1}&width=600")
    assert png.status_code == 200 and png.content.startswith(PNG)
    assert client.get(f"/api/projects/{pid}/heatmap.png?view=99").status_code == 404


def test_live_report_uses_latest_survey(client):
    client.post("/api/scan")
    html = client.get("/api/report.html?minutes=10").text
    assert "No measured points yet" in html and "data:image/png" not in html
    xml, media = _media(client.post("/api/report.docx", json={"minutes": 10}).content)
    assert "No measured points yet" in xml and media == []
    pid = _upload(client, survey(plan=False, placed=False))
    rep = client.get("/api/report?minutes=10").json()
    assert rep["coverage"]["project"] == pid and rep["coverage"]["has_plan"] is False
    html = client.get("/api/report.html?minutes=10").text
    assert len(re.findall("data:image/png;base64,", html)) == 4 and "no floor plan" in html
    xml, media = _media(client.post("/api/report.docx", json={"minutes": 10}).content)
    assert len(media) == 4 and "Heat test" in xml


def test_empty_project_report_says_so(client):
    pid = client.post("/api/projects", json={"name": "Nothing yet"}).json()["id"]
    assert "No measured points yet" in client.get(f"/api/report.html?project={pid}").text
    xml, media = _media(client.post("/api/report.docx", json={"project": pid}).content)
    assert "No measured points yet" in xml and media == []
