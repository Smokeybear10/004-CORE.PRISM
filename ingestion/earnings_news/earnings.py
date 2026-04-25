"""
HF-backed earnings calendar pipeline.

Reads `stock_earning_calendar.parquet` from the private BW HF repo,
filters to scheduled earnings releases on/before `as_of`, and writes:

    data/earnings/calendar_<as_of>.parquet  # RAW rows — consumed by aggregator

The filename `calendar_<as_of>.parquet` matches the glob in
`ingestion/events/aggregator._run_earnings`, so rows flow through the
unified events table and light up `JoinedEvidence.earnings_day`.

This pipeline is SHARED (not per-ticker): the calendar contains every
ticker's earnings dates. Per-run cost is trivial; we write one file per
as_of snapshot.

Foreknowledge firewall (CLAUDE.md rule 1): every emitted row has
`report_date <= as_of`. Filter pushed down via pyarrow predicate.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
from huggingface_hub import HfFileSystem

from ingestion.events.adapters import earnings as earnings_adapter
from schema import Event

DATA_DIR = Path("data/earnings")
HF_PATH = (
    "datasets/BridgewaterAIHackathon/BW-AI-Hackathon"
    "/Structured_Data/SNE/yahoo-finance-data/stock_earning_calendar.parquet"
)

# Columns the earnings adapter reads (see adapters/earnings.py).
_RAW_COLS = ["symbol", "report_date", "time", "name", "fiscal_quarter_ending"]


def run_earnings_calendar_pipeline(
    as_of: date,
    output_dir: Path | str = DATA_DIR,
) -> list[Event]:
    """Fetch, filter, write the shared calendar parquet, return Events.

    The returned Event list is a convenience for callers; the durable
    artifact is the parquet file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = as_of.isoformat()

    rows = _fetch_calendar_rows(as_of)
    raw_path = output_dir / f"calendar_{stamp}.parquet"
    _write_raw_parquet(rows, raw_path)

    return earnings_adapter.to_events(rows)


# ---------- internals ----------


def _read_calendar_table(filters: list | None):
    """Wrapped HF read — see news.py for rationale (tests swap this,
    NOT the shared pq.read_table symbol)."""
    fs = HfFileSystem()
    return pq.read_table(HF_PATH, filesystem=fs, filters=filters)


def _fetch_calendar_rows(as_of: date) -> list[dict[str, Any]]:
    filters = [("report_date", "<=", as_of.isoformat())]
    table = _read_calendar_table(filters)
    df = table.to_pandas()
    if df.empty:
        return []
    keep = [c for c in _RAW_COLS if c in df.columns]
    return df[keep].to_dict(orient="records")


def _write_raw_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    df = pd.DataFrame(rows, columns=_RAW_COLS) if rows else pd.DataFrame(columns=_RAW_COLS)
    df.to_parquet(path, index=False, compression="snappy")
