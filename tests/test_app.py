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
