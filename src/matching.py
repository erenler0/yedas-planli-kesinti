"""Shapely Point-in-Polygon eşleştirme + adres yedek kontrolü."""

from __future__ import annotations

from typing import Any, Optional

from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.validation import make_valid

from src.config import NEARBY_KM
from src.utils import haversine_km, names_match, safe_float


def build_polygon(outage: dict[str, Any]) -> Optional[Polygon | MultiPolygon]:
    coords = outage.get("coords") or []
    pts: list[tuple[float, float]] = []
    try:
        for c in coords:
            lat = safe_float(c.get("latitude"))
            lon = safe_float(c.get("longitude"))
            if lat is None or lon is None:
                continue
            pts.append((lon, lat))
        if len(pts) >= 3:
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            if len(pts) >= 4:
                poly = Polygon(pts)
                return _repair(poly)
        geo = outage.get("geojson") or {}
        geom = geo.get("geometry") if isinstance(geo, dict) and "geometry" in geo else geo
        if isinstance(geom, dict) and geom.get("type") and geom.get("coordinates"):
            return _repair(shape(geom))
    except Exception:
        return None
    return None


def _repair(geom):
    try:
        if geom is None or geom.is_empty:
            return None
        if not geom.is_valid:
            geom = make_valid(geom)
        if geom.geom_type == "Polygon":
            return geom
        if geom.geom_type == "MultiPolygon":
            return geom
        if geom.geom_type == "GeometryCollection":
            polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty]
            if not polys:
                return None
            merged = polys[0]
            for g in polys[1:]:
                merged = merged.union(g)
            return merged if merged.geom_type in ("Polygon", "MultiPolygon") else None
        buffered = geom.buffer(0)
        if buffered.geom_type in ("Polygon", "MultiPolygon") and not buffered.is_empty:
            return buffered
    except Exception:
        return None
    return None


def site_point(site: dict[str, Any]) -> Optional[Point]:
    try:
        lat = safe_float(site.get("lat"))
        lon = safe_float(site.get("lon"))
        if lat is None or lon is None:
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return Point(float(lon), float(lat))
    except Exception:
        return None


def _covers(geom, point: Point) -> bool:
    try:
        if geom is None or point is None:
            return False
        if geom.intersects(point) or geom.covers(point):
            return True
        return geom.distance(point) <= 1e-12
    except Exception:
        return False


def address_fallback(outage: dict[str, Any], site: dict[str, Any]) -> bool:
    addresses = outage.get("address") or []
    if not isinstance(addresses, list) or not addresses:
        return False
    site_il = site.get("il")
    site_ilce = site.get("ilce")
    site_mah = site.get("mahalle")
    if not site_il and not site_ilce and not site_mah:
        return False
    for addr in addresses:
        if not isinstance(addr, dict):
            continue
        city = addr.get("city_name") or addr.get("city")
        district = addr.get("district_name") or addr.get("district")
        mah = addr.get("mah_name") or addr.get("neighborhood")
        il_ok = names_match(site_il, city) if site_il and city else False
        ilce_ok = names_match(site_ilce, district) if site_ilce and district else False
        mah_ok = names_match(site_mah, mah) if site_mah and mah else False
        if il_ok and (ilce_ok or mah_ok):
            return True
        if ilce_ok and mah_ok:
            return True
    return False


def match_sites(outages: list[dict[str, Any]], sites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Her kesinti için etkilenen / yakın sahaları döndürür."""
    prepared: list[dict[str, Any]] = []
    for outage in outages:
        geom = build_polygon(outage)
        item = dict(outage)
        item["polygon"] = geom
        prepared.append(item)

    results = []
    for outage in prepared:
        affected: list[dict[str, Any]] = []
        nearby: list[dict[str, Any]] = []
        geom = outage["polygon"]
        centroid = None
        try:
            centroid = geom.representative_point() if geom is not None and not geom.is_empty else None
        except Exception:
            centroid = None

        for site in sites:
            pt = site_point(site)
            if pt is None:
                continue
            match_type = None
            if geom is not None and _covers(geom, pt):
                match_type = "polygon"
            elif address_fallback(outage, site):
                match_type = "address"
            if match_type:
                rec = dict(site)
                rec["match_type"] = match_type
                affected.append(rec)
                continue
            if centroid is not None:
                try:
                    dist = haversine_km(site["lat"], site["lon"], centroid.y, centroid.x)
                    if dist <= NEARBY_KM:
                        rec = dict(site)
                        rec["nearby_km"] = round(dist, 2)
                        nearby.append(rec)
                except Exception:
                    pass

        nearby.sort(key=lambda r: r.get("nearby_km", 9999))
        item = dict(outage)
        item["affected"] = affected
        item["nearby"] = nearby[:80]
        results.append(item)
    return results
