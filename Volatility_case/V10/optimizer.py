"""
Optimal Straddle Position Sizing - Liam Yih's Method

Implements equations 2-5 from "Optimisation method for options" (Aug 2025).

Finds n* straddles that maximizes:
  f(n) = [expected_gain - positioning_cost] * |n| - rebalancing_cost

Key equations:
  Eq 2: Expected gain per contract from vol mismatch
  Eq 3: Mean First Passage Time (MFPT) for delta boundary hit
  Eq 4: Expected rebalancing cost from delta hedging
  Eq 5: Combined optimization function
"""

import math
import logging
from typing import Tuple

from config import MAX_STRADDLES

logger = logging.getLogger(__name__)

# Fixed parameters from Table 1
STOCK_FEE_ROUNDTRIP = 0.02          # Fs = $0.01/share * 2 ways
OPTION_FEE_ROUNDTRIP_STRADDLE = 4.0 # Fo = $1/contract * 2 legs * 2 ways
MULTIPLIER = 100                     # shares per option contract


def compute_straddle_gamma(gamma_call_per_share: float) -> float:
    """
    Per-contract straddle Gamma.
    Gamma_straddle = 2 * Gamma_C = 2 * 100 * gamma_C
    (gamma same for calls and puts)
    """
    return 2.0 * MULTIPLIER * gamma_call_per_share


def expected_gain_per_contract(S0: float, Gamma: float, sigma: float,
                                sigma_hat: float, T: float) -> float:
    """
    Equation 2: Expected gain per straddle contract.

    gain = (1/2) * S0^2 * Gamma * |sigma^2 - sigma_hat^2| / sigma^2 * (e^(sigma^2*T) - 1)

    Uses absolute vol edge; direction handled separately.
    """
    sigma_sq = sigma * sigma
    sigma_hat_sq = sigma_hat * sigma_hat

    if sigma_sq < 1e-12:
        return 0.0

    vol_edge_ratio = abs(sigma_sq - sigma_hat_sq) / sigma_sq
    exp_term = math.exp(sigma_sq * T) - 1.0

    return 0.5 * S0 * S0 * Gamma * vol_edge_ratio * exp_term


def mfpt_bracket(S0: float, X: float, Z: float) -> float:
    """
    MFPT bracket from equation 3:
    bracket = 2*ln(S0/(X-Z)) - (1 + (S0-X)/Z) * ln((X+Z)/(X-Z))

    Returns bracket value, or +inf if boundary unreachable (no rebalancing).
    Returns 0 if stock is already at boundary.
    """
    if Z <= 0:
        return float('inf')

    lower = X - Z
    upper = X + Z

    # If lower boundary <= 0, stock (GBM, always positive) can never reach it.
    # Effectively infinite MFPT -> no rebalancing needed.
    if lower <= 0:
        return float('inf')

    # Stock already at or beyond boundary -> instant rebalance
    if S0 <= lower or S0 >= upper:
        return 0.0

    try:
        term1 = 2.0 * math.log(S0 / lower)
        term2 = (1.0 + (S0 - X) / Z) * math.log(upper / lower)
        bracket = term1 - term2
        return max(bracket, 0.0)
    except (ValueError, ZeroDivisionError):
        return float('inf')


def rebalancing_cost(S0: float, X: float, Z: float, sigma: float,
                     T: float, L: float, Bs: float) -> float:
    """
    Equation 4: Expected rebalancing cost over holding period T.

    cost = L * (sigma^2 * T / 2) * bracket^(-1) * (Fs + Bs/2)
    """
    bracket = mfpt_bracket(S0, X, Z)

    if bracket == float('inf'):
        return 0.0  # Boundary unreachable, no cost

    if bracket <= 1e-12:
        return float('inf')  # Instant rebalancing, infinite cost

    sigma_sq = sigma * sigma
    per_share_cost = STOCK_FEE_ROUNDTRIP + 0.5 * Bs

    return L * (sigma_sq * T / 2.0) * (1.0 / bracket) * per_share_cost


def profit_function(n_abs: int, S0: float, X: float, Gamma: float,
                    sigma: float, sigma_hat: float, L: float,
                    Bo: float, Bs: float, T: float) -> float:
    """
    Equation 5: Total optimization function.

    f(n) = [gain_per_contract - (Bo/2 + Fo)] * |n|
           - L * (sigma^2*T/2) * bracket(Z)^(-1) * (Fs + Bs/2)

    where Z = L / (|n| * Gamma)
    """
    if n_abs <= 0:
        return 0.0

    # Per-contract expected gain
    gain = expected_gain_per_contract(S0, Gamma, sigma, sigma_hat, T)

    # Per-contract positioning cost
    pos_cost = 0.5 * Bo + OPTION_FEE_ROUNDTRIP_STRADDLE

    # Linear term
    linear_value = (gain - pos_cost) * n_abs

    # Rebalancing cost (depends on Z)
    if n_abs * Gamma > 1e-12:
        Z = L / (n_abs * Gamma)
    else:
        Z = float('inf')

    rebal = rebalancing_cost(S0, X, Z, sigma, T, L, Bs)

    if rebal == float('inf'):
        return -float('inf')

    return linear_value - rebal


def find_optimal_n(S0: float, X: float, gamma_call_per_share: float,
                   sigma: float, sigma_hat: float, L: float,
                   Bo: float, Bs: float,
                   T_hold: float, max_n: int = None) -> Tuple[int, int, float]:
    """
    Find optimal straddle count n* by scanning from L/100 upward.

    Args:
        S0: current stock price
        X: strike price (closest to S0)
        gamma_call_per_share: BS gamma for a call (per share)
        sigma: realized volatility (annualized decimal)
        sigma_hat: implied volatility (annualized decimal)
        L: delta limit (per-share units)
        Bo: straddle bid-ask spread (call_spread + put_spread)
        Bs: stock bid-ask spread
        T_hold: holding period in years (1/52 for one week)
        max_n: maximum straddles (defaults to MAX_STRADDLES from config)

    Returns:
        (n_star, direction, f_star)
        n_star: optimal magnitude (0 if no profitable trade)
        direction: +1 long, -1 short, 0 if no trade
        f_star: expected profit at n_star
    """
    if max_n is None:
        max_n = MAX_STRADDLES

    Gamma = compute_straddle_gamma(gamma_call_per_share)

    if Gamma <= 1e-12 or sigma <= 1e-6 or L <= 0:
        logger.warning("Invalid optimizer inputs: Gamma=%.6f sigma=%.6f L=%.0f",
                       Gamma, sigma, L)
        return 0, 0, 0.0

    # Direction from vol edge
    sigma_sq = sigma * sigma
    sigma_hat_sq = sigma_hat * sigma_hat
    if abs(sigma_sq - sigma_hat_sq) < 1e-8:
        logger.info("No vol edge: sigma=%.4f sigma_hat=%.4f", sigma, sigma_hat)
        return 0, 0, 0.0

    direction = 1 if sigma > sigma_hat else -1

    # n_start = L/100: contracts that never need rebalancing
    n_start = max(1, int(L / MULTIPLIER))
    n_max = min(max_n, MAX_STRADDLES)

    # NOTE: No gamma cap here - equation 5's rebalancing cost already penalizes
    # large n through the Z = L/(n*Gamma) boundary shrinkage.

    if n_start > n_max:
        n_start = 1

    # Check if even 1 straddle is profitable
    f_one = profit_function(1, S0, X, Gamma, sigma, sigma_hat, L, Bo, Bs, T_hold)
    if f_one <= 0:
        # Check if edge covers positioning cost
        gain_pc = expected_gain_per_contract(S0, Gamma, sigma, sigma_hat, T_hold)
        pos_cost_pc = 0.5 * Bo + OPTION_FEE_ROUNDTRIP_STRADDLE
        logger.info("Not profitable: gain/contract=$%.4f, pos_cost=$%.4f",
                    gain_pc, pos_cost_pc)
        return 0, 0, 0.0

    # Scan for optimal n
    best_n = 1
    best_f = f_one

    for n in range(2, n_max + 1):
        f_n = profit_function(n, S0, X, Gamma, sigma, sigma_hat, L, Bo, Bs, T_hold)

        if f_n > best_f:
            best_n = n
            best_f = f_n
        elif best_n >= n_start and f_n < best_f * 0.95:
            # Past the peak and declining meaningfully
            break

    if best_f <= 0:
        return 0, 0, 0.0

    # Log detailed results
    Z_opt = L / (best_n * Gamma) if best_n * Gamma > 0 else float('inf')
    gain_pc = expected_gain_per_contract(S0, Gamma, sigma, sigma_hat, T_hold)
    pos_cost_pc = 0.5 * Bo + OPTION_FEE_ROUNDTRIP_STRADDLE
    rebal = rebalancing_cost(S0, X, Z_opt, sigma, T_hold, L, Bs)

    logger.info("=" * 60)
    logger.info("OPTIMIZER RESULT: n*=%d %s straddles @ strike %.0f",
                best_n, "LONG" if direction > 0 else "SHORT", X)
    logger.info("  Expected profit: $%.2f", best_f)
    logger.info("  Gain/contract: $%.4f | Pos cost/contract: $%.4f",
                gain_pc, pos_cost_pc)
    logger.info("  Rebalancing cost: $%.2f | Z=%.2f", rebal, Z_opt)
    logger.info("  Inputs: S0=%.2f sigma=%.4f sigma_hat=%.4f L=%.0f Gamma=%.4f",
                S0, sigma, sigma_hat, L, Gamma)
    logger.info("  Spreads: Bo=%.4f Bs=%.4f T=%.6f", Bo, Bs, T_hold)
    logger.info("=" * 60)

    return best_n, direction, best_f
