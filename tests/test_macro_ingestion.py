"""
Tests for ingestion.macro.

Pure-data — no parquet, no network. Asserts:
  - public API returns the right shape
  - foreknowledge filter (publication_date <= as_of) is honored
  - chunk_id format matches CLAUDE.md's stable-id contract
  - cache round-trips
"""
from __future__ import annotations

import json
from datetime import date

import pytest

import ingestion.macro as macro_mod
from ingestion.macro import (
    MACRO_TICKER_PLACEHOLDER,
    fetch_macro_events,
    get_macro_as_of,
)
from schema import SourceType


# ---------- Public API: shape ----------

def test_get_macro_as_of_returns_chunks_with_macro_source_type():
    chunks = get_macro_as_of(date(2025, 12, 31))
    assert chunks, "the curated set must produce at least one chunk by end-of-2025"
    for c in chunks:
        assert c.source_type == SourceType.MACRO
        assert c.ticker == MACRO_TICKER_PLACEHOLDER


def test_chunks_are_sorted_by_publication_date():
    chunks = get_macro_as_of(date(2025, 12, 31))
    dates = [c.publication_date for c in chunks]
    assert dates == sorted(dates)


def test_chunk_ids_match_stable_format():
    """Format per CLAUDE.md rule #3: {source_type}_{ticker}_{YYYY-MM-DD}_{section}_{NNN}"""
    chunks = get_macro_as_of(date(2025, 12, 31))
    for c in chunks:
        parts = c.chunk_id.split("_")
        # macro_<MACRO>_YYYY-MM-DD_<section>_<NNN>
        # Note: ticker is "_MACRO" — leading underscore eats one of our split tokens.
        assert c.chunk_id.startswith("macro_"), c.chunk_id
        assert MACRO_TICKER_PLACEHOLDER in c.chunk_id
        # Trailing 3-digit sequence
        assert parts[-1].isdigit() and len(parts[-1]) == 3


def test_section_names_are_known_set():
    chunks = get_macro_as_of(date(2025, 12, 31))
    sections = {c.section_name for c in chunks}
    assert sections.issubset({"fomc", "geopolitical", "market_structure", "health"})


# ---------- Foreknowledge firewall ----------

def test_get_macro_as_of_excludes_future_events():
    cutoff = date(2022, 1, 1)
    chunks = get_macro_as_of(cutoff)
    for c in chunks:
        assert c.publication_date <= cutoff, (
            f"future leak: {c.chunk_id} dated {c.publication_date}"
        )


def test_fetch_macro_events_excludes_outside_window():
    chunks = fetch_macro_events(date(2022, 1, 1), date(2022, 12, 31))
    assert chunks, "2022 had multiple FOMC meetings + Russia/Ukraine"
    for c in chunks:
        assert date(2022, 1, 1) <= c.publication_date <= date(2022, 12, 31)


def test_fetch_macro_events_inverted_window_returns_empty():
    assert fetch_macro_events(date(2024, 1, 1), date(2023, 1, 1)) == []


# ---------- Specific events present ----------

def test_includes_2020_covid_pandemic_declaration():
    chunks = fetch_macro_events(date(2020, 3, 1), date(2020, 3, 31))
    # WHO pandemic declaration was 2020-03-11
    assert any(
        c.publication_date == date(2020, 3, 11)
        and "pandemic" in c.text.lower()
        for c in chunks
    )


def test_includes_russia_ukraine_invasion():
    chunks = fetch_macro_events(date(2022, 2, 24), date(2022, 2, 24))
    assert any("Russia" in c.text and "Ukraine" in c.text for c in chunks)


def test_includes_fomc_2022_first_hike():
    chunks = fetch_macro_events(date(2022, 3, 16), date(2022, 3, 16))
    fomc = [c for c in chunks if c.section_name == "fomc"]
    assert fomc, "March 2022 FOMC meeting should be in the curated set"
    # Title should reflect the 25bps hike
    assert any("25bps" in c.text for c in fomc)


def test_fomc_meetings_present_for_each_focal_year():
    """One sanity check per year — every 2020-2025 should have FOMC chunks."""
    for year in range(2020, 2026):
        window = fetch_macro_events(date(year, 1, 1), date(year, 12, 31))
        fomc = [c for c in window if c.section_name == "fomc"]
        assert fomc, f"no FOMC chunks for {year}"


# ---------- Cache round-trip ----------

def test_cache_round_trip(tmp_path, monkeypatch):
    """First call writes the cache; second call reads from it without rebuilding."""
    cache_dir = tmp_path / "macro_cache"
    monkeypatch.setattr(macro_mod, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(macro_mod, "CACHE_FILE", cache_dir / "macro.json")

    first = fetch_macro_events(date(2020, 1, 1), date(2025, 12, 31))
    assert (cache_dir / "macro.json").exists()
    payload = json.loads((cache_dir / "macro.json").read_text())
    assert isinstance(payload, list) and len(payload) > 0

    # Second call: stub _all_events to return [] — if cache is honored we still
    # get the cached events.
    monkeypatch.setattr(macro_mod, "_all_events", lambda: [])
    second = fetch_macro_events(date(2020, 1, 1), date(2025, 12, 31))
    assert len(second) == len(first)


def test_cache_falsy_when_corrupt(tmp_path, monkeypatch):
    cache_dir = tmp_path / "macro_cache"
    cache_dir.mkdir()
    bad = cache_dir / "macro.json"
    bad.write_text("not valid json {{")
    monkeypatch.setattr(macro_mod, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(macro_mod, "CACHE_FILE", bad)
    # Should silently rebuild from _all_events()
    chunks = fetch_macro_events(date(2020, 1, 1), date(2025, 12, 31))
    assert chunks


# ---------- Determinism ----------

def test_identical_calls_produce_identical_chunk_ids():
    a = get_macro_as_of(date(2024, 12, 31))
    b = get_macro_as_of(date(2024, 12, 31))
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
