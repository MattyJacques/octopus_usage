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
