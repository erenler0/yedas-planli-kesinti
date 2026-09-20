"""Nominatim reverse geocoding (yalnızca yeni sahalar)."""

from __future__ import annotations

import time
from typing import Optional

from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

from src.config import NOMINATIM_SLEEP_SEC, REQUEST_TIMEOUT, USER_AGENT


def _geocoder() -> Nominatim:
    return Nominatim(user_agent=USER_AGENT, timeout=REQUEST_TIMEOUT)


def reverse_one(lat: float, lon: float) -> dict[str, Optional[str]]:
    geolocator = _geocoder()
    reverse = RateLimiter(
        geolocator.reverse,
        min_delay_seconds=NOMINATIM_SLEEP_SEC,
        max_retries=3,
        swallow_exceptions=False,
    )
    location = reverse((lat, lon), language="tr", exactly_one=True, addressdetails=True)
    if location is None:
        return {"il": None, "ilce": None, "mahalle": None, "raw": None}
    addr = location.raw.get("address") or {}
    il = (
        addr.get("province")
        or addr.get("state")
        or addr.get("region")
    )
    ilce = (
        addr.get("town")
        or addr.get("county")
        or addr.get("city_district")
        or addr.get("municipality")
        or addr.get("city")
    )
    if ilce and il and str(ilce).strip().lower() == str(il).strip().lower():
        ilce = addr.get("county") or addr.get("town") or ilce
    mahalle = (
        addr.get("suburb")
        or addr.get("neighbourhood")
        or addr.get("neighborhood")
        or addr.get("village")
        or addr.get("quarter")
        or addr.get("hamlet")
    )
    return {
        "il": _clean_place(il),
        "ilce": _clean_place(ilce),
        "mahalle": _clean_place(mahalle),
        "raw": location.address,
    }


def reverse_many(points: list[tuple[int, float, float]], progress_cb=None) -> dict[int, dict]:
    """points: (site_id or temp index, lat, lon). Nominatim 1 req/sn civarı."""
    geolocator = _geocoder()
    out: dict[int, dict] = {}
    total = len(points)
    for i, (key, lat, lon) in enumerate(points, start=1):
        try:
            loc = geolocator.reverse(
                (lat, lon),
                language="tr",
                exactly_one=True,
                addressdetails=True,
            )
            addr = (loc.raw.get("address") if loc else {}) or {}
            il = addr.get("province") or addr.get("state") or addr.get("region")
            ilce = (
                addr.get("town")
                or addr.get("county")
                or addr.get("city_district")
                or addr.get("municipality")
                or addr.get("city")
            )
            mahalle = (
                addr.get("suburb")
                or addr.get("neighbourhood")
                or addr.get("neighborhood")
                or addr.get("village")
                or addr.get("quarter")
                or addr.get("hamlet")
            )
            out[key] = {
                "il": _clean_place(il),
                "ilce": _clean_place(ilce),
                "mahalle": _clean_place(mahalle),
                "raw": loc.address if loc else None,
            }
        except Exception as exc:
            out[key] = {"il": None, "ilce": None, "mahalle": None, "raw": f"hata: {exc}"}
        if progress_cb:
            progress_cb(i, total, key)
        time.sleep(NOMINATIM_SLEEP_SEC)
    return out


def _clean_place(value) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip()
    for suffix in (" İli", " ili", " Province"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text or None
