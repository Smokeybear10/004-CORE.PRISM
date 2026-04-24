"""
Significant-move detector (CLAUDE.md "Definitions" section):

A `PriceMove` fires on a given ticker-date when either:
  1. |1-day return| > 2 × trailing 30-day realized vol, OR
  2. |1-day return| is in the top 5% of the trailing 60-day absolute returns.

Both lookback windows end at `move_date - 1` — no peeking.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from schema import PriceMove


def detect_moves(
    prices: pd.DataFrame,
    lookback_vol: int = 30,
    lookback_rank: int = 60,
) -> list[PriceMove]:
    """
    Detect significant moves in `prices`.

    Args:
        prices: DataFrame with at least [ticker, date, close]. May contain
            multiple tickers; each is evaluated independently.
        lookback_vol: trailing window (in trading days) for realized vol.
        lookback_rank: trailing window for the absolute-return percentile rank.

    Returns:
        List of PriceMove records, in (ticker, date) order.
    """
    required = {"ticker", "date", "close"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"prices is missing required columns: {missing}")

    moves: list[PriceMove] = []
    for ticker, group in prices.sort_values(["ticker", "date"]).groupby("ticker", sort=False):
        g = group.reset_index(drop=True)
        ret = g["close"].pct_change()

        # Trailing vol, ending at t-1 (shift then roll excludes the current row).
        vol = ret.shift(1).rolling(window=lookback_vol, min_periods=lookback_vol).std(ddof=1)

        # Trailing absolute-return rank, also ending at t-1.
        abs_ret = ret.abs().to_numpy()
        rank = np.full(len(g), np.nan)
        for i in range(lookback_rank, len(g)):
            prior = abs_ret[i - lookback_rank : i]
            if np.isnan(prior).any() or np.isnan(abs_ret[i]):
                continue
            rank[i] = float((prior < abs_ret[i]).mean())

        ret_arr = ret.to_numpy()
        vol_arr = vol.to_numpy()
        dates = g["date"].tolist()

        for i in range(len(g)):
            r = ret_arr[i]
            if np.isnan(r):
                continue
            v = vol_arr[i]
            rk = rank[i]
            vol_trigger = (not np.isnan(v)) and v > 0 and abs(r) > 2.0 * v
            rank_trigger = (not np.isnan(rk)) and rk >= 0.95
            if not (vol_trigger or rank_trigger):
                continue
            zscore = float(r / v) if (not np.isnan(v)) and v > 0 else 0.0
            moves.append(
                PriceMove(
                    ticker=ticker,
                    move_date=dates[i],
                    return_pct=float(r),
                    vol_zscore=zscore,
                    magnitude_rank=None if np.isnan(rk) else float(rk),
                )
            )
    return moves
