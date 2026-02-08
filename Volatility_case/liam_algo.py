# -*- coding: utf-8 -*-
"""Robust volatility + delta hedge algorithm."""

import re
import signal
import threading
from time import sleep

import numpy as np
import pandas as pd
import requests

try:
    from pynput import keyboard
except Exception:  # pragma: no cover - optional dependency
    keyboard = None

from library import (
    ApiException,
    DEFAULT_BASE_URL,
    calculate_bs_price,
    calculate_hedge_ratios,
    get_data,
    get_delta_limit,
    headline_vol,
    limit_order,
    market_order,
)

max_exposure=5000
API_KEY = {"X-API-Key": "LIAMAPI"}
shutdown = False
clear_positions_flag = False
restart_flag = False
clear_all_flag = False
gamma_swi="ON"


# ----------------------------- NEW FUNCTIONS START -----------------------------------------

def parse_delta_limit(news_string):
    """
    Extract delta limit from news string.
    Example: "The delta limit for this sub-heat is 10,000 and the penalty percentage is 1%"
    Returns: delta limit as a float
    """
    match = re.search(r'(\d+(?:,\d+)*(?:\.\d+)?)', news_string)
    if match:
        # Remove commas and convert to float
        return float(match.group(1).replace(',', ''))
    return None


def parse_annualized_volatility(news_string):
    """
    Extract annualized volatility from news string (already in sqrt(year) units).
    No conversion needed - use directly.
    
    Handles two formats:
    1. Tick 1: "...current annualized realized volatility is 27%..."
       Extract the SECOND percentage (27%)
    2. Tick 74+: "...realized volatility of RTM this week will be 26%"
       Extract the percentage (26%)
    
    Returns: volatility as a decimal (e.g., 0.18 for 18%)
    """
    # Find all percentages in the string
    matches = re.findall(r'(\d+(?:\.\d+)?)%', news_string)
    
    if not matches:
        return None
    
    # If this is tick 1 news (contains "risk free rate"), take the SECOND percentage
    if "risk free rate" in news_string.lower():
        if len(matches) >= 2:
            return float(matches[1]) / 100.0  # Second percentage is realized vol
        else:
            return None
    
    # Otherwise, take the FIRST percentage
    return float(matches[0]) / 100.0


def optimising_function(realised_sigma, implied_sigma, delta, gamma, current_price, current_position, L):
    """
    Function to define the optimising function, in which we maximise it over n
    |n| is an integer that ranges from 0 to 50, which is small enough to check each n individually
    current_position takes the form of N, an integer that ranges from -50 to +50 straddles,
    where negative N means a short position and positive means long positions
    
    Args:
        realised_sigma: Realized volatility per tick
        implied_sigma: Implied volatility (from market)
        delta: Delta of the straddle
        gamma: Gamma of the straddle
        current_price: Current stock price S
        current_position: Current number of straddles held (-50 to +50)
        L: Delta limit
    
    Returns:
        optimal_n: Target position in straddles (-50 to +50)
    """
    rsig = realised_sigma
    isig = implied_sigma
    S = current_price
    T = 1.0 / 52.0  # 1 week in years
    
    # Determine direction based on volatility difference
    vol_diff_sq = rsig**2 - isig**2
    gamma_per_contract = gamma * 100  # Scale for contract multiplier
    print(f"  🔍 Vol diff: rsig²={rsig**2:.8f}, isig²={isig**2:.8f}, diff={vol_diff_sq:.8f}")
    print(f"  🔍 Gamma: per-share={gamma:.6f}, per-contract={gamma_per_contract:.4f}")
    print(f"  🔍 Time horizon: T={T:.6f} years (1 week)")
    
    if vol_diff_sq > 1e-8:  # Small threshold for numerical stability
        direction = 1
    elif vol_diff_sq < -1e-8:
        direction = -1
    else:
        direction = 0
        print(f"  ⚠️ Direction=0 (vol diff too small), returning 0")
    
    if direction == 0:
        return 0
    
    # Avoid division by zero
    if gamma == 0:
        print(f"  ⚠️ Gamma=0, returning 0")
        return 0
    
    def SC(n):
        """Safe corridor function
        
        Note: gamma from BS formula is per-share, but straddles have 100 shares per contract.
        So we multiply gamma by 100 to get gamma per straddle contract.
        """
        if n == 0:
            return 0
        # Scale gamma by contract multiplier (100 shares per option contract)
        gamma_per_contract = gamma * 100
        return L / (100 * n * gamma_per_contract)
    
    def gain(n):
        """Gain from gamma scalping over the week (T years)
        
        Note: gamma from BS formula is per-share, but straddles have 100 shares per contract.
        With annualized volatilities, the gain formula becomes:
        gain = 50 * n * gamma_per_contract * S^2 * |rsig^2 - isig^2| / rsig^2 * (exp(rsig^2 * T) - 1)
        where T is the time horizon in years (1 week = 1/52 years)
        """
        if n == 0:
            return 0
        volatility_diff = abs(rsig**2 - isig**2)
        if rsig**2 == 0:
            return 0
        # Scale gamma by contract multiplier (100 shares per option contract)
        gamma_per_contract = gamma * 100
        return 50 * n * gamma_per_contract * S**2 * volatility_diff / rsig**2 * (np.exp(rsig**2 * T) - 1)
    
    def reposition_cost(n):
        """Cost to reposition from current_position to direction*n"""
        return (2 + abs(delta)) * abs(direction * n - current_position)
    
    def rebalancing_cost(n):
        """Cost of rebalancing within safe corridor"""
        if n == 0:
            return 0
        sc = SC(n)
        if sc == 0 or sc >= S:
            if n <= 5:
                print(f"    ⚠️ n={n}: SC={sc:.2f} >= S={S:.2f}, returning 1e10")
            return 1e10  # Very high cost to discourage this
        
        # Calculate log terms
        term1 = 2 * np.log(S / (S - sc))
        term2 = np.log((S + sc) / (S - sc))
        denominator = term1 - term2
        
        if n <= 5:
            print(f"    n={n}: SC={sc:.2f}, term1={term1:.4f}, term2={term2:.4f}, denom={denominator:.4f}")
        
        if denominator <= 0:
            if n <= 5:
                print(f"    ⚠️ n={n}: denominator={denominator:.4f} <= 0, returning 1e10")
            return 1e10
        
        return L / 200 * rsig**2 * T / denominator
    
    def profit(n):
        """Total profit function"""
        g = gain(n)
        rc = reposition_cost(n)
        rbc = rebalancing_cost(n)
        total = g - rc - rbc
        return total
    
    # Find optimal n by checking all values from 0 to 50
    optimal_n = 0
    max_profit = profit(0)
    
    # Debug first few iterations
    for n in range(1, min(6, 51)):
        current_profit = profit(n)
        if n <= 5:
            print(f"  n={n}: gain={gain(n):.2f}, repo_cost={reposition_cost(n):.2f}, "
                  f"rebal_cost={rebalancing_cost(n):.2f}, profit={current_profit:.2f}")
        if current_profit > max_profit:
            max_profit = current_profit
            optimal_n = n
    
    # Continue checking rest of range
    for n in range(6, 51):
        current_profit = profit(n)
        if current_profit > max_profit:
            max_profit = current_profit
            optimal_n = n
    
    # Return signed optimal position
    result = direction * optimal_n
    print(f"  ✅ Optimal n={optimal_n}, direction={direction}, result={result}, max_profit={max_profit:.2f}")
    return result


def find_atm_strike(options, spot):
    """
    Find the strike price closest to the current spot price.
    
    Args:
        options: DataFrame of options
        spot: Current spot price
    
    Returns:
        ATM strike price (float), or None if no options available
    """
    if options.empty:
        return None
    
    strikes = sorted(options["strike"].dropna().unique())
    if not strikes:
        return None
    
    # Find strike closest to spot
    atm_strike = min(strikes, key=lambda k: abs(k - spot))
    return float(atm_strike)


def close_all_straddles_at_strike(session, options, strike):
    """
    Close all straddle positions at a specific strike.
    
    Args:
        session: API session
        options: DataFrame of options
        strike: Strike price to close
    
    Returns:
        Number of trades executed
    """
    if options.empty:
        return 0
    
    # Get options at the strike
    strike_options = options[options["strike"] == strike]
    
    if strike_options.empty:
        print(f"  ℹ️ No positions at strike {strike} to close")
        return 0
    
    trades_executed = 0
    
    for opt_type in ["CALL", "PUT"]:
        opts = strike_options[strike_options["type"] == opt_type]
        if opts.empty:
            continue
        
        ticker = str(opts["ticker"].iloc[0])
        position = opts["position"].iloc[0] if "position" in opts.columns else 0
        
        if position == 0:
            continue
        
        # Close the position (reverse the action)
        action = "SELL" if position > 0 else "BUY"
        quantity = abs(int(position))
        
        try:
            market_order(session, ticker, quantity, action)
            print(f"  🔴 Closing {quantity} {ticker}: {action}")
            trades_executed += 1
        except Exception as e:
            print(f"  ⚠️ Error closing {ticker}: {e}")
    
    return trades_executed


def get_straddle_greeks(options, spot, fixed_strike=50):

    """
    Get the delta and gamma of a straddle at a fixed strike.
    
    Args:
        options: DataFrame of options
        spot: Current spot price
        fixed_strike: The strike price to use (default 50)
    
    Returns:
        (delta, gamma): Combined delta and gamma of call + put straddle at fixed strike
    """
    if options.empty:
        return 0.0, 0.0
    
    # Find options at the fixed strike
    strike_options = options[options["strike"] == fixed_strike]
    
    if strike_options.empty:
        print(f"  ⚠️ No options found at strike {fixed_strike}")
        return 0.0, 0.0
    
    # Get call and put at fixed strike
    call = strike_options[strike_options["type"] == "CALL"]
    put = strike_options[strike_options["type"] == "PUT"]
    
    if call.empty or put.empty:
        print(f"  ⚠️ Missing call or put at strike {fixed_strike}")
        return 0.0, 0.0
    
    delta_call = call["delta"].iloc[0] if not call.empty and "delta" in call.columns else 0.0
    delta_put = put["delta"].iloc[0] if not put.empty and "delta" in put.columns else 0.0
    
    # For gamma, we need to calculate it from BS formula or estimate
    # Gamma is the same for call and put at the same strike
    gamma_call = call["gamma"].iloc[0] if not call.empty and "gamma" in call.columns else 0.0
    gamma_put = put["gamma"].iloc[0] if not put.empty and "gamma" in put.columns else 0.0
    
    # If gamma not available, estimate it
    if gamma_call == 0 and gamma_put == 0:
        # Simple estimation: gamma ≈ 0.01 for ATM options (rough approximation)
        gamma_call = 0.01
        gamma_put = 0.01
    
    # Straddle delta and gamma
    straddle_delta = delta_call + delta_put
    straddle_gamma = gamma_call + gamma_put
    
    return straddle_delta, straddle_gamma


def execute_straddle_trades(session, options, target_position, current_straddle_position, fixed_strike=50):
    """
    Execute trades to reach target straddle position at a fixed strike.
    
    Args:
        session: API session
        options: DataFrame of options
        target_position: Target number of straddles (-50 to +50)
        current_straddle_position: Current number of straddles held
        fixed_strike: The strike price to trade (default 50)
    
    Returns:
        Number of trades executed
    """
    if options.empty:
        return 0
    
    # Calculate how many straddles to trade
    straddles_to_trade = target_position - current_straddle_position
    
    if straddles_to_trade == 0:
        return 0
    
    # Get options at the fixed strike
    strike_options = options[options["strike"] == fixed_strike]
    
    if strike_options.empty:
        print(f"  ⚠️ No options available at strike {fixed_strike} for trading")
        return 0
    
    # Get call and put at fixed strike
    call = strike_options[strike_options["type"] == "CALL"]
    put = strike_options[strike_options["type"] == "PUT"]
    
    if call.empty or put.empty:
        print(f"  ⚠️ Missing call or put at strike {fixed_strike} for trading")
        return 0
    
    call_ticker = str(call["ticker"].iloc[0])
    put_ticker = str(put["ticker"].iloc[0])
    
    # Determine action (BUY if positive, SELL if negative)
    action = "BUY" if straddles_to_trade > 0 else "SELL"
    quantity = abs(straddles_to_trade)
    
    print(f"  📝 Trading {quantity} straddles at strike {fixed_strike}: {action} {call_ticker} + {put_ticker}")
    
    # Each straddle is 1 call + 1 put
    trades_executed = 0
    
    try:
        # Trade calls
        market_order(session, call_ticker, quantity, action)
        trades_executed += 1
        
        # Trade puts
        market_order(session, put_ticker, quantity, action)
        trades_executed += 1
    except Exception as e:
        print(f"Error executing straddle trades: {e}")
    
    return trades_executed


def calculate_current_straddle_position(assets, fixed_strike=50):
    """
    Calculate current number of straddles from positions at a fixed strike.
    Only counts straddles at the specified strike.
    
    Args:
        assets: DataFrame of assets
        fixed_strike: The strike price to count (default 50)
    
    Returns:
        Number of straddles at fixed_strike (can be negative for short positions)
    """
    if assets.empty:
        return 0
    
    options = assets[assets["type"].isin(["CALL", "PUT"])].copy()
    if options.empty:
        return 0
    
    # Parse strikes
    options["strike"] = options["ticker"].apply(_parse_strike)
    options = options.dropna(subset=["strike"])
    
    # Filter to only the fixed strike
    strike_options = options[options["strike"] == fixed_strike]
    
    if strike_options.empty:
        return 0
    
    # Get calls and puts at fixed strike
    calls = strike_options[strike_options["type"] == "CALL"]
    puts = strike_options[strike_options["type"] == "PUT"]
    
    if calls.empty or puts.empty:
        return 0
    
    call_pos = calls["position"].iloc[0]
    put_pos = puts["position"].iloc[0]
    
    # A straddle requires equal call and put positions
    # Take the minimum to find matched pairs
    if np.sign(call_pos) == np.sign(put_pos):
        matched = min(abs(call_pos), abs(put_pos))
        straddle_count = matched * np.sign(call_pos)
    else:
        straddle_count = 0
    
    return int(straddle_count)


# ----------------------------- DELTA HEDGING HELPERS --------------------------------------

def calculate_portfolio_delta(assets_stock, options_with_delta):
    """
    Calculate total portfolio delta in share equivalents.
    Uses current positions and model deltas.
    """
    total_delta = 0.0

    if assets_stock is not None and not assets_stock.empty:
        stock_pos = pd.to_numeric(assets_stock["position"], errors="coerce").fillna(0.0).iloc[0]
        total_delta += float(stock_pos)

    if options_with_delta is not None and not options_with_delta.empty:
        opt_delta = pd.to_numeric(options_with_delta["delta"], errors="coerce").fillna(0.0)
        opt_pos = pd.to_numeric(options_with_delta["position"], errors="coerce").fillna(0.0)
        opt_size = pd.to_numeric(options_with_delta["size"], errors="coerce").fillna(100.0)
        total_delta += float((opt_delta * opt_pos * opt_size).sum())

    return float(total_delta)


# ----------------------------- NEW FUNCTIONS END -------------------------------------------

def _start_hotkeys():
    if keyboard is None:
        print("hotkeys disabled: pynput not installed")
        return

    pressed = set()

    def on_press(key):
        global clear_positions_flag, restart_flag, clear_all_flag
        pressed.add(key)
        if keyboard.Key.alt_l in pressed or keyboard.Key.alt_r in pressed:
            if hasattr(key, "char") and key.char:
                if key.char.lower() == "c":
                    clear_positions_flag = True
                elif key.char.lower() == "s":
                    restart_flag = True
                elif key.char.lower() == "o":
                    clear_all_flag = True

    def on_release(key):
        if key in pressed:
            pressed.remove(key)

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()


def signal_handler(signum, frame):
    del signum, frame
    global shutdown
    shutdown = True


def get_tick(session, base_url=DEFAULT_BASE_URL):
    case = get_data(session, "case", base_url=base_url)
    # Each period is 300 ticks (periods are 1-indexed)
    # Return the tick within current period (1-300)
    return case["tick"]


def mark_atm(options_df):
    if options_df.empty:
        return options_df
    sk_abs = options_df["S-K"].abs()
    if sk_abs.dropna().empty:
        return options_df
    atm_idx = sk_abs.idxmin()
    if "atm_flag" not in options_df.columns:
        options_df["atm_flag"] = False
    options_df.loc[atm_idx, "atm_flag"] = True
    return options_df


def _parse_strike(ticker):
    m = re.search(r"(\d+)$", str(ticker))
    return float(m.group(1)) if m else np.nan


def _prep_assets(raw_assets):
    assets = pd.DataFrame(raw_assets)
    if assets.empty:
        return assets

    drop_cols = [
        "vwap", "nlv", "bid_size", "ask_size", "volume", "realized", "unrealized", "currency",
        "total_volume", "limits", "is_tradeable", "is_shortable", "interest_rate", "start_period",
        "stop_period", "unit_multiplier", "description", "display_unit", "min_price", "max_price",
        "start_price", "quoted_decimals", "trading_fee", "limit_order_rebate", "min_trade_size",
        "max_trade_size", "required_tickers", "underlying_tickers", "bond_coupon",
        "interest_payments_per_period", "base_security", "fixing_ticker", "api_orders_per_second",
        "execution_delay_ms", "interest_rate_ticker", "otc_price_range",
    ]
    assets = assets.drop(columns=drop_cols, errors="ignore").copy()

    for col, default in [("position", 0.0), ("size", 1.0), ("bid", np.nan), ("ask", np.nan), ("ticker", "")]:
        if col not in assets.columns:
            assets[col] = default

    assets["ticker"] = assets["ticker"].astype(str)
    assets["type"] = np.where(
        assets["ticker"].str.contains("P", regex=False),
        "PUT",
        np.where(assets["ticker"].str.contains("C", regex=False), "CALL", None),
    )
    # Enforce contract multiplier for options; keep stock at 1.
    assets["size"] = np.where(
        assets["type"].isin(["CALL", "PUT"]),
        100.0,
        1.0,
    )
    return assets


def _calc_pnl(raw_assets):
    if not isinstance(raw_assets, list):
        return 0.0
    realized = 0.0
    unrealized = 0.0
    for item in raw_assets:
        realized += float(item.get("realized", 0) or 0)
        unrealized += float(item.get("unrealized", 0) or 0)
    return realized + unrealized


def _liquidate_all_positions(session, raw_assets):
    if not isinstance(raw_assets, list):
        return 0
    sent = 0
    for item in raw_assets:
        ticker = item.get("ticker")
        position = item.get("position")
        if ticker is None or position in (None, 0):
            continue
        try:
            pos = float(position)
        except (TypeError, ValueError):
            continue
        if pos == 0:
            continue
        action = "SELL" if pos > 0 else "BUY"
        market_order(session, ticker, abs(pos), action)
        sent += 1
    return sent


def _liquidate_options_only(session, raw_assets):
    if not isinstance(raw_assets, list):
        return 0
    sent = 0
    for item in raw_assets:
        ticker = str(item.get("ticker", ""))
        sec_type = str(item.get("type", ""))
        is_option = sec_type.upper() == "OPTION" or ("C" in ticker or "P" in ticker)
        if not is_option:
            continue
        position = item.get("position")
        if position in (None, 0):
            continue
        try:
            pos = float(position)
        except (TypeError, ValueError):
            continue
        if pos == 0:
            continue
        action = "SELL" if pos > 0 else "BUY"
        market_order(session, ticker, abs(pos), action)
        sent += 1
    return sent


def get_news_headlines(session, base_url=DEFAULT_BASE_URL):
    """
    Fetch news headlines from the API.
    
    Returns:
        List of news dictionaries
    """
    try:
        news = get_data(session, "news", base_url=base_url)
        return news if isinstance(news, list) else []
    except Exception as e:
        print(f"Error fetching news: {e}")
        return []


def calculate_gamma_bs(S, K, sigma, T, option_type="CALL"):
    """
    Calculate gamma using Black-Scholes formula.
    Gamma is the same for calls and puts.
    
    Gamma = N'(d1) / (S * sigma * sqrt(T))
    where N'(x) = (1/sqrt(2*pi)) * exp(-x^2/2)
    """
    if T <= 0 or sigma <= 0:
        return 0.0
    
    d1 = (np.log(S / K) + 0.5 * sigma**2 * T) / (sigma * np.sqrt(T))
    n_prime_d1 = (1 / np.sqrt(2 * np.pi)) * np.exp(-0.5 * d1**2)
    gamma = n_prime_d1 / (S * sigma * np.sqrt(T))
    
    return gamma


def main(
    margin=0.15,
    delta_limit_threshold=200,
    delta_hedge_min=1,
    delta_hedge_switch="ON",
    trade_switch="ON",
    max_orders_per_tick=3,
    pnl_stop=150000,
    exposure_cap=max_exposure,
    gamma_switch="OFF",  # Disabled old strategy
    gamma_quantity=5,
    gamma_max_levels=2,
    loop_sleep=0.5,
):
    sigma_last = 0.20
    realized_vol_last = 0.20
    delta_limit = 10000  # Default delta limit
    last_trade_tick = -75  # Initialize to ensure first trade at tick 0
    delta_limit_captured = False  # Flag to track if we've captured delta limit
    initial_trade_executed = False  # Flag to track if we've done the initial trade
    current_period = None  # Track current period
    current_strike = None  # Track which strike we're currently trading (changes weekly)
    
    with requests.Session() as session:
        session.headers.update(API_KEY)

        while not shutdown:
            if restart_flag:
                return "RESTART"
            try:
                case = get_data(session, "case")
                tick = case["tick"]
                period = case["period"]
                status = case.get("status", "")
            except Exception as exc:
                print(f"tick fetch failed: {exc}")
                sleep(loop_sleep)
                continue

            # Detect new period and reset flags
            if current_period is None:
                current_period = period
                delta_limit_captured = False
                initial_trade_executed = False
                current_strike = None  # Reset strike for new period
                print(f"🟦 Starting period {period} - initial_trade_executed reset to False")
            elif period != current_period:
                current_period = period
                delta_limit_captured = False
                initial_trade_executed = False
                current_strike = None  # Reset strike for new period
                print(f"🟦 New period started: {period} - initial_trade_executed reset to False")
            
            # Wait if simulation is between periods
            if status.lower() in ["stopped", "paused", "waiting"]:
                print(f"Simulation paused/waiting, status={status}")
                sleep(2.0)  # Wait longer when paused
                continue
            
            # Check if we've reached end of period (tick 300)
            if tick >= 300:
                print(f"End of period {period} reached (tick {tick}), waiting for next period")
                sleep(2.0)
                continue

            years_remaining = max((300 - tick) / 3600, 1e-6)
            maturity_1month = max(years_remaining - (1 / 12), 1e-6)

            # Check for news at tick 1 for delta limit (only once per period)
            # Do this BEFORE checking spot validity so we don't miss news
            if tick == 1 and not delta_limit_captured:
                print(f"Tick 1 detected in period {period}, checking for initial news")
                news_items = get_news_headlines(session)
                print(f"  Found {len(news_items)} news items at tick 1")
                for news_item in news_items:
                    headline = str(news_item.get("headline", "") or "")
                    body = str(news_item.get("body", "") or "")
                    combined = f"{headline} {body}".strip()
                    
                    # Parse delta limit at tick 1
                    if "delta limit" in combined.lower():
                        parsed_limit = parse_delta_limit(combined)
                        if parsed_limit is not None:
                            delta_limit = parsed_limit
                            delta_limit_captured = True
                            print(f"✓ Delta limit set to: {delta_limit}")
                    
                    # Also check for realized volatility in tick 1 news
                    if "realized volatility" in combined.lower() or "realised volatility" in combined.lower():
                        print(f"  Parsing realized vol from tick 1 news: {combined[:100]}...")
                        parsed_vol = parse_annualized_volatility(combined)
                        if parsed_vol is not None:
                            # Use annualized volatility directly (no conversion needed)
                            realized_vol_last = parsed_vol
                            print(f"✓ Initial realized volatility: {parsed_vol*100:.2f}% annualized")
                        else:
                            print(f"  ⚠️ Failed to parse realized vol from: {combined[:100]}...")
            
            # Check for realized volatility news at the scheduled ticks (74, 149, 224)
            # Do this BEFORE checking spot validity so we don't miss news
            if tick in (74, 149, 224):
                news_items = get_news_headlines(session)
                # Only process the FIRST realized volatility news item (most recent)
                vol_updated = False
                for news_item in news_items:
                    if vol_updated:
                        break  # Already found and processed the current week's news
                    
                    headline = str(news_item.get("headline", "") or "")
                    body = str(news_item.get("body", "") or "")
                    combined = f"{headline} {body}".strip()
                    
                    if "realized volatility" in combined.lower() or "realised volatility" in combined.lower():
                        # Skip if this is tick 1 news (already processed above)
                        if tick == 1 and "risk free rate" in combined.lower():
                            continue
                        
                        parsed_vol = parse_annualized_volatility(combined)
                        if parsed_vol is not None:
                            # Use annualized volatility directly (no conversion needed)
                            realized_vol_last = parsed_vol
                            vol_updated = True  # Mark as updated, stop processing more news
                            print(f"✓ Realized volatility updated: {parsed_vol*100:.2f}% annualized (tick {tick})")



            try:
                raw_assets = get_data(session, "securities")
                assets = _prep_assets(raw_assets)
            except Exception as exc:
                print(f"securities fetch failed: {exc}")
                sleep(loop_sleep)
                continue

            if assets.empty:
                print("no securities data")
                sleep(loop_sleep)
                continue

            assets_stock = assets.iloc[:1].copy()
            assets_options = assets.iloc[1:].copy()
            if assets_stock.empty:
                print("stock row missing")
                sleep(loop_sleep)
                continue

            spot_bid = pd.to_numeric(assets_stock["bid"], errors="coerce").iloc[0]
            spot_ask = pd.to_numeric(assets_stock["ask"], errors="coerce").iloc[0]
            spot = np.nanmean([spot_bid, spot_ask])
            if np.isnan(spot) or spot <= 0:
                print(f"invalid spot (bid={spot_bid}, ask={spot_ask}), waiting...")
                sleep(loop_sleep)
                continue

            assets_options["strike"] = assets_options["ticker"].apply(_parse_strike)
            assets_options = assets_options.dropna(subset=["strike", "type"]).copy()
            assets_options["S-K"] = spot - assets_options["strike"]

            pnl = _calc_pnl(raw_assets)
            if pnl_stop is not None and pnl >= pnl_stop:
                liquidated = _liquidate_all_positions(session, raw_assets)
                print(f"pnl={pnl:.2f} >= {pnl_stop}: liquidated_positions={liquidated}")
                break
            if clear_positions_flag:
                liquidated = _liquidate_options_only(session, raw_assets)
                print(f"hotkey: liquidated_options={liquidated}")
                globals()["clear_positions_flag"] = False
            if clear_all_flag:
                liquidated = _liquidate_all_positions(session, raw_assets)
                print(f"hotkey: liquidated_all_positions={liquidated}")
                globals()["clear_all_flag"] = False

            options_1m = mark_atm(assets_options.iloc[:20].copy())
            options_2m = mark_atm(assets_options.iloc[20:].copy())

            sigma = headline_vol(session, default=sigma_last)
            if sigma is None or pd.isna(sigma) or sigma <= 0:
                sigma = sigma_last
            else:
                sigma_last = sigma

            for frame, tenor in [(options_1m, maturity_1month), (options_2m, years_remaining)]:
                if frame.empty:
                    frame["bs_model_price"] = pd.Series(dtype=float)
                    frame["delta"] = pd.Series(dtype=float)
                    continue
                frame["bs_model_price"] = frame.apply(
                    lambda row: calculate_bs_price(row, s=spot, sigma=sigma, t=tenor, output="price"), axis=1
                )
                frame["delta"] = frame.apply(
                    lambda row: calculate_bs_price(row, s=spot, sigma=sigma, t=tenor, output="delta"), axis=1
                )

            options = pd.concat([options_1m, options_2m], axis=0)
            if options.empty:
                print(f"tick={tick} sigma={sigma:.4f} no options")
                sleep(loop_sleep)
                continue

            options["bid"] = pd.to_numeric(options["bid"], errors="coerce")
            options["ask"] = pd.to_numeric(options["ask"], errors="coerce")

            # Determine target strike for this week
            new_atm_strike = find_atm_strike(options_1m, spot)
            
            if new_atm_strike is None:
                print(f"  ⚠️ Could not find ATM strike, skipping trading")
                sleep(loop_sleep)
                continue
            
            # Calculate current straddle position at the CURRENT strike we're holding
            if current_strike is None:
                current_straddle_pos = 0  # No position yet
            else:
                current_straddle_pos = calculate_current_straddle_position(assets, fixed_strike=current_strike)
            
            # Execute straddle strategy
            traded_straddles = 0
            target_position = current_straddle_pos  # Default: maintain current position
            
            # Execute initial trade: once we have tick 1 news and valid spot price
            # OR execute regular trades every 75 ticks
            should_trade = False
            trade_reason = ""
            strike_changed = False
            
            if not initial_trade_executed and tick >= 2:
                # Initial trade: execute at first valid tick (tick 2+, after tick 1 news is parsed)
                should_trade = True
                trade_reason = "INITIAL"
                strike_changed = True  # First time setting strike
                print(f"  🟢 Initial trade triggered at tick {tick}")
            elif tick % 75 == 0 and tick > 0:
                # Weekly trade at ticks 75, 150, 225, etc.
                should_trade = True
                trade_reason = f"WEEKLY (tick {tick})"
                # Check if strike has changed
                if current_strike is not None and new_atm_strike != current_strike:
                    strike_changed = True
                print(f"  🟢 Weekly trade triggered at tick {tick}")
            
            print(f"  🔍 Trade check: should_trade={should_trade}, initial_executed={initial_trade_executed}, tick={tick}")
            
            if should_trade:
                # If strike changed, close old position completely
                if strike_changed and current_strike is not None:
                    print(f"🔄 STRIKE ROTATION: {current_strike} → {new_atm_strike}")
                    print(f"  Closing {current_straddle_pos} straddles at old strike {current_strike}")
                    
                    # Close all positions at old strike
                    closed_trades = close_all_straddles_at_strike(session, assets_options, current_strike)
                    print(f"  ✅ Closed position at strike {current_strike}: {closed_trades} trades")
                    
                    # Reset position to 0 since we closed everything
                    current_straddle_pos = 0
                
                # Update to new strike
                current_strike = new_atm_strike
                
                # Get straddle Greeks at the NEW strike
                straddle_delta, straddle_gamma = get_straddle_greeks(options_1m, spot, fixed_strike=current_strike)
                
                # If gamma not calculated, estimate it using current strike
                if straddle_gamma == 0:
                    # Calculate gamma at current strike
                    straddle_gamma = 2 * calculate_gamma_bs(spot, current_strike, sigma, maturity_1month)
                
                print(f"📊 DEBUG [{trade_reason}]: realized_vol={realized_vol_last:.6f}, implied_vol={sigma:.6f}, "
                      f"gamma={straddle_gamma:.6f}, delta={straddle_delta:.4f}, "
                      f"current_pos={current_straddle_pos}, L={delta_limit}, strike={current_strike}")
                
                # Run optimization
                target_position = optimising_function(
                    realised_sigma=realized_vol_last,
                    implied_sigma=sigma,
                    delta=straddle_delta,
                    gamma=straddle_gamma,
                    current_price=spot,
                    current_position=current_straddle_pos,
                    L=delta_limit
                )
                
                # Clamp target position to [-50, 50]
                target_position = max(-50, min(50, target_position))
                
                # Execute trades to reach target position at NEW strike
                if target_position != current_straddle_pos:
                    traded_straddles = execute_straddle_trades(
                        session, options_1m, target_position, current_straddle_pos, fixed_strike=current_strike
                    )
                
                print(f"🎯 STRADDLE STRATEGY [{trade_reason}]: tick={tick} strike={current_strike} "
                      f"current_pos={current_straddle_pos} target_pos={target_position} trades={traded_straddles} "
                      f"gamma={straddle_gamma:.6f} delta={straddle_delta:.4f}")
                
                # Mark initial trade as executed (after the trade completes)
                if trade_reason == "INITIAL":
                    initial_trade_executed = True
                    print(f"  ✅ Initial trade completed, flag set")

                # Refresh positions after trading so hedge uses updated holdings
                try:
                    raw_assets = get_data(session, "securities")
                    assets = _prep_assets(raw_assets)
                    assets_stock = assets.iloc[:1].copy()
                    assets_options = assets.iloc[1:].copy()
                    assets_options["strike"] = assets_options["ticker"].apply(_parse_strike)
                    assets_options = assets_options.dropna(subset=["strike", "type"]).copy()
                    assets_options["S-K"] = spot - assets_options["strike"]

                    options_1m = mark_atm(assets_options.iloc[:20].copy())
                    options_2m = mark_atm(assets_options.iloc[20:].copy())

                    for frame, tenor in [(options_1m, maturity_1month), (options_2m, years_remaining)]:
                        if frame.empty:
                            frame["bs_model_price"] = pd.Series(dtype=float)
                            frame["delta"] = pd.Series(dtype=float)
                            continue
                        frame["bs_model_price"] = frame.apply(
                            lambda row: calculate_bs_price(row, s=spot, sigma=sigma, t=tenor, output="price"), axis=1
                        )
                        frame["delta"] = frame.apply(
                            lambda row: calculate_bs_price(row, s=spot, sigma=sigma, t=tenor, output="delta"), axis=1
                        )

                    options = pd.concat([options_1m, options_2m], axis=0)
                    
                    # Delta hedge AFTER refreshing positions
                    if str(delta_hedge_switch).upper() == "ON":
                        total_delta = calculate_portfolio_delta(assets_stock, options)
                        effective_limit = 0 if delta_limit is None or pd.isna(delta_limit) else float(delta_limit)
                        near_limit_trigger = max(effective_limit - delta_limit_threshold, 0)
                        
                        print(f"  📐 Post-trade delta: {total_delta:.2f}, limit trigger: {near_limit_trigger:.2f}")
                        
                        # Hedge if we just traded OR if approaching limit
                        should_hedge = True  # Always hedge after a trade
                        if near_limit_trigger > 0 and abs(total_delta) >= near_limit_trigger:
                            print(f"  ⚠️ Delta approaching limit!")
                            should_hedge = True

                        if should_hedge:
                            if "RTM" in assets_stock["ticker"].values:
                                stock_ticker = "RTM"
                            else:
                                stock_ticker = str(assets_stock["ticker"].iloc[0])
                            hedge_qty = int(round(abs(total_delta)))
                            if hedge_qty > 0:
                                action = "SELL" if total_delta > 0 else "BUY"
                                market_order(session, stock_ticker, hedge_qty, action)
                                print(
                                    f"🧭 DELTA HEDGE [{trade_reason}]: total_delta={total_delta:.2f}, "
                                    f"action={action} {hedge_qty} {stock_ticker}"
                                )
                            else:
                                print(f"  ℹ️ Delta already near zero ({total_delta:.2f}), no hedge needed")
                        
                except Exception as exc:
                    print(f"post-trade refresh/hedge failed: {exc}")

            # Delta hedging is now handled inside the should_trade block after position refresh
            # However, we still need to monitor delta limits on non-trade ticks
            if not should_trade and str(delta_hedge_switch).upper() == "ON":
                total_delta = calculate_portfolio_delta(assets_stock, options)
                effective_limit = 0 if delta_limit is None or pd.isna(delta_limit) else float(delta_limit)
                near_limit_trigger = max(effective_limit - delta_limit_threshold, 0)
                
                # Only hedge if approaching limit (not on every tick)
                if near_limit_trigger > 0 and abs(total_delta) >= near_limit_trigger:
                    if "RTM" in assets_stock["ticker"].values:
                        stock_ticker = "RTM"
                    else:
                        stock_ticker = str(assets_stock["ticker"].iloc[0])
                    hedge_qty = int(round(abs(total_delta)))
                    if hedge_qty > 0:
                        action = "SELL" if total_delta > 0 else "BUY"
                        market_order(session, stock_ticker, hedge_qty, action)
                        print(
                            f"🚨 EMERGENCY DELTA HEDGE: total_delta={total_delta:.2f}, "
                            f"trigger={near_limit_trigger}, action={action} {hedge_qty} {stock_ticker}"
                        )
            
            print(
                f"[P{period}] tick={tick} strike={current_strike if current_strike else 'None'} "
                f"sigma={sigma:.4f} realized_vol={realized_vol_last:.6f} "
                f"straddles={current_straddle_pos} target={target_position} pnl={pnl:.2f}"
            )
            sleep(loop_sleep)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    _start_hotkeys()
    try:
        while True:
            result = main()
            if result != "RESTART" or shutdown:
                break
            globals()["restart_flag"] = False
    except ApiException as exc:
        print(f"api error: {exc}")
