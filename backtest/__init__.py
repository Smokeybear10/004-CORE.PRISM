"""
Step 6: Fade-or-follow framework + ablation runner.

Owner: shared. The ablation-comparison chart in demo/ lives here conceptually.

Public API:
    - fade_or_follow(attribution, realized_return_pct=None) -> FadeFollow
    - run_ablation(moves, chunks_by_source, configs) -> dict[str, list[Attribution]]
    - evaluate(attributions, realized_next5_returns) -> BacktestResult

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

import pandas as pd

from schema import (
    AblationConfig,
    Attribution,
    BacktestResult,
    FadeFollow,
    PriceMove,
    SourceType,
    TextChunk,
)

from backtest.fixtures import generate_attribution
from backtest.pnl import compute_pnl, summarize
from backtest.signal import attribution_to_trade, strategy_fundamental_vs_nonfundamental


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
        name="+sector_news",
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
        description="All sources including Fed / commodities / geopolitics.",
    ),
    AblationConfig(
        name="+positioning",
        sources=[
            SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_10Q, SourceType.SEC_8K,
            SourceType.EARNINGS_TRANSCRIPT, SourceType.PEER_NEWS, SourceType.SECTOR_NEWS,
            SourceType.MACRO, SourceType.RESEARCH_13F,
        ],
        description="Full pipeline + 13F positioning / analyst consensus.",
    ),
]


# ---------- Public API ----------

def fade_or_follow(
    attribution: Attribution,
    realized_return_pct: float | None = None,
) -> FadeFollow:
    """
    Emit lean / fade / neutral based on move_character and, when available,
    the predicted-vs-realized magnitude relationship.

    With no ``predicted_return_pct`` this is the simple character rule
    (``structural -> lean``, ``transient -> fade``); with one, transient
    moves need the realized magnitude to exceed 1.5x predicted to fade,
    and structural moves need matching signs to lean. Mentor asked for
    the simple rule — this adds one magnitude check on top, nothing more.
    """
    if attribution.predicted_return_pct is None:
        return strategy_fundamental_vs_nonfundamental(attribution)

    char = attribution.move_character
    predicted = attribution.predicted_return_pct
    realized = (
        realized_return_pct
        if realized_return_pct is not None
        else attribution.return_pct
    )

    if char == "transient":
        if abs(predicted) > 0 and abs(realized) > 1.5 * abs(predicted):
            return "fade"
        return "neutral"

    if char == "structural":
        if predicted * realized > 0:
            return "lean"
        return "neutral"

    return "neutral"


def run_ablation(
    moves: list[PriceMove],
    chunks_by_source: dict[SourceType, list[TextChunk]],
    configs: list[AblationConfig] = DEFAULT_ABLATIONS,
) -> dict[str, list[Attribution]]:
    """
    For each AblationConfig, filter chunks to ``config.sources`` and produce an
    Attribution per PriceMove.

    Until the real model module is wired, this delegates to the stub classifier
    in ``backtest.fixtures.generate_attribution``. When ``model.attribute()``
    lands, swap the call below — the rest of this function stays the same.

    ``chunks_by_source`` is accepted (and filtered to ``config.sources``) for
    API-shape stability. The stub classifier does not read the chunks; the real
    one will.
    """
    out: dict[str, list[Attribution]] = {}
    for cfg in configs:
        allowed = set(cfg.sources)
        # Filter even though the stub ignores it; lets downstream code assume
        # the contract holds when the real model lands.
        _ = {st: chunks for st, chunks in chunks_by_source.items() if st in allowed}
        attrs: list[Attribution] = []
        for move in moves:
            attrs.append(generate_attribution(
                ticker=move.ticker,
                move_date=move.move_date,
                return_pct=move.return_pct,
                vol_zscore=move.vol_zscore,
                ablation_name=cfg.name,
                sources_used=cfg.sources,
            ))
        out[cfg.name] = attrs
    return out


def evaluate(
    attributions: list[Attribution],
    realized_next5_returns: dict[str, float],
) -> BacktestResult:
    """
    Reduce one ablation's Attributions to a single BacktestResult.

    ``realized_next5_returns`` maps ``f"{ticker}_{move_date:%Y%m%d}"`` to the
    SPY-excess 5d forward return to realize the trade against. Attributions
    are assumed to share an ``ablation_name`` (we take the first as the group
    label). Trades are built via the same path as the full runner:
    ``attribution_to_trade -> compute_pnl -> summarize``.
    """
    if not attributions:
        return BacktestResult(
            strategy_name="struct_fundamental_vs_nonfundamental",
            ablation_name=None,
            n_trades=0, sharpe=0.0, hit_rate=0.0, avg_return=0.0, max_drawdown=0.0,
            notes="no attributions passed to evaluate()",
        )

    ablation_name = attributions[0].ablation_name

    # Build a minimal events frame: compute_pnl only needs event_id +
    # fwd_5d_excess (since use_excess=True by default). The trade's
    # reaction_return comes from attribution.return_pct directly.
    rows = []
    trades = []
    for attr in attributions:
        event_id = f"{attr.ticker}_{attr.move_date.strftime('%Y%m%d')}"
        if event_id not in realized_next5_returns:
            continue
        rows.append({
            "event_id": event_id,
            "fwd_5d_excess": float(realized_next5_returns[event_id]),
            "fwd_5d": float(realized_next5_returns[event_id]),  # compute_pnl handles either
        })
        trades.append(attribution_to_trade(
            attr=attr,
            event_id=event_id,
            reaction_return=float(attr.return_pct),
            exit_horizon_days=5,
        ))

    if not rows:
        return BacktestResult(
            strategy_name="struct_fundamental_vs_nonfundamental",
            ablation_name=ablation_name,
            n_trades=0, sharpe=0.0, hit_rate=0.0, avg_return=0.0, max_drawdown=0.0,
            notes="no attributions matched realized_next5_returns keys",
        )

    events_df = pd.DataFrame(rows)
    pnl_df = compute_pnl(trades, events_df, horizon=5, use_excess=True)
    return summarize(
        pnl_df,
        strategy_name="struct_fundamental_vs_nonfundamental",
        ablation_name=ablation_name,
        horizon_days=5,
    )
