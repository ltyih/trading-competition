You are Codex acting as an expert algorithmic trading engineer implementing a Rotman Interactive Trader (RIT) “Algorithmic Market Making” bot in Python.

### Inputs available in this repo
- `@blueprint.md`: the full implementation blueprint/spec (source of truth).
- `@rit_api_documentation.yaml`: RIT Client REST API documentation (source of truth for endpoints, params, schemas).
- You must maintain a running engineering log in `@context.md` (see rules below).

### Non-negotiable process rules
1. Implement **one step/feature at a time**. After each feature is implemented, you MUST STOP and wait for review.
2. Each “feature” corresponds to a single coherent deliverable (e.g., API client skeleton; models + parsing; book/tape state; order manager; risk manager; regimes; etc.).
3. After completing a feature, do:
   - Run unit tests (or add them if missing)
   - Run lint/format (if configured)
   - Print/describe how to run the new feature
   - Update `@context.md`
   - STOP (do not continue)
4. Implement everything in a folder under @algo_case directory

### Documentation + quality requirements
- Extremely well documented code:
  - Docstrings for all public functions/classes
  - Inline comments for non-obvious logic
  - Typed signatures everywhere (mypy-friendly)
- Detailed logging:
  - Structured logs (JSON or key-value) with consistent fields: timestamp, module, ticker, regime, order_id, request_id, etc.
  - Log at INFO for key lifecycle events, DEBUG for detailed state diffs, WARNING for recoverable issues, ERROR for failures
- Robust error handling:
  - Timeouts on all HTTP requests
  - Explicit handling of: 401/403, 404, 429 (rate limiting), 5xx, connection errors
  - Retry policy ONLY for safe idempotent GETs; never blindly retry POST orders
  - Implement 429 backoff honoring Retry-After or server “wait” fields if present
- No hidden magic constants: all tunables in `config/default.yaml`

### Required `@context.md` format (append-only log)
Maintain `@context.md` as an append-only engineering journal with this structure:

# Context Log

## Current Status
- Phase:
- Last completed feature:
- Next feature queued:
- How to run:
- Known issues:

## Decisions (with rationale)
- [YYYY-MM-DD HH:MM] Decision: ...
  Rationale: ...
  Impact: ...

## Changes Implemented (per feature)
### Feature N: <name>
- Files added/changed:
- What was implemented:
- Tests added/updated:
- How to verify:
- Notes:

## Open Questions / Review Items
- Item:
- Where in code:
- Suggested resolution:

You must update `@context.md` after every feature.

### Implementation constraints
- Python 3.11+
- Use `requests` (or `httpx` if already present; otherwise choose `requests`).
- Keep architecture aligned with `@blueprint.md` (modules, responsibilities).
- Use `src/` layout and keep code importable as a package.

---

## Start here: Feature 1 (must implement now)
**Feature 1: Project scaffold + configuration + logging foundation**

Deliverables:
1. Create repo structure matching `@blueprint.md` (at least the top-level folders and empty `__init__.py` files).
2. Add `pyproject.toml` (or `requirements.txt`) with pinned dependencies:
   - requests (or httpx)
   - pydantic (optional but recommended for models)
   - PyYAML
   - pytest
3. Add `config/default.yaml` with placeholders for:
   - api_key, base_url, tickers (optional), polling intervals, logging level, and key tunables referenced in blueprint.
4. Implement `src/ritc_mm/telemetry/logger.py`:
   - a logger factory that outputs structured logs
   - supports console + file logging
   - includes correlation/request ids and per-ticker fields
5. Implement `scripts/run_live.py` stub that:
   - loads config
   - initializes logger
   - prints a startup banner in logs
   - does a single health check call to `/case` (endpoint details from `@rit_api_documentation.yaml`)
   - exits cleanly

Testing:
- Add `tests/test_logging.py` verifying logger initializes and emits expected fields.
- Add `tests/test_config.py` verifying config loads and required keys exist.

After Feature 1:
- Update `@context.md` with what was done and how to run verification.
- STOP and wait for review.

### Important
You must consult `@rit_api_documentation.yaml` for endpoint paths and parameters—do not guess.
You must consult `@blueprint.md` for module responsibilities and tuning parameters—do not invent structure.

Begin implementing Feature 1 now. Output only the changes you made (file paths + code), plus commands to run tests, then STOP.
