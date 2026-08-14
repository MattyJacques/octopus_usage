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
   ```

   Requires Python 3.11 or later; if `python3 --version` is older, install a newer version (e.g. via `mise install python@3.12` or Homebrew) and create the venv with its full path instead (e.g. `~/.local/share/mise/installs/python/3.12.*/bin/python -m venv .venv`).

   ```bash
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
