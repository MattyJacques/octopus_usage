from datetime import date, timedelta

from octopus_usage import yearly


def make_daily(start, end, kwh=24.0, cost=288.0):
    """One complete daily-totals row per day, start..end inclusive."""
    out = []
    d = start
    while d <= end:
        out.append({"date": d, "kwh": kwh, "cost_pence": cost, "complete": True})
        d += timedelta(days=1)
    return out


def make_points(start, days, kwh=20.0):
    return [
        {"date": start + timedelta(days=i), "kwh": kwh, "lower": kwh, "upper": kwh}
        for i in range(days)
    ]


TODAY = date(2026, 8, 15)


def test_monthly_buckets_splits_current_month():
    daily = make_daily(date(2026, 7, 1), date(2026, 8, 14))
    points = make_points(date(2026, 8, 15), 365)
    months = yearly.monthly_buckets(daily, points, TODAY, rate=10.0, sc=48.0)

    assert months[0] == {
        "month": "2026-07", "kwh": 31 * 24.0, "cost_pence": 31 * 288.0, "forecast": False,
    }
    # current month: actual-to-date entry then forecast-remainder entry
    aug = [m for m in months if m["month"] == "2026-08"]
    assert [m["forecast"] for m in aug] == [False, True]
    assert aug[0]["kwh"] == 14 * 24.0
    assert aug[1]["kwh"] == 17 * 20.0                      # 15..31 Aug = 17 days
    assert aug[1]["cost_pence"] == 17 * 20.0 * 10.0 + 17 * 48.0
    # 365 points from 15 Aug 2026 end 14 Aug 2027
    assert months[-1]["month"] == "2027-08"
    assert months[-1]["forecast"] is True
    # sorted by month throughout
    assert [m["month"] for m in months] == sorted(m["month"] for m in months)


def test_monthly_buckets_limits_actuals_to_12_months_back():
    daily = make_daily(date(2024, 1, 1), date(2026, 8, 14))
    months = yearly.monthly_buckets(daily, [], TODAY, rate=10.0, sc=48.0)
    assert months[0]["month"] == "2025-08"                 # 12 months before Aug 2026
    assert len(months) == 13                               # Aug 2025 .. Aug 2026


def test_monthly_bucket_cost_none_propagates():
    daily = make_daily(date(2026, 7, 1), date(2026, 7, 31))
    daily[10]["cost_pence"] = None
    months = yearly.monthly_buckets(daily, [], TODAY, rate=10.0, sc=48.0)
    assert months[0]["kwh"] == 31 * 24.0                   # kWh unaffected
    assert months[0]["cost_pence"] is None


def test_forecast_bucket_cost_none_without_rate():
    points = make_points(date(2026, 8, 15), 30)
    months = yearly.monthly_buckets([], points, TODAY, rate=None, sc=48.0)
    assert all(m["cost_pence"] is None for m in months)
    assert all(m["forecast"] for m in months)
