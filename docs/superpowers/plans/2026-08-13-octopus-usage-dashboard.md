# Octopus Usage Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A locally-run FastAPI web dashboard that syncs half-hourly Octopus Energy consumption (electricity + gas) into SQLite, shows historical usage in kWh/£, and forecasts the next 30 days.

**Architecture:** FastAPI backend + SQLite cache + vanilla-JS frontend with vendored Chart.js. A thin `httpx` client wraps the Octopus REST API; `sync.py` backfills then incrementally updates readings and tariff rates; `costs.py` matches per-half-hour rates; `forecast.py` is a pure-Python seasonal/day-of-week model. Spec: `docs/superpowers/specs/2026-08-13-octopus-usage-dashboard-design.md`.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, httpx, python-dotenv, pytest, Chart.js 4 (vendored), SQLite (stdlib `sqlite3`).

## Global Constraints

- Python 3.11+ (needs stdlib `zoneinfo` and `datetime.fromisoformat` offset parsing).
- Runtime deps ONLY: `fastapi`, `uvicorn[standard]`, `httpx`, `python-dotenv`. Tests: `pytest`. Forecast uses stdlib `statistics` — no pandas/numpy/ML libraries.
- No CDN/network assets at runtime: Chart.js is vendored at `octopus_usage/static/chart.umd.js`.
- All timestamps stored as UTC ISO-8601 with `+00:00` offset via `db.to_utc_iso()`; this makes string comparison chronological. Never store a raw API timestamp.
- Days are grouped by **Europe/London local date**; a day is `complete` when it has ≥ 46 half-hour intervals (DST days have 46 or 50).
- Money is **pence, inc. VAT** everywhere in Python; the frontend formats £.
- Fuel identifiers are exactly the strings `"electricity"` and `"gas"` (`db.FUELS`).
- Gas m³→kWh factor: `1.02264 × 39.5 ÷ 3.6` (volume correction × calorific value ÷ MJ-per-kWh).
- Chart palette (validated with the dataviz palette validator, both modes PASS): electricity `#2a78d6` light / `#3987e5` dark; gas `#eb6834` light / `#d95926` dark. Surfaces `#fcfcfb` light / `#1a1a19` dark.
- Run commands use the project venv explicitly: `.venv/bin/pytest`, `.venv/bin/uvicorn`.
- Commit after every task (messages given per task).

## File Structure

```
requirements.txt
.gitignore
.env.example
README.md                    (Task 9)
octopus_usage/
  __init__.py
  config.py                  (Task 1)  .env loading
  db.py                      (Task 2)  SQLite schema + queries
  octopus_client.py          (Task 3)  REST wrapper: auth, paging, retry
  costs.py                   (Task 4)  gas conversion, rate matching, daily costs
  sync.py                    (Task 5)  meter discovery, backfill/incremental sync
  forecast.py                (Task 6)  seasonal + day-of-week forecast
  app.py                     (Task 7)  FastAPI routes, setup page
  static/                    (Task 8)  index.html, style.css, app.js, chart.umd.js
tests/
  __init__.py
  test_config.py             (Task 1)
  test_db.py                 (Task 2)
  test_octopus_client.py     (Task 3)
  test_costs.py              (Task 4)
  fixtures.py                (Task 5)  canned API responses + mock transport handler
  test_sync.py               (Task 5)
  test_forecast.py           (Task 6)
  test_app.py                (Tasks 7–8)
```

---

### Task 1: Scaffolding & config

**Files:**
- Create: `requirements.txt`, `.gitignore`, `.env.example`, `octopus_usage/__init__.py`, `octopus_usage/config.py`
- Test: `tests/__init__.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `config.Config` dataclass (`api_key: str`, `account_number: str`, `db_path: str = "octopus_usage.db"`), `config.load_config(env_file=".env") -> Config`, `config.ConfigError(Exception)`. Later tasks import these exact names.

- [ ] **Step 1: Create venv and project files**

```bash
cd /Users/matty/Development/octopus_usage
python3 -m venv .venv
mkdir -p octopus_usage tests
touch octopus_usage/__init__.py tests/__init__.py
```

`requirements.txt`:

```
fastapi>=0.110
uvicorn[standard]>=0.29
httpx>=0.27
python-dotenv>=1.0
pytest>=8.0
```

`.gitignore`:

```
.venv/
__pycache__/
.pytest_cache/
.env
*.db
```

`.env.example`:

```
# Octopus dashboard -> Personal details -> API access
OCTOPUS_API_KEY=sk_live_xxxxxxxxxxxxxxxx
OCTOPUS_ACCOUNT_NUMBER=A-XXXXXXXX
```

Then install: `.venv/bin/pip install -r requirements.txt`

- [ ] **Step 2: Write the failing test**

`tests/test_config.py`:

```python
import pytest

from octopus_usage.config import ConfigError, load_config


def test_load_config_reads_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("OCTOPUS_API_KEY", raising=False)
    monkeypatch.delenv("OCTOPUS_ACCOUNT_NUMBER", raising=False)
    env = tmp_path / ".env"
    env.write_text("OCTOPUS_API_KEY=sk_live_abc\nOCTOPUS_ACCOUNT_NUMBER=A-12345678\n")
    cfg = load_config(str(env))
    assert cfg.api_key == "sk_live_abc"
    assert cfg.account_number == "A-12345678"
    assert cfg.db_path == "octopus_usage.db"


def test_load_config_missing_credentials_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("OCTOPUS_API_KEY", raising=False)
    monkeypatch.delenv("OCTOPUS_ACCOUNT_NUMBER", raising=False)
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "missing.env"))
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octopus_usage.config'`

- [ ] **Step 4: Write minimal implementation**

`octopus_usage/config.py`:

```python
"""Configuration loaded from environment / .env file."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


@dataclass
class Config:
    api_key: str
    account_number: str
    db_path: str = "octopus_usage.db"


def load_config(env_file: str | None = ".env") -> Config:
    if env_file and Path(env_file).exists():
        load_dotenv(env_file, override=True)
    api_key = os.environ.get("OCTOPUS_API_KEY", "").strip()
    account = os.environ.get("OCTOPUS_ACCOUNT_NUMBER", "").strip()
    if not api_key or not account:
        raise ConfigError(
            "Set OCTOPUS_API_KEY and OCTOPUS_ACCOUNT_NUMBER in a .env file "
            "(see .env.example; values are under Personal details -> API access "
            "in your Octopus dashboard)."
        )
    return Config(
        api_key=api_key,
        account_number=account,
        db_path=os.environ.get("OCTOPUS_DB_PATH", "octopus_usage.db"),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .gitignore .env.example octopus_usage tests
git commit -m "feat: scaffold project with env-based config"
```

---

### Task 2: Storage layer (db.py)

**Files:**
- Create: `octopus_usage/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces (all take `conn: sqlite3.Connection` first unless noted):
  - `FUELS = ("electricity", "gas")`, `LONDON = ZoneInfo("Europe/London")`
  - `to_utc_iso(ts: str) -> str` — module function, no conn
  - `connect(path: str) -> sqlite3.Connection`
  - `upsert_readings(conn, fuel, rows) -> int` — rows: dicts with `interval_start`, `interval_end`, `consumption`, `consumption_kwh`
  - `readings(conn, fuel, start: str | None = None) -> list[sqlite3.Row]`
  - `latest_interval_start(conn, fuel) -> str | None`, `earliest_interval_start(conn, fuel) -> str | None`
  - `daily_totals(conn, fuel) -> list[dict]` — `{"date": date, "kwh": float, "intervals": int, "complete": bool}` sorted by date
  - `upsert_rates(conn, fuel, tariff_code, rows) -> int` / `upsert_standing_charges(conn, fuel, tariff_code, rows) -> int` — rows: API dicts with `valid_from`, `valid_to`, `value_inc_vat`
  - `rates_for(conn, fuel) -> list[sqlite3.Row]` (columns incl. `unit_rate_inc_vat`), `standing_charges_for(conn, fuel) -> list[sqlite3.Row]` (columns incl. `charge_inc_vat`) — both ordered by `valid_from`
  - `meta_get(conn, key, default=None) -> str | None`, `meta_set(conn, key, value) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_db.py`:

```python
from datetime import date, datetime, timedelta

import pytest

from octopus_usage import db


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "test.db"))


def rows_for_day(day_iso, n=48, consumption=0.5):
    t = datetime.fromisoformat(day_iso + "T00:00:00+00:00")
    return [
        {
            "interval_start": (t + timedelta(minutes=30 * i)).isoformat(),
            "interval_end": (t + timedelta(minutes=30 * (i + 1))).isoformat(),
            "consumption": consumption,
            "consumption_kwh": consumption,
        }
        for i in range(n)
    ]


def test_to_utc_iso_normalises_offsets():
    assert db.to_utc_iso("2026-06-01T01:30:00+01:00") == "2026-06-01T00:30:00+00:00"
    assert db.to_utc_iso("2026-06-01T00:30:00Z") == "2026-06-01T00:30:00+00:00"


def test_upsert_readings_is_idempotent(conn):
    rows = rows_for_day("2026-08-01")
    db.upsert_readings(conn, "electricity", rows)
    db.upsert_readings(conn, "electricity", rows)
    assert len(db.readings(conn, "electricity")) == 48


def test_latest_and_earliest_interval_start(conn):
    db.upsert_readings(conn, "electricity", rows_for_day("2026-08-01"))
    db.upsert_readings(conn, "electricity", rows_for_day("2026-08-02"))
    assert db.earliest_interval_start(conn, "electricity") == "2026-08-01T00:00:00+00:00"
    assert db.latest_interval_start(conn, "electricity") == "2026-08-02T23:30:00+00:00"
    assert db.latest_interval_start(conn, "gas") is None


def test_daily_totals_groups_by_london_date_and_flags_completeness(conn):
    # August is BST (UTC+1): the 23:00Z and 23:30Z intervals of Aug 1 belong to
    # Aug 2 in London. So Aug 1 keeps 46 intervals (complete, kwh = 46 * 0.5)
    # and Aug 2 gets those 2 plus the 4 seeded below = 6 (incomplete).
    db.upsert_readings(conn, "electricity", rows_for_day("2026-08-01", n=48))
    db.upsert_readings(conn, "electricity", rows_for_day("2026-08-02", n=4))
    totals = db.daily_totals(conn, "electricity")
    assert [t["date"] for t in totals] == [date(2026, 8, 1), date(2026, 8, 2)]
    assert totals[0]["intervals"] == 46 and totals[0]["complete"]
    assert totals[0]["kwh"] == pytest.approx(23.0)
    assert totals[1]["intervals"] == 6 and not totals[1]["complete"]


def test_upsert_rates_maps_value_key(conn):
    db.upsert_rates(
        conn,
        "electricity",
        "E-1R-VAR-22-11-01-C",
        [{"valid_from": "2026-01-01T00:00:00Z", "valid_to": None, "value_inc_vat": 28.0}],
    )
    rows = db.rates_for(conn, "electricity")
    assert rows[0]["unit_rate_inc_vat"] == 28.0
    assert rows[0]["valid_to"] is None
    assert rows[0]["valid_from"] == "2026-01-01T00:00:00+00:00"


def test_upsert_standing_charges(conn):
    db.upsert_standing_charges(
        conn,
        "gas",
        "G-1R-VAR-22-11-01-C",
        [{"valid_from": "2026-01-01T00:00:00Z", "valid_to": None, "value_inc_vat": 29.6}],
    )
    assert db.standing_charges_for(conn, "gas")[0]["charge_inc_vat"] == 29.6


def test_meta_roundtrip(conn):
    assert db.meta_get(conn, "x") is None
    assert db.meta_get(conn, "x", "fallback") == "fallback"
    db.meta_set(conn, "x", "1")
    db.meta_set(conn, "x", "2")
    assert db.meta_get(conn, "x") == "2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octopus_usage.db'`

- [ ] **Step 3: Write the implementation**

`octopus_usage/db.py`:

```python
"""SQLite storage for readings, tariff rates, and app metadata.

All timestamps are stored as UTC ISO-8601 strings with a +00:00 offset
(see to_utc_iso), so lexicographic comparison equals chronological order.
"""
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

FUELS = ("electricity", "gas")
LONDON = ZoneInfo("Europe/London")

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
  fuel TEXT NOT NULL,
  interval_start TEXT NOT NULL,
  interval_end TEXT NOT NULL,
  consumption REAL NOT NULL,
  consumption_kwh REAL NOT NULL,
  PRIMARY KEY (fuel, interval_start)
);
CREATE TABLE IF NOT EXISTS rates (
  fuel TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  unit_rate_inc_vat REAL NOT NULL,
  PRIMARY KEY (fuel, valid_from)
);
CREATE TABLE IF NOT EXISTS standing_charges (
  fuel TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  charge_inc_vat REAL NOT NULL,
  PRIMARY KEY (fuel, valid_from)
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def to_utc_iso(ts: str) -> str:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).isoformat()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_readings(conn, fuel, rows) -> int:
    with conn:
        conn.executemany(
            "INSERT INTO readings"
            " (fuel, interval_start, interval_end, consumption, consumption_kwh)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(fuel, interval_start) DO UPDATE SET"
            " interval_end=excluded.interval_end,"
            " consumption=excluded.consumption,"
            " consumption_kwh=excluded.consumption_kwh",
            [
                (fuel, to_utc_iso(r["interval_start"]), to_utc_iso(r["interval_end"]),
                 r["consumption"], r["consumption_kwh"])
                for r in rows
            ],
        )
    return len(rows)


def readings(conn, fuel, start=None):
    if start:
        cur = conn.execute(
            "SELECT * FROM readings WHERE fuel=? AND interval_start>=?"
            " ORDER BY interval_start",
            (fuel, start),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM readings WHERE fuel=? ORDER BY interval_start", (fuel,)
        )
    return cur.fetchall()


def latest_interval_start(conn, fuel):
    row = conn.execute(
        "SELECT MAX(interval_start) AS m FROM readings WHERE fuel=?", (fuel,)
    ).fetchone()
    return row["m"]


def earliest_interval_start(conn, fuel):
    row = conn.execute(
        "SELECT MIN(interval_start) AS m FROM readings WHERE fuel=?", (fuel,)
    ).fetchone()
    return row["m"]


def daily_totals(conn, fuel):
    """Group half-hourly readings by Europe/London calendar date.

    A day is complete with >= 46 intervals (DST days have 46 or 50)."""
    days = {}
    for r in readings(conn, fuel):
        d = datetime.fromisoformat(r["interval_start"]).astimezone(LONDON).date()
        day = days.setdefault(d, {"date": d, "kwh": 0.0, "intervals": 0})
        day["kwh"] += r["consumption_kwh"]
        day["intervals"] += 1
    out = []
    for d in sorted(days):
        day = days[d]
        day["complete"] = day["intervals"] >= 46
        out.append(day)
    return out


def _upsert_tariff_rows(conn, table, value_column, fuel, tariff_code, rows) -> int:
    with conn:
        conn.executemany(
            f"INSERT INTO {table} (fuel, tariff_code, valid_from, valid_to, {value_column})"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(fuel, valid_from) DO UPDATE SET"
            " tariff_code=excluded.tariff_code, valid_to=excluded.valid_to,"
            f" {value_column}=excluded.{value_column}",
            [
                (fuel, tariff_code, to_utc_iso(r["valid_from"]),
                 to_utc_iso(r["valid_to"]) if r.get("valid_to") else None,
                 r["value_inc_vat"])
                for r in rows
            ],
        )
    return len(rows)


def upsert_rates(conn, fuel, tariff_code, rows) -> int:
    return _upsert_tariff_rows(conn, "rates", "unit_rate_inc_vat", fuel, tariff_code, rows)


def upsert_standing_charges(conn, fuel, tariff_code, rows) -> int:
    return _upsert_tariff_rows(conn, "standing_charges", "charge_inc_vat", fuel, tariff_code, rows)


def rates_for(conn, fuel):
    return conn.execute(
        "SELECT * FROM rates WHERE fuel=? ORDER BY valid_from", (fuel,)
    ).fetchall()


def standing_charges_for(conn, fuel):
    return conn.execute(
        "SELECT * FROM standing_charges WHERE fuel=? ORDER BY valid_from", (fuel,)
    ).fetchall()


def meta_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn, key, value):
    with conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add octopus_usage/db.py tests/test_db.py
git commit -m "feat: SQLite storage for readings, rates and metadata"
```

---

### Task 3: Octopus API client

**Files:**
- Create: `octopus_usage/octopus_client.py`
- Test: `tests/test_octopus_client.py`

**Interfaces:**
- Produces:
  - `BASE_URL = "https://api.octopus.energy"`, `OctopusError(Exception)`
  - `OctopusClient(api_key, transport=None, backoff=1.0, max_tries=3)`
  - `.account(account_number) -> dict`
  - `.consumption(fuel, mpxn, serial, period_from=None) -> list[dict]` — dicts have `consumption`, `interval_start`, `interval_end`
  - `.unit_rates(product_code, tariff_code, fuel, period_from=None) -> list[dict]` — dicts have `value_inc_vat`, `valid_from`, `valid_to`
  - `.standing_charges(product_code, tariff_code, fuel, period_from=None) -> list[dict]`
- `transport` is an `httpx` transport for tests (`httpx.MockTransport`); `backoff=0` disables retry sleeps in tests.

- [ ] **Step 1: Write the failing tests**

`tests/test_octopus_client.py`:

```python
import httpx
import pytest

from octopus_usage.octopus_client import BASE_URL, OctopusClient, OctopusError


def make_client(handler, **kw):
    return OctopusClient("sk_test", transport=httpx.MockTransport(handler), backoff=0, **kw)


def empty_page(request):
    return httpx.Response(200, json={"count": 0, "next": None, "results": []})


def test_sends_basic_auth_and_follows_pagination():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json={"count": 3, "next": None, "results": [{"n": 3}]})
        return httpx.Response(
            200,
            json={
                "count": 3,
                "next": f"{BASE_URL}{request.url.path}?page=2",
                "results": [{"n": 1}, {"n": 2}],
            },
        )

    rows = make_client(handler).consumption("electricity", "1200000000000", "ELEC001")
    assert [r["n"] for r in rows] == [1, 2, 3]
    assert seen[0].headers["authorization"].startswith("Basic ")
    assert seen[0].url.params["page_size"] == "25000"
    assert "/v1/electricity-meter-points/1200000000000/meters/ELEC001/consumption/" in str(seen[0].url)


def test_gas_consumption_uses_gas_path_and_period_from():
    seen = []

    def handler(request):
        seen.append(request)
        return empty_page(request)

    make_client(handler).consumption("gas", "3000000000", "GAS001", period_from="2026-01-01T00:00:00+00:00")
    assert "/v1/gas-meter-points/3000000000/meters/GAS001/consumption/" in str(seen[0].url)
    assert seen[0].url.params["period_from"] == "2026-01-01T00:00:00+00:00"


def test_unit_rates_and_standing_charges_paths():
    seen = []

    def handler(request):
        seen.append(request)
        return empty_page(request)

    client = make_client(handler)
    client.unit_rates("VAR-22-11-01", "E-1R-VAR-22-11-01-C", "electricity")
    client.standing_charges("VAR-22-11-01", "G-1R-VAR-22-11-01-C", "gas")
    assert "/v1/products/VAR-22-11-01/electricity-tariffs/E-1R-VAR-22-11-01-C/standard-unit-rates/" in str(seen[0].url)
    assert "/v1/products/VAR-22-11-01/gas-tariffs/G-1R-VAR-22-11-01-C/standing-charges/" in str(seen[1].url)


def test_retries_on_500_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500)
        return empty_page(request)

    assert make_client(handler).consumption("electricity", "m", "s") == []
    assert calls["n"] == 2


def test_gives_up_after_max_tries():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500)

    with pytest.raises(OctopusError):
        make_client(handler).account("A-1")
    assert calls["n"] == 3


def test_4xx_raises_without_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401)

    with pytest.raises(OctopusError):
        make_client(handler).account("A-1")
    assert calls["n"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_octopus_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octopus_usage.octopus_client'`

- [ ] **Step 3: Write the implementation**

`octopus_usage/octopus_client.py`:

```python
"""Thin wrapper over the Octopus Energy REST API.

Auth is HTTP Basic with the API key as username and a blank password.
Retries 429/5xx/transport errors with exponential backoff; other 4xx fail fast.
"""
import time

import httpx

BASE_URL = "https://api.octopus.energy"
PAGE_SIZE = 25000


class OctopusError(Exception):
    pass


class OctopusClient:
    def __init__(self, api_key, transport=None, backoff=1.0, max_tries=3):
        self._backoff = backoff
        self._max_tries = max_tries
        self._client = httpx.Client(
            base_url=BASE_URL, auth=(api_key, ""), timeout=30.0, transport=transport
        )

    def _get(self, url, params=None):
        last_error = None
        for attempt in range(self._max_tries):
            try:
                resp = self._client.get(url, params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_error = OctopusError(f"HTTP {resp.status_code} from {url}")
                else:
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError as e:
                raise OctopusError(str(e)) from e
            except httpx.TransportError as e:
                last_error = OctopusError(str(e))
            if attempt < self._max_tries - 1:
                time.sleep(self._backoff * 2 ** attempt)
        raise last_error

    def _paged(self, url, params):
        data = self._get(url, params)
        results = list(data["results"])
        while data.get("next"):
            data = self._get(data["next"])
            results.extend(data["results"])
        return results

    def account(self, account_number):
        return self._get(f"/v1/accounts/{account_number}/")

    def consumption(self, fuel, mpxn, serial, period_from=None):
        kind = "electricity-meter-points" if fuel == "electricity" else "gas-meter-points"
        params = {"page_size": PAGE_SIZE, "order_by": "period"}
        if period_from:
            params["period_from"] = period_from
        return self._paged(f"/v1/{kind}/{mpxn}/meters/{serial}/consumption/", params)

    def unit_rates(self, product_code, tariff_code, fuel, period_from=None):
        return self._tariff_data(product_code, tariff_code, fuel, "standard-unit-rates", period_from)

    def standing_charges(self, product_code, tariff_code, fuel, period_from=None):
        return self._tariff_data(product_code, tariff_code, fuel, "standing-charges", period_from)

    def _tariff_data(self, product_code, tariff_code, fuel, endpoint, period_from):
        kind = "electricity-tariffs" if fuel == "electricity" else "gas-tariffs"
        params = {"page_size": 1500}
        if period_from:
            params["period_from"] = period_from
        return self._paged(f"/v1/products/{product_code}/{kind}/{tariff_code}/{endpoint}/", params)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_octopus_client.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add octopus_usage/octopus_client.py tests/test_octopus_client.py
git commit -m "feat: Octopus API client with paging and retry"
```

---

### Task 4: Cost estimation (costs.py)

**Files:**
- Create: `octopus_usage/costs.py`
- Test: `tests/test_costs.py`

**Interfaces:**
- Consumes: `db.readings`, `db.rates_for`, `db.standing_charges_for`, `db.LONDON` (Task 2).
- Produces:
  - `M3_TO_KWH` (float constant), `m3_to_kwh(volume) -> float`
  - `gas_looks_like_m3(rows) -> bool` — rows are raw API reading dicts
  - `daily_costs(conn, fuel) -> list[dict]` — `{"date": date, "kwh": float, "cost_pence": float | None, "complete": bool}` sorted by date; `cost_pence` is `None` when rates/standing charges don't cover the day
  - `current_unit_rate(conn, fuel) -> float | None` — pence/kWh
  - `current_standing_charge(conn, fuel) -> float | None` — pence/day

- [ ] **Step 1: Write the failing tests**

`tests/test_costs.py` (uses winter dates — GMT — so UTC-aligned days group cleanly into 48-interval London days):

```python
from datetime import date, datetime, timedelta

import pytest

from octopus_usage import costs, db


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "test.db"))


def rows_for_day(day_iso, n=48, consumption=0.5):
    t = datetime.fromisoformat(day_iso + "T00:00:00+00:00")
    return [
        {
            "interval_start": (t + timedelta(minutes=30 * i)).isoformat(),
            "interval_end": (t + timedelta(minutes=30 * (i + 1))).isoformat(),
            "consumption": consumption,
            "consumption_kwh": consumption,
        }
        for i in range(n)
    ]


def flat_rate(conn, fuel, pence, sc=50.0, valid_from="2026-01-01T00:00:00Z"):
    db.upsert_rates(conn, fuel, "T", [{"valid_from": valid_from, "valid_to": None, "value_inc_vat": pence}])
    db.upsert_standing_charges(conn, fuel, "T", [{"valid_from": valid_from, "valid_to": None, "value_inc_vat": sc}])


def test_m3_to_kwh_factor():
    assert costs.m3_to_kwh(1.0) == pytest.approx(11.2151, abs=1e-3)


def test_gas_heuristic_m3_vs_kwh():
    m3_rows = [{"interval_start": f"2026-01-15T{h:02d}:00:00Z", "consumption": 0.1} for h in range(24)]
    kwh_rows = [{"interval_start": f"2026-01-15T{h:02d}:00:00Z", "consumption": 1.5} for h in range(24)]
    assert costs.gas_looks_like_m3(m3_rows) is True      # 2.4/day
    assert costs.gas_looks_like_m3(kwh_rows) is False    # 36/day
    assert costs.gas_looks_like_m3([]) is False


def test_daily_costs_flat_rate(conn):
    db.upsert_readings(conn, "electricity", rows_for_day("2026-01-15"))
    flat_rate(conn, "electricity", 28.0, sc=50.0)
    daily = costs.daily_costs(conn, "electricity")
    assert daily[0]["date"] == date(2026, 1, 15)
    assert daily[0]["kwh"] == pytest.approx(24.0)
    assert daily[0]["cost_pence"] == pytest.approx(24.0 * 28.0 + 50.0)
    assert daily[0]["complete"] is True


def test_daily_costs_time_of_use(conn):
    db.upsert_readings(conn, "electricity", rows_for_day("2026-01-15"))
    db.upsert_rates(conn, "electricity", "T", [
        {"valid_from": "2026-01-15T00:00:00Z", "valid_to": "2026-01-15T12:00:00Z", "value_inc_vat": 10.0},
        {"valid_from": "2026-01-15T12:00:00Z", "valid_to": None, "value_inc_vat": 20.0},
    ])
    db.upsert_standing_charges(conn, "electricity", "T",
                               [{"valid_from": "2026-01-01T00:00:00Z", "valid_to": None, "value_inc_vat": 50.0}])
    daily = costs.daily_costs(conn, "electricity")
    # 12 kWh before noon @10p + 12 kWh after @20p + 50p standing
    assert daily[0]["cost_pence"] == pytest.approx(12 * 10.0 + 12 * 20.0 + 50.0)


def test_daily_costs_none_when_rates_missing(conn):
    db.upsert_readings(conn, "electricity", rows_for_day("2025-12-01"))
    flat_rate(conn, "electricity", 28.0, valid_from="2026-01-01T00:00:00Z")
    daily = costs.daily_costs(conn, "electricity")
    assert daily[0]["kwh"] == pytest.approx(24.0)
    assert daily[0]["cost_pence"] is None


def test_current_unit_rate_averages_recent_readings(conn):
    # Old readings priced at 10p, the newest day at 30p -> the last-7-days
    # average over one seeded day is 30.
    db.upsert_readings(conn, "electricity", rows_for_day("2026-01-15"))
    db.upsert_rates(conn, "electricity", "T", [
        {"valid_from": "2026-01-01T00:00:00Z", "valid_to": "2026-01-15T00:00:00Z", "value_inc_vat": 10.0},
        {"valid_from": "2026-01-15T00:00:00Z", "valid_to": None, "value_inc_vat": 30.0},
    ])
    assert costs.current_unit_rate(conn, "electricity") == pytest.approx(30.0)


def test_current_unit_rate_falls_back_to_newest_rate(conn):
    flat_rate(conn, "electricity", 28.0)
    assert costs.current_unit_rate(conn, "electricity") == pytest.approx(28.0)
    assert costs.current_unit_rate(conn, "gas") is None


def test_current_standing_charge(conn):
    flat_rate(conn, "gas", 7.0, sc=29.6)
    assert costs.current_standing_charge(conn, "gas") == pytest.approx(29.6)
    assert costs.current_standing_charge(conn, "electricity") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_costs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octopus_usage.costs'`

- [ ] **Step 3: Write the implementation**

`octopus_usage/costs.py`:

```python
"""Tariff-rate matching and cost estimation. Money is pence, inc. VAT."""
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime

from octopus_usage import db

M3_TO_KWH = 1.02264 * 39.5 / 3.6  # volume correction x calorific value / MJ-per-kWh


def m3_to_kwh(volume):
    return volume * M3_TO_KWH


def gas_looks_like_m3(rows):
    """SMETS2 gas meters report m3 (a few units/day); SMETS1 report kWh (tens/day).

    rows are raw API reading dicts. Mean daily total below 15 -> treat as m3."""
    per_day = defaultdict(float)
    for r in rows:
        per_day[r["interval_start"][:10]] += r["consumption"]
    if not per_day:
        return False
    return (sum(per_day.values()) / len(per_day)) < 15.0


def _lookup(rows, starts, column, ts):
    """Value of the row whose [valid_from, valid_to) covers UTC-ISO ts, else None."""
    i = bisect_right(starts, ts) - 1
    if i < 0:
        return None
    row = rows[i]
    if row["valid_to"] is not None and ts >= row["valid_to"]:
        return None
    return row[column]


def daily_costs(conn, fuel):
    """Daily kWh and estimated cost. cost_pence is None where tariff data has gaps."""
    rate_rows = db.rates_for(conn, fuel)
    rate_starts = [r["valid_from"] for r in rate_rows]
    sc_rows = db.standing_charges_for(conn, fuel)
    sc_starts = [r["valid_from"] for r in sc_rows]
    days = {}
    for r in db.readings(conn, fuel):
        d = datetime.fromisoformat(r["interval_start"]).astimezone(db.LONDON).date()
        day = days.setdefault(d, {"kwh": 0.0, "energy": 0.0, "priced": True, "intervals": 0})
        day["kwh"] += r["consumption_kwh"]
        day["intervals"] += 1
        rate = _lookup(rate_rows, rate_starts, "unit_rate_inc_vat", r["interval_start"])
        if rate is None:
            day["priced"] = False
        else:
            day["energy"] += r["consumption_kwh"] * rate
    out = []
    for d in sorted(days):
        day = days[d]
        noon = f"{d.isoformat()}T12:00:00+00:00"  # midday sidesteps midnight boundaries
        sc = _lookup(sc_rows, sc_starts, "charge_inc_vat", noon)
        priced = day["priced"] and sc is not None
        out.append({
            "date": d,
            "kwh": day["kwh"],
            "cost_pence": (day["energy"] + sc) if priced else None,
            "complete": day["intervals"] >= 46,
        })
    return out


def current_unit_rate(conn, fuel):
    """Mean rate matched to the last 7 days of readings; falls back to the newest rate."""
    rate_rows = db.rates_for(conn, fuel)
    if not rate_rows:
        return None
    rate_starts = [r["valid_from"] for r in rate_rows]
    recent = db.readings(conn, fuel)[-336:]  # 48 half-hours x 7 days
    matched = [
        _lookup(rate_rows, rate_starts, "unit_rate_inc_vat", r["interval_start"])
        for r in recent
    ]
    matched = [m for m in matched if m is not None]
    if matched:
        return sum(matched) / len(matched)
    return rate_rows[-1]["unit_rate_inc_vat"]


def current_standing_charge(conn, fuel):
    sc_rows = db.standing_charges_for(conn, fuel)
    return sc_rows[-1]["charge_inc_vat"] if sc_rows else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_costs.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add octopus_usage/costs.py tests/test_costs.py
git commit -m "feat: cost estimation with per-interval rate matching and gas conversion"
```

---

### Task 5: Sync (sync.py + shared fixtures)

**Files:**
- Create: `octopus_usage/sync.py`, `tests/fixtures.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `db.*` (Task 2), `OctopusClient` methods (Task 3), `costs.m3_to_kwh` / `costs.gas_looks_like_m3` (Task 4).
- Produces:
  - `BACKFILL_DAYS = 730`
  - `product_code_from_tariff(tariff_code) -> str`
  - `discover_meters(account_data) -> dict` — `{"electricity": {"mpxn", "serials", "agreements"}, "gas": {...}}`; missing fuels absent; export electricity points skipped
  - `sync_fuel_readings(conn, client, fuel, meter, now=None) -> int`
  - `sync_fuel_rates(conn, client, fuel, meter) -> int`
  - `full_sync(conn, client, account_number, now=None) -> dict` — `{"synced_at": iso, "fuels": {fuel: count}}`
- `tests/fixtures.py` produces: `ACCOUNT` (dict), `consumption_results(values, start=...)`, `RATE_RESULTS`, `SC_RESULTS`, `make_handler(elec_values=..., gas_values=...)` returning an `httpx.MockTransport`-compatible handler. Task 7's tests import `make_handler`.

- [ ] **Step 1: Write the shared fixtures**

`tests/fixtures.py`:

```python
"""Canned Octopus API responses and a mock HTTP handler shared across tests."""
from datetime import datetime, timedelta

import httpx

ACCOUNT = {
    "number": "A-12345678",
    "properties": [
        {
            "electricity_meter_points": [
                {
                    "mpan": "1200000000000",
                    "is_export": False,
                    "meters": [{"serial_number": "ELEC001"}],
                    "agreements": [
                        {
                            "tariff_code": "E-1R-VAR-22-11-01-C",
                            "valid_from": "2023-01-01T00:00:00Z",
                            "valid_to": None,
                        }
                    ],
                }
            ],
            "gas_meter_points": [
                {
                    "mprn": "3000000000",
                    "meters": [{"serial_number": "GAS001"}],
                    "agreements": [
                        {
                            "tariff_code": "G-1R-VAR-22-11-01-C",
                            "valid_from": "2023-01-01T00:00:00Z",
                            "valid_to": None,
                        }
                    ],
                }
            ],
        }
    ],
}

RATE_RESULTS = [{"value_inc_vat": 28.0, "valid_from": "2023-01-01T00:00:00Z", "valid_to": None}]
SC_RESULTS = [{"value_inc_vat": 50.0, "valid_from": "2023-01-01T00:00:00Z", "valid_to": None}]


def consumption_results(values, start="2026-08-01T00:00:00Z"):
    """Consecutive half-hourly readings taking their values from `values`."""
    t = datetime.fromisoformat(start.replace("Z", "+00:00"))
    out = []
    for v in values:
        out.append({
            "consumption": v,
            "interval_start": t.isoformat(),
            "interval_end": (t + timedelta(minutes=30)).isoformat(),
        })
        t += timedelta(minutes=30)
    return out


def make_handler(elec_values=(1.0,) * 96, gas_values=(0.1,) * 96):
    """Handler serving account, consumption, and tariff endpoints for MockTransport."""
    def handler(request):
        path = request.url.path
        if path.startswith("/v1/accounts/"):
            return httpx.Response(200, json=ACCOUNT)
        if "consumption" in path:
            values = list(elec_values) if "electricity" in path else list(gas_values)
            results = consumption_results(values)
            return httpx.Response(200, json={"count": len(results), "next": None, "results": results})
        if "standing-charges" in path:
            return httpx.Response(200, json={"count": 1, "next": None, "results": SC_RESULTS})
        if "standard-unit-rates" in path:
            return httpx.Response(200, json={"count": 1, "next": None, "results": RATE_RESULTS})
        return httpx.Response(404, json={"detail": "not found"})

    return handler
```

- [ ] **Step 2: Write the failing tests**

`tests/test_sync.py`:

```python
from datetime import datetime, timezone

import httpx
import pytest

from octopus_usage import costs, db, sync
from octopus_usage.octopus_client import OctopusClient
from tests.fixtures import ACCOUNT, make_handler


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "test.db"))


def make_client(handler):
    return OctopusClient("sk_test", transport=httpx.MockTransport(handler), backoff=0)


def test_product_code_from_tariff():
    assert sync.product_code_from_tariff("E-1R-VAR-22-11-01-C") == "VAR-22-11-01"
    assert sync.product_code_from_tariff("E-1R-AGILE-24-10-01-A") == "AGILE-24-10-01"


def test_discover_meters():
    meters = sync.discover_meters(ACCOUNT)
    assert meters["electricity"]["mpxn"] == "1200000000000"
    assert meters["electricity"]["serials"] == ["ELEC001"]
    assert meters["gas"]["mpxn"] == "3000000000"
    assert meters["gas"]["agreements"][0]["tariff_code"] == "G-1R-VAR-22-11-01-C"


def test_discover_meters_skips_export_points():
    account = {
        "properties": [{
            "electricity_meter_points": [
                {"mpan": "999", "is_export": True, "meters": [{"serial_number": "X"}], "agreements": []},
            ],
            "gas_meter_points": [],
        }]
    }
    assert sync.discover_meters(account) == {}


def test_full_sync_stores_readings_rates_and_converts_gas(conn):
    client = make_client(make_handler())
    result = sync.full_sync(conn, client, "A-12345678")
    assert result["fuels"] == {"electricity": 96, "gas": 96}
    # gas: 0.1 x 48 = 4.8/day -> heuristic says m3 -> converted
    gas = db.readings(conn, "gas")
    assert gas[0]["consumption_kwh"] == pytest.approx(costs.m3_to_kwh(0.1))
    assert db.meta_get(conn, "gas_unit") == "m3"
    elec = db.readings(conn, "electricity")
    assert elec[0]["consumption_kwh"] == 1.0
    assert db.rates_for(conn, "electricity")[0]["unit_rate_inc_vat"] == 28.0
    assert db.standing_charges_for(conn, "gas")[0]["charge_inc_vat"] == 50.0
    assert db.meta_get(conn, "last_sync") is not None
    assert db.meta_get(conn, "electricity_serial") == "ELEC001"


def test_incremental_sync_requests_from_latest_stored(conn):
    client = make_client(make_handler())
    sync.full_sync(conn, client, "A-12345678")
    latest = db.latest_interval_start(conn, "electricity")

    seen = []

    def spy_handler(request):
        seen.append(request)
        return make_handler()(request)

    sync.full_sync(conn, make_client(spy_handler), "A-12345678")
    elec_requests = [r for r in seen if "electricity-meter-points" in r.url.path]
    assert elec_requests[0].url.params["period_from"] == latest


def test_backfill_window_on_first_run(conn):
    seen = []

    def spy_handler(request):
        seen.append(request)
        return make_handler()(request)

    now = datetime(2026, 8, 13, tzinfo=timezone.utc)
    meters = sync.discover_meters(ACCOUNT)
    sync.sync_fuel_readings(conn, make_client(spy_handler), "electricity", meters["electricity"], now=now)
    assert seen[0].url.params["period_from"] == "2024-08-13T00:00:00+00:00"


def test_gas_unit_decision_is_sticky(conn):
    # First sync sees kWh-scale values; a later small batch must not flip the unit.
    client = make_client(make_handler(gas_values=(1.5,) * 96))
    meters = sync.discover_meters(ACCOUNT)
    sync.sync_fuel_readings(conn, client, "gas", meters["gas"])
    assert db.meta_get(conn, "gas_unit") == "kwh"
    sync.sync_fuel_readings(conn, make_client(make_handler(gas_values=(0.1,) * 4)), "gas", meters["gas"])
    assert db.meta_get(conn, "gas_unit") == "kwh"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_sync.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octopus_usage.sync'`

- [ ] **Step 4: Write the implementation**

`octopus_usage/sync.py`:

```python
"""Meter discovery and data synchronisation."""
from datetime import datetime, timedelta, timezone

from octopus_usage import costs, db

BACKFILL_DAYS = 730


def product_code_from_tariff(tariff_code):
    """'E-1R-VAR-22-11-01-C' -> 'VAR-22-11-01' (strip fuel/register prefix, region suffix)."""
    parts = tariff_code.split("-")
    return "-".join(parts[2:-1])


def discover_meters(account_data):
    """Per-fuel meter point info from /v1/accounts/ data.

    Uses the first meter point per fuel that has meter serials; export
    electricity points are skipped."""
    meters = {}
    for prop in account_data.get("properties", []):
        for mp in prop.get("electricity_meter_points", []):
            if mp.get("is_export"):
                continue
            serials = [m["serial_number"] for m in mp.get("meters", []) if m.get("serial_number")]
            if serials and "electricity" not in meters:
                meters["electricity"] = {
                    "mpxn": mp["mpan"], "serials": serials,
                    "agreements": mp.get("agreements", []),
                }
        for mp in prop.get("gas_meter_points", []):
            serials = [m["serial_number"] for m in mp.get("meters", []) if m.get("serial_number")]
            if serials and "gas" not in meters:
                meters["gas"] = {
                    "mpxn": mp["mprn"], "serials": serials,
                    "agreements": mp.get("agreements", []),
                }
    return meters


def sync_fuel_readings(conn, client, fuel, meter, now=None):
    """Fetch new readings for one fuel.

    Backfills BACKFILL_DAYS on first run, then only asks for readings after the
    newest stored interval. A meter point can list several serials (meter swaps);
    the first serial that returns data is remembered in meta."""
    now = now or datetime.now(timezone.utc)
    period_from = db.latest_interval_start(conn, fuel) or (
        (now - timedelta(days=BACKFILL_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    )
    chosen = db.meta_get(conn, f"{fuel}_serial")
    for serial in [chosen] if chosen else meter["serials"]:
        rows = client.consumption(fuel, meter["mpxn"], serial, period_from=period_from)
        if not rows:
            continue
        db.meta_set(conn, f"{fuel}_serial", serial)
        if fuel == "gas":
            unit = db.meta_get(conn, "gas_unit")
            if unit is None:
                unit = "m3" if costs.gas_looks_like_m3(rows) else "kwh"
                db.meta_set(conn, "gas_unit", unit)
            for r in rows:
                r["consumption_kwh"] = costs.m3_to_kwh(r["consumption"]) if unit == "m3" else r["consumption"]
        else:
            for r in rows:
                r["consumption_kwh"] = r["consumption"]
        return db.upsert_readings(conn, fuel, rows)
    return 0


def sync_fuel_rates(conn, client, fuel, meter):
    """Fetch unit rates + standing charges for agreements overlapping stored readings."""
    earliest = db.earliest_interval_start(conn, fuel)
    if earliest is None:
        return 0
    count = 0
    for ag in meter["agreements"]:
        valid_to = ag.get("valid_to")
        if valid_to and db.to_utc_iso(valid_to) < earliest:
            continue
        product = product_code_from_tariff(ag["tariff_code"])
        count += db.upsert_rates(
            conn, fuel, ag["tariff_code"],
            client.unit_rates(product, ag["tariff_code"], fuel, period_from=earliest),
        )
        db.upsert_standing_charges(
            conn, fuel, ag["tariff_code"],
            client.standing_charges(product, ag["tariff_code"], fuel, period_from=earliest),
        )
    return count


def full_sync(conn, client, account_number, now=None):
    """Discover meters, then sync readings and rates for each fuel."""
    meters = discover_meters(client.account(account_number))
    now = now or datetime.now(timezone.utc)
    result = {"synced_at": now.isoformat(), "fuels": {}}
    for fuel, meter in meters.items():
        result["fuels"][fuel] = sync_fuel_readings(conn, client, fuel, meter, now=now)
        sync_fuel_rates(conn, client, fuel, meter)
    db.meta_set(conn, "last_sync", result["synced_at"])
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sync.py -v`
Expected: 7 PASS

- [ ] **Step 6: Commit**

```bash
git add octopus_usage/sync.py tests/fixtures.py tests/test_sync.py
git commit -m "feat: meter discovery and incremental data sync"
```

---

### Task 6: Forecast (forecast.py)

**Files:**
- Create: `octopus_usage/forecast.py`
- Test: `tests/test_forecast.py`

**Interfaces:**
- Consumes: nothing from other modules — pure functions over daily-totals dicts (`{"date": date, "kwh": float, "complete": bool}`, sorted by date; extra keys like `cost_pence` are ignored, so `costs.daily_costs` output can be passed directly).
- Produces:
  - `day_of_week_factors(daily) -> dict[int, float]` (keys 0–6 = Mon–Sun)
  - `seasonal_baseline(daily, target: date) -> float | None`
  - `recent_weighted_mean(daily, asof: date) -> float | None`
  - `make_forecast(daily, days=30, start: date | None = None) -> list[dict]` — `{"date": date, "kwh": float, "lower": float, "upper": float}`; empty list when there's no usable history. `start` defaults to the day after the last data point.

- [ ] **Step 1: Write the failing tests**

`tests/test_forecast.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_forecast.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octopus_usage.forecast'`

- [ ] **Step 3: Write the implementation**

`octopus_usage/forecast.py`:

```python
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
```

Note the `break` in `seasonal_baseline`: without it a day sitting in both years' windows would be counted twice — it can match at most one anchor, so break after the first hit.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_forecast.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add octopus_usage/forecast.py tests/test_forecast.py
git commit -m "feat: seasonal day-of-week usage forecast with confidence band"
```

---

### Task 7: Backend API (app.py)

**Files:**
- Create: `octopus_usage/app.py`, `octopus_usage/static/` (empty dir for now; StaticFiles needs it to exist — add a placeholder `octopus_usage/static/.gitkeep`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `load_config`/`Config`/`ConfigError` (Task 1), `db.*` (Task 2), `OctopusClient`/`OctopusError` (Task 3), `costs.daily_costs`/`current_unit_rate`/`current_standing_charge` (Task 4), `sync.full_sync` (Task 5), `forecast.make_forecast` (Task 6).
- Produces: `create_app(config: Config | None = None, transport=None, sync_on_start: bool = True) -> FastAPI` with routes `GET /`, `GET /api/summary`, `GET /api/history?fuel=&days=`, `GET /api/forecast?fuel=`, `POST /api/sync`, and `/static` mount. Task 8's frontend consumes the JSON shapes shown in the tests below.

- [ ] **Step 1: Write the failing tests**

`tests/test_app.py`:

```python
from datetime import date, datetime, time, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from octopus_usage import db
from octopus_usage.app import create_app
from octopus_usage.config import Config
from tests.fixtures import make_handler


def make_test_app(tmp_path, transport=None, seed=None):
    cfg = Config(api_key="sk_test", account_number="A-12345678", db_path=str(tmp_path / "t.db"))
    if seed:
        conn = db.connect(cfg.db_path)
        seed(conn)
        conn.close()
    return create_app(config=cfg, transport=transport, sync_on_start=False)


def seed_days(conn, fuel, end_day, n_days, per_interval=0.5, rate=10.0, sc=48.0):
    """Complete London-midnight-aligned days ending on end_day (inclusive)."""
    rows = []
    start_day = end_day - timedelta(days=n_days - 1)
    t = datetime.combine(start_day, time(0), tzinfo=db.LONDON).astimezone(timezone.utc)
    end = datetime.combine(end_day + timedelta(days=1), time(0), tzinfo=db.LONDON).astimezone(timezone.utc)
    while t < end:
        rows.append({
            "interval_start": t.isoformat(),
            "interval_end": (t + timedelta(minutes=30)).isoformat(),
            "consumption": per_interval,
            "consumption_kwh": per_interval,
        })
        t += timedelta(minutes=30)
    db.upsert_readings(conn, fuel, rows)
    db.upsert_rates(conn, fuel, "T",
                    [{"valid_from": "2020-01-01T00:00:00Z", "valid_to": None, "value_inc_vat": rate}])
    db.upsert_standing_charges(conn, fuel, "T",
                               [{"valid_from": "2020-01-01T00:00:00Z", "valid_to": None, "value_inc_vat": sc}])


def seed_elec_60_days(conn):
    seed_days(conn, "electricity", date.today() - timedelta(days=1), 60)


def test_summary_tiles(tmp_path):
    app = make_test_app(tmp_path, seed=seed_elec_60_days)
    with TestClient(app) as client:
        data = client.get("/api/summary").json()
        elec = data["fuels"]["electricity"]
        # 24 kWh/day (48 x 0.5); rel tolerance absorbs 46/50-interval DST days
        assert elec["yesterday"]["kwh"] == pytest.approx(24.0, rel=0.05)
        assert elec["yesterday"]["cost_pence"] == pytest.approx(24.0 * 10.0 + 48.0, rel=0.05)
        assert elec["last_7"]["kwh"] == pytest.approx(7 * 24.0, rel=0.05)
        assert elec["last_30"]["kwh"] == pytest.approx(30 * 24.0, rel=0.05)
        assert elec["next_30"]["kwh"] == pytest.approx(30 * 24.0, rel=0.05)
        assert elec["next_30"]["cost_pence"] is not None
        assert "gas" not in data["fuels"]
        assert data["sync_error"] is None


def test_history_endpoint(tmp_path):
    app = make_test_app(tmp_path, seed=seed_elec_60_days)
    with TestClient(app) as client:
        data = client.get("/api/history", params={"fuel": "electricity", "days": 7}).json()
        assert len(data["days"]) == 7
        d = data["days"][-1]
        assert set(d) == {"date", "kwh", "cost_pence", "complete"}
        assert d["date"] == (date.today() - timedelta(days=1)).isoformat()
        assert client.get("/api/history", params={"fuel": "water"}).status_code == 422
        assert client.get("/api/history", params={"fuel": "gas"}).status_code == 404


def test_forecast_endpoint(tmp_path):
    app = make_test_app(tmp_path, seed=seed_elec_60_days)
    with TestClient(app) as client:
        data = client.get("/api/forecast", params={"fuel": "electricity"}).json()
        assert len(data["points"]) == 30
        p = data["points"][0]
        assert p["date"] == date.today().isoformat()
        assert p["lower"] <= p["kwh"] <= p["upper"]
        assert p["cost_pence"] == pytest.approx(p["kwh"] * 10.0 + 48.0)


def test_sync_endpoint_populates_db(tmp_path):
    app = make_test_app(tmp_path, transport=httpx.MockTransport(make_handler()))
    with TestClient(app) as client:
        result = client.post("/api/sync").json()
        assert result["fuels"]["electricity"] == 96
        data = client.get("/api/summary").json()
        assert "electricity" in data["fuels"]
        assert "gas" in data["fuels"]


def test_sync_failure_returns_502_but_cache_still_served(tmp_path):
    app = make_test_app(
        tmp_path,
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        seed=seed_elec_60_days,
    )
    with TestClient(app) as client:
        assert client.post("/api/sync").status_code == 502
        data = client.get("/api/summary").json()
        assert data["fuels"]["electricity"]["yesterday"]["kwh"] > 0
        assert data["sync_error"] is not None


def test_setup_page_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv("OCTOPUS_API_KEY", raising=False)
    monkeypatch.delenv("OCTOPUS_ACCOUNT_NUMBER", raising=False)
    monkeypatch.chdir(tmp_path)  # keep any real .env out of reach
    app = create_app(sync_on_start=False)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "OCTOPUS_API_KEY" in page.text
        assert client.get("/api/summary").status_code == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octopus_usage.app'`

- [ ] **Step 3: Write the implementation**

First: `mkdir -p octopus_usage/static && touch octopus_usage/static/.gitkeep`

`octopus_usage/app.py`:

```python
"""FastAPI app serving the dashboard and JSON API."""
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from octopus_usage import costs, db, forecast, sync
from octopus_usage.config import Config, ConfigError, load_config
from octopus_usage.octopus_client import OctopusClient, OctopusError

STATIC_DIR = Path(__file__).parent / "static"

SETUP_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Octopus Usage - setup</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 40rem; margin: 4rem auto; line-height: 1.6; padding: 0 1rem">
<h1>Almost there</h1>
<p>Create a file called <code>.env</code> in the project directory containing:</p>
<pre style="background:#f4f4f2;padding:1rem;border-radius:6px">OCTOPUS_API_KEY=sk_live_...
OCTOPUS_ACCOUNT_NUMBER=A-XXXXXXXX</pre>
<p>Find both in your <a href="https://octopus.energy/dashboard/">Octopus Energy dashboard</a>
under <strong>Personal details &rarr; API access</strong>, then restart the app.</p>
</body></html>"""


def create_app(config: Config | None = None, transport=None, sync_on_start: bool = True) -> FastAPI:
    config_error = None
    if config is None:
        try:
            config = load_config()
        except ConfigError as e:
            config_error = str(e)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if config is not None:
            app.state.conn = db.connect(config.db_path)
            app.state.client = OctopusClient(config.api_key, transport=transport)
            app.state.sync_error = None
            if sync_on_start:
                try:
                    sync.full_sync(app.state.conn, app.state.client, config.account_number)
                except OctopusError as e:
                    app.state.sync_error = str(e)
        yield

    app = FastAPI(title="Octopus Usage", lifespan=lifespan)

    def guard():
        if config_error is not None:
            raise HTTPException(status_code=503, detail=config_error)

    def check_fuel(fuel: str):
        if fuel not in db.FUELS:
            raise HTTPException(status_code=422, detail="fuel must be 'electricity' or 'gas'")

    @app.get("/", include_in_schema=False)
    def index():
        if config_error is not None:
            return HTMLResponse(SETUP_HTML)
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/summary")
    def summary():
        guard()
        conn = app.state.conn
        today = date.today()
        out = {
            "last_sync": db.meta_get(conn, "last_sync"),
            "sync_error": app.state.sync_error,
            "fuels": {},
        }
        for fuel in db.FUELS:
            daily = costs.daily_costs(conn, fuel)
            if not daily:
                continue

            def window(n):
                sel = [d for d in daily if today - timedelta(days=n) <= d["date"] < today]
                cost_vals = [d["cost_pence"] for d in sel]
                return {
                    "kwh": sum(d["kwh"] for d in sel),
                    "cost_pence": sum(cost_vals) if sel and None not in cost_vals else None,
                }

            fc = forecast.make_forecast(daily)
            rate = costs.current_unit_rate(conn, fuel)
            sc = costs.current_standing_charge(conn, fuel)
            fc_kwh = sum(p["kwh"] for p in fc)
            fc_cost = (
                fc_kwh * rate + len(fc) * sc
                if fc and rate is not None and sc is not None
                else None
            )
            out["fuels"][fuel] = {
                "yesterday": window(1),
                "last_7": window(7),
                "last_30": window(30),
                "next_30": {"kwh": fc_kwh, "cost_pence": fc_cost},
            }
        return out

    @app.get("/api/history")
    def history(fuel: str, days: int = 30):
        guard()
        check_fuel(fuel)
        if not 1 <= days <= 730:
            raise HTTPException(status_code=422, detail="days must be 1-730")
        daily = costs.daily_costs(app.state.conn, fuel)
        if not daily:
            raise HTTPException(status_code=404, detail=f"no data for {fuel}")
        cutoff = date.today() - timedelta(days=days)
        return {
            "days": [
                {
                    "date": d["date"].isoformat(),
                    "kwh": d["kwh"],
                    "cost_pence": d["cost_pence"],
                    "complete": d["complete"],
                }
                for d in daily
                if d["date"] >= cutoff
            ]
        }

    @app.get("/api/forecast")
    def get_forecast(fuel: str):
        guard()
        check_fuel(fuel)
        conn = app.state.conn
        daily = costs.daily_costs(conn, fuel)
        if not daily:
            raise HTTPException(status_code=404, detail=f"no data for {fuel}")
        rate = costs.current_unit_rate(conn, fuel)
        sc = costs.current_standing_charge(conn, fuel)
        points = []
        for p in forecast.make_forecast(daily):
            cost = p["kwh"] * rate + sc if rate is not None and sc is not None else None
            points.append({
                "date": p["date"].isoformat(),
                "kwh": p["kwh"],
                "lower": p["lower"],
                "upper": p["upper"],
                "cost_pence": cost,
            })
        return {"points": points}

    @app.post("/api/sync")
    def do_sync():
        guard()
        try:
            result = sync.full_sync(app.state.conn, app.state.client, config.account_number)
            app.state.sync_error = None
            return result
        except OctopusError as e:
            app.state.sync_error = str(e)
            raise HTTPException(status_code=502, detail=str(e))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: 6 PASS. Note: `test_forecast_endpoint` asserts the first forecast date is today — `make_forecast` starts the day after the last data point, and seeding ends yesterday.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: all tests from Tasks 1-7 PASS

- [ ] **Step 6: Commit**

```bash
git add octopus_usage/app.py octopus_usage/static/.gitkeep tests/test_app.py
git commit -m "feat: FastAPI endpoints for summary, history, forecast and sync"
```

---

### Task 8: Frontend (static dashboard)

**Files:**
- Create: `octopus_usage/static/index.html`, `octopus_usage/static/style.css`, `octopus_usage/static/app.js`, `octopus_usage/static/chart.umd.js` (vendored)
- Modify: `tests/test_app.py` (add one test)

**Interfaces:**
- Consumes: the JSON endpoints from Task 7 exactly as their tests show them.
- Design constraints (from the dataviz skill, palette already validated during planning): electricity = blue (`#2a78d6` light / `#3987e5` dark), gas = orange (`#eb6834` light / `#d95926` dark); one y-axis only (kWh/£ is a toggle, never dual axes); legend for the 2-series history chart; recessive hairline grid; bars with 4px rounded tops and gaps; 2px lines; missing days render as gaps (nulls), not zeros; both light and dark themes via `prefers-color-scheme`.

- [ ] **Step 1: Vendor Chart.js**

```bash
curl -fsSL -o octopus_usage/static/chart.umd.js https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.js
rm octopus_usage/static/.gitkeep
```

Verify it downloaded: `head -c 200 octopus_usage/static/chart.umd.js` should show a JS banner, not HTML.

- [ ] **Step 2: Write `octopus_usage/static/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Octopus Usage</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body class="viz-root">
<main>
  <header>
    <h1>Energy usage</h1>
    <div class="sync">
      <span id="last-sync" class="muted"></span>
      <button id="refresh">Refresh</button>
    </div>
  </header>
  <div id="banner" class="banner" hidden></div>

  <section id="tiles"></section>

  <section class="card">
    <div class="controls">
      <h2>History</h2>
      <div class="btn-group" id="period" role="group" aria-label="Period">
        <button data-days="7">7d</button>
        <button data-days="30" class="active">30d</button>
        <button data-days="90">90d</button>
        <button data-days="365">1y</button>
      </div>
      <div class="btn-group" id="unit" role="group" aria-label="Unit">
        <button data-unit="kwh" class="active">kWh</button>
        <button data-unit="cost">&pound;</button>
      </div>
    </div>
    <div class="chart"><canvas id="history-chart"></canvas></div>
  </section>

  <section class="card">
    <div class="controls">
      <h2>Next 30 days</h2>
      <div class="btn-group" id="forecast-fuel" role="group" aria-label="Fuel"></div>
    </div>
    <div class="chart"><canvas id="forecast-chart"></canvas></div>
    <p class="muted">Forecast from your seasonal and weekday usage patterns; the shaded area is the likely range.</p>
  </section>

  <footer class="muted">
    Costs are estimates including VAT &mdash; your bill may differ slightly.
    Smart meter data usually lags about a day.
  </footer>
</main>
<script src="/static/chart.umd.js"></script>
<script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Write `octopus_usage/static/style.css`**

```css
:root { color-scheme: light dark; }

.viz-root {
  --page: #f9f9f7;
  --surface-1: #fcfcfb;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --muted: #898781;
  --grid: #e1e0d9;
  --axis: #c3c2b7;
  --border: rgba(11, 11, 11, 0.10);
  --series-1: #2a78d6;   /* electricity */
  --series-2: #eb6834;   /* gas */
  --serious: #ec835a;
}
@media (prefers-color-scheme: dark) {
  .viz-root {
    --page: #0d0d0d;
    --surface-1: #1a1a19;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --grid: #2c2c2a;
    --axis: #383835;
    --border: rgba(255, 255, 255, 0.10);
    --series-1: #3987e5;
    --series-2: #d95926;
  }
}

body {
  margin: 0;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--page);
  color: var(--text-primary);
}
main { max-width: 64rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; }

header { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; flex-wrap: wrap; }
h1 { font-size: 1.4rem; margin: 0 0 1rem; }
h2 { font-size: 1rem; margin: 0; }
.muted { color: var(--muted); font-size: 0.85rem; }
.sync { display: flex; align-items: center; gap: 0.75rem; }

button {
  font: inherit; font-size: 0.85rem;
  color: var(--text-secondary);
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.3rem 0.7rem;
  cursor: pointer;
}
button:hover { border-color: var(--axis); }
button.active { color: var(--text-primary); border-color: var(--axis); font-weight: 600; }
button:disabled { opacity: 0.5; cursor: wait; }
.btn-group { display: inline-flex; gap: 0.25rem; }

.banner {
  background: var(--surface-1);
  border: 1px solid var(--serious);
  border-radius: 8px;
  padding: 0.6rem 0.9rem;
  margin-bottom: 1rem;
  font-size: 0.9rem;
}

#tiles { display: flex; flex-direction: column; gap: 1rem; margin-bottom: 1.5rem; }
.fuel-row h2 { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; }
.swatch { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
.tile-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; }
.tile {
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.75rem 0.9rem;
}
.tile-label { color: var(--muted); font-size: 0.78rem; margin-bottom: 0.25rem; }
.tile-value { font-size: 1.35rem; font-weight: 650; }
.tile-sub { color: var(--text-secondary); font-size: 0.85rem; }

.card {
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem;
  margin-bottom: 1.5rem;
}
.controls { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
.controls h2 { margin-right: auto; }
.chart { position: relative; height: 320px; }
footer { margin-top: 1rem; }
```

- [ ] **Step 4: Write `octopus_usage/static/app.js`**

```javascript
const $ = (sel) => document.querySelector(sel);
const cssVar = (name) => getComputedStyle(document.body).getPropertyValue(name).trim();

const FUEL_META = {
  electricity: { label: "Electricity", colorVar: "--series-1" },
  gas: { label: "Gas", colorVar: "--series-2" },
};

const state = { days: 30, unit: "kwh", forecastFuel: null, fuels: [] };
let historyChart = null;
let forecastChart = null;

const fmt = {
  kwh: (v) => (v == null ? "—" : `${v.toFixed(1)} kWh`),
  cost: (p) => (p == null ? "—" : `£${(p / 100).toFixed(2)}`),
};

function hexToRgba(hex, alpha) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

function shortDate(iso) {
  return new Date(iso + "T12:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

// Inclusive ISO-date range; missing dates become nulls so charts show gaps.
function dateRange(fromIso, toIso) {
  const out = [];
  const d = new Date(fromIso + "T12:00:00Z");
  for (;;) {
    const iso = d.toISOString().slice(0, 10);
    out.push(iso);
    if (iso === toIso) return out;
    d.setUTCDate(d.getUTCDate() + 1);
  }
}

function series(labels, rows, key) {
  const byDate = Object.fromEntries(rows.map((r) => [r.date, r[key]]));
  return labels.map((l) => byDate[l] ?? null);
}

function baseOptions(valueFmt) {
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    scales: {
      x: {
        grid: { display: false },
        border: { color: cssVar("--axis") },
        ticks: { color: cssVar("--muted"), maxTicksLimit: 12, maxRotation: 0 },
      },
      y: {
        beginAtZero: true,
        grid: { color: cssVar("--grid") },
        border: { display: false },
        ticks: { color: cssVar("--muted") },
      },
    },
    plugins: {
      legend: {
        labels: {
          color: cssVar("--text-secondary"),
          boxWidth: 12,
          boxHeight: 12,
          filter: (item) => !item.text.startsWith("_"),
        },
      },
      tooltip: {
        filter: (item) => !item.dataset.label.startsWith("_"),
        callbacks: { label: (ctx) => `${ctx.dataset.label}: ${valueFmt(ctx.parsed.y)}` },
      },
    },
  };
}

function showBanner(message) {
  const banner = $("#banner");
  banner.textContent = `⚠ ${message}`;
  banner.hidden = false;
}

function renderTiles(summary) {
  const tiles = [
    ["yesterday", "Yesterday"],
    ["last_7", "Last 7 days"],
    ["last_30", "Last 30 days"],
    ["next_30", "Next 30 days (projected)"],
  ];
  $("#tiles").innerHTML = state.fuels
    .map((fuel) => `
      <div class="fuel-row">
        <h2><span class="swatch" style="background:${cssVar(FUEL_META[fuel].colorVar)}"></span>${FUEL_META[fuel].label}</h2>
        <div class="tile-grid">${tiles
          .map(([key, label]) => {
            const t = summary.fuels[fuel][key];
            return `<div class="tile">
              <div class="tile-label">${label}</div>
              <div class="tile-value">${fmt.kwh(t.kwh)}</div>
              <div class="tile-sub">${fmt.cost(t.cost_pence)}</div>
            </div>`;
          })
          .join("")}</div>
      </div>`)
    .join("");
}

async function renderHistory() {
  const perFuel = await Promise.all(
    state.fuels.map((fuel) =>
      fetch(`/api/history?fuel=${fuel}&days=${state.days}`).then((r) => (r.ok ? r.json() : { days: [] })))
  );
  const allDates = perFuel.flatMap((p) => p.days.map((d) => d.date)).sort();
  if (!allDates.length) return;
  const labels = dateRange(allDates[0], allDates[allDates.length - 1]);
  const key = state.unit === "cost" ? "cost_pence" : "kwh";
  const datasets = state.fuels.map((fuel, i) => ({
    label: FUEL_META[fuel].label,
    data: series(labels, perFuel[i].days, key),
    backgroundColor: cssVar(FUEL_META[fuel].colorVar),
    borderRadius: 4,
    maxBarThickness: 24,
    barPercentage: 0.9,
    categoryPercentage: 0.8,
  }));
  historyChart?.destroy();
  historyChart = new Chart($("#history-chart"), {
    type: "bar",
    data: { labels: labels.map(shortDate), datasets },
    options: baseOptions(state.unit === "cost" ? fmt.cost : fmt.kwh),
  });
}

async function renderForecast() {
  const fuel = state.forecastFuel;
  if (!fuel) return;
  const [hist, fc] = await Promise.all([
    fetch(`/api/history?fuel=${fuel}&days=30`).then((r) => (r.ok ? r.json() : { days: [] })),
    fetch(`/api/forecast?fuel=${fuel}`).then((r) => (r.ok ? r.json() : { points: [] })),
  ]);
  if (!hist.days.length) return;
  const points = fc.points;
  const lastDate = (points.length ? points[points.length - 1] : hist.days[hist.days.length - 1]).date;
  const labels = dateRange(hist.days[0].date, lastDate);
  const color = cssVar(FUEL_META[fuel].colorVar);
  // Join the two lines: the forecast series starts from the last actual point.
  const lastActual = hist.days[hist.days.length - 1];
  const fcRows = points.length
    ? [{ date: lastActual.date, kwh: lastActual.kwh, lower: lastActual.kwh, upper: lastActual.kwh }, ...points]
    : [];
  const datasets = [
    {
      label: "Actual",
      data: series(labels, hist.days.filter((d) => d.complete), "kwh"),
      borderColor: color, borderWidth: 2, pointRadius: 0, spanGaps: false,
    },
    {
      label: "Forecast",
      data: series(labels, fcRows, "kwh"),
      borderColor: color, borderWidth: 2, borderDash: [5, 4], pointRadius: 0,
    },
    {
      label: "_upper",
      data: series(labels, fcRows, "upper"),
      borderWidth: 0, pointRadius: 0, fill: "+1", backgroundColor: hexToRgba(color, 0.18),
    },
    { label: "_lower", data: series(labels, fcRows, "lower"), borderWidth: 0, pointRadius: 0 },
  ];
  forecastChart?.destroy();
  forecastChart = new Chart($("#forecast-chart"), {
    type: "line",
    data: { labels: labels.map(shortDate), datasets },
    options: baseOptions(fmt.kwh),
  });
}

function renderFuelTabs() {
  const group = $("#forecast-fuel");
  group.innerHTML = state.fuels
    .map((fuel) =>
      `<button data-fuel="${fuel}" class="${fuel === state.forecastFuel ? "active" : ""}">${FUEL_META[fuel].label}</button>`)
    .join("");
  group.querySelectorAll("button").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.forecastFuel = btn.dataset.fuel;
      renderFuelTabs();
      renderForecast();
    })
  );
}

async function loadAll() {
  const res = await fetch("/api/summary");
  if (!res.ok) {
    showBanner("The app isn't configured yet — see the README.");
    return;
  }
  const summary = await res.json();
  state.fuels = Object.keys(summary.fuels);
  if (!state.fuels.length) {
    showBanner("No smart meter data yet — press Refresh to sync.");
    return;
  }
  if (!state.forecastFuel || !state.fuels.includes(state.forecastFuel)) {
    state.forecastFuel = state.fuels[0];
  }
  $("#last-sync").textContent = summary.last_sync
    ? `Updated ${new Date(summary.last_sync).toLocaleString("en-GB")} · data lags ~1 day`
    : "";
  if (summary.sync_error) showBanner("Couldn't reach Octopus — showing cached data.");
  renderTiles(summary);
  renderFuelTabs();
  await Promise.all([renderHistory(), renderForecast()]);
}

function wireControls() {
  $("#period").querySelectorAll("button").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.days = Number(btn.dataset.days);
      $("#period").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      renderHistory();
    })
  );
  $("#unit").querySelectorAll("button").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.unit = btn.dataset.unit;
      $("#unit").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      renderHistory();
    })
  );
  $("#refresh").addEventListener("click", async () => {
    const btn = $("#refresh");
    btn.disabled = true;
    btn.textContent = "Syncing…";
    try {
      const res = await fetch("/api/sync", { method: "POST" });
      if (!res.ok) showBanner("Sync failed — showing cached data.");
      else $("#banner").hidden = true;
    } finally {
      btn.disabled = false;
      btn.textContent = "Refresh";
      loadAll();
    }
  });
  // Chart colors are read from CSS at render time; re-render on theme change.
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => location.reload());
}

wireControls();
loadAll();
```

- [ ] **Step 5: Add a test that the dashboard is served**

Append to `tests/test_app.py`:

```python
def test_index_serves_dashboard_when_configured(tmp_path):
    app = make_test_app(tmp_path)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "history-chart" in page.text
        assert client.get("/static/chart.umd.js").status_code == 200
```

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: 7 PASS

- [ ] **Step 6: Manual visual check**

Seed a demo database and look at the page (no real credentials needed):

```bash
.venv/bin/python - << 'EOF'
from datetime import date, datetime, time, timedelta, timezone
import random
from octopus_usage import db

conn = db.connect("demo.db")
random.seed(1)
for fuel, base in (("electricity", 0.25), ("gas", 0.4)):
    rows = []
    end = date.today()
    t = datetime.combine(end - timedelta(days=120), time(0), tzinfo=db.LONDON).astimezone(timezone.utc)
    stop = datetime.combine(end, time(0), tzinfo=db.LONDON).astimezone(timezone.utc)
    while t < stop:
        v = base * (1.5 if t.astimezone(db.LONDON).weekday() >= 5 else 1.0) * random.uniform(0.6, 1.4)
        rows.append({"interval_start": t.isoformat(), "interval_end": (t + timedelta(minutes=30)).isoformat(),
                     "consumption": v, "consumption_kwh": v})
        t += timedelta(minutes=30)
    db.upsert_readings(conn, fuel, rows)
    db.upsert_rates(conn, fuel, "T", [{"valid_from": "2020-01-01T00:00:00Z", "valid_to": None,
                                       "value_inc_vat": 27.0 if fuel == "electricity" else 6.2}])
    db.upsert_standing_charges(conn, fuel, "T", [{"valid_from": "2020-01-01T00:00:00Z", "valid_to": None,
                                                  "value_inc_vat": 53.0 if fuel == "electricity" else 32.0}])
db.meta_set(conn, "last_sync", datetime.now(timezone.utc).isoformat())
print("seeded demo.db")
EOF
OCTOPUS_API_KEY=sk_demo OCTOPUS_ACCOUNT_NUMBER=A-DEMO OCTOPUS_DB_PATH=demo.db \
  .venv/bin/uvicorn --factory octopus_usage.app:create_app --port 8000
```

Note: startup will try one sync against the real API with the demo key; it fails fast and the app then serves the cached demo data (this also exercises the stale-data banner). Open http://localhost:8000 and check: tiles for both fuels; grouped bars with gaps; period + unit toggles work; forecast line continues from actuals with a shaded band; fuel tabs switch; layout holds in light and dark (toggle the OS theme). Then stop the server and `rm demo.db`.

- [ ] **Step 7: Commit**

```bash
git add octopus_usage/static tests/test_app.py
git commit -m "feat: dashboard frontend with history and forecast charts"
```

---

### Task 9: README & smoke test

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: everything; documents the run commands used above.

- [ ] **Step 1: Write `README.md`**

````markdown
# Octopus Usage

A local dashboard for your Octopus Energy (UK) smart meter data: historical
electricity + gas usage in kWh and estimated £, plus a 30-day forecast.

## Setup

1. **Get your API credentials.** Log in at
   [octopus.energy/dashboard](https://octopus.energy/dashboard/), open
   **Personal details → API access**. Copy:
   - your **API key** (looks like `sk_live_...`)
   - your **account number** (looks like `A-XXXXXXXX`)
2. **Create `.env`** in this directory (copy `.env.example`):

   ```
   OCTOPUS_API_KEY=sk_live_...
   OCTOPUS_ACCOUNT_NUMBER=A-XXXXXXXX
   ```

3. **Install and run:**

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   .venv/bin/uvicorn --factory octopus_usage.app:create_app --port 8000
   ```

4. Open <http://localhost:8000>. The first start backfills up to 2 years of
   half-hourly readings into a local SQLite file (`octopus_usage.db`) and can
   take a minute; afterwards it only fetches new readings.

## Notes

- **Costs are estimates** (rates include VAT); bills can differ slightly —
  e.g. gas calorific values vary day to day.
- **Smart meter data lags ~1 day** — that's an Octopus/DCC limitation.
- Meters, tariffs, and rates are discovered automatically from your account.
- Forecasts use your seasonal + weekday patterns; with under a year of
  history they fall back to a recent-weeks trend.
- Requires a smart meter sending half-hourly readings to Octopus. Fuels
  without data are hidden.

## Development

```bash
.venv/bin/pytest        # run the test suite (no network needed)
```
````

- [ ] **Step 2: Full verification**

Run: `.venv/bin/pytest -v`
Expected: entire suite PASSES (Tasks 1-8, ~40 tests)

- [ ] **Step 3: Manual smoke test against the real account**

Requires the user's real `.env`. If credentials aren't available at execution
time, note that in the final report and hand this checklist to the user:

```bash
.venv/bin/uvicorn --factory octopus_usage.app:create_app --port 8000
```

- First start completes a backfill sync without errors (watch the log).
- http://localhost:8000 shows tiles for each fuel on the account.
- Yesterday's kWh roughly matches the Octopus app/dashboard.
- History chart matches expectations (weekends vs weekdays visible).
- Forecast chart shows a plausible continuation with a band.
- Refresh button re-syncs and updates "Updated ..." time.
- Stop and restart: startup is fast (incremental sync, no re-backfill).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: setup and usage README"
```

---

## Spec coverage map

| Spec section | Task(s) |
|---|---|
| Credentials & .env + README instructions | 1, 9 |
| Meter discovery via account endpoint | 5 |
| Half-hourly sync, 2-year backfill, incremental | 5 |
| SQLite storage (readings/rates/standing charges/meta) | 2 |
| Cost per half-hour, time-of-use, standing charge | 4 |
| Gas m³→kWh conversion + unit heuristic | 4, 5 |
| Forecast (seasonal, day-of-week, fallback, band) | 6 |
| Forecast cost via current rate | 7 (`/api/forecast`, summary `next_30`) |
| API endpoints (summary/history/forecast/sync) | 7 |
| Frontend (tiles, history, forecast, refresh, lag note) | 8 |
| Error handling (setup page, stale cache banner, hidden fuels, gaps) | 7, 8 |
| Testing (unit + API + manual smoke) | every task; 9 |
