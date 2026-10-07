import pytest
from conftest import ap

from wifilab import analysis as A


@pytest.mark.parametrize("ch,hint,band", [(1, None, "2.4"), (14, None, "2.4"), (36, None, "5"), (165, None, "5"),
                                          (37, "6", "6"), (181, None, "6"), (0, None, "?"), ("x", None, "?")])
def test_band_of(ch, hint, band):
    assert A.band_of(ch, hint) == band


@pytest.mark.parametrize("rec,span", [
    (dict(channel=6, width=20), (4, 8, 6)),
    (dict(channel=1, width=40, second="above"), (-1, 7, 3)),
    (dict(channel=11, width=40, second="below"), (5, 13, 9)),
    (dict(channel=3, width=40), (1, 9, 5)),          # no secondary given: low channels go above
    (dict(channel=44, width=80, band="5"), (34, 50, 42)),
    (dict(channel=157, width=40, band="5"), (155, 163, 159)),
    (dict(channel=104, width=160, band="5"), (98, 130, 114)),
    (dict(channel=37, width=80, band="6"), (31, 47, 39)),
    (dict(channel=36, width=20, band="5"), (34, 38, 36)),
])
def test_span(rec, span):
    assert A.span(rec) == span


def test_channel_stats_matches_the_tablet_weighting():
    aps = [ap(channel=1, rssi=-50), ap("02:00:00:00:00:02", channel=6, rssi=-70, width=40, second="above")]
    st = {s["channel"]: s for s in A.channel_stats(aps, "2.4")}
    assert st[1]["aps"] == 1 and st[1]["max_rssi"] == -50
    assert st[3]["load"] == 50   # ch 1 reaches up to 3, the 40 MHz AP on 6 reaches down to 4
    assert st[4]["load"] == 30 and st[12]["load"] == 30 and st[13]["load"] == 0
    assert [s["channel"] for s in A.channel_stats([], "2.4")] == list(range(1, 14))


def test_recommend():
    aps = [ap(channel=1, rssi=-40), ap("02:00:00:00:00:02", channel=6, rssi=-45),
           ap("02:00:00:00:00:03", channel=36, rssi=-50, width=80, band="5")]
    r = A.recommend(aps)
    assert r["2.4"]["channel"] == 11
    assert r["5"]["channel"] in (149, 153, 157, 161, 165)
    assert r["5"]["block80"]["channels"] == [149, 153, 157, 161]
    assert r["6"]["channel"] == 5


@pytest.mark.parametrize("label,cls", [("OPEN", "open"), ("WEP", "wep"), ("WPA", "wpa1"), ("WPA/WPA2", "wpa1"),
                                       ("WPA2", "wpa2"), ("WPA2/WPA3", "wpa2"), ("WPA3", "wpa3"), ("WPA2-ENT", "ent"),
                                       ("WPA3-ENT192", "ent"), ("OWE", "owe"), ("OWE-TRANS", "owe"), ("", "unknown"),
                                       ("WAPI", "unknown")])
def test_sec_class(label, cls):
    assert A.sec_class(label) == cls


def test_environment_issues():
    aps = [ap("02:00:00:00:00:01", "Cafe", 11, -60, security="OPEN"),
           ap("02:00:00:00:00:02", "Printer", 6, -60, security="WEP"),
           ap("02:00:00:00:00:03", "Legacy", 1, -60, security="WPA/WPA2"),
           ap("02:00:00:00:00:04", "", 6, -70),
           ap("02:00:00:00:00:05", "Wide", 3, -60, width=40)]
    codes = {i["code"]: i for i in A.environment_issues(aps)}
    assert {"open_network", "wep", "wpa1", "hidden_ssid", "wide_24", "nonstandard_24", "adjacent_overlap"} <= set(codes)
    assert codes["open_network"]["severity"] == "high"
    assert codes["open_network"]["bssids"] == ["02:00:00:00:00:01"]
    sev = [i["severity"] for i in A.environment_issues(aps)]
    assert sev == sorted(sev, key=A.SEVERITY_ORDER.get)


def test_cochannel_counts_radios_not_bssids():
    aps = [ap("02:00:00:00:01:00", "A", 6, -60), ap("02:00:00:00:01:01", "B", 6, -60),   # one radio, two SSIDs
           ap("02:00:00:00:02:00", "A", 6, -65), ap("02:00:00:00:03:00", "C", 6, -70),
           ap("02:00:00:00:04:00", "D", 6, -90)]                                         # too weak to count
    [e] = A.cochannel(aps)
    assert e["channel"] == 6 and e["radios"] == 3
    assert any(i["code"] == "cochannel" for i in A.environment_issues(aps))


def test_adjacent_overlap_dedupes_virtual_aps():
    aps = [ap("02:00:00:00:01:00", "A", 1, -60), ap("02:00:00:00:01:01", "B", 1, -60), ap("02:00:00:00:02:00", "C", 3, -60)]
    assert len(A.adjacent_overlap(aps)) == 1
    assert A.adjacent_overlap([ap(channel=1), ap("02:00:00:00:00:09", channel=6)]) == []


def test_coverage_issues():
    pts = [{"x_m": 1, "y_m": 1, "noise": -90, "aps": [{"bssid": "a", "ssid": "Corp", "rssi": -50}]},
           {"x_m": 5, "y_m": 1, "noise": -90, "aps": [{"bssid": "a", "ssid": "Corp", "rssi": -80}]},
           {"x_m": 9, "y_m": 1, "noise": -90, "aps": [{"bssid": "b", "ssid": "Other", "rssi": -55}]},
           {"x_m": 9, "y_m": 5, "aps": []}]
    codes = {i["code"]: i for i in A.coverage_issues(pts, "Corp")}
    assert "weak_coverage" in codes and "no_coverage" in codes and "low_snr" in codes
    assert "2 point(s)" in codes["no_coverage"]["title"]
    assert A.coverage_issues([]) == []
    assert A.point_best(pts[2]) == -55 and A.point_best(pts[2], "Corp") is None
