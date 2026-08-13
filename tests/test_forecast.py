from datetime import date, timedelta

import pytest

from octopus_usage.forecast import (
    day_of_week_factors,
    make_forecast,
    recent_weighted_mean,
    seasonal_baseline,
)


def daily_series(start, values, complete=True):
    return [
        {"date": start + timedelta(days=i), "kwh": float(v), "complete": complete}
        for i, v in enumerate(values)
    ]


def test_constant_history_forecasts_constant_with_zero_band():
    daily = daily_series(date(2026, 6, 1), [10.0] * 56)
    fc = make_forecast(daily, days=30)
    assert len(fc) == 30
    assert fc[0]["date"] == date(2026, 7, 27)
    for p in fc:
        assert p["kwh"] == pytest.approx(10.0)
        assert p["lower"] == pytest.approx(10.0)
        assert p["upper"] == pytest.approx(10.0)


def test_weekend_factor_carries_into_forecast():
    start = date(2026, 6, 1)  # a Monday
    vals = [20.0 if (start + timedelta(days=i)).weekday() >= 5 else 10.0 for i in range(56)]
    fc = make_forecast(daily_series(start, vals), days=7)
    by_weekday = {p["date"].weekday(): p["kwh"] for p in fc}
    assert by_weekday[5] > 1.7 * by_weekday[0]


def test_short_history_uses_recent_weighted_mean():
    # Old level 50, last 28 days level 5: forecast must track the recent level.
    vals = [50.0] * 72 + [5.0] * 28
    fc = make_forecast(daily_series(date(2026, 3, 1), vals), days=5)
    assert all(p["kwh"] == pytest.approx(5.0, rel=0.05) for p in fc)


def test_seasonal_baseline_uses_same_calendar_window_of_previous_years():
    daily = daily_series(date(2025, 1, 1), [30.0] * 29) + daily_series(date(2025, 6, 1), [5.0] * 29)
    assert seasonal_baseline(daily, date(2026, 1, 15)) == pytest.approx(30.0)
    assert seasonal_baseline(daily, date(2026, 6, 15)) == pytest.approx(5.0)
    assert seasonal_baseline(daily, date(2026, 9, 15)) is None


def test_long_history_uses_seasonal_baseline():
    # 370 days: January 2025 ran at 30, everything after at 10. Forecasting
    # mid-January 2026 must come from last January's window, not recent days.
    daily = daily_series(date(2025, 1, 1), [30.0] * 31 + [10.0] * 339)
    fc = make_forecast(daily, days=5, start=date(2026, 1, 10))
    assert all(25.0 < p["kwh"] < 35.0 for p in fc)


def test_incomplete_days_are_excluded():
    daily = daily_series(date(2026, 6, 1), [10.0] * 56)
    daily[10]["kwh"] = 999.0
    daily[10]["complete"] = False
    fc = make_forecast(daily, days=7)
    assert all(p["kwh"] == pytest.approx(10.0) for p in fc)


def test_band_reflects_noise_and_never_goes_negative():
    daily = daily_series(date(2026, 6, 1), [10.0, 14.0] * 28)
    fc = make_forecast(daily, days=3)
    for p in fc:
        assert p["upper"] > p["kwh"] > p["lower"] >= 0.0


def test_day_of_week_factors_and_recent_weighted_mean_edge_cases():
    assert day_of_week_factors([]) == {i: 1.0 for i in range(7)}
    assert recent_weighted_mean([], date(2026, 6, 1)) is None
    assert make_forecast([]) == []
