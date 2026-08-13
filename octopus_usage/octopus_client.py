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
