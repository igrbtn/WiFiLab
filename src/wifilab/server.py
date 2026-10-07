"""Running the web server: a random free port on 127.0.0.1 by default, and <data dir>/server.json so the CLI, the
menubar app and the user can find the running instance (and a second start opens it instead of starting another)."""
from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import tempfile
import time
import urllib.request
from pathlib import Path

from . import __version__, config

INFO_NAME = "server.json"
log = logging.getLogger("wifilab.server")


def bind(host: str, port: int = 0) -> socket.socket:
    """A listening socket; port 0 lets the OS pick a free one. Bound before uvicorn starts, so there is no race
    between finding a free port and using it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if port:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    sock.set_inheritable(True)
    return sock


def url_for(host: str, port: int) -> str:
    return f"http://{host}:{port}/"


def info_path(data_dir: Path) -> Path:
    return Path(data_dir) / INFO_NAME


def write_info(data_dir: Path, host: str, port: int) -> dict:
    info = {"url": url_for(host, port), "host": host, "port": port, "pid": os.getpid(), "version": __version__,
            "started": time.time()}
    path = info_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".server-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(info, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return info


def read_info(data_dir: Path) -> dict | None:
    try:
        info = json.loads(info_path(data_dir).read_text())
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and isinstance(info.get("port"), int) else None


def remove_info(data_dir: Path, port: int | None = None) -> None:
    """Delete server.json if it describes this process (another instance may have replaced it)."""
    info = read_info(data_dir)
    if info and info.get("pid") == os.getpid() and (port is None or info.get("port") == port):
        with contextlib.suppress(OSError):
            info_path(data_dir).unlink()


def running(data_dir: Path, timeout: float = 1.5) -> str | None:
    """URL of a live WiFiLab described by server.json, else None (a stale file is ignored)."""
    info = read_info(data_dir)
    if not info:
        return None
    url = url_for(info.get("host") or "127.0.0.1", info["port"])
    try:
        with urllib.request.urlopen(url + "api/info", timeout=timeout) as r:
            ok = "version" in json.loads(r.read())
    except (OSError, ValueError):
        return None
    return url if ok else None


def serve(cfg: config.Config, sock: socket.socket | None = None, app=None) -> None:
    """Run uvicorn on sock (bound from cfg when not given); server.json lives while the server does."""
    import uvicorn

    from .web import create_app

    sock = sock or bind(cfg.host, cfg.port)
    port = sock.getsockname()[1]
    write_info(cfg.data_dir, cfg.host, port)
    log.info("WiFiLab %s on %s", __version__, url_for(cfg.host, port))
    try:
        server = uvicorn.Server(uvicorn.Config(app or create_app(cfg), log_level="info"))
        server.run(sockets=[sock])
    finally:
        remove_info(cfg.data_dir, port)
        sock.close()
