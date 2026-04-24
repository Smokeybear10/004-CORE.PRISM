"""
SEC adapter: read pre-built Event + TextChunk files produced by
`ingestion.sec.filings.run_sec_pipeline`.

Unlike every other adapter in this directory, SEC does NOT re-derive
Events from raw source rows — the raw source (HF JSONL shards, one line
per company) is too expensive to re-stream on every aggregation pass.
`run_sec_pipeline` does the heavy work once and writes canonical
Event-shaped and TextChunk-shaped files under `data/sec/`; this adapter
is a thin loader that reconstructs Pydantic objects.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from schema import Event, SourceType, TextChunk


def to_events(rows: list[dict[str, Any]]) -> list[Event]:
    """Rows are already Event-shaped (from `events_*.parquet`)."""
    out: list[Event] = []
    for r in rows:
        event_date = _as_date(r.get("event_date"))
        if event_date is None:
            continue
        out.append(
            Event(
                event_id=str(r["event_id"]),
                ticker=str(r["ticker"]).upper(),
                event_date=event_date,
                event_type=str(r["event_type"]),
                source=str(r["source"]),
                payload_ref=str(r["payload_ref"]),
                text=r.get("text") if r.get("text") is not None else None,
            )
        )
    return out


def to_chunks(rows: list[dict[str, Any]]) -> list[TextChunk]:
    """Rows are already TextChunk-shaped (from `chunks_*.jsonl`)."""
    out: list[TextChunk] = []
    for r in rows:
        pub_date = _as_date(r.get("publication_date"))
        if pub_date is None:
            continue
        source_type = r.get("source_type")
        if isinstance(source_type, str):
            source_type = SourceType(source_type)
        period_end = _as_date(r.get("period_end"))
        token_count = r.get("token_count")
        if token_count is not None:
            try:
                token_count = int(token_count)
            except (TypeError, ValueError):
                token_count = None
        out.append(
            TextChunk(
                chunk_id=str(r["chunk_id"]),
                ticker=str(r["ticker"]).upper(),
                source_type=source_type,
                publication_date=pub_date,
                period_end=period_end,
                source_url=r.get("source_url"),
                section_name=r.get("section_name"),
                text=str(r["text"]),
                token_count=token_count,
            )
        )
    return out


def load_events_from_parquet(path: Path | str) -> list[dict[str, Any]]:
    df = pd.read_parquet(path)
    return df.to_dict(orient="records")


def load_chunks_from_jsonl(path: Path | str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _as_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None
