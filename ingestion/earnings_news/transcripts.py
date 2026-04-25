"""
Earnings-call transcript ingestion.

Pulls from the bundled hackathon dataset on HuggingFace:
    Unstructured_Data/SNE/yahoo_finance/stock_earning_call_transcripts.parquet

228K transcripts across 5,783 tickers, 2005-10 → 2026-04.

Schema of the source parquet (one row per call):
    symbol, fiscal_year, fiscal_quarter, report_date
    transcripts       — list[struct{paragraph_number, speaker, content}]
    transcripts_id

This module flattens each call's paragraph stream into speaker-tagged
chunks, mirrors the tokenization scheme used by the news ingest
(tiktoken cl100k_base, 800 target / 100 overlap), and emits `TextChunk`s
with `source_type=EARNINGS_TRANSCRIPT`.

Section inference heuristic:
    "prepared" — paragraphs spoken by Operator, Executives, IR, CEO/CFO
                 titles, before the first analyst question.
    "qa"       — everything from the first analyst question onward
                 (speaker contains "Analyst" OR a question mark appears
                 early in the content).

Cached on first use to ingestion/earnings_news/.cache/
"""
from __future__ import annotations

import re
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from schema import SourceType, TextChunk

CACHE_DIR = Path(__file__).parent / ".cache"
TRANSCRIPTS_FILENAME = (
    "Unstructured_Data/SNE/yahoo_finance/stock_earning_call_transcripts.parquet"
)
HF_REPO_ID = "BridgewaterAIHackathon/BW-AI-Hackathon"

DEFAULT_TARGET_TOKENS = 800
DEFAULT_OVERLAP_TOKENS = 100

# Conservative QA-transition cue. Earlier we transitioned on any operator
# mention of "question-and-answer" but operators typically describe the
# format in their OPENING line ("a brief question-and-answer session WILL
# FOLLOW"), which mis-classified entire calls as QA. Only switch on an
# explicit analyst speaker tag now — calls without that stay 100%
# "prepared", which is correct (just under-segmented).
_ANALYST_SPEAKER_RE = re.compile(r"\banalyst\b", re.IGNORECASE)


def _local_parquet_path() -> Path:
    return CACHE_DIR / "stock_earning_call_transcripts.parquet"


def _ensure_parquet() -> Path:
    local = _local_parquet_path()
    if local.exists() and local.stat().st_size > 0:
        return local
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import hf_hub_download

    downloaded = hf_hub_download(
        repo_id=HF_REPO_ID,
        filename=TRANSCRIPTS_FILENAME,
        repo_type="dataset",
    )
    shutil.copy(downloaded, local)
    return local


# ---------- Tokenization (matches ingestion.earnings_news.fetch_news) ----------

def _chunk_text(
    text: str,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[tuple[str, int]]:
    text = (text or "").strip()
    if not text:
        return []
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(text)
        if len(tokens) <= target_tokens:
            return [(text, len(tokens))]
        step = max(1, target_tokens - overlap_tokens)
        out: list[tuple[str, int]] = []
        i = 0
        while i < len(tokens):
            slice_ = tokens[i : i + target_tokens]
            piece = enc.decode(slice_).strip()
            if piece:
                out.append((piece, len(slice_)))
            if i + target_tokens >= len(tokens):
                break
            i += step
        return out
    except ImportError:
        # Word-count fallback if tiktoken isn't installed.
        words = text.split()
        target_words = int(target_tokens * 0.75)
        overlap_words = int(overlap_tokens * 0.75)
        if len(words) <= target_words:
            return [(text, int(len(words) / 0.75))]
        step = max(1, target_words - overlap_words)
        out: list[tuple[str, int]] = []
        i = 0
        while i < len(words):
            piece = " ".join(words[i : i + target_words]).strip()
            if piece:
                out.append((piece, int(len(piece.split()) / 0.75)))
            if i + target_words >= len(words):
                break
            i += step
        return out


# ---------- Section inference + transcript flattening ----------

def _flatten_and_section(paragraphs: list[dict]) -> list[tuple[str, str, str]]:
    """Return list of (section, speaker, content).

    Walks the paragraph stream; switches from 'prepared' to 'qa' at the first
    analyst / operator-question cue.
    """
    section = "prepared"
    out: list[tuple[str, str, str]] = []
    for p in paragraphs or []:
        try:
            speaker = str(p.get("speaker") or "").strip() if isinstance(p, dict) else ""
            content = str(p.get("content") or "").strip() if isinstance(p, dict) else ""
        except AttributeError:
            continue
        if not content:
            continue

        if section == "prepared" and _ANALYST_SPEAKER_RE.search(speaker):
            section = "qa"

        out.append((section, speaker, content))
    return out


def _to_date(val: Any) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return datetime.fromisoformat(str(val)[:19]).date()
    except ValueError:
        try:
            return date.fromisoformat(str(val)[:10])
        except ValueError:
            return None


def _paragraphs_as_list(field: Any) -> list[dict]:
    """The `transcripts` column comes back as a numpy array of dicts in most
    pyarrow/pandas versions; sometimes as a plain list. Normalize."""
    if field is None:
        return []
    if hasattr(field, "tolist"):
        field = field.tolist()
    if not isinstance(field, list):
        return []
    out: list[dict] = []
    for item in field:
        if isinstance(item, dict):
            out.append(item)
    return out


# ---------- Public API ----------

def fetch_earnings_transcripts(
    tickers: list[str] | str,
    start_date: date,
    end_date: date,
) -> list[TextChunk]:
    """Pull earnings-call transcripts and emit speaker-tagged TextChunks.

    `tickers` accepts a single ticker string or a list — mirrors the
    flexibility of `fetch_news`.

    Each chunk:
        chunk_id      : earnings_transcript_{TICKER}_{DATE}_{section}_{idx:03d}
        section_name  : "prepared" or "qa"
        text          : speaker tag prefixed, e.g. "[CEO]: We expect..."
        token_count   : tiktoken count where available
    """
    import pandas as pd

    if isinstance(tickers, str):
        tickers = [tickers]
    tickers_upper = {t.upper() for t in tickers}

    parquet_path = _ensure_parquet()
    df = pd.read_parquet(parquet_path)

    df["_pub_date"] = df["report_date"].map(_to_date)
    df = df[df["symbol"].str.upper().isin(tickers_upper)]
    df = df[df["_pub_date"].map(lambda d: d is not None and start_date <= d <= end_date)]
    if df.empty:
        return []

    out: list[TextChunk] = []
    for _, row in df.iterrows():
        ticker = str(row["symbol"]).upper()
        pub_date: date = row["_pub_date"]
        paragraphs = _paragraphs_as_list(row.get("transcripts"))
        if not paragraphs:
            continue

        sectioned = _flatten_and_section(paragraphs)
        if not sectioned:
            continue

        # Accumulate within a section, chunk when we hit the target, reset on
        # section boundaries so a single chunk is entirely "prepared" or entirely "qa".
        buckets: list[tuple[str, str]] = []
        cur_section = sectioned[0][0]
        buf: list[str] = []
        for sec, speaker, content in sectioned:
            if sec != cur_section and buf:
                buckets.append((cur_section, "\n\n".join(buf)))
                buf = []
                cur_section = sec
            cur_section = sec
            buf.append(f"[{speaker}]: {content}" if speaker else content)
        if buf:
            buckets.append((cur_section, "\n\n".join(buf)))

        for section_name, section_text in buckets:
            pieces = _chunk_text(section_text)
            for idx, (chunk_body, tok_count) in enumerate(pieces, start=1):
                chunk_id = (
                    f"earnings_transcript_{ticker}_{pub_date.isoformat()}"
                    f"_{section_name}_{idx:03d}"
                )
                out.append(
                    TextChunk(
                        chunk_id=chunk_id,
                        ticker=ticker,
                        source_type=SourceType.EARNINGS_TRANSCRIPT,
                        publication_date=pub_date,
                        period_end=None,
                        source_url=f"https://finance.yahoo.com/quote/{ticker}/history/",
                        section_name=section_name,
                        text=chunk_body,
                        token_count=tok_count,
                    )
                )
    return out


def get_earnings_transcripts_as_of(ticker: str, as_of: date) -> list[TextChunk]:
    """All transcript chunks for `ticker` with publication_date <= as_of.

    No-foreknowledge firewall (CLAUDE.md rule #1).
    """
    chunks = fetch_earnings_transcripts(
        [ticker], start_date=date(1900, 1, 1), end_date=as_of
    )
    return [c for c in chunks if c.publication_date <= as_of]
