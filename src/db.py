"""Kalıcı SQLite şeması ve veri erişimi. Başlangıçta boş; sahte kayıt yok."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from src.config import DB_PATH, ensure_data_dir
from src.utils import iso, now_tr

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kml_file TEXT,
    name TEXT NOT NULL,
    description TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    altitude REAL,
    raw_coords TEXT,
    il TEXT,
    ilce TEXT,
    mahalle TEXT,
    district_center_id INTEGER,
    assignment_override INTEGER NOT NULL DEFAULT 0,
    identity_key TEXT NOT NULL UNIQUE,
    geocoded_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (district_center_id) REFERENCES district_centers(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS district_centers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    province TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(province, name)
);

CREATE TABLE IF NOT EXISTS osrm_cache (
    site_id INTEGER NOT NULL,
    district_center_id INTEGER NOT NULL,
    distance_km REAL,
    duration_min REAL,
    geometry_json TEXT,
    source TEXT NOT NULL DEFAULT 'osrm',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (site_id, district_center_id),
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE,
    FOREIGN KEY (district_center_id) REFERENCES district_centers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS outages (
    yedas_id TEXT PRIMARY KEY,
    title TEXT,
    details TEXT,
    start_at TEXT,
    end_at TEXT,
    address_json TEXT,
    coords_json TEXT,
    geojson_json TEXT,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outage_matches (
    yedas_id TEXT NOT NULL,
    site_id INTEGER NOT NULL,
    match_type TEXT NOT NULL,
    snapshot_at TEXT NOT NULL,
    PRIMARY KEY (yedas_id, site_id),
    FOREIGN KEY (yedas_id) REFERENCES outages(yedas_id) ON DELETE CASCADE,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fault_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL,
    mains_at TEXT NOT NULL,
    down_at TEXT NOT NULL,
    restored_at TEXT,
    backup_minutes REAL,
    outage_minutes REAL,
    comment TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    added_json TEXT NOT NULL,
    removed_json TEXT NOT NULL,
    updated_json TEXT NOT NULL,
    geocoded_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sites_il ON sites(il, ilce);
CREATE INDEX IF NOT EXISTS idx_matches_site ON outage_matches(site_id);
CREATE INDEX IF NOT EXISTS idx_outages_start ON outages(start_at);
CREATE INDEX IF NOT EXISTS idx_faults_site ON fault_events(site_id);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_data_dir()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def row_to_dict(row: sqlite3.Row | None) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def fetchall(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        cur = conn.execute(sql, params)
        return [row_to_dict(r) for r in cur.fetchall()]


def fetchone(sql: str, params: tuple = ()) -> Optional[dict[str, Any]]:
    with connect() as conn:
        cur = conn.execute(sql, params)
        return row_to_dict(cur.fetchone())


def execute(sql: str, params: tuple = ()) -> int:
    with connect() as conn:
        cur = conn.execute(sql, params)
        return int(cur.lastrowid)


def executemany(sql: str, seq: list[tuple]) -> None:
    with connect() as conn:
        conn.executemany(sql, seq)


# ----- sites -----

def site_count() -> int:
    row = fetchone("SELECT COUNT(*) AS c FROM sites")
    return int(row["c"]) if row else 0


def list_sites(where: str = "", params: tuple = ()) -> list[dict[str, Any]]:
    sql = "SELECT * FROM sites"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY name COLLATE NOCASE"
    return fetchall(sql, params)


def get_site(site_id: int) -> Optional[dict[str, Any]]:
    return fetchone("SELECT * FROM sites WHERE id = ?", (site_id,))


def upsert_site(payload: dict[str, Any]) -> int:
    now = iso(now_tr())
    existing = fetchone("SELECT id FROM sites WHERE identity_key = ?", (payload["identity_key"],))
    if existing:
        execute(
            """
            UPDATE sites SET
                kml_file=?, name=?, description=?, lat=?, lon=?, altitude=?, raw_coords=?,
                il=COALESCE(?, il), ilce=COALESCE(?, ilce), mahalle=COALESCE(?, mahalle),
                geocoded_at=COALESCE(?, geocoded_at), updated_at=?
            WHERE id=?
            """,
            (
                payload.get("kml_file"),
                payload["name"],
                payload.get("description"),
                payload["lat"],
                payload["lon"],
                payload.get("altitude"),
                payload.get("raw_coords"),
                payload.get("il"),
                payload.get("ilce"),
                payload.get("mahalle"),
                payload.get("geocoded_at"),
                now,
                existing["id"],
            ),
        )
        return int(existing["id"])
    return execute(
        """
        INSERT INTO sites (
            kml_file, name, description, lat, lon, altitude, raw_coords,
            il, ilce, mahalle, district_center_id, assignment_override,
            identity_key, geocoded_at, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,NULL,0,?,?,?,?)
        """,
        (
            payload.get("kml_file"),
            payload["name"],
            payload.get("description"),
            payload["lat"],
            payload["lon"],
            payload.get("altitude"),
            payload.get("raw_coords"),
            payload.get("il"),
            payload.get("ilce"),
            payload.get("mahalle"),
            payload["identity_key"],
            payload.get("geocoded_at"),
            now,
            now,
        ),
    )


def delete_sites_not_in(keys: list[str]) -> list[str]:
    if not keys:
        rows = fetchall("SELECT name FROM sites")
        execute("DELETE FROM sites")
        return [r["name"] for r in rows]
    placeholders = ",".join("?" * len(keys))
    rows = fetchall(f"SELECT name FROM sites WHERE identity_key NOT IN ({placeholders})", tuple(keys))
    execute(f"DELETE FROM sites WHERE identity_key NOT IN ({placeholders})", tuple(keys))
    return [r["name"] for r in rows]


def list_identity_keys() -> set[str]:
    return {r["identity_key"] for r in fetchall("SELECT identity_key FROM sites")}


def set_site_address(site_id: int, il: str, ilce: str, mahalle: str) -> None:
    execute(
        "UPDATE sites SET il=?, ilce=?, mahalle=?, geocoded_at=?, updated_at=? WHERE id=?",
        (il, ilce, mahalle, iso(now_tr()), iso(now_tr()), site_id),
    )


def set_site_district(site_id: int, district_id: Optional[int], override: bool) -> None:
    execute(
        "UPDATE sites SET district_center_id=?, assignment_override=?, updated_at=? WHERE id=?",
        (district_id, 1 if override else 0, iso(now_tr()), site_id),
    )
    if district_id is not None:
        execute("DELETE FROM osrm_cache WHERE site_id=?", (site_id,))


# ----- districts -----

def list_districts() -> list[dict[str, Any]]:
    return fetchall("SELECT * FROM district_centers ORDER BY province, name")


def add_district(name: str, province: str, lat: float, lon: float) -> int:
    return execute(
        "INSERT OR IGNORE INTO district_centers (name, province, lat, lon, created_at) VALUES (?,?,?,?,?)",
        (name.strip(), province.strip(), lat, lon, iso(now_tr())),
    )


def delete_district(district_id: int) -> None:
    execute("DELETE FROM district_centers WHERE id=?", (district_id,))


def get_district(district_id: int) -> Optional[dict[str, Any]]:
    return fetchone("SELECT * FROM district_centers WHERE id=?", (district_id,))


# ----- outages -----

def upsert_outage(item: dict[str, Any]) -> None:
    execute(
        """
        INSERT INTO outages (yedas_id, title, details, start_at, end_at, address_json, coords_json, geojson_json, last_seen_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(yedas_id) DO UPDATE SET
            title=excluded.title,
            details=excluded.details,
            start_at=excluded.start_at,
            end_at=excluded.end_at,
            address_json=excluded.address_json,
            coords_json=excluded.coords_json,
            geojson_json=excluded.geojson_json,
            last_seen_at=excluded.last_seen_at
        """,
        (
            item["yedas_id"],
            item.get("title"),
            item.get("details"),
            item.get("start_at"),
            item.get("end_at"),
            json.dumps(item.get("address") or [], ensure_ascii=False),
            json.dumps(item.get("coords") or [], ensure_ascii=False),
            json.dumps(item.get("geojson") or {}, ensure_ascii=False),
            iso(now_tr()),
        ),
    )


def replace_matches(yedas_id: str, site_ids: list[tuple[int, str]]) -> None:
    execute("DELETE FROM outage_matches WHERE yedas_id=?", (yedas_id,))
    if not site_ids:
        return
    now = iso(now_tr())
    executemany(
        "INSERT OR REPLACE INTO outage_matches (yedas_id, site_id, match_type, snapshot_at) VALUES (?,?,?,?)",
        [(yedas_id, sid, mtype, now) for sid, mtype in site_ids],
    )


def list_outages() -> list[dict[str, Any]]:
    return fetchall("SELECT * FROM outages ORDER BY start_at")


def matches_for_analysis() -> list[dict[str, Any]]:
    return fetchall(
        """
        SELECT m.yedas_id, m.site_id, m.match_type, m.snapshot_at,
               o.title, o.details, o.start_at, o.end_at,
               s.name AS site_name, s.il, s.ilce, s.mahalle, s.lat, s.lon
        FROM outage_matches m
        JOIN outages o ON o.yedas_id = m.yedas_id
        JOIN sites s ON s.id = m.site_id
        """
    )


# ----- osrm -----

def get_osrm(site_id: int, district_id: int) -> Optional[dict[str, Any]]:
    return fetchone(
        "SELECT * FROM osrm_cache WHERE site_id=? AND district_center_id=?",
        (site_id, district_id),
    )


def save_osrm(site_id: int, district_id: int, distance_km: float, duration_min: float, geometry, source: str) -> None:
    execute(
        """
        INSERT OR REPLACE INTO osrm_cache
            (site_id, district_center_id, distance_km, duration_min, geometry_json, source, fetched_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            site_id,
            district_id,
            distance_km,
            duration_min,
            json.dumps(geometry) if geometry is not None else None,
            source,
            iso(now_tr()),
        ),
    )


# ----- faults -----

def add_fault(site_id: int, mains_at: str, down_at: str, restored_at: Optional[str], backup_min: Optional[float], outage_min: Optional[float], comment: str) -> int:
    return execute(
        """
        INSERT INTO fault_events (site_id, mains_at, down_at, restored_at, backup_minutes, outage_minutes, comment, created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (site_id, mains_at, down_at, restored_at, backup_min, outage_min, comment, iso(now_tr())),
    )


def list_faults(site_id: Optional[int] = None) -> list[dict[str, Any]]:
    if site_id:
        return fetchall(
            """
            SELECT f.*, s.name AS site_name, s.il, s.ilce
            FROM fault_events f JOIN sites s ON s.id=f.site_id
            WHERE f.site_id=? ORDER BY f.mains_at
            """,
            (site_id,),
        )
    return fetchall(
        """
        SELECT f.*, s.name AS site_name, s.il, s.ilce
        FROM fault_events f JOIN sites s ON s.id=f.site_id
        ORDER BY f.mains_at DESC
        """
    )


def delete_fault(fault_id: int) -> None:
    execute("DELETE FROM fault_events WHERE id=?", (fault_id,))


def save_sync_run(added: list[str], removed: list[str], updated: list[str], geocoded: int) -> int:
    return execute(
        "INSERT INTO sync_runs (created_at, added_json, removed_json, updated_json, geocoded_count) VALUES (?,?,?,?,?)",
        (
            iso(now_tr()),
            json.dumps(added, ensure_ascii=False),
            json.dumps(removed, ensure_ascii=False),
            json.dumps(updated, ensure_ascii=False),
            geocoded,
        ),
    )


def last_sync() -> Optional[dict[str, Any]]:
    return fetchone("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1")
