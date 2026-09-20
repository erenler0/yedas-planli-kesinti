"""İş kuralları: senkron, eşleştirme kalıcılığı, ilçe ataması, OSRM önbelleği."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

import streamlit as st

from src import db
from src.config import YEDAS_CACHE_TTL
from src.district_seed import DISTRICT_CENTERS
from src.excel_io import read_sites_excel
from src.geocoding import reverse_many
from src.matching import match_sites
from src.osrm_client import driving_route, nearest_district
from src.utils import from_iso, now_tr
from src.yedas_api import fetch_yedas_raw, normalize_outages


@st.cache_data(ttl=YEDAS_CACHE_TTL, show_spinner="YEDAŞ planlı kesintiler güncelleniyor…")
def load_yedas_cached() -> list[dict[str, Any]]:
    raw = fetch_yedas_raw()
    return normalize_outages(raw)


def persist_and_match(outages: list[dict[str, Any]], sites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matched = match_sites(outages, sites)
    for item in matched:
        db.upsert_outage(item)
        pairs = [(int(s["id"]), s.get("match_type") or "polygon") for s in item.get("affected") or []]
        db.replace_matches(item["yedas_id"], pairs)
    return matched


def filter_window(matched: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    start = now_tr().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    out = []
    for item in matched:
        s = item.get("start_dt") or from_iso(item.get("start_at"))
        e = item.get("end_dt") or from_iso(item.get("end_at"))
        if s is None and e is None:
            out.append(item)
            continue
        s = s or e
        e = e or s
        if s < end and e >= start:
            out.append(item)
    return out


def sync_sites_from_excel(uploaded, progress) -> dict[str, Any]:
    df = read_sites_excel(uploaded)
    incoming = df.to_dict("records")
    incoming_keys = [r["identity_key"] for r in incoming]
    existing_keys = db.list_identity_keys()
    incoming_set = set(incoming_keys)

    added_keys = [k for k in incoming_keys if k not in existing_keys]
    removed_names = db.delete_sites_not_in(incoming_keys)
    updated_names: list[str] = []
    added_names: list[str] = []
    geocode_jobs: list[tuple[int, float, float]] = []

    for rec in incoming:
        is_new = rec["identity_key"] not in existing_keys
        site_id = db.upsert_site(rec)
        if is_new:
            added_names.append(rec["name"])
            geocode_jobs.append((site_id, rec["lat"], rec["lon"]))
        elif rec["identity_key"] in existing_keys:
            updated_names.append(rec["name"])

    geocoded = 0
    if geocode_jobs:
        bar = progress.progress(0.0, text="Reverse geocoding (Nominatim)…")

        def cb(i, total, _key):
            bar.progress(i / total, text=f"Adres çözülüyor {i}/{total}")

        results = reverse_many(geocode_jobs, progress_cb=cb)
        for sid, addr in results.items():
            db.set_site_address(sid, addr.get("il") or "", addr.get("ilce") or "", addr.get("mahalle") or "")
            geocoded += 1
        bar.progress(1.0, text="Reverse geocoding tamam")

    assign_nearest_districts()
    db.save_sync_run(added_names, removed_names, sorted(set(updated_names) - set(added_names)), geocoded)
    return {
        "added": added_names,
        "removed": removed_names,
        "updated": sorted(set(updated_names) - set(added_names)),
        "geocoded": geocoded,
        "total": db.site_count(),
    }


def seed_districts() -> int:
    n = 0
    for d in DISTRICT_CENTERS:
        db.add_district(d["name"], d["province"], d["lat"], d["lon"])
        n += 1
    assign_nearest_districts()
    return n


def assign_nearest_districts() -> None:
    districts = db.list_districts()
    if not districts:
        return
    for site in db.list_sites():
        if site.get("assignment_override"):
            continue
        nearest = nearest_district(site, districts)
        if nearest and site.get("district_center_id") != nearest["id"]:
            db.set_site_district(int(site["id"]), int(nearest["id"]), override=False)


def enrich_sites_with_routes(sites: list[dict[str, Any]], compute_missing: bool = False, progress=None) -> list[dict[str, Any]]:
    districts = {d["id"]: d for d in db.list_districts()}
    missing = []
    enriched = []
    for s in sites:
        rec = dict(s)
        dc_id = s.get("district_center_id")
        dc = districts.get(dc_id) if dc_id else None
        rec["district_name"] = f"{dc['province']} / {dc['name']}" if dc else None
        rec["district_lat"] = dc["lat"] if dc else None
        rec["district_lon"] = dc["lon"] if dc else None
        cached = db.get_osrm(int(s["id"]), int(dc_id)) if dc_id else None
        if cached:
            rec["distance_km"] = cached.get("distance_km")
            rec["duration_min"] = cached.get("duration_min")
            rec["route_source"] = cached.get("source")
            rec["route_geometry"] = cached.get("geometry_json")
        elif dc and compute_missing:
            missing.append((s, dc))
        enriched.append(rec)

    total = len(missing)
    for i, (s, dc) in enumerate(missing, start=1):
        if progress:
            progress.progress(i / total, text=f"OSRM rota {i}/{total}: {s.get('name')}")
        result = driving_route(float(s["lat"]), float(s["lon"]), float(dc["lat"]), float(dc["lon"]), overview="false")
        db.save_osrm(
            int(s["id"]),
            int(dc["id"]),
            result["distance_km"],
            result["duration_min"],
            result.get("geometry"),
            result["source"],
        )
        for rec in enriched:
            if rec["id"] == s["id"]:
                rec["distance_km"] = result["distance_km"]
                rec["duration_min"] = result["duration_min"]
                rec["route_source"] = result["source"]
                break
    return enriched


def route_geometry_for(site: dict[str, Any]) -> Optional[dict]:
    dc_id = site.get("district_center_id")
    if not dc_id:
        return None
    cached = db.get_osrm(int(site["id"]), int(dc_id))
    dc = db.get_district(int(dc_id))
    if not dc:
        return None
    if cached and cached.get("geometry_json"):
        import json

        try:
            return json.loads(cached["geometry_json"])
        except Exception:
            pass
    result = driving_route(
        float(site["lat"]),
        float(site["lon"]),
        float(dc["lat"]),
        float(dc["lon"]),
        overview="full",
    )
    db.save_osrm(
        int(site["id"]),
        int(dc["id"]),
        result["distance_km"],
        result["duration_min"],
        result.get("geometry"),
        result["source"],
    )
    return result.get("geometry")
