"""
News + earnings transcript ingestion.

Owner: Thomas (branch: thomas-test). Reads from the bundled hackathon dataset
on HuggingFace: `BridgewaterAIHackathon/BW-AI-Hackathon` -> `stock_news.parquet`.

No paywalls, no API keys, no scraping. 806K ticker-tagged articles with
paragraph-level body text, publisher, timestamp, and source link.

Public API:
    fetch_news(tickers, start_date, end_date, source_type=NEWS) -> list[TextChunk]
    get_news_as_of(ticker, as_of) -> list[TextChunk]
    fetch_earnings_transcripts(ticker, start_date, end_date) -> list[TextChunk]  # stub

`fetch_news` accepts a LIST of tickers so the same function powers both the
company-news and peer-news ablation runs. Pass source_type=PEER_NEWS when
fetching peer/sector chunks to keep the ablation clean.

Foreknowledge firewall:
    get_news_as_of MUST filter publication_date <= as_of. CLAUDE.md rule #1.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from schema import SourceType, TextChunk

CACHE_DIR = Path(__file__).parent / ".cache"
NEWS_PARQUET_FILENAME = "Structured_Data/SNE/yahoo-finance-data/stock_news.parquet"
HF_REPO_ID = "BridgewaterAIHackathon/BW-AI-Hackathon"

DEFAULT_TARGET_TOKENS = 800
DEFAULT_OVERLAP_TOKENS = 100


# ---------- Parquet cache (download once, reuse) ----------

def _local_parquet_path() -> Path:
    return CACHE_DIR / "stock_news.parquet"


def _ensure_parquet() -> Path:
    """Download the bundled news parquet to local cache on first call."""
    local = _local_parquet_path()
    if local.exists() and local.stat().st_size > 0:
        return local
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import hf_hub_download

    downloaded = hf_hub_download(
        repo_id=HF_REPO_ID,
        filename=NEWS_PARQUET_FILENAME,
        repo_type="dataset",
    )
    shutil.copy(downloaded, local)
    return local


# ---------- Text chunking (same scheme as SEC pipeline) ----------

def _word_chunks(text: str, target_words: int, overlap_words: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    if len(words) <= target_words:
        return [text.strip()]
    step = max(1, target_words - overlap_words)
    out: list[str] = []
    i = 0
    while i < len(words):
        piece = " ".join(words[i : i + target_words]).strip()
        if piece:
            out.append(piece)
        if i + target_words >= len(words):
            break
        i += step
    return out


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
        target_words = int(target_tokens * 0.75)
        overlap_words = int(overlap_tokens * 0.75)
        return [(p, int(len(p.split()) / 0.75)) for p in _word_chunks(text, target_words, overlap_words)]


# ---------- Row normalization ----------

def _normalize_ticker_field(val: Any) -> list[str]:
    """`related_symbols` may be a single string, comma-sep string, or a list.

    Return an uppercased, deduped list.
    """
    if val is None:
        return []
    if isinstance(val, (list, tuple, set)):
        return [str(x).strip().upper() for x in val if x]
    s = str(val).strip()
    if not s:
        return []
    # Try JSON list first
    if s.startswith("["):
        try:
            parsed = json.loads(s.replace("'", '"'))
            if isinstance(parsed, list):
                return [str(x).strip().upper() for x in parsed if x]
        except json.JSONDecodeError:
            pass
    # Fall back to comma/semicolon split
    parts = re.split(r"[,;\s]+", s)
    return [p.strip().upper() for p in parts if p.strip()]


def _paragraph_texts(news_field: Any) -> list[str]:
    """Extract paragraph strings from the `news` column."""
    if news_field is None:
        return []
    if isinstance(news_field, str):
        # Could be a JSON-stringified list of dicts
        try:
            parsed = json.loads(news_field.replace("'", '"'))
            news_field = parsed
        except Exception:
            return [news_field]
    if hasattr(news_field, "tolist"):
        news_field = news_field.tolist()
    out: list[str] = []
    try:
        for item in news_field:
            if isinstance(item, dict):
                p = item.get("paragraph") or item.get("text") or ""
                if p:
                    out.append(str(p))
            elif isinstance(item, str):
                out.append(item)
    except TypeError:
        pass
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


# ---------- Public API ----------

def fetch_news(
    tickers: list[str],
    start_date: date,
    end_date: date,
    source_type: SourceType = SourceType.NEWS,
) -> list[TextChunk]:
    """Pull ticker-tagged news articles from the bundled Yahoo Finance feed.

    Emits one or more TextChunks per article. Passing `source_type=PEER_NEWS`
    tags the same fetch for peer-news ablation runs.
    """
    import pandas as pd

    parquet_path = _ensure_parquet()
    df = pd.read_parquet(parquet_path)

    tickers_upper = {t.upper() for t in tickers}

    # Filter: match if any ticker in related_symbols overlaps with requested tickers.
    def _row_tickers(row_val: Any) -> list[str]:
        return [t for t in _normalize_ticker_field(row_val) if t in tickers_upper]

    df["_matched_tickers"] = df["related_symbols"].map(_row_tickers)
    df = df[df["_matched_tickers"].map(len) > 0]
    if df.empty:
        return []

    # Filter by date
    df["_pub_date"] = df["report_date"].map(_to_date)
    df = df[df["_pub_date"].map(lambda d: d is not None and start_date <= d <= end_date)]
    if df.empty:
        return []

    out: list[TextChunk] = []
    for _, row in df.iterrows():
        pub_date: date = row["_pub_date"]
        title = str(row.get("title") or "").strip()
        publisher = str(row.get("publisher") or "news").strip() or "news"
        url = str(row.get("link") or "").strip() or None
        paragraphs = _paragraph_texts(row.get("news"))
        body_parts = [title] if title else []
        body_parts.extend(paragraphs)
        body = "\n\n".join(p for p in body_parts if p).strip()
        if not body:
            continue

        pieces = _chunk_text(body)
        if not pieces:
            continue

        for matched_ticker in row["_matched_tickers"]:
            for idx, (chunk_body, tok_count) in enumerate(pieces, start=1):
                chunk_id = (
                    f"{source_type.value}_{matched_ticker}_{pub_date.isoformat()}"
                    f"_article_{idx:03d}"
                )
                out.append(
                    TextChunk(
                        chunk_id=chunk_id,
                        ticker=matched_ticker,
                        source_type=source_type,
                        publication_date=pub_date,
                        period_end=None,
                        source_url=url,
                        section_name=publisher,
                        text=chunk_body,
                        token_count=tok_count,
                    )
                )
    return out


def get_news_as_of(ticker: str, as_of: date) -> list[TextChunk]:
    """All news chunks for `ticker` with publication_date <= as_of.

    Foreknowledge firewall (CLAUDE.md #1). Pulls from the cached parquet so
    this is cheap after the first fetch.
    """
    # Pull across the full date range; filter afterwards. Cheap because we
    # operate on the local parquet.
    chunks = fetch_news(
        [ticker],
        start_date=date(1900, 1, 1),
        end_date=as_of,
    )
    return [c for c in chunks if c.publication_date <= as_of]


def fetch_earnings_transcripts(
    ticker: str | list[str],
    start_date: date,
    end_date: date,
) -> list[TextChunk]:
    """Pull earnings-call transcripts as speaker-tagged TextChunks.

    Accepts a single ticker or list. Dispatches to the submodule which handles
    the HF download, caching, section inference (prepared vs qa), and
    tiktoken-based chunking. See `ingestion.earnings_news.transcripts`.
    """
    from ingestion.earnings_news.transcripts import (
        fetch_earnings_transcripts as _impl,
    )

    return _impl(ticker, start_date, end_date)


def get_earnings_transcripts_as_of(ticker: str, as_of: date) -> list[TextChunk]:
    """All transcript chunks for `ticker` with publication_date <= as_of."""
    from ingestion.earnings_news.transcripts import (
        get_earnings_transcripts_as_of as _impl,
    )

    return _impl(ticker, as_of)
