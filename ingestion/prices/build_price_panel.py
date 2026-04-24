"""
Step 2: Build the price panel
Output: prices.parquet — clean daily OHLCV + adj_close + log_return for full universe
"""
import numpy as np
import pandas as pd
from huggingface_hub import hf_hub_download
from pathlib import Path

BW_REPO = "BridgewaterAIHackathon/BW-AI-Hackathon"
BW_PREFIX = "Structured_Data/SNE/yahoo-finance-data"
PUBLIC_REPO = "defeatbeta/yahoo-finance-data"
PUBLIC_PREFIX = "data"
OUT  = Path("prices.parquet")

# ── 1. Load raw tables ────────────────────────────────────────────────────────
def load(fname):
    """Try the private BW HF repo first, fall back to the public mirror.

    The BW repo layout has files directly under BW_PREFIX (no `data/`
    subfolder), while the public mirror nests them under `data/`.
    """
    try:
        path = hf_hub_download(BW_REPO, f"{BW_PREFIX}/{fname}", repo_type="dataset")
    except Exception:
        path = hf_hub_download(PUBLIC_REPO, f"{PUBLIC_PREFIX}/{fname}", repo_type="dataset")
    return pd.read_parquet(path)

print("Loading prices …")
prices_raw = load("data/stock_prices.parquet")
splits_raw = load("data/stock_split_events.parquet")
divs_raw   = load("data/stock_dividend_events.parquet")
print(f"  prices  : {len(prices_raw):>10,} rows  |  {prices_raw['symbol'].nunique():,} tickers")
print(f"  splits  : {len(splits_raw):>10,} rows")
print(f"  divs    : {len(divs_raw):>10,} rows")

# ── 2. Clean types + rename ───────────────────────────────────────────────────
prices = (prices_raw
          .rename(columns={'symbol': 'ticker', 'report_date': 'date'})
          .assign(date=lambda d: pd.to_datetime(d['date'])))
for col in ['open', 'high', 'low', 'close']:
    prices[col] = prices[col].astype(float)
prices['volume'] = prices['volume'].astype('Int64')

splits = (splits_raw
          .rename(columns={'report_date': 'date'})
          .assign(date=lambda d: pd.to_datetime(d['date'])))
splits['ratio'] = splits['split_factor'].apply(
    lambda s: float(s.split(':')[0]) / float(s.split(':')[1])
)

divs = (divs_raw
        .rename(columns={'report_date': 'date'})
        .assign(date=lambda d: pd.to_datetime(d['date']),
                amount=lambda d: d['amount'].astype(float)))

# ── 3. Compute adjusted close ─────────────────────────────────────────────────
# IMPORTANT: the source `close` column is ALREADY split-adjusted (verified
# against NVDA 2021-07-20 4:1 and AAPL 2014-06-09 7:1 — stored close shows no
# jump across split days). So we do NOT re-apply split adjustment here;
# doing so double-adjusts and creates fake huge returns on split days.
#
# The source `close` is NOT dividend-adjusted (verified on AT&T: close drops
# by the dividend amount on ex-div days). So we DO apply dividend adjustment.
print("\nComputing adjusted close (dividend-only; splits already applied in source) …")

prices = prices.sort_values(['ticker', 'date']).reset_index(drop=True)

splits_by = {k: v for k, v in splits.groupby('symbol')}
divs_by   = {k: v for k, v in divs.groupby('symbol')}

adj_factors = np.ones(len(prices), dtype=np.float64)
ticker_col  = prices['ticker'].values
date_col    = prices['date'].values
close_col   = prices['close'].values

# Build an index: ticker -> (start_idx, end_idx) in the sorted prices array
# prices is sorted by (ticker, date) so we can use searchsorted
tickers_sorted, ticker_starts = np.unique(ticker_col, return_index=True)
ticker_ends = np.append(ticker_starts[1:], len(prices))

for i, ticker in enumerate(tickers_sorted):
    s, e = ticker_starts[i], ticker_ends[i]
    dates  = date_col[s:e]          # numpy datetime64, sorted ascending
    closes = close_col[s:e]
    adj    = np.ones(e - s)

    # splits are intentionally skipped — already baked into the source `close`.

    # dividends
    if ticker in divs_by:
        for _, row in divs_by[ticker].iterrows():
            dd  = np.datetime64(row['date'], 'ns')
            idx = np.searchsorted(dates, dd)
            if idx < len(dates) and dates[idx] == dd:
                c = float(closes[idx])
                d = float(row['amount'])
                if 0 < d < c:
                    adj[dates < dd] *= (c - d) / c

    adj_factors[s:e] = adj

    if (i + 1) % 1000 == 0:
        print(f"  {i+1:,} / {len(tickers_sorted):,} tickers done")

prices['adj_close'] = np.round(prices['close'].values * adj_factors, 4)
print(f"  Done. adj_close range: {prices['adj_close'].min():.2f} – {prices['adj_close'].max():.2f}")

# ── 4. Uniqueness check ───────────────────────────────────────────────────────
dupes = prices.duplicated(subset=['ticker', 'date'])
if dupes.any():
    print(f"\nWARNING: {dupes.sum():,} duplicate (ticker, date) rows — keeping first")
    prices = prices[~dupes].reset_index(drop=True)

# ── 5. Log returns ────────────────────────────────────────────────────────────
prices['log_return'] = (
    prices
    .groupby('ticker')['adj_close']
    .transform(lambda x: np.log(x / x.shift(1)))
)
# First day of each ticker has NaN — expected

# ── 6. Gap analysis ───────────────────────────────────────────────────────────
print("\nGap analysis (calendar days between consecutive trading dates per ticker):")
prev_date  = prices.groupby('ticker')['date'].shift(1)
gap_days   = (prices['date'] - prev_date).dt.days

normal_max = 4   # Mon after a 3-day holiday weekend
flag_above = 7   # anything > 7 days is suspicious

suspicious = prices[gap_days > flag_above].copy()
suspicious['gap_days'] = gap_days[gap_days > flag_above]

print(f"  Gaps ≤ {flag_above}d  : {(gap_days.dropna() <= flag_above).sum():,}  (normal)")
print(f"  Gaps  > {flag_above}d  : {len(suspicious):,}  (flagged)")

if not suspicious.empty:
    gap_summary = (suspicious
                   .groupby('ticker')['gap_days']
                   .agg(n_gaps='count', max_gap='max')
                   .sort_values('max_gap', ascending=False)
                   .head(20))
    print("\n  Top tickers by largest gap:")
    print(gap_summary.to_string())

# ── 7. Survivorship bias note ─────────────────────────────────────────────────
print("\nSurvivorship bias check:")
ranges = prices.groupby('ticker')['date'].agg(first='min', last='max')
data_end = ranges['last'].max()
cutoff   = data_end - pd.Timedelta(days=90)
delisted = ranges[ranges['last'] < cutoff]

print(f"  Dataset end : {data_end.date()}")
print(f"  Total tickers : {len(ranges):,}")
print(f"  Tickers last seen >90d before end (likely delisted/inactive): {len(delisted):,}")
print(f"  Sample delisted : {list(delisted.index[:10])}")
print("  NOTE: Returns for these tickers are included but may end abruptly.")
print("  If you restrict to current S&P 500, you introduce survivorship bias.")

# ── 8. Final column order + save ─────────────────────────────────────────────
prices = prices[['ticker', 'date', 'open', 'high', 'low', 'close', 'adj_close', 'volume', 'log_return']]

print(f"\nFinal price panel:")
print(f"  Shape     : {prices.shape}")
print(f"  Date range: {prices['date'].min().date()} → {prices['date'].max().date()}")
print(f"  Tickers   : {prices['ticker'].nunique():,}")
print(f"\nDtypes:")
print(prices.dtypes.to_string())
print(f"\nSample (AAPL):")
aapl = prices[prices['ticker'] == 'AAPL'].tail(10)
print(aapl.to_string(index=False) if not aapl.empty else "  AAPL not in dataset")

prices.to_parquet(OUT, index=False)
print(f"\nSaved → {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB)")
