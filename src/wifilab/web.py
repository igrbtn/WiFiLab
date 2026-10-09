"""HTTP API and the web UI (127.0.0.1 only)."""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, analysis, config, fieldtab, heatmap, location, oui, plans, projects, report
from .scanloop import ScanService, enrich
from .scanner import Scanner, get_scanner
from .store import Store

STATIC = Path(str(resources.files("wifilab").joinpath("static")))
BSSID = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
MAX_IMPORT = 32 * 1024 * 1024

# Set by the menubar app: asks for Location Services on the Cocoa main thread.
location_request_hook: Callable[[], None] | None = None


def _safe_name(s: str, default: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s or "").strip("._")[:60] or default


def create_app(cfg: config.Config | None = None, scanner: Scanner | None = None, store: Store | None = None,
               start_loop: bool = True) -> FastAPI:
    cfg = cfg or config.load()
    store = store or Store(cfg.db_path)
    scanner = scanner or get_scanner(cfg.scanner)
    saved = store.settings()
    svc = ScanService(scanner, store, interval=saved.get("interval", cfg.scan_interval),
                      running=saved.get("running", cfg.autoscan), retention_days=cfg.retention_days,
                      oui_extra=cfg.data_dir / "oui.csv")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_loop:
            svc.start()
        yield
        svc.stop()

    app = FastAPI(title="WiFiLab", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.svc, app.state.store, app.state.cfg = svc, store, cfg
    oui_extra = cfg.data_dir / "oui.csv"

    def _project(pid: str) -> dict:
        p = store.project(pid)
        if p is None:
            raise HTTPException(404, f"no survey {pid}")
        return p

    def _location() -> dict:
        st = scanner.location_status()
        last = svc.last or {}
        st["hidden_results"] = int(last.get("anonymous", 0))
        st["blocking"] = bool(st.get("needed")) and st["hidden_results"] > 0 and not last.get("aps")
        return st

    # ---------- pages ----------

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def revalidate_static(request: Request, call_next):
        # Same-name JS modules change between versions: make the browser revalidate (ETag) instead of guessing.
        resp = await call_next(request)
        if request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    # ---------- info, settings, scanning ----------

    @app.get("/api/info")
    def info() -> dict:
        return {"version": __version__, "backend": scanner.backend, "data_dir": str(cfg.data_dir),
                "location": _location(), "state": svc.state(), "time": time.time()}

    @app.get("/api/settings")
    def get_settings() -> dict:
        return {"interval": svc.interval, "running": svc.running, "retention_days": cfg.retention_days}

    @app.put("/api/settings")
    def put_settings(body: dict = Body(...)) -> dict:
        if "interval" in body:
            try:
                svc.set_interval(int(body["interval"]))
            except (TypeError, ValueError):
                raise HTTPException(422, "interval must be a number of seconds") from None
            store.set_setting("interval", svc.interval)
        if "running" in body:
            svc.set_running(bool(body["running"]))
            store.set_setting("running", svc.running)
        return get_settings()

    @app.get("/api/oui")
    def oui_info() -> dict:
        return oui.info(cfg.data_dir)

    @app.post("/api/oui/update")
    def oui_update() -> dict:
        try:
            return oui.update(cfg.data_dir)
        except (RuntimeError, OSError) as e:
            raise HTTPException(502, f"Vendor database update failed: {e}. The previous database is still used.") from None

    @app.post("/api/scan")
    def scan_now() -> dict:
        return _live(svc.scan_now())

    def _live(res: dict | None) -> dict:
        res = res or {"ok": False, "aps": [], "error_code": "", "error": "", "connection": None, "anonymous": 0}
        aps = res.get("aps", [])
        return {"state": svc.state(), "location": _location(), "scan": res,
                "channels": {b: analysis.channel_stats(aps, b) for b in analysis.BANDS},
                "recommend": analysis.recommend(aps), "cochannel": analysis.cochannel(aps),
                "adjacent": analysis.adjacent_overlap(aps), "issues": analysis.environment_issues(aps)}

    @app.get("/api/live")
    def live() -> dict:
        return _live(svc.last if svc.last and (svc.last.get("ok") or not svc.last_ok) else svc.last_ok)

    @app.post("/api/location/request")
    def location_request() -> dict:
        if location_request_hook is None:
            st = location.status()
            st["message"] = ("Location permission can only be granted to the WiFiLab app bundle. Build it with "
                             "scripts/build_app.sh and start /Applications/WiFiLab.app, or allow your terminal app in "
                             "System Settings > Privacy & Security > Location Services.")
            return st
        location_request_hook()
        return {**location.status(), "message": "Requested: answer the macOS prompt, then rescan."}

    # ---------- history ----------

    @app.get("/api/inventory")
    def inventory(minutes: int = 60) -> dict:
        since = time.time() - max(1, min(minutes, 60 * 24 * 365)) * 60
        inv = [enrich({**a, "rssi": a.get("rssi_last")}, oui_extra) for a in store.inventory(since)]
        return {"minutes": minutes, "scans": store.scan_count(since), "aps": inv}

    @app.get("/api/history")
    def history(bssids: str, minutes: int = 30) -> dict:
        since = time.time() - max(1, min(minutes, 60 * 24 * 30)) * 60
        out = {}
        for b in bssids.lower().split(",")[:12]:
            b = b.strip()
            if BSSID.match(b):
                out[b] = store.history(b, since)
        return {"since": since, "scans": store.scan_times(since), "series": out}

    @app.delete("/api/history")
    def clear_history() -> dict:
        store.clear_history()
        return {"ok": True}

    # ---------- plans ----------

    @app.get("/api/plans/prompt")
    def plans_prompt(known: str = "", width_m: float | None = None, length_m: float | None = None,
                     notes: str = "", name: str = "") -> dict:
        if not (known or width_m or length_m or notes or name):
            return {"prompt": plans.PROMPT}
        return {"prompt": plans.build_prompt(known, width_m, length_m, notes, name)}

    @app.post("/api/plans/validate")
    async def plans_validate(request: Request):
        plan, errs = plans.validate_plan(await request.body())
        if errs:
            return JSONResponse({"detail": "\n".join(errs), "errors": errs}, status_code=422)
        return {"plan": plan}

    # ---------- projects ----------

    @app.get("/api/projects")
    def project_list() -> list[dict]:
        return store.projects()

    @app.post("/api/projects")
    def project_create(body: dict = Body(...)) -> dict:
        proj = projects.new_project(str(body.get("name") or "Untitled survey"))
        if body.get("plan") is not None:
            errs = projects.set_plan(proj, body["plan"])
            if errs:
                raise HTTPException(422, "\n".join(errs))
        return store.save_project(proj)

    @app.get("/api/projects/{pid}")
    def project_get(pid: str) -> dict:
        return _project(pid)

    @app.patch("/api/projects/{pid}")
    def project_patch(pid: str, body: dict = Body(...)) -> dict:
        p = _project(pid)
        for k, limit in (("name", 80), ("note", 2000), ("target_ssid", 64)):
            if k in body:
                p[k] = str(body[k] or "")[:limit]
        return store.save_project(p)

    @app.delete("/api/projects/{pid}")
    def project_delete(pid: str) -> dict:
        if not store.delete_project(pid):
            raise HTTPException(404, f"no survey {pid}")
        return {"ok": True}

    @app.put("/api/projects/{pid}/plan")
    async def project_plan(pid: str, request: Request):
        p = _project(pid)
        errs = projects.set_plan(p, await request.body())
        if errs:
            return JSONResponse({"detail": "\n".join(errs), "errors": errs}, status_code=422)
        return store.save_project(p)

    @app.put("/api/projects/{pid}/aps")
    def project_aps(pid: str, body: dict = Body(...)) -> dict:
        p = _project(pid)
        try:
            projects.set_aps(p, body.get("aps"))
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return {"aps": store.save_project(p)["aps"]}

    @app.put("/api/projects/{pid}/bg")
    def project_bg(pid: str, body: dict = Body(...)) -> dict:
        p = _project(pid)
        try:
            projects.set_bg(p, body.get("data_url"))
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        store.save_project(p)
        return {"ok": True, "bg": bool(p.get("bg"))}

    @app.post("/api/projects/{pid}/points")
    def point_measure(pid: str, body: dict = Body(...)) -> dict:
        """Scan here and now, store it as a survey point at (x_m, y_m)."""
        p = _project(pid)
        try:
            x, y = projects.check_xy(p, body.get("x_m"), body.get("y_m"))
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        res = svc.scan_fresh(position=(x, y))
        if not res["ok"]:
            raise HTTPException(409, res["error"] or "scan failed")
        if not res["aps"] and res.get("anonymous"):
            raise HTTPException(409, "The scan returned only hidden networks: allow Location Services for WiFiLab "
                                     "(see the banner) and measure again.")
        point = projects.point_from_scan(x, y, res, body.get("note", ""))
        if res.get("stale"):
            point["stale"] = True
        p = _project(pid)   # reread: the scan took seconds
        try:
            projects.add_point(p, point)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        store.save_project(p)
        out = {"point": point, "points": len(p["points"])}
        if point.get("stale"):
            out["warning"] = ("macOS returned the same scan as for the previous point (the radio was busy or the scan "
                              "came from the system cache). Wait a few seconds and measure this point again.")
        return out

    @app.patch("/api/projects/{pid}/points/{point_id}")
    def point_patch(pid: str, point_id: str, body: dict = Body(...)) -> dict:
        p = _project(pid)
        pt = next((x for x in p.get("points", []) if x.get("id") == point_id), None)
        if pt is None:
            raise HTTPException(404, "no such point")
        if "x_m" in body or "y_m" in body:
            try:
                pt["x_m"], pt["y_m"] = projects.check_xy(p, body.get("x_m", pt["x_m"]), body.get("y_m", pt["y_m"]))
            except ValueError as e:
                raise HTTPException(422, str(e)) from None
        if "note" in body:
            pt["note"] = str(body["note"] or "")[:120]
        store.save_project(p)
        return {"point": pt}

    @app.delete("/api/projects/{pid}/points/{point_id}")
    def point_delete(pid: str, point_id: str) -> dict:
        p = _project(pid)
        n = len(p.get("points", []))
        p["points"] = [x for x in p.get("points", []) if x.get("id") != point_id]
        if len(p["points"]) == n:
            raise HTTPException(404, "no such point")
        store.save_project(p)
        return {"ok": True, "points": len(p["points"])}

    @app.get("/api/projects/{pid}/inventory")
    def project_inventory(pid: str) -> dict:
        p = _project(pid)
        return {"aps": projects.inventory(p, oui_extra), "ssids": projects.ssids(p)}

    @app.get("/api/projects/{pid}/export.json")
    def project_export(pid: str) -> Response:
        p = _project(pid)
        body = {"kind": "wifilab_project", "v": 1, "app": f"WiFiLab {__version__}", **p}
        return _download(json.dumps(body, ensure_ascii=False, indent=1), f"{_safe_name(p['name'], 'survey')}.json",
                         "application/json")

    @app.get("/api/projects/{pid}/fieldtab-map.json")
    def project_export_map(pid: str) -> Response:
        p = _project(pid)
        return _download(json.dumps(projects.export_fieldtab_map(p), ensure_ascii=False),
                         f"wifi-map-{_safe_name(p['name'], 'survey')}.json", "application/json")

    @app.get("/api/projects/{pid}/plan.json")
    def project_export_plan(pid: str) -> Response:
        p = _project(pid)
        return _download(plans.compact(p["plan"]).decode("utf-8"), f"plan-{_safe_name(p['name'], 'survey')}.json",
                         "application/json")

    # ---------- import ----------

    @app.post("/api/import")
    async def import_file(request: Request, name: str = "import.json") -> dict:
        data = await request.body()
        if len(data) > MAX_IMPORT:
            raise HTTPException(413, "file is too big")
        try:
            kind, payload = fieldtab.detect(name, data)
            if kind == "wifilab_project":
                proj = {k: v for k, v in payload.items() if k not in ("kind", "v", "app", "id", "created", "updated")}
                errs = projects.set_plan(proj, proj.get("plan"))
                if errs:
                    raise ValueError("; ".join(errs))
                projects.set_aps(proj, proj.get("aps") or [])
                proj["points"] = [pt for pt in proj.get("points") or [] if isinstance(pt, dict)][:projects.MAX_POINTS]
            else:
                stem = re.sub(r"\.(json|csv)$", "", Path(name).name, flags=re.IGNORECASE)
                proj = fieldtab.to_project(stem, kind, payload)
        except (ValueError, TypeError, KeyError) as e:
            raise HTTPException(422, f"cannot import {name}: {e}") from None
        saved = store.save_project(proj)
        return {"kind": kind, "project": {"id": saved["id"], "name": saved["name"],
                                          "points": len(saved.get("points") or []),
                                          "aps": len(saved.get("survey_aps") or [])}}

    # ---------- reports ----------

    def _report_ctx(project: str | None, ssid: str | None, minutes: int) -> tuple[dict, dict | None]:
        """Report data and the survey whose heatmaps it shows (live report: the latest survey with points)."""
        if project:
            p = _project(project)
            return report.project_report(p, ssid, oui_extra), p
        since = time.time() - max(1, min(minutes, 60 * 24 * 365)) * 60
        inv = [enrich({**a, "rssi": a.get("rssi_last")}, oui_extra) for a in store.inventory(since)]
        data = report.report_data(inv, title="Live Wi-Fi environment", source="live",
                                  meta={"minutes": minutes, "scans": store.scan_count(since)})
        latest = next((x for x in store.projects() if x["points"]), None)
        p = store.project(latest["id"]) if latest else None
        data["coverage"] = report.coverage_info(p)
        if p and not ssid:
            data["coverage_ssid"] = p.get("target_ssid") or ""
        return data, p

    def _report(project: str | None, ssid: str | None, minutes: int) -> dict:
        return _report_ctx(project, ssid, minutes)[0]

    @app.get("/api/report")
    def report_json(project: str | None = None, ssid: str | None = None, minutes: int = 60) -> dict:
        return _report(project, ssid, minutes)

    @app.get("/api/report.csv")
    def report_csv(project: str | None = None, minutes: int = 60) -> Response:
        data = _report(project, None, minutes)
        return _download(report.inventory_csv(data["inventory"]), f"{_safe_name(data['title'], 'wifi')}-aps.csv",
                         "text/csv")

    @app.get("/api/report.html")
    def report_html(project: str | None = None, ssid: str | None = None, minutes: int = 60) -> Response:
        data, p = _report_ctx(project, ssid, minutes)
        return _download(report.html_report(data, p), f"{_safe_name(data['title'], 'wifi')}-report.html", "text/html")

    @app.get("/api/projects/{pid}/heatmaps")
    def project_heatmaps(pid: str, ssid: str = "") -> dict:
        p = _project(pid)
        return {"views": [{"index": i, "kind": v["kind"], "title": v["title"]}
                          for i, v in enumerate(heatmap.report_views(p, ssid or p.get("target_ssid") or ""))]}

    @app.get("/api/projects/{pid}/heatmap.png")
    def project_heatmap_png(pid: str, view: int = 0, ssid: str = "", width: int = 1400) -> Response:
        p = _project(pid)
        views = heatmap.report_views(p, ssid or p.get("target_ssid") or "")
        if not 0 <= view < len(views):
            raise HTTPException(404, "no such heatmap view")
        png = heatmap.render_png(p, views[view], max(400, min(width, 3000)))
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/report.docx")
    def report_docx(body: dict = Body(...)) -> Response:
        data, p = _report_ctx(body.get("project"), body.get("ssid"), int(body.get("minutes") or 60))
        images = [im for im in body.get("images") or [] if isinstance(im, dict)][:16]
        blob = report.docx_report(data, images, p)
        return _download(blob, f"{_safe_name(data['title'], 'wifi')}-report.docx",
                         "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    return app


def _download(body, filename: str, media: str) -> Response:
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
