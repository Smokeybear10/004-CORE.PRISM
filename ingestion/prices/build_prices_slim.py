"""
Produce prices_slim.parquet: a GitHub-committable slice of prices.parquet.

Keeps only what the downstream pipeline (earnings_events → reactions →
events → events_focal) actually reads:
  - columns : ticker, date, adj_close, log_return
  - tickers : those appearing in events_focal.parquet + SPY
  - dates   : earliest focal earnings_date - 120d  to  latest + 45d
              (covers the ≥60 trading-day pre-window + 20d forward horizon)
  - dtypes  : float32 for price/return columns, categorical ticker
  - codec   : zstd (level 15)

The full prices.parquet is regenerable from build_price_panel.py, so it
stays local and is git-ignored.
"""
import pandas as pd
from pathlib import Path

SRC = Path("prices.parquet")
FOCAL = Path("events_focal.parquet")
OUT = Path("prices_slim.parquet")

KEEP_COLS = ["ticker", "date", "adj_close", "log_return"]
PAD_BEFORE_DAYS = 120   # ≥60 trading days + buffer
PAD_AFTER_DAYS = 45     # ≥20 trading days + buffer

print("Loading events_focal for ticker/date scope …")
focal = pd.read_parquet(FOCAL, columns=["ticker", "earnings_date"])
tickers = set(focal["ticker"].unique()) | {"SPY"}
min_date = focal["earnings_date"].min() - pd.Timedelta(days=PAD_BEFORE_DAYS)
max_date = focal["earnings_date"].max() + pd.Timedelta(days=PAD_AFTER_DAYS)
print(f"  tickers kept : {len(tickers):,}")
print(f"  date window  : {min_date.date()} → {max_date.date()}")

print("\nLoading prices (column-projected) …")
prices = pd.read_parquet(SRC, columns=KEEP_COLS)
print(f"  full prices  : {len(prices):,} rows")

mask = (
    prices["ticker"].isin(tickers)
    & (prices["date"] >= min_date)
    & (prices["date"] <= max_date)
)
slim = prices.loc[mask].copy()
print(f"  after filter : {len(slim):,} rows ({100*len(slim)/len(prices):.1f}%)")

# Dtype downcasts
slim["adj_close"] = slim["adj_close"].astype("float32")
slim["log_return"] = slim["log_return"].astype("float32")
slim["ticker"] = slim["ticker"].astype("category")
slim = slim.sort_values(["ticker", "date"]).reset_index(drop=True)

slim.to_parquet(OUT, index=False, compression="zstd", compression_level=15)
size_mb = OUT.stat().st_size / 1e6
print(f"\nSaved → {OUT}  ({size_mb:.1f} MB)")
print(f"  source was    : {SRC.stat().st_size / 1e6:.1f} MB")
print(f"  reduction     : {100 * (1 - OUT.stat().st_size / SRC.stat().st_size):.1f}%")
