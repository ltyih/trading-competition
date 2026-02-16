"""Terminal UI: rich panels/tables with fallback to plain text."""

from __future__ import annotations

from typing import Any, Optional

from parsers import SunlightResult, SpotBulletinResult
from planner import PlannerOutput

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

_console: Optional["Console"] = None


def _get_console() -> Optional["Console"]:
    global _console
    if RICH_AVAILABLE and _console is None:
        _console = Console()
    return _console if RICH_AVAILABLE else None


def render_case(period: Any, tick: Any, status: str) -> None:
    c = _get_console()
    if c:
        c.print(Panel(f"Period: [bold]{period}[/]  Tick: [bold]{tick}[/]  Status: [bold green]{status}[/]", title="Case"))
    else:
        print(f"\n--- Case ---\n  Period={period} tick={tick} status={status}")


def render_news(
    last_sunlight: Optional[SunlightResult],
    last_spot: Optional[SpotBulletinResult],
) -> None:
    c = _get_console()
    lines = []
    if last_sunlight:
        lines.append(f"SUNLIGHT: day={last_sunlight.delivery_day} exact={last_sunlight.is_exact()} mid={last_sunlight.mid_hours()} hrs")
    else:
        lines.append("SUNLIGHT: (none yet)")
    if last_spot:
        lines.append(f"SPOT: day={last_spot.delivery_day} price=${last_spot.spot_price} vol={last_spot.spot_contract_volume} contracts")
    else:
        lines.append("SPOT: (none yet)")
    body = "\n".join(lines)
    if c:
        c.print(Panel(body, title="News"))
    else:
        print("\n--- News ---\n" + body)


def render_prices(sec_list: Any, elec_day_pattern: Any) -> None:
    c = _get_console()
    if not isinstance(sec_list, list):
        return
    rows = []
    for s in sec_list:
        if not isinstance(s, dict):
            continue
        ticker = (s.get("ticker") or "").strip()
        if ticker.upper() not in ("NG", "ELEC-F") and not (elec_day_pattern and elec_day_pattern.match(ticker)):
            continue
        bid = s.get("bid")
        ask = s.get("ask")
        last = s.get("last")
        rows.append((ticker, str(bid) if bid is not None else "-", str(ask) if ask is not None else "-", str(last) if last is not None else "-"))
    if c:
        t = Table(title="Prices")
        t.add_column("Ticker")
        t.add_column("Bid")
        t.add_column("Ask")
        t.add_column("Last")
        for r in rows:
            t.add_row(*r)
        c.print(t)
    else:
        print("\n--- Prices ---")
        for r in rows:
            print(f"  {r[0]}: bid={r[1]} ask={r[2]} last={r[3]}")


def render_limits(limits_list: Any) -> None:
    c = _get_console()
    if not isinstance(limits_list, list):
        return
    rows = []
    for lim in limits_list:
        if not isinstance(lim, dict):
            continue
        name = lim.get("name") or "?"
        gross = lim.get("gross")
        net = lim.get("net")
        gl = lim.get("gross_limit")
        nl = lim.get("net_limit")
        rows.append((name, str(gross), str(gl), str(net), str(nl)))
    if c:
        t = Table(title="Limits")
        t.add_column("Name")
        t.add_column("Gross")
        t.add_column("Gross limit")
        t.add_column("Net")
        t.add_column("Net limit")
        for r in rows:
            t.add_row(*r)
        c.print(t)
    else:
        print("\n--- Limits ---")
        for r in rows:
            print(f"  {r[0]}: gross={r[1]}/{r[2]} net={r[3]}/{r[4]}")


def render_supply_base(out: PlannerOutput) -> None:
    """Supply base: solar + conversion + total in contracts and MWh."""
    c = _get_console()
    solar_c = out.solar_elec_tomorrow
    conv_c = out.q_produce_non_solar
    total_c = out.target_total_elec
    solar_mwh = out.solar_mwh
    conv_mwh = out.conversion_mwh
    total_mwh = out.total_supply_mwh
    body = (
        f"Solar tomorrow:     [bold]{solar_c:.1f}[/] ELEC contracts  ({solar_mwh:,.0f} MWh)\n"
        f"Conversion tomorrow: [bold]{conv_c:.1f}[/] ELEC contracts  ({conv_mwh:,.0f} MWh)\n"
        f"Total supply tomorrow: [bold]{total_c:.1f}[/] ELEC contracts  ([bold]{total_mwh:,.0f} MWh[/])"
    )
    if c:
        c.print(Panel(body, title="Supply base (for communication)"))
    else:
        print("\n--- Supply base ---")
        print(f"  Solar tomorrow:     {solar_c:.1f} ELEC contracts  ({solar_mwh:,.0f} MWh)")
        print(f"  Conversion tomorrow: {conv_c:.1f} ELEC contracts  ({conv_mwh:,.0f} MWh)")
        print(f"  Total supply tomorrow: {total_c:.1f} ELEC contracts  ({total_mwh:,.0f} MWh)")


def render_recommendations(out: PlannerOutput) -> None:
    """Forwards to sell + Production (contracts + MWh) + NG + crude + disposal risk."""
    c = _get_console()
    fwd = out.recommended_forwards_sell_elec_f
    fwd_mwh = fwd * 500  # 1 ELEC-F = 500 MWh
    prod_c = out.q_produce_non_solar
    prod_mwh = out.conversion_mwh
    body = (
        f"[bold]Forwards:[/] Sell [bold]{fwd:.0f}[/] ELEC-F contracts ({fwd_mwh:,.0f} MWh)\n"
        f"[bold]Production:[/] Produce [bold]{prod_c:.1f}[/] ELEC contracts ({prod_mwh:,.0f} MWh) via conversion; "
        f"buy [bold]{out.ng_needed}[/] NG today; use [bold]{out.crude_oil_units}[/] crude oil units\n"
        f"Expected disposal risk: [bold]${out.disposal_risk_dollars:,.0f}[/]"
    )
    if c:
        c.print(Panel(body, title="Recommendations"))
    else:
        print("\n--- Recommendations ---")
        print(f"  Forwards: Sell {fwd:.0f} ELEC-F contracts ({fwd_mwh:,.0f} MWh)")
        print(f"  Production: Produce {prod_c:.1f} ELEC contracts ({prod_mwh:,.0f} MWh); buy {out.ng_needed} NG; use {out.crude_oil_units} crude")
        print(f"  Expected disposal risk: ${out.disposal_risk_dollars:,.0f}")


def render_lock_in(lock_in: bool) -> None:
    c = _get_console()
    if lock_in:
        msg = "Sunlight exact evening update received; recommendation is final for tomorrow."
        if c:
            c.print(Panel(Text(msg, style="bold green"), title="LOCK-IN WINDOW"))
        else:
            print("\nLOCK-IN WINDOW: " + msg)
    else:
        msg = "Not yet (wait for exact evening sunlight update for tomorrow)."
        if c:
            c.print(Panel(Text(msg, style="yellow"), title="LOCK-IN WINDOW"))
        else:
            print("\nLOCK-IN WINDOW: " + msg)


def print_news_item(kind: str, detail: str) -> None:
    """Log a single parsed news item (e.g. [SUNLIGHT] or [SPOT])."""
    c = _get_console()
    if c:
        c.print(f"  [dim][{kind}][/] {detail}")
    else:
        print(f"  [{kind}] {detail}")
