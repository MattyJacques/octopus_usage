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


def _window(daily, start, end):
    """kWh and None-propagating cost over daily rows with start <= date <= end."""
    sel = [d for d in daily if start <= d["date"] <= end]
    costs = [d["cost_pence"] for d in sel]
    return (
        sum(d["kwh"] for d in sel),
        sum(costs) if sel and None not in costs else None,
    )


def totals(daily, points, today, rate, sc):
    """Rolling-365 and calendar-year totals; None where data can't support them."""
    if daily and daily[0]["date"] <= today - timedelta(days=365):
        kwh, cost = _window(daily, today - timedelta(days=365), today - timedelta(days=1))
        last_365 = {"kwh": kwh, "cost_pence": cost}
    else:
        last_365 = {"kwh": None, "cost_pence": None}

    if points:
        fc_kwh = sum(p["kwh"] for p in points)
        next_365 = {"kwh": fc_kwh, "cost_pence": _forecast_cost(fc_kwh, len(points), rate, sc)}
    else:
        next_365 = {"kwh": None, "cost_pence": None}

    prev = today.year - 1
    if (daily and daily[0]["date"] <= date(prev, 1, 1)
            and daily[-1]["date"] >= date(prev, 12, 31)):
        kwh, cost = _window(daily, date(prev, 1, 1), date(prev, 12, 31))
        calendar_prev = {"year": prev, "kwh": kwh, "cost_pence": cost}
    else:
        calendar_prev = {"year": prev, "kwh": None, "cost_pence": None}

    kwh, cost = _window(daily, date(today.year, 1, 1), today)
    fc_sel = [p for p in points if p["date"] <= date(today.year, 12, 31)]
    fc_kwh = sum(p["kwh"] for p in fc_sel)
    fc_cost = _forecast_cost(fc_kwh, len(fc_sel), rate, sc)
    calendar_current = {
        "year": today.year,
        "kwh": kwh + fc_kwh,
        "cost_pence": cost + fc_cost if cost is not None and fc_cost is not None else None,
    }

    return {
        "last_365": last_365,
        "next_365": next_365,
        "calendar_prev": calendar_prev,
        "calendar_current": calendar_current,
    }
