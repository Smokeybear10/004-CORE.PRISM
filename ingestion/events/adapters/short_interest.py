"""
FINRA short-interest adapter: ShortInterestRecord -> Event("short_interest_spike").

Emits an Event ONLY when period-over-period shares_short rises by more than
SPIKE_THRESHOLD (20%). event_date uses settlement_date directly — FINRA
publishes ~8 business days after settlement; for hackathon scope that lag
is acceptable (not joined against intraday moves).
"""
from __future__ import annotations

from schema import Event, ShortInterestRecord

SPIKE_THRESHOLD = 0.20


def to_events(records: list[ShortInterestRecord]) -> list[Event]:
    by_ticker: dict[str, list[ShortInterestRecord]] = {}
    for r in records:
        by_ticker.setdefault(r.ticker, []).append(r)

    events: list[Event] = []
    for ticker, trows in by_ticker.items():
        trows.sort(key=lambda r: r.settlement_date)
        for prior, current in zip(trows, trows[1:]):
            if prior.shares_short <= 0:
                continue
            change = (current.shares_short - prior.shares_short) / prior.shares_short
            if change <= SPIKE_THRESHOLD:
                continue
            event_id = (
                f"short_interest_spike_{ticker}_"
                f"{current.settlement_date.isoformat()}"
            )
            dtc = (
                f"{current.days_to_cover:.2f}"
                if current.days_to_cover is not None
                else "n/a"
            )
            text = (
                f"{ticker} short interest rose {change:.1%} from "
                f"{prior.shares_short:,} to {current.shares_short:,} shares "
                f"at the {current.settlement_date.isoformat()} settlement "
                f"(days-to-cover {dtc})."
            )
            events.append(
                Event(
                    event_id=event_id,
                    ticker=ticker,
                    event_date=current.settlement_date,
                    event_type="short_interest_spike",
                    source="FINRA",
                    payload_ref=event_id,
                    text=text,
                )
            )
    return events
