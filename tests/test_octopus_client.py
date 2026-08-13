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
