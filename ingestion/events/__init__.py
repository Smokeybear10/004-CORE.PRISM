"""
Event-joining layer.

This module is the bridge between per-source ingestion (`ingestion/sec/`,
`ingestion/idiosyncratic/`, `ingestion/prices/`, etc.) and the attribution
runner in `model/`. It does two jobs:

1. **Adapters** (`ingestion.events.adapters.*`): each source's Pydantic
   records get converted into the unified `schema.Event` envelope, with a
   deterministic `event_id` and a human-readable `text` summary so the
   attribution LLM can reason over any source uniformly.

2. **Aggregator + joiner** (`aggregator.py`, `join.py`): load every source's
   parquet, apply the adapters, filter by `as_of`, dedupe news, write
   `data/cache/events.parquet`. Then `join_evidence(move, ...)` pulls the
   events and chunks that fall inside a trading-day window around a
   flagged `PriceMove` and returns a `JoinedEvidence` bundle.

## Demo

    from datetime import date
    from pathlib import Path
    import pandas as pd
    from schema import PriceMove
    from ingestion.events.aggregator import build_events_parquet
    from ingestion.events.join import join_evidence

    # 1. Build the unified events table from every source's parquet.
    build_events_parquet(
        as_of=date(2024, 2, 5),
        out_path=Path("data/cache/events.parquet"),
    )

    # 2. For a flagged move, pull its evidence window.
    events_df = pd.read_parquet("data/cache/events.parquet")
    chunks_df = pd.read_parquet("data/cache/text_chunks.parquet")
    earnings_cal = pd.read_parquet("data/cache/earnings_calendar.parquet")

    move = PriceMove(
        ticker="AAPL",
        move_date=date(2024, 2, 2),
        return_pct=-0.037,
        vol_zscore=-2.8,
        magnitude_rank=0.97,
    )
    evidence = join_evidence(move, events_df, chunks_df, earnings_cal)
    print(evidence.model_dump_json(indent=2))
"""
from __future__ import annotations

from ingestion.events.aggregator import build_events_parquet
from ingestion.events.join import join_evidence

__all__ = ["build_events_parquet", "join_evidence"]
