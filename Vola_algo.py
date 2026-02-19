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
API_KEY = {"X-API-Key": "AJDSYHVC"}
shutdown = False
clear_positions_flag = False
restart_flag = False
clear_all_flag = False
gamma_swi="ON"

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
    return case["tick"] + (case["period"] - 1) * 300


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


def _execute_signal_orders(session, options, max_orders=2):
    if options.empty or max_orders <= 0:
        return 0

    candidates = options[options["Decision"] != ""].copy()
    if candidates.empty:
        return 0

    candidates = candidates.sort_values("Average Spread % Abs", ascending=False)
    count = 0
    for _, row in candidates.head(max_orders).iterrows():
        ticker = str(row.get("ticker", ""))
        side = row.get("Decision")
        if not ticker or side not in {"Buy", "Sell"}:
            continue

        action = "BUY" if side == "Buy" else "SELL"
        if action == "BUY":
            price = row.get("ask")
        else:
            price = row.get("bid")

        if price is None or pd.isna(price):
            continue

        limit_order(session, ticker, price=float(price), quantity=10, action=action)
        count += 1

    return count


def _execute_gamma_straddles(session, options, max_levels=5, quantity=10):
    if options.empty or max_levels <= 0 or quantity <= 0:
        return 0

    strikes = sorted(options["strike"].dropna().unique())
    if not strikes:
        return 0

    atm_idx = options["S-K"].abs().idxmin()
    atm_strike = float(options.loc[atm_idx, "strike"])
    if atm_strike not in strikes:
        return 0

    atm_pos = strikes.index(atm_strike)
    offsets = [-2, -1, 0, 1, 2]
    selected = []
    for off in offsets:
        i = atm_pos + off
        if 0 <= i < len(strikes):
            selected.append(strikes[i])
    selected = selected[:max_levels]

    count = 0
    for k in selected:
        legs = options[options["strike"] == k]
        for opt_type in ("CALL", "PUT"):
            leg = legs[legs["type"] == opt_type]
            if leg.empty:
                continue
            price = leg["ask"].iloc[0]
            ticker = str(leg["ticker"].iloc[0])
            if pd.isna(price) or not ticker:
                continue
            limit_order(session, ticker, price=float(price), quantity=quantity, action="BUY")
            count += 1

    return count


def main(
    margin=0.15,
    delta_limit_threshold=200,
    delta_hedge_switch="OFF",
    trade_switch="ON",
    max_orders_per_tick=3,
    pnl_stop=150000,
    exposure_cap=max_exposure,
    gamma_switch="ON",
    gamma_quantity=5,
    gamma_max_levels=2,
    loop_sleep=0.5,
):
    '''
    if str(gamma_switch).upper() == "ON":
        delta_hedge_switch = "OFF"
'''
    sigma_last = 0.20
    with requests.Session() as session:
        session.headers.update(API_KEY)

        while not shutdown:
            if restart_flag:
                return "RESTART"
            try:
                tick = get_tick(session)
            except Exception as exc:
                print(f"tick fetch failed: {exc}")
                sleep(loop_sleep)
                continue

            if tick >= 600:
                break

            years_remaining = max((600 - tick) / 3600, 1e-6)
            maturity_1month = max(years_remaining - (1 / 12), 1e-6)

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
                print("invalid spot")
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

            options["Bid Spread"] = options["bs_model_price"] - options["bid"]
            options["Ask Spread"] = options["bs_model_price"] - options["ask"]
            options["Bid Spread %"] = np.where(options["bid"] != 0, options["Bid Spread"] / options["bid"], np.nan)
            options["Ask Spread %"] = np.where(options["ask"] != 0, options["Ask Spread"] / options["ask"], np.nan)
            options["Average Spread % Abs"] = (options[["Bid Spread %", "Ask Spread %"]].mean(axis=1)).abs()
            options["Decision"] = np.where(
                options["Bid Spread %"] < -margin,
                "Sell",
                np.where(options["Ask Spread %"] > margin, "Buy", ""),
            )
            options = calculate_hedge_ratios(options)

            assets_stock["delta"] = 1.0
            portfolio = pd.concat([assets_stock, options], axis=0)
            portfolio["position"] = pd.to_numeric(portfolio["position"], errors="coerce").fillna(0)
            portfolio["size"] = pd.to_numeric(portfolio["size"], errors="coerce").fillna(0)
            portfolio["delta"] = pd.to_numeric(portfolio["delta"], errors="coerce").fillna(0)

            share_exposure = float((portfolio["position"] * portfolio["size"] * portfolio["delta"]).sum())

            exposure_capped = False
            if exposure_cap is not None:
                try:
                    cap = float(exposure_cap)
                except (TypeError, ValueError):
                    cap = None
                if cap is not None and cap > 0 and abs(share_exposure) > cap:
                    excess = abs(share_exposure) - cap
                    action = "SELL" if share_exposure > 0 else "BUY"
                    market_order(session, "RTM", excess, action)
                    exposure_capped = True

            # Delta hedge disabled: use exposure_cap to keep risk within range.

            traded = 0
            if trade_switch.upper() == "ON" and not exposure_capped:
                traded = _execute_signal_orders(session, options, max_orders=max_orders_per_tick)

            traded_gamma = 0
            if gamma_switch.upper() == "ON" and not exposure_capped:
                traded_gamma = _execute_gamma_straddles(
                    session,
                    options_1m,
                    max_levels=gamma_max_levels,
                    quantity=gamma_quantity,
                )

            print(
                f"tick={tick} sigma={sigma:.4f} exposure={share_exposure:.1f} "
                f"signals={int((options['Decision'] != '').sum())} traded={traded} "
                f"gamma={traded_gamma} pnl={pnl:.2f}"
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
