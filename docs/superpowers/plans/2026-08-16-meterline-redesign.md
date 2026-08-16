# Meterline Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Chart.js frontend with the Meterline design (dark two-screen dashboard) driven by real synced data, adding the four small backend endpoints it needs.

**Architecture:** Backend keeps the existing module layout — new aggregation helpers go in `costs.py`, `yearly.py`, `db.py`; `app.py` only wires endpoints. Frontend stays no-framework: `index.html` static layout with container ids, `app.js` renders bars as flex divs and lines as inline SVG, `style.css` holds the design's oklch palette as custom properties. Chart.js is deleted.

**Tech Stack:** FastAPI, SQLite, vanilla JS/CSS, pytest + TestClient (no network).

**Spec:** `docs/superpowers/specs/2026-08-16-meterline-redesign-design.md`

## Global Constraints

- All DB timestamps are UTC ISO-8601 strings with `+00:00`; string comparison = chronological order.
- Days bucket by **Europe/London** calendar date; complete = ≥46 half-hour intervals.
- Money is **pence inc. VAT**; `cost_pence` is `None` (never 0) where tariff data has gaps; aggregations propagate None.
- Tests seed SQLite directly (`seed_days` in `tests/test_app.py`); no network.
- Run tests with `.venv/bin/pytest`.

---

### Task 1: `units` in daily costs and `/api/history`

**Files:**
- Modify: `octopus_usage/costs.py` (`daily_costs`)
- Modify: `octopus_usage/app.py` (`/api/history` response)
- Test: `tests/test_costs.py`, `tests/test_app.py`

**Interfaces:**
- Produces: `costs.daily_costs(conn, fuel)` rows gain `"units": float` (sum of raw `consumption` — m³ for gas, kWh for electricity). `/api/history` days gain `"units"`.

- [ ] **Step 1: Failing tests.** In `tests/test_costs.py` seed a gas reading where `consumption=1.0, consumption_kwh=11.2` and assert the day's `units == 1.0` and `kwh == 11.2`. In `tests/test_app.py::test_history_endpoint` extend the key-set assertion to include `"units"`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_costs.py tests/test_app.py -k "history or units"` — expect FAIL (KeyError/assert).
- [ ] **Step 3: Implement.** In `daily_costs`, accumulate `day["units"] += r["consumption"]` alongside kwh; include `"units"` in the output dict. In `app.py` history response add `"units": d["units"]`.
- [ ] **Step 4: Run same tests — PASS; then full suite.**
- [ ] **Step 5: Commit** `feat: expose raw units (m3) in daily costs and /api/history`.

### Task 2: `costs.halfhourly` + `GET /api/halfhourly`

**Files:**
- Modify: `octopus_usage/costs.py`, `octopus_usage/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `costs.halfhourly(conn, fuel, day: date) -> list[dict]` — readings whose London date is `day`, each `{"start": <UTC ISO>, "kwh": float, "units": float, "cost_pence": float|None}` (cost = kwh × matched unit rate; None when no rate covers the interval; no standing charge at interval level).
- Produces: `GET /api/halfhourly?fuel=X[&date=YYYY-MM-DD]` → `{"date": "YYYY-MM-DD", "intervals": [...]}`; `date` defaults to the London date of the newest reading; 404 when the fuel has no data at all (empty `intervals` for an explicit dataless date is fine); 422 on bad fuel/date.

```python
def halfhourly(conn, fuel, day):
    """Half-hourly readings for one Europe/London calendar date, with per-interval cost."""
    rate_rows = db.rates_for(conn, fuel)
    rate_starts = [r["valid_from"] for r in rate_rows]
    start = datetime.combine(day, time(0), tzinfo=db.LONDON).astimezone(timezone.utc).isoformat()
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=db.LONDON).astimezone(timezone.utc).isoformat()
    out = []
    for r in db.readings(conn, fuel, start=start):
        if r["interval_start"] >= end:
            break
        rate = _lookup(rate_rows, rate_starts, "unit_rate_inc_vat", r["interval_start"])
        out.append({
            "start": r["interval_start"],
            "kwh": r["consumption_kwh"],
            "units": r["consumption"],
            "cost_pence": r["consumption_kwh"] * rate if rate is not None else None,
        })
    return out
```

Endpoint: default day = `datetime.fromisoformat(db.latest_interval_start(conn, fuel)).astimezone(db.LONDON).date()`; 404 if `latest_interval_start` is None; parse explicit `date` with `date.fromisoformat` → 422 on ValueError.

- [ ] **Step 1: Failing tests** (seeded via `seed_days`, 60 days): default returns latest day with 46–50 intervals, each interval has the four keys, `cost_pence == kwh*10.0`; explicit `date` returns that day; `fuel=water` → 422, `date=nope` → 422, gas (unseeded) → 404.
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** as above (imports: `datetime, time, timedelta, timezone` in costs.py).
- [ ] **Step 4: Run — PASS; full suite.**
- [ ] **Step 5: Commit** `feat: /api/halfhourly endpoint for one-day interval detail`.

### Task 3: `yearly.months_of_year` + `GET /api/monthly`

**Files:**
- Modify: `octopus_usage/yearly.py`, `octopus_usage/app.py`
- Test: `tests/test_yearly.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `daily` rows now carrying `units` (Task 1).
- Produces: `yearly.months_of_year(daily, year: int) -> list[dict]` — `[{"month": "YYYY-MM", "kwh": float, "units": float, "cost_pence": float|None}]`, actuals only, sorted, months with no data omitted.
- Produces: `GET /api/monthly?fuel=X&year=YYYY` → `{"months": [...]}` (empty list when no data that year); 422 on bad fuel or year outside 2000–2100; 404 when fuel has no data.

```python
def months_of_year(daily, year):
    """Actual calendar-month buckets for one year: kwh, raw units, None-propagating cost."""
    buckets = {}
    for d in daily:
        if d["date"].year != year:
            continue
        b = buckets.setdefault(_month_key(d["date"]), {"kwh": 0.0, "units": 0.0, "costs": []})
        b["kwh"] += d["kwh"]
        b["units"] += d["units"]
        b["costs"].append(d["cost_pence"])
    return [
        {"month": key, "kwh": b["kwh"], "units": b["units"],
         "cost_pence": sum(b["costs"]) if None not in b["costs"] else None}
        for key, b in sorted(buckets.items())
    ]
```

- [ ] **Step 1: Failing tests.** Unit test in `test_yearly.py` with hand-built daily rows spanning Dec year−1 → Jan year+1 (assert only `year` months, sums, None propagation). App test: seed 60 days ending yesterday, request current year → months present, keys `{month, kwh, units, cost_pence}`; `year=1999` → 422.
- [ ] **Step 2: Run — FAIL.**  
- [ ] **Step 3: Implement** helper + endpoint (guard, check_fuel, `if not 2000 <= year <= 2100: 422`, daily = costs.daily_costs, 404 if empty).
- [ ] **Step 4: Run — PASS; full suite.**
- [ ] **Step 5: Commit** `feat: /api/monthly endpoint with per-year actual buckets`.

### Task 4: `db.hourly_profile` + `GET /api/heatmap`

**Files:**
- Modify: `octopus_usage/db.py`, `octopus_usage/app.py`
- Test: `tests/test_db.py`, `tests/test_app.py`

**Interfaces:**
- Produces: `db.hourly_profile(conn, fuel, weeks=12) -> list[dict]` — 7 rows Mon→Sun: `{"day": "Mon", "cells": [24 floats]}`; cell = mean kWh consumed in that (weekday, hour-of-London-day) over the last `weeks*7` London dates ending at the newest reading's date; 0.0 where no data. Empty list when the fuel has no readings.
- Produces: `GET /api/heatmap?fuel=X` → `{"weeks": 12, "rows": [...]}`; 404 when no data; 422 bad fuel.

```python
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

def hourly_profile(conn, fuel, weeks=12):
    """Mean kWh by (weekday, London hour) over the trailing `weeks` ending at the newest reading."""
    latest = latest_interval_start(conn, fuel)
    if latest is None:
        return []
    end = datetime.fromisoformat(latest).astimezone(LONDON).date()
    start = end - timedelta(days=weeks * 7 - 1)
    sums = {}
    dates_by_wd = {wd: set() for wd in range(7)}
    for r in readings(conn, fuel):
        dt = datetime.fromisoformat(r["interval_start"]).astimezone(LONDON)
        if not start <= dt.date() <= end:
            continue
        wd = dt.weekday()
        sums[(wd, dt.hour)] = sums.get((wd, dt.hour), 0.0) + r["consumption_kwh"]
        dates_by_wd[wd].add(dt.date())
    return [
        {"day": DAY_NAMES[wd],
         "cells": [sums.get((wd, h), 0.0) / max(1, len(dates_by_wd[wd])) for h in range(24)]}
        for wd in range(7)
    ]
```

(`timedelta` needs importing in db.py.)

- [ ] **Step 1: Failing tests.** `test_db.py`: seed 14 days of 0.5 kWh half-hours via direct `upsert_readings` (reuse the pattern from `seed_days`), assert shape 7×24 and each cell ≈ 1.0 (two 0.5 intervals/hour); empty fuel → []. App test: heatmap endpoint returns weeks=12, 7 rows, 24 cells; gas → 404; water → 422.
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run — PASS; full suite.**
- [ ] **Step 5: Commit** `feat: /api/heatmap endpoint with 12-week hourly profile`.

### Task 5: `/api/summary` additions

**Files:**
- Modify: `octopus_usage/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Produces top-level summary keys:
  - `"meters": {"electricity": str|None, "gas": str|None}` from meta `{fuel}_serial`
  - `"first_data": "YYYY-MM-DD"|None` — earliest London date across fuels (`db.earliest_interval_start`)
  - `"gas_m3_to_kwh": float` — `costs.M3_TO_KWH`
  - per-fuel `"standing_charge": float|None` — `costs.current_standing_charge` (pence/day), inside each `out["fuels"][fuel]`

- [ ] **Step 1: Failing test.** Extend seed to set `db.meta_set(conn, "electricity_serial", "20E5138188")`; assert `meters["electricity"] == "20E5138188"`, `meters["gas"] is None`, `first_data == (today-60 days).isoformat()`, `gas_m3_to_kwh == pytest.approx(11.22, rel=0.01)`, `fuels["electricity"]["standing_charge"] == 48.0`.
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** in the summary endpoint (earliest = min of non-None `earliest_interval_start` per fuel, converted via `.astimezone(db.LONDON).date()`).
- [ ] **Step 4: Run — PASS; full suite.**
- [ ] **Step 5: Commit** `feat: meters, first_data, standing charge and gas factor in /api/summary`.

### Task 6: Meterline frontend

**Files:**
- Rewrite: `octopus_usage/static/index.html`, `octopus_usage/static/style.css`, `octopus_usage/static/app.js`
- Delete: `octopus_usage/static/chart.umd.js`
- Test: `tests/test_app.py` (index/static assertions)

**Interfaces:**
- Consumes: `/api/summary` (Task 5 shape), `/api/history?fuel&days=70`, `/api/halfhourly` (Task 2), `/api/monthly` (Task 3), `/api/heatmap` (Task 4), `/api/yearly` (unchanged), `POST /api/sync`.

**Layout (index.html):** one `.shell` flex container — `.sidebar` (brand "Meterline"; nav buttons Usage/Forecast active + Meters/Tariff/Settings inert `disabled`; Fuels card with two toggle buttons; footer with meter serials `ELEC ···xxxx` / `GAS ···xxxx`, `Synced HH:MM`, small "Sync now" button) and `.main` (header: preset segmented control + year stepper ‹ YYYY ›; `#banner` for sync errors; `#screen-usage` and `#screen-forecast` containers filled by JS). Google Fonts link for Space Grotesk + Roboto Mono with `system-ui`/`ui-monospace` fallbacks.

**Palette (style.css custom properties):** `--bg: oklch(0.12 0.012 250)`, `--shell: oklch(0.155 0.013 250)`, `--side: oklch(0.135 0.012 250)`, `--panel: oklch(0.175 0.013 250)`, `--rail: oklch(0.165 0.013 250)`, `--card: oklch(0.185 0.013 250)`, `--card2: oklch(0.20 0.013 250)`, `--border: oklch(0.24 0.013 250)`, `--border2: oklch(0.28 0.014 250)`, `--text: oklch(0.95 0.005 250)`, `--dim: oklch(0.78 0.01 250)`, `--muted: oklch(0.58 0.01 250)`, `--faint: oklch(0.50 0.01 250)`, `--elec: oklch(0.70 0.16 230)`, `--gas: oklch(0.78 0.14 75)`, `--act: oklch(0.86 0.02 250)`, `--act-fg: oklch(0.16 0.02 250)`, `--up: oklch(0.74 0.12 30)`, `--down: oklch(0.76 0.11 150)`. Shell `width: min(1440px, 100%)`, radii/spacing per the mock (20px shell, 13px cards, 12px rail cards).

**app.js structure:** module-level `state = {screen:'usage', preset:'month', year:<current>, elec:true, gas:true, heatFuel:'electricity'}`; `cache` Map keyed by URL for `fetchJSON`; `render()` re-renders both screens' dynamic regions from fetched data; event listeners mutate state and call `render()`. Formatting helpers `money(pence)` (→ `£x,xxx` / `£x.xx` under £100; `—` for null) and `fmtUnits(v, unit)`.

Rendering rules (per spec):
- Bars: flex row of `.bar-slot` divs; fill height = `v/max*100%` (max over bars and ghosts × 1.08); ghost = absolutely-positioned dashed-top block behind the fill (month preset: previous month same day index; year preset: previous year same month; none for yesterday/7d). Labels row under each chart (hh: every 8th "HH:00"; 7d: weekday; month: 1 and multiples of 5; year: month abbr).
- Fuel cards: dot + name; right: total units (fuel colour) + total cost. Gas bars/units use `units` (m³); electricity uses kWh. Sum cost with None-propagation → `—`.
- Heatmap: rows from `/api/heatmap`; cell opacity `0.07 + 0.93 * (v/max)^1.25`; colour = active fuel token; Electric/Gas tab switches `state.heatFuel`; hour labels every 3.
- Rail (usage): "Spend so far — {label}" (label: actual date for yesterday preset, "last 7 days", "August 2026", "2026"); hero = Σ enabled fuels' period cost; delta line `▲/▼ x.x% vs previous period` coloured `--up`/`--down`, hidden when either side is None; per-fuel rows (units · cost, or "off"); Notable card: Peak slot from heatmap max ("Tue 18:00"), Cheapest day (min complete-day cost in period; label "Cheapest month" + min month cost for year preset), Standing charges = days-in-period × Σ enabled fuels' `standing_charge`.
- Forecast screen (per enabled fuel, from `/api/yearly` months): merge duplicate current-month actual+forecast entries; series = last 6 actual months + 12 forecast months; SVG viewBox `0 0 1000 200`, y-scale max = series max × 1.24; band path over forecast points (mid×0.86 … mid×1.19, fuel colour at 16% alpha), dashed mid line, solid actual line, dashed vertical split; axis labels every 3rd month. Rail: "Projected — {Mon YYYY – Mon YYYY}" (12 full months after the current one), hero £mid with units totals, low–high line, 12-row table (Month, kWh, m³, Low, Exp., High; m³ = gas kwh ÷ `gas_m3_to_kwh`; `—` for a disabled fuel), footnote copy from the mock.
- Empty/error states: fuel absent from summary → card "No data yet"; `sync_error` → `#banner` shown; "Sync now" POSTs `/api/sync`, then clears cache and re-renders.

- [ ] **Step 1: Update failing app tests.** `test_index_serves_dashboard_when_configured`: assert `"Meterline"` and `"screen-usage"` in page text and `/static/app.js` is 200; drop the `chart.umd.js` assertion. Run — FAIL (old markup).
- [ ] **Step 2: Write `index.html`, `style.css`, `app.js`; delete `chart.umd.js`.**
- [ ] **Step 3: Run full suite — PASS.**
- [ ] **Step 4: Commit** `feat: Meterline frontend replacing Chart.js dashboard`.

### Task 7: End-to-end verification against the real DB

- [ ] **Step 1:** `.venv/bin/pytest` — all green.
- [ ] **Step 2:** Run `.venv/bin/uvicorn --factory octopus_usage.app:create_app --port 8000` (sync on start OK), open both screens in a browser, exercise presets, year stepper, fuel toggles, heatmap tab; screenshot Usage + Forecast.
- [ ] **Step 3:** Fix anything visually broken; re-run suite; commit fixes.

## Self-review

- Spec coverage: presets/ghosts (T2/T3/T6), heatmap (T4/T6), rail + notable (T5/T6), forecast screen (T6, existing /api/yearly), deviations (weather omitted; serial labels; latest-date yesterday) all encoded in T6 rules. ✓
- No placeholders in backend tasks; frontend task locks palette, layout, data flow, and rendering formulas. ✓
- Types consistent: `units` introduced T1, consumed T3/T6; `standing_charge` T5 → T6. ✓
