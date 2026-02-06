# -*- coding: utf-8 -*-
"""Robust utilities for the volatility strategy."""

import re
import warnings
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import norm


class ApiException(Exception):
    pass


DEFAULT_BASE_URL = "http://localhost:10005/v1"


def _safe_float(value, default=np.nan):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_percent(text: str) -> Optional[float]:
    if not isinstance(text, str):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    return float(m.group(1)) / 100 if m else None


def black_scholes_call(s, k, t, r, sigma, output="price"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        s = _safe_float(s)
        k = _safe_float(k)
        t = _safe_float(t)
        sigma = _safe_float(sigma)
        r = _safe_float(r, 0.0)

        if any(np.isnan([s, k, t, sigma])) or s <= 0 or k <= 0:
            return np.nan

        if t <= 0 or sigma <= 0:
            intrinsic = max(s - k, 0.0)
            if output == "price":
                return intrinsic
            if output == "delta":
                return 1.0 if s > k else 0.0
            return np.nan

        d1 = (np.log(s / k) + (r + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
        d2 = d1 - sigma * np.sqrt(t)

        if output == "price":
            return s * norm.cdf(d1) - k * np.exp(-r * t) * norm.cdf(d2)
        if output == "delta":
            return norm.cdf(d1)
        return np.nan


def black_scholes_put(s, k, t, r, sigma, output="price"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        s = _safe_float(s)
        k = _safe_float(k)
        t = _safe_float(t)
        sigma = _safe_float(sigma)
        r = _safe_float(r, 0.0)

        if any(np.isnan([s, k, t, sigma])) or s <= 0 or k <= 0:
            return np.nan

        if t <= 0 or sigma <= 0:
            intrinsic = max(k - s, 0.0)
            if output == "price":
                return intrinsic
            if output == "delta":
                return -1.0 if s < k else 0.0
            return np.nan

        d1 = (np.log(s / k) + (r + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
        d2 = d1 - sigma * np.sqrt(t)

        if output == "price":
            return k * np.exp(-r * t) * norm.cdf(-d2) - s * norm.cdf(-d1)
        if output == "delta":
            return -norm.cdf(-d1)
        return np.nan


def calculate_bs_price(row, s, sigma, t, r=0, output="price"):
    k = _safe_float(row.get("strike"))
    option_type = str(row.get("type", "")).upper()
    if option_type == "CALL":
        return black_scholes_call(s, k, t, r, sigma, output=output)
    if option_type == "PUT":
        return black_scholes_put(s, k, t, r, sigma, output=output)
    return np.nan


def fetch_data(session, endpoint, base_url=DEFAULT_BASE_URL):
    resp = session.get(f"{base_url}/{endpoint}")
    if resp.ok:
        return resp.json()
    raise ApiException(f"Failed to fetch data from {endpoint}: HTTP {resp.status_code}")


def get_data(session, endpoint, base_url=DEFAULT_BASE_URL):
    return fetch_data(session, endpoint, base_url=base_url)


def market_order(session, security_name, quantity, action, position_size=10000, base_url=DEFAULT_BASE_URL):
    quantity = int(abs(_safe_float(quantity, 0)))
    if quantity <= 0:
        return

    chunk = max(int(position_size), 1)
    full_orders = quantity // chunk
    remainder = quantity % chunk

    for _ in range(full_orders):
        session.post(
            f"{base_url}/orders",
            params={"ticker": security_name, "type": "MARKET", "quantity": chunk, "action": action},
        )

    if remainder > 0:
        session.post(
            f"{base_url}/orders",
            params={"ticker": security_name, "type": "MARKET", "quantity": remainder, "action": action},
        )


def limit_order(session, security_name, price, quantity, action, position_size=10, base_url=DEFAULT_BASE_URL):
    quantity = int(abs(_safe_float(quantity, 0)))
    if quantity <= 0:
        return

    chunk = max(int(position_size), 1)
    full_orders = quantity // chunk
    remainder = quantity % chunk

    for _ in range(full_orders):
        session.post(
            f"{base_url}/orders",
            params={"ticker": security_name, "type": "LIMIT", "price": price, "quantity": chunk, "action": action},
        )

    if remainder > 0:
        session.post(
            f"{base_url}/orders",
            params={"ticker": security_name, "type": "LIMIT", "price": price, "quantity": remainder, "action": action},
        )


def delete_all_orders(session, ticker, base_url=DEFAULT_BASE_URL):
    resp = session.get(f"{base_url}/orders", params={"status": "OPEN", "ticker": ticker})
    if not resp.ok:
        return
    for order in resp.json():
        order_id = order.get("order_id")
        if order_id is not None:
            session.delete(f"{base_url}/orders/{order_id}")


def headline_vol(session, default=None, base_url=DEFAULT_BASE_URL):
    news = get_data(session, "news", base_url=base_url)
    if not isinstance(news, list) or not news:
        return default

    # Favor more recent announcements if available.
    for item in news:
        headline = str(item.get("headline", ""))
        body = str(item.get("body", ""))

        if "Announcement" in headline:
            ann_vol = _extract_percent(body)
            if ann_vol is not None:
                return ann_vol

        if "News" in headline:
            m = re.search(
                r"between\s*(\d+(?:\.\d+)?)\s*%\s*~\s*(\d+(?:\.\d+)?)\s*%",
                body,
                flags=re.IGNORECASE,
            )
            if m:
                low = float(m.group(1)) / 100
                high = float(m.group(2)) / 100
                return (low + high) / 2

        if "Risk" in headline:
            m = re.search(r"volatility\s+is\s*(\d+(?:\.\d+)?)\s*%", body, flags=re.IGNORECASE)
            if m:
                return float(m.group(1)) / 100

    return default


def _abs_delta_value(x):
    if isinstance(x, (list, tuple, np.ndarray, pd.Series)):
        if len(x) == 0:
            return np.nan
        return abs(_safe_float(x[0]))
    return abs(_safe_float(x))


def calculate_hedge_ratios(df):
    df = df.copy()
    if df.empty:
        df["Absolute Delta"] = pd.Series(dtype=float)
        df["Hedge Ratio"] = pd.Series(dtype=float)
        return df

    df["Absolute Delta"] = df["delta"].apply(_abs_delta_value)
    df["Hedge Ratio"] = np.nan

    work = df.reset_index()
    for i in range(0, len(work) - 1, 2):
        call_delta = _safe_float(work.loc[i, "Absolute Delta"])
        put_delta = _safe_float(work.loc[i + 1, "Absolute Delta"])

        if np.isnan(call_delta) or np.isnan(put_delta) or call_delta == 0 or put_delta == 0:
            call_ratio, put_ratio = np.nan, np.nan
        elif call_delta >= put_delta:
            call_ratio, put_ratio = 1.0, call_delta / put_delta
        else:
            call_ratio, put_ratio = put_delta / call_delta, 1.0

        df.loc[work.loc[i, "index"], "Hedge Ratio"] = call_ratio
        df.loc[work.loc[i + 1, "index"], "Hedge Ratio"] = put_ratio

    df["Hedge Ratio"] = df["Hedge Ratio"].round(2)
    return df


def extract_delta(text):
    if not isinstance(text, str):
        return None
    m = re.search(r"delta limit[^\d]*(\d{1,3}(?:,\d{3})*)", text, flags=re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def get_delta_limit(session, default=None, base_url=DEFAULT_BASE_URL):
    news = get_data(session, "news", base_url=base_url)
    if not isinstance(news, list):
        return default

    for item in news:
        if "Delta Limit" in str(item.get("headline", "")):
            parsed = extract_delta(str(item.get("body", "")))
            if parsed is not None:
                return parsed

    return default
