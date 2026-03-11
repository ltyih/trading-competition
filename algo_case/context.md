# Context Log

## Current Status
- Phase: Feature 4 implementation complete and ready for review.
- Last completed feature: Feature 4 - Core strategy logic (3-regime dry-run + fair value + quote targets).
- Next feature queued: Feature 5 - Execution and risk management (order reconciliation + limits enforcement).
- How to run: `cd algo_case && python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/python -m pytest src/*REMOVED*_mm/tests/ -v`
- Known issues: Unit tests are fully mocked and platform-independent; live runner validation still requires a reachable RIT Client API (commonly Windows VM from macOS). Feature 4 strategy mode is dry-run only and does not submit/cancel orders.

## Decisions (with rationale)
- [2026-02-07 13:50] Decision: Use `pyproject.toml` with pinned dependencies and a `src/` package layout.
  Rationale: Matches implementation constraints and provides reproducible installs with package importability.
  Impact: Project can be installed in editable mode and tested consistently.
- [2026-02-07 13:50] Decision: Use JSON-lines structured logging as the canonical logger output format.
  Rationale: Structured fields are required for diagnostics and downstream analytics.
  Impact: Console and file logs have a stable schema with ticker/regime/order/request correlation fields.
- [2026-02-07 13:50] Decision: Keep `/case` health check fail-fast with deterministic non-zero exit codes.
  Rationale: Startup failures must be obvious during development and automation runs.
  Impact: `run_live.py` exits with explicit status codes for auth, endpoint, rate-limit, server, and connectivity failures.
- [2026-02-07 13:50] Decision: Treat macOS-to-Windows VM connectivity as a first-class diagnostic path.
  Rationale: Localhost routing commonly differs when API runs in a VM.
  Impact: Connection/timeout failures log actionable hints (VM networking, host/IP, port forwarding, base URL).
- [2026-02-12 17:40] Decision: Use Pydantic models for all API response types.
  Rationale: Type-safe parsing prevents silent data corruption from unexpected API shapes.
  Impact: Every `ApiClient` method returns a validated Pydantic model; invalid responses raise `ValidationError`.
- [2026-02-12 17:40] Decision: Separate GET retry from POST/DELETE.
  Rationale: GETs are idempotent and safe to retry; POSTs (order insertion) mutate state and must not be retried automatically.
  Impact: `_get()` retries on connection/timeout/5xx; `_post()` and `_delete()` fail immediately.
- [2026-02-12 17:40] Decision: Implement proactive per-ticker rate-limit gating.
  Rationale: The RIT API's `X-Wait-Until` and `Retry-After` provide cooldown windows per ticker; proactively blocking avoids wasting API calls on 429s.
  Impact: `submit_order()` checks `RateLimitTracker` before making HTTP call; raises `RateLimitError` if ticker is in cooldown.
- [2026-02-12 17:40] Decision: All tests use mocked HTTP — no live RIT connectivity required.
  Rationale: RIT Client only runs on Windows; development is on macOS.
  Impact: Full test suite runs on any platform.
- [2026-02-13 09:10] Decision: Scope Feature 3 to ingestion/state only, with no strategy or order execution behavior.
  Rationale: Keeps implementation boundary aligned with roadmap and avoids mixing concerns before data truth is stable.
  Impact: New `data/*` modules and runner changes only ingest and log state; no quotes or order actions are emitted.
- [2026-02-13 09:10] Decision: Represent L2 books as aggregated price levels (not raw per-order queue objects).
  Rationale: Strategy math uses level depth and BBO; aggregated levels simplify downstream logic and tests.
  Impact: `parse_book_response()` combines same-price quantities and produces deterministic best-first level ordering.
- [2026-02-13 09:10] Decision: Use a single-loop deterministic scheduler for ingestion.
  Rationale: Simpler than threaded polling and sufficient for current cadence/rate-limit constraints.
  Impact: `run_live.py` maintains per-endpoint due timers but executes one ingestion update call when any timer is due.
- [2026-02-13 09:10] Decision: Continue-on-error per endpoint while retaining last good state.
  Rationale: Live ingestion should degrade gracefully during transient API issues.
  Impact: `GlobalState.update()` logs warnings and preserves prior slice values when an endpoint call fails.
- [2026-02-13 10:05] Decision: Scope Feature 4 to strategy dry-run only with no execution calls.
  Rationale: Keep boundary clean between signal generation (Feature 4) and order management (Feature 5).
  Impact: `run_live.py` in `strategy_dry_run` computes/logs targets only; no `submit_order`, `cancel_order`, or `bulk_cancel`.
- [2026-02-13 10:05] Decision: Implement Day-1 regime subset (`NORMAL_MM`, `NEWS_LOCKOUT`, `CLOSEOUT`) first.
  Rationale: Delivers practical behavior quickly while deferring jump/rebalance complexity.
  Impact: Regime engine supports closeout priority and news lockout transitions; `JUMP_REPRICE` and `INVENTORY_REBALANCE` remain future work.
- [2026-02-13 10:05] Decision: Use typed quote target dataclasses as strategy output contract.
  Rationale: Stable typed interface simplifies Feature 5 reconciliation integration and testing.
  Impact: `StrategyEngine.step()` returns per-ticker `QuoteTarget` objects with bid/ask/cancel intent and reason fields.
- [2026-02-13 10:05] Decision: Use EMA mid-price with one-shot rule-based news impulse for fair value.
  Rationale: Deterministic and testable baseline that captures first-order news effects without model-training overhead.
  Impact: `FairValueEngine` tracks per-ticker EMA and applies sentiment impulse once per new news id.

## Changes Implemented (per feature)
### Feature 1: Project scaffold + configuration + logging foundation
- Files added/changed:
  - `algo_case/pyproject.toml`
  - `algo_case/config/default.yaml`
  - `algo_case/scripts/run_live.py`
  - `algo_case/src/*REMOVED*_mm/__init__.py`
  - `algo_case/src/*REMOVED*_mm/config.py`
  - `algo_case/src/*REMOVED*_mm/api/__init__.py`
  - `algo_case/src/*REMOVED*_mm/data/__init__.py`
  - `algo_case/src/*REMOVED*_mm/strategy/__init__.py`
  - `algo_case/src/*REMOVED*_mm/execution/__init__.py`
  - `algo_case/src/*REMOVED*_mm/risk/__init__.py`
  - `algo_case/src/*REMOVED*_mm/telemetry/__init__.py`
  - `algo_case/src/*REMOVED*_mm/telemetry/logger.py`
  - `algo_case/src/*REMOVED*_mm/sim/__init__.py`
  - `algo_case/src/*REMOVED*_mm/tests/__init__.py`
  - `algo_case/src/*REMOVED*_mm/tests/test_config.py`
  - `algo_case/src/*REMOVED*_mm/tests/test_logging.py`
  - `algo_case/context.md`
- What was implemented:
  - Blueprint-aligned project scaffold under `src/*REMOVED*_mm` with module directories and package markers.
  - Pinned dependency configuration in `pyproject.toml` for Python 3.11+.
  - Baseline runtime config file with API, polling, logging, and strategy tunables from blueprint section 4.1.
  - Config loader + required-key validator (`*REMOVED*_mm.config`).
  - Structured JSON logger factory with console/file handlers and context binding (`*REMOVED*_mm.telemetry.logger`).
  - Live runner stub that loads config, initializes logging, performs a single `GET /case` health check, maps failure modes to exit codes, and logs startup outcomes.
- Tests added/updated:
  - `src/*REMOVED*_mm/tests/test_config.py` validates config loading and required keys.
  - `src/*REMOVED*_mm/tests/test_logging.py` validates logger initialization and JSON field output.
- How to verify:
  - `cd algo_case`
  - `python3 -m venv .venv && .venv/bin/pip install -e .`
  - `.venv/bin/python -m pytest src/*REMOVED*_mm/tests/test_config.py src/*REMOVED*_mm/tests/test_logging.py -q`
  - `.venv/bin/python scripts/run_live.py --config config/default.yaml`
- Notes:
  - `run_live.py` uses `X-API-Key` and calls `/case` under configured `base_url`, aligned with `rit_api_documentation.yaml` (`/v1/case`).
  - Retry behavior is limited to GET request paths only.

### Feature 2: API client skeleton with robust error handling
- Files added/changed:
  - `algo_case/src/*REMOVED*_mm/api/errors.py` — Custom exception hierarchy (6 types: `AuthenticationError`, `RateLimitError`, `ServerError`, `ConnectionFailure`, `EndpointNotFoundError`, `UnexpectedStatusError`).
  - `algo_case/src/*REMOVED*_mm/api/models.py` — Pydantic models for every endpoint response (`CaseResponse`, `SecurityResponse`, `BookResponse`, `OrderResponse`, `TasEntry`, `NewsItem`, `LimitInfo`, `CancelResult`, `OhlcEntry`, `TraderResponse`).
  - `algo_case/src/*REMOVED*_mm/api/ratelimit.py` — Per-ticker + global rate-limit tracker using monotonic clock.
  - `algo_case/src/*REMOVED*_mm/api/client.py` — Central `ApiClient` class with `requests.Session`, GET retry/backoff/jitter, 429 handling, proactive rate-limit gating, and typed return values.
  - `algo_case/src/*REMOVED*_mm/api/__init__.py` — Updated with public re-exports.
  - `algo_case/src/*REMOVED*_mm/tests/test_api.py` — 47 mocked test cases.
- What was implemented:
  - `ApiClient` using `requests.Session` for connection pooling with `X-API-Key` header.
  - Methods for all endpoints: `/case`, `/trader`, `/securities`, `/securities/book`, `/securities/history`, `/securities/tas`, `/news`, `/limits`, `/orders`, `/orders/{id}`, `/commands/cancel`.
  - GET retry loop with configurable `max_get_retries`, exponential backoff, and random jitter.
  - 429 rate-limit handling: parse `Retry-After` header and `wait` body; retry once for GETs; record in `RateLimitTracker` for POSTs.
  - Proactive rate-limit gating: `submit_order()` checks tracker before HTTP call.
  - Every HTTP call logs with unique `request_id` for full traceability.
  - Error mapping: 401→`AuthenticationError`, 404→`EndpointNotFoundError`, 429→`RateLimitError`, 5xx→`ServerError`, connection/timeout→`ConnectionFailure`.
- Tests added/updated:
  - `src/*REMOVED*_mm/tests/test_api.py` — 47 tests covering:
    - Pydantic model parsing (9 classes × multiple payloads).
    - Error hierarchy attributes.
    - `RateLimitTracker` per-ticker and global cooldown logic.
    - `ApiClient` happy path, retry on connection/timeout/5xx, 429 retry-then-success, 429 retry-then-fail, auth failure, 404, unexpected status, proactive rate-limit block, session close, and bulk cancel.
- How to verify:
  - `cd algo_case`
  - `python3 -m venv .venv && .venv/bin/pip install -e .`
  - `.venv/bin/python -m pytest src/*REMOVED*_mm/tests/ -v`
  - Expected: `50 passed` (3 Feature 1 + 47 Feature 2).
- Notes:
  - All tests use mocked HTTP via `unittest.mock` — no live RIT Client required.
  - API endpoint paths and field names strictly follow `rit_api_documentation.yaml` (Swagger 2.0, v1.0.3).
  - POST/DELETE requests are NOT retried automatically to avoid duplicate order submission.

### Feature 3: Market data ingestion and global state
- Files added/changed:
  - `algo_case/src/*REMOVED*_mm/data/book.py` — L2 aggregation + L1 projection data structures and parsing helpers.
  - `algo_case/src/*REMOVED*_mm/data/tape.py` — Per-ticker TAS ring buffers, monotonic deduplication, incremental pointers.
  - `algo_case/src/*REMOVED*_mm/data/news.py` — Incremental news storage, deduplication, and query helpers.
  - `algo_case/src/*REMOVED*_mm/data/state.py` — `GlobalState` aggregator polling `/case`, `/securities`, `/orders`, `/limits`, `/news`, `/securities/book`, `/securities/tas`.
  - `algo_case/src/*REMOVED*_mm/data/__init__.py` — Public exports for new data/state interfaces.
  - `algo_case/scripts/run_live.py` — Ingestion-only polling loop using `ApiClient` + `GlobalState` with startup health check preserved.
  - `algo_case/config/default.yaml` — Added polling keys: `loop_sleep_ms`, `book_depth`, `tape_maxlen_per_ticker`, `news_max_items`.
  - `algo_case/src/*REMOVED*_mm/tests/test_book.py` — Book aggregation and L1 tests.
  - `algo_case/src/*REMOVED*_mm/tests/test_tape.py` — TAS incremental/dedup/ring-buffer tests.
  - `algo_case/src/*REMOVED*_mm/tests/test_news.py` — News incremental/dedup/query tests.
  - `algo_case/src/*REMOVED*_mm/tests/test_state.py` — Global state aggregation and per-endpoint failure resilience tests.
- What was implemented:
  - Added pure data-layer dataclasses for books, prints, and news events.
  - Implemented incremental ingestion primitives:
    - TAS pointer tracking per ticker (`tas_after` semantics via latest accepted `id`).
    - News pointer tracking (`news_since` semantics via latest accepted `news_id`).
  - Implemented `GlobalState.update(api)` with required endpoint order:
    - `get_case()`, `get_securities()`, `get_orders(status=\"OPEN\")`, `get_limits()`, `get_news(since=...)`, then per ticker `get_book()` and `get_tas(after=...)`.
  - Implemented resilient per-slice refresh behavior:
    - endpoint failures log warnings and keep prior in-memory values.
  - Converted `run_live.py` from one-shot health check to ingestion loop:
    - keeps startup `/case` health check fail-fast behavior.
    - uses single-loop deterministic scheduling.
    - logs periodic ingestion summaries with counts, pointers, and BBO snapshots.
    - exits cleanly on non-`ACTIVE` case status or keyboard interrupt.
  - Confirmed no order-placement behavior is introduced in Feature 3.
- Tests added/updated:
  - `src/*REMOVED*_mm/tests/test_book.py` (3 tests)
  - `src/*REMOVED*_mm/tests/test_tape.py` (3 tests)
  - `src/*REMOVED*_mm/tests/test_news.py` (3 tests)
  - `src/*REMOVED*_mm/tests/test_state.py` (2 tests)
- How to verify:
  - `cd algo_case`
  - `python3 -m venv .venv && .venv/bin/pip install -e .`
  - `.venv/bin/python -m pytest src/*REMOVED*_mm/tests/ -v`
  - Expected: `61 passed` (50 existing + 11 Feature 3 tests).
  - Optional live ingestion check: `.venv/bin/python scripts/run_live.py --config config/default.yaml`
- Notes:
  - Feature boundary intentionally excludes strategy/risk/execution logic.
  - State persistence in Feature 3 is in-memory only (no disk checkpointing).

### Feature 4: Core strategy logic (dry-run)
- Files added/changed:
  - `algo_case/src/*REMOVED*_mm/strategy/regimes.py` — 3-regime state machine with per-ticker persistent state and decision reasons.
  - `algo_case/src/*REMOVED*_mm/strategy/fair_value.py` — EMA fair value engine with one-shot rule-based news impulse.
  - `algo_case/src/*REMOVED*_mm/strategy/quoting.py` — Typed quote target construction, inventory skew, hard-cap gating, and cancel logic.
  - `algo_case/src/*REMOVED*_mm/strategy/engine.py` — Orchestrator composing regimes, fair value, and quote builder.
  - `algo_case/src/*REMOVED*_mm/strategy/__init__.py` — Public exports for Feature 4 strategy interfaces.
  - `algo_case/scripts/run_live.py` — Added `runtime.run_mode` gating; strategy dry-run computes and logs targets after ingestion updates.
  - `algo_case/config/default.yaml` — Added `runtime.run_mode` and FV/news keyword tunables.
  - `algo_case/src/*REMOVED*_mm/tests/test_regimes.py` — Regime transition and closeout-priority tests.
  - `algo_case/src/*REMOVED*_mm/tests/test_fair_value.py` — EMA and news-impulse tests.
  - `algo_case/src/*REMOVED*_mm/tests/test_quoting.py` — Quote math, skew, hard-cap, and cancel behavior tests.
  - `algo_case/src/*REMOVED*_mm/tests/test_strategy_engine.py` — End-to-end strategy orchestration tests.
- What was implemented:
  - Added a `RegimeEngine` for `NORMAL_MM`, `NEWS_LOCKOUT`, and `CLOSEOUT`.
    - `CLOSEOUT` has highest priority based on minute and heat windows.
    - news lockout triggers from ticker-specific or market-wide news (`ticker == \"\"`).
  - Added `FairValueEngine`:
    - per-ticker EMA of mid-price.
    - polarity-based impulse from configured positive/negative keyword lists.
    - news impulse applied at most once per new news id per ticker.
  - Added `QuoteBuilder`:
    - normal-mode bid/ask generation from FV with fixed half-spread.
    - inventory skew via normalized position.
    - hard-cap side suppression and cancel-all fallback.
    - lockout/closeout emit cancel/no-passive targets.
  - Added `StrategyEngine.step(state)` returning typed `QuoteTarget` map.
  - Integrated dry-run mode in `run_live.py`:
    - `runtime.run_mode=ingest` keeps existing behavior.
    - `runtime.run_mode=strategy_dry_run` logs per-ticker regime/FV/target payloads.
    - no execution methods are called.
- Tests added/updated:
  - `src/*REMOVED*_mm/tests/test_regimes.py` (5 tests)
  - `src/*REMOVED*_mm/tests/test_fair_value.py` (6 tests)
  - `src/*REMOVED*_mm/tests/test_quoting.py` (5 tests)
  - `src/*REMOVED*_mm/tests/test_strategy_engine.py` (4 tests)
- How to verify:
  - `cd algo_case`
  - `python3 -m venv .venv && .venv/bin/pip install -e .`
  - `.venv/bin/python -m pytest src/*REMOVED*_mm/tests/ -v`
  - Expected: `81 passed` (61 existing + 20 Feature 4 tests).
  - Optional dry-run strategy check: set `runtime.run_mode: strategy_dry_run` in `config/default.yaml`, then run `.venv/bin/python scripts/run_live.py --config config/default.yaml`.
- Notes:
  - Feature 4 intentionally does not place/cancel orders.
  - Strategy targets are designed for Feature 5 order-manager integration.

## Open Questions / Review Items
- Item: Should `run_live.py` auto-detect VM host IP when `localhost` fails, or remain explicit via config.
- Where in code: `algo_case/scripts/run_live.py`
- Suggested resolution: Keep explicit configuration for now; add host discovery only if needed in a later feature.
