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
        assert set(d) == {"date", "kwh", "units", "cost_pence", "complete"}
        assert d["date"] == (date.today() - timedelta(days=1)).isoformat()
        assert client.get("/api/history", params={"fuel": "water"}).status_code == 422
        assert client.get("/api/history", params={"fuel": "gas"}).status_code == 404


def test_halfhourly_defaults_to_latest_day(tmp_path):
    app = make_test_app(tmp_path, seed=seed_elec_60_days)
    with TestClient(app) as client:
        data = client.get("/api/halfhourly", params={"fuel": "electricity"}).json()
        assert data["date"] == (date.today() - timedelta(days=1)).isoformat()
        assert 46 <= len(data["intervals"]) <= 50
        iv = data["intervals"][0]
        assert set(iv) == {"start", "kwh", "units", "cost_pence"}
        assert iv["kwh"] == pytest.approx(0.5)
        assert iv["cost_pence"] == pytest.approx(0.5 * 10.0)


def test_halfhourly_explicit_date_and_errors(tmp_path):
    app = make_test_app(tmp_path, seed=seed_elec_60_days)
    with TestClient(app) as client:
        day = (date.today() - timedelta(days=10)).isoformat()
        data = client.get("/api/halfhourly", params={"fuel": "electricity", "date": day}).json()
        assert data["date"] == day
        assert 46 <= len(data["intervals"]) <= 50
        assert client.get("/api/halfhourly", params={"fuel": "water"}).status_code == 422
        assert client.get("/api/halfhourly",
                          params={"fuel": "electricity", "date": "nope"}).status_code == 422
        assert client.get("/api/halfhourly", params={"fuel": "gas"}).status_code == 404


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


def test_index_serves_dashboard_when_configured(tmp_path):
    app = make_test_app(tmp_path)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "history-chart" in page.text
        assert "year-chart" in page.text
        assert client.get("/static/chart.umd.js").status_code == 200


def test_responses_forbid_stale_caching(tmp_path):
    # Without Cache-Control, browsers heuristically cache static files and can
    # pair a stale app.js with fresh HTML after an upgrade (blank new sections).
    app = make_test_app(tmp_path, seed=seed_elec_60_days)
    with TestClient(app) as client:
        assert client.get("/").headers["cache-control"] == "no-cache"
        assert client.get("/static/app.js").headers["cache-control"] == "no-cache"
        assert client.get("/api/summary").headers["cache-control"] == "no-cache"


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


def test_heatmap_endpoint(tmp_path):
    app = make_test_app(tmp_path, seed=seed_elec_60_days)
    with TestClient(app) as client:
        data = client.get("/api/heatmap", params={"fuel": "electricity"}).json()
        assert data["weeks"] == 12
        assert len(data["rows"]) == 7
        assert data["rows"][0]["day"] == "Mon"
        assert all(len(r["cells"]) == 24 for r in data["rows"])
        assert client.get("/api/heatmap", params={"fuel": "water"}).status_code == 422
        assert client.get("/api/heatmap", params={"fuel": "gas"}).status_code == 404


def test_monthly_endpoint(tmp_path):
    app = make_test_app(tmp_path, seed=seed_elec_60_days)
    with TestClient(app) as client:
        year = (date.today() - timedelta(days=1)).year
        data = client.get("/api/monthly", params={"fuel": "electricity", "year": year}).json()
        assert data["months"], "expected month buckets"
        m = data["months"][-1]
        assert set(m) == {"month", "kwh", "units", "cost_pence"}
        assert m["month"].startswith(str(year))
        empty = client.get("/api/monthly", params={"fuel": "electricity", "year": 2001}).json()
        assert empty == {"months": []}
        assert client.get("/api/monthly", params={"fuel": "water", "year": year}).status_code == 422
        assert client.get("/api/monthly", params={"fuel": "electricity", "year": 1999}).status_code == 422
        assert client.get("/api/monthly", params={"fuel": "gas", "year": year}).status_code == 404


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
