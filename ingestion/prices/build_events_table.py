"""
Step 5: Master event table — one row per event, what every downstream person queries.

Inputs : earnings_reactions.parquet (Step 4) + prices.parquet (Step 2)
Output : events.parquet

Adds on top of earnings_reactions:
  event_id                   — "{TICKER}_{YYYYMMDD}"
  reaction_start/_end        — trading-day dates of the reaction window
  pre_event_price            — adj_close at reaction_start (denominator of reaction_return)
  reaction_return_excess     — reaction_return minus SPY over the same window
  reaction_return_zscore     — reaction_return / pre_event_30d_vol
  is_significant             — |zscore| > Z_THRESHOLD AND |reaction| > R_THRESHOLD

Columns are renamed to match the canonical hackathon schema.
"""
import numpy as np
import pandas as pd
from pathlib import Path

OUT          = Path("events.parquet")
Z_THRESHOLD  = 2.5    # reaction must be ≥ 2.5σ relative to the stock's own 30d vol
R_THRESHOLD  = 0.05   # AND move at least ±5% in absolute terms

# ── 1. Load inputs ───────────────────────────────────────────────────────────
print("Loading inputs …")
reactions = pd.read_parquet("earnings_reactions.parquet")
reactions['earnings_date'] = pd.to_datetime(reactions['earnings_date'])

# call_timestamp lives on earnings_events — pull it in via a left join
events_src = pd.read_parquet("earnings_events.parquet",
                             columns=['ticker', 'earnings_date', 'call_timestamp'])
events_src['earnings_date'] = pd.to_datetime(events_src['earnings_date'])
reactions = reactions.merge(events_src, on=['ticker', 'earnings_date'], how='left')

prices = pd.read_parquet("prices.parquet", columns=['ticker', 'date', 'adj_close'])
prices['date'] = pd.to_datetime(prices['date'])
prices = prices[prices['adj_close'] > 0].sort_values(['ticker', 'date']).reset_index(drop=True)
print(f"  reactions : {len(reactions):,}  |  prices : {len(prices):,}")

# Per-ticker (dates, adj_close) arrays for fast lookups
by_ticker = {t: (g['date'].values, g['adj_close'].values.astype(np.float64))
             for t, g in prices.groupby('ticker', sort=False)}
spy_dates, spy_adj = by_ticker['SPY']

# ── 2. Per-event: reaction window dates, pre-event price, SPY-reaction ───────
print("\nComputing reaction window dates + SPY reaction …")
rows = []
for ticker, grp in reactions.groupby('ticker', sort=False):
    td = by_ticker.get(ticker)
    if td is None:
        continue
    dates, adj = td
    n = len(dates)
    for ev in grp.itertuples(index=False):
        ed = np.datetime64(ev.earnings_date, 'ns')
        Ti = np.searchsorted(dates, ed, side='left')
        is_trading = (Ti < n) and (dates[Ti] == ed)
        is_bmo     = (ev.before_market_open_or_after_close == 'BMO')

        if is_trading and is_bmo:           # reaction = close[T-1] → close[T]
            s, e = Ti - 1, Ti
        elif is_trading and not is_bmo:     # reaction = close[T]   → close[T+1]
            s, e = Ti, Ti + 1
        else:                               # non-trading earnings date
            s, e = Ti - 1, Ti
        if s < 0 or e >= n:
            continue

        start_d = dates[s]
        end_d   = dates[e]
        pre_px  = float(adj[s])

        # SPY reaction over the exact same two trading dates
        ss = np.searchsorted(spy_dates, start_d, side='left')
        ee = np.searchsorted(spy_dates, end_d,   side='left')
        if (ss < len(spy_dates) and ee < len(spy_dates)
                and spy_dates[ss] == start_d and spy_dates[ee] == end_d):
            spy_reaction = float(spy_adj[ee] / spy_adj[ss] - 1)
        else:
            spy_reaction = np.nan

        rows.append((ticker, ev.earnings_date, start_d, end_d, pre_px, spy_reaction))

extra = pd.DataFrame(rows, columns=[
    'ticker', 'earnings_date', 'reaction_start', 'reaction_end',
    'pre_event_price', 'spy_reaction',
])
extra['reaction_start'] = pd.to_datetime(extra['reaction_start'])
extra['reaction_end']   = pd.to_datetime(extra['reaction_end'])
print(f"  computed for {len(extra):,} / {len(reactions):,} events")

# ── 3. Merge + rename to target schema ───────────────────────────────────────
print("\nJoining and renaming …")
m = reactions.merge(extra, on=['ticker', 'earnings_date'], how='inner')

m['event_id'] = (m['ticker'] + '_' + m['earnings_date'].dt.strftime('%Y%m%d'))
m['reaction_return_excess'] = m['reaction_return'] - m['spy_reaction']
m['reaction_return_zscore'] = m['reaction_return'] / m['baseline_vol_30d'].replace(0, np.nan)
m['is_significant'] = (
    m['reaction_return_zscore'].abs() > Z_THRESHOLD
) & (m['reaction_return'].abs() > R_THRESHOLD)

events = m.rename(columns={
    'before_market_open_or_after_close': 'bmo_or_amc',
    'baseline_vol_30d':                  'pre_event_30d_vol',
    'fwd_return_1d':                     'fwd_1d',
    'fwd_return_5d':                     'fwd_5d',
    'fwd_return_20d':                    'fwd_20d',
    'fwd_return_1d_excess_spy':          'fwd_1d_excess',
    'fwd_return_5d_excess_spy':          'fwd_5d_excess',
    'fwd_return_20d_excess_spy':         'fwd_20d_excess',
    'transcripts_id':                    'transcript_id',
})

events = events[[
    # Identifiers
    'event_id', 'ticker', 'earnings_date', 'fiscal_quarter', 'sector',
    # Timing
    'call_timestamp', 'bmo_or_amc', 'reaction_start', 'reaction_end',
    # Market data
    'pre_event_price',
    'reaction_return', 'reaction_return_excess',
    'fwd_1d', 'fwd_5d', 'fwd_20d',
    'fwd_1d_excess', 'fwd_5d_excess', 'fwd_20d_excess',
    'pre_event_30d_vol', 'reaction_return_zscore',
    # Significance
    'is_significant',
    # Link to text
    'transcript_id',
]].sort_values(['ticker', 'earnings_date']).reset_index(drop=True)

# ── 4. Report + save ────────────────────────────────────────────────────────
print(f"\nFinal events table: {events.shape}")
n_sig = events['is_significant'].sum()
print(f"  is_significant = True : {n_sig:,}  ({100*n_sig/len(events):.1f}%)")
print(f"    (threshold: |zscore| > {Z_THRESHOLD} AND |reaction| > {R_THRESHOLD:.0%})")

print("\nSignificance break-down by BMO/AMC:")
print(events.groupby('bmo_or_amc')['is_significant'].agg(['count', 'sum']).to_string())

print("\nReaction distribution on significant events:")
sig = events[events['is_significant']]
print(sig['reaction_return'].describe(percentiles=[.05, .5, .95]).to_string())

print("\nTop 10 significant AAPL-like names by |reaction|:")
top = sig.loc[sig['reaction_return'].abs().sort_values(ascending=False).index].head(10)
print(top[['event_id', 'bmo_or_amc', 'reaction_return',
          'reaction_return_zscore', 'fwd_5d_excess']].to_string(index=False))

events.to_parquet(OUT, index=False)
print(f"\nSaved → {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB)")
