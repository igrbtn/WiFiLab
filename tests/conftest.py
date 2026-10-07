from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wifilab import config
from wifilab.scanner.fake import FakeScanner
from wifilab.store import Store
from wifilab.web import create_app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg(tmp_path):
    return config.Config(host="127.0.0.1", port=0, data_dir=tmp_path, scanner="fake", scan_interval=15,
                         retention_days=14, autoscan=False)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def client(cfg, store):
    app = create_app(cfg, scanner=FakeScanner(), store=store, start_loop=False)
    with TestClient(app) as c:
        yield c


def ap(bssid="02:00:00:00:00:01", ssid="Net", channel=6, rssi=-60, width=20, security="WPA2", band=None, **kw):
    from wifilab.analysis import band_of
    return {"bssid": bssid, "ssid": ssid, "channel": channel, "rssi": rssi, "width": width, "security": security,
            "band": band or band_of(channel), "hidden": not ssid, **kw}
