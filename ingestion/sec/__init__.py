"""
SEC filings ingestion.

Owner: Sophia (branch: person1-sec)

Public API that downstream modules rely on:
    - fetch_filings(tickers, start_date, end_date) -> list[TextChunk]
    - get_filings_as_of(ticker, as_of) -> list[TextChunk]

MVP scope: ONE ticker first, last 2-5 years of 10-Ks/10-Qs. Cache aggressively
to .cache/ so re-runs don't re-hit HuggingFace / SEC EDGAR.

CRITICAL: get_filings_as_of enforces the no-foreknowledge rule. It MUST
return only chunks whose publication_date <= as_of.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from schema import TextChunk, SourceType

# Cache location for downloaded filings (gitignored)
CACHE_DIR = Path(__file__).parent / ".cache"


# ---------- Public API ----------

def fetch_filings(
    tickers: list[str],
    start_date: date,
    end_date: date,
    filing_types: Optional[list[SourceType]] = None,
) -> list[TextChunk]:
    """
    Download and chunk SEC filings for the given tickers in the date range.

    TODO: Implement using JanosAudran/financial-reports-sec (recommended) or
    sec-edgar-downloader as a fallback. JanosAudran is pre-processed and
    section-labeled, which saves you parsing 10-Ks by hand.

    Implementation sketch:
      1. Load the JanosAudran dataset filtered by ticker and date range.
      2. For each filing, extract these sections:
         - Item 7: MD&A (most important for attribution)
         - Item 1A: Risk Factors
         - Item 7A: Quantitative disclosures (if present)
      3. Chunk each section to ~800 tokens with ~100 token overlap.
      4. Emit TextChunk records with stable chunk_ids of the form
         "{source_type}_{ticker}_{filing_date}_{section}_{chunk_idx:03d}"
    """
    if filing_types is None:
        filing_types = [SourceType.SEC_10K, SourceType.SEC_10Q]
    raise NotImplementedError("fetch_filings — implement me")


def get_filings_as_of(ticker: str, as_of: date) -> list[TextChunk]:
    """
    Return all SEC text chunks for `ticker` whose publication_date <= as_of.

    This is THE function the model/ and backtest/ modules will call to avoid
    foreknowledge leaks. Filter aggressively.

    TODO: Implement as a query over the local cache built by fetch_filings.
    """
    raise NotImplementedError("get_filings_as_of — implement me")


# ---------- Chunking helpers (fine to share across ingestion modules) ----------

def chunk_text(text: str, target_tokens: int = 800, overlap_tokens: int = 100) -> list[str]:
    """
    Split text into overlapping chunks of approximately target_tokens each.

    TODO: Use tiktoken for accurate token counts. For now, a word-based
    approximation is fine (1 token ≈ 0.75 words).
    """
    raise NotImplementedError("chunk_text — implement me")


def make_chunk_id(
    source_type: SourceType,
    ticker: str,
    publication_date: date,
    section: str,
    index: int,
) -> str:
    """Stable, deterministic chunk ID used as evidence citation."""
    return f"{source_type.value}_{ticker}_{publication_date.isoformat()}_{section}_{index:03d}"
