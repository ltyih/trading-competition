"""Configuration for RITC 2026 Producer Fuel Planner.

API base URL, API key (from env or default), and poll intervals for
case, news, securities, and limits endpoints.
"""

import os

# RIT Client REST API (Swagger v1.0.3)
API_BASE_URL: str = os.environ.get("RIT_API_BASE_URL", "http://localhost:9999/v1")
API_KEY: str = os.environ.get("RIT_API_KEY", "L5NIT389")

# Poll intervals in seconds
POLL_CASE_INTERVAL: float = 2.0
POLL_NEWS_INTERVAL: float = 3.0
POLL_SECURITIES_INTERVAL: float = 2.0
POLL_LIMITS_INTERVAL: float = 3.0

# 429 retry
MAX_429_WAIT_SECONDS: float = 60.0
DEFAULT_429_WAIT_SECONDS: float = 5.0
