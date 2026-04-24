"""
Ingestion glue for the demo.

Wraps `ingestion.sec.get_filings_as_of` + `ingestion.earnings_news` into a
single call, but with an in-memory preload of the news parquet so per-request
latency is tolerable. `ingestion.earnings_news.fetch_news` re-reads the full
628 MB parquet every call (~4 minutes for AMD); the demo can't pay that per
request, so we load once at server startup and index by ticker.

Public API:
    preload_news(tickers)                 # call at startup / module import
    chunks_for_real(ticker, as_of)        # SEC filings + news, publication_date <= as_of
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from ingestion.earnings_news import (
    _chunk_text,
    _ensure_parquet,
    _normalize_ticker_field,
    _paragraph_texts,
    _to_date,
)
from ingestion.sec import get_filings_as_of
from schema import SourceType, TextChunk


_NEWS_DF: Optional[pd.DataFrame] = None
_NEWS_BY_TICKER: dict[str, pd.DataFrame] = {}


def preload_news(tickers: list[str]) -> None:
    """Load the bundled news parquet once and pre-slice per ticker."""
    global _NEWS_DF
    if _NEWS_DF is None:
        path = _ensure_parquet()
        df = pd.read_parquet(path)
        df = df.copy()
        df["_pub_date"] = df["report_date"].map(_to_date)
        _NEWS_DF = df

    for t in tickers:
        t_upper = t.upper()
        if t_upper in _NEWS_BY_TICKER:
            continue
        mask = _NEWS_DF["related_symbols"].map(
            lambda v: t_upper in _normalize_ticker_field(v)
        )
        _NEWS_BY_TICKER[t_upper] = _NEWS_DF[mask].reset_index(drop=True)


def _news_chunks_for(ticker: str, as_of: date) -> list[TextChunk]:
    """Produce TextChunks from the pre-indexed news DataFrame for (ticker, as_of)."""
    t = ticker.upper()
    df = _NEWS_BY_TICKER.get(t)
    if df is None or len(df) == 0:
        return []
    df = df[df["_pub_date"].map(lambda d: d is not None and d <= as_of)]
    if len(df) == 0:
        return []

    out: list[TextChunk] = []
    for _, row in df.iterrows():
        pub_date = row["_pub_date"]
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
        # Take only the first chunk per article — keeps citation counts demo-friendly.
        chunk_body, tok_count = pieces[0]
        out.append(TextChunk(
            chunk_id=f"news_{t}_{pub_date.isoformat()}_article_{len(out)+1:04d}",
            ticker=t,
            source_type=SourceType.NEWS,
            publication_date=pub_date,
            source_url=url,
            section_name=publisher,
            text=chunk_body,
            token_count=tok_count,
        ))
    return out


def chunks_for_real(ticker: str, as_of: date) -> list[TextChunk]:
    """
    Real SEC filings + news chunks for (ticker, as_of).

    Sorted by publication_date DESC so the most recent evidence is first —
    `model.attribute()` picks `chunks[:5]` for evidence, so recent-first means
    the citations surface current narrative, not ancient boilerplate.
    """
    sec = get_filings_as_of(ticker, as_of)
    news = _news_chunks_for(ticker, as_of)
    combined = sec + news
    combined.sort(key=lambda c: c.publication_date, reverse=True)
    return combined
