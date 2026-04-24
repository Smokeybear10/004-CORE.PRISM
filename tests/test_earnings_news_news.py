"""HF-backed news pipeline: mock pyarrow/HF read, assert parquet + adapter output."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest


def _fake_news_table(rows: list[dict]) -> pa.Table:
    """Build a pyarrow Table with the stock_news.parquet column shape."""
    return pa.Table.from_pylist(
        rows,
        schema=pa.schema(
            [
                ("uuid", pa.string()),
                ("related_symbols", pa.string()),
                ("title", pa.string()),
                ("publisher", pa.string()),
                ("report_date", pa.string()),
                ("type", pa.string()),
                ("link", pa.string()),
                (
                    "news",
                    pa.list_(
                        pa.struct(
                            [
                                ("paragraph_number", pa.int32()),
                                ("highlight", pa.string()),
                                ("paragraph", pa.string()),
                            ]
                        )
                    ),
                ),
            ]
        ),
    )


_ROWS = [
    # AMD-tagged article with two paragraphs, within as_of window.
    {
        "uuid": "uuid-amd-1",
        "related_symbols": "AMD,NVDA",
        "title": "AMD beats estimates",
        "publisher": "Reuters",
        "report_date": "2026-04-10",
        "type": "STORY",
        "link": "https://example.com/amd1",
        "news": [
            {"paragraph_number": 0, "highlight": None, "paragraph": "AMD posted blowout Q1 earnings on data center strength."},
            {"paragraph_number": 1, "highlight": None, "paragraph": "Lisa Su raised full-year guidance."},
        ],
    },
    # Non-AMD article — pandas should filter this out since `related_symbols`
    # doesn't contain AMD exactly.
    {
        "uuid": "uuid-nvda-1",
        "related_symbols": "NVDA",
        "title": "NVDA rally continues",
        "publisher": "Bloomberg",
        "report_date": "2026-04-11",
        "type": "STORY",
        "link": "https://example.com/nvda1",
        "news": [
            {"paragraph_number": 0, "highlight": None, "paragraph": "NVDA chips sold out."},
        ],
    },
]


@pytest.fixture
def _fake_pq(monkeypatch):
    """Stand-in for pyarrow.parquet.read_table inside the news module. Honors
    the `report_date <= X` predicate so the as_of-filter test bites."""
    import ingestion.earnings_news.news as news_mod

    def _fake_read(filters=None):
        rows = list(_ROWS)
        if filters:
            for col, op, val in filters:
                if col == "report_date" and op == "<=":
                    rows = [r for r in rows if r["report_date"] <= val]
        return _fake_news_table(rows)

    monkeypatch.setattr(news_mod, "_read_news_table", _fake_read)
    return news_mod


def test_run_news_pipeline_writes_three_files_and_filters_to_ticker(_fake_pq, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    events, chunks = _fake_pq.run_news_pipeline("AMD", date(2026, 4, 15))

    out_dir = tmp_path / "data" / "news"
    assert (out_dir / "news_AMD_2026-04-15.parquet").exists()
    assert (out_dir / "events_AMD_2026-04-15.parquet").exists()
    assert (out_dir / "chunks_AMD_2026-04-15.jsonl").exists()

    # Only the AMD-tagged article makes it through.
    raw = pd.read_parquet(out_dir / "news_AMD_2026-04-15.parquet")
    assert len(raw) == 1
    assert raw.iloc[0]["uuid"] == "uuid-amd-1"

    # Adapter fans to (article, ticker-in-related-symbols, paragraph).
    # The raw row is filtered to AMD-tagged articles only, but the ADAPTER
    # still sees "AMD,NVDA" in related_symbols and emits one (Event, Chunk)
    # per (paragraph, related_ticker). 2 paragraphs x 2 related tickers = 4.
    assert len(events) == 4
    assert {c.ticker for c in chunks} == {"AMD", "NVDA"}
    # Per-ticker chunks are unique (ticker + chunk_id is the real key).
    amd_chunks = [c for c in chunks if c.ticker == "AMD"]
    amd_ids = [c.chunk_id for c in amd_chunks]
    assert sorted(amd_ids) == ["news_uuid-amd-1_p0", "news_uuid-amd-1_p1"]
    assert len(amd_ids) == len(set(amd_ids))


def test_run_news_pipeline_respects_as_of(_fake_pq, tmp_path, monkeypatch):
    """Article dated 2026-04-11 is filtered out when as_of=2026-04-05."""
    monkeypatch.chdir(tmp_path)
    events, chunks = _fake_pq.run_news_pipeline("AMD", date(2026, 4, 5))
    # No AMD article <= 2026-04-05 in our fake data, so everything is empty.
    assert events == [] and chunks == []
    raw = pd.read_parquet(tmp_path / "data" / "news" / "news_AMD_2026-04-05.parquet")
    assert raw.empty


def test_run_news_pipeline_is_idempotent(_fake_pq, tmp_path, monkeypatch):
    """Running twice produces the same chunk_id set — no duplicates, no drift."""
    monkeypatch.chdir(tmp_path)
    _, chunks_first = _fake_pq.run_news_pipeline("AMD", date(2026, 4, 15))
    _, chunks_second = _fake_pq.run_news_pipeline("AMD", date(2026, 4, 15))
    ids_first = sorted(c.chunk_id for c in chunks_first)
    ids_second = sorted(c.chunk_id for c in chunks_second)
    assert ids_first == ids_second
