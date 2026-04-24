"""
Shared data contracts. Every module's inputs and outputs conform to these types.

RULE: Do not modify this file without posting in team chat first. Downstream
modules depend on field names and types being stable.

Updated 2026-04-24 after mentor meeting to support: peer/macro/sector sources,
ablation runs (additive-testing demo), expected-vs-realized prediction, and
a Step 5 coherence check.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------- Sources ----------

class SourceType(str, Enum):
    SEC_10K = "sec_10k"
    SEC_10Q = "sec_10q"
    SEC_8K = "sec_8k"
    EARNINGS_TRANSCRIPT = "earnings_transcript"
    NEWS = "news"                  # company-specific news (Yahoo Finance, WSJ, CNBC)
    PEER_NEWS = "peer_news"        # news about peer / competitor tickers
    SECTOR_NEWS = "sector_news"    # sector-wide stories
    MACRO = "macro"                # Fed decisions, commodities, geopolitics
    RESEARCH_13F = "research_13f"  # 13F filings, analyst research, open-source notes


# ---------- Text chunks (what ingestion produces) ----------

class TextChunk(BaseModel):
    """
    A single chunk of text from any source. This is the atomic unit the
    attribution model reasons over and cites as evidence.
    """
    chunk_id: str  # stable, e.g. "sec_10k_AAPL_2024-11-01_mda_003"
    ticker: str    # for MACRO/SECTOR chunks, use a placeholder like "_MACRO" or sector symbol
    source_type: SourceType

    # Dates matter. publication_date is when the market saw it.
    publication_date: date  # filing date for SEC, event date for news, call date for earnings
    period_end: Optional[date] = None  # fiscal period end, if applicable

    # Provenance
    source_url: Optional[str] = None
    section_name: Optional[str] = None  # e.g. "mda", "risk_factors", "qa" (earnings Q&A)

    # Content
    text: str
    token_count: Optional[int] = None


# ---------- Price moves (Step 1) ----------

class PriceMove(BaseModel):
    ticker: str
    move_date: date
    return_pct: float       # e.g. -0.082 for -8.2%
    vol_zscore: float       # return normalized by trailing 30d realized vol
    volume_zscore: Optional[float] = None  # trading volume vs trailing 30d avg
    magnitude_rank: Optional[float] = None  # percentile rank in trailing 60d
    is_significant: bool = False  # passed the flagging threshold (size + vol + volume)


# ---------- Ablation (additive-testing demo) ----------

class AblationConfig(BaseModel):
    """
    A single run configuration for additive testing. Mentor called these
    out as 'demo gold' — e.g. base=news_only, +sec, +peer, +macro, +sector.
    Each run produces Attributions; compare them side-by-side in demo/.
    """
    name: str                     # e.g. "base_news", "+sec_10k", "+peer_news", "+macro"
    sources: list[SourceType]
    description: Optional[str] = None


# ---------- Attribution (Step 3 + 4 output) ----------

class DimensionScore(BaseModel):
    """One of the 5 dimensions, with how much it drove this move."""
    weight: float = Field(ge=0.0, le=1.0)  # normalized weight across all dimensions
    direction: Literal["positive", "negative", "neutral"]
    rationale: str                          # one sentence, model's reasoning
    evidence_chunk_ids: list[str]           # MUST be non-empty and reference real chunks


class Attribution(BaseModel):
    """
    The structured output: for a given price move, what drove it across
    dimensions, AND what the model thought the move *should* have been.
    """
    ticker: str
    move_date: date
    return_pct: float                        # actual realized return
    predicted_return_pct: Optional[float] = None  # what model expected given evidence

    demand: DimensionScore
    pricing: DimensionScore
    competitive: DimensionScore
    management_credibility: DimensionScore
    macro: DimensionScore

    # Fade-or-follow classification (Step 6 reads this)
    move_character: Literal["structural", "transient", "mixed", "unclear"]
    confidence: float = Field(ge=0.0, le=1.0)

    # Which ablation run produced this, for side-by-side demo comparisons
    ablation_name: Optional[str] = None
    sources_used: list[SourceType] = Field(default_factory=list)

    # For debugging / demo
    chunks_considered: int
    model_notes: Optional[str] = None


# ---------- Coherence evaluation (Step 5) ----------

class CoherenceCheck(BaseModel):
    """
    Step 5 output: is the attribution's reasoning plausible? Catches things
    like 'crude oil moved Apple stock'. Run before touching any trading
    logic — mentor called this the mid-pipeline validation gate.
    """
    ticker: str
    move_date: date
    ablation_name: Optional[str] = None
    plausible: bool
    issues: list[str] = Field(default_factory=list)  # human-readable failures
    reviewer_notes: Optional[str] = None


# ---------- Fade-or-follow + Backtest (Step 6) ----------

FadeFollow = Literal["lean", "fade", "neutral"]


class BacktestResult(BaseModel):
    strategy_name: str            # e.g. "fade_transient", "lean_structural"
    ablation_name: Optional[str] = None  # which data-source config produced the signals
    n_trades: int
    sharpe: float
    hit_rate: float
    avg_return: float
    max_drawdown: float
    notes: Optional[str] = None
