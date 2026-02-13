# Context Log

## Current Status
- Phase: Feature 2 implementation complete and ready for review.
- Last completed feature: Feature 2 - API client skeleton with robust error handling and GET retry/backoff policy.
- Next feature queued: Feature 3 - Market data ingestion and state persistence.
- How to run: `cd algo_case && python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/python -m pytest src/ritc_mm/tests/ -v`
- Known issues: Live API connectivity tests will fail on macOS; all tests use mocked HTTP so macOS development is fully supported.

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

## Changes Implemented (per feature)
### Feature 1: Project scaffold + configuration + logging foundation
- Files added/changed:
  - `algo_case/pyproject.toml`
  - `algo_case/config/default.yaml`
  - `algo_case/scripts/run_live.py`
  - `algo_case/src/ritc_mm/__init__.py`
  - `algo_case/src/ritc_mm/config.py`
  - `algo_case/src/ritc_mm/api/__init__.py`
  - `algo_case/src/ritc_mm/data/__init__.py`
  - `algo_case/src/ritc_mm/strategy/__init__.py`
  - `algo_case/src/ritc_mm/execution/__init__.py`
  - `algo_case/src/ritc_mm/risk/__init__.py`
  - `algo_case/src/ritc_mm/telemetry/__init__.py`
  - `algo_case/src/ritc_mm/telemetry/logger.py`
  - `algo_case/src/ritc_mm/sim/__init__.py`
  - `algo_case/src/ritc_mm/tests/__init__.py`
  - `algo_case/src/ritc_mm/tests/test_config.py`
  - `algo_case/src/ritc_mm/tests/test_logging.py`
  - `algo_case/context.md`
- What was implemented:
  - Blueprint-aligned project scaffold under `src/ritc_mm` with module directories and package markers.
  - Pinned dependency configuration in `pyproject.toml` for Python 3.11+.
  - Baseline runtime config file with API, polling, logging, and strategy tunables from blueprint section 4.1.
  - Config loader + required-key validator (`ritc_mm.config`).
  - Structured JSON logger factory with console/file handlers and context binding (`ritc_mm.telemetry.logger`).
  - Live runner stub that loads config, initializes logging, performs a single `GET /case` health check, maps failure modes to exit codes, and logs startup outcomes.
- Tests added/updated:
  - `src/ritc_mm/tests/test_config.py` validates config loading and required keys.
  - `src/ritc_mm/tests/test_logging.py` validates logger initialization and JSON field output.
- How to verify:
  - `cd algo_case`
  - `python3 -m venv .venv && .venv/bin/pip install -e .`
  - `.venv/bin/python -m pytest src/ritc_mm/tests/test_config.py src/ritc_mm/tests/test_logging.py -q`
  - `.venv/bin/python scripts/run_live.py --config config/default.yaml`
- Notes:
  - `run_live.py` uses `X-API-Key` and calls `/case` under configured `base_url`, aligned with `rit_api_documentation.yaml` (`/v1/case`).
  - Retry behavior is limited to GET request paths only.

### Feature 2: API client skeleton with robust error handling
- Files added/changed:
  - `algo_case/src/ritc_mm/api/errors.py` — Custom exception hierarchy (6 types: `AuthenticationError`, `RateLimitError`, `ServerError`, `ConnectionFailure`, `EndpointNotFoundError`, `UnexpectedStatusError`).
  - `algo_case/src/ritc_mm/api/models.py` — Pydantic models for every endpoint response (`CaseResponse`, `SecurityResponse`, `BookResponse`, `OrderResponse`, `TasEntry`, `NewsItem`, `LimitInfo`, `CancelResult`, `OhlcEntry`, `TraderResponse`).
  - `algo_case/src/ritc_mm/api/ratelimit.py` — Per-ticker + global rate-limit tracker using monotonic clock.
  - `algo_case/src/ritc_mm/api/client.py` — Central `ApiClient` class with `requests.Session`, GET retry/backoff/jitter, 429 handling, proactive rate-limit gating, and typed return values.
  - `algo_case/src/ritc_mm/api/__init__.py` — Updated with public re-exports.
  - `algo_case/src/ritc_mm/tests/test_api.py` — 47 mocked test cases.
- What was implemented:
  - `ApiClient` using `requests.Session` for connection pooling with `X-API-Key` header.
  - Methods for all endpoints: `/case`, `/trader`, `/securities`, `/securities/book`, `/securities/history`, `/securities/tas`, `/news`, `/limits`, `/orders`, `/orders/{id}`, `/commands/cancel`.
  - GET retry loop with configurable `max_get_retries`, exponential backoff, and random jitter.
  - 429 rate-limit handling: parse `Retry-After` header and `wait` body; retry once for GETs; record in `RateLimitTracker` for POSTs.
  - Proactive rate-limit gating: `submit_order()` checks tracker before HTTP call.
  - Every HTTP call logs with unique `request_id` for full traceability.
  - Error mapping: 401→`AuthenticationError`, 404→`EndpointNotFoundError`, 429→`RateLimitError`, 5xx→`ServerError`, connection/timeout→`ConnectionFailure`.
- Tests added/updated:
  - `src/ritc_mm/tests/test_api.py` — 47 tests covering:
    - Pydantic model parsing (9 classes × multiple payloads).
    - Error hierarchy attributes.
    - `RateLimitTracker` per-ticker and global cooldown logic.
    - `ApiClient` happy path, retry on connection/timeout/5xx, 429 retry-then-success, 429 retry-then-fail, auth failure, 404, unexpected status, proactive rate-limit block, session close, and bulk cancel.
- How to verify:
  - `cd algo_case`
  - `python3 -m venv .venv && .venv/bin/pip install -e .`
  - `.venv/bin/python -m pytest src/ritc_mm/tests/ -v`
  - Expected: `50 passed` (3 Feature 1 + 47 Feature 2).
- Notes:
  - All tests use mocked HTTP via `unittest.mock` — no live RIT Client required.
  - API endpoint paths and field names strictly follow `rit_api_documentation.yaml` (Swagger 2.0, v1.0.3).
  - POST/DELETE requests are NOT retried automatically to avoid duplicate order submission.

## Open Questions / Review Items
- Item: Should `run_live.py` auto-detect VM host IP when `localhost` fails, or remain explicit via config.
- Where in code: `algo_case/scripts/run_live.py`
- Suggested resolution: Keep explicit configuration for now; add host discovery only if needed in a later feature.
