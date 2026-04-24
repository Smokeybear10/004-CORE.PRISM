"""
FINRA bi-monthly short interest ingestion.

Source: FINRA Data API — `consolidatedShortInterest` dataset
        POST https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest
        (no auth required for this dataset; CSV and JSON formats supported)

Pipeline:
    fetch_short_interest(ticker, as_of) -> List[ShortInterestRecord]
    detect_short_interest_spikes(records) -> List[Event]  # >20% SI increase PoP
    events_to_text_chunks(events) -> List[TextChunk]       # for model citation

Outputs saved under data/short_interest/ as parquet (and JSONL for TextChunks).

as_of rule (CLAUDE.md #1): short interest for a settlement date is not public
until FINRA publishes it ~8 business days later. We use settlement + 14 calendar
days as a conservative estimate of publication, and filter so that only records
whose publication date <= as_of are returned.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from schema import Event, ShortInterestRecord, SourceType, TextChunk

FINRA_API_URL = (
    "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
)
# FINRA publishes bi-monthly short interest ~8 business days after settlement.
# Use 14 calendar days as a conservative no-foreknowledge buffer.
PUBLICATION_LAG_DAYS = 14
SPIKE_THRESHOLD = 0.20  # >20% PoP increase triggers "short_interest_spike"
USER_AGENT = "BW-Hackathon/1.0 (henryji327@gmail.com)"
DATA_DIR = Path("data/short_interest")


# ---------- Public API ----------

def fetch_short_interest(
    ticker: Optional[str],
    as_of: date,
) -> list[ShortInterestRecord]:
    """
    Fetch FINRA bi-monthly short interest records.

    Returns records whose estimated publication date (settlement + 14d) is
    <= as_of. If `ticker` is None, returns all tickers that FINRA reports.

    Paginates internally; FINRA's dataset API caps a single response at 5000 rows.
    """
    max_settlement = as_of - timedelta(days=PUBLICATION_LAG_DAYS)
    compare_filters = [
        {
            "fieldName": "settlementDate",
            "fieldValue": max_settlement.isoformat(),
            "compareType": "LTE",  # FINRA Data API: LTE/GTE/EQUAL/etc
        }
    ]
    if ticker:
        compare_filters.append(
            {
                "fieldName": "symbolCode",
                "fieldValue": ticker.upper(),
                "compareType": "EQUAL",
            }
        )

    records: list[ShortInterestRecord] = []
    offset = 0
    page_size = 5000
    while True:
        body = {"limit": page_size, "offset": offset, "compareFilters": compare_filters}
        resp = requests.post(
            FINRA_API_URL,
            json=body,
            timeout=60,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        records.extend(_row_to_record(row) for row in rows if row.get("symbolCode"))
        if len(rows) < page_size:
            break
        offset += page_size
    return records


def detect_short_interest_spikes(
    records: list[ShortInterestRecord],
) -> list[Event]:
    """
    Emit a `short_interest_spike` Event for every ticker/settlement where
    shares_short increased >20% vs the prior reported period. event_date is
    the estimated publication date (settlement + 14d) to avoid foreknowledge.
    """
    by_ticker: dict[str, list[ShortInterestRecord]] = {}
    for r in records:
        by_ticker.setdefault(r.ticker, []).append(r)

    events: list[Event] = []
    for ticker, trows in by_ticker.items():
        trows.sort(key=lambda r: r.settlement_date)
        for prior, current in zip(trows, trows[1:]):
            if prior.shares_short <= 0:
                continue
            change = (current.shares_short - prior.shares_short) / prior.shares_short
            if change <= SPIKE_THRESHOLD:
                continue
            pub_date = _publication_date(current.settlement_date)
            event_id = (
                f"short_interest_spike_{ticker}_{current.settlement_date.isoformat()}"
            )
            dtc = (
                f"{current.days_to_cover:.2f}"
                if current.days_to_cover is not None
                else "n/a"
            )
            text = (
                f"{ticker} short interest rose {change:.1%} from "
                f"{prior.shares_short:,} to {current.shares_short:,} shares "
                f"at the {current.settlement_date.isoformat()} settlement "
                f"(days-to-cover {dtc})."
            )
            events.append(
                Event(
                    event_id=event_id,
                    ticker=ticker,
                    event_date=pub_date,
                    event_type="short_interest_spike",
                    source="FINRA",
                    payload_ref=event_id,  # matches TextChunk.chunk_id below
                    text=text,
                )
            )
    return events


def events_to_text_chunks(events: list[Event]) -> list[TextChunk]:
    """
    Materialize each Event's generated text as a TextChunk so the attribution
    model can cite it via evidence_chunk_ids.
    """
    chunks: list[TextChunk] = []
    for e in events:
        if not e.text:
            continue
        chunks.append(
            TextChunk(
                chunk_id=e.event_id,
                ticker=e.ticker,
                source_type=SourceType.SHORT_INTEREST,
                publication_date=e.event_date,
                source_url="https://www.finra.org/finra-data/browse-catalog/short-interest",
                section_name=e.event_type,
                text=e.text,
                token_count=len(e.text.split()),
            )
        )
    return chunks


def run_short_interest_pipeline(
    as_of: date,
    ticker: Optional[str] = None,
    output_dir: Path | str = DATA_DIR,
) -> tuple[list[ShortInterestRecord], list[Event], list[TextChunk]]:
    """End-to-end: fetch, detect spikes, emit chunks, write parquet + JSONL."""
    records = fetch_short_interest(ticker, as_of)
    events = detect_short_interest_spikes(records)
    chunks = events_to_text_chunks(events)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = as_of.isoformat()
    suffix = f"_{ticker.upper()}" if ticker else ""

    save_short_interest_to_parquet(records, output_dir / f"records{suffix}_{stamp}.parquet")
    save_events_to_parquet(events, output_dir / f"events{suffix}_{stamp}.parquet")
    _write_chunks_jsonl(chunks, output_dir / f"chunks{suffix}_{stamp}.jsonl")

    return records, events, chunks


# ---------- I/O helpers ----------

def save_short_interest_to_parquet(
    records: list[ShortInterestRecord],
    filepath: Path | str,
) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        # Write an empty frame with the right columns so downstream readers don't break
        pd.DataFrame(
            columns=[
                "ticker",
                "settlement_date",
                "shares_short",
                "avg_daily_volume",
                "days_to_cover",
                "float_short_percent",
            ]
        ).to_parquet(filepath, index=False)
        return
    df = pd.DataFrame([r.model_dump() for r in records])
    df["settlement_date"] = df["settlement_date"].astype(str)  # UTC ISO-8601
    df.to_parquet(filepath, compression="snappy", index=False)


def save_events_to_parquet(events: list[Event], filepath: Path | str) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    if not events:
        pd.DataFrame(
            columns=[
                "event_id",
                "ticker",
                "event_date",
                "event_type",
                "source",
                "payload_ref",
                "text",
            ]
        ).to_parquet(filepath, index=False)
        return
    df = pd.DataFrame([e.model_dump() for e in events])
    df["event_date"] = df["event_date"].astype(str)
    df.to_parquet(filepath, compression="snappy", index=False)


def _write_chunks_jsonl(chunks: list[TextChunk], filepath: Path | str) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(
        "\n".join(c.model_dump_json() for c in chunks),
        encoding="utf-8",
    )


# ---------- internals ----------

def _publication_date(settlement_date: date) -> date:
    """FINRA publishes ~8 business days after settlement; 14 calendar days is
    a safe conservative estimate."""
    return settlement_date + timedelta(days=PUBLICATION_LAG_DAYS)


def _row_to_record(row: dict) -> ShortInterestRecord:
    raw_dtc = row.get("daysToCoverQuantity")
    # FINRA returns daysToCoverQuantity as a string in CSV and as a number in
    # JSON. Cast to float FIRST, then compare against the 999.99 "no volume
    # data" sentinel — otherwise a string ">=" comparison silently drops data.
    if raw_dtc is None or raw_dtc == "":
        days_to_cover: Optional[float] = None
    else:
        try:
            days_to_cover = float(raw_dtc)
        except (TypeError, ValueError):
            days_to_cover = None
        if days_to_cover is not None and days_to_cover >= 999:
            days_to_cover = None

    avg_vol = row.get("averageDailyVolumeQuantity")
    # Preserve 0 as a real observed value ("stock didn't trade"), not missing.
    avg_vol = int(avg_vol) if avg_vol is not None else None
    return ShortInterestRecord(
        ticker=row["symbolCode"].upper(),
        settlement_date=date.fromisoformat(row["settlementDate"]),
        shares_short=int(row.get("currentShortPositionQuantity") or 0),
        avg_daily_volume=avg_vol,
        days_to_cover=days_to_cover,
        float_short_percent=None,  # FINRA API doesn't expose float-normalized SI
    )
