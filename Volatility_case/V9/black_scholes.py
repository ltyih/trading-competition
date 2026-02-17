"""Black-Scholes pricing and Greeks calculation."""

import math
from typing import Tuple


def _norm_cdf(x: float) -> float:
    """Standard normal CDF using the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float) -> Tuple[float, float]:
    """Calculate d1 and d2 for Black-Scholes."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0, 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes call option price."""
    if T <= 0:
        return max(0.0, S - K)
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes put option price."""
    if T <= 0:
        return max(0.0, K - S)
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str) -> float:
    """Black-Scholes option price for call or put."""
    if option_type.upper() == "CALL":
        return bs_call_price(S, K, T, r, sigma)
    else:
        return bs_put_price(S, K, T, r, sigma)


def bs_delta(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str) -> float:
    """Black-Scholes delta."""
    if T <= 0 or sigma <= 0:
        if option_type.upper() == "CALL":
            return 1.0 if S > K else 0.0
        else:
            return -1.0 if S < K else 0.0
    d1, _ = _d1_d2(S, K, T, r, sigma)
    if option_type.upper() == "CALL":
        return _norm_cdf(d1)
    else:
        return _norm_cdf(d1) - 1.0


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes gamma (same for calls and puts)."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1, _ = _d1_d2(S, K, T, r, sigma)
    return _norm_pdf(d1) / (S * sigma * math.sqrt(T))


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes vega (same for calls and puts). Per 1% vol change."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1, _ = _d1_d2(S, K, T, r, sigma)
    return S * _norm_pdf(d1) * math.sqrt(T) * 0.01


def bs_theta(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str) -> float:
    """Black-Scholes theta (per day, negative for long positions)."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    term1 = -(S * _norm_pdf(d1) * sigma) / (2.0 * math.sqrt(T))
    if option_type.upper() == "CALL":
        term2 = -r * K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        term2 = r * K * math.exp(-r * T) * _norm_cdf(-d2)
    return (term1 + term2) / 365.0


def implied_volatility(market_price: float, S: float, K: float, T: float,
                       r: float, option_type: str,
                       tol: float = 1e-6, max_iter: int = 100) -> float:
    """
    Calculate implied volatility using Newton-Raphson method.
    Returns annualized implied vol as a decimal (e.g. 0.20 for 20%).
    """
    if market_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0

    # Check for intrinsic value bounds
    if option_type.upper() == "CALL":
        intrinsic = max(0.0, S - K * math.exp(-r * T))
        if market_price < intrinsic:
            return 0.0
    else:
        intrinsic = max(0.0, K * math.exp(-r * T) - S)
        if market_price < intrinsic:
            return 0.0

    # Initial guess
    sigma = 0.25

    for _ in range(max_iter):
        price = bs_price(S, K, T, r, sigma, option_type)
        diff = price - market_price

        if abs(diff) < tol:
            return sigma

        # Vega for Newton step (raw, not per 1%)
        d1, _ = _d1_d2(S, K, T, r, sigma)
        vega_raw = S * _norm_pdf(d1) * math.sqrt(T)

        if vega_raw < 1e-10:
            break

        sigma = sigma - diff / vega_raw
        sigma = max(sigma, 0.001)  # Keep positive
        sigma = min(sigma, 5.0)    # Cap at 500%

    return sigma
