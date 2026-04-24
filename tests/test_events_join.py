"""
Tests for ingestion/events/join.py.

Covers: window bounds (trading days, not calendar), ticker filter,
30-event cap by (proximity, type priority), earnings_day detection,
and behavior when supplied with an explicit trading-day index.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from schema import PriceMove
from ingestion.events.join import (
    EVENT_PRIORITY,
    MAX_EVENTS_PER_MOVE,
    join_evidence,
)


MOVE = PriceMove(
    ticker="AAPL",
    move_date=date(2024, 2, 2),   # Friday
    return_pct=-0.037,
    vol_zscore=-2.8,
    magnitude_rank=0.97,
)


def _event(
    event_id: str,
    ticker: str,
    event_date: date,
    event_type: str,
    source: str = "test",
    text: str | None = None,
) -> dict:
    return {
        "event_id": event_id,
        "ticker": ticker,
        "event_date": event_date.isoformat(),
        "event_type": event_type,
        "source": source,
        "payload_ref": event_id,
        "text": text if text is not None else f"{event_type} @ {event_date.isoformat()}",
    }


def _events_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=[
            "event_id", "ticker", "event_date", "event_type",
            "source", "payload_ref", "text",
        ]
    )


def _empty_chunks() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "chunk_id", "ticker", "source_type", "publication_date",
            "period_end", "source_url", "section_name", "text", "token_count",
        ]
    )


def _empty_earnings() -> pd.DataFrame:
    return pd.DataFrame(columns=["ticker", "report_date"])


# ---------- window bounds ----------

def test_window_is_in_trading_days_not_calendar():
    """Move on Friday, window_before=5. Calendar-day window_start would be
    the previous Sunday (Jan 28); trading-day start must be the prior Friday
    (Jan 26) — 5 business days back.
    """
    ev = join_evidence(
        move=MOVE,
        events_df=_events_df([]),
        chunks_df=_empty_chunks(),
        earnings_calendar=_empty_earnings(),
        window_before=5,
        window_after=1,
    )
    # Default fallback uses pandas BDay which skips weekends only.
    # Friday 2024-02-02 minus 5 BDay = Friday 2024-01-26.
    # Friday 2024-02-02 plus  1 BDay = Monday 2024-02-05.
    assert ev.window_start == date(2024, 1, 26)
    assert ev.window_end == date(2024, 2, 5)


def test_window_excludes_events_outside_bounds():
    rows = [
        _event("too_early", "AAPL", date(2024, 1, 25), "news"),      # outside
        _event("boundary_start", "AAPL", date(2024, 1, 26), "news"),  # inside
        _event("at_move", "AAPL", date(2024, 2, 2), "news"),          # inside
        _event("boundary_end", "AAPL", date(2024, 2, 5), "news"),     # inside
        _event("too_late", "AAPL", date(2024, 2, 6), "news"),         # outside
    ]
    ev = join_evidence(MOVE, _events_df(rows), _empty_chunks(), _empty_earnings())
    ids = {e.event_id for e in ev.events}
    assert ids == {"boundary_start", "at_move", "boundary_end"}


def test_ticker_filter_excludes_other_tickers():
    rows = [
        _event("match", "AAPL", date(2024, 2, 1), "news"),
        _event("other", "MSFT", date(2024, 2, 1), "news"),
        _event("case_diff", "aapl", date(2024, 2, 1), "news"),  # case-insensitive
    ]
    ev = join_evidence(MOVE, _events_df(rows), _empty_chunks(), _empty_earnings())
    ids = {e.event_id for e in ev.events}
    assert "other" not in ids
    assert "match" in ids
    assert "case_diff" in ids


# ---------- explicit trading-day index ----------

def test_custom_trading_days_is_respected():
    # Provide a sparse trading-day list. window_before=2 from the move_date
    # should step back two entries in this list.
    trading_days = [
        date(2024, 1, 15),
        date(2024, 1, 20),
        date(2024, 1, 25),
        date(2024, 1, 30),
        date(2024, 2, 2),   # the move
        date(2024, 2, 7),
    ]
    ev = join_evidence(
        move=MOVE,
        events_df=_events_df([]),
        chunks_df=_empty_chunks(),
        earnings_calendar=_empty_earnings(),
        window_before=2,
        window_after=1,
        trading_days=trading_days,
    )
    assert ev.window_start == date(2024, 1, 25)
    assert ev.window_end == date(2024, 2, 7)


def test_custom_trading_days_clips_at_edges():
    trading_days = [date(2024, 2, 1), date(2024, 2, 2), date(2024, 2, 3)]
    ev = join_evidence(
        move=MOVE,
        events_df=_events_df([]),
        chunks_df=_empty_chunks(),
        earnings_calendar=_empty_earnings(),
        window_before=10,
        window_after=10,
        trading_days=trading_days,
    )
    # Can't go before/after the list.
    assert ev.window_start == date(2024, 2, 1)
    assert ev.window_end == date(2024, 2, 3)


# ---------- cap + priority ----------

def test_event_cap_at_30():
    # 50 news events for AAPL all within-window.
    rows = [
        _event(f"n{i}", "AAPL", date(2024, 1, 29) + pd.Timedelta(days=i % 5).to_pytimedelta(), "news")
        for i in range(50)
    ]
    ev = join_evidence(MOVE, _events_df(rows), _empty_chunks(), _empty_earnings())
    assert len(ev.events) == MAX_EVENTS_PER_MOVE


def test_priority_breaks_ties_on_same_day():
    # All events land on move_date so proximity is 0 for all; priority orders them.
    rows = [
        _event("idx", "AAPL", MOVE.move_date, "index_change_effective"),
        _event("thirteenf", "AAPL", MOVE.move_date, "13f_delta"),
        _event("n", "AAPL", MOVE.move_date, "news"),
        _event("earn", "AAPL", MOVE.move_date, "earnings_release"),
        _event("sr", "AAPL", MOVE.move_date, "short_report"),
    ]
    ev = join_evidence(MOVE, _events_df(rows), _empty_chunks(), _empty_earnings())
    order = [e.event_id for e in ev.events]
    assert order == ["sr", "earn", "n", "thirteenf", "idx"]

    # Sanity: the priority constants actually enforce this order.
    assert (
        EVENT_PRIORITY["short_report"]
        < EVENT_PRIORITY["earnings_release"]
        < EVENT_PRIORITY["news"]
        < EVENT_PRIORITY["13f_delta"]
        < EVENT_PRIORITY["index_change_effective"]
    )


def test_proximity_dominates_priority():
    # A low-priority event ON move_date beats a high-priority one 5 days away.
    rows = [
        _event("near_low", "AAPL", MOVE.move_date, "index_change_effective"),
        _event("far_high", "AAPL", date(2024, 1, 26), "short_report"),
    ]
    ev = join_evidence(MOVE, _events_df(rows), _empty_chunks(), _empty_earnings())
    order = [e.event_id for e in ev.events]
    assert order == ["near_low", "far_high"]


# ---------- earnings_day flag ----------

def test_earnings_day_true_when_move_matches_scheduled_release():
    cal = pd.DataFrame(
        [{"ticker": "AAPL", "report_date": MOVE.move_date.isoformat()}]
    )
    ev = join_evidence(MOVE, _events_df([]), _empty_chunks(), cal)
    assert ev.earnings_day is True


def test_earnings_day_false_when_no_release_scheduled():
    cal = pd.DataFrame(
        [{"ticker": "AAPL", "report_date": date(2024, 3, 1).isoformat()}]
    )
    ev = join_evidence(MOVE, _events_df([]), _empty_chunks(), cal)
    assert ev.earnings_day is False


def test_earnings_day_ignores_other_tickers():
    cal = pd.DataFrame(
        [{"ticker": "MSFT", "report_date": MOVE.move_date.isoformat()}]
    )
    ev = join_evidence(MOVE, _events_df([]), _empty_chunks(), cal)
    assert ev.earnings_day is False


def test_earnings_day_handles_empty_calendar():
    ev = join_evidence(MOVE, _events_df([]), _empty_chunks(), _empty_earnings())
    assert ev.earnings_day is False


# ---------- chunk selection ----------

def test_chunks_pulled_for_ticker_in_window():
    chunks = pd.DataFrame(
        [
            {
                "chunk_id": "news_xxx_p0",
                "ticker": "AAPL",
                "source_type": "news",
                "publication_date": date(2024, 2, 1).isoformat(),
                "period_end": None,
                "source_url": "https://example.com/x",
                "section_name": "p0",
                "text": "Apple news paragraph.",
                "token_count": 3,
            },
            {
                "chunk_id": "news_yyy_p0",
                "ticker": "MSFT",
                "source_type": "news",
                "publication_date": date(2024, 2, 1).isoformat(),
                "period_end": None,
                "source_url": None,
                "section_name": "p0",
                "text": "Msft news paragraph.",
                "token_count": 3,
            },
        ]
    )
    ev = join_evidence(MOVE, _events_df([]), chunks, _empty_earnings())
    cids = {c.chunk_id for c in ev.text_chunks}
    assert cids == {"news_xxx_p0"}


def test_joined_evidence_is_pydantic_valid():
    """The returned object must round-trip through model_dump_json without
    pydantic errors — that's what the attribution runner depends on."""
    rows = [_event("n1", "AAPL", MOVE.move_date, "news", text="body")]
    ev = join_evidence(MOVE, _events_df(rows), _empty_chunks(), _empty_earnings())
    s = ev.model_dump_json()
    assert '"AAPL"' in s
    assert '"n1"' in s


# ---------- additional window / filtering cases ----------

def test_zero_window_is_move_date_only():
    """window_before=0, window_after=0 -> only events ON move_date."""
    rows = [
        _event("on_move", "AAPL", MOVE.move_date, "news"),
        _event("day_before", "AAPL", date(2024, 2, 1), "news"),
        _event("day_after", "AAPL", date(2024, 2, 5), "news"),
    ]
    ev = join_evidence(
        MOVE, _events_df(rows), _empty_chunks(), _empty_earnings(),
        window_before=0, window_after=0,
    )
    ids = {e.event_id for e in ev.events}
    assert ids == {"on_move"}


def test_empty_events_df_yields_empty_bundle():
    ev = join_evidence(MOVE, _events_df([]), _empty_chunks(), _empty_earnings())
    assert ev.events == []
    assert ev.text_chunks == []
    assert ev.earnings_day is False


def test_events_with_null_text_survive():
    row = _event("t1", "AAPL", MOVE.move_date, "news")
    row["text"] = None
    ev = join_evidence(MOVE, _events_df([row]), _empty_chunks(), _empty_earnings())
    assert len(ev.events) == 1
    assert ev.events[0].text is None


def test_unknown_event_type_gets_default_priority():
    """Event types not in EVENT_PRIORITY get a low default priority so they
    sort after named types with the same proximity."""
    rows = [
        _event("weird", "AAPL", MOVE.move_date, "fomc_speech"),     # unknown
        _event("newsy", "AAPL", MOVE.move_date, "news"),            # known, pri=4
    ]
    ev = join_evidence(MOVE, _events_df(rows), _empty_chunks(), _empty_earnings())
    ids = [e.event_id for e in ev.events]
    # news wins over unknown at the same proximity.
    assert ids == ["newsy", "weird"]


def test_same_day_same_type_stable_by_event_id():
    """When proximity AND priority tie, the final tiebreaker is event_id
    alphabetical order (deterministic)."""
    rows = [
        _event("zzz_last", "AAPL", MOVE.move_date, "news"),
        _event("aaa_first", "AAPL", MOVE.move_date, "news"),
        _event("mmm_mid", "AAPL", MOVE.move_date, "news"),
    ]
    ev = join_evidence(MOVE, _events_df(rows), _empty_chunks(), _empty_earnings())
    order = [e.event_id for e in ev.events]
    assert order == sorted(order)


def test_case_insensitive_ticker_filter_both_directions():
    """Move has ticker 'AAPL'; events with 'aapl' or 'AAPL' both match;
    'Aapl' mixed-case matches too."""
    rows = [
        _event("u", "AAPL", MOVE.move_date, "news"),
        _event("l", "aapl", MOVE.move_date, "news"),
        _event("m", "Aapl", MOVE.move_date, "news"),
        _event("x", "AAPLX", MOVE.move_date, "news"),  # NOT a match
    ]
    ev = join_evidence(MOVE, _events_df(rows), _empty_chunks(), _empty_earnings())
    ids = {e.event_id for e in ev.events}
    assert ids == {"u", "l", "m"}


def test_chunks_pulled_by_payload_ref_even_across_tickers():
    """If a selected event's payload_ref matches a chunk_id, pull that chunk
    even if the chunk's ticker differs (e.g., _MACRO chunks)."""
    event_rows = [
        _event("macro_1", "AAPL", MOVE.move_date, "news"),
    ]
    events_df = _events_df(event_rows)
    # We fake the payload_ref to point at a _MACRO chunk.
    events_df.loc[0, "payload_ref"] = "macro_chunk_xyz"

    chunks = pd.DataFrame(
        [{
            "chunk_id": "macro_chunk_xyz",
            "ticker": "_MACRO",                              # cross-ticker
            "source_type": "macro",
            "publication_date": MOVE.move_date.isoformat(),
            "period_end": None,
            "source_url": None,
            "section_name": None,
            "text": "Fed statement.",
            "token_count": 2,
        }]
    )
    ev = join_evidence(MOVE, events_df, chunks, _empty_earnings())
    cids = {c.chunk_id for c in ev.text_chunks}
    assert "macro_chunk_xyz" in cids


def test_chunks_out_of_window_and_not_referenced_excluded():
    chunks = pd.DataFrame(
        [
            {
                "chunk_id": "stale",
                "ticker": "AAPL",
                "source_type": "news",
                "publication_date": date(2023, 12, 1).isoformat(),  # way outside window
                "period_end": None,
                "source_url": None,
                "section_name": None,
                "text": "old",
                "token_count": 1,
            },
        ]
    )
    ev = join_evidence(MOVE, _events_df([]), chunks, _empty_earnings())
    assert ev.text_chunks == []


def test_earnings_day_accepts_earnings_date_column():
    """Earnings calendars sometimes use 'earnings_date' instead of 'report_date'."""
    cal = pd.DataFrame(
        [{"ticker": "AAPL", "earnings_date": MOVE.move_date.isoformat()}]
    )
    ev = join_evidence(MOVE, _events_df([]), _empty_chunks(), cal)
    assert ev.earnings_day is True


def test_earnings_day_case_insensitive_ticker():
    cal = pd.DataFrame(
        [{"ticker": "aapl", "report_date": MOVE.move_date.isoformat()}]
    )
    ev = join_evidence(MOVE, _events_df([]), _empty_chunks(), cal)
    assert ev.earnings_day is True


def test_earnings_day_unknown_columns_returns_false():
    """If the calendar frame is non-empty but has neither 'report_date' nor
    'earnings_date', fail closed (False) rather than crash."""
    cal = pd.DataFrame([{"ticker": "AAPL", "some_other_col": "whatever"}])
    ev = join_evidence(MOVE, _events_df([]), _empty_chunks(), cal)
    assert ev.earnings_day is False


def test_joined_evidence_carries_original_move():
    rows = [_event("e1", "AAPL", MOVE.move_date, "news")]
    ev = join_evidence(MOVE, _events_df(rows), _empty_chunks(), _empty_earnings())
    assert ev.move.ticker == MOVE.ticker
    assert ev.move.move_date == MOVE.move_date
    assert ev.move.return_pct == MOVE.return_pct


def test_default_priority_is_strictly_higher_than_named():
    """Sanity: any named event_type should outrank the default."""
    for named, pri in EVENT_PRIORITY.items():
        assert pri < _DEFAULT_PRIORITY_FLOOR, (
            f"{named} priority {pri} should be < default floor"
        )


def test_priority_ordering_matches_spec_exactly():
    """Spec: short_report > earnings_release > news > 13f_delta > index_change."""
    chain = [
        "short_report",
        "earnings_release",
        "news",
        "13f_delta",
        "index_change_effective",
    ]
    priorities = [EVENT_PRIORITY[t] for t in chain]
    assert priorities == sorted(priorities)
    assert len(set(priorities)) >= 4  # at minimum 4 distinct levels


def test_proximity_metric_uses_absolute_days():
    """An event 5 days BEFORE move_date ties proximity with one 5 days AFTER
    (if both were in-window, they'd tie on proximity and priority orders them).
    Here we only need to confirm both distances are computed symmetrically."""
    rows = [
        _event("before", "AAPL", date(2024, 1, 29), "news"),  # 4 bdays before Friday 2/2
        _event("after", "AAPL", date(2024, 2, 5), "news"),    # 1 bday after
        _event("on", "AAPL", MOVE.move_date, "news"),
    ]
    ev = join_evidence(MOVE, _events_df(rows), _empty_chunks(), _empty_earnings())
    # "on" is closest; "after" is 3 calendar days away; "before" is 4.
    order = [e.event_id for e in ev.events]
    assert order[0] == "on"


# ---------- sentinel: constants are sensible ----------

def test_max_events_per_move_is_30():
    assert MAX_EVENTS_PER_MOVE == 30


# Capture the default priority constant for the assertion above.
from ingestion.events.join import _DEFAULT_PRIORITY as _DEFAULT_PRIORITY_FLOOR  # noqa: E402


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
