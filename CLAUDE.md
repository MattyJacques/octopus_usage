# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local FastAPI dashboard for Octopus Energy (UK) smart meter data: historical electricity + gas usage and estimated cost, plus 30-day and 12-month forecasts. Data is synced from the Octopus REST API into a local SQLite file (`octopus_usage.db`, gitignored along with `.env`).

## Commands

```bash
.venv/bin/pytest                          # run the test suite (no network needed)
.venv/bin/pytest tests/test_sync.py       # one test file
.venv/bin/pytest tests/test_app.py -k yearly   # tests matching a keyword

.venv/bin/uvicorn --factory octopus_usage.app:create_app --port 8000   # run the app
```

Setup (once): `python3 -m venv .venv` (Python 3.11+) then `.venv/bin/pip install -r requirements.txt`. Running the real app needs `OCTOPUS_API_KEY` and `OCTOPUS_ACCOUNT_NUMBER` in `.env` (see `.env.example`). There is no linter or build step configured.

## Architecture

Data flow: Octopus API → `octopus_client` → `sync` → SQLite (`db`) → `costs` → `forecast`/`yearly` → JSON endpoints in `app` → static frontend.

- `app.py` — `create_app()` factory (used with uvicorn `--factory`). Endpoints: `/api/summary`, `/api/history`, `/api/halfhourly`, `/api/monthly`, `/api/heatmap`, `/api/forecast`, `/api/yearly`, `/api/weather`, `POST /api/sync`. A sync runs on startup (lifespan) unless `sync_on_start=False`. Missing config serves a setup page instead of erroring.
- `octopus_client.py` — thin httpx wrapper; HTTP Basic auth (API key as username, blank password), pagination, retries on 429/5xx. Accepts a `transport` for tests.
- `sync.py` — discovers meters/tariffs from the account endpoint, backfills up to 730 days of half-hourly readings, then fetches incrementally from the newest stored interval. Rate rows are clipped to each agreement's window (`_clip_row`) because the products API returns full rate history and the rates tables are keyed on `(fuel, valid_from)` alone.
- `db.py` — schema + queries. Tables: `readings`, `rates`, `standing_charges`, `meta` (key/value: `last_sync`, `{fuel}_serial`, `gas_unit`).
- `costs.py` — matches half-hourly readings to unit rates via bisect; gas m³→kWh conversion (SMETS2 meters report m³, detected heuristically on first sync and remembered in `meta`).
- `weather.py` — local temperatures for the dashboard's weather card: account postcode (stored by sync) → postcodes.io geocode (cached in `meta`) → open-meteo daily/hourly temps (dailies cached in `weather_daily`). Every failure path returns None and `/api/weather` degrades to `{"available": false}` — third-party outages must never break the dashboard.
- `forecast.py` — statistical forecast: seasonal baseline (±14 days around the same date in prior years) × day-of-week factor, falling back to a recent-weighted mean with under a year of history.
- `yearly.py` — monthly actual/forecast buckets and rolling-365/calendar-year totals.
- `static/` — no-framework frontend (`app.js` + `index.html`), Chart.js vendored as `chart.umd.js`. Served with `Cache-Control: no-cache`.

## Conventions

- All DB timestamps are UTC ISO-8601 strings with `+00:00` offset (`db.to_utc_iso`), so string comparison equals chronological order — keep it that way when adding queries.
- Days are bucketed by **Europe/London** calendar date; a day is "complete" with ≥46 half-hour intervals (DST days have 46 or 50). Incomplete days are excluded from forecast training.
- Money is **pence, inc. VAT** throughout the backend; `cost_pence` is `None` (never 0) where tariff data has gaps, and aggregations propagate that None.
- Costs are estimates by design — don't try to make them bill-exact.
- Tests use canned API responses in `tests/fixtures.py` via `httpx.MockTransport`, passed through `create_app(transport=...)`; app tests seed the SQLite DB directly with helpers like `seed_days` in `tests/test_app.py`. New features should follow this pattern rather than hitting the network.

## Design docs

`docs/superpowers/specs/` and `docs/superpowers/plans/` hold the design specs and implementation plans for each feature (dated filenames).
