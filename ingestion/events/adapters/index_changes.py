"""
Index rebalance adapter: IndexChange -> 2 Events (announcement + effective).

S&P typically pre-announces index changes 2-5 business days before they
take effect. Market reaction shows up on BOTH the announcement day
(anticipation) and the effective day (forced index-fund rebalance flow),
so we emit both as separate events. Downstream the joiner can decide
which to weight.
"""
from __future__ import annotations

from schema import Event, IndexChange, IndexChangeAction


def to_events(records: list[IndexChange]) -> list[Event]:
    events: list[Event] = []
    for c in records:
        base_text = _change_text(c)
        events.append(
            Event(
                event_id=f"{c.change_id}_announcement",
                ticker=c.ticker,
                event_date=c.announcement_date,
                event_type="index_change_announcement",
                source="S&P Global",
                payload_ref=f"{c.change_id}_announcement",
                text=f"Announced: {base_text}",
            )
        )
        events.append(
            Event(
                event_id=f"{c.change_id}_effective",
                ticker=c.ticker,
                event_date=c.effective_date,
                event_type="index_change_effective",
                source="S&P Global",
                payload_ref=f"{c.change_id}_effective",
                text=f"Effective: {base_text}",
            )
        )
    return events


def _change_text(c: IndexChange) -> str:
    verb = "added to" if c.action == IndexChangeAction.ADD else "removed from"
    if c.action == IndexChangeAction.ADD and c.replacing_ticker:
        pair = f" (replacing {c.replacing_ticker})"
    elif c.action == IndexChangeAction.DELETE and c.replacing_ticker:
        pair = f" (replaced by {c.replacing_ticker})"
    else:
        pair = ""
    return (
        f"{c.company_name} ({c.ticker}) {verb} {c.index_name}{pair}. "
        f"Effective {c.effective_date.isoformat()}."
    )
