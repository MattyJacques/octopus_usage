"""FastAPI app serving the dashboard and JSON API."""
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from octopus_usage import costs, db, forecast, sync, weather, yearly
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


def create_app(config: Config | None = None, transport=None, sync_on_start: bool = True,
               weather_transport=None) -> FastAPI:
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
            app.state.weather_client = httpx.Client(transport=weather_transport, timeout=30)
            app.state.sync_error = None
            if sync_on_start:
                try:
                    sync.full_sync(app.state.conn, app.state.client, config.account_number)
                except OctopusError as e:
                    app.state.sync_error = str(e)
        yield

    app = FastAPI(title="Octopus Usage", lifespan=lifespan)

    @app.middleware("http")
    async def no_stale_caching(request, call_next):
        # Force revalidation (ETags make this a cheap 304): heuristic caching
        # can otherwise pair a stale app.js with newer HTML after an upgrade.
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

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
        earliest = [
            e for e in (db.earliest_interval_start(conn, f) for f in db.FUELS)
            if e is not None
        ]
        out = {
            "last_sync": db.meta_get(conn, "last_sync"),
            "sync_error": app.state.sync_error,
            "meters": {f: db.meta_get(conn, f"{f}_serial") for f in db.FUELS},
            "first_data": (
                datetime.fromisoformat(min(earliest)).astimezone(db.LONDON).date().isoformat()
                if earliest else None
            ),
            "gas_m3_to_kwh": costs.M3_TO_KWH,
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
                "standing_charge": sc,
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
                    "units": d["units"],
                    "cost_pence": d["cost_pence"],
                    "complete": d["complete"],
                }
                for d in daily
                if d["date"] >= cutoff
            ]
        }

    @app.get("/api/halfhourly")
    def halfhourly(fuel: str, date_: str | None = Query(None, alias="date")):
        guard()
        check_fuel(fuel)
        conn = app.state.conn
        if date_ is None:
            latest = db.latest_interval_start(conn, fuel)
            if latest is None:
                raise HTTPException(status_code=404, detail=f"no data for {fuel}")
            day = datetime.fromisoformat(latest).astimezone(db.LONDON).date()
        else:
            try:
                day = date.fromisoformat(date_)
            except ValueError:
                raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
        return {"date": day.isoformat(), "intervals": costs.halfhourly(conn, fuel, day)}

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

    @app.get("/api/heatmap")
    def heatmap(fuel: str):
        guard()
        check_fuel(fuel)
        rows = db.hourly_profile(app.state.conn, fuel, weeks=12)
        if not rows:
            raise HTTPException(status_code=404, detail=f"no data for {fuel}")
        return {"weeks": 12, "rows": rows}

    @app.get("/api/monthly")
    def monthly(fuel: str, year: int):
        guard()
        check_fuel(fuel)
        if not 2000 <= year <= 2100:
            raise HTTPException(status_code=422, detail="year must be 2000-2100")
        daily = costs.daily_costs(app.state.conn, fuel)
        if not daily:
            raise HTTPException(status_code=404, detail=f"no data for {fuel}")
        return {"months": yearly.months_of_year(daily, year)}

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

    @app.get("/api/weather")
    def get_weather(start: str | None = None, end: str | None = None,
                    date_: str | None = Query(None, alias="date")):
        guard()
        conn = app.state.conn
        wc = app.state.weather_client
        try:
            if date_ is not None:
                hours = weather.hourly_temps(conn, wc, date.fromisoformat(date_))
                return {"available": True, "hours": hours} if hours else {"available": False}
            if start is not None and end is not None:
                days = weather.daily_temps(
                    conn, wc, date.fromisoformat(start), date.fromisoformat(end))
                return {"available": True, "days": days} if days is not None else {"available": False}
        except ValueError:
            raise HTTPException(status_code=422, detail="dates must be YYYY-MM-DD")
        raise HTTPException(status_code=422, detail="pass start & end, or date")

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
