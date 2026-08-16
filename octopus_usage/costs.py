"""Tariff-rate matching and cost estimation. Money is pence, inc. VAT."""
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime

from octopus_usage import db

M3_TO_KWH = 1.02264 * 39.5 / 3.6  # volume correction x calorific value / MJ-per-kWh


def m3_to_kwh(volume):
    return volume * M3_TO_KWH


def gas_looks_like_m3(rows):
    """SMETS2 gas meters report m3 (a few units/day); SMETS1 report kWh (tens/day).

    rows are raw API reading dicts. Mean daily total below 15 -> treat as m3."""
    per_day = defaultdict(float)
    for r in rows:
        per_day[r["interval_start"][:10]] += r["consumption"]
    if not per_day:
        return False
    return (sum(per_day.values()) / len(per_day)) < 15.0


def _lookup(rows, starts, column, ts):
    """Value of the row whose [valid_from, valid_to) covers UTC-ISO ts, else None."""
    i = bisect_right(starts, ts) - 1
    if i < 0:
        return None
    row = rows[i]
    if row["valid_to"] is not None and ts >= row["valid_to"]:
        return None
    return row[column]


def daily_costs(conn, fuel):
    """Daily kWh and estimated cost. cost_pence is None where tariff data has gaps."""
    rate_rows = db.rates_for(conn, fuel)
    rate_starts = [r["valid_from"] for r in rate_rows]
    sc_rows = db.standing_charges_for(conn, fuel)
    sc_starts = [r["valid_from"] for r in sc_rows]
    days = {}
    for r in db.readings(conn, fuel):
        d = datetime.fromisoformat(r["interval_start"]).astimezone(db.LONDON).date()
        day = days.setdefault(d, {"kwh": 0.0, "units": 0.0, "energy": 0.0, "priced": True, "intervals": 0})
        day["kwh"] += r["consumption_kwh"]
        day["units"] += r["consumption"]
        day["intervals"] += 1
        rate = _lookup(rate_rows, rate_starts, "unit_rate_inc_vat", r["interval_start"])
        if rate is None:
            day["priced"] = False
        else:
            day["energy"] += r["consumption_kwh"] * rate
    out = []
    for d in sorted(days):
        day = days[d]
        noon = f"{d.isoformat()}T12:00:00+00:00"  # midday sidesteps midnight boundaries
        sc = _lookup(sc_rows, sc_starts, "charge_inc_vat", noon)
        priced = day["priced"] and sc is not None
        out.append({
            "date": d,
            "kwh": day["kwh"],
            "units": day["units"],
            "cost_pence": (day["energy"] + sc) if priced else None,
            "complete": day["intervals"] >= 46,
        })
    return out


def current_unit_rate(conn, fuel):
    """Mean rate matched to the last 7 days of readings; falls back to the newest rate."""
    rate_rows = db.rates_for(conn, fuel)
    if not rate_rows:
        return None
    rate_starts = [r["valid_from"] for r in rate_rows]
    recent = db.readings(conn, fuel)[-336:]  # 48 half-hours x 7 days
    matched = [
        _lookup(rate_rows, rate_starts, "unit_rate_inc_vat", r["interval_start"])
        for r in recent
    ]
    matched = [m for m in matched if m is not None]
    if matched:
        return sum(matched) / len(matched)
    return rate_rows[-1]["unit_rate_inc_vat"]


def current_standing_charge(conn, fuel):
    sc_rows = db.standing_charges_for(conn, fuel)
    return sc_rows[-1]["charge_inc_vat"] if sc_rows else None
