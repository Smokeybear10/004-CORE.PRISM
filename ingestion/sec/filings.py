"""
HF-backed SEC filings pipeline.

Reads the `financial_reports_sec/small/{train,test}/shard_*.jsonl` shards
from the private BW HF repo, filters by ticker and filing_date <= as_of,
emits canonical Event + TextChunk records, and writes:

    data/sec/events_<TICKER>_<as_of>.parquet   # Event rows
    data/sec/chunks_<TICKER>_<as_of>.jsonl     # TextChunk rows

Both files are consumed by `ingestion/events/adapters/sec.py` at
aggregation time — the shards are nested per-CIK and too expensive to
re-stream on every aggregation pass, so the pipeline does the heavy
work once and the adapter is a thin loader.

Shard structure (one line = one COMPANY):
    {cik, name, tickers: [...], exchanges, entityType, sic,
     filings: [
        {form, filingDate, reportDate, labels, returns,
         report: {section_1: [sent, ...], section_1A: [...], section_7: [...], ...}},
        ...
     ]}

We emit:
  - One Event per filing, event_type in {10k_filing, 10q_filing, 8k_filing}.
  - One or more TextChunks per filing's relevant sections (Business /
    Risk Factors / MD&A for 10-K/Q, all present sections for 8-K),
    capped at `max_chunks_per_filing` total per filing.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
from huggingface_hub import HfFileSystem

from schema import Event, SourceType, TextChunk

DATA_DIR = Path("data/sec")
HF_BASE = (
    "datasets/BridgewaterAIHackathon/BW-AI-Hackathon"
    "/Unstructured_Data/SNE/financial_reports_sec/small"
)

# form -> (SourceType, short label used in event_type + chunk_id)
_FORM_MAP: dict[str, tuple[SourceType, str]] = {
    "10-K": (SourceType.SEC_10K, "10k"),
    "10-Q": (SourceType.SEC_10Q, "10q"),
    "8-K": (SourceType.SEC_8K, "8k"),
}

# 10-K / 10-Q sections we actually care about.
_PREFERRED_SECTIONS = ("section_1", "section_1A", "section_7")

# Chunk splitting: join a section's sentences, then split by chars if long.
_CHUNK_CHAR_TARGET = 4000
_CHUNK_CHAR_OVERLAP = 400


def run_sec_pipeline(
    ticker: str,
    as_of: date,
    output_dir: Path | str = DATA_DIR,
    max_chunks_per_filing: int = 80,
) -> tuple[list[Event], list[TextChunk]]:
    """Stream HF shards, emit + write Events/TextChunks for `ticker`."""
    ticker = ticker.upper()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = as_of.isoformat()

    events: list[Event] = []
    chunks: list[TextChunk] = []

    for company in _stream_shards(ticker):
        if not _company_tags_ticker(company, ticker):
            continue
        for filing in company.get("filings") or []:
            ev, ch = _process_filing(ticker, filing, as_of, max_chunks_per_filing)
            if ev is None:
                continue
            events.append(ev)
            chunks.extend(ch)

    _write_events_parquet(events, output_dir / f"events_{ticker}_{stamp}.parquet")
    _write_chunks_jsonl(chunks, output_dir / f"chunks_{ticker}_{stamp}.jsonl")
    return events, chunks


# ---------- internals ----------


def _stream_shards(ticker: str) -> Iterator[dict[str, Any]]:
    """Yield one company row per line from every train/test shard.

    We stream rather than downloading to keep memory flat — shards can be
    hundreds of MB. HfFileSystem returns a file-like object we iterate."""
    fs = HfFileSystem()
    for split in ("train", "test"):
        for shard_path in _list_shards(fs, f"{HF_BASE}/{split}"):
            with fs.open(shard_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue


def _list_shards(fs: HfFileSystem, path: str) -> list[str]:
    try:
        return sorted(
            p for p in fs.ls(path, detail=False)
            if p.endswith(".jsonl")
        )
    except FileNotFoundError:
        return []


def _company_tags_ticker(company: dict[str, Any], ticker: str) -> bool:
    tickers = company.get("tickers") or []
    return any(str(t).strip().upper() == ticker for t in tickers)


def _process_filing(
    ticker: str,
    filing: dict[str, Any],
    as_of: date,
    max_chunks_per_filing: int,
) -> tuple[Event | None, list[TextChunk]]:
    form = (filing.get("form") or "").strip()
    entry = _FORM_MAP.get(form)
    if entry is None:
        return None, []
    source_type, form_label = entry

    filing_date = _coerce_date(filing.get("filingDate"))
    if filing_date is None or filing_date > as_of:
        return None, []

    report_date = _coerce_date(filing.get("reportDate"))
    event_id = f"sec_{form_label}_{ticker}_{filing_date.isoformat()}"

    text = (
        f"{ticker} filed {form} with SEC on {filing_date.isoformat()}"
        + (f" for period ending {report_date.isoformat()}" if report_date else "")
        + "."
    )
    event = Event(
        event_id=event_id,
        ticker=ticker,
        event_date=filing_date,
        event_type=f"{form_label}_filing",
        source="sec_edgar",
        payload_ref=event_id,
        text=text,
    )
    chunks = _filing_to_chunks(
        ticker=ticker,
        filing=filing,
        source_type=source_type,
        form_label=form_label,
        filing_date=filing_date,
        report_date=report_date,
        max_chunks=max_chunks_per_filing,
    )
    return event, chunks


def _filing_to_chunks(
    *,
    ticker: str,
    filing: dict[str, Any],
    source_type: SourceType,
    form_label: str,
    filing_date: date,
    report_date: date | None,
    max_chunks: int,
) -> list[TextChunk]:
    report = filing.get("report") or {}
    # For 10-K/Q prefer Business, Risk Factors, MD&A. For 8-Ks iterate
    # whatever keys are present (they're small).
    if source_type in (SourceType.SEC_10K, SourceType.SEC_10Q):
        section_keys = [k for k in _PREFERRED_SECTIONS if k in report]
    else:
        section_keys = sorted(report.keys())

    out: list[TextChunk] = []
    for sec_key in section_keys:
        if len(out) >= max_chunks:
            break
        section_text = _join_section(report[sec_key])
        if not section_text:
            continue
        pieces = _split_chars(section_text, _CHUNK_CHAR_TARGET, _CHUNK_CHAR_OVERLAP)
        for idx, piece in enumerate(pieces, start=1):
            if len(out) >= max_chunks:
                break
            section_slug = sec_key.replace("section_", "sec").lower()
            chunk_id = (
                f"sec_{form_label}_{ticker}_{filing_date.isoformat()}"
                f"_{section_slug}_{idx:03d}"
            )
            out.append(
                TextChunk(
                    chunk_id=chunk_id,
                    ticker=ticker,
                    source_type=source_type,
                    publication_date=filing_date,
                    period_end=report_date,
                    source_url=None,
                    section_name=sec_key,
                    text=piece,
                    token_count=len(piece.split()),
                )
            )
    return out


def _join_section(section_value: Any) -> str:
    """Sections arrive as a list of sentence strings; join on spaces. Accept
    a single string too, defensively."""
    if section_value is None:
        return ""
    if isinstance(section_value, str):
        return section_value.strip()
    if isinstance(section_value, list):
        parts = [str(s).strip() for s in section_value if s]
        return " ".join(p for p in parts if p)
    return str(section_value).strip()


def _split_chars(text: str, target: int, overlap: int) -> list[str]:
    """Deterministic character-count splitter. One chunk if text fits."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= target:
        return [text]
    step = max(1, target - overlap)
    out: list[str] = []
    i = 0
    while i < len(text):
        piece = text[i : i + target].strip()
        if piece:
            out.append(piece)
        if i + target >= len(text):
            break
        i += step
    return out


def _coerce_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _write_events_parquet(events: list[Event], path: Path) -> None:
    cols = ["event_id", "ticker", "event_date", "event_type",
            "source", "payload_ref", "text"]
    if not events:
        pd.DataFrame(columns=cols).to_parquet(path, index=False)
        return
    df = pd.DataFrame([e.model_dump() for e in events])
    df["event_date"] = df["event_date"].astype(str)
    df.to_parquet(path, index=False, compression="snappy")


def _write_chunks_jsonl(chunks: list[TextChunk], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(c.model_dump_json() + "\n")
