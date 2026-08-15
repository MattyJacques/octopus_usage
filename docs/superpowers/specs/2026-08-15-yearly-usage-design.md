# Yearly usage and forecast — design

**Date:** 2026-08-15
**Status:** Approved

## Goal

Show total energy usage for the previous year and a projection for the coming
year, per fuel, in the dashboard. "Year" means both rolling 365-day windows and
calendar years.

## Context

The app already stores ~2 years of half-hourly readings (since Aug 2024) for
electricity and gas, computes daily totals with costs (`costs.daily_costs`),
and produces a statistical daily forecast (`forecast.make_forecast`) whose
seasonal baseline works well once ≥365 days of history exist. The dashboard has
a summary tile row, a history bar chart, and a 30-day forecast line chart.

## Approach

A dedicated `GET /api/yearly` endpoint computes all yearly aggregates
server-side (testable in Python, consistent with `/api/summary`), and the
frontend renders a new "Year" card from that single payload. The alternative —
fetching daily data and bucketing client-side — was rejected because it puts
untested aggregation maths in JavaScript.

## Backend

### `GET /api/yearly`

No parameters. Response shape:

```json
{
  "fuels": {
    "electricity": {
      "months": [
        {"month": "2025-09", "kwh": 123.4, "cost_pence": 4567.8, "forecast": false},
        ...
        {"month": "2027-08", "kwh": 110.2, "cost_pence": 4100.0, "forecast": true}
      ],
      "totals": {
        "last_365":         {"kwh": ..., "cost_pence": ...},
        "next_365":         {"kwh": ..., "cost_pence": ...},
        "calendar_prev":    {"year": 2025, "kwh": ..., "cost_pence": ...},
        "calendar_current": {"year": 2026, "kwh": ..., "cost_pence": ...}
      }
    },
    "gas": { ... }
  }
}
```

Semantics:

- **Forecast horizon:** one call to `make_forecast(daily, days=365)` per fuel
  (the function already accepts `days`; no change needed). Forecast costs use
  the *current* unit rate and standing charge — future tariffs are unknown.
- **`months`:** 25 calendar-month buckets — the 12 months before the current
  month (actuals), the current month, and the 12 months the 365-day forecast
  reaches into. The current month appears as *two* entries when split:
  actual-to-date (`forecast: false`) and forecast-remainder (`forecast: true`),
  so the chart can stack them into one bar. Months with no data are omitted.
- **Past-month costs:** sum of daily `cost_pence`; `null` if any contributing
  day's cost is unknown (matches existing `/api/summary` behaviour).
- **`last_365`:** sum of actual daily totals in `[today − 365, today)`. If the
  fuel has fewer than ~365 days of history, the kWh/cost are `null` rather
  than a misleading partial sum (threshold: first reading more than 365 days
  ago).
- **`next_365`:** sum over the 365 forecast points; cost =
  `kwh × current_rate + 365 × standing_charge`, `null` if either rate is
  missing. `null` if the forecast is empty.
- **`calendar_prev`:** Jan 1 – Dec 31 of last year, actuals only; `null` kWh if
  the year is not fully covered by data.
- **`calendar_current`:** actuals from Jan 1 to date plus forecast from
  tomorrow to Dec 31 (clipped from the 365-day forecast). Marked in the UI as
  projected.
- Fuels with no data are omitted, as in `/api/summary`. Standard `guard()` /
  503 behaviour applies.

Implementation lives in a small aggregation module or functions alongside the
endpoint; monthly bucketing and totals are pure functions over the
daily-totals list so they unit-test without the app.

## Frontend

New "Year" card between the History and Next-30-days cards:

- **Tiles per fuel:** Last 365 days · Next 365 days (projected) ·
  2025 (calendar) · 2026 (projected). Same tile markup/formatting as the
  existing summary tiles; `—` for nulls.
- **Monthly chart:** grouped bar chart, both fuels, one bar per month across
  the 25-month span. Actual months use the fuel's solid series colour;
  forecast months use the same colour at reduced opacity. The split current
  month renders as a stacked actual+forecast bar. kWh/£ toggle, same control
  pattern as the history chart.
- **Caption:** projection assumes today's tariff; shaded bars are forecast.
- Chart.js and existing CSS variables/theme handling are reused; re-render on
  theme change already reloads the page.

## Error handling

- `/api/yearly` returns 503 with detail when unconfigured (same `guard()`).
- Empty fuels object → frontend hides the Year card.
- Nulls flow through to `—` in tiles and gaps in the chart.

## Testing

`tests/test_yearly.py` (pure-function tests) plus endpoint tests in
`test_app.py`:

- Monthly bucketing: correct month keys, partial current-month split into
  actual + forecast entries.
- Costs: unknown daily cost → month cost `null`; forecast cost `null` when
  rate or standing charge missing.
- `last_365` and `calendar_prev` are `null` with <365 days / incomplete
  calendar year of history.
- `calendar_current` combines actuals-to-date with clipped forecast.
- Endpoint: 503 when unconfigured, fuel omitted when no data, response shape.
