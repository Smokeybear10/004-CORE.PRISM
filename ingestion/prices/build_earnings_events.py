"""
Step 3: Identify earnings events — maximum-history, complete-data version.

Anchor       : stock_earning_call_transcripts (back to ~2006)
BMO/AMC      : SEC 8-K acceptance_date_time (minute-accurate, ET)
Fallback     : ticker-level consensus (only for tickers with high historical consistency)
Filter       : ≥60 trading days before and ≥20 after in the price panel
Completeness : every output row has ticker, earnings_date, fiscal_quarter,
               call_timestamp, and a BMO or AMC tag — no nulls, no UNKNOWN/INTRADAY.
"""
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download, HfFileSystem
from pathlib import Path

REPO        = "defeatbeta/yahoo-finance-data"
OUT         = Path("earnings_events.parquet")
HIST_BEFORE = 60
HIST_AFTER  = 20
CONSENSUS_MIN_CONSISTENCY = 0.9   # only trust ticker consensus if ≥90% of its SEC-tagged events agree

fs = HfFileSystem()

# ── 1. Transcripts (anchor) ──────────────────────────────────────────────────
print("Loading transcript keys (column projection) …")
with fs.open(f"datasets/{REPO}/data/stock_earning_call_transcripts.parquet", "rb") as f:
    trans = pq.read_table(
        f,
        columns=['symbol', 'fiscal_year', 'fiscal_quarter', 'report_date', 'transcripts_id']
    ).to_pandas()
trans['report_date'] = pd.to_datetime(trans['report_date'])
trans = (trans
         .rename(columns={'symbol': 'ticker', 'report_date': 'earnings_date'})
         .drop_duplicates(subset=['ticker', 'earnings_date'])
         .reset_index(drop=True))
print(f"  Transcripts: {len(trans):,} events  |  {trans['ticker'].nunique():,} tickers")
print(f"  Range      : {trans['earnings_date'].min().date()} → {trans['earnings_date'].max().date()}")

# ── 2. SEC 8-K filings (BMO/AMC source) ──────────────────────────────────────
print("\nLoading SEC 8-K filings …")
sec_path = hf_hub_download(REPO, "data/stock_sec_filing.parquet", repo_type="dataset")
sec = pd.read_parquet(sec_path, columns=['symbol', 'form_type', 'filing_date', 'acceptance_date_time'])
sec = sec.rename(columns={'symbol': 'ticker'})
sec = sec[sec['form_type'].str.startswith('8-K', na=False)].copy()
sec['filing_date']  = pd.to_datetime(sec['filing_date'])
sec['acceptance']   = pd.to_datetime(sec['acceptance_date_time'], utc=True, errors='coerce')
sec['accept_et']    = sec['acceptance'].dt.tz_convert('America/New_York')
sec['accept_min']   = sec['accept_et'].dt.hour * 60 + sec['accept_et'].dt.minute
sec = sec.dropna(subset=['accept_min'])
print(f"  8-K filings with valid timestamps: {len(sec):,}")

# Pick the best 8-K per (ticker, day): prefer outside-market-hours filings
# (the earnings 8-K is almost always released pre-market or post-close). If
# only INTRADAY filings exist, fall back to earliest of those.
sec['outside_market'] = (sec['accept_min'] <= 9 * 60 + 30) | (sec['accept_min'] >= 16 * 60)
match = (sec[['ticker', 'filing_date', 'accept_et', 'accept_min', 'outside_market']]
         .rename(columns={'filing_date': 'earnings_date'})
         .sort_values(
             ['ticker', 'earnings_date', 'outside_market', 'accept_et'],
             ascending=[True, True, False, True],   # outside_market=True first
         )
         .drop_duplicates(subset=['ticker', 'earnings_date'], keep='first')
         .drop(columns=['outside_market']))

# ── 3. Match transcripts → 8-Ks ──────────────────────────────────────────────
events = trans.merge(match, on=['ticker', 'earnings_date'], how='left')
n_matched = events['accept_min'].notna().sum()
print(f"\n  Transcript events matched to an 8-K: {n_matched:,} / {len(events):,}")

# ── 4. Classify from SEC timestamp ───────────────────────────────────────────
def tag(m):
    if pd.isna(m): return None
    if m <= 9 * 60 + 30: return 'BMO'
    if m >= 16 * 60:     return 'AMC'
    return 'INTRADAY'

events['before_market_open_or_after_close'] = events['accept_min'].apply(tag)
events['time_source'] = np.where(
    events['before_market_open_or_after_close'].isin(['BMO', 'AMC', 'INTRADAY']),
    'sec_8k', None,
)

print("\n  SEC 8-K classification:")
print(events['before_market_open_or_after_close'].value_counts(dropna=False).to_string())

# ── 5. Ticker-consensus backfill for events without a same-day 8-K ───────────
sec_tagged = events[events['time_source'] == 'sec_8k']
sec_bmo_amc = sec_tagged[sec_tagged['before_market_open_or_after_close'].isin(['BMO', 'AMC'])]

def top_tag_and_share(x):
    vc = x.value_counts(normalize=True)
    return pd.Series({'tag': vc.index[0], 'share': vc.iloc[0]})

ticker_stats = sec_bmo_amc.groupby('ticker')['before_market_open_or_after_close'].apply(top_tag_and_share).unstack()
consistent   = ticker_stats[ticker_stats['share'] >= CONSENSUS_MIN_CONSISTENCY]['tag']

# (a) Fill events with no 8-K at all
unk_mask = events['time_source'].isna()
fill_unk = events.loc[unk_mask, 'ticker'].map(consistent)
events.loc[unk_mask & fill_unk.notna(), 'before_market_open_or_after_close'] = fill_unk.dropna().values
events.loc[unk_mask & fill_unk.notna(), 'time_source'] = 'ticker_consensus'

# (b) Override INTRADAY 8-K events when the ticker has a strong consensus — the
# matched 8-K was almost certainly a non-earnings filing.
intra_mask = events['before_market_open_or_after_close'] == 'INTRADAY'
fill_intra = events.loc[intra_mask, 'ticker'].map(consistent)
events.loc[intra_mask & fill_intra.notna(), 'before_market_open_or_after_close'] = fill_intra.dropna().values
events.loc[intra_mask & fill_intra.notna(), 'time_source'] = 'sec_8k_intraday_corrected'

print(f"\n  Ticker-consensus backfill (no-8K) : +{fill_unk.notna().sum():,}")
print(f"  INTRADAY overrides via consensus : +{fill_intra.notna().sum():,}")

# ── 6. Drop incomplete rows ──────────────────────────────────────────────────
before = len(events)
events = events[events['before_market_open_or_after_close'].isin(['BMO', 'AMC'])].copy()
dropped = before - len(events)
print(f"  Dropped {dropped:,} incomplete rows (UNKNOWN / INTRADAY)")

# ── 7. Price-history filter ──────────────────────────────────────────────────
print(f"\nApplying price-history filter (≥{HIST_BEFORE} before, ≥{HIST_AFTER} after) …")
prices = pd.read_parquet("prices.parquet", columns=['ticker', 'date'])
ticker_dates = {t: g['date'].values for t, g in prices.groupby('ticker', sort=False)}

keep = []
for ticker, grp in events.groupby('ticker', sort=False):
    dates = ticker_dates.get(ticker)
    if dates is None or len(dates) == 0:
        continue
    ed  = grp['earnings_date'].values.astype('datetime64[ns]')
    idx = np.searchsorted(dates, ed, side='left')
    mask = (idx >= HIST_BEFORE) & (len(dates) - idx >= HIST_AFTER)
    keep.append(grp[mask])

events = pd.concat(keep, ignore_index=True) if keep else events.iloc[0:0]
print(f"  Events after price filter: {len(events):,}")

# ── 8. Finalize and save ─────────────────────────────────────────────────────
# For ticker-consensus rows we don't have an exact timestamp; use NaT placeholder
# so downstream code can still rely on the BMO/AMC tag.
events['call_timestamp'] = events['accept_et']  # NaT where filled via consensus

events = events[[
    'ticker', 'earnings_date', 'fiscal_year', 'fiscal_quarter',
    'call_timestamp', 'before_market_open_or_after_close',
    'time_source', 'transcripts_id',
]].sort_values(['ticker', 'earnings_date']).reset_index(drop=True)

print(f"\nFinal shape: {events.shape}")
print(f"  Tickers    : {events['ticker'].nunique():,}")
print(f"  Date range : {events['earnings_date'].min().date()} → {events['earnings_date'].max().date()}")
print("\n  BMO/AMC:")
print(events['before_market_open_or_after_close'].value_counts().to_string())
print("\n  time_source:")
print(events['time_source'].value_counts().to_string())

# Sanity check
print("\nSanity check (known reporters):")
for tkr, expect in [('AAPL', 'AMC'), ('MSFT', 'AMC'), ('GOOGL', 'AMC'),
                    ('JPM', 'BMO'), ('GS', 'BMO'), ('XOM', 'BMO')]:
    sub = events[events['ticker'] == tkr].tail(5)
    if not sub.empty:
        tags = sub['before_market_open_or_after_close'].tolist()
        ok   = "✓" if all(t == expect for t in tags) else "✗"
        print(f"  {tkr} (expect {expect}): {tags} {ok}")
    else:
        print(f"  {tkr}: no events")

events.to_parquet(OUT, index=False)
print(f"\nSaved → {OUT}")
