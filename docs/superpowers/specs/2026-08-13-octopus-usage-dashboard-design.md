# Octopus Energy Usage Dashboard — Design

**Date:** 2026-08-13
**Status:** Approved by user (sections reviewed in brainstorming session)

## Purpose

A locally-run web dashboard for a UK Octopus Energy customer that:

1. Displays historical electricity and gas usage (kWh and estimated £).
2. Predicts the next 30 days of daily usage and cost with a statistical forecast.

## Decisions made during brainstorming

- **App type:** local web dashboard (FastAPI backend + browser UI).
- **Fuels:** electricity + gas.
- **Credentials:** user needs README instructions for locating API key and account number.
- **Predictions:** statistical forecast (seasonal + day-of-week), no ML dependencies.
- **Units:** kWh and estimated £.
- **Stack:** Python. Approach chosen: FastAPI + SQLite cache + browser charts.

## Architecture

```
octopus_usage/
  config.py         # loads API key + account number from .env
  octopus_client.py # thin Octopus REST API wrapper
  db.py             # SQLite schema + queries
  sync.py           # backfill + incremental sync
  costs.py          # tariff rates + cost estimation
  forecast.py       # statistical forecast
  app.py            # FastAPI routes, serves the frontend
  static/           # index.html, JS, Chart.js (vendored, no CDN)
```

### Credentials & configuration

- A git-ignored `.env` file holds `OCTOPUS_API_KEY` and `OCTOPUS_ACCOUNT_NUMBER`.
- README documents where to find them: Octopus online dashboard → Personal details → API access. API key looks like `sk_live_...`; account number like `A-XXXXXXXX`.
- The Octopus API uses HTTP Basic auth with the API key as username and blank password.

### Meter discovery

On startup, call `GET /v1/accounts/{account}/`. The response contains properties with:

- electricity meter points: MPAN + meter serial numbers,
- gas meter points: MPRN + meter serial numbers,
- tariff agreements per meter point (product/tariff codes with `valid_from`/`valid_to`).

No manual meter configuration. If a property has multiple meters/serials, use the ones that return consumption data.

### Data sync

- Consumption endpoints (half-hourly, `group_by` unset):
  - Electricity: `GET /v1/electricity-meter-points/{mpan}/meters/{serial}/consumption/`
  - Gas: `GET /v1/gas-meter-points/{mprn}/meters/{serial}/consumption/`
- Results are paginated (`page_size` up to 25000; use it to minimise requests).
- **First run:** backfill up to 2 years of history per fuel.
- **Subsequent runs:** request only readings with `period_from` after the latest stored interval.
- Sync runs on startup and on demand via `POST /api/sync`.
- Smart meter data typically lags ~1 day; the UI states this.

### Storage (SQLite)

Tables:

- `readings(fuel, interval_start, interval_end, consumption)` — half-hourly; unique on `(fuel, interval_start)`. Electricity in kWh; gas stored as returned (see gas conversion below) plus a normalised kWh column.
- `rates(fuel, tariff_code, valid_from, valid_to, unit_rate_inc_vat)` — unit rates over time.
- `standing_charges(fuel, tariff_code, valid_from, valid_to, charge_inc_vat)` — pence/day.
- `meta(key, value)` — meter identifiers, last sync time, discovered account details.

## Cost estimation

- From the account's tariff agreements, fetch unit rates and standing charges from
  `GET /v1/products/{product}/electricity-tariffs/{tariff}/standard-unit-rates/` (and gas equivalents), covering the same date span as stored readings.
- **Cost per half-hour** = consumption(kWh) × unit rate valid at that interval. This handles flat tariffs and time-of-use tariffs (e.g. Agile) uniformly.
- **Daily cost** = sum of half-hour costs + standing charge for that day.
- **Gas conversion:** SMETS2 meters report m³, SMETS1 report kWh. Convert m³ → kWh as
  `kWh = volume × 1.02264 × 39.5 (calorific value) ÷ 3.6` before pricing. Detect which unit applies by meter generation from account data (fallback: heuristic on magnitude, documented in code).
- Costs are **estimates**, labelled as such in the UI (rates include VAT; real bills vary slightly, e.g. daily calorific values).

## Forecasting

Per fuel, predict the next 30 days of daily kWh:

1. Aggregate half-hourly readings into daily totals. Days with missing intervals are excluded from training.
2. **Day-of-week factors:** mean usage per weekday ÷ overall mean.
3. **Seasonal baseline:** mean daily usage in a ±14-day window around the same calendar dates in previous years.
   - **Fallback (<1 year of history):** weighted mean of the last 28 days, linearly weighted toward recent days.
4. **Forecast(day) = seasonal baseline × day-of-week factor.**
5. **Confidence band:** ± one standard deviation of residuals computed by applying the same model to the most recent 28 days of actuals.
6. **Forecast cost** = forecast kWh × current unit rate (for time-of-use tariffs, the average rate over the last 7 days) + standing charge.

Pure Python (`statistics` module); no pandas/ML dependencies.

## Backend API

- `GET /api/summary` — per fuel: yesterday, last 7 days, last 30 days, projected next 30 days (kWh + £), last sync time.
- `GET /api/history?fuel=electricity|gas&days=7|30|90|365` — daily series of kWh and £.
- `GET /api/forecast?fuel=...` — 30 daily points with lower/upper band, kWh and £.
- `POST /api/sync` — fetch new readings and rates; returns counts + timestamp.
- `GET /` — serves the dashboard.

## Frontend

Single page at `http://localhost:8000`, plain HTML/JS with Chart.js vendored locally. No build step.

- **Summary tiles:** yesterday / last 7 days / last 30 days / projected next 30 days, per fuel, kWh + estimated £.
- **History chart:** daily bars, period selector (7/30/90/365 days), kWh ↔ £ toggle.
- **Forecast chart:** last 30 days of actuals continuing into the 30-day forecast line with shaded confidence band.
- **Refresh button:** triggers `/api/sync`, shows last-updated time and the ~1-day smart-meter lag note.

## Error handling

- **Missing/invalid credentials:** render a setup page explaining exactly what to put in `.env`; no stack traces.
- **Octopus API unavailable/rate-limited:** serve from SQLite cache with a "data may be stale" banner; sync retries with exponential backoff.
- **No gas meter / no smart data for a fuel:** hide that fuel gracefully.
- **Gaps in meter data:** rendered as gaps (not zeros); excluded from forecast training.

## Testing

- **Unit tests (pytest):** forecast maths (factors, seasonal window, fallback, band), cost calculation (flat tariff, time-of-use, gas m³ conversion), incremental sync logic — all against fixture API responses; no live calls.
- **API tests:** FastAPI TestClient against a temporary SQLite database.
- **Manual smoke test:** run against the real account as the final acceptance step.

## Out of scope (YAGNI)

- Multi-account or multi-property support.
- Tariff comparison/switching advice.
- ML forecasting models.
- Authentication on the local dashboard (localhost only).
- Export prices / outgoing tariffs.
