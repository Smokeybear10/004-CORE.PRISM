"""
Short-seller research-report ingestion.

One scraper function per publisher, plus a dispatcher. Each scraper does a
best-effort HTML walk over the publisher's research index, extracts
(title, date, target_ticker, source_url), and returns a list of ShortReport
records. Publisher-specific selectors live with each scraper; shared logic
(generic WordPress archive walk, ticker extraction) is factored out.

If a publisher's structure blocks scraping we emit zero records and log a
warning. Never raise — don't let one publisher's markup break the whole run.

Pipeline mirrors short_interest.py / thirteen_f.py:

    fetch_short_reports(publisher, as_of)     -> list[ShortReport]
    fetch_all_short_reports(as_of)            -> list[ShortReport]
    reports_to_events(reports)                -> list[Event]
    events_to_text_chunks(events)             -> list[TextChunk]
    run_short_reports_pipeline(as_of, ...)    -> (reports, events, chunks)

Ticker extraction (two-step):
  1. $TICKER regex against the title (supports "$NKLA", "$RIVN:", etc.).
  2. Fallback: name → ticker via _load_name_to_ticker() from thirteen_f.py
     (SEC company_tickers.json, canonical ticker map).

as_of rule: publication_date <= as_of, same as every retrieval function.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

from ingestion.idiosyncratic.thirteen_f import (
    _load_name_to_ticker,
    _normalize_name,
)
from schema import Event, ShortReport, SourceType, TextChunk

LOG = logging.getLogger(__name__)

USER_AGENT = "BW-Hackathon henryji327@gmail.com"
DATA_DIR = Path("data/short_reports")

TICKER_TOKEN_RE = re.compile(r"\$([A-Z][A-Z0-9\-.]{0,6})\b")

PUBLISHER_SLUGS = {
    "Hindenburg Research": "hindenburg",
    "Muddy Waters Research": "muddy_waters",
    "Citron Research": "citron",
    "Kerrisdale Capital": "kerrisdale",
    "Spruce Point Capital": "spruce_point",
    "Scorpion Capital": "scorpion",
}

# Publisher config: each tuple is (landing URL, post-link CSS selector,
# date-extraction hint). The scraper uses these for a generic WordPress-ish
# archive walk; publishers that deviate get their own dedicated scraper.
_GENERIC_CONFIG: dict[str, dict] = {
    "Hindenburg Research": {
        "url": "https://hindenburgresearch.com/",
        "link_selector": "article a[href], h2 a[href], h3 a[href]",
    },
    "Muddy Waters Research": {
        "url": "https://muddywatersresearch.com/research/",
        "link_selector": "article a[href], .post a[href]",
    },
    "Citron Research": {
        "url": "https://citronresearch.com/",
        "link_selector": "article a[href], h2 a[href]",
    },
    "Kerrisdale Capital": {
        "url": "https://www.kerrisdalecap.com/category/investments/",
        "link_selector": "article a[href], h2 a[href]",
    },
    "Spruce Point Capital": {
        "url": "https://www.sprucepointcap.com/research/",
        "link_selector": "article a[href], .research-item a[href]",
    },
}

# Scorpion Capital's archive is JavaScript-rendered. requests+bs4 gets an
# empty shell; a Selenium/Playwright scraper is out-of-scope for this
# hackathon. The scraper below makes one attempt and logs a warning.
# Trailing slash matters for urljoin — without it urljoin drops "/reports".
_SCORPION_URL = "https://www.scorpioncapital.com/reports/"


# ---------- Public API ----------

def fetch_short_reports(
    publisher: str,
    as_of: date,
    *,
    session: Optional[requests.Session] = None,
) -> list[ShortReport]:
    """Fetch all reports from one publisher with publication_date <= as_of."""
    scraper = _SCRAPERS.get(publisher)
    if scraper is None:
        raise ValueError(
            f"unknown publisher: {publisher!r}. "
            f"Known publishers: {list(_SCRAPERS)}"
        )
    sess = session or _session()
    try:
        reports = scraper(as_of, sess)
    except Exception as e:  # noqa: BLE001 — per spec, don't fail the whole run
        LOG.warning("scraping %s failed: %s", publisher, e)
        return []
    return [r for r in reports if r.publication_date <= as_of]


def fetch_all_short_reports(
    as_of: date,
    *,
    session: Optional[requests.Session] = None,
) -> list[ShortReport]:
    """Aggregate across every publisher; dedup by chunk_id; newest first."""
    out: dict[str, ShortReport] = {}
    sess = session or _session()
    for publisher in _SCRAPERS:
        for r in fetch_short_reports(publisher, as_of, session=sess):
            out.setdefault(r.chunk_id, r)
    return sorted(out.values(), key=lambda r: r.publication_date, reverse=True)


def reports_to_events(reports: list[ShortReport]) -> list[Event]:
    """Wrap each ShortReport in an Event. event_date = publication_date."""
    events: list[Event] = []
    for r in reports:
        events.append(
            Event(
                event_id=r.chunk_id,
                ticker=r.target_ticker,
                event_date=r.publication_date,
                event_type="short_report",
                source=r.publisher,
                payload_ref=r.chunk_id,
                text=_report_text(r),
            )
        )
    return events


def events_to_text_chunks(events: list[Event]) -> list[TextChunk]:
    """
    Materialize Events as TextChunks.

    SourceType has no SHORT_REPORT value; use NEWS as closest fit. Flag in
    commit message — adding SourceType.SHORT_REPORT requires team sign-off.
    """
    chunks: list[TextChunk] = []
    for e in events:
        if not e.text:
            continue
        chunks.append(
            TextChunk(
                chunk_id=e.event_id,
                ticker=e.ticker,
                source_type=SourceType.NEWS,  # SourceType.SHORT_REPORT not defined
                publication_date=e.event_date,
                source_url=None,
                section_name=e.event_type,
                text=e.text,
                token_count=len(e.text.split()),
            )
        )
    return chunks


def run_short_reports_pipeline(
    as_of: date,
    publisher: Optional[str] = None,
    output_dir: Path | str = DATA_DIR,
    *,
    session: Optional[requests.Session] = None,
) -> tuple[list[ShortReport], list[Event], list[TextChunk]]:
    """End-to-end: scrape (one publisher or all), wrap, chunk, write outputs."""
    if publisher:
        reports = fetch_short_reports(publisher, as_of, session=session)
    else:
        reports = fetch_all_short_reports(as_of, session=session)
    events = reports_to_events(reports)
    chunks = events_to_text_chunks(events)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = as_of.isoformat()
    suffix = f"_{PUBLISHER_SLUGS.get(publisher, 'all')}" if publisher else "_all"

    save_reports_to_parquet(reports, output_dir / f"records{suffix}_{stamp}.parquet")
    _save_events_to_parquet(events, output_dir / f"events{suffix}_{stamp}.parquet")
    _write_chunks_jsonl(chunks, output_dir / f"chunks{suffix}_{stamp}.jsonl")
    return reports, events, chunks


def make_chunk_id(publisher: str, ticker: str, publication_date: date) -> str:
    """Stable chunk_id: short_report_{publisher_slug}_{TICKER}_{YYYY-MM-DD}."""
    slug = PUBLISHER_SLUGS.get(publisher) or _fallback_slug(publisher)
    return f"short_report_{slug}_{ticker.upper()}_{publication_date.isoformat()}"


def extract_ticker(title: str, body: Optional[str] = None) -> Optional[str]:
    """$TICKER regex first, then fall back to SEC name → ticker lookup."""
    for source in (title, body or ""):
        match = TICKER_TOKEN_RE.search(source)
        if match:
            return match.group(1).upper()
    mapping = _load_name_to_ticker_safe()
    if mapping:
        # Try the full title, then progressively shorter prefixes
        candidates = [title]
        if ":" in title:
            candidates.append(title.split(":", 1)[0])
        if "—" in title:
            candidates.append(title.split("—", 1)[0])
        for cand in candidates:
            ticker = mapping.get(_normalize_name(cand))
            if ticker:
                return ticker
    return None


# ---------- I/O helpers ----------

def save_reports_to_parquet(
    reports: list[ShortReport], filepath: Path | str,
) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "chunk_id", "publisher", "target_ticker", "publication_date",
        "title", "thesis_text", "source_url", "token_count",
    ]
    if not reports:
        pd.DataFrame(columns=columns).to_parquet(filepath, index=False)
        return
    df = pd.DataFrame([r.model_dump() for r in reports])
    df["publication_date"] = df["publication_date"].astype(str)
    df[columns].to_parquet(filepath, compression="snappy", index=False)


def _save_events_to_parquet(events: list[Event], filepath: Path | str) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    columns = ["event_id", "ticker", "event_date", "event_type",
               "source", "payload_ref", "text"]
    if not events:
        pd.DataFrame(columns=columns).to_parquet(filepath, index=False)
        return
    df = pd.DataFrame([e.model_dump() for e in events])
    df["event_date"] = df["event_date"].astype(str)
    df[columns].to_parquet(filepath, compression="snappy", index=False)


def _write_chunks_jsonl(chunks: list[TextChunk], filepath: Path | str) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(
        "\n".join(c.model_dump_json() for c in chunks),
        encoding="utf-8",
    )


# ---------- Scrapers ----------

def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _scrape_generic(
    publisher: str, as_of: date, session: requests.Session,
) -> list[ShortReport]:
    """
    Generic scraper for WordPress-ish research indexes. Walks one landing
    page, collects unique article links, extracts title + publication date
    from the URL slug (/YYYY/MM/DD/), extracts the target ticker from the
    title ($TICKER regex or SEC name lookup), and uses the title as the
    thesis snippet. If no ticker is found in the title we fetch the article
    body for a second-chance extraction.

    Reports without a parseable URL date are DROPPED — a chunk_id built
    from `as_of` would drift every run and violate CLAUDE.md rule #3
    (stable IDs). A dated-URL-only policy keeps IDs deterministic at the
    cost of missing some reports on sites that don't embed dates in URLs.
    """
    cfg = _GENERIC_CONFIG[publisher]
    url = cfg["url"]
    selector = cfg["link_selector"]

    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    reports: list[ShortReport] = []
    seen_urls: set[str] = set()
    seen_chunk_ids: set[str] = set()
    for a in soup.select(selector):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        if not href.startswith("http"):
            href = requests.compat.urljoin(url, href)
        if href in seen_urls:
            continue
        seen_urls.add(href)

        title = a.get_text(strip=True) or a.get("title", "") or ""
        if not title or len(title) < 6:  # skip nav links, logos, etc.
            continue

        pub_date = _parse_date_from_url(href)
        if pub_date is None:
            LOG.info("skipping %s (no URL date, chunk_id would drift): %s",
                     publisher, href)
            continue
        if pub_date > as_of:
            continue

        ticker = extract_ticker(title)
        if not ticker:
            # Fetch the article body for a second-chance ticker extraction
            body = _fetch_article_body(href, session)
            ticker = extract_ticker(title, body)
            thesis = (body or title)[:2000]
        else:
            thesis = title

        if not ticker:
            LOG.info("skipping %s (no ticker found): %s", publisher, title)
            continue

        chunk_id = make_chunk_id(publisher, ticker, pub_date)
        if chunk_id in seen_chunk_ids:
            # Two articles same publisher, ticker, day → keep the first only.
            continue
        seen_chunk_ids.add(chunk_id)

        reports.append(
            ShortReport(
                chunk_id=chunk_id,
                publisher=publisher,
                target_ticker=ticker,
                publication_date=pub_date,
                title=title,
                thesis_text=thesis,
                source_url=href,
                token_count=len(thesis.split()),
            )
        )
    return reports


def _scrape_hindenburg(as_of: date, session: requests.Session) -> list[ShortReport]:
    return _scrape_generic("Hindenburg Research", as_of, session)


def _scrape_muddy_waters(as_of: date, session: requests.Session) -> list[ShortReport]:
    return _scrape_generic("Muddy Waters Research", as_of, session)


def _scrape_citron(as_of: date, session: requests.Session) -> list[ShortReport]:
    return _scrape_generic("Citron Research", as_of, session)


def _scrape_kerrisdale(as_of: date, session: requests.Session) -> list[ShortReport]:
    return _scrape_generic("Kerrisdale Capital", as_of, session)


def _scrape_spruce_point(as_of: date, session: requests.Session) -> list[ShortReport]:
    return _scrape_generic("Spruce Point Capital", as_of, session)


def _scrape_scorpion(as_of: date, session: requests.Session) -> list[ShortReport]:
    """
    Scorpion Capital's archive is JavaScript-rendered. requests+bs4 gets an
    empty shell. One best-effort attempt; if the rendered HTML has fewer than
    two links matching the expected shape, log and return [].
    """
    try:
        resp = session.get(_SCORPION_URL, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        anchors = [a for a in soup.select("a[href]")
                   if "report" in (a.get("href") or "").lower()]
        if len(anchors) < 2:
            LOG.warning(
                "Scorpion Capital archive appears JS-rendered; "
                "requests+bs4 returned %d anchors. Skipping.", len(anchors),
            )
            return []
    except Exception as e:  # noqa: BLE001
        LOG.warning("Scorpion Capital scrape failed: %s", e)
        return []
    # If we ever get real HTML, fall back to generic parsing of the anchor set.
    # Same undated-drop rule as _scrape_generic — no as_of fallback, stable IDs.
    reports: list[ShortReport] = []
    seen_chunk_ids: set[str] = set()
    for a in anchors:
        href = a["href"]
        title = a.get_text(strip=True)
        if not title or len(title) < 6:
            continue
        pub_date = _parse_date_from_url(href)
        if pub_date is None or pub_date > as_of:
            continue
        ticker = extract_ticker(title)
        if not ticker:
            continue
        chunk_id = make_chunk_id("Scorpion Capital", ticker, pub_date)
        if chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(chunk_id)
        absolute = href if href.startswith("http") else requests.compat.urljoin(_SCORPION_URL, href)
        reports.append(
            ShortReport(
                chunk_id=chunk_id,
                publisher="Scorpion Capital",
                target_ticker=ticker,
                publication_date=pub_date,
                title=title,
                thesis_text=title,
                source_url=absolute,
                token_count=len(title.split()),
            )
        )
    return reports


_SCRAPERS: dict[str, Callable[[date, requests.Session], list[ShortReport]]] = {
    "Hindenburg Research": _scrape_hindenburg,
    "Muddy Waters Research": _scrape_muddy_waters,
    "Citron Research": _scrape_citron,
    "Kerrisdale Capital": _scrape_kerrisdale,
    "Spruce Point Capital": _scrape_spruce_point,
    "Scorpion Capital": _scrape_scorpion,
}


# ---------- internals ----------

_URL_DATE_RE = re.compile(r"/(20\d{2})/(\d{1,2})(?:/(\d{1,2}))?/")


def _parse_date_from_url(url: str) -> Optional[date]:
    m = _URL_DATE_RE.search(url)
    if not m:
        return None
    year, month, day = m.group(1), m.group(2), m.group(3) or "1"
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _fetch_article_body(url: str, session: requests.Session) -> Optional[str]:
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        article = soup.find("article") or soup.find("main") or soup.body
        if article is None:
            return None
        paragraphs = [p.get_text(" ", strip=True) for p in article.find_all("p")]
        return "\n".join(paragraphs[:10]) or None
    except Exception as e:  # noqa: BLE001
        LOG.info("article fetch failed for %s: %s", url, e)
        return None


_NAME_MAP_CACHE: Optional[dict[str, str]] = None


def _load_name_to_ticker_safe() -> Optional[dict[str, str]]:
    """
    Wrap the SEC name-to-ticker loader. Populate the cache on success only;
    a transient network failure should NOT poison the cache with {} for the
    rest of the process — that would permanently disable the fallback path.
    """
    global _NAME_MAP_CACHE
    if _NAME_MAP_CACHE is not None:
        return _NAME_MAP_CACHE
    try:
        _NAME_MAP_CACHE = _load_name_to_ticker()
        return _NAME_MAP_CACHE
    except Exception as e:  # noqa: BLE001
        LOG.info("SEC name→ticker lookup unavailable: %s", e)
        return None


def _fallback_slug(publisher: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", publisher.lower()).strip("_")


def _report_text(r: ShortReport) -> str:
    return (
        f"{r.publisher} published short report on {r.target_ticker} "
        f"on {r.publication_date.isoformat()}: {r.title}. {r.thesis_text}"
    )
