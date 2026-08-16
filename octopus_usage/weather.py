"""Local temperature history from open-meteo, geocoded via postcodes.io.

Failures (no postcode, geocode or weather API errors) surface as None so the
dashboard degrades to simply not showing weather.
"""
import httpx

from octopus_usage import db

GEOCODE_URL = "https://api.postcodes.io/postcodes/"


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
