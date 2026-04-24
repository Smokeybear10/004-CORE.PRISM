"""
Trades + realized forward returns → P&L rows → BacktestResult.

Because `events_focal.parquet` already has `fwd_1d`, `fwd_5d`, `fwd_20d`
(and the SPY-excess versions), running a backtest is mostly a join + sum.

Default exit horizon is 5 trading days per the plan's fade-window spec.
We use the market-neutral (SPY-excess) forward return by default so that
a 'fade all up moves' strategy doesn't just short beta.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from schema import BacktestResult
from backtest.signal import Trade


HORIZON_TO_COL = {
    1:  ("fwd_1d",  "fwd_1d_excess"),
    5:  ("fwd_5d",  "fwd_5d_excess"),
    20: ("fwd_20d", "fwd_20d_excess"),
}


@dataclass
class PnLRow:
    event_id: str
    ticker: str
    action: str
    direction: int
    size: float
    horizon_days: int
    realized_fwd_return: float
    pnl: float
    confidence: float


def compute_pnl(
    trades: Iterable[Trade],
    events_df: pd.DataFrame,
    horizon: int = 5,
    use_excess: bool = True,
) -> pd.DataFrame:
    """
    Join trades to events_df on event_id, compute P&L per trade.

    P&L = direction × size × forward_return. With `use_excess=True` the
    forward return is net of SPY over the same window (market-neutral).

    Returns a DataFrame of PnLRow-shaped records.
    """
    if horizon not in HORIZON_TO_COL:
        raise ValueError(f"horizon must be one of {list(HORIZON_TO_COL)}")
    raw_col, excess_col = HORIZON_TO_COL[horizon]
    ret_col = excess_col if use_excess else raw_col

    ev = events_df.set_index("event_id")[[ret_col]].rename(columns={ret_col: "fwd_return"})

    rows = []
    for t in trades:
        if t.event_id not in ev.index:
            continue
        fwd = ev.at[t.event_id, "fwd_return"]
        if pd.isna(fwd):
            continue
        pnl = float(t.direction * t.size * fwd)
        rows.append(PnLRow(
            event_id=t.event_id,
            ticker=t.ticker,
            action=t.action,
            direction=int(t.direction),
            size=float(t.size),
            horizon_days=horizon,
            realized_fwd_return=float(fwd),
            pnl=pnl,
            confidence=float(t.confidence),
        ).__dict__)

    return pd.DataFrame(rows)


def summarize(
    pnl_df: pd.DataFrame,
    strategy_name: str,
    ablation_name: Optional[str] = None,
    horizon_days: int = 5,
    trading_days_per_year: int = 252,
) -> BacktestResult:
    """
    Reduce a per-trade P&L frame to a BacktestResult.

    Sharpe uses per-trade P&L and annualizes assuming each trade
    occupies ~horizon_days of capital. This is a coarse approximation;
    for overlapping trades you'd want a daily-mark-to-market series.
    """
    active = pnl_df[pnl_df["direction"] != 0]
    n = len(active)
    if n == 0:
        return BacktestResult(
            strategy_name=strategy_name,
            ablation_name=ablation_name,
            n_trades=0, sharpe=0.0, hit_rate=0.0, avg_return=0.0, max_drawdown=0.0,
            notes="zero active trades (all neutral)",
        )

    pnl = active["pnl"].values
    avg = float(pnl.mean())
    std = float(pnl.std(ddof=1)) if n > 1 else float("nan")

    # Coarse annualization: trades-per-year ≈ 252 / horizon_days
    sharpe = float(avg / std * np.sqrt(trading_days_per_year / horizon_days)) if std and std > 0 else 0.0

    hit_rate = float((pnl > 0).mean())

    # Max drawdown on sequential cumulative P&L (chronologically, by event implied order)
    cum = pnl.cumsum()
    running_peak = np.maximum.accumulate(cum)
    drawdown = cum - running_peak
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0

    return BacktestResult(
        strategy_name=strategy_name,
        ablation_name=ablation_name,
        n_trades=int(n),
        sharpe=sharpe,
        hit_rate=hit_rate,
        avg_return=avg,
        max_drawdown=max_dd,
        notes=f"horizon_days={horizon_days}",
    )
