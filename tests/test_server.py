"""Port selection and server.json: a random free port by default, the running instance is found and reused."""
import json
import os
import stat
import threading
import time

import pytest

from wifilab import cli, config, server
from wifilab.scanner.fake import FakeScanner
from wifilab.store import Store
from wifilab.web import create_app


def test_port_from_environment(monkeypatch):
    for raw, want in (("", 0), ("0", 0), ("abc", 0), ("80", 0), ("8197", 8197), ("70000", 0)):
        monkeypatch.setenv("WIFILAB_PORT", raw)
        assert config.load().port == want
    monkeypatch.delenv("WIFILAB_PORT")
    assert config.load().port == 0


def test_bind_random_ports_are_free_and_distinct():
    a, b = server.bind("127.0.0.1"), server.bind("127.0.0.1")
    try:
        pa, pb = a.getsockname()[1], b.getsockname()[1]
        assert pa and pb and pa != pb
        with pytest.raises(OSError):
            server.bind("127.0.0.1", pa)      # taken: an explicit port fails loudly
    finally:
        a.close()
        b.close()


def test_info_file(tmp_path):
    info = server.write_info(tmp_path, "127.0.0.1", 50123)
    path = server.info_path(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["url"] == "http://127.0.0.1:50123/" == info["url"]
    assert server.running(tmp_path, timeout=0.3) is None     # nothing listens there: stale file is ignored
    server.write_info(tmp_path, "127.0.0.1", 50123)
    data = json.loads(path.read_text())
    data["pid"] = os.getpid() + 1
    path.write_text(json.dumps(data))
    server.remove_info(tmp_path)                                # not ours: kept
    assert path.exists()
    path.write_text("garbage")
    assert server.read_info(tmp_path) is None


def test_serve_writes_and_removes_info(tmp_path, monkeypatch, capsys):
    cfg = config.Config(host="127.0.0.1", port=0, data_dir=tmp_path, scanner="fake", scan_interval=15,
                        retention_days=14, autoscan=False)
    app = create_app(cfg, scanner=FakeScanner(), store=Store(":memory:"), start_loop=False)
    servers = []
    import uvicorn
    real_server = uvicorn.Server

    class Capturing(real_server):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            servers.append(self)

    monkeypatch.setattr(uvicorn, "Server", Capturing)
    sock = server.bind(cfg.host, 0)
    port = sock.getsockname()[1]
    t = threading.Thread(target=server.serve, args=(cfg, sock, app), daemon=True)
    t.start()
    url = None
    for _ in range(100):
        url = server.running(tmp_path, timeout=0.3)
        if url:
            break
        time.sleep(0.05)
    assert url == f"http://127.0.0.1:{port}/"
    # A second start finds the live instance instead of starting another.
    monkeypatch.setenv("WIFILAB_DATA_DIR", str(tmp_path))
    cli.main(["web"])
    assert "already runs at " + url in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        cli.main(["url"])
    assert e.value.code == 0 and url in capsys.readouterr().out
    servers[0].should_exit = True
    t.join(5)
    assert not server.info_path(tmp_path).exists()
    with pytest.raises(SystemExit) as e:
        cli.main(["url"])
    assert e.value.code == 1
