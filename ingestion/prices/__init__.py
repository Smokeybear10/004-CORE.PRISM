"""
Step 1: Price move detection.

Owner: Srilekha (branch: person2-yahoo)

Public API downstream modules rely on:
    - detect_significant_moves(ticker, start_date, end_date) -> list[PriceMove]
    - fetch_price_series(ticker, start, end) -> pd.DataFrame  (date-indexed, close + volume)
    - get_next_n_day_return(ticker, start_date, n=5) -> float  (for fade/follow evaluation)

MVP scope: ONE ticker first, last 2-5 years. Cache yfinance output to .cache/
so re-runs don't re-hit the API.

Flagging heuristic (mentor: pick ONE and stick with it - don't polish Step 1):
    A move is "significant" if ANY of:
      - |return_pct| > vol_mult * trailing_30d_realized_vol  (default vol_mult=2.0)
      - |return_pct| in top 5% of trailing 60d absolute returns
      - volume_zscore > 3.0  (volume spike even on small move)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from schema import PriceMove

CACHE_DIR = Path(__file__).parent / ".cache"


# ---------- Public API ----------

def detect_significant_moves(
    ticker: str,
    start_date: date,
    end_date: date,
    vol_mult: float = 2.0,
) -> list[PriceMove]:
    """
    Return PriceMove records for every flagged day in [start_date, end_date].
    Every returned move must have is_significant=True. Non-flagged days are
    omitted from the output.

    TODO: Implement using yfinance. Cache raw OHLCV to .cache/{ticker}.parquet.
    """
    raise NotImplementedError("detect_significant_moves - implement me")


def fetch_price_series(ticker: str, start_date: date, end_date: date):
    """
    Return a pandas DataFrame indexed by date with columns: open, high, low,
    close, adj_close, volume. Cached to .cache/{ticker}.parquet.

    TODO: yfinance.Ticker(ticker).history(start=..., end=...).
    """
    raise NotImplementedError("fetch_price_series - implement me")


def get_next_n_day_return(ticker: str, start_date: date, n: int = 5) -> float:
    """
    Forward cumulative return over the next n TRADING days after start_date.
    Used by backtest/ to evaluate fade-or-follow calls.

    TODO: read from cached series; skip weekends/holidays.
    """
    raise NotImplementedError("get_next_n_day_return - implement me")


# ---------- Helpers ----------

def trailing_realized_vol(returns, window: int = 30) -> float:
    """Annualized realized vol over the last `window` trading days."""
    raise NotImplementedError


def volume_zscore(volumes, window: int = 30) -> float:
    """Most-recent volume's z-score vs trailing `window`-day mean/std."""
    raise NotImplementedError
