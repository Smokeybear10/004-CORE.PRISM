"""
HF-backed news pipeline.

Reads `stock_news.parquet` from the private BW HF repo, filters to
articles tagged with `ticker` and published on/before `as_of`, and writes:

    data/news/news_<TICKER>_<as_of>.parquet    # RAW rows — consumed by aggregator
    data/news/events_<TICKER>_<as_of>.parquet  # Events (one per article, per para, per ticker)
    data/news/chunks_<TICKER>_<as_of>.jsonl    # TextChunks (human-readable mirror)

The filename `news_<TICKER>_<as_of>.parquet` matches the glob in
`ingestion/events/aggregator._run_news`, so this file flows through the
unified events table automatically. The derived events/chunks files are
convenience outputs for direct inspection; the aggregator does NOT read
them (it re-derives via the adapter so dedup and source-type bookkeeping
stay centralized).

Foreknowledge firewall (CLAUDE.md rule 1): every emitted record has
`report_date <= as_of`. Filter is pushed down via pyarrow predicate;
ticker filtering happens post-read (substring on comma-sep string, which
pyarrow can't pushdown).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
from huggingface_hub import HfFileSystem

from ingestion.events.adapters import news as news_adapter
from schema import Event, TextChunk

DATA_DIR = Path("data/news")
HF_PATH = (
    "datasets/BridgewaterAIHackathon/BW-AI-Hackathon"
    "/Structured_Data/SNE/yahoo-finance-data/stock_news.parquet"
)

# Columns we persist to the raw parquet. Must be a subset of the HF schema
# that the aggregator's news adapter understands (see adapters/news.py).
_RAW_COLS = [
    "uuid", "related_symbols", "title", "publisher",
    "report_date", "type", "link", "news",
]


def run_news_pipeline(
    ticker: str,
    as_of: date,
    output_dir: Path | str = DATA_DIR,
) -> tuple[list[Event], list[TextChunk]]:
    """Read HF news for `ticker` as of `as_of`, write parquets + jsonl.

    Returns (events, chunks) after adapter processing so callers can
    sanity-check without re-reading from disk.
    """
    ticker = ticker.upper()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = as_of.isoformat()

    rows = _fetch_news_rows(ticker, as_of)
    rows = news_adapter.dedupe_articles(rows)

    raw_path = output_dir / f"news_{ticker}_{stamp}.parquet"
    _write_raw_parquet(rows, raw_path)

    events = news_adapter.to_events(rows)
    chunks = news_adapter.to_chunks(rows)

    _write_events_parquet(events, output_dir / f"events_{ticker}_{stamp}.parquet")
    _write_chunks_jsonl(chunks, output_dir / f"chunks_{ticker}_{stamp}.jsonl")

    return events, chunks


# ---------- internals ----------


def _read_news_table(filters: list | None):
    """Wrapped HF read so tests can swap this one function without clobbering
    `pyarrow.parquet.read_table` globally (which would break pandas'
    read_parquet on the same process)."""
    fs = HfFileSystem()
    return pq.read_table(HF_PATH, filesystem=fs, filters=filters)


def _fetch_news_rows(ticker: str, as_of: date) -> list[dict[str, Any]]:
    """Read news rows from HF, filter to ticker + as_of. Returns raw dicts
    matching the HF schema."""
    filters = [("report_date", "<=", as_of.isoformat())]
    table = _read_news_table(filters)
    df = table.to_pandas()
    if df.empty:
        return []

    # Ticker filter: `related_symbols` is a comma-separated string. No pyarrow
    # pushdown for substring, so filter in pandas.
    ticker_u = ticker.upper()
    matched = df["related_symbols"].fillna("").map(
        lambda s: any(t.strip().upper() == ticker_u for t in str(s).split(","))
    )
    df = df[matched]
    if df.empty:
        return []

    # Keep the columns the adapter understands. `news` is a list-of-struct;
    # pandas preserves it as a list/ndarray of dicts.
    keep = [c for c in _RAW_COLS if c in df.columns]
    df = df[keep]
    return [_normalize_row(r) for r in df.to_dict(orient="records")]


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce the `news` field to a plain list of plain dicts so downstream
    code never touches numpy/pandas container types."""
    out = dict(row)
    news_field = out.get("news")
    if news_field is None:
        out["news"] = []
        return out
    if hasattr(news_field, "tolist"):
        news_field = news_field.tolist()
    cleaned: list[dict[str, Any]] = []
    for p in news_field:
        if isinstance(p, dict):
            cleaned.append(
                {
                    "paragraph_number": p.get("paragraph_number"),
                    "highlight": p.get("highlight"),
                    "paragraph": p.get("paragraph"),
                }
            )
    out["news"] = cleaned
    return out


def _write_raw_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    """Write the aggregator-consumed raw file. Empty result still writes an
    empty parquet so downstream skip-if-exists logic works."""
    df = pd.DataFrame(rows, columns=_RAW_COLS) if rows else pd.DataFrame(columns=_RAW_COLS)
    df.to_parquet(path, index=False, compression="snappy")


def _write_events_parquet(events: list[Event], path: Path) -> None:
    if not events:
        pd.DataFrame(columns=[
            "event_id", "ticker", "event_date", "event_type",
            "source", "payload_ref", "text",
        ]).to_parquet(path, index=False)
        return
    df = pd.DataFrame([e.model_dump() for e in events])
    df["event_date"] = df["event_date"].astype(str)
    df.to_parquet(path, index=False, compression="snappy")


def _write_chunks_jsonl(chunks: list[TextChunk], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(c.model_dump_json() + "\n")
