"""Tests for the news ingestion module (ingestion/earnings_news)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from schema import SourceType, TextChunk

FIXTURE = Path(__file__).parent / "fixtures" / "news_sample.json"


def _load_fixture() -> list[TextChunk]:
    raw = json.loads(FIXTURE.read_text())
    return [TextChunk.model_validate(row) for row in raw]


def test_fixture_loads_as_textchunks():
    chunks = _load_fixture()
    assert len(chunks) == 3
    assert all(isinstance(c, TextChunk) for c in chunks)


def test_fixture_has_news_and_peer_news_types():
    chunks = _load_fixture()
    types = {c.source_type for c in chunks}
    assert SourceType.NEWS in types
    assert SourceType.PEER_NEWS in types


def test_fixture_chunk_ids_well_formed():
    chunks = _load_fixture()
    for c in chunks:
        parts = c.chunk_id.split("_")
        # source-type prefix can be 1 or 2 tokens ("news" or "peer_news")
        assert c.ticker in c.chunk_id
        assert c.publication_date.isoformat() in c.chunk_id
        assert parts[-1].isdigit(), f"chunk index suffix not numeric: {c.chunk_id}"


def test_as_of_filter_inclusive_boundary():
    chunks = _load_fixture()
    cutoff = date(2024, 8, 15)
    filtered = [c for c in chunks if c.publication_date <= cutoff]
    assert len(filtered) == 1
    assert filtered[0].publication_date == cutoff


def test_as_of_filter_drops_future_chunks():
    chunks = _load_fixture()
    cutoff = date(2024, 7, 1)
    filtered = [c for c in chunks if c.publication_date <= cutoff]
    assert filtered == []


def test_as_of_filter_keeps_all_if_future_cutoff():
    chunks = _load_fixture()
    cutoff = date(2025, 1, 1)
    filtered = [c for c in chunks if c.publication_date <= cutoff]
    assert len(filtered) == 3


def test_malformed_textchunk_rejected():
    bad = {
        "chunk_id": "news_AAPL_bad",
        "ticker": "AAPL",
        "source_type": "news",
        # missing publication_date
        "text": "foo",
    }
    with pytest.raises(ValidationError):
        TextChunk.model_validate(bad)
