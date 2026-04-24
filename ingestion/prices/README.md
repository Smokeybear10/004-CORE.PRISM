# `ingestion/prices/` — Yahoo Finance pipeline

Produces the master event table that downstream modules (model, backtest) consume.

Source: HuggingFace dataset `defeatbeta/yahoo-finance-data` (15 parquet tables:
prices, SEC filings, transcripts, earnings calendar, splits, dividends, etc.).

## Run order

Run these scripts sequentially from this directory. Each writes a parquet to
the current working directory, which the next script reads.

```bash
cd ingestion/prices/

python build_price_panel.py         # → prices.parquet              (~1 GB,  gitignored)
python build_earnings_events.py     # → earnings_events.parquet     (~2 MB)
python build_earnings_reactions.py  # → earnings_reactions.parquet  (~16 MB)
python build_events_table.py        # → events.parquet              (~16 MB)
python build_focal_universe.py      # → events_focal.parquet        (~5 MB)
python sanity_checks.py             # verification (no output file)
python audit_pitfalls.py            # verification (no output file)
```

`dataset_exploration.py` and `explore_dataset.py` are one-off scripts that
discover the source dataset's structure; they don't need to run again.

All parquet outputs are gitignored (repo-wide `*.parquet` rule). Regenerate
locally by running the pipeline.

## What each step does

| Script | Purpose |
|---|---|
| `build_price_panel.py` | Load 34.6M daily bars across 11,046 tickers (1994-2026). Adjust close for dividends (splits are already baked into the source — verified). Compute log returns. |
| `build_earnings_events.py` | Anchor on transcripts (228k events back to 2006). Match each event to an SEC 8-K on the same day; derive BMO/AMC from `acceptance_date_time` (UTC → ET). Backfill missing tags via per-ticker consensus. Filter to events with ≥60 trading days before and ≥20 after. |
| `build_earnings_reactions.py` | For each event compute reaction return (BMO: `close[T]/close[T-1]-1`, AMC: `close[T+1]/close[T]-1`), forward 1/5/20d returns, SPY-neutralized excess, synthetic-EW sector-neutralized excess, and 30d/60d baseline realized vol. |
| `build_events_table.py` | Add `event_id`, `reaction_start`/`reaction_end` dates, `pre_event_price`, `reaction_return_excess` (vs SPY), `reaction_return_zscore`, and the `is_significant` flag. Renames columns to the canonical hackathon schema. |
| `build_focal_universe.py` | Filter to the sectors relevant to the focal companies (currently Healthcare, Consumer Defensive, Industrials, Technology, Basic Materials), drop junk-adj_close rows (`|reaction| > 50%` or zero vol), and add an `is_focal` flag for the target tickers (ABT, ACU, AIR, AMD, APD). |
| `sanity_checks.py` | Spot-check 5 famous events (META Q4'21 ≈ −26%, NVDA Q1'24 ≈ +24%, etc.), verify reaction-distribution symmetry, confirm forward returns are ≈ zero-mean (no leakage), and count extreme reactions. |
| `audit_pitfalls.py` | Deep audit against the 4 classic pitfalls: timezone, split/dividend adjustment, release-vs-call date alignment, and ticker changes / dual-class double counting. |

## Output: `events_focal.parquet`

One row per (ticker, earnings_date). 43,463 events across 1,356 tickers in
the 3 focal sectors.

### Schema

| Column | Type | Notes |
|---|---|---|
| `event_id` | str | `"{TICKER}_{YYYYMMDD}"` — globally unique |
| `ticker` | str | |
| `earnings_date` | datetime | Date of the earnings announcement |
| `fiscal_quarter` | int | 1-4 |
| `sector` | str | Yahoo sector (Technology / Healthcare / Communication Services) |
| `call_timestamp` | datetime[UTC] | SEC 8-K acceptance time (null for consensus-filled rows) |
| `bmo_or_amc` | str | `"BMO"` (reaction = day T) or `"AMC"` (reaction = day T+1) |
| `reaction_start` | datetime | Trading day at start of reaction window |
| `reaction_end` | datetime | Trading day at end of reaction window |
| `pre_event_price` | float | adj_close at `reaction_start` |
| `reaction_return` | float | Raw return over the reaction window, e.g. `-0.073` for -7.3% |
| `reaction_return_excess` | float | `reaction_return` − SPY over same window |
| `fwd_1d` / `fwd_5d` / `fwd_20d` | float | Close-to-close from `reaction_end` forward N trading days |
| `fwd_{1,5,20}d_excess` | float | Same minus SPY over same window |
| `pre_event_30d_vol` | float | Std of daily log returns in the 30 trading days ending before the event |
| `reaction_return_zscore` | float | `reaction_return / pre_event_30d_vol` |
| `is_significant` | bool | `|zscore| > 2.5 AND |reaction_return| > 0.05` |
| `is_focal` | bool | `ticker in {ABT, ACU, AIR, AMD, APD}` |
| `focal_company` | str | Human-readable company name (null for peers) |
| `transcript_id` | int | Foreign key to the transcripts table |

### Mapping to `schema.PriceMove`

```python
PriceMove(
    ticker           = row.ticker,
    move_date        = row.reaction_end.date(),   # day the reaction completes
    return_pct       = row.reaction_return,
    vol_zscore       = row.reaction_return_zscore,
    is_significant   = row.is_significant,
)
```

`volume_zscore` and `magnitude_rank` aren't computed in this pipeline yet —
add if the model needs them.

## Focal universe

5 target companies (flagged `is_focal=True`) spanning 5 sectors:

| Ticker | Company | Sector | Events | Significant |
|---|---|---|---|---|
| ABT | Abbott Laboratories | Healthcare | 74 | 7 |
| ACU | Acme United Corporation | Consumer Defensive | 13 | 0 |
| AIR | AAR Corp | Industrials | 53 | 29 |
| AMD | Advanced Micro Devices | Technology | 76 | 16 |
| APD | Air Products and Chemicals | Basic Materials | 74 | 16 |

**68 significant events** across the focal tickers — a workable hackathon
size. Peers in the same 5 sectors contribute another ~19,500 significant
events available for sector-relative baselines.

### Note on dual-class tickers in the peer set

`GOOG/GOOGL`, `FOX/FOXA`, and `NWS/NWSA` appear in the Technology /
Communication Services peer set as separate events for the same underlying
earnings. For aggregate statistics, drop the non-voting class (median
`|GOOG - GOOGL|` reaction diff is ~0.08%, effectively the same signal).

## Data-quality notes (verified in `audit_pitfalls.py`)

1. **Timezones**: SEC `acceptance_date_time` is UTC; converted to `America/New_York`
   before applying 09:30 / 16:00 cutoffs.
2. **Splits already adjusted in source**: `build_price_panel.py` does NOT re-apply
   split adjustment (verified against NVDA 4:1/10:1 and AAPL 7:1/4:1 — stored
   close shows no jump across split days). Dividends ARE applied (verified on
   AT&T ex-div drops).
3. **Release = call date** for 71% of events with an SEC 8-K timestamp (rest
   use per-ticker consensus).
4. **Ticker renames**: `FB → META` and `SQ → XYZ` are consolidated cleanly in
   the source. `TWTR` is absent (delisted Oct 2022).
