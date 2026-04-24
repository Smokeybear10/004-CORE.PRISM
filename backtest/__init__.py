"""
Step 6: Fade-or-follow framework + ablation runner.

Owner: shared. The ablation-comparison chart in demo/ lives here conceptually.

Public API:
    - fade_or_follow(attribution, realized_next5_return) -> FadeFollow
    - run_ablation(moves, chunks_by_source, configs) -> dict[str, list[Attribution]]
    - evaluate(attributions, realized_returns) -> BacktestResult

Framework (keep simple - mentor was explicit: don't over-invest here):
    expected = attribution.predicted_return_pct
    realized = actual return on move_date (the same-day move)
    IF move_character == "transient" AND |realized| > |expected| * 1.5:
        signal = "fade"     (we expect reversal, trade opposite direction)
    ELIF move_character == "structural" AND sign(predicted) == sign(realized):
        signal = "lean"     (we expect persistence, trade in the same direction)
    ELSE:
        signal = "neutral"
    Evaluate via next-5-day forward return in the signal's direction.

THE REAL DEMO OUTPUT IS THE ABLATION TABLE. How do hit rate / Sharpe /
coherence change as we add source types? Build that chart in demo/.
"""

from __future__ import annotations

from schema import (
    AblationConfig,
    Attribution,
    BacktestResult,
    FadeFollow,
    PriceMove,
    SourceType,
    TextChunk,
)


# ---------- The 6 canonical ablation configs (demo goldmine) ----------

DEFAULT_ABLATIONS: list[AblationConfig] = [
    AblationConfig(
        name="base_news",
        sources=[SourceType.NEWS],
        description="Company-specific news only. Baseline.",
    ),
    AblationConfig(
        name="+sec",
        sources=[SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_10Q, SourceType.SEC_8K],
        description="Add SEC filings (MD&A, Risk Factors).",
    ),
    AblationConfig(
        name="+earnings",
        sources=[
            SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_10Q, SourceType.SEC_8K,
            SourceType.EARNINGS_TRANSCRIPT,
        ],
        description="Add earnings-call transcripts (prepared + Q&A).",
    ),
    AblationConfig(
        name="+peer_news",
        sources=[
            SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_10Q, SourceType.SEC_8K,
            SourceType.EARNINGS_TRANSCRIPT, SourceType.PEER_NEWS,
        ],
        description="Add news about peer tickers. Cheap additive lever.",
    ),
    AblationConfig(
        name="+sector",
        sources=[
            SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_10Q, SourceType.SEC_8K,
            SourceType.EARNINGS_TRANSCRIPT, SourceType.PEER_NEWS, SourceType.SECTOR_NEWS,
        ],
        description="Add sector-wide news.",
    ),
    AblationConfig(
        name="+macro",
        sources=[
            SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_10Q, SourceType.SEC_8K,
            SourceType.EARNINGS_TRANSCRIPT, SourceType.PEER_NEWS, SourceType.SECTOR_NEWS,
            SourceType.MACRO,
        ],
        description="Full pipeline: all sources including Fed / commodities / geopolitics.",
    ),
]


# ---------- Public API ----------

def fade_or_follow(
    attribution: Attribution,
    realized_return_pct: float,
) -> FadeFollow:
    """
    Given the model's attribution (with predicted_return_pct) and the actual
    same-day realized return, emit lean / fade / neutral.

    TODO: implement the simple rule above. Don't over-engineer.
    """
    raise NotImplementedError("fade_or_follow - implement me")


def run_ablation(
    moves: list[PriceMove],
    chunks_by_source: dict[SourceType, list[TextChunk]],
    configs: list[AblationConfig] = DEFAULT_ABLATIONS,
) -> dict[str, list[Attribution]]:
    """
    For each AblationConfig, filter chunks to config.sources, attribute every
    move, and return a map from config name to the list of Attributions.

    TODO: call model.attribute() per (move, config) pair. Cache outputs -
    each call hits the Claude API. Mentor: cache everything, iterate is expensive.
    """
    raise NotImplementedError("run_ablation - implement me")


def evaluate(
    attributions: list[Attribution],
    realized_next5_returns: dict[str, float],  # key: f"{ticker}_{move_date}"
) -> BacktestResult:
    """
    Per-ablation backtest result. Use `ablation_name` from the attributions as
    the strategy_name so the demo chart can group bars.
    """
    raise NotImplementedError("evaluate - implement me")
