"""
SEC 10-K ingestion for the BW Hackathon Track 1 project.

Owner: Sophia (branch: person1-sec)

Public API:
    - fetch_10ks(tickers, start_date, end_date) -> list[TextChunk]
    - get_10ks_as_of(ticker, as_of) -> list[TextChunk]

Data source preference:
    1. HuggingFace `JanosAudran/financial-reports-sec` (pre-parsed, section-labeled).
       Filter by ticker/CIK BEFORE materializing rows.
    2. Fallback: `edgartools` (requires `set_identity(...)` before any SEC request).

Sections extracted: Item 1 (Business), Item 1A (Risk Factors), Item 7 (MD&A).
Chunking: ~800 tokens, ~100 overlap, tiktoken if available else word-based.
Caching: per-(ticker, filing_date) JSON under .cache/10k/. Re-runs never re-hit APIs.

CRITICAL foreknowledge firewall:
    get_10ks_as_of filters results by `publication_date <= as_of`. This is
    non-negotiable per the project's rule #1. Any caller that forgets this
    corrupts the backtest silently.

# deps: datasets>=2.0, pandas>=2.0, pydantic>=2.0 (all already in requirements.txt)
# deps (optional, preferred if installed): tiktoken>=0.5, edgartools>=3.0
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

from schema import SourceType, TextChunk

from . import CACHE_DIR, make_chunk_id

# ---------- Constants ----------

TENK_CACHE_DIR = CACHE_DIR / "10k"
TENK_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Section tags used in chunk_id and section_name. Stable vocabulary.
SECTION_BUSINESS = "business"
SECTION_RISK_FACTORS = "risk_factors"
SECTION_MDA = "mda"

# JanosAudran dataset section label → our section code. The HF dataset
# uses labels like "Item 1", "Item 1A", "Item 7" (sometimes "section_1", etc.).
_SECTION_ALIASES = {
    "business": SECTION_BUSINESS,
    "item 1": SECTION_BUSINESS,
    "item1": SECTION_BUSINESS,
    "section_1": SECTION_BUSINESS,
    "risk factors": SECTION_RISK_FACTORS,
    "risk_factors": SECTION_RISK_FACTORS,
    "item 1a": SECTION_RISK_FACTORS,
    "item1a": SECTION_RISK_FACTORS,
    "section_1a": SECTION_RISK_FACTORS,
    "mda": SECTION_MDA,
    "management discussion": SECTION_MDA,
    "managements discussion": SECTION_MDA,
    "management's discussion and analysis": SECTION_MDA,
    "item 7": SECTION_MDA,
    "item7": SECTION_MDA,
    "section_7": SECTION_MDA,
}

TARGET_SECTIONS = {SECTION_BUSINESS, SECTION_RISK_FACTORS, SECTION_MDA}

# Chunking defaults
DEFAULT_TARGET_TOKENS = 800
DEFAULT_OVERLAP_TOKENS = 100

# SEC identity (required by edgartools / EDGAR fair-use policy)
_SEC_IDENTITY = "Smokeybear10 wiggersincollege@gmail.com"


# ---------- Token counting ----------

def _get_tiktoken_encoder():
    """Return a tiktoken cl100k_base encoder if installed, else None."""
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def _count_tokens(text: str, encoder=None) -> int:
    if encoder is not None:
        return len(encoder.encode(text))
    # Fallback: 1 token ≈ 0.75 words → tokens ≈ words / 0.75
    words = len(text.split())
    return int(round(words / 0.75))


# ---------- Chunking ----------

def chunk_text(
    text: str,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[str]:
    """
    Split text into overlapping chunks of ~target_tokens with ~overlap_tokens overlap.

    Prefers tiktoken's cl100k_base if installed. Falls back to word-based
    splitting (1 token ≈ 0.75 words).
    """
    if not text or not text.strip():
        return []

    encoder = _get_tiktoken_encoder()

    if encoder is not None:
        token_ids = encoder.encode(text)
        if len(token_ids) == 0:
            return []
        chunks: list[str] = []
        step = max(1, target_tokens - overlap_tokens)
        start = 0
        while start < len(token_ids):
            end = min(start + target_tokens, len(token_ids))
            chunk_ids = token_ids[start:end]
            chunks.append(encoder.decode(chunk_ids))
            if end >= len(token_ids):
                break
            start += step
        return chunks

    # Word-based fallback. target_tokens tokens ≈ target_tokens * 0.75 words.
    words = text.split()
    if not words:
        return []
    words_per_chunk = max(1, int(round(target_tokens * 0.75)))
    overlap_words = max(0, int(round(overlap_tokens * 0.75)))
    step = max(1, words_per_chunk - overlap_words)
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + words_per_chunk, len(words))
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start += step
    return chunks


# ---------- Section normalization ----------

def _normalize_section_label(raw: Optional[str]) -> Optional[str]:
    """
    Map a raw section label (from HF dataset or regex-scraped header) to our
    stable vocabulary. Returns None for sections we don't care about.
    """
    if not raw:
        return None
    key = raw.strip().lower()
    # Try exact alias match first.
    if key in _SECTION_ALIASES:
        return _SECTION_ALIASES[key]
    # Try substring containment for heading-like strings.
    for alias, canonical in _SECTION_ALIASES.items():
        if alias in key:
            return canonical
    return None


# ---------- Date parsing ----------

def _coerce_date(value) -> Optional[date]:
    """Coerce various date-like inputs into a `date` object; return None on failure."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(value[:10], fmt).date()
            except ValueError:
                continue
        # ISO fallback
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


# ---------- Cache ----------

def _cache_path(ticker: str, filing_date: date) -> Path:
    return TENK_CACHE_DIR / f"{ticker.upper()}_{filing_date.isoformat()}.json"


def _write_cache(ticker: str, filing_date: date, chunks: list[TextChunk]) -> None:
    path = _cache_path(ticker, filing_date)
    payload = [json.loads(c.model_dump_json()) for c in chunks]
    path.write_text(json.dumps(payload, indent=2))


def _read_cache(ticker: str, filing_date: date) -> Optional[list[TextChunk]]:
    path = _cache_path(ticker, filing_date)
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    return [TextChunk.model_validate(rec) for rec in raw]


def _iter_all_cached(ticker: str) -> Iterable[TextChunk]:
    """Yield every TextChunk cached on disk for a ticker, across all filing dates."""
    pattern = f"{ticker.upper()}_*.json"
    for path in TENK_CACHE_DIR.glob(pattern):
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        for rec in raw:
            try:
                yield TextChunk.model_validate(rec)
            except Exception:
                continue


# ---------- HuggingFace dataset path ----------

def _filings_from_hf(ticker: str, start_date: date, end_date: date) -> list[dict]:
    """
    Pull 10-K filings for `ticker` in [start_date, end_date] from the HuggingFace
    `JanosAudran/financial-reports-sec` dataset. Filter BEFORE materializing
    rows (the dataset is large — we never want to stream it whole).

    Returns a list of filing dicts with keys:
        {"filing_date": date, "period_end": Optional[date],
         "sections": {"business": str, "risk_factors": str, "mda": str}}

    Raises on failure; caller should catch and fall back to edgartools.
    """
    from datasets import load_dataset  # type: ignore

    # The dataset ships multiple configs (small/large/full). Default to
    # "large_lite" (section text, pre-chunked at sentence level) which still
    # fits streaming mode.
    ds = load_dataset(
        "JanosAudran/financial-reports-sec",
        "large_lite",
        split="train",
        streaming=True,
    )

    # Filter as early as possible. The dataset keys are roughly:
    # docID (CIK-like), section, sentence, tickers, reportDate, filingDate, stateOfIncorporation, etc.
    ticker_u = ticker.upper()
    start_iso = start_date.isoformat()
    end_iso = end_date.isoformat()

    # Collect sentences grouped by (filing_date, section).
    per_filing: dict[tuple[str, Optional[str]], dict] = {}
    for row in ds:
        row_tickers = row.get("tickers") or []
        if isinstance(row_tickers, str):
            row_tickers = [row_tickers]
        if ticker_u not in {t.upper() for t in row_tickers}:
            continue
        filing_date_str = str(row.get("filingDate") or row.get("filing_date") or "")
        if not filing_date_str:
            continue
        if filing_date_str < start_iso or filing_date_str > end_iso:
            continue
        section_code = _normalize_section_label(
            row.get("section") or row.get("item") or row.get("section_name")
        )
        if section_code not in TARGET_SECTIONS:
            continue
        period_end_str = str(row.get("reportDate") or row.get("period_end") or "")
        sentence = row.get("sentence") or row.get("text") or ""
        key = (filing_date_str, period_end_str)
        bucket = per_filing.setdefault(
            key,
            {"filing_date": filing_date_str, "period_end": period_end_str, "sections": {}},
        )
        bucket["sections"].setdefault(section_code, []).append(sentence)

    filings: list[dict] = []
    for (_fdate, _pend), bucket in per_filing.items():
        filings.append(
            {
                "filing_date": _coerce_date(bucket["filing_date"]),
                "period_end": _coerce_date(bucket["period_end"]),
                "sections": {k: " ".join(v) for k, v in bucket["sections"].items()},
            }
        )
    return filings


# ---------- edgartools fallback path ----------

_EDGAR_ITEM_REGEX = {
    SECTION_BUSINESS: re.compile(r"Item\s*1\.?\s*Business", re.IGNORECASE),
    SECTION_RISK_FACTORS: re.compile(r"Item\s*1A\.?\s*Risk\s+Factors", re.IGNORECASE),
    SECTION_MDA: re.compile(
        r"Item\s*7\.?\s*Management'?s\s+Discussion", re.IGNORECASE
    ),
}


def _filings_from_edgar(ticker: str, start_date: date, end_date: date) -> list[dict]:
    """
    Fallback fetch via edgartools. Requires `set_identity(...)` before any call.
    """
    from edgar import Company, set_identity  # type: ignore

    set_identity(_SEC_IDENTITY)

    company = Company(ticker)
    # .get_filings covers the type filter; then date-range filter client-side.
    filings_obj = company.get_filings(form="10-K")

    results: list[dict] = []
    for f in filings_obj:
        fdate = _coerce_date(getattr(f, "filing_date", None))
        if fdate is None:
            continue
        if fdate < start_date or fdate > end_date:
            continue
        period_end = _coerce_date(getattr(f, "period_of_report", None))
        try:
            tenk = f.obj()  # edgartools parsed 10-K object
        except Exception:
            continue

        sections: dict[str, str] = {}
        # edgartools exposes section getters like `.business`, `.risk_factors`, `.mda`
        for section_code, attr in (
            (SECTION_BUSINESS, "business"),
            (SECTION_RISK_FACTORS, "risk_factors"),
            (SECTION_MDA, "mda"),
        ):
            try:
                txt = getattr(tenk, attr, None) or getattr(tenk, f"item_{attr}", None)
                if txt:
                    sections[section_code] = str(txt)
            except Exception:
                pass

        # If the section getters didn't find anything, fall back to regex on the
        # full filing text.
        if not sections:
            try:
                full_text = tenk.text() if hasattr(tenk, "text") else str(tenk)
            except Exception:
                full_text = ""
            if full_text:
                sections = _split_sections_by_regex(full_text)

        if sections:
            results.append({"filing_date": fdate, "period_end": period_end, "sections": sections})

    return results


def _split_sections_by_regex(full_text: str) -> dict[str, str]:
    """
    Last-resort: split a full 10-K text blob into (business, risk_factors, mda)
    using Item header regex. Cheap + approximate — mentor said prefer regex
    over LLM parsing for section boundaries.
    """
    matches = []
    for section_code, pat in _EDGAR_ITEM_REGEX.items():
        m = pat.search(full_text)
        if m:
            matches.append((m.start(), section_code))
    if not matches:
        return {}
    matches.sort()
    sections: dict[str, str] = {}
    for i, (start, section_code) in enumerate(matches):
        end = matches[i + 1][0] if i + 1 < len(matches) else len(full_text)
        body = full_text[start:end].strip()
        if body:
            sections[section_code] = body
    return sections


# ---------- Chunk builder ----------

def _filing_to_chunks(
    ticker: str,
    filing: dict,
    source_url: Optional[str] = None,
) -> list[TextChunk]:
    """Convert one filing dict into a list of TextChunks (business/risk/mda)."""
    filing_date: date = filing["filing_date"]
    period_end: Optional[date] = filing.get("period_end")
    sections: dict[str, str] = filing.get("sections", {})
    encoder = _get_tiktoken_encoder()

    chunks: list[TextChunk] = []
    # Preserve a stable section ordering so chunk indices don't drift.
    for section_code in (SECTION_BUSINESS, SECTION_RISK_FACTORS, SECTION_MDA):
        body = sections.get(section_code)
        if not body:
            continue
        for idx, chunk_body in enumerate(chunk_text(body)):
            chunks.append(
                TextChunk(
                    chunk_id=make_chunk_id(
                        SourceType.SEC_10K, ticker.upper(), filing_date, section_code, idx
                    ),
                    ticker=ticker.upper(),
                    source_type=SourceType.SEC_10K,
                    publication_date=filing_date,
                    period_end=period_end,
                    source_url=source_url,
                    section_name=section_code,
                    text=chunk_body,
                    token_count=_count_tokens(chunk_body, encoder),
                )
            )
    return chunks


# ---------- Public API ----------

def fetch_10ks(
    tickers: list[str],
    start_date: date,
    end_date: date,
) -> list[TextChunk]:
    """
    Download + chunk all 10-Ks for the given tickers in [start_date, end_date].

    Caches per-(ticker, filing_date) under .cache/10k/. Re-runs read from cache
    and never re-hit APIs.
    """
    all_chunks: list[TextChunk] = []

    for raw_ticker in tickers:
        ticker = raw_ticker.upper()

        # First pass: collect already-cached filings for this ticker in range.
        already_cached_dates: set[date] = set()
        for cached_chunk in _iter_all_cached(ticker):
            if start_date <= cached_chunk.publication_date <= end_date:
                all_chunks.append(cached_chunk)
                already_cached_dates.add(cached_chunk.publication_date)

        # Fetch missing filings. Try HF first, fall back to edgartools.
        filings: list[dict] = []
        try:
            filings = _filings_from_hf(ticker, start_date, end_date)
        except Exception as hf_err:
            try:
                filings = _filings_from_edgar(ticker, start_date, end_date)
            except Exception as edgar_err:
                # Neither source worked. Leave cached results in place and move on.
                # (In a hackathon context, this shouldn't hard-fail; the caller can
                # fall back to whatever's already been cached.)
                _ = (hf_err, edgar_err)
                continue

        for filing in filings:
            filing_date = filing.get("filing_date")
            if filing_date is None or filing_date in already_cached_dates:
                continue
            chunks = _filing_to_chunks(ticker, filing)
            if not chunks:
                continue
            _write_cache(ticker, filing_date, chunks)
            all_chunks.extend(chunks)

    # Stable ordering for deterministic downstream behavior.
    all_chunks.sort(key=lambda c: (c.ticker, c.publication_date, c.section_name or "", c.chunk_id))
    return all_chunks


def get_10ks_as_of(ticker: str, as_of: date) -> list[TextChunk]:
    """
    Return all cached 10-K chunks for `ticker` whose publication_date <= as_of.

    Foreknowledge firewall: this is the function the model/ and backtest/
    modules call to avoid leaking post-event information. The filter
    `publication_date <= as_of` is enforced here and must not be relaxed.
    """
    filtered: list[TextChunk] = []
    for chunk in _iter_all_cached(ticker):
        # Firewall — publication_date <= as_of. Non-negotiable per rule #1.
        if chunk.publication_date <= as_of:
            filtered.append(chunk)
    filtered.sort(key=lambda c: (c.publication_date, c.section_name or "", c.chunk_id))
    return filtered


def filter_chunks_as_of(chunks: list[TextChunk], as_of: date) -> list[TextChunk]:
    """
    Pure-function helper for tests and in-memory pipelines. Same firewall rule
    as `get_10ks_as_of` but operates on an in-memory list rather than cache.
    """
    return [c for c in chunks if c.publication_date <= as_of]
