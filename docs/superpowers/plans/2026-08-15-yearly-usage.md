# Yearly Usage and Forecast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Year" dashboard card showing per-fuel yearly totals (rolling 365-day and calendar-year, actual and projected) plus a 25-month actual-vs-forecast monthly bar chart.

**Architecture:** A new pure-function module `octopus_usage/yearly.py` buckets the existing daily-totals list and 365-day forecast points into calendar months and computes the four headline totals. A new `GET /api/yearly` endpoint in `app.py` wires it to the DB per fuel. The frontend adds one card (tiles + stacked/grouped Chart.js bar chart) fed by that single payload.

**Tech Stack:** Python 3.11+, FastAPI, sqlite3, pytest; vanilla JS + Chart.js (vendored `chart.umd.js`).

**Spec:** `docs/superpowers/specs/2026-08-15-yearly-usage-design.md`

## Global Constraints

- Money is **pence, inc. VAT** throughout the backend; the frontend formats to £.
- Cost sums are **None-propagating**: a bucket/window cost is `null` if any contributing day's `cost_pence` is `None` (matches `/api/summary`).
- Forecast costs always use the **current** unit rate and standing charge (`costs.current_unit_rate`, `costs.current_standing_charge`); `null` if either is missing.
- `last_365` and `calendar_prev` are `null` when history doesn't fully cover the window — never a partial sum.
- Daily-totals dicts look like `{"date": date, "kwh": float, "cost_pence": float|None, "complete": bool}` (from `costs.daily_costs`). Forecast points look like `{"date": date, "kwh": float, "lower": float, "upper": float}` (from `forecast.make_forecast`).
- Tests are plain pytest functions (no classes), style-matched to the existing suite. Run with `.venv/bin/pytest`.
- Fuels with no readings are omitted from API responses, as `/api/summary` does.

---

### Task 1: `yearly.monthly_buckets` — calendar-month aggregation

**Files:**
- Create: `octopus_usage/yearly.py`
- Create: `tests/test_yearly.py`

**Interfaces:**
- Consumes: daily-totals dicts and forecast-point dicts (shapes in Global Constraints). Rate/standing charge are plain floats-or-None.
- Produces: `monthly_buckets(daily, points, today, rate, sc) -> list[dict]`, each entry `{"month": "YYYY-MM", "kwh": float, "cost_pence": float|None, "forecast": bool}`, sorted by month with an actual entry before a forecast entry for the same month. Also module-private helpers `_month_key(d)`, `_months_ago(today, n)` reused by Task 2.

Semantics (from spec): actual buckets cover the 12 calendar months before the current month plus the current month to date; older daily rows are excluded. Forecast buckets cover every month the forecast points touch. The current month can therefore appear twice — actual-to-date and forecast-remainder. Months with no data simply don't appear.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_yearly.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_yearly.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'octopus_usage.yearly'` (or `ImportError`).

- [ ] **Step 3: Implement `octopus_usage/yearly.py`**

```python
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
```

(`timedelta` is unused until Task 2 adds `totals`; keep the import.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_yearly.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add octopus_usage/yearly.py tests/test_yearly.py
git commit -m "feat: monthly actual/forecast bucketing for yearly view"
```

---

### Task 2: `yearly.totals` — rolling and calendar-year totals

**Files:**
- Modify: `octopus_usage/yearly.py` (append)
- Modify: `tests/test_yearly.py` (append)

**Interfaces:**
- Consumes: same inputs as Task 1; `_forecast_cost` helper from Task 1.
- Produces: `totals(daily, points, today, rate, sc) -> dict` with keys:
  - `"last_365"`: `{"kwh": float|None, "cost_pence": float|None}` — actuals in `[today-365, today)`; both `None` unless `daily[0]["date"] <= today - 365 days`.
  - `"next_365"`: same shape — sum over all forecast points; `None` if `points` empty.
  - `"calendar_prev"`: `{"year": int, "kwh": ..., "cost_pence": ...}` — last calendar year, `None` values unless data covers Jan 1–Dec 31.
  - `"calendar_current"`: `{"year": int, "kwh": ..., "cost_pence": ...}` — actuals Jan 1→today plus forecast clipped to Dec 31.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_yearly.py`:

```python
def test_totals_last_365_needs_full_history():
    long = make_daily(TODAY - timedelta(days=400), TODAY - timedelta(days=1))
    t = yearly.totals(long, [], TODAY, rate=10.0, sc=48.0)
    assert t["last_365"]["kwh"] == 365 * 24.0
    assert t["last_365"]["cost_pence"] == 365 * 288.0

    short = make_daily(TODAY - timedelta(days=100), TODAY - timedelta(days=1))
    t = yearly.totals(short, [], TODAY, rate=10.0, sc=48.0)
    assert t["last_365"] == {"kwh": None, "cost_pence": None}


def test_totals_next_365():
    points = make_points(TODAY, 365)
    t = yearly.totals([], points, TODAY, rate=10.0, sc=48.0)
    assert t["next_365"]["kwh"] == 365 * 20.0
    assert t["next_365"]["cost_pence"] == 365 * 20.0 * 10.0 + 365 * 48.0

    t = yearly.totals([], [], TODAY, rate=10.0, sc=48.0)
    assert t["next_365"] == {"kwh": None, "cost_pence": None}


def test_totals_calendar_prev_requires_complete_year():
    covered = make_daily(date(2024, 12, 1), date(2026, 8, 14))
    t = yearly.totals(covered, [], TODAY, rate=10.0, sc=48.0)
    assert t["calendar_prev"]["year"] == 2025
    assert t["calendar_prev"]["kwh"] == 365 * 24.0

    partial = make_daily(date(2025, 3, 1), date(2026, 8, 14))
    t = yearly.totals(partial, [], TODAY, rate=10.0, sc=48.0)
    assert t["calendar_prev"] == {"year": 2025, "kwh": None, "cost_pence": None}


def test_totals_calendar_current_combines_actual_and_clipped_forecast():
    daily = make_daily(date(2026, 1, 1), date(2026, 8, 14))
    points = make_points(date(2026, 8, 15), 365)
    t = yearly.totals(daily, points, TODAY, rate=10.0, sc=48.0)
    actual_days = (date(2026, 8, 14) - date(2026, 1, 1)).days + 1   # 226
    fc_days = (date(2026, 12, 31) - date(2026, 8, 15)).days + 1     # 139, clipped at Dec 31
    assert t["calendar_current"]["year"] == 2026
    assert t["calendar_current"]["kwh"] == actual_days * 24.0 + fc_days * 20.0
    assert t["calendar_current"]["cost_pence"] == (
        actual_days * 288.0 + fc_days * 20.0 * 10.0 + fc_days * 48.0
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_yearly.py -v`
Expected: the 4 new tests FAIL with `AttributeError: module 'octopus_usage.yearly' has no attribute 'totals'`; Task 1's tests still PASS.

- [ ] **Step 3: Implement `totals`**

Append to `octopus_usage/yearly.py`:

```python
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
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest -v`
Expected: all PASS (8 in test_yearly.py plus the existing suite).

- [ ] **Step 5: Commit**

```bash
git add octopus_usage/yearly.py tests/test_yearly.py
git commit -m "feat: rolling-365 and calendar-year totals"
```

---

### Task 3: `GET /api/yearly` endpoint

**Files:**
- Modify: `octopus_usage/app.py` (add endpoint after `get_forecast`, ~line 148; add `yearly` to the `octopus_usage` import at line 10)
- Modify: `tests/test_app.py` (append tests)

**Interfaces:**
- Consumes: `yearly.monthly_buckets(daily, points, today, rate, sc)` and `yearly.totals(daily, points, today, rate, sc)` from Tasks 1–2; existing `costs.daily_costs`, `costs.current_unit_rate`, `costs.current_standing_charge`, `forecast.make_forecast(daily, days=365)`, `db.FUELS`, and the app's `guard()` closure.
- Produces: `GET /api/yearly` (no params) → `{"fuels": {<fuel>: {"months": [...], "totals": {...}}}}`, dates serialized as `"YYYY-MM"` strings inside `months`. 503 when unconfigured; fuels without data omitted.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
def test_yearly_endpoint(tmp_path):
    app = make_test_app(tmp_path, seed=seed_elec_60_days)
    with TestClient(app) as client:
        data = client.get("/api/yearly").json()
        assert set(data["fuels"]) == {"electricity"}
        elec = data["fuels"]["electricity"]

        # 60 days of history: rolling/calendar-prev totals must be null, not partial
        assert elec["totals"]["last_365"] == {"kwh": None, "cost_pence": None}
        assert elec["totals"]["calendar_prev"]["kwh"] is None
        assert elec["totals"]["next_365"]["kwh"] == pytest.approx(365 * 24.0, rel=0.05)
        assert elec["totals"]["next_365"]["cost_pence"] is not None
        assert elec["totals"]["calendar_current"]["year"] == date.today().year
        assert elec["totals"]["calendar_current"]["kwh"] > 0

        months = elec["months"]
        assert months, "expected month buckets"
        assert set(months[0]) == {"month", "kwh", "cost_pence", "forecast"}
        assert any(m["forecast"] for m in months)
        assert any(not m["forecast"] for m in months)
        assert [m["month"] for m in months] == sorted(m["month"] for m in months)


def test_yearly_unconfigured_returns_503(tmp_path, monkeypatch):
    monkeypatch.delenv("OCTOPUS_API_KEY", raising=False)
    monkeypatch.delenv("OCTOPUS_ACCOUNT_NUMBER", raising=False)
    monkeypatch.chdir(tmp_path)
    app = create_app(sync_on_start=False)
    with TestClient(app) as client:
        assert client.get("/api/yearly").status_code == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_app.py -v -k yearly`
Expected: both FAIL — the GET returns 404 (`json()` lacks `"fuels"` → KeyError) and the 503 assertion sees 404.

- [ ] **Step 3: Implement the endpoint**

In `octopus_usage/app.py`, change line 10 to:

```python
from octopus_usage import costs, db, forecast, sync, yearly
```

Add after the `get_forecast` handler:

```python
    @app.get("/api/yearly")
    def get_yearly():
        guard()
        conn = app.state.conn
        today = date.today()
        out = {"fuels": {}}
        for fuel in db.FUELS:
            daily = costs.daily_costs(conn, fuel)
            if not daily:
                continue
            points = forecast.make_forecast(daily, days=365)
            rate = costs.current_unit_rate(conn, fuel)
            sc = costs.current_standing_charge(conn, fuel)
            out["fuels"][fuel] = {
                "months": yearly.monthly_buckets(daily, points, today, rate, sc),
                "totals": yearly.totals(daily, points, today, rate, sc),
            }
        return out
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add octopus_usage/app.py tests/test_app.py
git commit -m "feat: /api/yearly endpoint with monthly buckets and year totals"
```

---

### Task 4: Frontend Year card

**Files:**
- Modify: `octopus_usage/static/index.html` (new section between the History card and the Next-30-days card)
- Modify: `octopus_usage/static/app.js`
- Modify: `tests/test_app.py:114` (extend the index-page assertion)
- Modify: `README.md:4` (mention yearly view)

**Interfaces:**
- Consumes: `GET /api/yearly` payload from Task 3; existing JS helpers `$`, `cssVar`, `hexToRgba`, `fmt`, `baseOptions`, `FUEL_META`, `state.fuels`.
- Produces: a `#year-card` section with `#year-unit` toggle, `#year-tiles`, and `#year-chart` canvas; `renderYear()` called from `loadAll()`.

Chart design (from spec): one stacked-bar group per fuel per month (`stack: fuel` + stacked axes gives grouped-by-fuel, stacked-within-fuel bars). Each fuel contributes two datasets — actuals in the solid series colour, forecast in the same colour at reduced opacity — so the split current month naturally stacks actual + forecast into one bar, and all other months have only one non-null segment.

- [ ] **Step 1: Update the failing page test**

In `tests/test_app.py`, `test_index_serves_dashboard_when_configured`, after the `"history-chart"` assertion add:

```python
        assert "year-chart" in page.text
```

Run: `.venv/bin/pytest tests/test_app.py::test_index_serves_dashboard_when_configured -v`
Expected: FAIL on the new assertion.

- [ ] **Step 2: Add the Year section to `index.html`**

Insert between the History `</section>` and the Next-30-days `<section class="card">`:

```html
  <section class="card" id="year-card">
    <div class="controls">
      <h2>Year</h2>
      <div class="btn-group" id="year-unit" role="group" aria-label="Unit">
        <button data-unit="kwh" class="active">kWh</button>
        <button data-unit="cost">&pound;</button>
      </div>
    </div>
    <div id="year-tiles"></div>
    <div class="chart"><canvas id="year-chart"></canvas></div>
    <p class="muted">Shaded bars are forecast. Projections assume today's tariff.</p>
  </section>
```

Run: `.venv/bin/pytest tests/test_app.py::test_index_serves_dashboard_when_configured -v`
Expected: PASS.

- [ ] **Step 3: Render tiles and chart in `app.js`**

Add `yearUnit: "kwh"` to the `state` object literal and two module-level variables next to the existing chart handles:

```js
const state = { days: 30, unit: "kwh", yearUnit: "kwh", forecastFuel: null, fuels: [] };
let yearChart = null;
let yearlyData = null;
```

Add after `renderHistory()`:

```js
function monthLabel(key) {
  return new Date(key + "-15T12:00:00Z").toLocaleDateString("en-GB", { month: "short", year: "2-digit" });
}

function renderYearTiles() {
  const tiles = (t) => [
    ["Last 365 days", t.last_365],
    ["Next 365 days (projected)", t.next_365],
    [`${t.calendar_prev.year} (calendar)`, t.calendar_prev],
    [`${t.calendar_current.year} (projected)`, t.calendar_current],
  ];
  $("#year-tiles").innerHTML = state.fuels
    .filter((fuel) => yearlyData.fuels[fuel])
    .map((fuel) => `
      <div class="fuel-row">
        <h2><span class="swatch" style="background:${cssVar(FUEL_META[fuel].colorVar)}"></span>${FUEL_META[fuel].label}</h2>
        <div class="tile-grid">${tiles(yearlyData.fuels[fuel].totals)
          .map(([label, t]) => `<div class="tile">
            <div class="tile-label">${label}</div>
            <div class="tile-value">${fmt.kwh(t.kwh)}</div>
            <div class="tile-sub">${fmt.cost(t.cost_pence)}</div>
          </div>`)
          .join("")}</div>
      </div>`)
    .join("");
}

function renderYearChart() {
  const key = state.yearUnit === "cost" ? "cost_pence" : "kwh";
  const monthKeys = [...new Set(
    state.fuels.flatMap((f) => (yearlyData.fuels[f]?.months ?? []).map((m) => m.month))
  )].sort();
  if (!monthKeys.length) return;
  const datasets = state.fuels.flatMap((fuel) => {
    const months = yearlyData.fuels[fuel]?.months ?? [];
    const color = cssVar(FUEL_META[fuel].colorVar);
    const pick = (isFc) => monthKeys.map((mk) => {
      const m = months.find((x) => x.month === mk && x.forecast === isFc);
      return m ? m[key] : null;
    });
    const common = { stack: fuel, borderRadius: 4, maxBarThickness: 24, barPercentage: 0.9, categoryPercentage: 0.8 };
    return [
      { label: FUEL_META[fuel].label, data: pick(false), backgroundColor: color, ...common },
      { label: `${FUEL_META[fuel].label} (forecast)`, data: pick(true), backgroundColor: hexToRgba(color, 0.35), ...common },
    ];
  });
  const options = baseOptions(state.yearUnit === "cost" ? fmt.cost : fmt.kwh);
  options.scales.x.stacked = true;
  options.scales.y.stacked = true;
  yearChart?.destroy();
  yearChart = new Chart($("#year-chart"), {
    type: "bar",
    data: { labels: monthKeys.map(monthLabel), datasets },
    options,
  });
}

async function renderYear() {
  const res = await fetch("/api/yearly");
  yearlyData = res.ok ? await res.json() : { fuels: {} };
  const hasData = state.fuels.some((fuel) => yearlyData.fuels[fuel]);
  $("#year-card").hidden = !hasData;
  if (!hasData) return;
  renderYearTiles();
  renderYearChart();
}
```

In `loadAll()`, extend the final render line to include the year card:

```js
  await Promise.all([renderHistory(), renderForecast(), renderYear()]);
```

In `wireControls()`, wire the unit toggle (tiles show both units, so only the chart re-renders):

```js
  $("#year-unit").querySelectorAll("button").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.yearUnit = btn.dataset.unit;
      $("#year-unit").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      if (yearlyData) renderYearChart();
    })
  );
```

- [ ] **Step 4: Update the README feature line**

In `README.md`, change the intro sentence (lines 3–4) to:

```markdown
A local dashboard for your Octopus Energy (UK) smart meter data: historical
electricity + gas usage in kWh and estimated £, yearly totals, plus 30-day
and 12-month forecasts.
```

- [ ] **Step 5: Run the full suite and check the page renders**

Run: `.venv/bin/pytest -v`
Expected: all PASS.

Then start the app against the real DB and eyeball the Year card (tiles populated, monthly bars with translucent forecast months, kWh/£ toggle works, current month stacked):

```bash
.venv/bin/uvicorn --factory octopus_usage.app:create_app --port 8000
# open http://localhost:8000, then Ctrl-C
```

If the machine has no `.env`/data, skip the visual check and rely on the test suite.

- [ ] **Step 6: Commit**

```bash
git add octopus_usage/static/index.html octopus_usage/static/app.js tests/test_app.py README.md
git commit -m "feat: Year card with yearly totals and monthly forecast chart"
```
