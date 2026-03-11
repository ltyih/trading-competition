# Context Log – Producer Fuel Planner

## Current status

- **App**: Minimal *REMOVED* 2026 Producer decision-support tool (4–6 files). Recommends how much fuel (NG + crude) to convert into next-day electricity using SUNLIGHT news, spot bulletins, market prices, limits, and manual distributor demand.
- **Location**: `GBE_Energy/producer_planner/`
- **Run**: `python3 main.py` (requires RIT API at `http://localhost:9999/v1` and `RIT_API_KEY`). Self-tests: `python3 parsers.py`, `python3 planner.py`.

---

## Decisions and rationale (by step)

### Step 1 – config.py

- **Files added**: `config.py`
- **Rationale**: Central place for API base URL (`http://localhost:9999/v1`), API key (env `RIT_API_KEY` or default), poll intervals for case/news/securities/limits, and 429 wait caps. Keeps main.py free of magic numbers and allows override via environment.

### Step 2 – parsers.py

- **Files added**: `parsers.py`
- **Rationale**: Spec requires robust extraction of SUNLIGHT (delivery_day, exact hours or low/high range) and SPOT BULLETIN (delivery_day, spot_price, spot_contract_volume) without assuming “sunshine”; support both “There will be X hours” and “between X and Y hours”. Regex-based parsing with safe defaults (return `None` on failure). Dataclasses `SunlightResult` and `SpotBulletinResult` for typed output. Self-test block with exact spec bodies (13 hours, 13–20 range, $18.31 and 402 contracts) runnable via `python3 parsers.py`.

### Step 3 – planner.py

- **Files added**: `planner.py`
- **Rationale**: Pure, unit-testable decision rule. Encodes case mechanics: solar 6× hours, 8 NG → 1 ELEC, disposal $20k/contract, crude cap 100. Implements exact formulas: Q_demand, Q_solar_mid, Q_safe_sell (0.5× spot volume + optional forward liquidity), Q_committed, Q_min_needed, Q_discretionary_cap, margin gate (spot_price - 8×NG_ask > 0 and disposal_budget_contracts == 0), no-disposal safeguard, then Q_produce_non_solar, NG_needed = ceil(Q×8), disposal risk in contracts and dollars. Outputs recommendation line and LOCK-IN flag (true when sunlight is exact). Crude oil shown as 0 when all production via NG (no oil→ELEC ratio in spec). Self-test with fixed inputs checks recommendation shape and NG_needed.

### Step 4 – main.py

- **Files added**: `main.py`
- **Rationale**: Single entry point. API layer via `urllib.request` + `json` (no external deps); GET helper honours 429 using `Retry-After` header or body `wait`, then sleep and retry. Poll `/v1/case` until ACTIVE; track period and reset last_news_id/sunlight/spot on period change. Poll `/v1/news?since=last_id`; run parsers; update last SUNLIGHT and last SPOT; log parsed values. Poll `/v1/securities` and `/v1/limits`; display NG, ELEC-F, ELEC-dayX (regex) and limits. Prompt for distributor demand and tender_net (manual). Build `PlannerInputs` from current state and call `compute_recommendation`; print recommendation line and LOCK-IN WINDOW status. Robust error handling so unexpected news/formats do not crash the loop.

### Step 5 – README.md

- **Files added**: `README.md`
- **Rationale**: API documentation must be included: base URL, X-API-Key, table of endpoints (case, news with since, securities, limits), rate limiting (429, Retry-After/wait). News formats: SUNLIGHT (exact vs range, delivery_day), SPOT (price, volume, delivery_day). Case mechanics: units, solar formula, NG conversion, disposal, oil cap, limits. Usage: run, set API key, enter demand. Demo snippet showing sample recommendation and LOCK-IN output.

### Step 6 – context.md

- **Files added**: `context.md`
- **Rationale**: Plan requires maintaining a context log at the end of each development step with changes and rationale; this file fulfils that and provides an audit trail for the minimal app.

### Step 7 – Prettier terminal, supply base, forwards/production

- **Files changed**: `requirements.txt` (add `rich`), `planner.py` (PlannerOutput: solar_mwh, conversion_mwh, total_supply_mwh, recommended_forwards_sell_elec_f; constants MWH_PER_ELEC_DAY, MWH_PER_ELEC_F; compute forwards = min(floor(target_total/5), elec_f_bid_size)), `main.py` (use ui module for Case, News, Prices, Limits, Supply base, Recommendations, LOCK-IN; remove inline print tables).
- **Files added**: `ui.py` (rich panels/tables with fallback to plain text: render_case, render_news, render_prices, render_limits, render_supply_base, render_recommendations, render_lock_in, print_news_item).
- **Rationale**: User requested a prettier terminal app, “how many units/contracts suppliers will be able to make” for communication, and explicit “how many forwards to sell” and “how many contracts/units to produce”. Supply base shows solar + conversion + total in contracts and MWh. Forwards recommendation = ELEC-F contracts to sell (capped by production and ELEC-F bid size). Production recommendation already present, now shown as contracts + MWh. Rich is optional; app runs without it using plain text.
