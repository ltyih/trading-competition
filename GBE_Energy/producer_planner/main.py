"""Main loop: poll RIT API, parse news, run planner, print recommendation."""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import config
from parsers import parse_sunlight, parse_spot_bulletin, SunlightResult, SpotBulletinResult
from planner import PlannerInputs, PlannerOutput, compute_recommendation
from ui import (
    render_case,
    render_news,
    render_prices,
    render_limits,
    render_supply_base,
    render_recommendations,
    render_lock_in,
    print_news_item,
)


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


def wait_for_active() -> dict[str, Any]:
    """Poll GET /case until status is ACTIVE."""
    while True:
        try:
            data = api_get("case")
            if isinstance(data, dict) and data.get("status") == "ACTIVE":
                return data
        except Exception as e:
            print(f"Case poll error: {e}", file=sys.stderr)
        time.sleep(config.POLL_CASE_INTERVAL)


def main_loop() -> None:
    last_period: Optional[int] = None
    last_news_id: int = 0
    last_sunlight: Optional[SunlightResult] = None
    last_spot: Optional[SpotBulletinResult] = None
    tomorrow_day: Optional[int] = None

    print("Waiting for case ACTIVE...")
    case = wait_for_active()
    period = case.get("period")
    tick = case.get("tick")
    status = case.get("status") or "ACTIVE"
    last_period = period
    render_case(period, tick, status)

    # Ticker patterns
    elec_day_pattern = re.compile(r"^ELEC-day\d+$", re.IGNORECASE)

    while True:
        try:
            case = api_get("case")
            if not isinstance(case, dict):
                time.sleep(config.POLL_CASE_INTERVAL)
                continue
            status = case.get("status")
            if status != "ACTIVE":
                print(f"Case status: {status}; waiting for ACTIVE...")
                time.sleep(config.POLL_CASE_INTERVAL)
                continue
            period = case.get("period")
            tick = case.get("tick")
            if last_period is not None and period != last_period:
                last_period = period
                last_news_id = 0
                last_sunlight = None
                last_spot = None
                tomorrow_day = None
                print(f"New period: {period}; state reset.")
            last_period = period

            # News (incremental)
            news_list = api_get("news", params={"since": str(last_news_id)})
            if isinstance(news_list, list):
                for item in news_list:
                    if isinstance(item, dict):
                        nid = item.get("news_id")
                        if nid is not None and nid > last_news_id:
                            last_news_id = nid
                        headline = item.get("headline") or ""
                        body = item.get("body") or ""
                        sun = parse_sunlight(headline, body)
                        if sun is not None:
                            last_sunlight = sun
                            tomorrow_day = sun.delivery_day or (period + 1 if period is not None else None)
                            print_news_item("SUNLIGHT", f"day={sun.delivery_day} exact={sun.is_exact()} mid={sun.mid_hours()}")
                        spot = parse_spot_bulletin(headline, body)
                        if spot is not None:
                            last_spot = spot
                            if tomorrow_day is None:
                                tomorrow_day = spot.delivery_day
                            print_news_item("SPOT", f"day={spot.delivery_day} price={spot.spot_price} vol={spot.spot_contract_volume}")
            time.sleep(0.1)

            # Securities
            sec_list = api_get("securities")
            ng_ask: Optional[float] = None
            elec_f_bid_size: Optional[float] = None
            if isinstance(sec_list, list):
                for s in sec_list:
                    if not isinstance(s, dict):
                        continue
                    ticker = (s.get("ticker") or "").strip()
                    if ticker.upper() == "NG":
                        v = s.get("ask")
                        if v is not None:
                            try:
                                ng_ask = float(v)
                            except (TypeError, ValueError):
                                pass
                    elif ticker.upper() == "ELEC-F":
                        v = s.get("bid_size")
                        if v is not None:
                            try:
                                elec_f_bid_size = float(v)
                            except (TypeError, ValueError):
                                pass

            # Limits
            limits_list = api_get("limits")

            # Display: News, Prices, Limits (rich or plain)
            render_news(last_sunlight, last_spot)
            render_prices(sec_list, elec_day_pattern)
            render_limits(limits_list)

            # Manual input
            try:
                raw = input("\nDistributor demand (ELEC contracts, Enter=0): ").strip()
                distributor_demand = float(raw) if raw else 0.0
            except (ValueError, EOFError):
                distributor_demand = 0.0
            try:
                raw = input("Tender net (ELEC contracts, Enter=0): ").strip()
                tender_net = float(raw) if raw else 0.0
            except (ValueError, EOFError):
                tender_net = 0.0

            # Planner inputs
            mid_h = last_sunlight.mid_hours() if last_sunlight else None
            spot_price = last_spot.spot_price if last_spot else None
            spot_vol = last_spot.spot_contract_volume if last_spot else None
            sunlight_exact = last_sunlight.is_exact() if last_sunlight else False
            if tomorrow_day is None and period is not None:
                tomorrow_day = period + 1

            inp = PlannerInputs(
                distributor_demand=distributor_demand,
                tender_net=tender_net,
                mid_sunlight_hours=mid_h,
                spot_price=spot_price,
                spot_volume=spot_vol,
                ng_ask=ng_ask,
                elec_f_bid_size=elec_f_bid_size,
                disposal_budget_contracts=0.0,
                sunlight_is_exact=sunlight_exact,
            )
            out = compute_recommendation(inp, tomorrow_day=tomorrow_day)

            render_supply_base(out)
            render_recommendations(out)
            render_lock_in(out.lock_in_window)

        except KeyboardInterrupt:
            print("\nExiting.")
            break
        except Exception as e:
            print(f"Loop error: {e}", file=sys.stderr)
            time.sleep(config.POLL_CASE_INTERVAL)


if __name__ == "__main__":
    main_loop()
