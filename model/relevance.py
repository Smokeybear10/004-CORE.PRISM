"""
Pre-LLM filtering and weighting of evidence chunks.

Mentor ask: do not let the LLM see every chunk equally. Rank by
relevance / recency / source quality, drop the bottom tier, and pass the
weights into the LLM context so it knows which evidence to trust most.

Scoring (each factor in [0, 1]; composite = product in [0, 1]):

    score = source_quality(chunk) * recency_decay(chunk, move_date)
            * ticker_alignment(chunk, move.ticker)

  - source_quality: prior per SourceType, with a publisher overlay for
    NEWS / PEER_NEWS / SECTOR_NEWS (Reuters/Bloomberg/WSJ > Yahoo Finance
    aggregator > Seeking Alpha / Benzinga).
  - recency_decay: exp(-ln(2) * days_ago / half_life), with a per-source
    half-life (SEC 10-K = 180d, news = 7d, etc.).
  - ticker_alignment: 1.0 for an exact ticker match, 0.6 for peer_news,
    0.4 for sector_news, 0.5 for macro, 0.3 for off-ticker news.

Filter (FilterConfig):
  - Sort chunks by score desc.
  - Keep the top `keep_fraction` (default 75%).
  - Cap at `max_chunks` (default 50 — LLM context budget).
  - Drop anything below `min_score` (default 0.02) — but never return
    fewer than `min_chunks` (default 5) when the pool has that many.

Annotation:
  - `annotate_with_weights` returns copies of the surviving chunks with
    a `[EVIDENCE_WEIGHT <tier> (<score>)]` tag prepended to `text`. The
    chunk_id is preserved so validation of cited chunk_ids still passes.

Escape hatch: setting env var `BW_DISABLE_CHUNK_FILTER=1` makes
filter_and_rank a passthrough that returns every chunk with score 1.0 —
useful for A/B experiments or regression checks.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from schema import PriceMove, SourceType, TextChunk


# ---------- Source quality priors ----------

SOURCE_QUALITY: dict[SourceType, float] = {
    SourceType.SEC_10K:              1.00,
    SourceType.SEC_10Q:              0.95,
    SourceType.EARNINGS_TRANSCRIPT:  0.95,
    SourceType.SEC_8K:               0.90,
    SourceType.MACRO:                0.80,
    SourceType.RESEARCH_13F:         0.80,
    SourceType.THIRTEEN_F:           0.80,
    SourceType.SHORT_INTEREST:       0.75,
    SourceType.INDEX_CHANGE:         0.70,
    SourceType.NEWS:                 0.70,
    SourceType.PEER_NEWS:            0.55,
    SourceType.SECTOR_NEWS:          0.45,
}
DEFAULT_SOURCE_QUALITY = 0.50


# Publisher-level quality overlay. Applied only to news-family chunks
# (NEWS / PEER_NEWS / SECTOR_NEWS) when we can identify the publisher in
# `TextChunk.section_name` — that's where the ingestion stashes the
# publisher field. Unknown publishers get PUBLISHER_UNKNOWN_MULT (gently
# penalized).
PUBLISHER_QUALITY: dict[str, float] = {
    # Tier 1 — wire services + flagship business press
    "reuters":                 1.10,
    "bloomberg":               1.10,
    "wall street journal":     1.10,
    "wsj":                     1.10,
    "financial times":         1.05,
    # Tier 2 — major financial media
    "cnbc":                    1.00,
    "barron's":                1.00,
    "barrons":                 1.00,
    "marketwatch":             1.00,
    "dow jones":               1.00,
    "associated press":        1.00,
    # Tier 3 — aggregators
    "yahoo finance":           0.90,
    "yahoo":                   0.90,
    "investopedia":            0.85,
    # Tier 4 — opinion / SEO-heavy
    "seeking alpha":           0.70,
    "the motley fool":         0.65,
    "motley fool":             0.65,
    "zacks":                   0.70,
    "benzinga":                0.65,
    "insider monkey":          0.55,
    "simply wall st":          0.70,
}
PUBLISHER_UNKNOWN_MULT = 0.85


# Exponential-decay half-life (days) per SourceType.
HALF_LIFE_DAYS: dict[SourceType, float] = {
    SourceType.NEWS:                7.0,
    SourceType.PEER_NEWS:           5.0,
    SourceType.SECTOR_NEWS:         10.0,
    SourceType.EARNINGS_TRANSCRIPT: 60.0,
    SourceType.SEC_10K:             180.0,
    SourceType.SEC_10Q:             90.0,
    SourceType.SEC_8K:               30.0,
    SourceType.MACRO:                21.0,
    SourceType.RESEARCH_13F:         30.0,
    SourceType.THIRTEEN_F:           30.0,
    SourceType.SHORT_INTEREST:       14.0,
    SourceType.INDEX_CHANGE:         30.0,
}
DEFAULT_HALF_LIFE = 14.0


DISABLE_ENV_VAR = "BW_DISABLE_CHUNK_FILTER"


# ---------- Sub-scorers ----------

def _publisher_multiplier(chunk: TextChunk) -> float:
    """Look up the publisher for a news-family chunk; 1.0 for non-news."""
    if chunk.source_type not in (
        SourceType.NEWS, SourceType.PEER_NEWS, SourceType.SECTOR_NEWS,
    ):
        return 1.0
    name = (chunk.section_name or "").strip().lower()
    if not name:
        return PUBLISHER_UNKNOWN_MULT
    for key, mult in PUBLISHER_QUALITY.items():
        if key in name:
            return mult
    return PUBLISHER_UNKNOWN_MULT


def source_quality(chunk: TextChunk) -> float:
    """Per-chunk source-quality score in [0, 1]."""
    base = SOURCE_QUALITY.get(chunk.source_type, DEFAULT_SOURCE_QUALITY)
    return float(min(1.0, base * _publisher_multiplier(chunk)))


def recency_decay(chunk: TextChunk, move_date: date) -> float:
    """exp(-ln(2) * days_ago / half_life). Future-dated chunks score 0.0
    (foreknowledge guard)."""
    half_life = HALF_LIFE_DAYS.get(chunk.source_type, DEFAULT_HALF_LIFE)
    days_ago = (move_date - chunk.publication_date).days
    if days_ago < 0:
        return 0.0
    lam = math.log(2) / half_life
    return float(math.exp(-lam * days_ago))


def ticker_alignment(chunk: TextChunk, move_ticker: str) -> float:
    """1.0 for exact ticker match; discounted for peer / sector / macro / off-ticker."""
    chunk_ticker = (chunk.ticker or "").upper()
    move_ticker = (move_ticker or "").upper()
    if chunk_ticker == move_ticker:
        return 1.0
    if chunk.source_type == SourceType.PEER_NEWS:
        return 0.60
    if chunk.source_type == SourceType.SECTOR_NEWS:
        return 0.40
    if chunk.source_type == SourceType.MACRO:
        return 0.50
    if chunk.source_type in (
        SourceType.SHORT_INTEREST,
        SourceType.THIRTEEN_F,
        SourceType.INDEX_CHANGE,
        SourceType.RESEARCH_13F,
    ):
        return 0.50
    # News / SEC chunk tagged with a different ticker — low relevance.
    return 0.30


def score_chunk(chunk: TextChunk, move: PriceMove) -> float:
    """Composite score in [0, 1]."""
    return (
        source_quality(chunk)
        * recency_decay(chunk, move.move_date)
        * ticker_alignment(chunk, move.ticker)
    )


def score_chunks(
    chunks: Iterable[TextChunk],
    move: PriceMove,
) -> list[tuple[TextChunk, float]]:
    """Return [(chunk, score)] sorted by score descending."""
    scored = [(c, score_chunk(c, move)) for c in chunks]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


# ---------- Filter ----------

@dataclass
class FilterConfig:
    keep_fraction: float = 0.75
    max_chunks: int = 50
    min_chunks: int = 5
    min_score: float = 0.02


DEFAULT_FILTER = FilterConfig()


def filter_and_rank(
    chunks: list[TextChunk],
    move: PriceMove,
    config: FilterConfig = DEFAULT_FILTER,
) -> list[tuple[TextChunk, float]]:
    """Score, sort, cap, and drop low-quality chunks.

    The bottom tier is dropped (keep_fraction); the result is capped at
    max_chunks; anything below min_score is removed AFTER that — except
    the output is never shorter than min(min_chunks, len(chunks)).

    Setting BW_DISABLE_CHUNK_FILTER=1 turns this into a passthrough
    (every chunk returned with score 1.0) so ablation / regression tests
    can measure what filtering is worth.
    """
    if os.environ.get(DISABLE_ENV_VAR, "") == "1":
        return [(c, 1.0) for c in chunks]
    if not chunks:
        return []

    scored = score_chunks(chunks, move)
    n = len(scored)
    min_keep = min(config.min_chunks, n)
    if n <= config.min_chunks:
        return scored

    n_keep_by_fraction = max(min_keep, int(round(n * config.keep_fraction)))
    keep_n = min(config.max_chunks, n_keep_by_fraction)
    top = scored[:keep_n]

    # Apply the min_score floor, but always keep at least min_keep so the
    # LLM isn't starved of context when scores are universally low.
    filtered = [p for p in top if p[1] >= config.min_score]
    if len(filtered) < min_keep:
        filtered = top[:min_keep]
    return filtered


# ---------- Annotation ----------

_WEIGHT_TAG_RE = re.compile(r"^\[EVIDENCE_WEIGHT [^\]]+\]\s*")


def _weight_tier(score: float) -> str:
    if score >= 0.75:
        return "HIGH"
    if score >= 0.40:
        return "MED"
    return "LOW"


def _weight_tag(score: float) -> str:
    return f"[EVIDENCE_WEIGHT {_weight_tier(score)} ({score:.2f})]"


def annotate_with_weights(
    scored: list[tuple[TextChunk, float]],
) -> list[TextChunk]:
    """Return COPIES of each chunk with a weight tag prepended to `text`.

    chunk_id and every other field are preserved — only `text` changes —
    so validate_attribution's "citations reference real chunk_ids" check
    still passes. An existing weight tag on the text is stripped before
    the new one is prepended (idempotent re-annotation).
    """
    out: list[TextChunk] = []
    for chunk, score in scored:
        existing = _WEIGHT_TAG_RE.sub("", chunk.text or "")
        tagged = f"{_weight_tag(score)} {existing}".rstrip()
        out.append(chunk.model_copy(update={"text": tagged}))
    return out
