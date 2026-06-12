"""Weather for voice commands — Open-Meteo (free, no API key).

geocode_place() resolves a spoken place name (Czech names work) and
fetch_daily()/fetch_hourly() return compact forecast rows. Default location
comes from the WEATHER_DEFAULT_LOCATION env var (fallback: Oxford, the
company's home area).
"""
from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_PLACE = os.environ.get("WEATHER_DEFAULT_LOCATION", "Oxford")

# WMO weather codes -> short Czech description.
_CODES = {
    0: "jasno", 1: "skoro jasno", 2: "polojasno", 3: "zataženo",
    45: "mlha", 48: "námraza a mlha",
    51: "mrholení", 53: "mrholení", 55: "silné mrholení",
    56: "mrznoucí mrholení", 57: "mrznoucí mrholení",
    61: "slabý déšť", 63: "déšť", 65: "silný déšť",
    66: "mrznoucí déšť", 67: "mrznoucí déšť",
    71: "slabé sněžení", 73: "sněžení", 75: "silné sněžení", 77: "sněhové zrna",
    80: "přeháňky", 81: "přeháňky", 82: "silné přeháňky",
    85: "sněhové přeháňky", 86: "sněhové přeháňky",
    95: "bouřky", 96: "bouřky s kroupami", 99: "bouřky s kroupami",
}


def describe_code(code: int | None) -> str:
    return _CODES.get(int(code) if code is not None else -1, "proměnlivo")


def geocode_place(name: str) -> dict[str, Any] | None:
    """Return {name, latitude, longitude, country} or None."""
    try:
        r = httpx.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": name, "count": 1, "language": "cs"},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            return None
        hit = results[0]
        return {
            "name": hit.get("name") or name,
            "latitude": hit["latitude"],
            "longitude": hit["longitude"],
            "country": hit.get("country") or "",
        }
    except Exception:  # noqa: BLE001 — weather must never crash voice
        return None


def fetch_daily(lat: float, lon: float, days: int = 7) -> list[dict[str, Any]]:
    """Daily rows: {date, tmin, tmax, code, precip_prob, wind}."""
    r = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat, "longitude": lon, "timezone": "auto",
            "forecast_days": max(1, min(days, 14)),
            "daily": "weather_code,temperature_2m_min,temperature_2m_max,"
                     "precipitation_probability_max,wind_speed_10m_max",
        },
        timeout=10,
    )
    r.raise_for_status()
    d = r.json().get("daily") or {}
    rows = []
    for i, day in enumerate(d.get("time") or []):
        rows.append({
            "date": day,
            "tmin": round(d["temperature_2m_min"][i]),
            "tmax": round(d["temperature_2m_max"][i]),
            "code": d["weather_code"][i],
            "precip_prob": d.get("precipitation_probability_max", [None] * 99)[i],
            "wind": round(d.get("wind_speed_10m_max", [0] * 99)[i] or 0),
        })
    return rows


def fetch_hourly(lat: float, lon: float, hours: int = 12) -> list[dict[str, Any]]:
    """Hourly rows starting now: {time, temp, code, precip_prob}."""
    r = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat, "longitude": lon, "timezone": "auto",
            "forecast_days": 2, "forecast_hours": max(1, min(hours, 48)),
            "hourly": "temperature_2m,weather_code,precipitation_probability",
        },
        timeout=10,
    )
    r.raise_for_status()
    h = r.json().get("hourly") or {}
    rows = []
    for i, t in enumerate(h.get("time") or []):
        rows.append({
            "time": t,
            "temp": round(h["temperature_2m"][i]),
            "code": h["weather_code"][i],
            "precip_prob": h.get("precipitation_probability", [None] * 99)[i],
        })
    return rows
