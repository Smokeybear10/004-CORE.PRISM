"""
Per-ticker PnL summary baked into the static demo bundle.

Closes the loop on the demo: attribution → verdict → realized $ P&L. For the
loaded ticker, we treat every flagged event as a 5-trading-day trade based on
each strategy's verdict, then surface model vs. the four mandated baselines
(`always_lean`, `always_fade`, `random_attribution`, `sentiment_only`) as
total dollar P&L plus an equity curve.

Reuses backtest.signal / backtest.baselines / backtest.pnl directly so the
demo and the offline backtest share one set of math. The only new code is
plumbing: synthesize a minimal `events_df` from the bundled `moves_payload`
+ `prices_df` that compute_pnl already knows how to consume.

Forward returns here are RAW 5-day (not SPY-excess) — we don't load SPY at
build time. The card surfaces this caveat.
"""
from __future__ import annotations

from datetime import date as date_cls, datetime
from typing import Any, Iterable

import pandas as pd

from backtest.baselines import BASELINES
from backtest.pnl import compute_pnl, equity_curve, summarize, total_pnl
from backtest.signal import Trade, attribution_to_trade
from schema import Attribution, DimensionScore


DEFAULT_NOTIONAL = 10_000.0
DEFAULT_HORIZON = 5
MODEL_STRATEGY = "fundamental_vs_nonfundamental"

# Order matters: model first, then baselines as user-friendly labels.
_BASELINE_LABELS: dict[str, str] = {
    "always_lean":        "Always lean (follow the move)",
    "always_fade":        "Always fade big moves",
    "random_attribution": "Random attribution",
    "sentiment_only":     "Sentiment-only baseline",
}


def _to_date(d: Any) -> date_cls | None:
    """Coerce ISO strings, datetimes, pandas Timestamps, and dates → date."""
    if d is None:
        return None
    if isinstance(d, date_cls) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, pd.Timestamp):
        return d.date()
    if isinstance(d, str):
        try:
            return datetime.fromisoformat(d).date()
        except ValueError:
            return None
    return None


def _placeholder_dim() -> DimensionScore:
    """Minimal DimensionScore that satisfies Pydantic. attribution_to_trade
    only reads `move_character` and `confidence` from Attribution, so the
    dimension contents are inert for PnL purposes."""
    return DimensionScore(
        weight=0.2,
        direction="neutral",
        rationale="placeholder for pnl-summary attribution",
        evidence_chunk_ids=["placeholder"],
    )


def _attribution_from_payload(ticker: str, m: dict) -> Attribution | None:
    """Reconstruct a minimal Attribution from one move record in the bundle."""
    attr = m.get("attribution") or {}
    move_date = _to_date(m.get("move_date"))
    if move_date is None:
        return None
    character = attr.get("character") or "unclear"
    confidence = attr.get("confidence")
    if confidence is None:
        confidence = 0.5
    dim = _placeholder_dim()
    return Attribution(
        ticker=ticker,
        move_date=move_date,
        return_pct=float(m.get("return_pct") or 0.0),
        demand=dim, pricing=dim, competitive=dim,
        management_credibility=dim, macro=dim,
        move_character=character,
        confidence=float(confidence),
        ablation_name="bundled",
        sources_used=[],
        chunks_considered=int(attr.get("chunks_considered") or 0),
        model_notes="reconstructed for pnl summary",
    )


def _build_events_df(
    ticker: str,
    moves_payload: list[dict],
    prices_df: pd.DataFrame,
    horizon: int,
) -> pd.DataFrame:
    """Synthesize the events_df shape compute_pnl expects.

    Drops moves where the price panel doesn't extend `horizon` trading bars
    past the move date — we can't compute realized PnL for those yet.
    """
    if prices_df.empty:
        return pd.DataFrame(columns=[
            "event_id", "ticker", "reaction_return", "reaction_end",
            "fwd_5d", "fwd_5d_excess",
        ])

    # Index prices by date → close for O(1) lookup. The build_static loader
    # produces a `date` column of python date objects (or pandas Timestamps);
    # normalize to date for consistent keys.
    p = prices_df[["date", "close"]].copy()
    p["date_key"] = p["date"].map(_to_date)
    p = p.dropna(subset=["date_key"]).reset_index(drop=True)
    if len(p) == 0:
        return pd.DataFrame(columns=[
            "event_id", "ticker", "reaction_return", "reaction_end",
            "fwd_5d", "fwd_5d_excess",
        ])

    # date → row index; trading-day forward lookup uses iloc on this.
    date_to_idx = {d: i for i, d in enumerate(p["date_key"].tolist())}

    rows: list[dict] = []
    for m in moves_payload:
        d = _to_date(m.get("move_date"))
        if d is None or d not in date_to_idx:
            continue
        i0 = date_to_idx[d]
        i1 = i0 + horizon
        if i1 >= len(p):
            continue
        c0 = float(p["close"].iloc[i0])
        c1 = float(p["close"].iloc[i1])
        if c0 == 0:
            continue
        fwd = (c1 - c0) / c0
        rows.append({
            "event_id": f"{ticker}_{d.isoformat()}",
            "ticker": ticker,
            "reaction_return": float(m.get("return_pct") or 0.0),
            "reaction_end": d,
            "fwd_5d": fwd,
            "fwd_5d_excess": fwd,  # raw (no SPY); use_excess=False at compute time
        })
    return pd.DataFrame(rows)


def _model_trades(
    ticker: str,
    moves_payload: list[dict],
    events_df: pd.DataFrame,
    horizon: int,
) -> list[Trade]:
    """Build trades for our primary fundamental-vs-nonfundamental strategy."""
    valid_ids = set(events_df["event_id"].tolist())
    trades: list[Trade] = []
    for m in moves_payload:
        d = _to_date(m.get("move_date"))
        if d is None:
            continue
        eid = f"{ticker}_{d.isoformat()}"
        if eid not in valid_ids:
            continue
        attr = _attribution_from_payload(ticker, m)
        if attr is None:
            continue
        trades.append(attribution_to_trade(
            attr,
            event_id=eid,
            reaction_return=float(m.get("return_pct") or 0.0),
            exit_horizon_days=horizon,
            strategy=MODEL_STRATEGY,
        ))
    return trades


def _serialize_strategy(
    name: str,
    label: str,
    trades: Iterable[Trade],
    events_df: pd.DataFrame,
    horizon: int,
    notional: float,
) -> dict:
    pnl_df = compute_pnl(trades, events_df, horizon=horizon, use_excess=False)
    summary = summarize(pnl_df, strategy_name=name, horizon_days=horizon)
    total = total_pnl(pnl_df, notional=notional)
    curve = equity_curve(pnl_df, notional=notional, events_df=events_df)
    curve_payload: list[dict] = []
    for _, row in curve.iterrows():
        ed = _to_date(row.get("entry_date"))
        curve_payload.append({
            "date": ed.isoformat() if ed is not None else None,
            "equity": round(float(row["equity"]), 2),
            "pnl_dollars": round(float(row["pnl_dollars"]), 2),
        })
    n_trades = int(summary.n_trades)
    n_wins = int((pnl_df["pnl"] > 0).sum()) if not pnl_df.empty else 0
    return {
        "name": name,
        "label": label,
        "n_trades": n_trades,
        "n_wins": n_wins,
        "total_pnl_dollars": round(float(total), 2),
        "hit_rate": round(float(summary.hit_rate), 4),
        "avg_return_pct": round(float(summary.avg_return), 6),
        "sharpe": round(float(summary.sharpe), 4),
        "equity_curve": curve_payload,
    }


def build_pnl_summary(
    ticker: str,
    moves_payload: list[dict],
    prices_df: pd.DataFrame,
    *,
    notional: float = DEFAULT_NOTIONAL,
    horizon: int = DEFAULT_HORIZON,
) -> dict | None:
    """Compute the per-ticker PnL block written into the static bundle.

    Returns None if there's no usable data (no events, or the price panel
    doesn't extend past any of the moves). Caller is expected to omit the
    `pnl` key from the bundle in that case so the UI hides the card.
    """
    events_df = _build_events_df(ticker, moves_payload, prices_df, horizon)
    if events_df.empty:
        return None

    strategies: list[dict] = []
    strategies.append(_serialize_strategy(
        name="model",
        label=f"Our model ({MODEL_STRATEGY.replace('_', ' ')})",
        trades=_model_trades(ticker, moves_payload, events_df, horizon),
        events_df=events_df,
        horizon=horizon,
        notional=notional,
    ))
    for bname, bfn in BASELINES.items():
        strategies.append(_serialize_strategy(
            name=bname,
            label=_BASELINE_LABELS.get(bname, bname),
            trades=bfn(events_df, horizon=horizon),
            events_df=events_df,
            horizon=horizon,
            notional=notional,
        ))

    return {
        "notional_per_trade": int(notional),
        "horizon_days": horizon,
        "n_events": int(len(events_df)),
        "uses_market_neutral": False,
        "strategies": strategies,
    }
