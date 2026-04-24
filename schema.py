"""
Shared data contracts. Every module's inputs and outputs conform to these types.

RULE: Do not modify this file without team sign-off. Downstream modules depend
on field names and types being stable.
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
    NEWS = "news"


# ---------- Text chunks (what ingestion produces) ----------

class TextChunk(BaseModel):
    """
    A single chunk of text from any source. This is the atomic unit the
    attribution model reasons over and cites as evidence.
    """
    chunk_id: str  # stable, e.g. "sec_10k_AAPL_2024-11-01_mda_003"
    ticker: str
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


# ---------- Price moves (what model/detector produces) ----------

class PriceMove(BaseModel):
    ticker: str
    move_date: date
    return_pct: float  # e.g. -0.082 for -8.2%
    vol_zscore: float  # return normalized by trailing vol
    magnitude_rank: Optional[float] = None  # percentile rank in trailing 60d


# ---------- Attribution (what model produces per price move) ----------

class DimensionScore(BaseModel):
    """One of the 5 dimensions, with how much it drove this move."""
    weight: float = Field(ge=0.0, le=1.0)  # normalized weight across all dimensions
    direction: Literal["positive", "negative", "neutral"]
    rationale: str  # one sentence, model's reasoning
    evidence_chunk_ids: list[str]  # MUST be non-empty and reference real chunks


class Attribution(BaseModel):
    """
    The structured output: for a given price move, what drove it across dimensions.
    """
    ticker: str
    move_date: date
    return_pct: float

    demand: DimensionScore
    pricing: DimensionScore
    competitive: DimensionScore
    management_credibility: DimensionScore
    macro: DimensionScore

    # The key classification for "lean or fade?"
    move_character: Literal["structural", "transient", "mixed", "unclear"]
    confidence: float = Field(ge=0.0, le=1.0)

    # For debugging / demo
    chunks_considered: int
    model_notes: Optional[str] = None


# ---------- Backtest results ----------

class BacktestResult(BaseModel):
    strategy_name: str  # e.g. "fade_transient", "lean_structural"
    n_trades: int
    sharpe: float
    hit_rate: float
    avg_return: float
    max_drawdown: float
    notes: Optional[str] = None
