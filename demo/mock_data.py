"""
Schema-valid synthetic data for the demo app.

The frontend reads from this module today because ingestion/prices,
ingestion/earnings_news, model/, and backtest/ still raise NotImplementedError.
When a real module lands, swap the corresponding factory call in demo/app.py for
the live import — the schema contract stays the same.

The AAPL 2024-11-01 earnings reaction is the frozen-test-case narrative: news
alone predicts a small negative, +sec adds structural weight (China + FX),
+earnings confirms management framing, +peer widens the sector read, +macro
closes the predicted-vs-realized gap. This is the demo story.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from schema import (
    AblationConfig,
    Attribution,
    BacktestResult,
    DimensionScore,
    PriceMove,
    SourceType,
    TextChunk,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
DEFAULT_TICKER = "AAPL"


# ---------- Chunks ----------

def load_fixture_chunks() -> list[TextChunk]:
    """Real fixture chunks shipped with the repo (SEC 10-K + 8-K)."""
    chunks: list[TextChunk] = []
    for name in ("sec_10k_sample.json", "sec_8k_sample.json"):
        path = FIXTURES_DIR / name
        if not path.exists():
            continue
        for row in json.loads(path.read_text()):
            chunks.append(TextChunk(**row))
    return chunks


def synthetic_news_chunks() -> list[TextChunk]:
    """Hand-fabricated news chunks until ingestion/earnings_news/ lands."""
    return [
        TextChunk(
            chunk_id="news_AAPL_2024-10-31_preview_001",
            ticker="AAPL",
            source_type=SourceType.NEWS,
            publication_date=date(2024, 10, 31),
            source_url="https://example.com/aapl-q4-preview",
            section_name="headline",
            text=(
                "Apple Q4 expected to show Services strength as iPhone unit sales stay flat. "
                "Wall Street consensus: $94.3B revenue, $1.60 EPS. Analysts flag Greater China "
                "as the swing factor; Huawei's Mate 60 lineup continues to weigh on AAPL share."
            ),
            token_count=48,
        ),
        TextChunk(
            chunk_id="news_AAPL_2024-11-01_china_002",
            ticker="AAPL",
            source_type=SourceType.NEWS,
            publication_date=date(2024, 11, 1),
            source_url="https://example.com/aapl-china-miss",
            section_name="headline",
            text=(
                "Apple Greater China revenue slipped modestly again, missing bull-case estimates. "
                "Huawei and Xiaomi continue share gains in premium. Management framed the weakness "
                "as macro-led, not structural."
            ),
            token_count=42,
        ),
        TextChunk(
            chunk_id="peer_news_SMSN_2024-10-29_memory_003",
            ticker="_PEER",
            source_type=SourceType.PEER_NEWS,
            publication_date=date(2024, 10, 29),
            source_url="https://example.com/samsung-q3",
            section_name="headline",
            text=(
                "Samsung flagged weaker-than-expected smartphone and memory demand in China, "
                "citing the strong dollar and persistent consumer-electronics softness."
            ),
            token_count=32,
        ),
        TextChunk(
            chunk_id="macro_DXY_2024-10-31_fx_004",
            ticker="_MACRO",
            source_type=SourceType.MACRO,
            publication_date=date(2024, 10, 31),
            source_url="https://example.com/dxy-update",
            section_name="fx",
            text=(
                "Dollar index pushed to a three-month high into the Fed decision window, with "
                "JPY and CNY both weaker. Multinationals with non-USD revenue face translation headwinds."
            ),
            token_count=34,
        ),
    ]


def all_chunks() -> list[TextChunk]:
    return load_fixture_chunks() + synthetic_news_chunks()


# ---------- Moves ----------

def sample_moves() -> list[PriceMove]:
    return [
        PriceMove(
            ticker=DEFAULT_TICKER,
            move_date=date(2024, 8, 15),
            return_pct=-0.015,
            vol_zscore=-1.1,
            volume_zscore=0.8,
            magnitude_rank=0.72,
            is_significant=True,
        ),
        PriceMove(
            ticker=DEFAULT_TICKER,
            move_date=date(2024, 10, 17),
            return_pct=0.028,
            vol_zscore=2.3,
            volume_zscore=1.5,
            magnitude_rank=0.92,
            is_significant=True,
        ),
        PriceMove(
            ticker=DEFAULT_TICKER,
            move_date=date(2024, 11, 1),
            return_pct=-0.021,
            vol_zscore=-2.1,
            volume_zscore=2.4,
            magnitude_rank=0.87,
            is_significant=True,
        ),
    ]


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
                   description="Add peer-ticker news (Samsung, Qualcomm, TSMC)."),
    AblationConfig(name="+macro",
                   sources=[SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_8K,
                            SourceType.EARNINGS_TRANSCRIPT, SourceType.PEER_NEWS,
                            SourceType.MACRO],
                   description="Full stack: + Fed / FX / commodities."),
]


# ---------- Attributions ----------

def _ds(weight: float, direction: str, rationale: str, chunk_ids: list[str]) -> DimensionScore:
    return DimensionScore(
        weight=weight,
        direction=direction,  # type: ignore[arg-type]
        rationale=rationale,
        evidence_chunk_ids=chunk_ids,
    )


def _earnings_day_attribution(ablation: AblationConfig) -> Attribution:
    """The Nov 1, 2024 AAPL earnings-day reaction across ablations."""
    name = ablation.name

    news_chunk = "news_AAPL_2024-11-01_china_002"
    preview_chunk = "news_AAPL_2024-10-31_preview_001"
    mda_chunk = "sec_10k_AAPL_2024-11-01_mda_000"
    risk_chunk = "sec_10k_AAPL_2024-11-01_risk_factors_000"
    earnings_chunk = "sec_8k_AAPL_2024-11-01_item202+901_001"
    guidance_chunk = "sec_8k_AAPL_2024-10-17_item701_001"
    peer_chunk = "peer_news_SMSN_2024-10-29_memory_003"
    macro_chunk = "macro_DXY_2024-10-31_fx_004"

    # Dimension citations escalate as more sources are added.
    if name == "base_news":
        demand = _ds(0.30, "negative", "Preview flagged China as the swing factor.", [preview_chunk])
        pricing = _ds(0.10, "neutral", "No price-point shift cited in the headlines.", [preview_chunk])
        competitive = _ds(0.25, "negative", "Huawei/Xiaomi share gains called out.", [news_chunk])
        mgmt = _ds(0.15, "neutral", "Headlines didn't touch guidance credibility.", [news_chunk])
        macro = _ds(0.20, "negative", "Strong-dollar hints in the preview.", [preview_chunk])
        predicted = -0.005
        confidence = 0.45
        notes = "News only: hints at China softness, small negative predicted."
    elif name == "+sec":
        demand = _ds(0.32, "negative", "MD&A attributes iPhone decline to Greater China.", [mda_chunk, news_chunk])
        pricing = _ds(0.08, "neutral", "No pricing narrative in 10-K MD&A.", [mda_chunk])
        competitive = _ds(0.20, "negative", "Competitive risk language persists.", [risk_chunk, news_chunk])
        mgmt = _ds(0.15, "neutral", "Prior 7.01 guidance stayed intra-range.", [guidance_chunk])
        macro = _ds(0.25, "negative", "10-K risk factors: FX + inflation exposure.", [risk_chunk])
        predicted = -0.018
        confidence = 0.68
        notes = "10-K language supplies structural grounding for China + FX."
    elif name == "+earnings":
        demand = _ds(0.33, "negative", "Earnings release confirms China softness.", [earnings_chunk, mda_chunk])
        pricing = _ds(0.07, "neutral", "No pricing shift in transcript.", [earnings_chunk])
        competitive = _ds(0.20, "negative", "Peer pressure persists.", [news_chunk, risk_chunk])
        mgmt = _ds(0.15, "neutral", "Mgmt framed China as macro-led, guidance stable.", [earnings_chunk, guidance_chunk])
        macro = _ds(0.25, "negative", "FX headwind reiterated on the call.", [risk_chunk, earnings_chunk])
        predicted = -0.023
        confidence = 0.75
        notes = "Transcript Q&A reinforces management framing; confidence lifts."
    elif name == "+peer_news":
        demand = _ds(0.30, "negative", "China weakness echoed at Samsung.", [earnings_chunk, peer_chunk])
        pricing = _ds(0.05, "neutral", "No pricing shift.", [earnings_chunk])
        competitive = _ds(0.25, "negative", "Broader peer weakness, not AAPL-specific.", [peer_chunk, news_chunk])
        mgmt = _ds(0.12, "neutral", "Guidance unchanged; credibility intact.", [guidance_chunk])
        macro = _ds(0.28, "negative", "Strong dollar cited across peers.", [risk_chunk, peer_chunk])
        predicted = -0.025
        confidence = 0.82
        notes = "Peer read: sector-wide pattern, not idiosyncratic AAPL risk."
    else:  # "+macro"
        demand = _ds(0.28, "negative", "China demand weak across peer + AAPL MD&A.", [mda_chunk, peer_chunk])
        pricing = _ds(0.05, "neutral", "No pricing shift.", [earnings_chunk])
        competitive = _ds(0.22, "negative", "Peer share gains continue.", [peer_chunk, news_chunk])
        mgmt = _ds(0.10, "neutral", "Guidance credibility preserved.", [guidance_chunk])
        macro = _ds(0.35, "negative", "DXY three-month high + FX risk factor.", [macro_chunk, risk_chunk])
        predicted = -0.022
        confidence = 0.85
        notes = "Macro closes the predicted-vs-realized gap."

    return Attribution(
        ticker=DEFAULT_TICKER,
        move_date=date(2024, 11, 1),
        return_pct=-0.021,
        predicted_return_pct=predicted,
        demand=demand,
        pricing=pricing,
        competitive=competitive,
        management_credibility=mgmt,
        macro=macro,
        move_character="structural",
        confidence=confidence,
        ablation_name=name,
        sources_used=ablation.sources,
        chunks_considered={"base_news": 2, "+sec": 5, "+earnings": 7, "+peer_news": 10, "+macro": 12}[name],
        model_notes=notes,
    )


def _guidance_day_attribution(ablation: AblationConfig) -> Attribution:
    """Oct 17, 2024 — Apple files a 7.01 FD with guidance; market reacts positively."""
    name = ablation.name
    guidance_chunk = "sec_8k_AAPL_2024-10-17_item701_001"
    preview_chunk = "news_AAPL_2024-10-31_preview_001"

    if name == "base_news":
        confidence, predicted, notes = 0.40, 0.005, "News flow sparse pre-filing; mostly guesses."
        demand_src = [preview_chunk]
    else:
        confidence = {"+sec": 0.72, "+earnings": 0.74, "+peer_news": 0.76, "+macro": 0.77}[name]
        predicted = 0.022
        notes = "7.01 FD disclosure of Q1 guidance lifts expectations; Services double-digit."
        demand_src = [guidance_chunk]

    return Attribution(
        ticker=DEFAULT_TICKER,
        move_date=date(2024, 10, 17),
        return_pct=0.028,
        predicted_return_pct=predicted,
        demand=_ds(0.40, "positive", "Services guided double-digit growth.", demand_src),
        pricing=_ds(0.10, "positive", "GM guide 46-47%, at the top of recent range.",
                    [guidance_chunk] if name != "base_news" else [preview_chunk]),
        competitive=_ds(0.15, "neutral", "Guidance language didn't touch competitors.",
                        [guidance_chunk] if name != "base_news" else [preview_chunk]),
        management_credibility=_ds(0.25, "positive",
                                   "Rare pre-earnings Reg-FD: signals management confidence.",
                                   [guidance_chunk] if name != "base_news" else [preview_chunk]),
        macro=_ds(0.10, "neutral", "No macro commentary in the 8-K.",
                  [guidance_chunk] if name != "base_news" else [preview_chunk]),
        move_character="structural",
        confidence=confidence,
        ablation_name=name,
        sources_used=ablation.sources,
        chunks_considered={"base_news": 1, "+sec": 3, "+earnings": 4, "+peer_news": 5, "+macro": 6}[name],
        model_notes=notes,
    )


def _cfo_transition_attribution(ablation: AblationConfig) -> Attribution:
    """Aug 15, 2024 — CFO transition announced; muted but real reaction."""
    name = ablation.name
    cfo_chunk = "sec_8k_AAPL_2024-08-15_item502_001"
    preview_chunk = "news_AAPL_2024-10-31_preview_001"  # placeholder news chunk

    if name == "base_news":
        confidence, predicted, notes = 0.35, -0.005, "Headlines dominate; no structural read."
        src = [preview_chunk]
    else:
        confidence = {"+sec": 0.62, "+earnings": 0.64, "+peer_news": 0.65, "+macro": 0.66}[name]
        predicted = -0.010
        notes = "Orderly CFO succession: modestly negative but not credibility-breaking."
        src = [cfo_chunk]

    return Attribution(
        ticker=DEFAULT_TICKER,
        move_date=date(2024, 8, 15),
        return_pct=-0.015,
        predicted_return_pct=predicted,
        demand=_ds(0.10, "neutral", "No demand implications.", src),
        pricing=_ds(0.05, "neutral", "Not pricing-related.", src),
        competitive=_ds(0.10, "neutral", "Not a competitive event.", src),
        management_credibility=_ds(0.60, "negative",
                                   "CFO transition: Maestri steps down, Parekh steps in Jan 2025.",
                                   src),
        macro=_ds(0.15, "neutral", "No macro tie.", src),
        move_character="transient",
        confidence=confidence,
        ablation_name=name,
        sources_used=ablation.sources,
        chunks_considered={"base_news": 1, "+sec": 2, "+earnings": 2, "+peer_news": 3, "+macro": 3}[name],
        model_notes=notes,
    )


def sample_attributions() -> list[Attribution]:
    """One Attribution per (move, ablation) pair. This is the grid the UI walks."""
    out: list[Attribution] = []
    for ab in ABLATIONS:
        out.append(_cfo_transition_attribution(ab))
        out.append(_guidance_day_attribution(ab))
        out.append(_earnings_day_attribution(ab))
    return out


# ---------- Backtests ----------

def sample_backtest_results() -> list[BacktestResult]:
    """Synthetic ablation-level results to drive the comparison chart."""
    return [
        BacktestResult(strategy_name="base_news", ablation_name="base_news",
                       n_trades=18, sharpe=0.31, hit_rate=0.44, avg_return=0.0018,
                       max_drawdown=-0.082,
                       notes="Misses macro-driven moves; over-trusts headlines."),
        BacktestResult(strategy_name="+sec", ablation_name="+sec",
                       n_trades=18, sharpe=0.78, hit_rate=0.56, avg_return=0.0051,
                       max_drawdown=-0.061,
                       notes="10-K language unlocks structural attribution."),
        BacktestResult(strategy_name="+earnings", ablation_name="+earnings",
                       n_trades=18, sharpe=1.02, hit_rate=0.64, avg_return=0.0073,
                       max_drawdown=-0.053,
                       notes="Transcript Q&A adds management-credibility signal."),
        BacktestResult(strategy_name="+peer_news", ablation_name="+peer_news",
                       n_trades=18, sharpe=1.21, hit_rate=0.71, avg_return=0.0094,
                       max_drawdown=-0.041,
                       notes="Peer news distinguishes idiosyncratic from sector moves."),
        BacktestResult(strategy_name="+macro", ablation_name="+macro",
                       n_trades=18, sharpe=1.18, hit_rate=0.71, avg_return=0.0096,
                       max_drawdown=-0.040,
                       notes="Macro closes predicted-vs-realized gap."),
    ]
