"""
Finnhub-backed news ingester.

Why this exists: the bundled Yahoo Finance news parquet only covers
~2025-03 onwards, so older flagged moves show News=0 / Peer=0 in the UI.
Finnhub's `/company-news` endpoint covers back ~5 years on the free tier,
filling that historical gap.

Opt-in: requires `FINNHUB_API_KEY` in the environment (free signup at
finnhub.io). Without the key, every entry point is a graceful no-op so
the rest of the pipeline is unaffected.

Free-tier limits: 60 requests/min. Our worst case is ~30 tickers × one
window per warm-up = 30 requests, well under the cap. The rate-limit
guard below sleeps and retries once on a 429 anyway.

Public API:
    fetch_finnhub_news(tickers, start_date, end_date)  -> list[TextChunk]
    is_finnhub_available()                              -> bool
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from schema import SourceType, TextChunk


_API_URL = "https://finnhub.io/api/v1/company-news"
_API_KEY_ENV = "FINNHUB_API_KEY"

# Disk cache so re-running build_static / restarting uvicorn doesn't re-hit
# the API for the same (ticker, window). Keyed by ticker; payload is the
# raw response list. Filtering to the requested window happens in-process.
_CACHE_DIR = Path(__file__).parent / ".cache" / "finnhub"


def is_finnhub_available() -> bool:
    return bool(os.environ.get(_API_KEY_ENV, "").strip())


def _cache_path(ticker: str, start: date, end: date) -> Path:
    return _CACHE_DIR / f"{ticker.upper()}_{start.isoformat()}_{end.isoformat()}.json"


def _read_cache(ticker: str, start: date, end: date) -> list | None:
    path = _cache_path(ticker, start, end)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(ticker: str, start: date, end: date, payload: list) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(ticker, start, end).write_text(json.dumps(payload))


def _fetch_one(ticker: str, start: date, end: date, *, api_key: str) -> list:
    """Call /company-news for one ticker + window. Cached to disk."""
    cached = _read_cache(ticker, start, end)
    if cached is not None:
        return cached

    params = urllib.parse.urlencode({
        "symbol": ticker.upper(),
        "from": start.isoformat(),
        "to": end.isoformat(),
        "token": api_key,
    })
    url = f"{_API_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "bw-hackathon-demo/1.0"})

    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status != 200:
                    return []
                data = json.loads(resp.read().decode("utf-8"))
                if not isinstance(data, list):
                    return []
                _write_cache(ticker, start, end, data)
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(15)  # rate-limited; sleep and try once more
                continue
            return []
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return []
    return []


def _to_chunk(
    item: dict,
    *,
    ticker: str,
    source_type: SourceType,
    chunk_prefix: str,
    chunk_seq: int,
    via_symbol: str | None = None,
) -> TextChunk | None:
    """Map a Finnhub article record to a TextChunk."""
    headline = (item.get("headline") or "").strip()
    summary = (item.get("summary") or "").strip()
    if not (headline or summary):
        return None
    body = "\n\n".join(p for p in (headline, summary) if p)
    ts = item.get("datetime")
    if not ts:
        return None
    pub_date = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
    publisher = (item.get("source") or "Finnhub").strip() or "Finnhub"
    section = publisher if via_symbol is None else f"{publisher} (via {via_symbol})"
    url = (item.get("url") or "").strip() or None
    return TextChunk(
        chunk_id=(
            f"{chunk_prefix}_{ticker.upper()}_{pub_date.isoformat()}"
            f"_finnhub_{chunk_seq:04d}"
        ),
        ticker=ticker.upper(),
        source_type=source_type,
        publication_date=pub_date,
        section_name=section,
        source_url=url,
        text=body,
        token_count=len(body.split()),
    )


def _monthly_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Slice [start, end] into ~30-day windows. Finnhub silently truncates
    long-window requests on the free tier (a 5-year request comes back as
    just the last 8 days), so we fetch in monthly slices and union the
    results to actually cover the full ~12-month free-tier reach."""
    from datetime import timedelta
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        win_end = min(cursor + timedelta(days=29), end)
        windows.append((cursor, win_end))
        cursor = win_end + timedelta(days=1)
    return windows


def fetch_finnhub_news(
    tickers: Iterable[str],
    start_date: date,
    end_date: date,
    *,
    out_ticker: str | None = None,
    source_type: SourceType = SourceType.NEWS,
    chunk_prefix: str = "news",
    via_focal: bool = False,
) -> list[TextChunk]:
    """Pull news from Finnhub for `tickers` in [start_date, end_date].

    Internally requests one month at a time because Finnhub's free tier
    silently truncates wide-window requests to the last few days; monthly
    slices each return their full content (within the ~12-month free-tier
    reach). Cache is keyed per (ticker, monthly window) so subsequent
    starts are instant.

    When `out_ticker` is set, every emitted chunk is tagged with that
    ticker (used for peer news where we want chunks attributed to the
    focal ticker but sourced from the peer's news feed). `via_focal=True`
    additionally records the source-ticker in the section_name.

    Returns [] silently when FINNHUB_API_KEY isn't set.
    """
    api_key = os.environ.get(_API_KEY_ENV, "").strip()
    if not api_key or end_date < start_date:
        return []

    out: list[TextChunk] = []
    seen_urls: set[str] = set()
    seen_ids: set[int] = set()
    windows = _monthly_windows(start_date, end_date)
    for sym in tickers:
        sym_upper = sym.upper()
        for win_start, win_end in windows:
            records = _fetch_one(sym_upper, win_start, win_end, api_key=api_key)
            for item in records:
                # Dedup across overlapping windows + repeated articles.
                rid = item.get("id")
                if isinstance(rid, int) and rid in seen_ids:
                    continue
                if isinstance(rid, int):
                    seen_ids.add(rid)
                url = (item.get("url") or "").strip()
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                chunk = _to_chunk(
                    item,
                    ticker=out_ticker or sym_upper,
                    source_type=source_type,
                    chunk_prefix=chunk_prefix,
                    chunk_seq=len(out) + 1,
                    via_symbol=sym_upper if via_focal else None,
                )
                if chunk is not None:
                    out.append(chunk)
    return out
