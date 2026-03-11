## Assumptions (explicit)

1. **Universe**: four stocks **SPNG, SMMR, ATMN, WNTR**, CAD, start price ~$25. 
2. **Heat structure**: **12 heats**, **5 minutes per heat**; each minute represents a “day”; **news may arrive after each market close (every minute)** and is the primary driver of price moves.  
3. **Risk**:

   * **Aggregate position limit** across the 4 stocks is enforced at **each market close (each minute)** with **$10/share penalty** on shares above the limit. 
   * During trading, additional **gross/net trading/position limits** exist; **$5/share penalty** when exceeded. 
4. **Costs**: **$0.02/share fee for filled market orders**, and **rebates for filled limit orders** (by ticker).  
5. **Close-out**: any non-zero stock position is closed at end of trading (end of heat) at last traded price. 
6. **API**: polling REST API (localhost:9999/v1) with per-ticker order insertion rate limits and 429 backoff signals.  


---

## 1) Architecture + modules

### 1.1 Repository structure (implementation-ready)

```
*REMOVED*-mm-bot/
  README.md
  pyproject.toml
  config/
    default.yaml
    live.yaml
  scripts/
    run_live.py
    record_session.py
    replay_offline.py
    analyze_kpis.py
  src/*REMOVED*_mm/
    __init__.py
    api/
      client.py
      models.py
      ratelimit.py
      errors.py
    data/
      book.py
      tape.py
      news.py
      state.py
    strategy/
      regimes.py
      fair_value.py
      signals.py
      quoting.py
      inventory.py
    execution/
      order_manager.py
      execution_engine.py
      closeout.py
    risk/
      limits.py
      exposure.py
      guards.py
    telemetry/
      logger.py
      metrics.py
      dashboard.py
    sim/
      mock_exchange.py
      replay.py
      fixtures.py
    tests/
      test_book.py
      test_pnl.py
      test_regimes.py
      test_risk.py
      test_order_manager.py
```

### 1.2 Core components (responsibilities)

1. **Market data ingest**: poll `/securities/book`, `/securities/tas`, `/news`, `/case`; maintain incremental pointers (tas `after`, news `since`).  
2. **State**: in-memory “truth” of:

   * L1 (and optional L2) book snapshot per ticker
   * recent trades (tape)
   * positions + VWAP + NLV from `/securities`
   * open orders from `/orders`  
3. **Signal engine**: fair value, volatility proxy, news impulse estimator, regime selection.
4. **Risk manager**: enforce aggregate position + intra-heat limits guardrails, and “no inventory late” closeout schedule. (Aggregate penalty is immediate at each minute close.) 
5. **Execution engine**: chooses passive vs aggressive; manages quote refresh; handles partial fills and staleness.
6. **Order manager**: idempotent “desired quotes → actual orders” reconciliation; bulk cancel via `/commands/cancel`. 
7. **Telemetry**: structured logs + KPIs each tick; offline analysis scripts.

### 1.3 Data structures (concrete)

Use `dataclasses` to keep logic testable.

**Order book**

```python
@dataclass
class L1:
    bid_px: float | None
    bid_sz: float | None
    ask_px: float | None
    ask_sz: float | None
    mid: float | None
    spread: float | None
    ts: float

@dataclass
class BookSideLevel:
    px: float
    sz: float

@dataclass
class L2Book:
    bids: list[BookSideLevel]  # best-first
    asks: list[BookSideLevel]
    ts: float
```

**Fills / tape**

```python
@dataclass
class Print:
    id: int
    period: int
    tick: int
    px: float
    qty: float
```

**Orders / positions / PnL**

```python
@dataclass
class LiveOrder:
    order_id: int
    ticker: str
    side: Literal["BUY","SELL"]
    typ: Literal["LIMIT","MARKET"]
    px: float | None
    qty: float
    filled: float
    vwap: float | None
    status: Literal["OPEN","TRANSACTED","CANCELLED"]
    created_ts: float

@dataclass
class Position:
    ticker: str
    qty: float
    vwap: float
    last: float
    nlv: float

@dataclass
class PnL:
    realized: float
    unrealized: float
    fees: float
    rebates: float
    penalties: float
```

**Regime state**

```python
class Regime(Enum):
    NORMAL_MM = 1
    NEWS_LOCKOUT = 2
    JUMP_REPRICE = 3
    INVENTORY_REBALANCE = 4
    CLOSEOUT = 5

@dataclass
class RegimeState:
    regime: Regime
    since_ts: float
    last_news_id: int
    last_jump_ts: float
    lockout_until_ts: float
```

---

## 2) Strategy specification (jump overlay + MM base)

### 2.1 Regimes and rules (entry/exit)

#### A) NORMAL_MM

**Objective**: harvest spread + rebates while staying inventory-neutral.

* **Enter**: default; exit when news arrives or volatility spikes or inventory breach triggers.
* **Exit to NEWS_LOCKOUT**: on any new news item for a ticker (or market-wide news) detected via `/news?since=...`. 
* **Exit to INVENTORY_REBALANCE**: if per-ticker |pos| > soft cap OR aggregate exposure approaching limit.
* **Exit to CLOSEOUT**: late in minute or end-of-heat schedule.

#### B) NEWS_LOCKOUT

**Objective**: avoid being the passive liquidity immediately after news (high adverse selection).

* **Enter**: immediately upon news id increment for ticker.
* **Action**:

  * cancel quotes for affected ticker (or widen drastically)
  * no new passive quotes for `lockout_seconds`
* **Exit**: time-based → JUMP_REPRICE.

#### C) JUMP_REPRICE

**Objective**: re-anchor fair value rapidly after jump; optionally take small directional “jump trade” with strict risk.

* **Enter**: lockout ends OR detected jump event from tape mid move > threshold.
* **Action**:

  * compute new FV with impulse model
  * re-quote around FV with wider spreads
  * optional small aggressive trade if price is far from FV and liquidity is thin (bounded size)
* **Exit**: after `reprice_window_seconds` or volatility normalizes → NORMAL_MM; if inventory elevated → INVENTORY_REBALANCE.

#### D) INVENTORY_REBALANCE

**Objective**: cut inventory risk while still earning some edge.

* **Enter**: |pos| > soft cap OR aggregate exposure ratio > threshold.
* **Action**:

  * skew quotes to trade out (tighten on unwind side, widen on build side)
  * if near hard cap, cross with IOC/marketable limit in slices
* **Exit**: when |pos| < target band and exposure safe → NORMAL_MM; if late → CLOSEOUT.

#### E) CLOSEOUT

**Objective**: avoid minute-close aggregate penalty and end-of-heat forced close.

* **Enter**:

  * **Minute-close**: approaching each **market close (every minute)** where aggregate limit penalty is assessed. 
  * **End-of-heat**: final ~20–40 seconds of heat (staged flatten).
* **Action**:

  * cancel all passive quotes
  * flatten inventory aggressively in slices with IOC/market orders
* **Exit**: after close boundary passes (minute tick rollover) → NORMAL_MM.

---

### 2.2 Fair value estimation

#### a) Simple (robust baseline)

Per ticker:

1. Maintain **mid-price EMA**: `ema_mid_t = α*mid_t + (1-α)*ema_mid_{t-1}`
2. On news:

   * classify polarity/impact → `impulse`
   * apply adjustment: `fv = ema_mid + k_impulse * impulse`
3. During JUMP_REPRICE, increase α temporarily (faster tracking).

#### b) Improved (online calibration: “news → expected return”)

Goal: adapt each ticker’s reaction magnitude minute-to-minute (as stated correlations and reactions vary). 

Maintain an online linear model per ticker:

* Features from news text: polarity score, keyword buckets, “surprise” heuristics, ticker tag, time-in-heat.
* Target: realized return over horizon H seconds after news: `r = (mid_{t+H}-mid_t)/mid_t`
* Update via ridge SGD:

  * `w ← (1-λ)*w + η*(r - w·x)*x`
    Use calibrated expected return to shift FV:
* `fv = ema_mid * (1 + clip(w·x, -rmax, rmax))`

---

### 2.3 Spread/quote logic (bid/ask offsets)

Let:

* `fv` = fair value
* `σ` = short-horizon volatility proxy (EWMA of mid returns or tape variance)
* `inv` = position (shares)
* `inv_norm = inv / inv_soft_cap`

Base half-spread:

* `hs = max(min_hs, base_hs + vol_k * σ)`
  Inventory skew:
* `skew = inv_k * inv_norm * hs` (positive inv pushes quotes downward to sell)
  Quote prices:
* `bid_px = round_to_tick(fv - hs - skew)`
* `ask_px = round_to_tick(fv + hs - skew)`

Quote sizing:

* start with `base_size`
* reduce size when `σ` high or near limits:

  * `size = base_size * size_vol_mult(σ) * size_limit_mult(exposure_ratio)`

Rebate-aware quoting:

* prefer passive fills when spread ≥ (fees + desired_edge) since rebates exist for filled limit orders.  

---

### 2.4 Inventory controls (hard/soft caps, skew, fade)

Per ticker:

* **hard cap**: `|pos| <= hard_cap_tkr`
* **soft cap**: begin rebalancing when `|pos| >= soft_cap_tkr`

Aggregate exposure:

* `agg = Σ |pos_tkr|`
* enforce `agg <= agg_limit * agg_safety` where `agg_limit` announced per heat. 

Actions:

1. **Soft zone**: increase skew + widen on inventory-increasing side.
2. **Near hard cap**: stop quoting the “bad” side; only quote to unwind.
3. **Breach imminent**:

   * cancel all quotes
   * execute unwind slices aggressively (IOC/marketable limit), honoring max order size 10,000. 

---

### 2.5 News handling (parse, classify, cooldowns)

Use `/news?since=last_id` and maintain per-ticker last handled id. 

Processing steps:

1. Normalize text: lowercase, strip punctuation.
2. Determine affected ticker:

   * if API provides `ticker` field, trust it. 
   * else keyword-map to tickers (SPNG/SMMR/ATMN/WNTR).
3. Polarity/impact:

   * rule-based dictionary + optional lightweight logistic regression trained from recorded sessions.
4. Apply:

   * set `lockout_until = now + lockout_seconds`
   * store “event context” for online calibration (features snapshot)
5. Cooldown:

   * ignore additional news for same ticker within `news_cooldown_seconds` (or extend lockout).

---

### 2.6 Close-out logic (minute-close + end-of-heat)

**Minute-close (critical)**: aggregate position limit penalty assessed immediately at each minute close. 
Implement a **staged minute-close flatten**:

* If `seconds_into_minute >= 50`:

  * target `agg_target = agg_limit * 0.70`
  * unwind highest-risk inventories first (largest |pos| or most volatile)
* If `>= 55`:

  * target `agg_target = agg_limit * 0.30`
* If `>= 58`:

  * target `agg_target = 0` (or near-zero), accepting crossing cost to avoid $10/share penalty.

**End-of-heat**:

* In last ~30 seconds of the 5-minute heat, force positions toward 0 to avoid adverse end effects and forced close-out. 

The provided base script demonstrates a simplistic “tick%60 >= 55 flatten” approach; replace it with staged, risk-ranked flattening and per-ticker slices. 

---

## 3) Execution + microstructure rules

### 3.1 Order placement policy (passive vs aggressive)

1. **Passive default** (NORMAL_MM): two-sided quotes.
2. **Aggressive only when**:

   * CLOSEOUT
   * near hard caps
   * jump mispricing exceeds `cross_edge_threshold`
3. Prefer **marketable limit** (LIMIT at far price) where possible for deterministic fills; use MARKET when certainty matters (late closeout). Market orders incur $0.02/share fee. 

### 3.2 Quote refresh cadence + cancellation strategy

Constraints: order insertion is rate-limited; handle 429 with `Retry-After` and `X-Wait-Until`.  

Policy:

* Per ticker refresh every `quote_interval_ms` (e.g., 200–500ms), but **only update when** price/size materially changes:

  * `|new_bid-old_bid| >= 1 tick` OR
  * `|fv_new - fv_old| >= fv_update_threshold` OR
  * regime changed OR
  * order staleness > `max_age_ms`
* Use bulk cancel by ticker via `/commands/cancel?ticker=...` for fast reconciliation. 

Avoid “over-cancel” behavior:

* Maintain a minimum quote lifetime `min_rest_ms` unless in NEWS_LOCKOUT/CLOSEOUT.
* Cap cancels per second with a local token bucket (even if instructor settings are loose).

### 3.3 Queue priority tactics (pennying) — safe gating

Pennying = stepping inside by 1 tick to gain priority.
Allow only if:

* spread ≥ 3 ticks
* volatility proxy below threshold
* inventory within neutral band
* not within `news_lockout_window` and not within `closeout_window`
* total exposure < `agg_limit*0.6`

### 3.4 Partial fills, stale orders, spread spikes

* Track partial fills via polling `/orders` and `/orders/{id}`. 
* Mark an order stale if:

  * older than `max_order_age_ms`, or
  * order price is now outside top-of-book by > N ticks, or
  * spread spike event (spread > spread_spike_ticks) triggers cancel/widen.
* On spread spike: widen to avoid being picked off; reduce size.

---

## 4) Parameterization + tuning

### 4.1 Tunable parameters (defaults + ranges)

Defaults are conservative “Day 1”.

**Market/rounding**

* `tick_size`: infer from `quoted_decimals` in `/securities` metadata (or assume $0.01 initially). 
* `rounding_decimals`: from `quoted_decimals`.

**Quoting**

* `base_hs_ticks`: 2 (range 1–6)
* `min_hs_ticks`: 1 (range 1–3)
* `vol_k`: 3.0 (range 1–8)
* `fv_update_threshold_ticks`: 1 (range 1–3)
* `quote_interval_ms`: 300 (range 150–800)
* `min_rest_ms`: 250 (range 100–800)
* `max_order_age_ms`: 1200 (range 600–3000)

**Sizing**

* `base_size`: 300 shares (range 100–1500)
* `max_quote_size`: 1500 (range 500–5000)
* `size_vol_cut`: 0.5 (range 0.2–0.8)
* `size_limit_cut`: 0.3 (range 0.1–0.7)

**Inventory**

* `soft_cap_tkr`: 2500 (range 1000–6000)
* `hard_cap_tkr`: 5000 (range 2000–9000)
* `agg_safety`: 0.85 (range 0.6–0.95)
* `inv_k`: 1.2 (range 0.4–2.5)

**News/jump**

* `news_lockout_seconds`: 1.0 (range 0.3–2.5)
* `news_cooldown_seconds`: 3.0 (range 1–10)
* `jump_thresh_bps`: 20 (range 10–60) over 0.5–1.0s window
* `reprice_window_seconds`: 2.0 (range 0.5–5)

**Closeout**

* `minute_closeout_start_s`: 50 (range 45–55)
* `minute_closeout_hard_s`: 58 (range 55–59)
* `heat_closeout_start_s`: 270 (range 240–285) in a 300s heat

**Execution**

* `cross_edge_ticks`: 2 (range 1–5)
* `ioc_slice_size`: 800 (range 200–2000)
* `market_slice_size`: 1200 (range 500–5000)

### 4.2 Tuning workflow (grounded in observable KPIs)

1. **Start conservative**:

   * wider spreads, small size, strict lockout, aggressive closeout
2. **Validate constraints**:

   * zero aggregate limit penalties at minute closes (primary)
   * no repeated 429 rate-limit errors (proper backoff)
3. **Tighten** iteratively:

   * reduce half-spread
   * increase size only when adverse selection stays acceptable
4. **Separate per-regime tuning**:

   * NORMAL_MM: tighten spreads and size
   * NEWS_LOCKOUT/JUMP_REPRICE: lengthen lockout if picked off; otherwise shorten
5. **Metrics to watch** (per ticker + total):

   * realized spread capture
   * adverse selection: PnL after fill over next N seconds
   * fill ratio: fills / posted volume
   * inventory variance and time-in-inventory
   * penalties avoided (minute and intra-heat)
   * market order usage share (fees vs rebates) 

---

## 5) Testing plan (detailed, no hidden internals)

### 5.1 Unit tests (fast, deterministic)

1. **Book building**

   * from `/securities/book` snapshots → correct L1 and L2 parsing
2. **Tape ingestion**

   * incremental tas via `after` pointer, monotonic ids. 
3. **PnL accounting**

   * realized/unrealized with partial fills, fees, rebates (rebate schedule from case doc). 
4. **Risk constraints**

   * aggregate exposure computation matches spec (Σ|pos|). 
5. **Regime transitions**

   * news triggers lockout → reprice → normal; inventory triggers rebalance; time triggers closeout
6. **Order manager reconciliation**

   * desired quotes vs open orders: creates, replaces, cancels minimal set

### 5.2 Simulation harness: mock exchange (deterministic)

Implement `sim/mock_exchange.py`:

* Inputs: pre-recorded sequence of (book snapshots, prints, news events, timestamps)
* Outputs: simulated fills based on:

  * if your bid ≥ simulated best ask → fill immediately (cross)
  * else passive fills triggered probabilistically by tape prints hitting your level
* Keep it simple and deterministic with a seeded RNG and rule-based matching.

Purpose: validate strategy logic and risk handling without relying on exchange internals.

### 5.3 Replay testing (record from RIT practice, replay offline)

**Recording script** (`scripts/record_session.py`):

* poll at fixed cadence:

  * `/case`
  * `/securities` (positions/last)
  * `/securities/book?ticker=...&limit=20` for each ticker (L1/L2)
  * `/securities/tas?ticker=...&after=...`
  * `/news?since=...` 
* write newline-delimited JSON events with wall-clock timestamps.

**Replay script** (`scripts/replay_offline.py`):

* feeds recorded events to the same strategy code via an interface `MarketDataProvider`
* replaces `ApiClient` with a `SimBroker` that logs orders and simulates fills.

### 5.4 Scenario tests (must pass)

1. **Sudden jump up** after minute close (news):

   * verify lockout
   * verify FV shifts upward
   * verify quotes widen and no immediate adverse selection
2. **Sudden jump down**
3. **Whipsaw**: jump then partial mean reversion:

   * ensure inventory does not balloon; rebalance triggers
4. **Liquidity vacuum / empty book**:

   * skip quoting if book empty (base script already checks this) 
5. **End-of-minute penalty avoidance**:

   * with aggregate limit 15,000 example, ensure agg exposure < limit before close. 
6. **Competing penny bots**:

   * verify your pennying gates shut during volatility/news
7. **API failures**:

   * 429: obey Retry-After and X-Wait-Until; no busy loops. 
   * transient 500/timeout: degrade to cancel+safe-mode

### 5.5 Performance tests

* **Loop frequency**: sustain 2–5 Hz per ticker without rate-limit violations
* **Latency budget**: ensure full cycle (poll → compute → orders) stays below `quote_interval_ms` under load
* **Rate limiting**:

  * intentionally force 429 in a test; ensure exponential backoff + jitter

### 5.6 KPIs + acceptance criteria (quantitative gates)

Minimum acceptance (Day 1):

* **0 aggregate limit penalties** at minute closes
* **end-of-heat inventory = 0**
* **< 1% loops hitting 429** after backoff logic
* **stable runtime**: no crash for full 5 minutes

Competitive targets (Day 2–5):

* positive net PnL after fees
* adverse selection rate below threshold (e.g., median post-fill 2s PnL ≥ 0)
* inventory variance controlled (time outside soft caps < 10%)
* passive fill ratio improving without penalty risk

---

## 6) RIT API integration specifics

### 6.1 Endpoints typically needed (exact from swagger / base script)

**Case / time**

* `GET /v1/case` → tick, status, ticks_per_period. 

**Market data**

* `GET /v1/securities` → list of securities + positions + last/vwap/nlv fields. 
* `GET /v1/securities/book?ticker=...&limit=...` → order book snapshot (use limit=1 for L1; higher for L2). 
* `GET /v1/securities/tas?ticker=...&after=...` → time & sales incremental. 
* `GET /v1/news?since=...` → news items. 

**Orders**

* `POST /v1/orders?ticker=...&type=...&quantity=...&action=...&price=...` (rate-limited; supports market dry_run). 
* `GET /v1/orders?status=OPEN` (and optionally by ticker via query in practice) 
* `GET /v1/orders/{id}` → order status. 

**Cancels**

* `POST /v1/commands/cancel?ticker=...` or `?all=1` or `?ids=...` 

**Limits**

* `GET /v1/limits` for gross/net and fines behavior. 

### 6.2 Approx request/response shapes (grounded)

**/news**

```json
[
  {"news_id": 123, "period": 1, "tick": 60, "ticker": "SPNG", "headline": "...", "body": "..."}
]
```

(Fields per spec) 

**/orders POST success**
Returns an Order object including `order_id`, `quantity_filled`, `vwap`, `status`. 

**429**

* HTTP 429 with `Retry-After` header and body `wait`. 

### 6.3 Robust error handling patterns

* Wrap every request:

  * timeout (e.g., 250–500ms)
  * retry only on idempotent GETs
  * on 401: stop immediately (bad key)
  * on 429: sleep `max(wait, Retry-After)` plus small jitter, then resume
* Maintain a per-ticker “next_order_time” from `X-Wait-Until` if provided. 

### 6.4 Polling vs streaming

RIT client API is polling-oriented; use incremental pointers:

* tas: `after`
* news: `since`
  This minimizes bandwidth and avoids repeated full pulls.  

---

## Main loop pseudo-code (event-driven polling)

```python
def main():
    cfg = load_config("config/live.yaml")  # hot reload supported
    api = ApiClient(base_url="http://localhost:9999/v1", api_key=cfg.api_key)

    universe = api.get_securities_list()  # tickers
    state = GlobalState(universe)
    om = OrderManager(api, state, cfg)
    strat = StrategyEngine(cfg)
    risk = RiskManager(cfg)
    closeout = CloseoutManager(cfg)

    last_news_id = 0
    tas_after = {t: 0 for t in universe}
    loop_ts = now()

    while True:
        case = api.get_case()
        if case.status != "ACTIVE":
            break

        cfg = maybe_reload_config(cfg)  # hot reload
        state.update_clock(case)

        # 1) Ingest news (incremental)
        news_items = api.get_news(since=last_news_id)
        for n in news_items:
            last_news_id = max(last_news_id, n.news_id)
            state.news.apply(n)

        # 2) Ingest market data
        for t in universe:
            book = api.get_book(t, limit=cfg.book_depth)
            state.book.update(t, book)

            prints = api.get_tas(t, after=tas_after[t])
            for p in prints:
                tas_after[t] = max(tas_after[t], p.id)
                state.tape.add(t, p)

        # 3) Positions + open orders
        state.positions = api.get_positions()
        state.open_orders = api.get_open_orders(status="OPEN")

        # 4) Risk and regime selection
        reg = strat.select_regimes(state)
        targets = strat.build_quote_targets(state, reg)

        # 5) Closeout overrides (minute and end-of-heat)
        targets = closeout.apply(state, targets)

        # 6) Risk clamps (sizes/prices; block unsafe quotes)
        safe_targets = risk.apply(state, targets)

        # 7) Reconcile orders (minimal churn)
        om.reconcile(safe_targets)

        # 8) Telemetry
        log_kpis(state, reg, safe_targets)

        sleep_until(loop_ts + cfg.loop_interval_s)
        loop_ts = now()
```

---

## Regime engine pseudo-code (explicit)

```python
def select_regimes(state):
    reg = {}
    for t in state.universe:
        if state.is_end_of_heat() or state.is_minute_close_window():
            reg[t] = CLOSEOUT
            continue

        if state.news.has_new_for(t):
            state.regime[t].lockout_until_ts = now() + cfg.news_lockout_seconds
            reg[t] = NEWS_LOCKOUT
            continue

        if now() < state.regime[t].lockout_until_ts:
            reg[t] = NEWS_LOCKOUT
            continue

        if detect_jump(state, t, cfg.jump_thresh_bps):
            state.regime[t].last_jump_ts = now()
            reg[t] = JUMP_REPRICE
            continue

        if near_inventory_or_agg_limits(state, t):
            reg[t] = INVENTORY_REBALANCE
            continue

        reg[t] = NORMAL_MM
    return reg
```

---

## Order manager pseudo-code (idempotent reconciliation)

```python
def reconcile(targets_by_ticker):
    for t, targets in targets_by_ticker.items():
        live = state.open_orders.by_ticker(t)

        # cancel forbidden sides (e.g., NEWS_LOCKOUT or INVENTORY hard stop)
        if targets.cancel_all:
            api.bulk_cancel(ticker=t)  # /commands/cancel?ticker=...
            continue

        # build desired set (typically 0–2 orders per ticker)
        desired = targets.desired_orders()  # e.g., one bid + one ask

        # match existing orders that are "close enough" to keep (min churn)
        keep, replace = match_live_to_desired(live, desired, px_tol_ticks=0, min_age_ms=cfg.min_rest_ms)

        # cancel leftovers
        cancel_ids = [o.order_id for o in live if o not in keep]
        if cancel_ids:
            api.bulk_cancel(ids=",".join(map(str, cancel_ids)))

        # submit replacements (respect per-ticker rate limit scheduling)
        for o in replace:
            api.insert_order(**o.to_api_params())
```

Bulk cancel endpoint and semantics are defined in the swagger. 

---

## 7) Live runbook

### 7.1 Pre-heat checklist

1. API enabled; API key loaded; confirm `GET /case` returns ACTIVE/PAUSED correctly.
2. Pull `/limits` and log current gross/net; set internal safety margins.
3. Pull `/securities` and confirm tickers match expected 4.
4. Start in **SAFE MODE**: wide spreads, small size, longer news lockout.
5. Confirm recorder is on (for replay learning).

### 7.2 Safe-mode toggles (in config)

* `mode: SAFE | NORMAL | AGGRESSIVE`
* SAFE forces:

  * larger half-spread
  * smaller sizes
  * longer lockout
  * earlier closeout start
  * disables pennying

### 7.3 Hot reload (on-the-fly overrides)

* `config/live.yaml` polled every ~1s
* parameters applied without restart:

  * spreads, sizes, lockout, closeout thresholds
* protect with schema validation; reject malformed updates.

### 7.4 Logging/telemetry dashboards

Print each second:

* tick, seconds_into_minute, regime counts
* per ticker: mid, fv, quoted bid/ask, pos, realized+unrealized pnl
* aggregate exposure and proximity to limit
* order rate-limit status / last 429 wait
  Chart offline (replay logs):
* inventory vs time
* post-fill adverse selection distribution
* spread capture per regime
* market vs limit order share (fee vs rebate impact) 

---

## Minimum viable bot (Day 1)

1. API client + robust 429 backoff (Retry-After / wait). 
2. L1-only quoting:

   * FV = mid EMA
   * two-sided quotes with fixed half-spread and small size
3. Inventory skew + soft/hard caps
4. Minute-close staged flatten (critical to avoid $10/share penalty at each minute close). 
5. NEWS_LOCKOUT:

   * cancel and pause quoting for lockout seconds after news via `/news?since=...`. 
6. Recorder + offline replay harness skeleton

---

## Competitive bot (Day 2–5)

1. Add volatility-adaptive spreads + size throttles
2. Add jump detection from tape + JUMP_REPRICE logic
3. Online calibration “news → expected return” and per-ticker reaction scaling
4. L2-aware quoting:

   * detect shallow books; widen and reduce size
5. Smarter order reconciliation:

   * minimal churn; respect min rest time; selective replace
6. Scenario-based tuning and KPI gates per regime
7. Automated parameter sweeps on replay data (grid/random search)

---

## Concise checklist: implement first

1. **Risk + closeout core**: aggregate exposure computation + staged minute-close flatten to avoid $10/share penalties. 
2. **API robustness**: 429 backoff + per-ticker order pacing. 
3. **Order manager**: idempotent reconcile + bulk cancel. 
4. **Regimes**: NORMAL_MM + NEWS_LOCKOUT + CLOSEOUT (then add JUMP_REPRICE, INVENTORY_REBALANCE). 
5. **Recording + replay**: capture (book/tas/news/case) and replay offline for tuning.
