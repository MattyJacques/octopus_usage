"""Yearly aggregates: calendar-month buckets and rolling/calendar-year totals.

Operates on daily-totals dicts ({"date", "kwh", "cost_pence", "complete"})
from costs.daily_costs and forecast points ({"date", "kwh", ...}) from
forecast.make_forecast. Money is pence, inc. VAT; forecast costs use the
current unit rate and standing charge passed in by the caller.
"""
from datetime import date, timedelta


def _month_key(d):
    return f"{d.year:04d}-{d.month:02d}"


def _months_ago(today, n):
    """First day of the month n months before today's month."""
    idx = today.year * 12 + (today.month - 1) - n
    return date(idx // 12, idx % 12 + 1, 1)


def _forecast_cost(kwh, n_days, rate, sc):
    if rate is None or sc is None:
        return None
    return kwh * rate + n_days * sc


def monthly_buckets(daily, points, today, rate, sc):
    """Monthly entries: last 12 months + current month of actuals, then forecast.

    The current month may appear twice: actual-to-date, then forecast remainder.
    """
    start = _months_ago(today, 12)
    out = []

    actual = {}
    for d in daily:
        if d["date"] < start:
            continue
        b = actual.setdefault(_month_key(d["date"]), {"kwh": 0.0, "costs": []})
        b["kwh"] += d["kwh"]
        b["costs"].append(d["cost_pence"])
    for key in sorted(actual):
        b = actual[key]
        cost = sum(b["costs"]) if None not in b["costs"] else None
        out.append({"month": key, "kwh": b["kwh"], "cost_pence": cost, "forecast": False})

    fc = {}
    for p in points:
        b = fc.setdefault(_month_key(p["date"]), {"kwh": 0.0, "days": 0})
        b["kwh"] += p["kwh"]
        b["days"] += 1
    for key in sorted(fc):
        b = fc[key]
        out.append({
            "month": key,
            "kwh": b["kwh"],
            "cost_pence": _forecast_cost(b["kwh"], b["days"], rate, sc),
            "forecast": True,
        })

    out.sort(key=lambda e: (e["month"], e["forecast"]))
    return out
