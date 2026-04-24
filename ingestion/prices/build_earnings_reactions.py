"""
Step 4: Earnings reaction + forward returns

For each event in earnings_events.parquet, compute:
  reaction_return              — the market's immediate response
      BMO : close[T]   / close[T-1] - 1
      AMC : close[T+1] / close[T]   - 1
      non-trading-day earnings_date : close[next_td] / close[prev_td] - 1
  fwd_return_{1,5,20}d         — close-to-close from end-of-reaction-window
                                 to +N trading days (post-reaction)
  ..._excess_spy               — subtract SPY's return over the same window
  ..._excess_sector            — subtract the ticker's SPDR sector ETF
  baseline_vol_{30,60}d        — std of daily log_return in the N trading days
                                 ending on the trading day BEFORE the event
"""
import numpy as np
import pandas as pd
from huggingface_hub import hf_hub_download
from pathlib import Path
import time

REPO = "defeatbeta/yahoo-finance-data"
OUT  = Path("earnings_reactions.parquet")

# Yahoo-sector → SPDR sector ETF
SECTOR_ETF = {
    'Technology':             'XLK',
    'Financial Services':     'XLF',
    'Healthcare':             'XLV',
    'Consumer Cyclical':      'XLY',
    'Consumer Defensive':     'XLP',
    'Communication Services': 'XLC',
    'Industrials':            'XLI',
    'Industrial':             'XLI',
    'Energy':                 'XLE',
    'Utilities':              'XLU',
    'Real Estate':            'XLRE',
    'Basic Materials':        'XLB',
}

# ── 1. Load inputs ───────────────────────────────────────────────────────────
print("Loading prices + events …")
prices = pd.read_parquet("prices.parquet")
events = pd.read_parquet("earnings_events.parquet")
events['earnings_date'] = pd.to_datetime(events['earnings_date'])
prices['date'] = pd.to_datetime(prices['date'])

# Drop bad rows that would poison returns
prices = prices[prices['adj_close'] > 0].copy()
prices = prices.sort_values(['ticker', 'date']).reset_index(drop=True)
print(f"  prices : {len(prices):,} rows  |  events : {len(events):,}")

# ── 2. Sector mapping ────────────────────────────────────────────────────────
print("\nLoading sector mapping …")
prof_path = hf_hub_download(REPO, "data/stock_profile.parquet", repo_type="dataset")
profile = pd.read_parquet(prof_path, columns=['symbol', 'sector']).rename(columns={'symbol': 'ticker'})
profile = profile.dropna(subset=['ticker']).drop_duplicates('ticker')
profile['sector_etf'] = profile['sector'].map(SECTOR_ETF)
ticker_sector = dict(zip(profile['ticker'], profile['sector']))
ticker_etf    = dict(zip(profile['ticker'], profile['sector_etf']))
print(f"  Sectors mapped: {profile['sector_etf'].notna().sum():,} / {len(profile):,} tickers")

# ── 3. Per-ticker numpy arrays ───────────────────────────────────────────────
print("\nBuilding per-ticker price arrays …")
by_ticker = {}
for t, g in prices.groupby('ticker', sort=False):
    by_ticker[t] = (
        g['date'].values,                                # dates  (datetime64)
        g['adj_close'].values.astype(np.float64),        # adj_close
        g['log_return'].values.astype(np.float64),       # log_return
    )
print(f"  Tickers indexed: {len(by_ticker):,}")

spy = by_ticker.get('SPY')
if spy is None:
    print("  WARNING: SPY not in dataset — SPY-excess columns will be NaN")

# Which sector ETFs do we have price data for?
present_etfs = {s for s in set(SECTOR_ETF.values()) if s in by_ticker}
print(f"  Real sector ETFs in dataset: {sorted(present_etfs) or 'none'}")

# ── 3b. Build synthetic equal-weighted sector indices ─────────────────────────
# SPDR sector ETFs aren't in this dataset, so construct an EW sector-return
# series from the tickers we DO have labelled with each sector. Aligned on
# daily log returns and exponentiated to a cumulative adj_close-like series
# so the same `bench_fwd(...)` helper works.
print("\nBuilding synthetic equal-weighted sector indices …")
prices_with_sector = prices.merge(
    profile[['ticker', 'sector']].dropna(), on='ticker', how='inner'
)
# Clip individual stock daily log-returns before aggregating — a handful of
# tickers in stock_prices have corrupted adj_close (→ extreme log returns), and
# including them unclipped makes the cumulative sector index overflow to inf.
# ±0.5 = ±65% daily is already well past any real single-day move.
prices_with_sector['log_return'] = prices_with_sector['log_return'].clip(-0.5, 0.5)
# Mean log return per (sector, date)
sector_ret = (prices_with_sector
              .groupby(['sector', 'date'])['log_return']
              .mean()
              .reset_index())
sector_ret['synth_close'] = (sector_ret
                             .groupby('sector')['log_return']
                             .transform(lambda s: np.exp(s.fillna(0).cumsum())))

synth_sector = {}  # sector_name → (dates, adj_close, log_return)
for s, g in sector_ret.groupby('sector', sort=False):
    g = g.sort_values('date')
    synth_sector[s] = (
        g['date'].values,
        g['synth_close'].values.astype(np.float64),
        g['log_return'].values.astype(np.float64),
    )
print(f"  Synthetic indices built for {len(synth_sector)} sectors")

# ── 4. Core per-event computation ────────────────────────────────────────────
def fwd(adj, idx, n):
    """Close-to-close return from `idx` to `idx+n`, or NaN if OOB."""
    if idx + n >= len(adj): return np.nan
    p0, p1 = adj[idx], adj[idx + n]
    if p0 <= 0 or p1 <= 0: return np.nan
    return p1 / p0 - 1

def bench_fwd(bench, anchor_date, n):
    """Same but for a benchmark (SPY or sector ETF), aligned by calendar date."""
    if bench is None: return np.nan
    b_dates, b_adj, _ = bench
    idx = np.searchsorted(b_dates, anchor_date, side='left')
    if idx >= len(b_dates) or b_dates[idx] != anchor_date:
        return np.nan
    return fwd(b_adj, idx, n)


NS = ['1d', '5d', '20d']
NHORIZONS = [1, 5, 20]

rows   = []
t_start = time.time()
n_total = len(events)
n_done  = 0
n_skip  = 0

# Batch-process by ticker so we hit the dict once per ticker
for ticker, grp in events.groupby('ticker', sort=False):
    td = by_ticker.get(ticker)
    if td is None:
        n_skip += len(grp); continue
    dates, adj, logret = td

    sector     = ticker_sector.get(ticker)
    etf        = ticker_etf.get(ticker)
    # Prefer real ETF if available; fall back to synthetic sector index
    sector_td  = (by_ticker.get(etf) if etf in present_etfs
                  else synth_sector.get(sector))

    for ev in grp.itertuples(index=False):
        ed = np.datetime64(ev.earnings_date, 'ns')
        Ti = np.searchsorted(dates, ed, side='left')
        n  = len(dates)

        is_trading = (Ti < n) and (dates[Ti] == ed)
        is_bmo     = (ev.before_market_open_or_after_close == 'BMO')

        # ── reaction + anchor ────────────────────────────────────────────────
        if is_trading and is_bmo:
            if Ti < 1: n_skip += 1; continue
            react = adj[Ti] / adj[Ti - 1] - 1
            anchor = Ti
        elif is_trading and not is_bmo:               # AMC on a trading day
            if Ti + 1 >= n: n_skip += 1; continue
            react = adj[Ti + 1] / adj[Ti] - 1
            anchor = Ti + 1
        else:                                         # earnings on a non-trading day
            if Ti < 1 or Ti >= n: n_skip += 1; continue
            react = adj[Ti] / adj[Ti - 1] - 1
            anchor = Ti

        anchor_date = dates[anchor]

        # ── forwards + SPY/sector excess ─────────────────────────────────────
        f  = {n: fwd(adj, anchor, n) for n in NHORIZONS}
        sf = {n: bench_fwd(spy,        anchor_date, n) for n in NHORIZONS}
        kf = {n: bench_fwd(sector_td,  anchor_date, n) for n in NHORIZONS}

        # ── baseline vol (ends on day BEFORE earnings, to avoid leakage) ─────
        vol_end_idx = Ti if is_trading else Ti   # Ti already points to first trading day on/after ed
        vol_30 = vol_60 = np.nan
        if vol_end_idx >= 30:
            vol_30 = float(np.nanstd(logret[vol_end_idx - 30:vol_end_idx], ddof=1))
        if vol_end_idx >= 60:
            vol_60 = float(np.nanstd(logret[vol_end_idx - 60:vol_end_idx], ddof=1))

        rows.append((
            ticker, ev.earnings_date, ev.before_market_open_or_after_close, ev.time_source,
            ev.fiscal_year, ev.fiscal_quarter, ev.transcripts_id,
            sector, etf,
            float(react),
            f[1], f[5], f[20],
            (f[1]  - sf[1])  if not np.isnan(sf[1])  else np.nan,
            (f[5]  - sf[5])  if not np.isnan(sf[5])  else np.nan,
            (f[20] - sf[20]) if not np.isnan(sf[20]) else np.nan,
            (f[1]  - kf[1])  if not np.isnan(kf[1])  else np.nan,
            (f[5]  - kf[5])  if not np.isnan(kf[5])  else np.nan,
            (f[20] - kf[20]) if not np.isnan(kf[20]) else np.nan,
            vol_30, vol_60,
        ))
        n_done += 1

print(f"\nProcessed {n_done:,} events  ({n_skip:,} skipped — no price data or boundary)")
print(f"Wall time: {time.time() - t_start:.1f}s")

# ── 5. Assemble DataFrame ────────────────────────────────────────────────────
cols = [
    'ticker', 'earnings_date', 'before_market_open_or_after_close', 'time_source',
    'fiscal_year', 'fiscal_quarter', 'transcripts_id',
    'sector', 'sector_etf',
    'reaction_return',
    'fwd_return_1d',  'fwd_return_5d',  'fwd_return_20d',
    'fwd_return_1d_excess_spy',    'fwd_return_5d_excess_spy',    'fwd_return_20d_excess_spy',
    'fwd_return_1d_excess_sector', 'fwd_return_5d_excess_sector', 'fwd_return_20d_excess_sector',
    'baseline_vol_30d', 'baseline_vol_60d',
]
out = pd.DataFrame(rows, columns=cols).sort_values(['ticker', 'earnings_date']).reset_index(drop=True)

# ── 6. Coverage stats + sanity ───────────────────────────────────────────────
print(f"\nFinal shape: {out.shape}")
print(f"  tickers    : {out['ticker'].nunique():,}")
print(f"  date range : {out['earnings_date'].min().date()} → {out['earnings_date'].max().date()}")

print("\nNon-null coverage (key columns):")
for c in ['reaction_return', 'fwd_return_1d', 'fwd_return_5d', 'fwd_return_20d',
          'fwd_return_5d_excess_spy', 'fwd_return_5d_excess_sector',
          'baseline_vol_30d', 'baseline_vol_60d']:
    n = out[c].notna().sum()
    print(f"  {c:<34} {n:>7,}  ({100*n/len(out):.1f}%)")

print("\nReaction return distribution:")
print(out['reaction_return'].describe(percentiles=[.01, .05, .5, .95, .99]).to_string())

print("\nSample (AAPL, most recent 5):")
sub = out[out['ticker'] == 'AAPL'].tail(5)
print(sub[['earnings_date', 'before_market_open_or_after_close',
           'reaction_return', 'fwd_return_5d', 'fwd_return_5d_excess_spy',
           'baseline_vol_30d']].to_string(index=False))

out.to_parquet(OUT, index=False)
print(f"\nSaved → {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB)")
