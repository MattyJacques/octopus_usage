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
