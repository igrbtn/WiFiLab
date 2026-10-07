"""Entry points: `wifilab web` (server only), `wifilab menubar` (the app bundle default), `wifilab scan` (one scan,
printed as a table or JSON)."""
from __future__ import annotations

import argparse
import json
import logging
import threading
import webbrowser

import uvicorn

from . import __version__, config


def _logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def serve(cfg: config.Config) -> None:
    from .web import create_app
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="info")


def menubar(cfg: config.Config | None = None) -> None:
    import rumps
    from PyObjCTools import AppHelper

    from . import location, web

    _logging()   # the app bundle's launcher calls menubar() directly
    cfg = cfg or config.load()
    url = f"http://{cfg.host}:{cfg.port}/"

    class WiFiLabApp(rumps.App):
        def __init__(self) -> None:
            super().__init__("WiFiLab", title="WL", quit_button="Quit")
            self.menu = [rumps.MenuItem("Open WiFiLab", callback=lambda _: webbrowser.open(url)),
                         rumps.MenuItem("Allow Location Services...", callback=lambda _: location.request())]

    # The web API asks for the permission from a worker thread; CoreLocation wants the main one.
    web.location_request_hook = lambda: AppHelper.callAfter(location.request)
    threading.Thread(target=serve, args=(cfg,), daemon=True).start()
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    # Ask once at start: without it macOS 14+ hides SSIDs and BSSIDs from every scan.
    threading.Timer(0.5, lambda: AppHelper.callAfter(location.request)).start()
    WiFiLabApp().run()


def scan_once(as_json: bool, kind: str) -> int:
    from .analysis import environment_issues
    from .scanloop import enrich
    from .scanner import get_scanner

    sc = get_scanner(kind)
    res = sc.scan()
    res["aps"] = [enrich(a) for a in res["aps"]]
    if as_json:
        print(json.dumps(res, indent=1, default=str))
        return 0 if res["ok"] else 1
    if not res["ok"]:
        print(f"error ({res['error_code']}): {res['error']}")
        return 1
    print(f"backend {res['backend']}: {len(res['aps'])} networks listed, {res['anonymous']} without SSID/BSSID "
          f"(Location Services), location: {sc.location_status().get('status')}")
    for a in sorted(res["aps"], key=lambda x: -x["rssi"]):
        print(f"{a['rssi']:>4} dBm  {a['band']:>3} ch {a['channel']:>3}/{a['width']:<3}  {a['security']:<12} "
              f"{a['bssid']}  {a['ssid'] or '(hidden)'}  {a['vendor']}")
    for i in environment_issues(res["aps"]):
        print(f"[{i['severity']}] {i['title']}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="wifilab", description=f"WiFiLab {__version__}: Wi-Fi survey for macOS")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("web", help="run the web UI only (no menubar)")
    sub.add_parser("menubar", help="menubar app + web UI (default)")
    p = sub.add_parser("scan", help="one scan, printed")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args()
    cfg = config.load()
    if a.cmd == "web":
        _logging()
        serve(cfg)
    elif a.cmd == "scan":
        raise SystemExit(scan_once(a.json, cfg.scanner))
    else:
        menubar(cfg)


if __name__ == "__main__":
    main()
