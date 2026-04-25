"""
Tests for the peer-news pipeline added in ingestion.earnings_news.

Covers:
  - get_peers() returns the curated list per focal ticker, [] for unknowns
  - get_peer_news_as_of() resolves peers and tags chunks as PEER_NEWS
  - foreknowledge firewall (publication_date <= as_of)
  - explicit `peers=` override path
  - empty peer list short-circuits without touching the parquet

Doesn't touch HuggingFace. The parquet is replaced with a tiny pandas-built
fixture by monkeypatching `_ensure_parquet` to point at it.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

import ingestion.earnings_news as en
from ingestion.earnings_news import (
    _PEER_MAP,
    get_peer_news_as_of,
    get_peers,
)
from schema import SourceType


# ---------- Peer resolver ----------

def test_focal_universe_fully_mapped():
    """Every focal ticker the project ships with must have a peer list."""
    for ticker in ("ABT", "ACU", "AIR", "AMD", "APD"):
        peers = get_peers(ticker)
        assert peers, f"{ticker} has no peers configured"
        assert len(peers) >= 3, f"{ticker} peer list is suspiciously thin"
        assert ticker not in peers, f"{ticker} listed as its own peer"


def test_get_peers_lowercase_input():
    """Caller-friendly: lowercase ticker still resolves."""
    assert get_peers("amd") == _PEER_MAP["AMD"]


def test_get_peers_unknown_ticker_returns_empty():
    assert get_peers("ZZZZ_NONEXISTENT") == []


def test_get_peers_returns_copy_not_reference():
    """Mutating the returned list must not corrupt the static map."""
    out = get_peers("AMD")
    out.append("FAKE")
    assert "FAKE" not in get_peers("AMD")


# ---------- get_peer_news_as_of with a tiny fake parquet ----------

@pytest.fixture
def _fake_news_parquet(tmp_path, monkeypatch):
    """Write a 4-row stand-in for stock_news.parquet and redirect _ensure_parquet.

    Two rows match AMD's peers (NVDA + INTC), one is unrelated (TSLA), and
    one is a future-dated NVDA article that the foreknowledge filter must drop.
    """
    rows = [
        {
            "uuid": "uuid-nvda-1",
            "related_symbols": "NVDA",
            "title": "Nvidia hits new high",
            "publisher": "Reuters",
            "report_date": "2025-09-01",
            "type": "STORY",
            "link": "https://example.com/nvda-1",
            "news": [{"paragraph_number": 0, "highlight": None,
                      "paragraph": "NVDA reported record data-center revenue."}],
        },
        {
            "uuid": "uuid-intc-1",
            "related_symbols": "INTC",
            "title": "Intel restructure",
            "publisher": "Bloomberg",
            "report_date": "2025-09-15",
            "type": "STORY",
            "link": "https://example.com/intc-1",
            "news": [{"paragraph_number": 0, "highlight": None,
                      "paragraph": "Intel announced foundry separation."}],
        },
        {
            "uuid": "uuid-tsla-1",
            "related_symbols": "TSLA",
            "title": "Tesla deliveries",
            "publisher": "CNBC",
            "report_date": "2025-09-20",
            "type": "STORY",
            "link": "https://example.com/tsla-1",
            "news": [{"paragraph_number": 0, "highlight": None,
                      "paragraph": "TSLA Q3 deliveries beat consensus."}],
        },
        # Future-dated NVDA article — must be excluded by as_of filter
        {
            "uuid": "uuid-nvda-future",
            "related_symbols": "NVDA",
            "title": "Future Nvidia event",
            "publisher": "Reuters",
            "report_date": "2026-12-01",
            "type": "STORY",
            "link": "https://example.com/nvda-future",
            "news": [{"paragraph_number": 0, "highlight": None,
                      "paragraph": "NVDA hosted a developer day."}],
        },
    ]
    parquet_path = tmp_path / "fake_stock_news.parquet"
    pd.DataFrame(rows).to_parquet(parquet_path)
    monkeypatch.setattr(en, "_ensure_parquet", lambda: parquet_path)
    return parquet_path


def test_resolves_peers_and_tags_as_peer_news(_fake_news_parquet):
    chunks = get_peer_news_as_of("AMD", date(2025, 10, 1))
    assert chunks, "expected NVDA + INTC peer chunks for AMD"
    for c in chunks:
        assert c.source_type == SourceType.PEER_NEWS
    by_ticker = {c.ticker for c in chunks}
    # NVDA + INTC (peers); TSLA is unrelated and must NOT appear
    assert by_ticker == {"NVDA", "INTC"}
    assert "TSLA" not in by_ticker


def test_chunk_id_uses_peer_news_prefix(_fake_news_parquet):
    chunks = get_peer_news_as_of("AMD", date(2025, 10, 1))
    assert chunks
    for c in chunks:
        assert c.chunk_id.startswith("peer_news_")


def test_foreknowledge_filter_drops_future_articles(_fake_news_parquet):
    # The future-dated NVDA article (2026-12-01) must be excluded when
    # as_of is before that date.
    chunks = get_peer_news_as_of("AMD", date(2025, 10, 1))
    for c in chunks:
        assert c.publication_date <= date(2025, 10, 1)
    assert all("nvda-future" not in c.chunk_id.lower() for c in chunks)


def test_explicit_peers_override(_fake_news_parquet):
    """Caller can override the static map for one-off explorations."""
    chunks = get_peer_news_as_of(
        "AMD", date(2025, 10, 1),
        peers=["TSLA"],
    )
    # Now TSLA shows up because we asked for it; NVDA/INTC don't.
    by_ticker = {c.ticker for c in chunks}
    assert by_ticker == {"TSLA"}


def test_unknown_ticker_returns_empty_without_touching_parquet(monkeypatch):
    """A ticker with no peer mapping must short-circuit before any parquet I/O."""
    sentinel = {"called": False}

    def _boom():
        sentinel["called"] = True
        raise AssertionError("must not be called when peer list is empty")

    monkeypatch.setattr(en, "_ensure_parquet", _boom)
    chunks = get_peer_news_as_of("ZZZZ_NONEXISTENT", date(2025, 10, 1))
    assert chunks == []
    assert sentinel["called"] is False


def test_explicit_empty_peers_returns_empty(monkeypatch):
    """Passing an empty peers= list also short-circuits."""

    def _boom():
        raise AssertionError("empty peers must not trigger parquet I/O")

    monkeypatch.setattr(en, "_ensure_parquet", _boom)
    assert get_peer_news_as_of("AMD", date(2025, 10, 1), peers=[]) == []
