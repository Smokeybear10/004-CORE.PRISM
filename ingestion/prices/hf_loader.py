"""
Loaders for the Yahoo-Finance parquet tables on Hugging Face.

Source-of-truth priority:
  1. Private repo `BridgewaterAIHackathon/BW-AI-Hackathon` (preferred; curated
     and versioned for this project — see docs/hf_schemas.md).
  2. Public mirror `defeatbeta/yahoo-finance-data` (fallback when the private
     repo is unreachable or the caller isn't authenticated).

Every public function enforces the no-foreknowledge rule (CLAUDE.md rule 1):
data is filtered by `report_date <= as_of` before it leaves this module.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd
import pyarrow.parquet as pq
from huggingface_hub import HfFileSystem

BW_BASE = (
    "datasets/BridgewaterAIHackathon/BW-AI-Hackathon"
    "/Structured_Data/SNE/yahoo-finance-data"
)
DEFEATBETA_BASE = "datasets/defeatbeta/yahoo-finance-data/data"

# Cache lives at repo-root/data/cache — three levels up from this file
# (ingestion/prices/hf_loader.py → ingestion/prices → ingestion → repo root).
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"

_OHLC_COLS = ("open", "close", "high", "low")

# Memoize the resolved HF path per filename so we don't `fs.info()` every read.
_PATH_CACHE: dict[str, str] = {}


def _resolve_hf_path(filename: str, fs: HfFileSystem) -> str:
    """Return a readable HF path for `filename`, preferring the BW repo."""
    if filename in _PATH_CACHE:
        return _PATH_CACHE[filename]
    bw_path = f"{BW_BASE}/{filename}"
    try:
        fs.info(bw_path)
        resolved = bw_path
    except Exception:
        resolved = f"{DEFEATBETA_BASE}/{filename}"
    _PATH_CACHE[filename] = resolved
    return resolved


def _read_hf_parquet(filename: str, filters: list | None) -> pd.DataFrame:
    """Read a parquet from the source-of-truth repo with predicate pushdown."""
    fs = HfFileSystem()
    path = _resolve_hf_path(filename, fs)
    table = pq.read_table(path, filesystem=fs, filters=filters)
    return table.to_pandas()


def _normalize_tickers(tickers: Iterable[str] | None) -> set[str] | None:
    if tickers is None:
        return None
    return {t.upper() for t in tickers}


def load_prices(tickers: list[str] | None, as_of: date) -> pd.DataFrame:
    """
    Load daily OHLCV from stock_prices.parquet, filtered to `as_of`.

    Args:
        tickers: ticker symbols to include, or None for the full universe.
        as_of: inclusive upper bound on the price date (no foreknowledge).

    Returns:
        DataFrame with columns [ticker, date, open, close, high, low, volume].
        `date` is python `date`; OHLC are float64; volume is int64.

    The full-universe snapshot for a given `as_of` is cached at
    data/cache/stock_prices_<as_of>.parquet. Partial (ticker-filtered) reads
    do NOT populate the cache, since caching a subset would poison later
    broader reads.
    """
    want = _normalize_tickers(tickers)
    as_of_str = as_of.isoformat()
    cache_path = CACHE_DIR / f"stock_prices_{as_of_str}.parquet"

    if cache_path.exists():
        df = pd.read_parquet(cache_path)
    else:
        filters = [("report_date", "<=", as_of_str)]
        if want is not None:
            filters.append(("symbol", "in", list(want)))
        df = _read_hf_parquet("stock_prices.parquet", filters)
        df = _tidy_prices(df)
        if want is None:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            df.to_parquet(cache_path, index=False)

    if want is not None:
        df = df[df["ticker"].isin(want)]
    df = df[df["date"] <= as_of]
    return df.reset_index(drop=True)


def _tidy_prices(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={"symbol": "ticker", "report_date": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for col in _OHLC_COLS:
        df[col] = df[col].astype("float64")
    df["volume"] = df["volume"].astype("int64")
    return df[["ticker", "date", *_OHLC_COLS, "volume"]]


def load_splits(ticker: str, as_of: date) -> pd.DataFrame:
    """Load stock splits for `ticker` with ex-date <= `as_of`.

    Returns columns [ticker, date, split_factor] sorted by date.
    """
    as_of_str = as_of.isoformat()
    filters = [
        ("symbol", "=", ticker.upper()),
        ("report_date", "<=", as_of_str),
    ]
    df = _read_hf_parquet("stock_split_events.parquet", filters)
    df = df.rename(columns={"symbol": "ticker", "report_date": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["date"] <= as_of]
    return df.sort_values("date").reset_index(drop=True)[["ticker", "date", "split_factor"]]


def load_dividends(ticker: str, as_of: date) -> pd.DataFrame:
    """Load dividend events for `ticker` with ex-date <= `as_of`.

    Returns columns [ticker, date, amount] (amount float64) sorted by date.
    """
    as_of_str = as_of.isoformat()
    filters = [
        ("symbol", "=", ticker.upper()),
        ("report_date", "<=", as_of_str),
    ]
    df = _read_hf_parquet("stock_dividend_events.parquet", filters)
    df = df.rename(columns={"symbol": "ticker", "report_date": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["amount"] = df["amount"].astype("float64")
    df = df[df["date"] <= as_of]
    return df.sort_values("date").reset_index(drop=True)[["ticker", "date", "amount"]]


def adjust_for_splits(prices_df: pd.DataFrame, splits_df: pd.DataFrame) -> pd.DataFrame:
    """
    Backward-adjust OHLC for splits so historical prices align with the
    post-split share basis. "a:b" means `a` new shares per `b` old shares,
    so pre-split prices are multiplied by b/a for dates strictly before each
    ex-date. Volume is left untouched.

    NOTE: the stock_prices table in both the BW and defeatbeta repos is
    ALREADY split-adjusted at source (verified in build_price_panel.py against
    NVDA 2021-07-20 4:1 and AAPL 2014-06-09 7:1). Do NOT call this on HF
    data — you'll double-adjust. This helper is here for feeds that arrive
    unadjusted (e.g. synthetic fixtures, yfinance raw history).
    """
    if splits_df.empty:
        return prices_df.copy()
    out = prices_df.copy()

    def _factor(s: str) -> float:
        a, b = s.split(":")
        return float(b) / float(a)

    splits = splits_df.assign(factor=splits_df["split_factor"].map(_factor))
    for ticker, group in splits.groupby("ticker"):
        for _, row in group.sort_values("date").iterrows():
            mask = (out["ticker"] == ticker) & (out["date"] < row["date"])
            for col in _OHLC_COLS:
                out.loc[mask, col] *= row["factor"]
    return out
