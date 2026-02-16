"""Planner: pure functions to compute fuel conversion recommendations.

Encodes RITC 2026 Electricity case mechanics and the exact decision rule.
Unit-testable; no I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


# Case constants
SOLAR_MWH_PER_HOUR: float = 6.0  # ELEC_solar = 6 * sunlight_hours
NG_PER_ELEC: int = 8  # 8 NG contracts -> 1 ELEC-day(t+1)
DISPOSAL_PENALTY_PER_CONTRACT: float = 20_000.0
CRUDE_OIL_CAP_PER_DAY: int = 100
MWH_PER_ELEC_DAY: float = 100.0  # 1 ELEC-dayX contract = 100 MWh
MWH_PER_ELEC_F: float = 500.0  # 1 ELEC-F contract = 500 MWh (5 ELEC-dayX)


@dataclass
class PlannerInputs:
    """Inputs to the planner (all optional where market/data may be missing)."""

    distributor_demand: float = 0.0
    tender_net: float = 0.0
    mid_sunlight_hours: Optional[float] = None
    spot_price: Optional[float] = None
    spot_volume: Optional[float] = None
    ng_ask: Optional[float] = None
    elec_f_bid_size: Optional[float] = None
    disposal_budget_contracts: float = 0.0
    sunlight_is_exact: bool = False  # True when evening update received (LOCK-IN)
    extra_forward_liquidity: float = 0.0  # user-entered optional when ELEC-F liquid


@dataclass
class PlannerOutput:
    """Recommendation and intermediate values."""

    tomorrow_day: Optional[int]
    solar_elec_tomorrow: float
    target_total_elec: float
    required_non_solar_elec: float
    q_min_needed: float
    discretionary: float
    q_produce_non_solar: float
    ng_needed: int
    crude_oil_units: int
    disposal_risk_contracts: float
    disposal_risk_dollars: float
    recommendation_line: str
    lock_in_window: bool
    # Supply base (contracts -> MWh)
    solar_mwh: float = 0.0
    conversion_mwh: float = 0.0
    total_supply_mwh: float = 0.0
    # Forwards: recommend how many ELEC-F contracts to sell (capped by production and bid size)
    recommended_forwards_sell_elec_f: float = 0.0


def compute_recommendation(inp: PlannerInputs, tomorrow_day: Optional[int] = None) -> PlannerOutput:
    """
    Pure decision rule.

    - Q_demand = max(0, distributor_demand + tender_net)
    - Q_solar_mid = 6 * mid_sunlight_hours (or 0 if unknown)
    - Q_spot_sell_cap = spot_volume; Q_safe_sell = 0.5 * Q_spot_sell_cap
    - Q_committed = max(0, Q_demand)
    - Q_min_needed = max(0, Q_committed - Q_solar_mid)
    - Q_discretionary_cap = max(0, Q_safe_sell - max(0, Q_solar_mid - Q_committed))
    - Discretionary allowed only if margin_per_elec > 0 and disposal_budget_contracts == 0
    - Q_produce_non_solar = Q_min_needed + discretionary
    - NG_needed = ceil(Q_produce_non_solar * 8); crude capped at 100
    """
    q_demand = max(0.0, inp.distributor_demand + inp.tender_net)
    mid_h = inp.mid_sunlight_hours if inp.mid_sunlight_hours is not None else 0.0
    q_solar_mid = SOLAR_MWH_PER_HOUR * mid_h

    q_spot_sell_cap = inp.spot_volume if inp.spot_volume is not None else 0.0
    q_safe_sell = 0.5 * q_spot_sell_cap
    if inp.elec_f_bid_size is not None and inp.elec_f_bid_size > 0:
        q_safe_sell = q_safe_sell + inp.extra_forward_liquidity

    q_committed = max(0.0, q_demand)
    q_min_needed = max(0.0, q_committed - q_solar_mid)
    q_discretionary_cap = max(
        0.0,
        q_safe_sell - max(0.0, q_solar_mid - q_committed),
    )

    # Pricing gate for discretionary
    margin_ok = False
    if inp.spot_price is not None and inp.ng_ask is not None:
        margin_per_elec = inp.spot_price - (NG_PER_ELEC * inp.ng_ask)
        if margin_per_elec > 0 and inp.disposal_budget_contracts == 0:
            margin_ok = True
    discretionary = min(q_discretionary_cap, q_discretionary_cap if margin_ok else 0.0) if margin_ok else 0.0

    q_produce_non_solar = q_min_needed + discretionary
    ng_needed = math.ceil(q_produce_non_solar * NG_PER_ELEC) if q_produce_non_solar > 0 else 0

    # Crude oil: case says cap 100 units/day; no oil->ELEC ratio given, so we show
    # NG-only production and set crude_units to 0 (or user could split; here we keep minimal)
    crude_oil_units = min(CRUDE_OIL_CAP_PER_DAY, 0)  # 0 when all via NG

    # Disposal risk: excess production over committed + sellable
    target_total = q_solar_mid + q_produce_non_solar
    # Risk = max(0, target_total - committed - safe_sell) roughly; spec: show implied disposal risk
    sellable = q_committed + q_safe_sell
    disposal_risk_contracts = max(0.0, target_total - sellable)
    disposal_risk_dollars = disposal_risk_contracts * DISPOSAL_PENALTY_PER_CONTRACT

    # No-disposal safeguard: if solar already >= demand + confident sellable, zero discretionary
    if q_solar_mid >= q_committed + q_safe_sell and q_safe_sell > 0:
        discretionary = 0.0
        q_produce_non_solar = q_min_needed
        ng_needed = math.ceil(q_produce_non_solar * NG_PER_ELEC) if q_produce_non_solar > 0 else 0
        target_total = q_solar_mid + q_produce_non_solar
        disposal_risk_contracts = max(0.0, target_total - q_committed - q_safe_sell)
        disposal_risk_dollars = disposal_risk_contracts * DISPOSAL_PENALTY_PER_CONTRACT

    rec_line = (
        f"RECOMMENDED: produce {q_produce_non_solar:.1f} ELEC via conversion tomorrow; "
        f"buy {ng_needed} NG today; use {crude_oil_units} crude oil units; "
        f"expected disposal risk: ${disposal_risk_dollars:,.0f}"
    )
    lock_in = inp.sunlight_is_exact

    # Supply base: MWh (1 ELEC-dayX = 100 MWh)
    solar_mwh = q_solar_mid * MWH_PER_ELEC_DAY
    conversion_mwh = q_produce_non_solar * MWH_PER_ELEC_DAY
    total_supply_mwh = target_total * MWH_PER_ELEC_DAY

    # Forwards to sell: ELEC-F contracts (1 ELEC-F = 500 MWh = 5 ELEC-dayX). Conservative: do not exceed production or bid size.
    elec_f_bid = inp.elec_f_bid_size if inp.elec_f_bid_size is not None else 0.0
    recommended_forwards_sell_elec_f = min(
        math.floor(target_total / 5.0),  # ELEC-day equivalent -> ELEC-F contracts
        elec_f_bid,
    )
    recommended_forwards_sell_elec_f = max(0.0, recommended_forwards_sell_elec_f)

    return PlannerOutput(
        tomorrow_day=tomorrow_day,
        solar_elec_tomorrow=q_solar_mid,
        target_total_elec=target_total,
        required_non_solar_elec=q_produce_non_solar,
        q_min_needed=q_min_needed,
        discretionary=discretionary,
        q_produce_non_solar=q_produce_non_solar,
        ng_needed=ng_needed,
        crude_oil_units=crude_oil_units,
        disposal_risk_contracts=disposal_risk_contracts,
        disposal_risk_dollars=disposal_risk_dollars,
        recommendation_line=rec_line,
        lock_in_window=lock_in,
        solar_mwh=solar_mwh,
        conversion_mwh=conversion_mwh,
        total_supply_mwh=total_supply_mwh,
        recommended_forwards_sell_elec_f=recommended_forwards_sell_elec_f,
    )


def _self_test() -> None:
    # Fixed sunlight, demand, spot, NG_ask
    inp = PlannerInputs(
        distributor_demand=50.0,
        tender_net=0.0,
        mid_sunlight_hours=13.0,
        spot_price=18.31,
        spot_volume=402.0,
        ng_ask=2.0,
        disposal_budget_contracts=0.0,
        sunlight_is_exact=True,
    )
    out = compute_recommendation(inp, tomorrow_day=5)
    assert out.solar_elec_tomorrow == 6.0 * 13.0
    assert out.q_min_needed >= 0
    assert "RECOMMENDED:" in out.recommendation_line
    assert "NG today" in out.recommendation_line
    assert "disposal risk" in out.recommendation_line
    assert out.lock_in_window is True
    assert out.ng_needed == math.ceil(out.q_produce_non_solar * 8)
    assert out.solar_mwh == out.solar_elec_tomorrow * 100
    assert out.total_supply_mwh == out.target_total_elec * 100
    assert out.recommended_forwards_sell_elec_f >= 0
    print("planner.py self-test passed.")


if __name__ == "__main__":
    _self_test()
