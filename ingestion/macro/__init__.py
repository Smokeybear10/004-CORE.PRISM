"""
Step 4: Macro / market-wide drivers.

Owner: Henry (branch: person3-research) - paired with 13F / open research.

Public API:
    - fetch_macro_events(start_date, end_date) -> list[TextChunk]
    - get_macro_as_of(as_of) -> list[TextChunk]

Each macro event emits a TextChunk with:
    source_type      = SourceType.MACRO
    ticker           = "_MACRO"            (placeholder; not a real ticker)
    publication_date = the event date (when the market reacted)
    text             = one paragraph summary

Why this module matters (mentor quote):
    "A move on a given day is never purely explained by one news article.
     If an energy company moves on day X, maybe it's not the news article from
     that day - maybe the Suez Canal closed."

MVP sources (free, easy):
    - FOMC calendar + rate-decision headlines
    - VIX spikes (|change| > 20%)
    - Major commodity moves (crude / gold, |1d return| > 3%)
    - Major geopolitical / disaster events (curated list is fine for MVP)

NON-MVP: Bloomberg terminal, paid macro data vendors. Skip.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from schema import TextChunk

CACHE_DIR = Path(__file__).parent / ".cache"


def fetch_macro_events(start_date: date, end_date: date) -> list[TextChunk]:
    """
    Pull macro events in [start_date, end_date]. Cache to .cache/macro.json.

    TODO: FOMC calendar (publicly available), VIX from yfinance (^VIX),
    curated geopolitical events list (hand-maintain for MVP - 20 entries
    covering the last 5 years is plenty).
    """
    raise NotImplementedError("fetch_macro_events - implement me")


def get_macro_as_of(as_of: date) -> list[TextChunk]:
    """
    All macro chunks where publication_date <= as_of. No-foreknowledge filter.
    """
    raise NotImplementedError("get_macro_as_of - implement me")
