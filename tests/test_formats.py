"""Plans, AP marks and FieldTab import on synthetic fixtures."""
import json

import pytest
from conftest import FIXTURES

from wifilab import apmarks, fieldtab, plans


def test_plan_validation_normalizes_and_reports():
    plan, errs = plans.validate_plan((FIXTURES / "plan-example.json").read_text())
    assert errs == [] and plan["items"][2]["label"] == "D1"
    fenced = "Here you go:\n```json\n" + json.dumps(plan) + "\n```\nDone."
    assert plans.validate_plan(fenced)[0] == plan
    bad = {"format": "fieldtab.plan/1", "width_m": 200, "length_m": 5,
           "items": [{"kind": "roof", "x1": 0, "y1": 0, "x2": 1, "y2": 1}, {"kind": "wall", "x1": 1, "y1": 1, "x2": 1, "y2": 1}]}
    _, errs = plans.validate_plan(bad)
    assert any("width_m" in e for e in errs) and any("roof" in e for e in errs) and any("same point" in e for e in errs)
    assert plans.validate_plan("no json here")[1]


def test_prompt_matches_the_format_doc():
    doc = (FIXTURES.parents[1] / "docs" / "PLAN_FORMAT.md").read_text()
    quoted = " ".join(ln[2:].strip() for ln in doc.splitlines() if ln.startswith("> "))
    assert " ".join(plans.PROMPT.split()) == " ".join(quoted.split())


@pytest.mark.parametrize("raw,msg", [
    ("x", "list"), ([{"id": "a b", "x_m": 1, "y_m": 1}], "bad id"),
    ([{"id": "a", "x_m": -1, "y_m": 1}], "x_m"), ([{"id": "a", "x_m": 1, "y_m": 1, "bssids": ["zz"]}], "BSSID"),
    ([{"id": "a", "x_m": 1, "y_m": 1}, {"id": "a", "x_m": 1, "y_m": 1}], "duplicate"),
])
def test_apmarks_validation(raw, msg):
    with pytest.raises(ValueError, match=msg):
        apmarks.validate_aps(raw)


def test_apmarks_normalize():
    [a] = apmarks.validate_aps([{"id": "ap1", "name": " Hall\x00 ", "x_m": 1.23456, "y_m": 2,
                                 "bssids": ["02:00:00:00:00:01", "02:00:00:00:00:01".upper()]}])
    assert a == {"id": "ap1", "name": "Hall", "x_m": 1.235, "y_m": 2.0, "bssids": ["02:00:00:00:00:01"]}


def test_detect_kinds():
    for name, kind in [("fieldtab-wifi-survey.json", "wifi_survey"), ("fieldtab-wifi-survey.csv", "wifi_survey"),
                       ("fieldtab-wifi-map.json", "wifi_map"), ("fieldtab-wifi-doc.json", "wifi_doc"),
                       ("plan-example.json", "plan")]:
        assert fieldtab.detect(name, (FIXTURES / name).read_bytes())[0] == kind
    with pytest.raises(ValueError):
        fieldtab.detect("x.json", b'{"hello": 1}')
    with pytest.raises(ValueError):
        fieldtab.detect("x.json", b"not json")


def test_survey_import():
    for name in ("fieldtab-wifi-survey.json", "fieldtab-wifi-survey.csv"):
        kind, payload = fieldtab.detect(name, (FIXTURES / name).read_bytes())
        p = fieldtab.to_project("s", kind, payload)
        by = {a["bssid"]: a for a in p["survey_aps"]}
        assert len(by) == 3
        assert by["02:00:5e:10:00:02"]["hidden"] and by["02:00:5e:10:00:02"]["width"] == 40
        assert by["02:00:5e:20:00:01"]["security"] == "OPEN" and by["02:00:5e:20:00:01"]["band"] == "2.4"


def test_map_import_keeps_metres_and_plan():
    kind, payload = fieldtab.detect("m.json", (FIXTURES / "fieldtab-wifi-map.json").read_bytes())
    p = fieldtab.to_project("m", kind, payload)
    assert p["plan"]["width_m"] == 6 and p["plan"]["length_m"] == 4
    assert [it["kind"] for it in p["plan"]["items"]] == ["wall", "door"]
    assert plans.validate_plan(p["plan"])[1] == []
    assert [(pt["x_m"], pt["y_m"]) for pt in p["points"]] == [(0.25, 0.25), (2.25, 1.25), (4.75, 2.75)]
    assert p["points"][0]["aps"][0] == {"bssid": "02:00:5e:10:00:01", "ssid": "Example-Corp", "rssi": -50, "channel": 1,
                                        "band": "2.4"}


def test_doc_import_resolves_names_from_seen():
    kind, payload = fieldtab.detect("d.json", (FIXTURES / "fieldtab-wifi-doc.json").read_bytes())
    p = fieldtab.to_project("d", kind, payload)
    assert p["target_ssid"] == "Example-Corp"
    assert len(p["points"]) == 2 and len(p["survey_aps"]) == 3
    far = max(p["points"], key=lambda x: x["x_m"])
    assert (far["x_m"], far["y_m"]) == (1.25, 1.25)   # cell 23 of a 10-wide grid: col 3, row 3
    assert {a["ssid"] for a in far["aps"]} == {"Example-Corp", "Example-Open"}
    assert p["plan"]["items"][1]["kind"] == "window"


def test_old_cell_map_reads_at_half_metre():
    raw = {"cols": 4, "rows": 2, "points": [{"col": 4, "row": 2, "aps": [{"bssid": "02:00:00:00:00:01", "rssi": -60}]}]}
    p = fieldtab.to_project("old", "wifi_map", raw)
    assert p["plan"]["width_m"] == 2 and p["plan"]["length_m"] == 1
    assert (p["points"][0]["x_m"], p["points"][0]["y_m"]) == (1.75, 0.75)
