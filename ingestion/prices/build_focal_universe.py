"""
Step 5b: Focal-universe filter.

Keeps the three sectors relevant to the target companies (Technology,
Communication Services, Healthcare), drops junk-data rows, and flags
the explicit focal tickers. All non-focal tickers in the same sectors
are preserved as peers — the backtest needs sector context.

Output: events_focal.parquet
"""
import pandas as pd
from pathlib import Path

FOCAL_TICKERS = {
    'NVDA':  'NVIDIA',
    'GOOGL': 'Alphabet (Class A)',
    'GOOG':  'Alphabet (Class C)',
    'SNDK':  'SanDisk (2025 spin-off)',
    'WDC':   'Western Digital (legacy SanDisk proxy 2016-2025)',
    'IONS':  'Ionis Pharmaceuticals',
    'VRTX':  'Vertex Pharmaceuticals',
}
FOCAL_SECTORS = {'Technology', 'Communication Services', 'Healthcare'}

# Quality filters — drop junk adj_close rows that survived earlier steps.
# Note: we do NOT filter by pre_event_price because that column is *adjusted*
# close, which shrinks for stocks with large historical splits (e.g. NVDA
# in 2006 has adj_close ~$0.37 despite trading at ~$15 at the time).
MAX_REACTION    = 0.5   # |reaction_return| > 50% is almost certainly a data error
MIN_VOL         = 1e-4  # pre_event_30d_vol should be positive and non-degenerate

OUT = Path("events_focal.parquet")

# ── Load and filter ─────────────────────────────────────────────────────────
events = pd.read_parquet("events.parquet")
print(f"Input: {len(events):,} events")

# 1. Sector filter
sector_mask = events['sector'].isin(FOCAL_SECTORS)
print(f"  in focal sectors        : {sector_mask.sum():,}")

# 2. Quality filter
quality_mask = (
    (events['reaction_return'].abs() <= MAX_REACTION)
    & (events['pre_event_30d_vol'] >= MIN_VOL)
    & events['reaction_return'].notna()
    & events['pre_event_30d_vol'].notna()
)
print(f"  passing quality filter  : {quality_mask.sum():,}")

focal_events = events[sector_mask & quality_mask].copy()
focal_events['is_focal'] = focal_events['ticker'].isin(FOCAL_TICKERS)
focal_events['focal_company'] = focal_events['ticker'].map(FOCAL_TICKERS)
print(f"  combined filter kept    : {len(focal_events):,}")

focal_events = focal_events.sort_values(['ticker', 'earnings_date']).reset_index(drop=True)

# ── Report ───────────────────────────────────────────────────────────────────
print(f"\nFocal universe: {len(focal_events):,} events | "
      f"tickers: {focal_events['ticker'].nunique():,} | "
      f"range: {focal_events['earnings_date'].min().date()} → {focal_events['earnings_date'].max().date()}")

print("\nBy sector:")
print(focal_events.groupby('sector')
      .agg(events=('event_id', 'size'),
           tickers=('ticker', 'nunique'),
           significant=('is_significant', 'sum'))
      .to_string())

print("\nFocal companies (events in final set):")
for t, name in FOCAL_TICKERS.items():
    sub = focal_events[focal_events['ticker'] == t]
    sig = sub['is_significant'].sum()
    print(f"  {t:<6} {name:<45} events: {len(sub):>3}  significant: {sig}")

sig_focal = focal_events[focal_events['is_focal'] & focal_events['is_significant']]
sig_all   = focal_events[focal_events['is_significant']]
print(f"\nSignificant events across all peers in focal sectors : {len(sig_all):,}")
print(f"Significant events limited to the 7 focal tickers    : {len(sig_focal):,}")

print("\nSignificant events at the 7 focal tickers (most recent 10):")
print(sig_focal[['event_id', 'ticker', 'earnings_date', 'bmo_or_amc',
                 'reaction_return', 'reaction_return_zscore', 'fwd_5d_excess']]
      .sort_values('earnings_date', ascending=False)
      .head(10)
      .to_string(index=False))

focal_events.to_parquet(OUT, index=False)
print(f"\nSaved → {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB)")
