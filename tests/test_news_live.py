"""
Live-parquet smoke tests for ingestion.earnings_news.

Converts "Schrodinger's news ingestion" into observable behavior: if anyone
has the cached ``stock_news.parquet`` (658 MB), these tests prove fetch_news
actually returns chunks and that the ticker/date matcher works. If the cache
is missing, every test skips cleanly — no network, no huggingface download,
CI-safe.

The existing ``test_news.py`` only validates schema parsing on a 3-row JSON
fixture. That catches Pydantic drift but nothing about whether the live pull
works. These tests cover the gap.

Known data coverage (as of 2026-04): the bundled parquet has news for
2025-03-11 through 2026-04-04. Events outside that window legitimately
return 0 chunks — not a bug.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from schema import SourceType
from ingestion.earnings_news import CACHE_DIR, fetch_news, get_news_as_of


PARQUET_PATH = CACHE_DIR / "stock_news.parquet"

# Window that is known to have coverage in the bundled parquet.
IN_RANGE_START = date(2025, 9, 6)
IN_RANGE_END = date(2025, 10, 7)

# Window that predates the bundled parquet's earliest row (2025-03-11).
OUT_OF_RANGE_START = date(2022, 9, 7)
OUT_OF_RANGE_END = date(2022, 10, 8)


skip_without_parquet = pytest.mark.skipif(
    not PARQUET_PATH.exists() or PARQUET_PATH.stat().st_size == 0,
    reason=(
        f"live smoke requires {PARQUET_PATH.name} cached at {CACHE_DIR}. "
        "Run once: python -c 'from ingestion.earnings_news import _ensure_parquet; _ensure_parquet()'"
    ),
)


@skip_without_parquet
def test_fetch_news_in_range_returns_chunks():
    chunks = fetch_news(["AMD"], start_date=IN_RANGE_START, end_date=IN_RANGE_END)
    assert len(chunks) > 0, (
        "fetch_news returned 0 chunks for AMD in an in-range window — "
        "this is the exact failure mode we want to catch."
    )
    for c in chunks:
        assert c.ticker == "AMD"
        assert c.source_type == SourceType.NEWS
        assert IN_RANGE_START <= c.publication_date <= IN_RANGE_END
        assert c.text.strip()
        assert c.token_count is None or c.token_count > 0


@skip_without_parquet
def test_fetch_news_out_of_range_returns_empty():
    # Coverage starts 2025-03-11; a 2022 window must return 0 chunks.
    # This is NOT a bug — it documents the data limitation so future 0-chunk
    # results are recognized as "picked wrong window" rather than "matcher broken."
    chunks = fetch_news(
        ["AMD"],
        start_date=OUT_OF_RANGE_START,
        end_date=OUT_OF_RANGE_END,
    )
    assert chunks == []


@skip_without_parquet
def test_fetch_news_multi_ticker_separates_correctly():
    chunks = fetch_news(
        ["AMD", "NVDA", "INTC"],
        start_date=date(2025, 10, 1),
        end_date=date(2025, 10, 7),
    )
    by_ticker = {t: 0 for t in ["AMD", "NVDA", "INTC"]}
    for c in chunks:
        assert c.ticker in by_ticker, f"unexpected ticker: {c.ticker}"
        by_ticker[c.ticker] += 1
    # At least one chunk per ticker in a week-long window for 3 mega-caps.
    for t, n in by_ticker.items():
        assert n > 0, f"{t} returned 0 chunks in a week-long mega-cap window"


@skip_without_parquet
def test_fetch_news_peer_source_type_tag():
    # When pulled as peer news, source_type must flip — this is what keeps
    # +peer_news and base_news ablations distinguishable.
    chunks = fetch_news(
        ["NVDA"],
        start_date=IN_RANGE_START,
        end_date=IN_RANGE_END,
        source_type=SourceType.PEER_NEWS,
    )
    assert len(chunks) > 0
    for c in chunks:
        assert c.source_type == SourceType.PEER_NEWS
        # chunk_id should encode the new source type
        assert c.chunk_id.startswith("peer_news_"), f"unexpected chunk_id: {c.chunk_id}"


@skip_without_parquet
def test_get_news_as_of_respects_foreknowledge_firewall():
    as_of = date(2025, 10, 6)
    chunks = get_news_as_of("AMD", as_of=as_of)
    assert len(chunks) > 0
    assert all(c.publication_date <= as_of for c in chunks), (
        "foreknowledge firewall violated: get_news_as_of returned a chunk "
        "published after the as_of date"
    )


@skip_without_parquet
def test_chunk_id_format_is_stable():
    chunks = fetch_news(["AMD"], start_date=IN_RANGE_START, end_date=IN_RANGE_END)
    assert chunks, "need at least one chunk to assert chunk_id format"
    # Pattern: {source_type.value}_{ticker}_{YYYY-MM-DD}_article_{NNN}
    # (CLAUDE.md rule #3: stable chunk IDs. Citations rely on this shape.)
    for c in chunks[:25]:
        parts = c.chunk_id.split("_")
        assert parts[0] == "news"
        assert c.ticker in c.chunk_id
        assert c.publication_date.isoformat() in c.chunk_id
        assert parts[-2] == "article"
        assert parts[-1].isdigit() and len(parts[-1]) == 3
