"""
Aggregator: walk each source's parquet output, run its adapter, write one
unified `data/cache/events.parquet` (and `data/cache/text_chunks.parquet`
for text-bearing sources).

Every row in the output is filtered by `event_date <= as_of` (CLAUDE.md
rule 1). News is deduped at the article level by (title, report_date,
related_symbols) hash before fanout.

Sources discovered automatically under `data/`:

    data/thirteen_f/deltas_*.parquet         -> 13f_delta Events
    data/short_interest/records_*.parquet    -> short_interest_spike Events
    data/index_changes/changes_*.parquet     -> index_change_* Events (2 each)
    data/short_reports/reports_*.parquet     -> short_report Events (+ TextChunks)
    data/fda/events_*.parquet                -> fda.* Events
    data/analyst/ratings_*.parquet           -> analyst_rating_change Events
    data/analyst/targets_*.parquet           -> price_target_change Events
    data/news/news_*.parquet                 -> news Events (+ TextChunks)
    data/earnings/calendar_*.parquet         -> earnings_release Events

Missing parquet files are silently skipped — this is useful during the
hackathon when not every source has landed. The aggregator returns the
Events DataFrame it wrote so callers can sanity-check counts.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from schema import (
    AnalystRating,
    Event,
    FDAEvent,
    FDAEventType,
    HoldingAction,
    HoldingDelta,
    IndexChange,
    IndexChangeAction,
    PriceTargetChange,
    RatingAction,
    ShortInterestRecord,
    ShortReport,
    TextChunk,
)
from ingestion.events.adapters import (
    analyst,
    earnings,
    fda,
    index_changes,
    news,
    sec,
    short_interest,
    short_reports,
    thirteen_f,
)

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")
DEFAULT_OUT_EVENTS = Path("data/cache/events.parquet")
DEFAULT_OUT_CHUNKS = Path("data/cache/text_chunks.parquet")


def build_events_parquet(
    as_of: date,
    out_path: Path | str = DEFAULT_OUT_EVENTS,
    data_dir: Path | str = DEFAULT_DATA_DIR,
    chunks_out_path: Path | str | None = DEFAULT_OUT_CHUNKS,
) -> pd.DataFrame:
    """
    Build the unified events parquet and (optionally) the parallel text_chunks
    parquet. Returns the events DataFrame that was written.
    """
    data_dir = Path(data_dir)
    out_path = Path(out_path)

    events: list[Event] = []
    chunks: list[TextChunk] = []

    events.extend(_run_thirteen_f(data_dir))
    events.extend(_run_short_interest(data_dir))
    events.extend(_run_index_changes(data_dir))
    sr_events, sr_chunks = _run_short_reports(data_dir)
    events.extend(sr_events)
    chunks.extend(sr_chunks)
    events.extend(_run_fda(data_dir))
    events.extend(_run_analyst(data_dir))
    nw_events, nw_chunks = _run_news(data_dir)
    events.extend(nw_events)
    chunks.extend(nw_chunks)
    events.extend(_run_earnings(data_dir))
    sec_events, sec_chunks = _run_sec(data_dir)
    events.extend(sec_events)
    chunks.extend(sec_chunks)

    # No-foreknowledge filter (CLAUDE.md rule 1).
    events = [e for e in events if e.event_date <= as_of]
    chunks = [c for c in chunks if c.publication_date <= as_of]

    events_df = _events_to_df(events)
    events_df = events_df.sort_values(
        ["ticker", "event_date", "event_type"]
    ).reset_index(drop=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    events_df.to_parquet(out_path, index=False, compression="snappy")

    if chunks_out_path is not None:
        chunks_df = _chunks_to_df(chunks)
        chunks_path = Path(chunks_out_path)
        chunks_path.parent.mkdir(parents=True, exist_ok=True)
        chunks_df.to_parquet(chunks_path, index=False, compression="snappy")

    logger.info(
        "aggregated %d events (%d chunks) across sources as of %s -> %s",
        len(events_df), len(chunks), as_of.isoformat(), out_path,
    )
    return events_df


# ---------- per-source runners ----------

def _run_thirteen_f(data_dir: Path) -> list[Event]:
    files = sorted((data_dir / "thirteen_f").glob("deltas_*.parquet"))
    deltas: list[HoldingDelta] = []
    for f in files:
        df = pd.read_parquet(f)
        for row in df.to_dict(orient="records"):
            deltas.append(_mk_holding_delta(row))
    return thirteen_f.to_events(deltas)


def _run_short_interest(data_dir: Path) -> list[Event]:
    files = sorted((data_dir / "short_interest").glob("records_*.parquet"))
    records: list[ShortInterestRecord] = []
    for f in files:
        df = pd.read_parquet(f)
        for row in df.to_dict(orient="records"):
            records.append(_mk_short_interest(row))
    return short_interest.to_events(records)


def _run_index_changes(data_dir: Path) -> list[Event]:
    files = sorted((data_dir / "index_changes").glob("changes_*.parquet"))
    changes: list[IndexChange] = []
    for f in files:
        df = pd.read_parquet(f)
        for row in df.to_dict(orient="records"):
            changes.append(_mk_index_change(row))
    return index_changes.to_events(changes)


def _run_short_reports(data_dir: Path) -> tuple[list[Event], list[TextChunk]]:
    files = sorted((data_dir / "short_reports").glob("reports_*.parquet"))
    reports: list[ShortReport] = []
    for f in files:
        df = pd.read_parquet(f)
        for row in df.to_dict(orient="records"):
            reports.append(_mk_short_report(row))
    return short_reports.to_events(reports), short_reports.to_chunks(reports)


def _run_fda(data_dir: Path) -> list[Event]:
    files = sorted((data_dir / "fda").glob("events_*.parquet"))
    records: list[FDAEvent] = []
    for f in files:
        df = pd.read_parquet(f)
        for row in df.to_dict(orient="records"):
            records.append(_mk_fda_event(row))
    return fda.to_events(records)


def _run_analyst(data_dir: Path) -> list[Event]:
    rating_files = sorted((data_dir / "analyst").glob("ratings_*.parquet"))
    target_files = sorted((data_dir / "analyst").glob("targets_*.parquet"))
    ratings: list[AnalystRating] = []
    targets: list[PriceTargetChange] = []
    for f in rating_files:
        df = pd.read_parquet(f)
        for row in df.to_dict(orient="records"):
            ratings.append(_mk_rating(row))
    for f in target_files:
        df = pd.read_parquet(f)
        for row in df.to_dict(orient="records"):
            targets.append(_mk_target(row))
    return analyst.to_events(ratings=ratings, targets=targets)


def _run_news(data_dir: Path) -> tuple[list[Event], list[TextChunk]]:
    files = sorted((data_dir / "news").glob("news_*.parquet"))
    rows: list[dict[str, Any]] = []
    for f in files:
        df = pd.read_parquet(f)
        rows.extend(df.to_dict(orient="records"))
    rows = news.dedupe_articles(rows)
    return news.to_events(rows), news.to_chunks(rows)


def _run_earnings(data_dir: Path) -> list[Event]:
    files = sorted((data_dir / "earnings").glob("calendar_*.parquet"))
    rows: list[dict[str, Any]] = []
    for f in files:
        df = pd.read_parquet(f)
        rows.extend(df.to_dict(orient="records"))
    return earnings.to_events(rows)


def _run_sec(data_dir: Path) -> tuple[list[Event], list[TextChunk]]:
    """Load pre-built SEC Events and TextChunks written by
    `ingestion.sec.filings.run_sec_pipeline`. Unlike the other adapters,
    SEC does NOT re-derive from a raw source at aggregation time."""
    event_files = sorted((data_dir / "sec").glob("events_*.parquet"))
    chunk_files = sorted((data_dir / "sec").glob("chunks_*.jsonl"))
    event_rows: list[dict[str, Any]] = []
    for f in event_files:
        df = pd.read_parquet(f)
        event_rows.extend(df.to_dict(orient="records"))
    chunk_rows: list[dict[str, Any]] = []
    for f in chunk_files:
        chunk_rows.extend(sec.load_chunks_from_jsonl(f))
    return sec.to_events(event_rows), sec.to_chunks(chunk_rows)


# ---------- serialization ----------

def _events_to_df(events: list[Event]) -> pd.DataFrame:
    if not events:
        return pd.DataFrame(
            columns=[
                "event_id", "ticker", "event_date", "event_type",
                "source", "payload_ref", "text",
            ]
        )
    df = pd.DataFrame([e.model_dump() for e in events])
    df["event_date"] = df["event_date"].astype(str)  # UTC ISO-8601
    return df


def _chunks_to_df(chunks: list[TextChunk]) -> pd.DataFrame:
    if not chunks:
        return pd.DataFrame(
            columns=[
                "chunk_id", "ticker", "source_type", "publication_date",
                "period_end", "source_url", "section_name", "text", "token_count",
            ]
        )
    df = pd.DataFrame([c.model_dump() for c in chunks])
    df["source_type"] = df["source_type"].astype(str)
    df["publication_date"] = df["publication_date"].astype(str)
    if "period_end" in df.columns:
        df["period_end"] = df["period_end"].astype(str).where(df["period_end"].notna(), None)
    return df


# ---------- pydantic reconstitution (parquet -> model) ----------

def _mk_holding_delta(row: dict[str, Any]) -> HoldingDelta:
    return HoldingDelta(
        fund_cik=str(row["fund_cik"]),
        fund_name=str(row["fund_name"]),
        ticker=str(row["ticker"]),
        current_filing_date=_as_date(row["current_filing_date"]),
        current_period_end=_as_date(row["current_period_end"]),
        action=HoldingAction(str(row["action"])),
        shares_change=int(row["shares_change"]),
        market_value_change=int(row["market_value_change"]),
        prior_shares=_opt_int(row.get("prior_shares")),
        current_shares=int(row["current_shares"]),
    )


def _mk_short_interest(row: dict[str, Any]) -> ShortInterestRecord:
    return ShortInterestRecord(
        ticker=str(row["ticker"]),
        settlement_date=_as_date(row["settlement_date"]),
        shares_short=int(row["shares_short"]),
        avg_daily_volume=_opt_int(row.get("avg_daily_volume")),
        days_to_cover=_opt_float(row.get("days_to_cover")),
        float_short_percent=_opt_float(row.get("float_short_percent")),
    )


def _mk_index_change(row: dict[str, Any]) -> IndexChange:
    return IndexChange(
        change_id=str(row["change_id"]),
        index_name=str(row["index_name"]),
        action=IndexChangeAction(str(row["action"])),
        ticker=str(row["ticker"]),
        company_name=str(row["company_name"]),
        announcement_date=_as_date(row["announcement_date"]),
        effective_date=_as_date(row["effective_date"]),
        replacing_ticker=_opt_str(row.get("replacing_ticker")),
        source_url=_opt_str(row.get("source_url")),
    )


def _mk_short_report(row: dict[str, Any]) -> ShortReport:
    return ShortReport(
        chunk_id=str(row["chunk_id"]),
        publisher=str(row["publisher"]),
        target_ticker=str(row["target_ticker"]),
        publication_date=_as_date(row["publication_date"]),
        title=str(row["title"]),
        thesis_text=str(row["thesis_text"]),
        source_url=_opt_str(row.get("source_url")),
        token_count=_opt_int(row.get("token_count")),
    )


def _mk_fda_event(row: dict[str, Any]) -> FDAEvent:
    return FDAEvent(
        event_id=str(row["event_id"]),
        event_type=FDAEventType(str(row["event_type"])),
        event_date=_as_date(row["event_date"]),
        sponsor_ticker=_opt_str(row.get("sponsor_ticker")),
        drug_name=str(row["drug_name"]),
        indication=_opt_str(row.get("indication")),
        description=str(row["description"]),
        source_url=_opt_str(row.get("source_url")),
    )


def _mk_rating(row: dict[str, Any]) -> AnalystRating:
    return AnalystRating(
        rating_id=str(row["rating_id"]),
        ticker=str(row["ticker"]),
        analyst_firm=str(row["analyst_firm"]),
        analyst_name=_opt_str(row.get("analyst_name")),
        action=RatingAction(str(row["action"])),
        new_rating=_opt_str(row.get("new_rating")),
        prior_rating=_opt_str(row.get("prior_rating")),
        action_date=_as_date(row["action_date"]),
        source_url=_opt_str(row.get("source_url")),
    )


def _mk_target(row: dict[str, Any]) -> PriceTargetChange:
    return PriceTargetChange(
        target_id=str(row["target_id"]),
        ticker=str(row["ticker"]),
        analyst_firm=str(row["analyst_firm"]),
        analyst_name=_opt_str(row.get("analyst_name")),
        new_target=_opt_float(row.get("new_target")),
        prior_target=_opt_float(row.get("prior_target")),
        change_pct=_opt_float(row.get("change_pct")),
        action_date=_as_date(row["action_date"]),
        source_url=_opt_str(row.get("source_url")),
    )


# ---------- tiny coercion helpers ----------

def _as_date(v: Any) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _opt_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v)
    if s == "" or s.lower() == "nan" or s.lower() == "none":
        return None
    return s


def _opt_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _opt_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
