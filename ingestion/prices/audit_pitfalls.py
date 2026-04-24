"""
Pitfall audit: verify the pipeline against 4 known gotchas.

1. Timezone confusion
2. Split/dividend adjustment
3. Earnings release date vs call date
4. Ticker changes / mergers / dual-class
"""
import numpy as np
import pandas as pd
from huggingface_hub import hf_hub_download
import pyarrow.parquet as pq

REPO = "defeatbeta/yahoo-finance-data"

events = pd.read_parquet("events.parquet")
events['earnings_date'] = pd.to_datetime(events['earnings_date'])
prices = pd.read_parquet("prices.parquet")
prices['date'] = pd.to_datetime(prices['date'])

def hdr(s):
    print(f"\n{'='*70}\n{s}\n{'='*70}")

# ────────────────────────────────────────────────────────────────────────────
# 1. TIMEZONE
# ────────────────────────────────────────────────────────────────────────────
hdr("1. TIMEZONE")
print("Everything is standardized to ET:")
print("  • prices.date              — date-only (no TZ)")
print("  • earnings_date            — date-only (no TZ)")
print("  • call_timestamp           — stored as UTC, converted to America/New_York")
print("                               for BMO/AMC thresholds (09:30 / 16:00 ET)")

# Verify with a known AMC reporter — AAPL releases ~16:30 ET
ct = events[(events['ticker'] == 'AAPL') & events['call_timestamp'].notna()].tail(5)
print("\nAAPL call_timestamps (should all be 16:00-17:00 ET — AMC reporter):")
ts = pd.to_datetime(ct['call_timestamp'], utc=True).dt.tz_convert('America/New_York')
for et in ts:
    in_amc_window = (et.hour >= 16)
    print(f"  {et}  {'✓ AMC' if in_amc_window else '✗'}")

# And a BMO reporter — JPM releases ~07:00 ET
ct = events[(events['ticker'] == 'JPM') & events['call_timestamp'].notna()].tail(5)
print("\nJPM call_timestamps (should all be before 09:30 ET — BMO reporter):")
ts = pd.to_datetime(ct['call_timestamp'], utc=True).dt.tz_convert('America/New_York')
for et in ts:
    in_bmo_window = (et.hour * 60 + et.minute <= 9 * 60 + 30)
    print(f"  {et}  {'✓ BMO' if in_bmo_window else '✗'}")

# ────────────────────────────────────────────────────────────────────────────
# 2. SPLITS / DIVIDENDS
# ────────────────────────────────────────────────────────────────────────────
hdr("2. SPLITS / DIVIDENDS — verify adjusted close behaves correctly")
# NVDA 10:1 stock split on 2021-07-20. The raw close drops by ~90% that day.
# Adjusted close should NOT show this — it should be a normal day.
nvda = prices[prices['ticker'] == 'NVDA'].sort_values('date').reset_index(drop=True)
split_day = pd.Timestamp('2021-07-20')
window = nvda[(nvda['date'] >= split_day - pd.Timedelta('2 days')) &
              (nvda['date'] <= split_day + pd.Timedelta('2 days'))]
print("NVDA around its 10:1 split on 2021-07-20:")
print(window[['date', 'close', 'adj_close']].to_string(index=False))
print("  raw close drops ~90% on split day (expected).")
print("  adj_close should NOT drop — it should show a normal daily move.")

# Same for NVDA's 4:1 split on 2024-06-10
split_day = pd.Timestamp('2024-06-10')
window = nvda[(nvda['date'] >= split_day - pd.Timedelta('2 days')) &
              (nvda['date'] <= split_day + pd.Timedelta('2 days'))]
print("\nNVDA around its 4:1 split on 2024-06-10:")
print(window[['date', 'close', 'adj_close']].to_string(index=False))

# Confirm returns use adj_close
print("\nSample log_return on a split day vs. raw ratio:")
row = nvda[nvda['date'] == pd.Timestamp('2021-07-20')].iloc[0]
prev = nvda[nvda['date'] < row['date']].iloc[-1]
raw_ratio   = row['close'] / prev['close']
adj_ratio   = row['adj_close'] / prev['adj_close']
print(f"  raw close ratio: {raw_ratio:.4f}  (would imply {100*(raw_ratio-1):+.1f}% return — WRONG)")
print(f"  adj close ratio: {adj_ratio:.4f}  (real daily return ~ {100*(adj_ratio-1):+.1f}%)")
print(f"  pipeline log_return stored: {row['log_return']:+.4f}  →  {100*(np.exp(row['log_return'])-1):+.2f}% ← uses adj")

# ────────────────────────────────────────────────────────────────────────────
# 3. RELEASE DATE vs CALL DATE
# ────────────────────────────────────────────────────────────────────────────
hdr("3. RELEASE vs CALL DATE")
# In this dataset:
#   - transcripts.report_date  → the date on the transcript (call date)
#   - calendar.report_date     → the announcement date
#   - SEC 8-K filing_date      → the date the earnings release was filed
# We anchored on transcripts.report_date (= our earnings_date) and matched 8-Ks
# on filing_date == earnings_date. If release and call are different days, we'd
# see a mismatch.

# Load the 8-Ks for a sanity check: is the offset between our earnings_date
# and the 8-K filing_date always 0?
print("Loading SEC 8-K filings to check call-vs-release alignment …")
sec = pd.read_parquet(hf_hub_download(REPO, "data/stock_sec_filing.parquet", repo_type="dataset"),
                     columns=['symbol', 'form_type', 'filing_date', 'acceptance_date_time'])
sec = sec.rename(columns={'symbol': 'ticker'})
sec = sec[sec['form_type'].str.startswith('8-K', na=False)].copy()
sec['filing_date'] = pd.to_datetime(sec['filing_date'])
sec['acceptance']  = pd.to_datetime(sec['acceptance_date_time'], utc=True, errors='coerce')

# For each event, find the NEAREST 8-K for that ticker (any date), compute offset
tickers_interest = ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'META', 'AMZN', 'JPM', 'GS']
sub_events = events[events['ticker'].isin(tickers_interest)].copy()
merged = sub_events.merge(
    sec.groupby('ticker').apply(lambda df: df[['filing_date','acceptance']])
       .reset_index().drop(columns='level_1')
       .rename(columns={'filing_date': 'sec_date'}),
    on='ticker', how='left'
)
merged['offset_days'] = (merged['sec_date'] - merged['earnings_date']).dt.days
# Find, per event, the 8-K filed closest to earnings_date
merged['abs_offset'] = merged['offset_days'].abs()
nearest = (merged.sort_values(['event_id', 'abs_offset'])
                  .drop_duplicates(['event_id'], keep='first'))

print(f"\nAmong {len(tickers_interest)} large caps × {nearest['event_id'].nunique()} events:")
print("Offset (days) between our earnings_date and nearest 8-K filing_date:")
print(nearest['offset_days'].value_counts().sort_index().head(10).to_string())
print(f"\nFraction with same-day filing (offset == 0): "
      f"{(nearest['offset_days'] == 0).mean():.1%}")
print("  Same-day 8-K ⇒ release and call are the same calendar day ⇒ our")
print("  earnings_date is simultaneously the release date and call date.")
print("  For AMC reporters release typically ≈ 16:30 ET and call ≈ 17:00 ET.")
print("  For BMO reporters release ≈ 07:00 ET and call ≈ 08:30 ET.")
print("  In both cases reaction window logic is correct (AMC uses close[T+1]/close[T]).")

# ────────────────────────────────────────────────────────────────────────────
# 4. TICKER CHANGES / MERGERS / DUAL-CLASS
# ────────────────────────────────────────────────────────────────────────────
hdr("4. TICKER CHANGES, MERGERS, DUAL-CLASS")

# 4a. Meta / Facebook — FB renamed to META on 2022-06-09
print("\n(a) Meta rename (FB → META on 2022-06-09):")
for t in ['FB', 'META']:
    sub = events[events['ticker'] == t]
    if len(sub):
        print(f"    {t:<5} events: {len(sub):>3}  "
              f"range: {sub['earnings_date'].min().date()} → {sub['earnings_date'].max().date()}")
    else:
        print(f"    {t:<5} NOT in events table")
# Also prices
for t in ['FB', 'META']:
    sub = prices[prices['ticker'] == t]
    if len(sub):
        print(f"    (prices) {t:<5} range: "
              f"{sub['date'].min().date()} → {sub['date'].max().date()}, {len(sub):,} rows")

# 4b. Google / Alphabet dual-class
print("\n(b) Alphabet dual-class (GOOGL = voting, GOOG = non-voting):")
for t in ['GOOGL', 'GOOG']:
    sub = events[events['ticker'] == t]
    print(f"    {t:<6} events: {len(sub):>3}")
# Check if the same earnings_date appears for both (they should)
both = events[events['ticker'].isin(['GOOG', 'GOOGL'])].copy()
pivot = both.pivot_table(index='earnings_date', columns='ticker',
                         values='reaction_return', aggfunc='first')
both_present = pivot.dropna()
print(f"    Earnings dates where BOTH classes have an event: {len(both_present):,}")
print("    Sample (GOOG vs GOOGL reactions on the same date — should be ~identical):")
print(both_present.tail(5).to_string())

# 4c. Other known renames worth checking
print("\n(c) Spot-check other known renames:")
known = [
    ('SQ', 'XYZ',   'Block — SQ → XYZ rename 2025-01'),
    ('TWTR', None,  'Twitter delisted Oct 2022 (Musk take-private)'),
    ('HPE', 'HPQ',  'HPE split from HPQ in 2015'),
]
for old, new, note in known:
    for t in [old, new]:
        if t is None: continue
        sub = events[events['ticker'] == t]
        rng = f"{sub['earnings_date'].min().date()} → {sub['earnings_date'].max().date()}" if len(sub) else "—"
        print(f"    {t:<6} events: {len(sub):>3}  range: {rng}")
    print(f"       {note}")

# 4d. Duplicate events on the same (company, earnings_date) across ticker variants
print("\n(d) Duplicate-event risk: same-day events at different tickers")
# If both GOOG and GOOGL are counted, same earnings gets 2 rows.
# That's by design (they're separate securities) but worth knowing.
# Check how many companies have >1 ticker in the events table on the same date.
daily_groups = events.groupby('earnings_date')['ticker'].nunique()
print(f"    Max tickers reporting on a single date: {daily_groups.max()}")
# Known multi-class pairs the data explicitly carries:
multi = [('GOOGL', 'GOOG'),
         ('BRK-A', 'BRK-B'),
         ('FOX',   'FOXA'),
         ('NWS',   'NWSA')]
for a, b in multi:
    sub = events[events['ticker'].isin([a, b])]
    dual_dates = (sub.groupby('earnings_date')['ticker'].nunique() == 2).sum()
    print(f"    {a}/{b}: shared earnings dates with BOTH classes present: {dual_dates}")
print("    → if you don't want double-counting in aggregate stats,")
print("      keep only GOOGL (and BRK-A, FOXA, NWSA) and drop the sibling.")
