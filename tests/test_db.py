from datetime import date, datetime, timedelta

import pytest

from octopus_usage import db


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


def test_to_utc_iso_normalises_offsets():
    assert db.to_utc_iso("2026-06-01T01:30:00+01:00") == "2026-06-01T00:30:00+00:00"
    assert db.to_utc_iso("2026-06-01T00:30:00Z") == "2026-06-01T00:30:00+00:00"


def test_upsert_readings_is_idempotent(conn):
    rows = rows_for_day("2026-08-01")
    db.upsert_readings(conn, "electricity", rows)
    db.upsert_readings(conn, "electricity", rows)
    assert len(db.readings(conn, "electricity")) == 48


def test_latest_and_earliest_interval_start(conn):
    db.upsert_readings(conn, "electricity", rows_for_day("2026-08-01"))
    db.upsert_readings(conn, "electricity", rows_for_day("2026-08-02"))
    assert db.earliest_interval_start(conn, "electricity") == "2026-08-01T00:00:00+00:00"
    assert db.latest_interval_start(conn, "electricity") == "2026-08-02T23:30:00+00:00"
    assert db.latest_interval_start(conn, "gas") is None


def test_daily_totals_groups_by_london_date_and_flags_completeness(conn):
    # August is BST (UTC+1): the 23:00Z and 23:30Z intervals of Aug 1 belong to
    # Aug 2 in London. So Aug 1 keeps 46 intervals (complete, kwh = 46 * 0.5)
    # and Aug 2 gets those 2 plus the 4 seeded below = 6 (incomplete).
    db.upsert_readings(conn, "electricity", rows_for_day("2026-08-01", n=48))
    db.upsert_readings(conn, "electricity", rows_for_day("2026-08-02", n=4))
    totals = db.daily_totals(conn, "electricity")
    assert [t["date"] for t in totals] == [date(2026, 8, 1), date(2026, 8, 2)]
    assert totals[0]["intervals"] == 46 and totals[0]["complete"]
    assert totals[0]["kwh"] == pytest.approx(23.0)
    assert totals[1]["intervals"] == 6 and not totals[1]["complete"]


def test_upsert_rates_maps_value_key(conn):
    db.upsert_rates(
        conn,
        "electricity",
        "E-1R-VAR-22-11-01-C",
        [{"valid_from": "2026-01-01T00:00:00Z", "valid_to": None, "value_inc_vat": 28.0}],
    )
    rows = db.rates_for(conn, "electricity")
    assert rows[0]["unit_rate_inc_vat"] == 28.0
    assert rows[0]["valid_to"] is None
    assert rows[0]["valid_from"] == "2026-01-01T00:00:00+00:00"


def test_upsert_standing_charges(conn):
    db.upsert_standing_charges(
        conn,
        "gas",
        "G-1R-VAR-22-11-01-C",
        [{"valid_from": "2026-01-01T00:00:00Z", "valid_to": None, "value_inc_vat": 29.6}],
    )
    assert db.standing_charges_for(conn, "gas")[0]["charge_inc_vat"] == 29.6


def test_meta_roundtrip(conn):
    assert db.meta_get(conn, "x") is None
    assert db.meta_get(conn, "x", "fallback") == "fallback"
    db.meta_set(conn, "x", "1")
    db.meta_set(conn, "x", "2")
    assert db.meta_get(conn, "x") == "2"


def test_hourly_profile_mean_kwh_by_weekday_hour(conn):
    for i in range(14):  # 2026-01-01..14: winter, so London == UTC
        db.upsert_readings(conn, "electricity", rows_for_day(f"2026-01-{i + 1:02d}"))
    profile = db.hourly_profile(conn, "electricity")
    assert [r["day"] for r in profile] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for row in profile:
        assert len(row["cells"]) == 24
        for cell in row["cells"]:
            assert cell == pytest.approx(1.0)  # two 0.5 kWh half-hours per hour


def test_hourly_profile_empty_without_readings(conn):
    assert db.hourly_profile(conn, "gas") == []
