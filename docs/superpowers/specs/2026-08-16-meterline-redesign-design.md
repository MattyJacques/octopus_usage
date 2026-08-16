# Meterline dashboard redesign — design spec

**Date:** 2026-08-16
**Source design:** claude.ai/design project `ab0df67e` — `Energy Dashboard.dc.html` ("Meterline")

## Goal

Replace the current Chart.js frontend with the approved Meterline design: a dark,
two-screen dashboard (Usage / Forecast) with a sidebar, period presets, per-fuel
bar charts with previous-period ghost bars, a peak-hours heatmap, and a forecast
view with band charts and a monthly cost table — all driven by real synced data
instead of the design's mock generator.

## What the design shows, mapped to real data

### Usage screen

- **Presets:** Yesterday (48 half-hour bars), Last 7 days (daily), This month
  (daily, ghost = previous month), Calendar year (monthly, ghost = previous year).
- **Year stepper** applies to the Calendar-year preset only (disabled otherwise);
  clamped to years with data (from first reading to current year).
- **Per-fuel cards** (electricity kWh / gas m³) with period totals (units + £).
- **Peak-hours heatmap:** 7×24 mean consumption over the last 12 weeks, with an
  Electric/Gas tab.
- **Right rail:** Spend so far (enabled fuels, period) + delta vs the previous
  equivalent period; per-fuel breakdown; Notable card (peak slot, cheapest day
  in period, standing charges for the period).
- **Fuel toggles** in the sidebar switch each fuel's card and its contribution to
  the rail on/off (frontend state only).

### Forecast screen

- Per-fuel 18-month line: last 6 actual months (solid) + 12 forecast months
  (dashed, with a low/high band), from `/api/yearly`.
- Right rail: projected 12-month window totals (£ mid/low/high, units), and a
  12-row monthly table (kWh, m³, low/expected/high £).
- Bands use the design's fixed multipliers (low = 0.86×, high = 1.19×) with its
  explanatory footnote. The daily forecast's ±σ band is too narrow to be honest
  at annual scale; fixed scenario multipliers match the design copy.

### Deviations from the mock (deliberate)

- **Weather card and temperature overlay: omitted.** The app has no temperature
  data source; faking it would violate the "real data" goal. Possible follow-up:
  open-meteo history keyed off the account postcode.
- **Sidebar meter block** shows real meter serials (`ELEC ···8188`, `GAS ···1916`
  style) instead of the mock's MPAN/MPRN, plus last-sync time, plus a small
  "Sync now" button (preserves the old Refresh feature).
- **Yesterday preset** uses the latest London date with data (smart-meter data
  lags ~a day); the label shows the actual date.
- Nav items Meters / Tariff / Settings render inert, as in the mock.
- Missing tariff data renders £ as "—" (None propagates, per project convention).

## Backend changes

New/changed endpoints in `app.py`, logic in existing modules:

1. **`GET /api/halfhourly?fuel=X[&date=YYYY-MM-DD]`** — intervals for one
   Europe/London calendar date; `date` defaults to the latest date with data.
   Returns `{date, intervals: [{start, kwh, units, cost_pence}]}`.
   Logic: `costs.halfhourly(conn, fuel, day)` (rate lookup per interval).
2. **`GET /api/monthly?fuel=X&year=YYYY`** — actual calendar-month buckets for
   one year: `{months: [{month, kwh, units, cost_pence}]}`.
   Logic: `yearly.months_of_year(daily, year)` on `costs.daily_costs`.
3. **`GET /api/heatmap?fuel=X`** — mean consumption by weekday×hour over the
   last 12 weeks: `{weeks: 12, rows: [{day, cells: [24 floats]}]}`.
   Logic: `db.hourly_profile(conn, fuel, weeks=12)`.
4. **`/api/history`** gains a per-day `units` field (raw consumption — m³ for
   gas, = kWh for electricity). `costs.daily_costs` gains `units`.
5. **`/api/summary`** gains `meters` (per-fuel serials from meta), `first_data`
   (earliest reading date, for the year stepper clamp), and `gas_m3_to_kwh`
   (conversion factor, so the frontend can derive m³ for forecast months).

Everything else (`/api/forecast`, `/api/yearly`, `/api/sync`, setup page,
no-cache middleware) is unchanged.

## Frontend

`octopus_usage/static/` rewritten: `index.html` (Meterline layout, Google Fonts
link with system fallbacks), `style.css` (design's oklch palette as CSS custom
properties; inline styles from the mock converted to classes), `app.js`
(no-framework rendering, as now; bars are flex divs, lines are inline SVG).
`chart.umd.js` is deleted — nothing uses Chart.js in the new design.

Frontend orchestration: fetch per-fuel endpoints for the active preset plus the
previous period (for ghosts and the delta), cache responses per
(preset, year, fuel) in memory, re-render on state change
(screen/preset/year/fuel toggles/heatmap tab). Sync errors show a slim banner.

## Testing

Existing patterns: seed SQLite via `seed_days`, `TestClient`, no network.

- New endpoint tests: halfhourly (48 intervals, costs, latest-date default,
  explicit date, 404/422), monthly (buckets for a seeded year, empty year),
  heatmap (shape 7×24, values reflect seeded profile), summary additions,
  history `units` (gas m³ ≠ kWh).
- Updated app tests: index page markup assertions target the new layout;
  static-file assertions drop `chart.umd.js`.
- Frontend is verified by serving the app and checking screens render with a
  seeded DB (manual/browser check), as with the previous frontend.
