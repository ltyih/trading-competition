"""Shared RIT API client for main.py and web_ui.py."""

from __future__ import annotations

import json
import time
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import config


def api_get(path: str, params: Optional[dict[str, str]] = None) -> Any:
    """GET with X-API-Key; on 429 use Retry-After or body 'wait', sleep, retry. Returns JSON."""
    base = config.API_BASE_URL.rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    if params:
        q = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{q}"
    req = Request(url, headers={"X-API-Key": config.API_KEY})
    max_429_retries = 5
    for attempt in range(max_429_retries):
        try:
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            if e.code != 429:
                raise
            wait = config.DEFAULT_429_WAIT_SECONDS
            try:
                body = e.read().decode()
                data = json.loads(body)
                if isinstance(data, dict) and "wait" in data:
                    wait = min(float(data["wait"]), config.MAX_429_WAIT_SECONDS)
            except Exception:
                pass
            if e.headers.get("Retry-After"):
                try:
                    wait = min(float(e.headers["Retry-After"]), config.MAX_429_WAIT_SECONDS)
                except ValueError:
                    pass
            time.sleep(wait)
            continue
        except URLError as e:
            if attempt < max_429_retries - 1:
                time.sleep(config.DEFAULT_429_WAIT_SECONDS)
                continue
            raise
    return None
