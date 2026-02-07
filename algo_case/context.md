# Context Log

## Current Status
- Phase: Feature 1 implementation complete and ready for review.
- Last completed feature: Feature 1 - Project scaffold + configuration + logging foundation.
- Next feature queued: Feature 2 - API client skeleton with robust error handling and GET retry/backoff policy.
- How to run: `cd algo_case && python3.11 -m venv .venv && source .venv/bin/activate && pip install -U pip && pip install -e . && pytest src/ritc_mm/tests/test_config.py src/ritc_mm/tests/test_logging.py -q && python scripts/run_live.py --config config/default.yaml`
- Known issues: `scripts/run_live.py` may fail to connect to `localhost:9999` from macOS when RIT Client runs only inside a Windows VM; this is logged with explicit diagnostics and exit code `6`.

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
  - `python3.11 -m venv .venv && source .venv/bin/activate`
  - `pip install -U pip`
  - `pip install -e .`
  - `pytest src/ritc_mm/tests/test_config.py src/ritc_mm/tests/test_logging.py -q`
  - `python scripts/run_live.py --config config/default.yaml`
- Notes:
  - `run_live.py` uses `X-API-Key` and calls `/case` under configured `base_url`, aligned with `rit_api_documentation.yaml` (`/v1/case`).
  - Retry behavior is limited to GET request paths only.

## Open Questions / Review Items
- Item: Should `run_live.py` auto-detect VM host IP when `localhost` fails, or remain explicit via config.
- Where in code: `algo_case/scripts/run_live.py`
- Suggested resolution: Keep explicit configuration for now; add host discovery only if needed in a later feature.
