"""
FDA regulatory-event adapter: FDAEvent -> Event.

event_date uses FDAEvent.event_date directly. For PDUFA and AdComm,
that date is typically public well in advance; for approvals, CRLs, and
denials it's the decision/announcement date.

Sponsor-ticker mapping is the upstream fetcher's job — if sponsor_ticker
is None (private sponsor), we skip the record.
"""
from __future__ import annotations

from schema import Event, FDAEvent


def to_events(records: list[FDAEvent]) -> list[Event]:
    events: list[Event] = []
    for f in records:
        if f.sponsor_ticker is None:
            continue  # no ticker -> nothing to join against
        text = _event_text(f)
        events.append(
            Event(
                event_id=f.event_id,
                ticker=f.sponsor_ticker,
                event_date=f.event_date,
                event_type=f.event_type.value,
                source="FDA",
                payload_ref=f.event_id,
                text=text,
            )
        )
    return events


def _event_text(f: FDAEvent) -> str:
    indication = f" for {f.indication}" if f.indication else ""
    return (
        f"FDA {f.event_type.value.upper()} for {f.drug_name}{indication} "
        f"({f.sponsor_ticker}) on {f.event_date.isoformat()}: {f.description}"
    )
