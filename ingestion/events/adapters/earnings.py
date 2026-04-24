"""
Earnings calendar adapter: stock_earning_calendar.parquet -> Event("earnings_release").

Row shape (HF schema — see docs/hf_schemas.md):

    {
        "symbol": "AAPL",
        "report_date": "2024-02-01",        # earnings date
        "time": "post",                     # "pre" or "post" market
        "name": "Apple Inc.",
        "fiscal_quarter_ending": "2023-12-30",
    }

Used two ways downstream:
  1. As Events in the unified events table (so the attribution model sees
     the earnings release as a candidate driver).
  2. By `join_evidence` to set `JoinedEvidence.earnings_day=True` when the
     flagged move_date matches a scheduled earnings release.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from schema import Event


def to_events(records: list[dict[str, Any]]) -> list[Event]:
    events: list[Event] = []
    for row in records:
        ticker = (row.get("symbol") or "").strip().upper()
        report_date = _coerce_date(row.get("report_date"))
        if not ticker or report_date is None:
            continue
        event_id = f"earnings_{ticker}_{report_date.isoformat()}"
        timing = (row.get("time") or "").lower()
        timing_text = (
            "before market open" if timing == "pre"
            else "after market close" if timing == "post"
            else "release"
        )
        fq = row.get("fiscal_quarter_ending") or ""
        fq_text = f" (fiscal period ending {fq})" if fq else ""
        text = (
            f"{ticker} scheduled earnings {timing_text} on "
            f"{report_date.isoformat()}{fq_text}."
        )
        events.append(
            Event(
                event_id=event_id,
                ticker=ticker,
                event_date=report_date,
                event_type="earnings_release",
                source="yahoo_earnings_calendar",
                payload_ref=event_id,
                text=text,
            )
        )
    return events


def _coerce_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    s = str(v)
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None
