from datetime import date, datetime, timedelta

import pytest

from octopus_usage import costs, db


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "test.db"))


def rows_for_day(day_iso, n=48, consumption=0.5):
    t = datetime.fromisoformat(day_iso + "T00:00:00+00:00")
    return [
        {
            "interval_start": (t + timedelta(minutes=30 * i)).isoformat(),
            "interval_end": (t + timedelta(minutes=30 * (i + 1))).isoformat(),
            "consumption": consumption,
            "consumption_kwh": consumption,
        }
        for i in range(n)
    ]


def flat_rate(conn, fuel, pence, sc=50.0, valid_from="2026-01-01T00:00:00Z"):
    db.upsert_rates(conn, fuel, "T", [{"valid_from": valid_from, "valid_to": None, "value_inc_vat": pence}])
    db.upsert_standing_charges(conn, fuel, "T", [{"valid_from": valid_from, "valid_to": None, "value_inc_vat": sc}])


def test_m3_to_kwh_factor():
    assert costs.m3_to_kwh(1.0) == pytest.approx(11.2206, abs=1e-3)


def test_gas_heuristic_m3_vs_kwh():
    m3_rows = [{"interval_start": f"2026-01-15T{h:02d}:00:00Z", "consumption": 0.1} for h in range(24)]
    kwh_rows = [{"interval_start": f"2026-01-15T{h:02d}:00:00Z", "consumption": 1.5} for h in range(24)]
    assert costs.gas_looks_like_m3(m3_rows) is True      # 2.4/day
    assert costs.gas_looks_like_m3(kwh_rows) is False    # 36/day
    assert costs.gas_looks_like_m3([]) is False


def test_daily_costs_flat_rate(conn):
    db.upsert_readings(conn, "electricity", rows_for_day("2026-01-15"))
    flat_rate(conn, "electricity", 28.0, sc=50.0)
    daily = costs.daily_costs(conn, "electricity")
    assert daily[0]["date"] == date(2026, 1, 15)
    assert daily[0]["kwh"] == pytest.approx(24.0)
    assert daily[0]["cost_pence"] == pytest.approx(24.0 * 28.0 + 50.0)
    assert daily[0]["complete"] is True


def test_daily_costs_time_of_use(conn):
    db.upsert_readings(conn, "electricity", rows_for_day("2026-01-15"))
    db.upsert_rates(conn, "electricity", "T", [
        {"valid_from": "2026-01-15T00:00:00Z", "valid_to": "2026-01-15T12:00:00Z", "value_inc_vat": 10.0},
        {"valid_from": "2026-01-15T12:00:00Z", "valid_to": None, "value_inc_vat": 20.0},
    ])
    db.upsert_standing_charges(conn, "electricity", "T",
                               [{"valid_from": "2026-01-01T00:00:00Z", "valid_to": None, "value_inc_vat": 50.0}])
    daily = costs.daily_costs(conn, "electricity")
    # 12 kWh before noon @10p + 12 kWh after @20p + 50p standing
    assert daily[0]["cost_pence"] == pytest.approx(12 * 10.0 + 12 * 20.0 + 50.0)


def test_daily_costs_none_when_rates_missing(conn):
    db.upsert_readings(conn, "electricity", rows_for_day("2025-12-01"))
    flat_rate(conn, "electricity", 28.0, valid_from="2026-01-01T00:00:00Z")
    daily = costs.daily_costs(conn, "electricity")
    assert daily[0]["kwh"] == pytest.approx(24.0)
    assert daily[0]["cost_pence"] is None


def test_current_unit_rate_averages_recent_readings(conn):
    # Old readings priced at 10p, the newest day at 30p -> the last-7-days
    # average over one seeded day is 30.
    db.upsert_readings(conn, "electricity", rows_for_day("2026-01-15"))
    db.upsert_rates(conn, "electricity", "T", [
        {"valid_from": "2026-01-01T00:00:00Z", "valid_to": "2026-01-15T00:00:00Z", "value_inc_vat": 10.0},
        {"valid_from": "2026-01-15T00:00:00Z", "valid_to": None, "value_inc_vat": 30.0},
    ])
    assert costs.current_unit_rate(conn, "electricity") == pytest.approx(30.0)


def test_current_unit_rate_falls_back_to_newest_rate(conn):
    flat_rate(conn, "electricity", 28.0)
    assert costs.current_unit_rate(conn, "electricity") == pytest.approx(28.0)
    assert costs.current_unit_rate(conn, "gas") is None


def test_current_standing_charge(conn):
    flat_rate(conn, "gas", 7.0, sc=29.6)
    assert costs.current_standing_charge(conn, "gas") == pytest.approx(29.6)
    assert costs.current_standing_charge(conn, "electricity") is None
