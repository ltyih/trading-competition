"""
Electricity Case - News Monitor & Demand Calculator
*REMOVED* 2026

Polls the RIT news feed every 2 ticks and prints temperature forecasts
with computed electricity demand using:
    ELEC_customers = 200 - 15*AT + 0.8*AT^2 - 0.01*AT^3
"""

import sys
import time
import requests

from config import API_BASE_URL, API_KEY
from news_parser import TemperatureState, compute_demand

BANNER = r"""
============================================================
  *REMOVED* 2026 - Electricity Case: Demand Monitor
  Model: ELEC = 200 - 15*AT + 0.8*AT^2 - 0.01*AT^3
============================================================
"""

POLL_EVERY_N_TICKS = 2


class *REMOVED*onnection:
    """Minimal RIT API client — just enough for news + tick polling."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": API_KEY})
        self.base = API_BASE_URL

    def get(self, endpoint, params=None):
        try:
            resp = self.session.get(f"{self.base}{endpoint}", params=params, timeout=5)
            if resp.status_code == 429:
                time.sleep(float(resp.json().get("wait", 0.5)))
                return self.get(endpoint, params)
            if resp.ok:
                return resp.json()
        except requests.exceptions.ConnectionError:
            return None
        except Exception as e:
            print(f"  [API error] {e}")
        return None

    def get_case(self):
        return self.get("/case")

    def get_tick(self):
        case = self.get_case()
        return case.get("tick", 0) if case else 0

    def get_status(self):
        case = self.get_case()
        return case.get("status", "UNKNOWN") if case else "UNKNOWN"

    def get_news(self, since=0):
        params = {"limit": 50}
        if since > 0:
            params["since"] = since
        return self.get("/news", params)

    def is_connected(self):
        return self.get_case() is not None


def print_separator():
    print("-" * 70)


def print_demand_table(state: TemperatureState):
    """Print a formatted table of all known day forecasts and demands."""
    if not state.forecasts:
        print("  No temperature forecasts received yet.")
        return

    print(f"  {'Day':>4} | {'Type':>7} | {'Temp':>14} | "
          f"{'Metric':>11} | {'Value/Range':>16}")
    print(f"  {'----':>4}-+-{'-------':>7}-+-{'--------------':>14}-+-"
          f"{'-----------':>11}-+-{'----------------':>16}")

    for day in sorted(state.forecasts.keys()):
        low, high = state.forecasts[day]
        is_exact = (low == high)

        if is_exact:
            temp_str = f"{low:.1f}C"
            kind = "EXACT"
        else:
            temp_str = f"{low:.1f}-{high:.1f}C"
            kind = "RANGE"

        mid = (low + high) / 2.0
        demand_mid = compute_demand(mid)
        demand_range = state.get_demand_range(day)
        d_lo = demand_range[0] if demand_range else 0
        d_hi = demand_range[1] if demand_range else 0
        forwa_low = d_lo / 5
        forwa_mid = demand_mid / 5
        forwa_high = d_hi / 5


        if is_exact:
            demand_str = f"{demand_mid:>16.2f}"
            forwa_str = f"{forwa_mid:>16.2f}"
        else:
            demand_str = f"{d_lo:>7.2f} - {d_hi:.2f}"
            forwa_str = f"{forwa_low:>7.2f} - {forwa_high:.2f}"

        print(f"  {day:>4} | {kind:>7} | {temp_str:>14} | "
              f"{'Demand(mid)':>11} | {demand_str}")
        print(f"  {'':>4} | {'':>7} | {'':>14} | "
              f"{'Forward':>11} | {forwa_str}")


def main():
    print(BANNER)
    api = *REMOVED*onnection()

    print("Waiting for RIT connection...")
    while not api.is_connected():
        time.sleep(1)
    print("Connected to RIT!\n")

    state = TemperatureState()
    last_tick = -1
    current_period = None
    waiting_for_next_heat = False

    print(f"Polling news every {POLL_EVERY_N_TICKS} ticks. Press Ctrl+C to stop.\n")
    print_separator()

    try:
        while True:
            case = api.get_case()
            if not case:
                time.sleep(0.5)
                continue

            status = case.get("status", "")
            tick = case.get("tick", 0)
            period = case.get("period", "?")

            if status not in ("ACTIVE", "RUNNING"):
                if not waiting_for_next_heat:
                    print(
                        f"\n  Heat {current_period if current_period is not None else period} "
                        f"ended (status: {status}). Waiting for next heat..."
                    )
                    waiting_for_next_heat = True
                time.sleep(1)
                continue
            if waiting_for_next_heat:
                print(f"\n  New heat detected (Period {period}). Resuming...")
                waiting_for_next_heat = False

            if current_period != period:
                if current_period is not None:
                    print(f"  Switching from Period {current_period} to Period {period}.")
                # Reset per-heat state so news IDs/forecasts do not leak across heats
                state = TemperatureState()
                last_tick = -1
                current_period = period

            # Only act every N ticks
            if tick == last_tick or (tick - last_tick) < POLL_EVERY_N_TICKS:
                time.sleep(0.1)
                continue

            last_tick = tick

            # Fetch and process news
            news = api.get_news(since=state.last_news_id)
            new_data = False
            if news:
                for item in news:
                    nid = item.get("news_id", "?")
                    hl = item.get("headline", "")
                    if hl:
                        print(f"  [RAW NEWS] id={nid} | {hl}")
                new_data = state.process_news(news)

            # Print status line
            print(f"\n  Tick {tick:>3} | Period {period} | Status: {status}")

            if new_data:
                print("  >>> NEW FORECAST RECEIVED <<<")

            print_demand_table(state)
            print_separator()

            sys.stdout.flush()

    except KeyboardInterrupt:
        print("\n\nShutting down. Final state:")
        print_demand_table(state)
        print("\nGoodbye.")


if __name__ == "__main__":
    main()
