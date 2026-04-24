"""
Tests for SEC 8-K ingestion (ingestion/sec/eightk.py).

Runs against the hand-fabricated fixture tests/fixtures/sec_8k_sample.json
so we don't depend on live EDGAR. The live fetch path is exercised via
smoke tests outside CI.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from ingestion.sec.eightk import (
    RELEVANT_ITEM_CODES,
    _extract_item_codes,
    _item_codes_id_fragment,
    _item_codes_section_name,
    filter_chunks_as_of,
)
from schema import SourceType, TextChunk

FIXTURE = Path(__file__).parent / "fixtures" / "sec_8k_sample.json"

# chunk_id format: sec_8k_{TICKER}_{YYYY-MM-DD}_item{ITEMCODES}_{NNN}
_CHUNK_ID_RE = re.compile(
    r"^sec_8k_(?P<ticker>[A-Z0-9.\-]+)_(?P<date>\d{4}-\d{2}-\d{2})"
    r"_item(?P<codes>[0-9+]+)_(?P<idx>\d{3})$"
)


def _load_fixture() -> list[dict]:
    with FIXTURE.open() as f:
        return json.load(f)


# ---------- 1) Fixture parses into valid TextChunks ----------

def test_fixture_rows_parse_as_textchunks():
    raw = _load_fixture()
    assert len(raw) == 3, "fixture should cover one chunk per item type (2.02, 5.02, 7.01)"
    chunks = [TextChunk(**row) for row in raw]
    for c in chunks:
        assert c.chunk_id.startswith("sec_8k_")
        assert c.source_type == SourceType.SEC_8K
        assert c.ticker == "AAPL"
        assert c.publication_date
        assert c.text and len(c.text) > 50, "chunks must carry meaningful text"
        assert c.section_name, "section_name should hold the comma-joined item codes"


def test_fixture_section_names_match_item_code_format():
    """section_name is a comma+space joined list of item codes ('2.02, 9.01')."""
    for row in _load_fixture():
        section = row["section_name"]
        for code in section.split(","):
            code = code.strip()
            assert re.match(r"^\d+\.\d{2}$", code), (
                f"section token {code!r} should look like '2.02'"
            )


def test_fixture_spans_distinct_dates():
    """The three fixture rows should have three distinct publication_dates."""
    rows = _load_fixture()
    dates = {r["publication_date"] for r in rows}
    assert len(dates) == 3, "fixture dates should all differ"


# ---------- 2) As-of filter (foreknowledge firewall) ----------

def test_as_of_filter_drops_future_chunks():
    raw = _load_fixture()
    chunks = [TextChunk(**r) for r in raw]

    # Sort so we can talk about earliest/middle/latest precisely.
    chunks_sorted = sorted(chunks, key=lambda c: c.publication_date)
    earliest, middle, latest = chunks_sorted

    # as_of equal to middle -> include earliest + middle, drop latest.
    kept = filter_chunks_as_of(chunks, middle.publication_date)
    kept_ids = {c.chunk_id for c in kept}
    assert earliest.chunk_id in kept_ids
    assert middle.chunk_id in kept_ids
    assert latest.chunk_id not in kept_ids

    # as_of strictly before everything -> empty.
    before_all = date(1990, 1, 1)
    assert filter_chunks_as_of(chunks, before_all) == []

    # as_of after latest -> all survive.
    after_all = date(2099, 12, 31)
    assert len(filter_chunks_as_of(chunks, after_all)) == len(chunks)


def test_as_of_filter_is_inclusive_on_boundary():
    """publication_date == as_of must be kept, not dropped."""
    raw = _load_fixture()
    chunks = [TextChunk(**r) for r in raw]
    c = chunks[0]
    kept = filter_chunks_as_of([c], c.publication_date)
    assert kept == [c], "boundary case: as_of == publication_date must be included"


def test_as_of_filter_against_mixed_list():
    """Mixed list (past + future + boundary) must yield only those <= as_of."""
    base = _load_fixture()[0]
    # Synthesize three rows sharing everything but date / chunk_id.
    def _mk(d: str, idx: int) -> TextChunk:
        row = dict(base)
        row["publication_date"] = d
        row["chunk_id"] = f"sec_8k_AAPL_{d}_item202_{idx:03d}"
        return TextChunk(**row)

    chunks = [
        _mk("2023-01-15", 1),  # past
        _mk("2024-06-01", 2),  # boundary (will be as_of)
        _mk("2025-03-10", 3),  # future
    ]
    kept = filter_chunks_as_of(chunks, date(2024, 6, 1))
    kept_dates = sorted(c.publication_date for c in kept)
    assert kept_dates == [date(2023, 1, 15), date(2024, 6, 1)]


# ---------- 3) chunk_id parsing ----------

def test_fixture_chunk_ids_follow_format():
    raw = _load_fixture()
    for row in raw:
        m = _CHUNK_ID_RE.match(row["chunk_id"])
        assert m, f"chunk_id {row['chunk_id']!r} does not match expected pattern"
        assert m["ticker"] == row["ticker"]
        assert m["date"] == row["publication_date"]
        assert int(m["idx"]) >= 1


def test_chunk_id_item_codes_embed_section_name():
    """
    Item codes in the chunk_id ("item202+901") must reconstruct to the
    section_name ("2.02, 9.01"). This is the contract the model layer relies
    on to cite back into the right 8-K item.
    """
    for row in _load_fixture():
        m = _CHUNK_ID_RE.match(row["chunk_id"])
        assert m
        raw_codes = m["codes"].split("+")
        # Reconstruct "202" -> "2.02", "901" -> "9.01"
        reconstructed = [f"{c[:-2]}.{c[-2:]}" for c in raw_codes]
        section_codes = [s.strip() for s in row["section_name"].split(",")]
        assert reconstructed == section_codes


def test_chunk_id_item_codes_are_relevant():
    """At least one item code on every fixture chunk must be in the whitelist."""
    for row in _load_fixture():
        codes = [s.strip() for s in row["section_name"].split(",")]
        assert any(c in RELEVANT_ITEM_CODES for c in codes), (
            f"fixture row {row['chunk_id']} has no relevant item codes"
        )


# ---------- 4) Helper unit tests (belt-and-suspenders) ----------

@pytest.mark.parametrize(
    "raw,expected",
    [
        (["2.02", "9.01"], ["2.02", "9.01"]),
        ("Item 5.02", ["5.02"]),
        ("Item 2.02, Item 9.01", ["2.02", "9.01"]),
        ("item7.01;item8.01", ["7.01", "8.01"]),
        (None, []),
        ([], []),
        (["2.02", "2.02", "9.01"], ["2.02", "9.01"]),  # dedupe preserves order
    ],
)
def test_extract_item_codes(raw, expected):
    assert _extract_item_codes(raw) == expected


def test_item_codes_id_fragment_roundtrip():
    codes = ["2.02", "9.01"]
    assert _item_codes_id_fragment(codes) == "item202+901"
    assert _item_codes_section_name(codes) == "2.02, 9.01"


def test_item_codes_id_fragment_empty():
    assert _item_codes_id_fragment([]) == "itemNONE"
    assert _item_codes_section_name([]) == "unknown"
