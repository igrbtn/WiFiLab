"""Vendor database: IEEE CSV parsing, longest-prefix lookup, overrides and the update endpoint (no network)."""
import gzip

import pytest

from wifilab import oui

# Synthetic registry rows in the IEEE CSV layout; the organisations are made up.
MAL = (
    "Registry,Assignment,Organization Name,Organization Address\n"
    'MA-L,A4BBCC,"Example Widgets Co., Ltd.","1 Example Road Springfield US 00000 "\n'
    "MA-L,DDEEFF,IEEE Registration Authority,445 Hoes Lane Piscataway NJ US 08554\n"
    'MA-L,112233,"Muller Funk GmbH",Somewhere DE\n'
)
MAM = (
    "Registry,Assignment,Organization Name,Organization Address\n"
    "MA-M,DDEEFF1,Tiny Radio Inc,Somewhere\n"
    "MA-M,DDEEFF2,Private,\n"
)
MAS = (
    "Registry,Assignment,Organization Name,Organization Address\n"
    "MA-S,DDEEFF1AB,Smaller Block LLC,Somewhere\n"
)


def fake_fetch(url: str) -> str:
    return {oui.SOURCES["MA-L"]: MAL, oui.SOURCES["MA-M"]: MAM, oui.SOURCES["MA-S"]: MAS}[url]


@pytest.fixture(autouse=True)
def _fresh_cache():
    oui.reload()
    yield
    oui.reload()


@pytest.fixture
def small_registry(monkeypatch):
    # The real download refuses a suspiciously small MA-L list; the fixture is tiny on purpose.
    real = oui.download

    def download(dest, fetcher=None):
        fetcher = fetcher or oui.fetch
        entries = {}
        for url in oui.SOURCES.values():
            entries.update(oui.parse_ieee_csv(fetcher(url)))
        oui.write_db(dest, entries)
        return {"entries": len(entries)}

    monkeypatch.setattr(oui, "download", download)
    yield
    monkeypatch.setattr(oui, "download", real)


def test_short_name():
    assert oui.short_name("Example Widgets Co., Ltd.") == "Example Widgets"
    assert oui.short_name("M" + chr(0xFC) + "ller Funk GmbH") == "Muller Funk"
    assert oui.short_name("Cisco Systems, Inc") == "Cisco"
    assert oui.short_name("Private") == "Private registration"
    assert oui.short_name("  ") == ""
    assert len(oui.short_name("X" * 100)) == oui.MAX_NAME


def test_parse_and_round_trip(tmp_path):
    e = oui.parse_ieee_csv(MAL + MAM.split("\n", 1)[1] + MAS.split("\n", 1)[1])
    assert e["A4BBCC"] == "Example Widgets" and e["DDEEFF1"] == "Tiny Radio" and e["DDEEFF1AB"] == "Smaller Block"
    path = tmp_path / oui.DB_NAME
    oui.write_db(path, e)
    raw = gzip.decompress(path.read_bytes())
    assert raw.isascii() and raw.startswith(b"# wifilab-oui ")
    back, meta = oui.read_db(path.read_bytes())
    assert back == e and meta["entries"] == str(len(e))


def test_longest_prefix_and_overrides(tmp_path, small_registry):
    oui.update(tmp_path, fake_fetch)
    assert not oui.is_local("A4:BB:CC:00:00:00") and not oui.is_local("DD:EE:FF:00:00:00")
    assert oui.vendor("A4:BB:CC:01:02:03", tmp_path) == "Example Widgets"
    assert oui.vendor("DD:EE:FF:10:00:00", tmp_path) == "Tiny Radio"
    assert oui.vendor("DD:EE:FF:1A:B0:00", tmp_path) == "Smaller Block"
    assert oui.vendor("DD:EE:FF:30:00:00", tmp_path) == "IEEE Registration Authority"
    assert oui.vendor("02:00:00:00:00:01", tmp_path) == oui.PRIVATE
    assert oui.vendor("", tmp_path) == ""
    (tmp_path / "oui.csv").write_text("prefix,vendor\na4:bb:cc,My Name\n02:00:00,Lab VM\n")
    oui.reload()
    assert oui.vendor("A4:BB:CC:01:02:03", tmp_path / "oui.csv") == "My Name"
    assert oui.vendor("02:00:00:00:00:01", tmp_path) == "Lab VM"
    info = oui.info(tmp_path)
    assert info["source"] == "downloaded" and info["entries"] == 6 and info["user_entries"] == 2


def test_bundled_fallback_when_download_is_corrupt(tmp_path):
    (tmp_path / oui.DB_NAME).write_bytes(b"not gzip")
    assert oui.info(tmp_path)["source"] == "bundled"
    assert oui.vendor("00:00:0C:00:00:01", tmp_path) == "Cisco"


def test_download_errors_are_readable(tmp_path):
    def broken(url):
        raise OSError("network is unreachable")

    with pytest.raises(RuntimeError, match="cannot download MA-L"):
        oui.download(tmp_path / oui.DB_NAME, broken)
    with pytest.raises(RuntimeError, match="truncated"):
        oui.download(tmp_path / oui.DB_NAME, fake_fetch)
    assert not (tmp_path / oui.DB_NAME).exists()


def test_api_update(client, cfg, monkeypatch, small_registry):
    assert client.get("/api/oui").json()["source"] == "bundled"
    monkeypatch.setattr(oui, "fetch", fake_fetch)
    r = client.post("/api/oui/update")
    assert r.status_code == 200 and r.json()["source"] == "downloaded" and r.json()["entries"] == 6
    assert (cfg.data_dir / oui.DB_NAME).is_file()

    def fail(dest, fetcher=None):
        raise RuntimeError("cannot download MA-L registry: timed out")

    monkeypatch.setattr(oui, "download", fail)
    r = client.post("/api/oui/update")
    assert r.status_code == 502 and "still used" in r.json()["detail"]
