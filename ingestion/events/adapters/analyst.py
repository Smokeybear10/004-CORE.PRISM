"""
Analyst adapters: AnalystRating -> Event, PriceTargetChange -> Event.

Both use action_date as event_date (the market sees these same-day).
Two separate entry points since they come from different Pydantic models
and different data sources.
"""
from __future__ import annotations

from schema import AnalystRating, Event, PriceTargetChange


def rating_to_events(records: list[AnalystRating]) -> list[Event]:
    events: list[Event] = []
    for r in records:
        events.append(
            Event(
                event_id=r.rating_id,
                ticker=r.ticker,
                event_date=r.action_date,
                event_type="analyst_rating_change",
                source=r.analyst_firm,
                payload_ref=r.rating_id,
                text=_rating_text(r),
            )
        )
    return events


def target_to_events(records: list[PriceTargetChange]) -> list[Event]:
    events: list[Event] = []
    for t in records:
        events.append(
            Event(
                event_id=t.target_id,
                ticker=t.ticker,
                event_date=t.action_date,
                event_type="price_target_change",
                source=t.analyst_firm,
                payload_ref=t.target_id,
                text=_target_text(t),
            )
        )
    return events


def to_events(
    ratings: list[AnalystRating] | None = None,
    targets: list[PriceTargetChange] | None = None,
) -> list[Event]:
    """Convenience dispatcher used by the aggregator."""
    out: list[Event] = []
    if ratings:
        out.extend(rating_to_events(ratings))
    if targets:
        out.extend(target_to_events(targets))
    return out


# ---------- text synth ----------

def _rating_text(r: AnalystRating) -> str:
    action = r.action.value
    if r.prior_rating and r.new_rating:
        body = f"{r.prior_rating} -> {r.new_rating}"
    elif r.new_rating:
        body = f"{r.new_rating}"
    else:
        body = "(no rating)"
    analyst = f" ({r.analyst_name})" if r.analyst_name else ""
    return (
        f"{r.analyst_firm}{analyst} {action} {r.ticker}: {body} "
        f"on {r.action_date.isoformat()}."
    )


def _target_text(t: PriceTargetChange) -> str:
    analyst = f" ({t.analyst_name})" if t.analyst_name else ""
    if t.prior_target is not None and t.new_target is not None:
        change = f"${t.prior_target:.2f} -> ${t.new_target:.2f}"
        if t.change_pct is not None:
            change += f" ({t.change_pct:+.1%})"
    elif t.new_target is not None:
        change = f"initiated at ${t.new_target:.2f}"
    else:
        change = "(target withdrawn)"
    return (
        f"{t.analyst_firm}{analyst} price target on {t.ticker}: {change} "
        f"on {t.action_date.isoformat()}."
    )
