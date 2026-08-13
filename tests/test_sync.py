from datetime import datetime, timezone

import httpx
import pytest

from octopus_usage import costs, db, sync
from octopus_usage.octopus_client import OctopusClient
from tests.fixtures import ACCOUNT, make_handler


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "test.db"))


def make_client(handler):
    return OctopusClient("sk_test", transport=httpx.MockTransport(handler), backoff=0)


def test_product_code_from_tariff():
    assert sync.product_code_from_tariff("E-1R-VAR-22-11-01-C") == "VAR-22-11-01"
    assert sync.product_code_from_tariff("E-1R-AGILE-24-10-01-A") == "AGILE-24-10-01"


def test_discover_meters():
    meters = sync.discover_meters(ACCOUNT)
    assert meters["electricity"]["mpxn"] == "1200000000000"
    assert meters["electricity"]["serials"] == ["ELEC001"]
    assert meters["gas"]["mpxn"] == "3000000000"
    assert meters["gas"]["agreements"][0]["tariff_code"] == "G-1R-VAR-22-11-01-C"


def test_discover_meters_skips_export_points():
    account = {
        "properties": [{
            "electricity_meter_points": [
                {"mpan": "999", "is_export": True, "meters": [{"serial_number": "X"}], "agreements": []},
            ],
            "gas_meter_points": [],
        }]
    }
    assert sync.discover_meters(account) == {}


def test_full_sync_stores_readings_rates_and_converts_gas(conn):
    client = make_client(make_handler())
    result = sync.full_sync(conn, client, "A-12345678")
    assert result["fuels"] == {"electricity": 96, "gas": 96}
    # gas: 0.1 x 48 = 4.8/day -> heuristic says m3 -> converted
    gas = db.readings(conn, "gas")
    assert gas[0]["consumption_kwh"] == pytest.approx(costs.m3_to_kwh(0.1))
    assert db.meta_get(conn, "gas_unit") == "m3"
    elec = db.readings(conn, "electricity")
    assert elec[0]["consumption_kwh"] == 1.0
    assert db.rates_for(conn, "electricity")[0]["unit_rate_inc_vat"] == 28.0
    assert db.standing_charges_for(conn, "gas")[0]["charge_inc_vat"] == 50.0
    assert db.meta_get(conn, "last_sync") is not None
    assert db.meta_get(conn, "electricity_serial") == "ELEC001"


def test_incremental_sync_requests_from_latest_stored(conn):
    client = make_client(make_handler())
    sync.full_sync(conn, client, "A-12345678")
    latest = db.latest_interval_start(conn, "electricity")

    seen = []

    def spy_handler(request):
        seen.append(request)
        return make_handler()(request)

    sync.full_sync(conn, make_client(spy_handler), "A-12345678")
    elec_requests = [r for r in seen if "electricity-meter-points" in r.url.path]
    assert elec_requests[0].url.params["period_from"] == latest


def test_backfill_window_on_first_run(conn):
    seen = []

    def spy_handler(request):
        seen.append(request)
        return make_handler()(request)

    now = datetime(2026, 8, 13, tzinfo=timezone.utc)
    meters = sync.discover_meters(ACCOUNT)
    sync.sync_fuel_readings(conn, make_client(spy_handler), "electricity", meters["electricity"], now=now)
    assert seen[0].url.params["period_from"] == "2024-08-13T00:00:00+00:00"


def test_gas_unit_decision_is_sticky(conn):
    # First sync sees kWh-scale values; a later small batch must not flip the unit.
    client = make_client(make_handler(gas_values=(1.5,) * 96))
    meters = sync.discover_meters(ACCOUNT)
    sync.sync_fuel_readings(conn, client, "gas", meters["gas"])
    assert db.meta_get(conn, "gas_unit") == "kwh"
    sync.sync_fuel_readings(conn, make_client(make_handler(gas_values=(0.1,) * 4)), "gas", meters["gas"])
    assert db.meta_get(conn, "gas_unit") == "kwh"
