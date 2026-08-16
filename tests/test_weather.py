from datetime import date, timedelta

import httpx
import pytest

from octopus_usage import db, weather
from tests.fixtures import weather_handler


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "t.db"))


def make_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_coords_geocodes_postcode_once(conn):
    db.meta_set(conn, "postcode", "SW1A 1AA")
    calls = []
    client = make_client(weather_handler(calls))
    assert weather.coords(conn, client) == (51.501, -0.142)
    assert weather.coords(conn, client) == (51.501, -0.142)
    assert len(calls) == 1  # second call served from meta


def test_coords_none_without_postcode(conn):
    client = make_client(weather_handler())
    assert weather.coords(conn, client) is None


def test_coords_none_on_geocode_failure_and_not_cached(conn):
    db.meta_set(conn, "postcode", "SW1A 1AA")
    assert weather.coords(conn, make_client(weather_handler(fail_geocode=True))) is None
    # failure is not cached: a later working call succeeds
    assert weather.coords(conn, make_client(weather_handler())) == (51.501, -0.142)


def seed_location(conn):
    db.meta_set(conn, "weather_lat", "51.501")
    db.meta_set(conn, "weather_lon", "-0.142")


def test_daily_temps_fetches_and_caches(conn):
    seed_location(conn)
    calls = []
    client = make_client(weather_handler(calls))
    start, end = date(2026, 6, 1), date(2026, 6, 5)
    rows = weather.daily_temps(conn, client, start, end)
    assert [r["date"] for r in rows] == [(start + timedelta(days=i)).isoformat() for i in range(5)]
    assert rows[0]["tmin"] == 10.0
    assert rows[0]["tmax"] == 20.0
    assert rows[0]["tmean"] == 15.0
    n = len(calls)
    assert weather.daily_temps(conn, client, start, end) == rows
    assert len(calls) == n  # cache hit, no further HTTP


def test_daily_temps_fetches_only_missing_dates(conn):
    seed_location(conn)
    calls = []
    client = make_client(weather_handler(calls))
    weather.daily_temps(conn, client, date(2026, 6, 1), date(2026, 6, 5))
    weather.daily_temps(conn, client, date(2026, 6, 1), date(2026, 6, 10))
    assert "start_date=2026-06-06" in calls[-1]


def test_daily_temps_uses_forecast_api_for_recent_days(conn):
    seed_location(conn)
    calls = []
    client = make_client(weather_handler(calls))
    rows = weather.daily_temps(conn, client,
                               date.today() - timedelta(days=3),
                               date.today() - timedelta(days=1))
    assert len(rows) == 3
    assert all("api.open-meteo.com" in c for c in calls)


def test_daily_temps_none_without_location(conn):
    client = make_client(weather_handler())
    assert weather.daily_temps(conn, client, date(2026, 6, 1), date(2026, 6, 2)) is None


def test_hourly_temps_returns_24_values(conn):
    seed_location(conn)
    client = make_client(weather_handler())
    temps = weather.hourly_temps(conn, client, date.today() - timedelta(days=1))
    assert len(temps) == 24
    assert temps[0] == 12.0


def test_hourly_temps_picks_api_by_age(conn):
    seed_location(conn)
    calls = []
    client = make_client(weather_handler(calls))
    weather.hourly_temps(conn, client, date.today() - timedelta(days=1))
    weather.hourly_temps(conn, client, date(2020, 1, 1))
    assert "api.open-meteo.com" in calls[0]
    assert "archive-api.open-meteo.com" in calls[1]


def test_hourly_temps_none_without_location(conn):
    assert weather.hourly_temps(conn, make_client(weather_handler()), date(2026, 6, 1)) is None
