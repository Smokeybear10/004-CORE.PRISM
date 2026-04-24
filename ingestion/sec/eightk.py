"""
SEC 8-K ingestion. Non-earnings material-event text feeding the attribution model.

Owner: Thomas (branch: thomas-test). Companion to Sophia's 10-K/10-Q pipeline in
`tenk.py` (another module). Do not modify `ingestion/sec/__init__.py` — shared.

Pip deps used here (merge into requirements.txt; this file does not edit it):
    edgartools>=3.0      # EDGAR client; Company(ticker).get_filings(form="8-K", ...)
    tiktoken>=0.5        # (optional) accurate token counts; word-based fallback if missing

Public API:
    fetch_8ks(tickers, start_date, end_date) -> list[TextChunk]
    get_8ks_as_of(ticker, as_of) -> list[TextChunk]

Item-code filter (keep only these; drop 8-K/A amendments entirely):
    1.01  material definitive agreement
    2.02  results of operations and financial condition (earnings press releases)
    4.02  non-reliance on previously issued financials
    5.02  director / officer departure / appointment / comp
    7.01  Regulation FD disclosure (often guidance)
    8.01  other material events

Foreknowledge firewall:
    get_8ks_as_of MUST filter publication_date <= as_of. Non-negotiable — see
    CLAUDE.md rule #1. The whole backtest is meaningless without this.

Timestamp tradeoff (MVP):
    We use filing.acceptance_datetime's date as publication_date. Strictly, an
    8-K accepted after 16:00 ET should bump publication_date to the next
    trading day (market has no chance to react same-day). For the MVP we use
    the raw acceptance date and keep the full acceptance datetime in metadata
    when available — callers with a trading calendar can shift as needed.

Caching:
    .cache/8k/{TICKER}_{ACCESSION}.json — one file per (ticker, accession).
    Cache hit skips EDGAR entirely on re-runs.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable, Optional

from schema import SourceType, TextChunk

from . import CACHE_DIR, make_chunk_id

# ---------- Constants ----------

CACHE_8K_DIR = CACHE_DIR / "8k"

RELEVANT_ITEM_CODES = frozenset({"1.01", "2.02", "4.02", "5.02", "7.01", "8.01"})

# Set once per process. edgartools requires SEC-style identity for every request.
_SEC_IDENTITY = "Smokeybear10 wiggersincollege@gmail.com"
_identity_set = False

# After-hours cutoff for the (documented) next-trading-day shift. Unused in MVP
# but kept for when we wire in a trading calendar.
_ET_MARKET_CLOSE = time(16, 0)

# Chunking defaults match Sophia's 10-K pipeline so downstream retrieval is uniform.
DEFAULT_TARGET_TOKENS = 800
DEFAULT_OVERLAP_TOKENS = 100


# ---------- SEC identity (lazy) ----------

def _ensure_identity() -> None:
    """Set the EDGAR user-agent identity exactly once per process."""
    global _identity_set
    if _identity_set:
        return
    try:
        from edgar import set_identity

        set_identity(_SEC_IDENTITY)
        _identity_set = True
    except ImportError:
        # Let callers hit the actual import error at the fetch call site so the
        # message is clear. Cache-only code paths don't need edgartools.
        pass


# ---------- Cache helpers ----------

def _cache_path(ticker: str, accession: str) -> Path:
    CACHE_8K_DIR.mkdir(parents=True, exist_ok=True)
    safe_acc = accession.replace("/", "_")
    return CACHE_8K_DIR / f"{ticker.upper()}_{safe_acc}.json"


def _read_cache(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=str, indent=2))


def _iter_cached_records(ticker: str) -> Iterable[dict[str, Any]]:
    if not CACHE_8K_DIR.exists():
        return
    prefix = f"{ticker.upper()}_"
    for p in CACHE_8K_DIR.glob(f"{prefix}*.json"):
        rec = _read_cache(p)
        if rec:
            yield rec


# ---------- HTML / XBRL stripping ----------

def _strip_html(text: str) -> str:
    """Remove HTML/XML tags and XBRL wrappers; collapse whitespace.

    8-K filings from EDGAR come wrapped in XBRL/HTML. `.text()` on edgartools
    sometimes returns the raw document. This produces clean prose suitable
    for the attribution LLM.
    """
    if not text:
        return ""
    try:
        from bs4 import BeautifulSoup
        try:
            soup = BeautifulSoup(text, "lxml")
        except Exception:
            soup = BeautifulSoup(text, "html.parser")
        for bad in soup(["script", "style", "xbrli:context", "xbrli:unit"]):
            bad.decompose()
        cleaned = soup.get_text(separator=" ", strip=True)
    except ImportError:
        cleaned = re.sub(r"<[^>]+>", " ", text)
        cleaned = re.sub(r"&[#\w]+;", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# ---------- Item-code parsing ----------

_ITEM_PATTERN = re.compile(r"(?:item\s*)?(\d{1,2}\.\d{2})", re.IGNORECASE)


def _extract_item_codes(raw: Any) -> list[str]:
    """
    Pull 8-K item codes out of whatever edgartools (or our cache) hands us.

    Accepts:
      - list[str]: ["2.02", "9.01"]
      - str: "Item 2.02, Item 9.01" or "2.02;9.01"
      - anything with .items attribute (some edgar objects expose it)
    Dedupes while preserving first-seen order.
    """
    if raw is None:
        return []

    # edgartools filing objects sometimes expose .items; unwrap.
    if hasattr(raw, "items") and not isinstance(raw, (dict, str, list, tuple)):
        raw = raw.items

    if isinstance(raw, (list, tuple, set)):
        text = " ".join(str(x) for x in raw)
    else:
        text = str(raw)

    seen: list[str] = []
    for match in _ITEM_PATTERN.findall(text):
        if match not in seen:
            seen.append(match)
    return seen


def _item_codes_id_fragment(codes: list[str]) -> str:
    """item2.02 + item9.01 -> 'item202+901' (dots removed, joined by +)."""
    if not codes:
        return "itemNONE"
    cleaned = [c.replace(".", "") for c in codes]
    return "item" + "+".join(cleaned)


def _item_codes_section_name(codes: list[str]) -> str:
    return ", ".join(codes) if codes else "unknown"


def _keep_by_item_code(codes: list[str]) -> bool:
    """Keep the filing if ANY listed item code is in our whitelist."""
    return any(c in RELEVANT_ITEM_CODES for c in codes)


# ---------- Chunking ----------

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
    """
    Return [(chunk_text, token_count), ...]. Uses tiktoken when available;
    otherwise approximates 1 token ~ 0.75 words (rule of thumb for English).
    """
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
        # Fallback: ~0.75 words per token.
        target_words = int(target_tokens * 0.75)
        overlap_words = int(overlap_tokens * 0.75)
        pieces = _word_chunks(text, target_words, overlap_words)
        return [(p, int(len(p.split()) / 0.75)) for p in pieces]


# ---------- Core filing -> TextChunk conversion ----------

def _acceptance_to_publication_date(acceptance: Any) -> date:
    """
    Accept a datetime, date, or ISO string and return a date.

    MVP tradeoff: we do NOT shift to the next trading day for after-hours
    filings. Callers with a market calendar can bump in a post-step.
    """
    if isinstance(acceptance, datetime):
        return acceptance.date()
    if isinstance(acceptance, date):
        return acceptance
    if isinstance(acceptance, str):
        s = acceptance.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s).date()
        except ValueError:
            return date.fromisoformat(s[:10])
    raise ValueError(f"Unrecognized acceptance timestamp: {acceptance!r}")


def _record_to_chunks(record: dict[str, Any]) -> list[TextChunk]:
    """Turn a normalized cached 8-K record into TextChunks."""
    ticker: str = record["ticker"].upper()
    items: list[str] = record.get("items", [])
    body: str = record.get("text", "") or ""
    if not body.strip() or not _keep_by_item_code(items) or record.get("is_amendment"):
        return []

    pub_date = _acceptance_to_publication_date(record["acceptance_datetime"])
    section = _item_codes_section_name(items)
    id_fragment = _item_codes_id_fragment(items)
    url = record.get("source_url")
    period_end_raw = record.get("period_end")
    period_end = (
        date.fromisoformat(period_end_raw)
        if isinstance(period_end_raw, str)
        else period_end_raw
    )

    pieces = _chunk_text(body)
    chunks: list[TextChunk] = []
    for idx, (text, tok_count) in enumerate(pieces, start=1):
        # Custom chunk_id to encode item codes — intentionally does NOT go through
        # make_chunk_id because the standard formatter collapses the section.
        chunk_id = (
            f"{SourceType.SEC_8K.value}_{ticker}_{pub_date.isoformat()}_"
            f"{id_fragment}_{idx:03d}"
        )
        chunks.append(
            TextChunk(
                chunk_id=chunk_id,
                ticker=ticker,
                source_type=SourceType.SEC_8K,
                publication_date=pub_date,
                period_end=period_end,
                source_url=url,
                section_name=section,
                text=text,
                token_count=tok_count,
            )
        )
    return chunks


# ---------- EDGAR normalization ----------

def _filing_to_record(ticker: str, filing: Any) -> Optional[dict[str, Any]]:
    """
    Normalize an edgartools Filing into our cache schema. Returns None if the
    filing is an amendment or has no usable body.
    """
    form = str(getattr(filing, "form", "") or "").upper()
    if "/A" in form:  # drop 8-K/A amendments
        return None

    accession = str(getattr(filing, "accession_no", None) or getattr(filing, "accession_number", ""))
    items = _extract_item_codes(getattr(filing, "items", None))

    # Body extraction:
    #   1. filing.text() + HTML strip — raw document is most complete; we clean it
    #   2. filing.markdown() — alternative if .text() fails
    # We avoid filing.obj().items because that returns only item headers, not body.
    body: str = ""
    txt_fn = getattr(filing, "text", None)
    if callable(txt_fn):
        try:
            body = txt_fn() or ""
        except Exception:
            body = ""

    if not body.strip():
        md_fn = getattr(filing, "markdown", None)
        if callable(md_fn):
            try:
                body = md_fn() or ""
            except Exception:
                body = ""

    # Strip HTML/XBRL. If body is already plain text, this is a no-op.
    body = _strip_html(body)

    # Press-release exhibits (EX-99.1) sometimes hold the real content. If the
    # main document stripped short, try to append exhibit text.
    if len(body) < 500:
        exhibits = getattr(filing, "exhibits", None)
        if exhibits:
            try:
                exhibit_parts = []
                for ex in exhibits:
                    ex_text_fn = getattr(ex, "text", None)
                    if callable(ex_text_fn):
                        try:
                            ex_text = ex_text_fn() or ""
                        except Exception:
                            ex_text = ""
                        ex_text = _strip_html(ex_text)
                        if ex_text:
                            exhibit_parts.append(ex_text)
                if exhibit_parts:
                    body = (body + "\n\n" + "\n\n".join(exhibit_parts)).strip()
            except Exception:
                pass

    if not body.strip():
        return None

    acceptance = (
        getattr(filing, "acceptance_datetime", None)
        or getattr(filing, "filing_date", None)
        or getattr(filing, "date", None)
    )
    if acceptance is None:
        return None

    return {
        "ticker": ticker.upper(),
        "accession_no": accession,
        "form": form or "8-K",
        "items": items,
        "is_amendment": False,
        "acceptance_datetime": acceptance.isoformat() if hasattr(acceptance, "isoformat") else str(acceptance),
        "period_end": _get_period_end(filing),
        "source_url": str(getattr(filing, "filing_url", "") or getattr(filing, "homepage_url", "") or "") or None,
        "text": body.strip(),
    }


def _get_period_end(filing: Any) -> Optional[str]:
    for attr in ("period_of_report", "period", "period_end"):
        v = getattr(filing, attr, None)
        if v:
            return v.isoformat() if hasattr(v, "isoformat") else str(v)[:10]
    return None


# ---------- Public API ----------

def fetch_8ks(
    tickers: list[str],
    start_date: date,
    end_date: date,
) -> list[TextChunk]:
    """
    Download, cache, and chunk 8-K filings for each ticker in [start_date, end_date].

    Cache hits skip EDGAR entirely. Only relevant item codes are emitted.
    8-K/A amendments are dropped (post-hoc, would leak foreknowledge).
    """
    _ensure_identity()
    out: list[TextChunk] = []

    for ticker in tickers:
        tkr = ticker.upper()

        try:
            from edgar import Company
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "edgartools not installed. `pip install edgartools`"
            ) from exc

        try:
            filings = Company(tkr).get_filings(
                form="8-K",
                date=f"{start_date.isoformat()}:{end_date.isoformat()}",
            )
        except Exception as exc:  # network / auth / rate-limit — log and continue
            print(f"[eightk] EDGAR fetch failed for {tkr}: {exc}")
            continue

        # edgartools returns something iterable (Filings or list).
        for filing in filings:
            accession = str(
                getattr(filing, "accession_no", None)
                or getattr(filing, "accession_number", "")
            )
            if not accession:
                continue
            cache_file = _cache_path(tkr, accession)
            record = _read_cache(cache_file)
            if record is None:
                record = _filing_to_record(tkr, filing)
                if record is None:
                    continue
                _write_cache(cache_file, record)
            out.extend(_record_to_chunks(record))

    return out


def get_8ks_as_of(ticker: str, as_of: date) -> list[TextChunk]:
    """
    Return cached 8-K chunks for `ticker` whose publication_date <= as_of.

    Enforces the no-foreknowledge rule (CLAUDE.md #1). Reads only from the
    local cache — caller is expected to have primed it via fetch_8ks.
    """
    chunks: list[TextChunk] = []
    for rec in _iter_cached_records(ticker):
        for chunk in _record_to_chunks(rec):
            if chunk.publication_date <= as_of:
                chunks.append(chunk)
    # Stable ordering for downstream determinism.
    chunks.sort(key=lambda c: (c.publication_date, c.chunk_id))
    return chunks


def filter_chunks_as_of(chunks: list[TextChunk], as_of: date) -> list[TextChunk]:
    """
    Standalone as-of filter. Used in tests and by callers that already hold
    a list of chunks (e.g. fetched via fetch_8ks) and want to re-filter.
    """
    return [c for c in chunks if c.publication_date <= as_of]
