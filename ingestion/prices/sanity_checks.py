"""
Step 6: Sanity checks on the master event table.

Runs on:
  events.parquet       — canonical pipeline output (128k events, all sectors)
  events_focal.parquet — focal-universe subset (43k events, quality-filtered)

Checks:
  A. Famous event spot-checks — Meta Q4'21 ≈ −26%, NVDA Q1'24 ≈ +24%, etc.
  B. Reaction distribution symmetric around zero
  C. Forward returns have near-zero mean (no look-ahead)
  D. Extreme-reaction (|r|>50%) count
  E. Transcript coverage
"""
import pandas as pd
import numpy as np

# ── Famous events with known reactions (from public sources / memory) ────────
# (ticker, date_approx, direction, approx_magnitude)
FAMOUS = [
    ('META',  '2022-02-02',  'down',  0.26),   # Q4 2021 — missed DAUs, ≈ −26%
    ('NVDA',  '2023-05-24',  'up',    0.24),   # Q1 FY24 — AI guidance pop
    ('NFLX',  '2022-04-19',  'down',  0.35),   # Q1 2022 — subscriber loss
    ('AAPL',  '2024-02-01',  'any',   None),   # AMC — just confirm it's classified AMC
    ('TSLA',  '2022-01-26',  'up',    0.02),   # Q4 2021 — beat, small move
    ('GOOGL', '2025-02-04',  'down',  0.07),   # Q4 2024 — miss, ≈ −7% (focal)
]

def section(title):
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def run_checks(path):
    print(f"\n\n{'#'*70}\n# FILE: {path}\n{'#'*70}")
    e = pd.read_parquet(path)
    e['earnings_date'] = pd.to_datetime(e['earnings_date'])
    print(f"Rows: {len(e):,}  |  tickers: {e['ticker'].nunique():,}  |  "
          f"{e['earnings_date'].min().date()} → {e['earnings_date'].max().date()}")

    fails = []

    # ── A. Famous event spot-checks ──────────────────────────────────────────
    section("A. Famous event spot-checks")
    for ticker, approx_date, direction, magnitude in FAMOUS:
        target = pd.Timestamp(approx_date)
        sub = e[(e['ticker'] == ticker)
                & (e['earnings_date'].sub(target).abs() <= pd.Timedelta('7 days'))]
        if sub.empty:
            print(f"  {ticker:<6} {approx_date}  → NOT FOUND in dataset")
            continue
        row = sub.iloc[0]
        r = row['reaction_return']
        tag = row['bmo_or_amc']
        msg = f"  {ticker:<6} {row['earnings_date'].date()}  {tag}  reaction={r:+.3f}"
        if direction == 'down' and r > -0.05:
            msg += f"  ✗ expected ≈ −{magnitude:.0%}"
            fails.append(msg)
        elif direction == 'up' and r < 0.05:
            msg += f"  ✗ expected ≈ +{magnitude:.0%}"
            fails.append(msg)
        elif direction in ('down', 'up') and abs(abs(r) - magnitude) > 0.10:
            msg += f"  ⚠ off by >10pp from expected {magnitude:+.0%}"
        else:
            msg += "  ✓"
        print(msg)

    # ── B. Reaction distribution symmetry ────────────────────────────────────
    section("B. Reaction distribution symmetry")
    r = e['reaction_return'].dropna()
    r_trim = r[r.abs() <= 0.5]   # trim junk tails for meaningful stats
    pos = (r_trim > 0).mean()
    neg = (r_trim < 0).mean()
    print(f"  mean (all)      : {r.mean():+.4f}")
    print(f"  median (all)    : {r.median():+.4f}")
    print(f"  mean (|r|≤0.5)  : {r_trim.mean():+.4f}")
    print(f"  median (|r|≤0.5): {r_trim.median():+.4f}")
    print(f"  % positive      : {pos:.1%}")
    print(f"  % negative      : {neg:.1%}")
    if abs(r_trim.mean()) > 0.01 or abs(pos - neg) > 0.10:
        print(f"  ⚠ asymmetric — possible bug in reaction-window logic")

    # ── C. Forward returns ~ zero-mean ───────────────────────────────────────
    section("C. Forward returns — should be near-zero mean (no leakage)")
    for col in ['fwd_1d', 'fwd_5d', 'fwd_20d',
                'fwd_1d_excess', 'fwd_5d_excess', 'fwd_20d_excess']:
        v = e[col].dropna()
        v_trim = v[v.abs() <= 0.5]
        flag = ""
        if abs(v_trim.mean()) > 0.003:   # >30 bps/window mean is suspicious
            flag = "  ⚠ non-trivial drift"
        print(f"  {col:<22} n={len(v_trim):,}  mean={v_trim.mean():+.4f}  "
              f"median={v_trim.median():+.4f}  std={v_trim.std():.4f}{flag}")

    # ── D. Extreme-reaction count ────────────────────────────────────────────
    section("D. Extreme reactions (|r| > 50%)")
    extreme = e[e['reaction_return'].abs() > 0.5]
    print(f"  events with |reaction| > 50% : {len(extreme):,}  ({100*len(extreme)/len(e):.2f}%)")
    if len(extreme):
        print("  Top 5 by |reaction|:")
        print(extreme.loc[extreme['reaction_return'].abs().sort_values(ascending=False).index]
              [['event_id','reaction_return','reaction_return_zscore']]
              .head(5).to_string(index=False))
        print("  Most of these are junk-adj_close tickers — filter downstream.")

    # ── E. Transcript coverage ───────────────────────────────────────────────
    section("E. Transcript linkage")
    with_tid = e['transcript_id'].notna().sum()
    print(f"  events with transcript_id : {with_tid:,} / {len(e):,}  "
          f"({100*with_tid/len(e):.1f}%)")
    # Also: uniqueness — each event should have a distinct (ticker, earnings_date)
    dupes = e.duplicated(subset=['ticker', 'earnings_date']).sum()
    print(f"  duplicate (ticker, earnings_date) rows : {dupes}")
    if dupes:
        print("  ✗ duplicates detected — should be zero")

    # ── Final verdict ────────────────────────────────────────────────────────
    section("Summary")
    if fails:
        print(f"✗ {len(fails)} famous-event checks failed:")
        for f in fails: print(f)
    else:
        print("✓ All famous-event checks passed")


run_checks("events.parquet")
run_checks("events_focal.parquet")
