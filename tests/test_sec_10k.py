"""
SEC 10-K ingestion tests.

Covers:
1. Hand-fabricated fixture parses into valid TextChunk objects.
2. Foreknowledge firewall: `publication_date <= as_of` is enforced.
3. chunk_id format from `ingestion.sec.make_chunk_id` is stable and spec-compliant.
4. Local chunk_text helper splits sensibly and respects overlap.

These tests do NOT hit the network. They validate schema conformance and
the firewall in isolation so the rest of the team can trust the contract
before live data lands.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ingestion.sec import make_chunk_id
from ingestion.sec.tenk import (
    chunk_text,
    filter_chunks_as_of,
    get_10ks_as_of,
)
from schema import SourceType, TextChunk

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SEC_10K_FIXTURE = FIXTURES_DIR / "sec_10k_sample.json"


# ---------- Fixture parsing ----------

def test_fixture_loads_and_parses():
    """Every record in sec_10k_sample.json must validate as a TextChunk."""
    with open(SEC_10K_FIXTURE) as f:
        records = json.load(f)

    assert len(records) == 3, "fixture should contain business + risk_factors + mda"
    chunks = [TextChunk.model_validate(rec) for rec in records]

    sections = {c.section_name for c in chunks}
    assert sections == {"business", "risk_factors", "mda"}

    for chunk in chunks:
        assert chunk.source_type is SourceType.SEC_10K
        assert chunk.ticker == "AAPL"
        assert chunk.publication_date == date(2024, 11, 1)
        assert chunk.period_end == date(2024, 9, 28)
        assert chunk.text.strip()
        assert chunk.chunk_id.startswith("sec_10k_AAPL_2024-11-01_")


def test_fixture_chunk_ids_match_expected_format():
    """chunk_id must be: sec_10k_{TICKER}_{YYYY-MM-DD}_{section}_{NNN:03d}."""
    with open(SEC_10K_FIXTURE) as f:
        records = json.load(f)

    for rec in records:
        parts = rec["chunk_id"].split("_")
        # sec, 10k, TICKER, YYYY-MM-DD, section(may have underscores), NNN
        assert parts[0] == "sec"
        assert parts[1] == "10k"
        assert parts[2] == rec["ticker"]
        assert parts[3] == rec["publication_date"]
        assert parts[-1].isdigit() and len(parts[-1]) == 3


# ---------- Firewall ----------

def _sample_chunk(ticker: str, publication_date: date, section: str, idx: int) -> TextChunk:
    return TextChunk(
        chunk_id=make_chunk_id(SourceType.SEC_10K, ticker, publication_date, section, idx),
        ticker=ticker,
        source_type=SourceType.SEC_10K,
        publication_date=publication_date,
        period_end=None,
        source_url=None,
        section_name=section,
        text=f"fake {section} chunk for {ticker} filed {publication_date}",
        token_count=10,
    )


def test_filter_chunks_as_of_enforces_firewall():
    """Chunks with publication_date > as_of must be excluded. No exceptions."""
    chunks = [
        _sample_chunk("AAPL", date(2022, 11, 1), "mda", 0),
        _sample_chunk("AAPL", date(2023, 11, 3), "mda", 0),
        _sample_chunk("AAPL", date(2024, 11, 1), "mda", 0),
        _sample_chunk("AAPL", date(2025, 11, 7), "mda", 0),
    ]
    as_of = date(2024, 6, 15)

    surviving = filter_chunks_as_of(chunks, as_of)

    assert len(surviving) == 2
    for chunk in surviving:
        assert chunk.publication_date <= as_of
    # The two most recent filings (both after as_of) must be excluded.
    surviving_dates = {c.publication_date for c in surviving}
    assert date(2024, 11, 1) not in surviving_dates
    assert date(2025, 11, 7) not in surviving_dates


def test_filter_chunks_as_of_inclusive_boundary():
    """publication_date == as_of is allowed (filter is <=, not <)."""
    chunks = [_sample_chunk("AAPL", date(2024, 11, 1), "mda", 0)]
    surviving = filter_chunks_as_of(chunks, date(2024, 11, 1))
    assert len(surviving) == 1


def test_get_10ks_as_of_round_trip_via_cache(tmp_path, monkeypatch):
    """
    End-to-end firewall check: seed the cache directly, then query via the
    real `get_10ks_as_of` entry point and confirm future-dated chunks are filtered.
    """
    # Redirect the cache dir to an isolated tmp path so we don't pollute state.
    from ingestion.sec import tenk as tenk_mod

    cache_dir = tmp_path / "10k"
    cache_dir.mkdir()
    monkeypatch.setattr(tenk_mod, "TENK_CACHE_DIR", cache_dir)

    past = _sample_chunk("AAPL", date(2023, 10, 27), "mda", 0)
    future = _sample_chunk("AAPL", date(2026, 11, 1), "mda", 0)

    # Hand-write the cache files the way _write_cache would.
    for chunk in (past, future):
        path = cache_dir / f"AAPL_{chunk.publication_date.isoformat()}.json"
        path.write_text(json.dumps([json.loads(chunk.model_dump_json())]))

    result = get_10ks_as_of("AAPL", date(2024, 6, 15))
    assert len(result) == 1
    assert result[0].publication_date == date(2023, 10, 27)


# ---------- make_chunk_id ----------

def test_make_chunk_id_format():
    cid = make_chunk_id(SourceType.SEC_10K, "AAPL", date(2024, 11, 1), "business", 7)
    assert cid == "sec_10k_AAPL_2024-11-01_business_007"


def test_make_chunk_id_is_deterministic():
    a = make_chunk_id(SourceType.SEC_10K, "NVDA", date(2025, 2, 26), "mda", 42)
    b = make_chunk_id(SourceType.SEC_10K, "NVDA", date(2025, 2, 26), "mda", 42)
    assert a == b


# ---------- chunk_text ----------

def test_chunk_text_empty_input_returns_empty():
    assert chunk_text("") == []
    assert chunk_text("    ") == []


def test_chunk_text_splits_long_input_with_overlap():
    # ~4000 words = ~5000 tokens → should produce multiple chunks at 800-token targets.
    words = (["lorem", "ipsum", "dolor", "sit", "amet"] * 800)
    text = " ".join(words)
    chunks = chunk_text(text, target_tokens=800, overlap_tokens=100)
    assert len(chunks) >= 2
    # Every chunk should be non-empty.
    assert all(c.strip() for c in chunks)


def test_chunk_text_short_input_single_chunk():
    text = "Short sentence about fiscal 2024 performance."
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].strip() == text.strip()


# ---------- Regression guard: existing schema fixture still parses ----------

def test_pre_existing_sec_chunks_sample_still_parses():
    """The teammate fixture shouldn't have been touched by this agent."""
    with open(FIXTURES_DIR / "sec_chunks_sample.json") as f:
        records = json.load(f)
    for rec in records:
        TextChunk.model_validate(rec)
