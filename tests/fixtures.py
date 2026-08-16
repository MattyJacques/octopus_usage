"""Canned Octopus API responses and a mock HTTP handler shared across tests."""
from datetime import datetime, timedelta

import httpx

ACCOUNT = {
    "number": "A-12345678",
    "properties": [
        {
            "postcode": "SW1A 1AA",
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
