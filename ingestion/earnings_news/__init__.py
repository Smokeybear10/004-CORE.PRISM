"""
News + earnings transcript ingestion.

Owner: News lead (branch: person4-news). Yahoo Finance news overlaps with
person2-yahoo (Srilekha) - coordinate so you're not double-fetching.

Public API:
    - fetch_news(tickers, start_date, end_date, source_type=NEWS) -> list[TextChunk]
    - fetch_earnings_transcripts(ticker, start_date, end_date) -> list[TextChunk]
    - get_news_as_of(ticker, as_of) -> list[TextChunk]

KEY DESIGN RULE: `fetch_news` MUST accept a LIST of tickers, not just one.
We reuse this function for the peer-news ablation (call with peers=["MSFT","GOOGL"]
to build peer-news chunks for AAPL analysis). Mentor: broadening news scope to
peer tickers is the single cheapest additive lever - same fetch function,
different tickers. Tag output with source_type=PEER_NEWS in that mode.

MVP sources (free):
    - yfinance.Ticker(ticker).news   - headlines + summaries
    - Press releases / 8-K-linked news
    - Curated RSS (optional)

NON-MVP (paywalled): WSJ, CNBC, Bloomberg. Note limitation in demo.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from schema import TextChunk, SourceType

CACHE_DIR = Path(__file__).parent / ".cache"


def fetch_news(
    tickers: list[str],
    start_date: date,
    end_date: date,
    source_type: SourceType = SourceType.NEWS,
) -> list[TextChunk]:
    """
    Pull news for `tickers` in [start_date, end_date]. Tag with `source_type`
    so we can distinguish company news (NEWS) from peer-ticker news (PEER_NEWS)
    in ablation runs.

    TODO: yfinance for MVP. Cache per-ticker JSON to .cache/news_{ticker}.json.
    """
    raise NotImplementedError("fetch_news - implement me")


def fetch_earnings_transcripts(
    ticker: str,
    start_date: date,
    end_date: date,
) -> list[TextChunk]:
    """
    Earnings call transcripts. Chunk Q&A separately from prepared remarks
    (use section_name="prepared" vs "qa").

    TODO: evaluate free sources (Seeking Alpha scraping is fragile; Motley Fool
    has some). HuggingFace may have an earnings-transcript dataset - check first.
    """
    raise NotImplementedError("fetch_earnings_transcripts - implement me")


def get_news_as_of(ticker: str, as_of: date) -> list[TextChunk]:
    """All news chunks for `ticker` with publication_date <= as_of."""
    raise NotImplementedError("get_news_as_of - implement me")
