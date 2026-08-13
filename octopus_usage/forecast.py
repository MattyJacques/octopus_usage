"""Statistical daily-usage forecast: seasonal baseline x day-of-week factor.

Works on daily-totals dicts: {"date": date, "kwh": float, "complete": bool}.
Incomplete days (missing intervals) are never used for training.
"""
from datetime import date, timedelta
from statistics import mean, pstdev


def day_of_week_factors(daily):
    """Mean usage per weekday relative to the overall mean. Defaults to 1.0."""
    complete = [d for d in daily if d["complete"]]
    factors = {i: 1.0 for i in range(7)}
    if not complete:
        return factors
    overall = mean(d["kwh"] for d in complete)
    if overall <= 0:
        return factors
    for wd in range(7):
        vals = [d["kwh"] for d in complete if d["date"].weekday() == wd]
        if vals:
            factors[wd] = mean(vals) / overall
    return factors


def seasonal_baseline(daily, target):
    """Mean daily kWh within +/-14 days of the same calendar date in previous years."""
    vals = []
    for d in daily:
        if not d["complete"]:
            continue
        for years_back in (1, 2):
            try:
                anchor = target.replace(year=target.year - years_back)
            except ValueError:  # 29 Feb
                anchor = target.replace(year=target.year - years_back, day=28)
            if abs((d["date"] - anchor).days) <= 14:
                vals.append(d["kwh"])
                break
    return mean(vals) if vals else None


def recent_weighted_mean(daily, asof):
    """Mean of the last 28 complete days before asof, linearly weighted to recent."""
    window = sorted(
        (d for d in daily
         if d["complete"] and asof - timedelta(days=28) <= d["date"] < asof),
        key=lambda d: d["date"],
    )
    if not window:
        return None
    weights = range(1, len(window) + 1)
    return sum(w * d["kwh"] for w, d in zip(weights, window)) / sum(weights)


def _predict(daily, factors, target, asof, history_days):
    base = seasonal_baseline(daily, target) if history_days >= 365 else None
    if base is None:
        base = recent_weighted_mean(daily, asof)
    if base is None:
        return None
    return base * factors[target.weekday()]


def make_forecast(daily, days=30, start=None):
    """Forecast daily kWh for `days` days from `start` (default: after last data)."""
    if not daily:
        return []
    if start is None:
        start = daily[-1]["date"] + timedelta(days=1)
    factors = day_of_week_factors(daily)
    history_days = (daily[-1]["date"] - daily[0]["date"]).days + 1

    residuals = []
    for d in daily:
        if d["complete"] and start - timedelta(days=28) <= d["date"] < start:
            pred = _predict(daily, factors, d["date"], start, history_days)
            if pred is not None:
                residuals.append(d["kwh"] - pred)
    std = pstdev(residuals) if len(residuals) >= 2 else 0.0

    out = []
    for i in range(days):
        target = start + timedelta(days=i)
        kwh = _predict(daily, factors, target, start, history_days)
        if kwh is None:
            return []
        out.append({
            "date": target,
            "kwh": kwh,
            "lower": max(0.0, kwh - std),
            "upper": kwh + std,
        })
    return out
