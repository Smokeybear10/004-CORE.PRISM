"""
Step 1: Price move detection.

Public API:
  - `load_prices`, `load_splits`, `load_dividends`, `adjust_for_splits`
      Data loaders from the private BW-AI-Hackathon HF repo (with fallback
      to the public `defeatbeta/yahoo-finance-data` mirror). See `hf_loader`.
  - `fetch_price_series(ticker, start_date, end_date)` — single-ticker slice.
  - `detect_significant_moves(prices_df, ...)` — flag PriceMoves off a panel.
  - `get_next_n_day_return(ticker, start_date, n=5)` — fade/follow evaluation.
  - Helpers: `trailing_realized_vol`, `volume_zscore`.

Flagging heuristic (CLAUDE.md "Definitions"): a move is significant when
either
  1. |return_pct| > `vol_mult` × trailing 30d realized vol (default 2.0), OR
  2. |return_pct| is in the top 5% of trailing 60d absolute returns.
Both lookback windows end at `move_date - 1` — no peeking.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from schema import PriceMove

from ingestion.prices.hf_loader import (  # re-exported public API
    CACHE_DIR,
    adjust_for_splits,
    load_dividends,
    load_prices,
    load_splits,
)

__all__ = [
    "CACHE_DIR",
    "adjust_for_splits",
    "detect_significant_moves",
    "fetch_price_series",
    "get_next_n_day_return",
    "load_dividends",
    "load_prices",
    "load_splits",
    "trailing_realized_vol",
    "volume_zscore",
]


# ---------- Public API ----------


def fetch_price_series(ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
    """
    Single-ticker OHLCV slice from the HF price panel.

    Returns the same schema as `load_prices`: columns
    [ticker, date, open, close, high, low, volume], with `date >= start_date`
    and `date <= end_date`.
    """
    df = load_prices([ticker], as_of=end_date)
    df = df[df["date"] >= start_date].reset_index(drop=True)
    return df


def detect_significant_moves(
    prices: pd.DataFrame,
    lookback_vol: int = 30,
    lookback_rank: int = 60,
    vol_mult: float = 2.0,
) -> list[PriceMove]:
    """
    Flag significant moves in `prices`.

    Args:
        prices: DataFrame with at least [ticker, date, close]. Multiple
            tickers are evaluated independently.
        lookback_vol: trailing window (trading days) for realized vol.
        lookback_rank: trailing window for absolute-return percentile rank.
        vol_mult: multiplier on trailing vol for the vol trigger.

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
            vol_trigger = (not np.isnan(v)) and v > 0 and abs(r) > vol_mult * v
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


def get_next_n_day_return(ticker: str, start_date: date, n: int = 5) -> float:
    """
    Forward cumulative return over the next `n` TRADING days after `start_date`.
    Used by `backtest/` to evaluate fade-or-follow calls.
    """
    # Pull a generous calendar-day window; we'll slice trading days below.
    lookahead = start_date + timedelta(days=n * 3 + 14)
    df = load_prices([ticker], as_of=lookahead)
    after = df[df["date"] > start_date].head(n)
    if len(after) < n:
        raise ValueError(
            f"only {len(after)} trading days available after {start_date} for {ticker}"
        )
    first_close = float(after["close"].iloc[0])
    last_close = float(after["close"].iloc[-1])
    return last_close / first_close - 1.0


# ---------- Helpers ----------


def trailing_realized_vol(returns: pd.Series, window: int = 30) -> float:
    """Annualized realized vol over the last `window` trading days."""
    if len(returns) < window:
        return float("nan")
    return float(returns.tail(window).std(ddof=1) * np.sqrt(252))


def volume_zscore(volumes: pd.Series, window: int = 30) -> float:
    """Most-recent volume's z-score vs trailing `window`-day mean/std."""
    if len(volumes) < window + 1:
        return float("nan")
    trailing = volumes.iloc[-window - 1 : -1]
    mu = float(trailing.mean())
    sigma = float(trailing.std(ddof=1))
    if sigma == 0:
        return 0.0
    return (float(volumes.iloc[-1]) - mu) / sigma
