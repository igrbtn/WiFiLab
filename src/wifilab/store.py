"""SQLite store (one file in the data dir): scan history, survey projects, settings."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    ok INTEGER NOT NULL,
    error_code TEXT NOT NULL DEFAULT '',
    backend TEXT NOT NULL DEFAULT '',
    ap_count INTEGER NOT NULL DEFAULT 0,
    anonymous INTEGER NOT NULL DEFAULT 0,
    conn TEXT
);
CREATE INDEX IF NOT EXISTS scans_ts ON scans(ts);
CREATE TABLE IF NOT EXISTS obs (
    scan_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    bssid TEXT NOT NULL,
    ssid TEXT NOT NULL DEFAULT '',
    rssi INTEGER NOT NULL,
    noise INTEGER,
    channel INTEGER NOT NULL,
    band TEXT NOT NULL,
    width INTEGER NOT NULL DEFAULT 20,
    security TEXT NOT NULL DEFAULT '',
    phy TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    beacon_ms INTEGER,
    hidden INTEGER NOT NULL DEFAULT 0,
    extra TEXT
);
CREATE INDEX IF NOT EXISTS obs_bssid_ts ON obs(bssid, ts);
CREATE INDEX IF NOT EXISTS obs_ts ON obs(ts);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

def _obs_values(a: dict) -> tuple:
    return (str(a["bssid"]).lower(), a.get("ssid") or "", int(a["rssi"]), a.get("noise"), int(a["channel"]),
            a.get("band") or "?", int(a.get("width") or 20), a.get("security") or "", a.get("phy") or "",
            a.get("country") or "", a.get("beacon_ms"), int(bool(a.get("hidden"))),
            json.dumps({"phy_modes": a.get("phy_modes", []), "ibss": bool(a.get("ibss"))}))


class Store:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---------- scans ----------

    def add_scan(self, res: dict) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO scans (ts, ok, error_code, backend, ap_count, anonymous, conn) VALUES (?,?,?,?,?,?,?)",
                (res["ts"], int(res["ok"]), res.get("error_code", ""), res.get("backend", ""), len(res.get("aps", [])),
                 int(res.get("anonymous", 0)), json.dumps(res.get("connection")) if res.get("connection") else None))
            sid = cur.lastrowid
            rows = [(sid, res["ts"], *_obs_values(a)) for a in res.get("aps", [])]
            self._db.executemany(
                "INSERT INTO obs (scan_id, ts, bssid, ssid, rssi, noise, channel, band, width, security, phy, country, "
                "beacon_ms, hidden, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self._db.commit()
            return sid

    def scan_count(self, since: float = 0) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM scans WHERE ts >= ? AND ok = 1", (since,)).fetchone()[0]

    def recent_scans(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, ts, ok, error_code, backend, ap_count, anonymous FROM scans "
                                    "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def inventory(self, since: float = 0) -> list[dict]:
        """Every BSSID seen since `since`: latest attributes plus min/avg/max RSSI, count, first/last seen."""
        with self._lock:
            stats = self._db.execute(
                "SELECT bssid, MIN(rssi) AS rssi_min, ROUND(AVG(rssi)) AS rssi_avg, MAX(rssi) AS rssi_max, "
                "COUNT(*) AS seen_count, MIN(ts) AS first_seen FROM obs WHERE ts >= ? GROUP BY bssid", (since,)).fetchall()
            latest = self._db.execute(
                "SELECT bssid, MAX(ts) AS last_seen, ssid, rssi, noise, channel, band, width, security, phy, country, "
                "beacon_ms, hidden, extra FROM obs WHERE ts >= ? GROUP BY bssid", (since,)).fetchall()
        st = {r["bssid"]: dict(r) for r in stats}
        out = []
        for r in latest:
            d = dict(r)
            extra = json.loads(d.pop("extra") or "{}")
            d["rssi_last"] = d.pop("rssi")
            d["hidden"] = bool(d["hidden"]) or not d["ssid"]
            d["phy_modes"] = extra.get("phy_modes", [])
            s = st.get(d["bssid"], {})
            d.update({k: s.get(k) for k in ("rssi_min", "rssi_avg", "rssi_max", "seen_count", "first_seen")})
            if d["rssi_avg"] is not None:
                d["rssi_avg"] = int(d["rssi_avg"])
            out.append(d)
        return sorted(out, key=lambda a: -(a["rssi_max"] or -999))

    def history(self, bssid: str, since: float) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT ts, rssi, noise, channel FROM obs WHERE bssid = ? AND ts >= ? ORDER BY ts",
                                    (bssid.lower(), since)).fetchall()
        return [dict(r) for r in rows]

    def scan_times(self, since: float) -> list[float]:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT ts FROM scans WHERE ok = 1 AND ts >= ? ORDER BY ts", (since,))]

    def prune(self, keep_days: int) -> int:
        cut = time.time() - keep_days * 86400
        with self._lock:
            n = self._db.execute("DELETE FROM obs WHERE ts < ?", (cut,)).rowcount
            self._db.execute("DELETE FROM scans WHERE ts < ?", (cut,))
            self._db.commit()
        return n

    def clear_history(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM obs")
            self._db.execute("DELETE FROM scans")
            self._db.commit()

    # ---------- projects ----------

    def projects(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, name, created, updated, data FROM projects ORDER BY updated DESC").fetchall()
        out = []
        for r in rows:
            d = json.loads(r["data"])
            plan = d.get("plan") or {}
            out.append({"id": r["id"], "name": r["name"], "created": r["created"], "updated": r["updated"],
                        "points": len(d.get("points") or []), "width_m": plan.get("width_m"),
                        "length_m": plan.get("length_m"), "source": d.get("source", "")})
        return out

    def project(self, pid: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT id, name, created, updated, data FROM projects WHERE id = ?", (pid,)).fetchone()
        if not r:
            return None
        return {**json.loads(r["data"]), "id": r["id"], "name": r["name"], "created": r["created"],
                "updated": r["updated"]}

    def save_project(self, proj: dict) -> dict:
        now = time.time()
        pid = proj.get("id") or uuid.uuid4().hex[:12]
        data = {k: v for k, v in proj.items() if k not in ("id", "name", "created", "updated")}
        with self._lock:
            old = self._db.execute("SELECT created FROM projects WHERE id = ?", (pid,)).fetchone()
            created = old["created"] if old else now
            self._db.execute("INSERT OR REPLACE INTO projects (id, name, created, updated, data) VALUES (?,?,?,?,?)",
                             (pid, proj.get("name") or "Untitled survey", created, now, json.dumps(data)))
            self._db.commit()
        return {**proj, "id": pid, "created": created, "updated": now}

    def delete_project(self, pid: str) -> bool:
        with self._lock:
            n = self._db.execute("DELETE FROM projects WHERE id = ?", (pid,)).rowcount
            self._db.commit()
        return n > 0

    # ---------- settings ----------

    def settings(self) -> dict:
        with self._lock:
            return {r["key"]: json.loads(r["value"]) for r in self._db.execute("SELECT key, value FROM settings")}

    def set_setting(self, key: str, value) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))
            self._db.commit()
