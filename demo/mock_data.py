"""
Ticker-agnostic mock data for the demo frontend.

The app reads real prices + detects real significant moves off Srilekha's
`ingestion.prices` pipeline. Attributions, however, still come from here
because `model.attribute()` is still a stub. When Step 3 ships, swap
`generate_attribution()` calls in `demo/app.py` for the live pipeline.

Focal set matches PR #7: {ABT, ACU, AIR, AMD, APD}. AMD is the MVP pick —
clearest team intuition and richest peer ecosystem.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Literal

from schema import (
    AblationConfig,
    Attribution,
    BacktestResult,
    DimensionScore,
    SourceType,
    TextChunk,
)

DEFAULT_TICKER = "AMD"

FOCAL_TICKERS: dict[str, dict] = {
    "ABT": {"name": "Abbott Laboratories", "sector": "Healthcare"},
    "ACU": {"name": "Acme United Corporation", "sector": "Consumer Defensive"},
    "AIR": {"name": "AAR Corp", "sector": "Industrials"},
    "AMD": {"name": "Advanced Micro Devices", "sector": "Technology"},
    "APD": {"name": "Air Products and Chemicals", "sector": "Basic Materials"},
}


# ---------- Ablations ----------

ABLATIONS: list[AblationConfig] = [
    AblationConfig(name="base_news", sources=[SourceType.NEWS],
                   description="Company-specific news only. Baseline."),
    AblationConfig(name="+sec",
                   sources=[SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_8K],
                   description="Add SEC 10-K/8-K structural context."),
    AblationConfig(name="+earnings",
                   sources=[SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_8K,
                            SourceType.EARNINGS_TRANSCRIPT],
                   description="Add earnings-call transcript (prepared + Q&A)."),
    AblationConfig(name="+peer_news",
                   sources=[SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_8K,
                            SourceType.EARNINGS_TRANSCRIPT, SourceType.PEER_NEWS],
                   description="Add peer-ticker news."),
    AblationConfig(name="+macro",
                   sources=[SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_8K,
                            SourceType.EARNINGS_TRANSCRIPT, SourceType.PEER_NEWS,
                            SourceType.MACRO],
                   description="Full stack: + Fed / FX / commodities."),
]

ABLATION_BY_NAME = {a.name: a for a in ABLATIONS}


# ---------- Per-ticker sector narratives ----------

_SECTOR_NARRATIVES: dict[str, dict] = {
    "Healthcare": {
        "demand": "Prescription/volume trends in key franchises.",
        "competitive": "Generic entry and payer-driven formulary shifts.",
        "macro": "FX translation on international revenue; rate-sensitive M&A.",
    },
    "Consumer Defensive": {
        "demand": "Unit volume vs. pricing in household staples.",
        "competitive": "Private-label share gains at major retailers.",
        "macro": "Input costs, shipping, and discretionary-spend spillover.",
    },
    "Industrials": {
        "demand": "Backlog growth and defense/commercial mix.",
        "competitive": "MRO market share and aftermarket pricing.",
        "macro": "Fuel costs, freight demand, defense budget cycles.",
    },
    "Technology": {
        "demand": "Data-center vs. client mix; AI accelerator demand.",
        "competitive": "NVDA/INTC positioning; foundry capacity share.",
        "macro": "Dollar strength on Asia revenue; rates on capex.",
    },
    "Basic Materials": {
        "demand": "Industrial-gas volume to semis, electronics, healthcare.",
        "competitive": "Long-term supply contracts + pricing power.",
        "macro": "Energy costs, hydrogen strategy, EU industrial demand.",
    },
}


# ---------- Synthetic chunk pool ----------

def _pool_for(ticker: str, move_date: date) -> list[TextChunk]:
    """Deterministic synthetic chunk pool tied to (ticker, move_date)."""
    meta = FOCAL_TICKERS.get(ticker, {"name": ticker, "sector": "Unknown"})
    name = meta["name"]
    sector = meta["sector"]
    narr = _SECTOR_NARRATIVES.get(sector, {
        "demand": f"{name} demand commentary.",
        "competitive": f"{name} competitive pressure.",
        "macro": f"{name} macro exposure.",
    })
    d = move_date.isoformat()

    chunks: list[TextChunk] = [
        TextChunk(
            chunk_id=f"news_{ticker}_{d}_headline_001",
            ticker=ticker,
            source_type=SourceType.NEWS,
            publication_date=move_date,
            source_url=f"https://example.com/{ticker.lower()}-news-{d}",
            section_name="headline",
            text=(
                f"{name} reports a {sector.lower()}-sector-relevant update on {d}. "
                f"{narr['demand']} Market reaction focuses on the near-term guide "
                f"and comparable disclosures from peers."
            ),
            token_count=40,
        ),
        TextChunk(
            chunk_id=f"sec_8k_{ticker}_{d}_item202_001",
            ticker=ticker,
            source_type=SourceType.SEC_8K,
            publication_date=move_date,
            source_url=f"https://www.sec.gov/mock/{ticker}/{d}",
            section_name="2.02",
            text=(
                f"{name} furnishes results of operations for the quarter ended near {d}. "
                f"Press release emphasizes segment revenue mix and gross-margin drivers; "
                f"guidance language references prior range."
            ),
            token_count=38,
        ),
        TextChunk(
            chunk_id=f"sec_10k_{ticker}_{d}_mda_001",
            ticker=ticker,
            source_type=SourceType.SEC_10K,
            publication_date=move_date,
            section_name="mda",
            source_url=f"https://www.sec.gov/mock/{ticker}/10k-{d}",
            text=(
                f"MD&A discussion (mock): {narr['demand']} "
                f"{narr['competitive']} Risk factors call out macro exposures."
            ),
            token_count=35,
        ),
        TextChunk(
            chunk_id=f"earnings_{ticker}_{d}_qa_001",
            ticker=ticker,
            source_type=SourceType.EARNINGS_TRANSCRIPT,
            publication_date=move_date,
            source_url=f"https://example.com/{ticker.lower()}-transcript-{d}",
            section_name="qa",
            text=(
                f"{name} Q&A (mock): management reiterates strategic priorities, "
                f"addresses the analyst question on {narr['competitive'].lower().rstrip('.')}, "
                f"and reaffirms the guidance framework."
            ),
            token_count=36,
        ),
        TextChunk(
            chunk_id=f"peer_news_{ticker}_{d}_peer_001",
            ticker="_PEER",
            source_type=SourceType.PEER_NEWS,
            publication_date=move_date,
            source_url=f"https://example.com/peer-{sector.lower()}-{d}",
            section_name="headline",
            text=(
                f"Sector peers in {sector} report directionally similar dynamics. "
                f"{narr['competitive']}"
            ),
            token_count=28,
        ),
        TextChunk(
            chunk_id=f"macro_{ticker}_{d}_fx_001",
            ticker="_MACRO",
            source_type=SourceType.MACRO,
            publication_date=move_date,
            source_url=f"https://example.com/macro-{d}",
            section_name="fx",
            text=f"Macro read near {d}: {narr['macro']}",
            token_count=24,
        ),
    ]
    return chunks


def chunks_for(ticker: str, move_date: date) -> list[TextChunk]:
    """Public: deterministic synthetic chunk pool for one (ticker, date)."""
    return _pool_for(ticker, move_date)


# ---------- Deterministic attribution factory ----------

def _seeded_rand(ticker: str, move_date: date, ablation: str, key: str) -> float:
    """Stable pseudo-random float in [0,1) keyed by (ticker, date, ablation, key)."""
    h = hashlib.sha256(f"{ticker}|{move_date.isoformat()}|{ablation}|{key}".encode()).hexdigest()
    return int(h[:12], 16) / 16**12


def _pick_direction(r: float, return_pct: float) -> Literal["positive", "negative", "neutral"]:
    if r < 0.15:
        return "neutral"
    return "negative" if return_pct < 0 else "positive"


def generate_attribution(
    ticker: str,
    move_date: date,
    return_pct: float,
    ablation_name: str,
) -> Attribution:
    """
    Deterministic mock `Attribution` for (ticker, move_date, ablation).

    Every DimensionScore cites a real chunk_id from `chunks_for(ticker, date)`
    so the UI's citation-resolution always succeeds (CLAUDE.md rule #6).
    Confidence climbs as more sources are layered in — the ablation story.
    """
    ab = ABLATION_BY_NAME.get(ablation_name, ABLATIONS[0])
    chunks = _pool_for(ticker, move_date)
    chunks_by_type = {c.source_type: c for c in chunks}

    # Chunks citable under this ablation = those whose source_type is in ab.sources.
    allowed_types = set(ab.sources)

    def _pick_chunk(preferred_types: list[SourceType]) -> list[str]:
        """Pick a cited chunk_id from the first preferred type present in the ablation."""
        for t in preferred_types:
            if t in allowed_types and t in chunks_by_type:
                return [chunks_by_type[t].chunk_id]
        fallback = next((c for c in chunks if c.source_type in allowed_types), chunks[0])
        return [fallback.chunk_id]

    # Confidence ramps: base ~0.45 → +0.08 per additional source.
    confidence_base = 0.42 + 0.08 * (len(ab.sources) - 1)

    # Dimension weights — deterministic from (ticker, date, ablation).
    raw = {
        "demand": _seeded_rand(ticker, move_date, ablation_name, "demand") * 0.4 + 0.1,
        "pricing": _seeded_rand(ticker, move_date, ablation_name, "pricing") * 0.15 + 0.03,
        "competitive": _seeded_rand(ticker, move_date, ablation_name, "competitive") * 0.3 + 0.08,
        "management_credibility": _seeded_rand(ticker, move_date, ablation_name, "mgmt") * 0.25 + 0.05,
        "macro": _seeded_rand(ticker, move_date, ablation_name, "macro") * 0.3 + 0.1,
    }
    total = sum(raw.values())
    weights = {k: v / total for k, v in raw.items()}

    meta = FOCAL_TICKERS.get(ticker, {"name": ticker, "sector": "Unknown"})
    sector = meta["sector"]
    narr = _SECTOR_NARRATIVES.get(sector, {})

    dims = {
        "demand": DimensionScore(
            weight=weights["demand"],
            direction=_pick_direction(weights["demand"], return_pct),
            rationale=narr.get("demand", f"{meta['name']} demand commentary in the window."),
            evidence_chunk_ids=_pick_chunk([SourceType.SEC_8K, SourceType.SEC_10K, SourceType.NEWS]),
        ),
        "pricing": DimensionScore(
            weight=weights["pricing"],
            direction=_pick_direction(weights["pricing"], return_pct),
            rationale="No dominant pricing narrative in this window.",
            evidence_chunk_ids=_pick_chunk([SourceType.EARNINGS_TRANSCRIPT, SourceType.NEWS]),
        ),
        "competitive": DimensionScore(
            weight=weights["competitive"],
            direction=_pick_direction(weights["competitive"], return_pct),
            rationale=narr.get("competitive", "Competitive dynamics per sector norms."),
            evidence_chunk_ids=_pick_chunk([SourceType.PEER_NEWS, SourceType.SEC_10K, SourceType.NEWS]),
        ),
        "management_credibility": DimensionScore(
            weight=weights["management_credibility"],
            direction=_pick_direction(weights["management_credibility"], return_pct),
            rationale="Guidance language held within prior range; no credibility shock.",
            evidence_chunk_ids=_pick_chunk([SourceType.EARNINGS_TRANSCRIPT, SourceType.SEC_8K, SourceType.NEWS]),
        ),
        "macro": DimensionScore(
            weight=weights["macro"],
            direction=_pick_direction(weights["macro"], return_pct),
            rationale=narr.get("macro", "Macro read driven by FX + rates."),
            evidence_chunk_ids=_pick_chunk([SourceType.MACRO, SourceType.SEC_10K, SourceType.NEWS]),
        ),
    }

    # Predicted converges to realized as ablation grows.
    shrinkage = max(0.1, 1.0 - 0.15 * (len(ab.sources) - 1))
    predicted = return_pct * (1.0 - shrinkage) + (_seeded_rand(ticker, move_date, ablation_name, "pred") - 0.5) * 0.01
    move_character = (
        "structural" if abs(weights["demand"] + weights["competitive"] + weights["macro"]) >= 0.6
        else "transient"
    )

    return Attribution(
        ticker=ticker,
        move_date=move_date,
        return_pct=return_pct,
        predicted_return_pct=float(predicted),
        demand=dims["demand"],
        pricing=dims["pricing"],
        competitive=dims["competitive"],
        management_credibility=dims["management_credibility"],
        macro=dims["macro"],
        move_character=move_character,
        confidence=min(0.95, confidence_base + _seeded_rand(ticker, move_date, ablation_name, "conf") * 0.05),
        ablation_name=ablation_name,
        sources_used=ab.sources,
        chunks_considered=len([c for c in chunks if c.source_type in allowed_types]),
        model_notes=(
            f"[MOCK — model.attribute() is still a stub] "
            f"Ablation `{ablation_name}` would consume "
            f"{len([c for c in chunks if c.source_type in allowed_types])} chunk(s). "
            f"Real attribution ships when Step 3 lands."
        ),
    )


# ---------- Backtest (drives the ablation comparison chart) ----------

def sample_backtest_results() -> list[BacktestResult]:
    """Synthetic ablation-level results. Directional only — not from a real backtest."""
    return [
        BacktestResult(strategy_name="base_news", ablation_name="base_news",
                       n_trades=68, sharpe=0.28, hit_rate=0.43, avg_return=0.0018,
                       max_drawdown=-0.082,
                       notes="Misses macro- and peer-driven moves; over-trusts headlines."),
        BacktestResult(strategy_name="+sec", ablation_name="+sec",
                       n_trades=68, sharpe=0.71, hit_rate=0.55, avg_return=0.0049,
                       max_drawdown=-0.063,
                       notes="10-K / 8-K language unlocks structural attribution."),
        BacktestResult(strategy_name="+earnings", ablation_name="+earnings",
                       n_trades=68, sharpe=0.98, hit_rate=0.61, avg_return=0.0070,
                       max_drawdown=-0.054,
                       notes="Transcript Q&A adds management-credibility signal."),
        BacktestResult(strategy_name="+peer_news", ablation_name="+peer_news",
                       n_trades=68, sharpe=1.18, hit_rate=0.69, avg_return=0.0091,
                       max_drawdown=-0.042,
                       notes="Cross-sector peer read distinguishes idiosyncratic moves."),
        BacktestResult(strategy_name="+macro", ablation_name="+macro",
                       n_trades=68, sharpe=1.16, hit_rate=0.70, avg_return=0.0094,
                       max_drawdown=-0.040,
                       notes="Macro closes predicted-vs-realized gap on rate/FX days."),
    ]
