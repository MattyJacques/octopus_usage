"""SQLite storage for readings, tariff rates, and app metadata.

All timestamps are stored as UTC ISO-8601 strings with a +00:00 offset
(see to_utc_iso), so lexicographic comparison equals chronological order.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FUELS = ("electricity", "gas")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
LONDON = ZoneInfo("Europe/London")

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
  fuel TEXT NOT NULL,
  interval_start TEXT NOT NULL,
  interval_end TEXT NOT NULL,
  consumption REAL NOT NULL,
  consumption_kwh REAL NOT NULL,
  PRIMARY KEY (fuel, interval_start)
);
CREATE TABLE IF NOT EXISTS rates (
  fuel TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  unit_rate_inc_vat REAL NOT NULL,
  PRIMARY KEY (fuel, valid_from)
);
CREATE TABLE IF NOT EXISTS standing_charges (
  fuel TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  charge_inc_vat REAL NOT NULL,
  PRIMARY KEY (fuel, valid_from)
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def to_utc_iso(ts: str) -> str:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).isoformat()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_readings(conn, fuel, rows) -> int:
    with conn:
        conn.executemany(
            "INSERT INTO readings"
            " (fuel, interval_start, interval_end, consumption, consumption_kwh)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(fuel, interval_start) DO UPDATE SET"
            " interval_end=excluded.interval_end,"
            " consumption=excluded.consumption,"
            " consumption_kwh=excluded.consumption_kwh",
            [
                (fuel, to_utc_iso(r["interval_start"]), to_utc_iso(r["interval_end"]),
                 r["consumption"], r["consumption_kwh"])
                for r in rows
            ],
        )
    return len(rows)


def readings(conn, fuel, start=None):
    if start:
        cur = conn.execute(
            "SELECT * FROM readings WHERE fuel=? AND interval_start>=?"
            " ORDER BY interval_start",
            (fuel, start),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM readings WHERE fuel=? ORDER BY interval_start", (fuel,)
        )
    return cur.fetchall()


def latest_interval_start(conn, fuel):
    row = conn.execute(
        "SELECT MAX(interval_start) AS m FROM readings WHERE fuel=?", (fuel,)
    ).fetchone()
    return row["m"]


def earliest_interval_start(conn, fuel):
    row = conn.execute(
        "SELECT MIN(interval_start) AS m FROM readings WHERE fuel=?", (fuel,)
    ).fetchone()
    return row["m"]


def daily_totals(conn, fuel):
    """Group half-hourly readings by Europe/London calendar date.

    A day is complete with >= 46 intervals (DST days have 46 or 50)."""
    days = {}
    for r in readings(conn, fuel):
        d = datetime.fromisoformat(r["interval_start"]).astimezone(LONDON).date()
        day = days.setdefault(d, {"date": d, "kwh": 0.0, "intervals": 0})
        day["kwh"] += r["consumption_kwh"]
        day["intervals"] += 1
    out = []
    for d in sorted(days):
        day = days[d]
        day["complete"] = day["intervals"] >= 46
        out.append(day)
    return out


def hourly_profile(conn, fuel, weeks=12):
    """Mean kWh by (weekday, London hour) over the trailing `weeks` ending at the newest reading."""
    latest = latest_interval_start(conn, fuel)
    if latest is None:
        return []
    end = datetime.fromisoformat(latest).astimezone(LONDON).date()
    start = end - timedelta(days=weeks * 7 - 1)
    sums = {}
    dates_by_wd = {wd: set() for wd in range(7)}
    for r in readings(conn, fuel):
        dt = datetime.fromisoformat(r["interval_start"]).astimezone(LONDON)
        if not start <= dt.date() <= end:
            continue
        wd = dt.weekday()
        sums[(wd, dt.hour)] = sums.get((wd, dt.hour), 0.0) + r["consumption_kwh"]
        dates_by_wd[wd].add(dt.date())
    return [
        {"day": DAY_NAMES[wd],
         "cells": [sums.get((wd, h), 0.0) / max(1, len(dates_by_wd[wd])) for h in range(24)]}
        for wd in range(7)
    ]


def _upsert_tariff_rows(conn, table, value_column, fuel, tariff_code, rows) -> int:
    with conn:
        conn.executemany(
            f"INSERT INTO {table} (fuel, tariff_code, valid_from, valid_to, {value_column})"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(fuel, valid_from) DO UPDATE SET"
            " tariff_code=excluded.tariff_code, valid_to=excluded.valid_to,"
            f" {value_column}=excluded.{value_column}",
            [
                (fuel, tariff_code, to_utc_iso(r["valid_from"]),
                 to_utc_iso(r["valid_to"]) if r.get("valid_to") else None,
                 r["value_inc_vat"])
                for r in rows
            ],
        )
    return len(rows)


def upsert_rates(conn, fuel, tariff_code, rows) -> int:
    return _upsert_tariff_rows(conn, "rates", "unit_rate_inc_vat", fuel, tariff_code, rows)


def upsert_standing_charges(conn, fuel, tariff_code, rows) -> int:
    return _upsert_tariff_rows(conn, "standing_charges", "charge_inc_vat", fuel, tariff_code, rows)


def rates_for(conn, fuel):
    return conn.execute(
        "SELECT * FROM rates WHERE fuel=? ORDER BY valid_from", (fuel,)
    ).fetchall()


def standing_charges_for(conn, fuel):
    return conn.execute(
        "SELECT * FROM standing_charges WHERE fuel=? ORDER BY valid_from", (fuel,)
    ).fetchall()


def meta_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn, key, value):
    with conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
