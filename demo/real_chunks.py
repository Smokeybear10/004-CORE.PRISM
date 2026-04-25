"""
Ingestion glue for the demo.

Wraps three ingestion streams into a single call:
    - `ingestion.sec.get_filings_as_of`               (disk-cached JSON)
    - an in-memory slice of the news parquet          (~628 MB, load once)
    - a pre-fetched 13F chunks JSONL                  (built offline)

`ingestion.earnings_news.fetch_news` re-reads the full 628 MB parquet every
call (~4 minutes for AMD); the demo can't pay that per request, so we load
once at server startup and index by ticker. Same pattern for 13F.

Public API:
    preload_news(tickers)                 # call at startup
    preload_thirteen_f()                  # call at startup
    chunks_for_real(ticker, as_of)        # SEC + news + 13F, pub_date <= as_of
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
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
_THIRTEEN_F_BY_TICKER: dict[str, list[TextChunk]] = {}
_TRANSCRIPTS_BY_TICKER: dict[str, list[TextChunk]] = {}

_REPO_ROOT = Path(__file__).resolve().parent.parent
_THIRTEEN_F_JSONL = _REPO_ROOT / "data" / "thirteen_f" / "focal_chunks.jsonl"

# Per-focal peer set: tickers whose news we surface as PEER_NEWS for that focal.
_PEERS: dict[str, list[str]] = {
    "ABT": ["JNJ", "MDT", "BSX", "SYK", "BAX"],
    "ACU": ["CHD", "CL", "KMB", "PG", "EL"],
    "AIR": ["BA", "RTX", "LMT", "GD", "NOC", "HEI", "TDG"],
    "AMD": ["NVDA", "INTC", "TSM", "QCOM", "AVGO"],
    "APD": ["LIN", "ECL", "SHW", "DD", "DOW"],
}

# Per-focal sector proxies: bellwether tickers in the same sector but
# distinct from the direct peer set above. Sector ETFs (XLK, XLV, ...)
# don't show up in the news parquet's `related_symbols`, so we use real
# index-weight names as the sector signal.
_SECTOR_PROXIES: dict[str, list[str]] = {
    "ABT": ["UNH", "PFE", "MRK", "LLY", "TMO"],
    "ACU": ["WMT", "COST", "MO", "PEP", "KO"],
    "AIR": ["GE", "MMM", "CAT", "DE", "HON"],
    "AMD": ["MSFT", "GOOGL", "META", "ORCL", "CRM"],
    "APD": ["NEM", "FCX", "NUE", "MLM", "VMC"],
}


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


def _news_rows_for(symbol: str, as_of: date):
    """Return the in-window slice of the pre-indexed news df for `symbol`."""
    df = _NEWS_BY_TICKER.get(symbol.upper())
    if df is None or len(df) == 0:
        return None
    df = df[df["_pub_date"].map(lambda d: d is not None and d <= as_of)]
    if len(df) == 0:
        return None
    return df


def _row_to_chunk(
    row,
    *,
    chunk_prefix: str,
    chunk_seq: int,
    out_ticker: str,
    source_type: SourceType,
    via_symbol: str | None = None,
) -> TextChunk | None:
    pub_date = row["_pub_date"]
    title = str(row.get("title") or "").strip()
    publisher = str(row.get("publisher") or "news").strip() or "news"
    url = str(row.get("link") or "").strip() or None
    paragraphs = _paragraph_texts(row.get("news"))
    body_parts = [title] if title else []
    body_parts.extend(paragraphs)
    body = "\n\n".join(p for p in body_parts if p).strip()
    if not body:
        return None
    pieces = _chunk_text(body)
    if not pieces:
        return None
    chunk_body, tok_count = pieces[0]
    section_name = publisher if via_symbol is None else f"{publisher} (via {via_symbol})"
    return TextChunk(
        chunk_id=f"{chunk_prefix}_{out_ticker}_{pub_date.isoformat()}_article_{chunk_seq:04d}",
        ticker=out_ticker,
        source_type=source_type,
        publication_date=pub_date,
        source_url=url,
        section_name=section_name,
        text=chunk_body,
        token_count=tok_count,
    )


def _news_chunks_for(ticker: str, as_of: date) -> list[TextChunk]:
    df = _news_rows_for(ticker, as_of)
    if df is None:
        return []
    out: list[TextChunk] = []
    for _, row in df.iterrows():
        c = _row_to_chunk(
            row,
            chunk_prefix="news",
            chunk_seq=len(out) + 1,
            out_ticker=ticker.upper(),
            source_type=SourceType.NEWS,
        )
        if c is not None:
            out.append(c)
    return out


def _aggregate_news_chunks_from_symbols(
    focal: str,
    symbols: list[str],
    as_of: date,
    *,
    source_type: SourceType,
    chunk_prefix: str,
) -> list[TextChunk]:
    """Pull news rows for a list of `symbols` and tag the chunks as the
    focal ticker's PEER_NEWS / SECTOR_NEWS evidence.
    """
    seen_links: set[str] = set()
    out: list[TextChunk] = []
    focal_upper = focal.upper()
    for sym in symbols:
        df = _news_rows_for(sym, as_of)
        if df is None:
            continue
        for _, row in df.iterrows():
            link = str(row.get("link") or "").strip()
            if link and link in seen_links:
                continue
            if link:
                seen_links.add(link)
            c = _row_to_chunk(
                row,
                chunk_prefix=chunk_prefix,
                chunk_seq=len(out) + 1,
                out_ticker=focal_upper,
                source_type=source_type,
                via_symbol=sym.upper(),
            )
            if c is not None:
                out.append(c)
    return out


def _peer_chunks_for(ticker: str, as_of: date) -> list[TextChunk]:
    return _aggregate_news_chunks_from_symbols(
        ticker, _PEERS.get(ticker.upper(), []), as_of,
        source_type=SourceType.PEER_NEWS, chunk_prefix="peer_news",
    )


def _sector_chunks_for(ticker: str, as_of: date) -> list[TextChunk]:
    return _aggregate_news_chunks_from_symbols(
        ticker, _SECTOR_PROXIES.get(ticker.upper(), []), as_of,
        source_type=SourceType.SECTOR_NEWS, chunk_prefix="sector_news",
    )


def preload_peer_and_sector_news(focal_tickers: list[str]) -> None:
    """Pre-index peer-ticker + sector-ETF news rows so PEER_NEWS / SECTOR_NEWS
    chunks resolve without a re-scan of the 628MB news parquet per request.
    """
    extra: set[str] = set()
    for t in focal_tickers:
        u = t.upper()
        extra.update(s.upper() for s in _PEERS.get(u, []))
        extra.update(s.upper() for s in _SECTOR_PROXIES.get(u, []))
    if extra:
        preload_news(sorted(extra))


def preload_thirteen_f() -> None:
    """Load pre-fetched 13F TextChunks from the JSONL built by demo/build_13f_chunks.py."""
    if _THIRTEEN_F_BY_TICKER:
        return
    if not _THIRTEEN_F_JSONL.exists():
        return   # No 13F data available yet — chunks_for_real just skips the source.
    with _THIRTEEN_F_JSONL.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            chunk = TextChunk.model_validate(rec)
            _THIRTEEN_F_BY_TICKER.setdefault(chunk.ticker.upper(), []).append(chunk)


def _thirteen_f_chunks_for(ticker: str, as_of: date) -> list[TextChunk]:
    """Return 13F chunks for (ticker, as_of) from the pre-loaded JSONL."""
    pool = _THIRTEEN_F_BY_TICKER.get(ticker.upper(), [])
    if not pool:
        return []
    return [c for c in pool if c.publication_date <= as_of]


def preload_earnings_transcripts(tickers: list[str]) -> None:
    """Load earnings-call transcripts for the focal tickers and index by ticker.

    The full transcripts parquet is ~1.85GB; reading it whole alongside the
    628MB news parquet OOMs the server. We use pyarrow with a pushdown
    `symbol IN (...)` filter so only the focal-ticker rows are materialized.
    """
    import pyarrow.parquet as pq
    from ingestion.earnings_news.transcripts import (
        _ensure_parquet as _ensure_transcripts_parquet,
        _flatten_and_section,
        _paragraphs_as_list,
        _to_date as _ts_to_date,
        _chunk_text as _ts_chunk_text,
    )

    missing = [t.upper() for t in tickers if t.upper() not in _TRANSCRIPTS_BY_TICKER]
    if not missing:
        return

    parquet_path = _ensure_transcripts_parquet()
    table = pq.read_table(
        str(parquet_path),
        filters=[("symbol", "in", missing)],
    )
    df = table.to_pandas()
    del table

    for t in missing:
        _TRANSCRIPTS_BY_TICKER.setdefault(t, [])

    if df.empty:
        return

    df["_pub_date"] = df["report_date"].map(_ts_to_date)

    for _, row in df.iterrows():
        ticker = str(row["symbol"]).upper()
        pub_date = row["_pub_date"]
        if pub_date is None:
            continue
        paragraphs = _paragraphs_as_list(row.get("transcripts"))
        if not paragraphs:
            continue
        sectioned = _flatten_and_section(paragraphs)
        if not sectioned:
            continue

        buckets: list[tuple[str, str]] = []
        cur_section = sectioned[0][0]
        buf: list[str] = []
        for sec, speaker, content in sectioned:
            if sec != cur_section and buf:
                buckets.append((cur_section, "\n\n".join(buf)))
                buf = []
            cur_section = sec
            buf.append(f"[{speaker}]: {content}" if speaker else content)
        if buf:
            buckets.append((cur_section, "\n\n".join(buf)))

        for section_name, section_text in buckets:
            for idx, (chunk_body, tok_count) in enumerate(_ts_chunk_text(section_text), start=1):
                _TRANSCRIPTS_BY_TICKER[ticker].append(TextChunk(
                    chunk_id=(
                        f"earnings_transcript_{ticker}_{pub_date.isoformat()}"
                        f"_{section_name}_{idx:03d}"
                    ),
                    ticker=ticker,
                    source_type=SourceType.EARNINGS_TRANSCRIPT,
                    publication_date=pub_date,
                    period_end=None,
                    source_url=f"https://finance.yahoo.com/quote/{ticker}/history/",
                    section_name=section_name,
                    text=chunk_body,
                    token_count=tok_count,
                ))


def _earnings_chunks_for(ticker: str, as_of: date) -> list[TextChunk]:
    pool = _TRANSCRIPTS_BY_TICKER.get(ticker.upper(), [])
    if not pool:
        return []
    return [c for c in pool if c.publication_date <= as_of]


def chunks_for_real(ticker: str, as_of: date) -> list[TextChunk]:
    """
    Real SEC filings + news + 13F chunks for (ticker, as_of).

    Chunks are *stratified* by source_type: within each source we sort
    recent-first, then we round-robin one chunk per source until all are
    drained. This guarantees every source that has data gets representation
    in the top-N that `model.attribute()` consumes (it cites `chunks[:5]`).

    Pure-recency sort was crowding out SEC + 13F on recent moves where
    daily news dominates the timeline — the toggle would show
    `sec_10k: 94 available` but the evidence panel only ever cited news.
    """
    sec = get_filings_as_of(ticker, as_of)
    news = _news_chunks_for(ticker, as_of)
    thirteen_f = _thirteen_f_chunks_for(ticker, as_of)
    transcripts = _earnings_chunks_for(ticker, as_of)
    peer = _peer_chunks_for(ticker, as_of)
    sector = _sector_chunks_for(ticker, as_of)

    by_type: dict[SourceType, list[TextChunk]] = {}
    for c in sec + news + thirteen_f + transcripts + peer + sector:
        by_type.setdefault(c.source_type, []).append(c)
    for bucket in by_type.values():
        bucket.sort(key=lambda c: c.publication_date, reverse=True)

    interleaved: list[TextChunk] = []
    source_order = list(by_type.keys())
    while any(by_type[t] for t in source_order):
        for t in source_order:
            if by_type[t]:
                interleaved.append(by_type[t].pop(0))
    return interleaved
