"""
SEC filings ingestion.

Owner: Sophia (branch: person1-sec). Implemented by Thomas.

Public API:
    - fetch_filings(tickers, start_date, end_date, filing_types) -> list[TextChunk]
    - get_filings_as_of(ticker, as_of) -> list[TextChunk]
    - make_chunk_id(...) -> str  (stable evidence citation ID)

Dispatches to the submodules:
    - tenk.fetch_10ks / tenk.get_10ks_as_of  (10-K structural profiles)
    - eightk.fetch_8ks / eightk.get_8ks_as_of  (8-K material-event disclosures)

Cache aggressively to .cache/ so re-runs don't re-hit HuggingFace / SEC EDGAR.

CRITICAL: get_filings_as_of enforces the no-foreknowledge rule. Every chunk
returned has publication_date <= as_of.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from schema import TextChunk, SourceType

CACHE_DIR = Path(__file__).parent / ".cache"

# HF-backed filings pipeline (see filings.py). Writes data/sec/events_* +
# chunks_* which the aggregator picks up via ingestion/events/adapters/sec.py.
from ingestion.sec.filings import run_sec_pipeline  # noqa: E402,F401


def make_chunk_id(
    source_type: SourceType,
    ticker: str,
    publication_date: date,
    section: str,
    index: int,
) -> str:
    """Stable, deterministic chunk ID used as evidence citation."""
    return f"{source_type.value}_{ticker}_{publication_date.isoformat()}_{section}_{index:03d}"


def fetch_filings(
    tickers: list[str],
    start_date: date,
    end_date: date,
    filing_types: Optional[list[SourceType]] = None,
) -> list[TextChunk]:
    """Download and chunk SEC filings for the given tickers in the date range.

    Dispatches to tenk and/or eightk based on filing_types. Default pulls
    both 10-Ks and 8-Ks (the two supported sources).
    """
    if filing_types is None:
        filing_types = [SourceType.SEC_10K, SourceType.SEC_8K]

    chunks: list[TextChunk] = []
    if SourceType.SEC_10K in filing_types:
        from .tenk import fetch_10ks
        chunks.extend(fetch_10ks(tickers, start_date, end_date))
    if SourceType.SEC_8K in filing_types:
        from .eightk import fetch_8ks
        chunks.extend(fetch_8ks(tickers, start_date, end_date))
    return chunks


def get_filings_as_of(ticker: str, as_of: date) -> list[TextChunk]:
    """Return all SEC chunks for `ticker` whose publication_date <= as_of.

    Foreknowledge firewall: this is THE function model/ and backtest/ call
    to avoid leaking future information. Aggregates 10-K and 8-K chunks.
    """
    from .tenk import get_10ks_as_of
    from .eightk import get_8ks_as_of
    return get_10ks_as_of(ticker, as_of) + get_8ks_as_of(ticker, as_of)
