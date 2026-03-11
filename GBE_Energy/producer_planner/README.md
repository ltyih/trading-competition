# *REMOVED* 2026 Producer Fuel Planner

Minimal decision-support tool for a Producer in the *REMOVED* 2026 Electricity case: it recommends **how much fuel to convert into next-day electricity** using SUNLIGHT news, RAE spot bulletins, market prices, limits, and manual distributor demand.

---

## Setup

- **Python 3.10+**
- No pip install required (stdlib only). Optional: `rich` for prettier terminal tables if already installed.

```bash
cd producer_planner
export RIT_API_KEY="your-api-key"   # or set in config.py
python3 main.py
```

---

## API Documentation (RIT Client REST API, Swagger v1.0.3)

- **Base URL**: `http://localhost:9999/v1`
- **Authentication**: Pass API key in header **`X-API-Key`**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/case` | Case state: period, tick, status (ACTIVE/PAUSED/STOPPED), etc. |
| GET | `/v1/news` | Array of news: `news_id`, `headline`, `body`, `period`, `tick`. Use query **`since=<news_id>`** for incremental fetch (only items with `news_id > since`). |
| GET | `/v1/securities` | Array of securities: `ticker`, `bid`, `ask`, `last`, `position`, `bid_size`, `ask_size`, etc. |
| GET | `/v1/limits` | Array of trading limits: `name`, `gross`, `net`, `gross_limit`, `net_limit`, `gross_fine`, `net_fine`. |

**Rate limiting**: On HTTP **429**, the API may return a **`Retry-After`** header (seconds) or a JSON body field **`wait`** (seconds). The app sleeps for that duration (capped at 60s) and retries.

---

## News formats (parsed by the app)

### A) SUNLIGHT forecast

- **Headline examples**: `SUNLIGHT Forecast for DAY 5`
- **Body – exact**: *"There will be 13 hours of sunlight tomorrow."* → delivery_day from "DAY N", hours = 13 (exact).
- **Body – range**: *"The weather forecasts call for between 13 and 20 hours of sunlight tomorrow."* → low = 13, high = 20; midpoint used until evening update.
- Parser uses the word **"sunlight"** (not "sunshine"). Delivery day from "DAY N" in headline or body.

### B) Spot bulletin (RAE)

- **Headline examples**: `SPOT PRICE AND VOLUMES FOR DAY 4`, `PRICE AND VOLUME BULLETIN`
- **Body**: at least one **$ price** (e.g. `$18.31`) and **contract volume** (e.g. `402 contracts are available ...`).
- Parsed fields: **delivery_day** (DAY N), **spot_price**, **spot_contract_volume**.

---

## Case mechanics (encoded in planner)

| Item | Rule |
|------|------|
| **ELEC-dayX spot** | 1 contract = 100 MWh |
| **ELEC-F forward** | 1 contract = 500 MWh; delivers next day’s ELEC-dayX |
| **Solar** | ELEC_solar = **6 × sunlight_hours** (midpoint when range; exact when evening update) |
| **NG conversion** | **8 NG contracts → 1 ELEC-day(t+1)** ⇒ buy NG today, electricity tomorrow |
| **Disposal** | **$20,000** per ELEC-dayX contract not sold by end of day X; electricity cannot be stored |
| **Oil cap** | Producer can produce up to **100 units/day** using crude oil (hard cap) |
| **Limits** | Trading limits from `GET /v1/limits` are displayed; order submission may be disabled |

---

## Usage

1. Start the RIT Client with the Electricity case and ensure the API is reachable at `http://localhost:9999/v1`.
2. Run `python3 main.py`. The app polls `/v1/case` until status is **ACTIVE**.
3. It then polls news (incremental with `since=`), securities, and limits; prints last parsed SUNLIGHT and SPOT bulletin; displays prices for NG, ELEC-F, and ELEC-dayX (if present) and limits.
4. Enter **distributor demand** (ELEC-day(t+1) contracts) and optionally **tender net** (ELEC contracts) when prompted.
5. The planner prints a single **RECOMMENDED** line (produce X ELEC, buy Y NG, use Z crude oil units, expected disposal risk $…) and **LOCK-IN WINDOW** status (final when exact evening sunlight update has been received).

---

## Demo snippet (sample output)

```
Case ACTIVE. Period=3 tick=45
  [SUNLIGHT] day=4 exact=True mid=13.0
  [SPOT] day=4 price=18.31 vol=402.0

--- Prices ---
  NG: bid=1.95 ask=2.05 last=2.0
  ELEC-F: bid=17.5 ask=18.0 last=17.8
  ELEC-day4: bid=18.0 ask=18.5 last=18.31
--- Limits ---
    NG: gross=0/1000 net=0/500
    ...

Distributor demand (ELEC contracts, Enter=0): 50
Tender net (ELEC contracts, Enter=0): 0

RECOMMENDED: produce 22.0 ELEC via conversion tomorrow; buy 176 NG today; use 0 crude oil units; expected disposal risk: $0
LOCK-IN WINDOW: Sunlight exact evening update received; recommendation is final for tomorrow.
```

---

## Files

- **config.py** – API base URL, API key (env or default), poll intervals, 429 wait caps.
- **parsers.py** – SUNLIGHT and SPOT bulletin parsers; self-test with spec examples (`python3 parsers.py`).
- **planner.py** – Pure decision rule; self-test (`python3 planner.py`).
- **main.py** – Poll loop, manual input, planner call, recommendation and LOCK-IN output.
