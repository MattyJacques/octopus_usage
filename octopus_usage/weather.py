"""Local temperature history from open-meteo, geocoded via postcodes.io.

Failures (no postcode, geocode or weather API errors) surface as None so the
dashboard degrades to simply not showing weather.
"""
from datetime import date, timedelta

import httpx

from octopus_usage import db

GEOCODE_URL = "https://api.postcodes.io/postcodes/"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# The archive API trails real time by a few days; fetch newer dates from the
# forecast API (whose past_days window covers them) instead.
ARCHIVE_LAG_DAYS = 7


def coords(conn, client):
    """(lat, lon) for the account's postcode; geocoded once and cached in meta."""
    lat, lon = db.meta_get(conn, "weather_lat"), db.meta_get(conn, "weather_lon")
    if lat is not None and lon is not None:
        return float(lat), float(lon)
    postcode = db.meta_get(conn, "postcode")
    if postcode is None:
        return None
    try:
        resp = client.get(GEOCODE_URL + postcode.replace(" ", ""))
        resp.raise_for_status()
        result = resp.json()["result"]
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return None
    db.meta_set(conn, "weather_lat", str(result["latitude"]))
    db.meta_set(conn, "weather_lon", str(result["longitude"]))
    return result["latitude"], result["longitude"]


def _fetch_daily(client, url, lat, lon, start, end):
    resp = client.get(url, params={
        "latitude": lat, "longitude": lon,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "daily": "temperature_2m_min,temperature_2m_max,temperature_2m_mean",
        "timezone": "Europe/London",
    })
    resp.raise_for_status()
    d = resp.json()["daily"]
    rows = []
    for i, day in enumerate(d["time"]):
        tmin, tmax, tmean = (d["temperature_2m_min"][i], d["temperature_2m_max"][i],
                             d["temperature_2m_mean"][i])
        if None in (tmin, tmax, tmean):
            continue  # not yet available upstream; retried on a later request
        rows.append({"date": day, "tmin": tmin, "tmax": tmax, "tmean": tmean})
    return rows


def daily_temps(conn, client, start, end):
    """Daily min/max/mean temperatures, cached in weather_daily.

    Only missing dates are fetched. Clamped to complete days (< today);
    None when location is unknown or a fetch fails."""
    loc = coords(conn, client)
    if loc is None:
        return None
    lat, lon = loc
    end = min(end, date.today() - timedelta(days=1))
    if end < start:
        return []
    have = {r["date"] for r in db.weather_daily_range(conn, start.isoformat(), end.isoformat())}
    missing = [d for i in range((end - start).days + 1)
               if (d := start + timedelta(days=i)).isoformat() not in have]
    if missing:
        cutoff = date.today() - timedelta(days=ARCHIVE_LAG_DAYS)
        try:
            for url, dates in ((ARCHIVE_URL, [d for d in missing if d < cutoff]),
                               (FORECAST_URL, [d for d in missing if d >= cutoff])):
                if dates:
                    db.upsert_weather_daily(
                        conn, _fetch_daily(client, url, lat, lon, dates[0], dates[-1]))
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            return None
    return [dict(r) for r in db.weather_daily_range(conn, start.isoformat(), end.isoformat())]


def hourly_temps(conn, client, day):
    """24 hourly temperatures for one London date; None when unavailable.

    Not cached: one small request per view, and only the yesterday preset
    uses it. The forecast API's past window covers ~3 months; older dates
    come from the archive API."""
    loc = coords(conn, client)
    if loc is None:
        return None
    lat, lon = loc
    url = FORECAST_URL if day >= date.today() - timedelta(days=90) else ARCHIVE_URL
    try:
        resp = client.get(url, params={
            "latitude": lat, "longitude": lon,
            "start_date": day.isoformat(), "end_date": day.isoformat(),
            "hourly": "temperature_2m",
            "timezone": "Europe/London",
        })
        resp.raise_for_status()
        return resp.json()["hourly"]["temperature_2m"]
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return None
