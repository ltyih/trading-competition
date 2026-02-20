"""Flask web UI for Producer Planner."""

from __future__ import annotations

import re
from typing import Any, Optional

from flask import Flask, jsonify, render_template, request

import config
from api_client import api_get
from parsers import parse_sunlight, parse_spot_bulletin, SunlightResult, SpotBulletinResult
from planner import PlannerInputs, PlannerOutput, compute_recommendation

app = Flask(__name__)

# Ticker pattern
elec_day_pattern = re.compile(r"^ELEC-day\d+$", re.IGNORECASE)


@app.route("/")
def index():
    """Serve the main HTML page."""
    return render_template("index.html")


@app.route("/api/case")
def api_case():
    """Proxy GET /v1/case."""
    try:
        data = api_get("case")
        return jsonify(data if data is not None else {})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/news")
def api_news():
    """Proxy GET /v1/news?since=<id>."""
    since = request.args.get("since", "0")
    try:
        data = api_get("news", params={"since": since})
        return jsonify(data if data is not None else [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/securities")
def api_securities():
    """Proxy GET /v1/securities."""
    try:
        data = api_get("securities")
        return jsonify(data if data is not None else [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/limits")
def api_limits():
    """Proxy GET /v1/limits."""
    try:
        data = api_get("limits")
        return jsonify(data if data is not None else [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/recommendation", methods=["POST"])
def api_recommendation():
    """Compute recommendation from current state + user inputs."""
    try:
        body = request.get_json()
        distributor_demand = float(body.get("distributor_demand", 0))
        tender_net = float(body.get("tender_net", 0))

        # Fetch current state
        case = api_get("case")
        if not isinstance(case, dict):
            return jsonify({"error": "Failed to fetch case"}), 500
        period = case.get("period")

        # Fetch and parse news
        news_list = api_get("news")
        last_sunlight: Optional[SunlightResult] = None
        last_spot: Optional[SpotBulletinResult] = None
        tomorrow_day: Optional[int] = None
        if isinstance(news_list, list):
            for item in news_list:
                if not isinstance(item, dict):
                    continue
                headline = item.get("headline") or ""
                body = item.get("body") or ""
                sun = parse_sunlight(headline, body)
                if sun is not None:
                    last_sunlight = sun
                    if tomorrow_day is None:
                        tomorrow_day = sun.delivery_day or (period + 1 if period is not None else None)
                spot = parse_spot_bulletin(headline, body)
                if spot is not None:
                    last_spot = spot
                    if tomorrow_day is None:
                        tomorrow_day = spot.delivery_day

        # Fetch securities for NG ask and ELEC-F bid_size
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

        # Build planner inputs
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

        # Serialize PlannerOutput to dict
        result = {
            "tomorrow_day": out.tomorrow_day,
            "solar_elec_tomorrow": out.solar_elec_tomorrow,
            "target_total_elec": out.target_total_elec,
            "required_non_solar_elec": out.required_non_solar_elec,
            "q_min_needed": out.q_min_needed,
            "discretionary": out.discretionary,
            "q_produce_non_solar": out.q_produce_non_solar,
            "ng_needed": out.ng_needed,
            "crude_oil_units": out.crude_oil_units,
            "disposal_risk_contracts": out.disposal_risk_contracts,
            "disposal_risk_dollars": out.disposal_risk_dollars,
            "recommendation_line": out.recommendation_line,
            "lock_in_window": out.lock_in_window,
            "solar_mwh": out.solar_mwh,
            "conversion_mwh": out.conversion_mwh,
            "total_supply_mwh": out.total_supply_mwh,
            "recommended_forwards_sell_elec_f": out.recommended_forwards_sell_elec_f,
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
