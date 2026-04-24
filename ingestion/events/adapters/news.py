"""
News adapter: stock_news.parquet rows -> Event + parallel TextChunk per paragraph.

Each row in `stock_news.parquet` is one article. The `news` column is a
list of paragraph records. We fan each paragraph out to a (Event, TextChunk)
pair keyed by `news_{uuid}_p{paragraph_number}` — stable, collision-free.

Input record shape (matching the HF schema — see docs/hf_schemas.md):

    {
        "uuid": "abc123...",
        "related_symbols": "AAPL,MSFT",
        "title": "Apple earnings...",
        "publisher": "Reuters",
        "report_date": "2024-02-02",           # can be str or date
        "type": "STORY",
        "link": "https://...",
        "news": [                               # list of paragraph structs
            {"paragraph_number": 0, "highlight": "...", "paragraph": "..."},
            ...
        ],
    }

A single article may be indexed under multiple tickers in `related_symbols`.
The aggregator handles dedup at the article level so we don't emit the same
paragraph twice across overlapping tickers.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from schema import Event, SourceType, TextChunk


def to_events(records: list[dict[str, Any]]) -> list[Event]:
    """
    Fan each article out to one Event per paragraph, per related ticker.
    The event text is the paragraph content; the article title is prepended
    so the LLM has the headline context.
    """
    events: list[Event] = []
    for row in records:
        pub_date = _coerce_date(row.get("report_date"))
        if pub_date is None:
            continue
        title = (row.get("title") or "").strip()
        uuid = row.get("uuid") or ""
        if not uuid:
            continue

        for ticker in _tickers_from(row):
            for p in row.get("news") or []:
                pn = p.get("paragraph_number")
                if pn is None:
                    continue
                para = (p.get("paragraph") or "").strip()
                if not para:
                    continue
                event_id = f"news_{uuid}_p{pn}"
                text = f"{title} — {para}" if title else para
                events.append(
                    Event(
                        event_id=event_id,
                        ticker=ticker,
                        event_date=pub_date,
                        event_type="news",
                        source=row.get("publisher") or "news",
                        payload_ref=event_id,
                        text=text,
                    )
                )
    return events


def to_chunks(records: list[dict[str, Any]]) -> list[TextChunk]:
    """One TextChunk per (article, ticker, paragraph). Chunk IDs match Events."""
    chunks: list[TextChunk] = []
    for row in records:
        pub_date = _coerce_date(row.get("report_date"))
        if pub_date is None:
            continue
        uuid = row.get("uuid") or ""
        if not uuid:
            continue
        link = row.get("link")

        for ticker in _tickers_from(row):
            for p in row.get("news") or []:
                pn = p.get("paragraph_number")
                if pn is None:
                    continue
                para = (p.get("paragraph") or "").strip()
                if not para:
                    continue
                chunks.append(
                    TextChunk(
                        chunk_id=f"news_{uuid}_p{pn}",
                        ticker=ticker,
                        source_type=SourceType.NEWS,
                        publication_date=pub_date,
                        source_url=link,
                        section_name=f"p{pn}",
                        text=para,
                        token_count=len(para.split()),
                    )
                )
    return chunks


def dedupe_articles(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Drop duplicate articles by hash of (title, report_date, related_symbols).
    First occurrence wins. Preserves input order.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for row in records:
        key = (
            (row.get("title") or "").strip().lower(),
            str(row.get("report_date") or ""),
            (row.get("related_symbols") or "").strip().upper(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


# ---------- internals ----------

def _tickers_from(row: dict[str, Any]) -> list[str]:
    raw = row.get("related_symbols") or ""
    return [t.strip().upper() for t in raw.split(",") if t.strip()]


def _coerce_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    s = str(v)
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None
