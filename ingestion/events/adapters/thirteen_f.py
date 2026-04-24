"""
13F holding-delta adapter: HoldingDelta -> Event.

event_date = current_filing_date (when the market sees the holdings),
NOT period_end (CLAUDE.md rule 2).
"""
from __future__ import annotations

from schema import Event, HoldingAction, HoldingDelta


def to_events(records: list[HoldingDelta]) -> list[Event]:
    events: list[Event] = []
    for d in records:
        event_id = (
            f"13f_delta_{d.fund_cik}_{d.ticker}_"
            f"{d.current_period_end.isoformat()}"
        )
        events.append(
            Event(
                event_id=event_id,
                ticker=d.ticker,
                event_date=d.current_filing_date,
                event_type="13f_delta",
                source="SEC EDGAR",
                payload_ref=event_id,
                text=_delta_text(d),
            )
        )
    return events


def _delta_text(d: HoldingDelta) -> str:
    mv_millions = d.market_value_change / 1_000_000
    filing = d.current_filing_date.isoformat()
    period = d.current_period_end.isoformat()
    if d.action == HoldingAction.NEW:
        return (
            f"{d.fund_name} opened new {d.ticker} position in {period} "
            f"(filed {filing}): {d.current_shares:,} shares, "
            f"${mv_millions:+,.1f}M."
        )
    if d.action == HoldingAction.EXITED:
        return (
            f"{d.fund_name} exited {d.ticker} in {period} (filed {filing}): "
            f"sold {abs(d.shares_change):,} shares, ${mv_millions:+,.1f}M."
        )
    return (
        f"{d.fund_name} {d.action.value} {d.ticker} in {period} "
        f"(filed {filing}): {d.shares_change:+,} shares "
        f"(now {d.current_shares:,}), ${mv_millions:+,.1f}M value change."
    )
