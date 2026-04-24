"""
Event joiner: window-join a PriceMove against the unified events + text_chunks
tables to produce a `JoinedEvidence` bundle for the attribution runner.

Window is in TRADING days (not calendar days). If the caller supplies a
trading-day index (e.g. derived from `ingestion.prices.load_prices`), we
use that. Otherwise we fall back to pandas business-day offsets, which
cover Mon-Fri and miss US market holidays — fine for hackathon scope.

Cap at 30 events per move. Sort by (proximity-to-move, type-priority):

    short_report > earnings_release > news > 13f_delta > index_change

Other event types (analyst, fda, short_interest_spike) interleave at a
middle priority; proximity dominates.

`earnings_day=True` when `move.move_date` matches a scheduled earnings
release for the ticker in the supplied earnings calendar.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

import pandas as pd
from pandas.tseries.offsets import BDay

from schema import Event, JoinedEvidence, PriceMove, SourceType, TextChunk

# Lower number = higher priority.
EVENT_PRIORITY: dict[str, int] = {
    "short_report": 1,
    "earnings_release": 2,
    "analyst_rating_change": 3,
    "price_target_change": 3,
    "pdufa": 3,
    "adcomm": 3,
    "approval": 3,
    "crl": 3,
    "denial": 3,
    "news": 4,
    "short_interest_spike": 5,
    "13f_delta": 6,
    "index_change_announcement": 7,
    "index_change_effective": 7,
}
_DEFAULT_PRIORITY = 8  # anything unrecognized goes to the bottom
MAX_EVENTS_PER_MOVE = 30


def join_evidence(
    move: PriceMove,
    events_df: pd.DataFrame,
    chunks_df: pd.DataFrame,
    earnings_calendar: pd.DataFrame,
    window_before: int = 5,
    window_after: int = 1,
    trading_days: Optional[Iterable[date]] = None,
) -> JoinedEvidence:
    """
    Build the evidence bundle for `move`.

    Args:
        move: the flagged PriceMove.
        events_df: DataFrame with at least
            [event_id, ticker, event_date, event_type, source, payload_ref, text].
        chunks_df: DataFrame with at least
            [chunk_id, ticker, source_type, publication_date, ...other TextChunk fields].
        earnings_calendar: DataFrame with at least [ticker, report_date].
            Used to flag `earnings_day` when move_date matches a scheduled release.
        window_before / window_after: trading-day window around move_date (inclusive).
        trading_days: optional iterable of date; if provided, used to compute the
            window. Otherwise falls back to pandas business-day offsets.

    Returns:
        JoinedEvidence with events capped at 30, ordered by (proximity, priority).
    """
    window_start, window_end = _compute_window(
        move.move_date, window_before, window_after, trading_days
    )

    ticker_events = _filter_events(events_df, move.ticker, window_start, window_end)
    selected = _rank_and_cap(ticker_events, move.move_date)

    # Collect text chunks matching the selected events by payload_ref / chunk_id,
    # and any ticker chunks that fall in-window regardless.
    chunks = _collect_chunks(chunks_df, move.ticker, window_start, window_end, selected)

    earnings_day = _is_earnings_day(earnings_calendar, move.ticker, move.move_date)

    return JoinedEvidence(
        move=move,
        window_start=window_start,
        window_end=window_end,
        events=selected,
        text_chunks=chunks,
        earnings_day=earnings_day,
    )


# ---------- window ----------

def _compute_window(
    move_date: date,
    before: int,
    after: int,
    trading_days: Optional[Iterable[date]],
) -> tuple[date, date]:
    if trading_days is not None:
        days = sorted({_as_date(d) for d in trading_days})
        if not days:
            return _bday_window(move_date, before, after)
        # Anchor at the largest trading day <= move_date (move may land on a
        # non-trading day in degenerate test inputs; never look forward to
        # anchor, that would leak).
        anchor_idx = _last_le_index(days, move_date)
        if anchor_idx is None:
            return _bday_window(move_date, before, after)
        start_idx = max(0, anchor_idx - before)
        end_idx = min(len(days) - 1, anchor_idx + after)
        return days[start_idx], days[end_idx]
    return _bday_window(move_date, before, after)


def _bday_window(move_date: date, before: int, after: int) -> tuple[date, date]:
    ts = pd.Timestamp(move_date)
    # pandas BDay treats the anchor day as business day 0 if it's a weekday.
    # We want the anchor included on the weekday side; shift by `before` full
    # business days, then add `after` business days on the right.
    start_ts = (ts - BDay(before)).normalize()
    end_ts = (ts + BDay(after)).normalize()
    return start_ts.date(), end_ts.date()


def _last_le_index(days: list[date], target: date) -> Optional[int]:
    # small list -> linear scan is fine
    best: Optional[int] = None
    for i, d in enumerate(days):
        if d <= target:
            best = i
        else:
            break
    return best


# ---------- filter / rank ----------

def _filter_events(
    events_df: pd.DataFrame,
    ticker: str,
    window_start: date,
    window_end: date,
) -> list[Event]:
    if events_df.empty:
        return []
    df = events_df.copy()
    df["event_date"] = df["event_date"].map(_as_date)
    mask = (
        (df["ticker"].str.upper() == ticker.upper())
        & (df["event_date"] >= window_start)
        & (df["event_date"] <= window_end)
    )
    rows = df[mask].to_dict(orient="records")
    out: list[Event] = []
    for row in rows:
        out.append(
            Event(
                event_id=str(row["event_id"]),
                ticker=str(row["ticker"]),
                event_date=row["event_date"],
                event_type=str(row["event_type"]),
                source=str(row["source"]),
                payload_ref=str(row["payload_ref"]),
                text=row.get("text") if row.get("text") is not None else None,
            )
        )
    return out


def _rank_and_cap(events: list[Event], move_date: date) -> list[Event]:
    def sort_key(e: Event) -> tuple[int, int, str]:
        proximity = abs((e.event_date - move_date).days)
        priority = EVENT_PRIORITY.get(e.event_type, _DEFAULT_PRIORITY)
        return (proximity, priority, e.event_id)

    ordered = sorted(events, key=sort_key)
    return ordered[:MAX_EVENTS_PER_MOVE]


# ---------- chunks ----------

def _collect_chunks(
    chunks_df: pd.DataFrame,
    ticker: str,
    window_start: date,
    window_end: date,
    selected_events: list[Event],
) -> list[TextChunk]:
    if chunks_df.empty:
        return []
    df = chunks_df.copy()
    df["publication_date"] = df["publication_date"].map(_as_date)

    # Take two sets of chunks and union them by chunk_id:
    # (a) chunks for the ticker that land in-window
    # (b) chunks whose chunk_id is referenced by a selected event's payload_ref
    #     (covers cross-ticker / _MACRO chunks that still matter to this move)
    wanted_ids = {e.payload_ref for e in selected_events if e.payload_ref}

    in_window = (
        (df["ticker"].str.upper() == ticker.upper())
        & (df["publication_date"] >= window_start)
        & (df["publication_date"] <= window_end)
    )
    by_ref = df["chunk_id"].isin(wanted_ids)
    rows = df[in_window | by_ref].to_dict(orient="records")

    seen_ids: set[str] = set()
    out: list[TextChunk] = []
    for row in rows:
        cid = str(row["chunk_id"])
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        out.append(_row_to_chunk(row))
    return out


def _row_to_chunk(row: dict) -> TextChunk:
    source_type = row["source_type"]
    if isinstance(source_type, str):
        source_type = SourceType(source_type)
    period_end = row.get("period_end")
    period_end = _as_date(period_end) if _has_value(period_end) else None
    token_count = row.get("token_count")
    tc: Optional[int]
    if _has_value(token_count):
        try:
            tc = int(token_count)
        except (TypeError, ValueError):
            tc = None
    else:
        tc = None
    return TextChunk(
        chunk_id=str(row["chunk_id"]),
        ticker=str(row["ticker"]),
        source_type=source_type,
        publication_date=row["publication_date"],
        period_end=period_end,
        source_url=row.get("source_url") if _has_value(row.get("source_url")) else None,
        section_name=row.get("section_name") if _has_value(row.get("section_name")) else None,
        text=str(row["text"]),
        token_count=tc,
    )


# ---------- earnings day ----------

def _is_earnings_day(
    earnings_calendar: pd.DataFrame,
    ticker: str,
    move_date: date,
) -> bool:
    if earnings_calendar is None or earnings_calendar.empty:
        return False
    df = earnings_calendar
    # Normalize on-the-fly; avoid mutating caller's DataFrame.
    tickers = df["ticker"].str.upper() if "ticker" in df.columns else pd.Series([], dtype=str)
    if "report_date" in df.columns:
        dates = df["report_date"].map(_as_date)
    elif "earnings_date" in df.columns:
        dates = df["earnings_date"].map(_as_date)
    else:
        return False
    mask = (tickers == ticker.upper()) & (dates == move_date)
    return bool(mask.any())


# ---------- coercion ----------

def _as_date(v) -> date:
    if isinstance(v, date):
        return v
    if isinstance(v, pd.Timestamp):
        return v.date()
    return date.fromisoformat(str(v)[:10])


def _has_value(v) -> bool:
    if v is None:
        return False
    try:
        if pd.isna(v):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(v, str) and (v == "" or v.lower() in ("nan", "none")):
        return False
    return True
