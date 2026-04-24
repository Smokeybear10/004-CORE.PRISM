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
    Emit lean / fade / neutral from an Attribution's move_character plus the
    realized same-day return.

    Rule (matches the docstring above this function's original stub):
        transient  + |realized| materially overshoots expected  → fade
        structural + sign(predicted) == sign(realized)           → lean
        otherwise                                                 → neutral

    Simple and explicit — mentor was clear: don't over-engineer this.
    """
    mc = attribution.move_character
    predicted = attribution.predicted_return_pct

    if mc == "transient":
        # No predicted? Fall back to: any transient big move is a fade candidate.
        if predicted is None:
            return "fade" if abs(realized_return_pct) > 0.02 else "neutral"
        if abs(realized_return_pct) > abs(predicted) * 1.5:
            return "fade"
        return "neutral"

    if mc == "structural":
        if predicted is None:
            return "lean" if realized_return_pct != 0 else "neutral"
        if (predicted > 0 and realized_return_pct > 0) or (predicted < 0 and realized_return_pct < 0):
            return "lean"
        return "neutral"

    # "mixed" or "unclear"
    return "neutral"


def run_ablation(
    moves: list[PriceMove],
    chunks_by_source: dict[SourceType, list[TextChunk]],
    configs: list[AblationConfig] = DEFAULT_ABLATIONS,
) -> dict[str, list[Attribution]]:
    """
    For each AblationConfig, filter chunks to config.sources, attribute every
    move, and return a map from config name to the list of Attributions.

    Delegates to `model.attribute` — which currently ships as a placeholder
    wrapper around `backtest.fixtures.generate_attribution`. Swap the model
    implementation later without touching this function.
    """
    # Import here to avoid a circular import at module load
    from model import attribute

    out: dict[str, list[Attribution]] = {}
    for cfg in configs:
        allowed = set(cfg.sources)
        chunks_for_cfg: list[TextChunk] = []
        for src in allowed:
            chunks_for_cfg.extend(chunks_by_source.get(src, []))
        per_move: list[Attribution] = []
        for mv in moves:
            # No-foreknowledge filter: only chunks published on/before move_date
            visible = [c for c in chunks_for_cfg if c.publication_date <= mv.move_date]
            per_move.append(attribute(mv, visible, cfg))
        out[cfg.name] = per_move
    return out


def evaluate(
    attributions: list[Attribution],
    realized_next5_returns: dict[str, float],  # key: f"{ticker}_{move_date}"
) -> BacktestResult:
    """
    Per-ablation backtest result. Uses `fade_or_follow` + realized forward
    returns to produce a BacktestResult. `strategy_name` is derived from the
    attributions' `ablation_name` so the demo chart can group bars.
    """
    import math

    if not attributions:
        return BacktestResult(
            strategy_name="evaluate:empty",
            n_trades=0, sharpe=0.0, hit_rate=0.0,
            avg_return=0.0, max_drawdown=0.0,
            notes="no attributions provided",
        )

    ablation_name = attributions[0].ablation_name
    strategy_name = f"fade_follow:{ablation_name or 'unlabeled'}"

    pnls: list[float] = []
    for a in attributions:
        key = f"{a.ticker}_{a.move_date}"
        if key not in realized_next5_returns:
            continue
        fwd = realized_next5_returns[key]
        signal = fade_or_follow(a, a.return_pct)
        sign = 1 if a.return_pct > 0 else (-1 if a.return_pct < 0 else 0)
        direction = sign if signal == "lean" else (-sign if signal == "fade" else 0)
        pnls.append(direction * fwd)

    active = [p for p in pnls if p != 0]
    n = len(active)
    if n == 0:
        return BacktestResult(
            strategy_name=strategy_name, ablation_name=ablation_name,
            n_trades=0, sharpe=0.0, hit_rate=0.0,
            avg_return=0.0, max_drawdown=0.0,
            notes="no active trades",
        )

    avg = sum(active) / n
    var = sum((x - avg) ** 2 for x in active) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var) if var > 0 else 0.0
    # Annualize coarsely: horizon 5d → ~50 trades/yr per series
    sharpe = (avg / std * math.sqrt(252 / 5)) if std > 0 else 0.0
    hit = sum(1 for x in active if x > 0) / n

    # Max drawdown on cumulative P&L (order = attribution list order)
    cum = 0.0; peak = 0.0; max_dd = 0.0
    for x in active:
        cum += x
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    return BacktestResult(
        strategy_name=strategy_name, ablation_name=ablation_name,
        n_trades=n, sharpe=sharpe, hit_rate=hit,
        avg_return=avg, max_drawdown=max_dd,
        notes="evaluate(): horizon=5d, SPY-neutralization not applied here — see backtest.pnl for that",
    )
