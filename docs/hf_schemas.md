# HuggingFace dataset schemas — `BridgewaterAIHackathon/BW-AI-Hackathon`

Private repo. Auth: `huggingface-cli login`, then pass `token=True` to any
loader. Files live directly under `Structured_Data/SNE/yahoo-finance-data/` —
**no `data/` subfolder** (unlike the public `defeatbeta/yahoo-finance-data`
mirror).

Schemas below were verified via parquet-footer probe (`HfFileSystem` +
`pq.read_metadata`) on **2026-04-24**. Footer probes only pull the last few
KB of each file, not the whole parquet — do the same if you need to re-verify
rather than calling `load_dataset` or `df.head()`, which download everything.

## Global gotchas (apply broadly)

1. Ticker column is `symbol`, not `ticker`. Rename on load.
2. `report_date` is a string (`YYYY-MM-DD`) on every parquet. Parse once.
3. All prices / amounts / financials are `decimal128(38, 2)`. Cast to float64
   before any math — pandas won't auto-convert.
4. `stock_prices` has no `adj_close`. Join `stock_split_events` +
   `stock_dividend_events` or document the non-adjustment.
5. Survivorship bias — delisted companies are absent. Flag in the demo.

## Files we consume

### `stock_prices.parquet` — daily OHLCV per ticker
- **Rows:** 34,462,350 | **Row groups:** 1,624
- **Columns:**
  - `symbol` — string (ticker)
  - `report_date` — string, `YYYY-MM-DD` (parse to `date`)
  - `open` / `close` / `high` / `low` — `decimal128(38, 2)` (cast to float64)
  - `volume` — int64
- **Gotcha:** no `adj_close`. Must adjust via `stock_split_events` +
  `stock_dividend_events`, or document the non-adjustment in the demo.

### `stock_split_events.parquet` — stock splits
- **Rows:** 9,001 | **Row groups:** 1
- **Columns:**
  - `symbol` — string
  - `report_date` — string, `YYYY-MM-DD` (ex-date)
  - `split_factor` — string, e.g. `"2:1"`, `"1:3"` (needs parsing)

### `stock_dividend_events.parquet` — dividend distributions
- **Rows:** 296,326 | **Row groups:** 1
- **Columns:**
  - `symbol` — string
  - `report_date` — string, `YYYY-MM-DD` (ex-date)
  - `amount` — `decimal128(38, 2)` (cast to float64)

### `stock_earning_calendar.parquet` — earnings release schedule
- **Rows:** 59,382 | **Row groups:** 5
- **Columns:**
  - `symbol` — string
  - `report_date` — string, `YYYY-MM-DD` (earnings date)
  - `time` — string (`pre` / `post` market)
  - `name` — string (company name)
  - `fiscal_quarter_ending` — string (fiscal period end)
- **Use:** mask earnings-day moves from idiosyncratic attribution so
  earnings surprises don't get double-counted.

### `stock_news.parquet` — dated headlines + body paragraphs
- **Rows:** 806,420 | **Row groups:** 605
- **Columns:**
  - `uuid` — string (unique article id)
  - `related_symbols` — string, comma-separated tickers
  - `title` — string
  - `publisher` — string
  - `report_date` — string, `YYYY-MM-DD`
  - `type` — string (article kind)
  - `link` — string (URL)
  - `news` — `list<struct<paragraph_number: int32, highlight: string, paragraph: string>>`
- **Use:** paragraphs from `news[*].paragraph` become `TextChunk` records for
  the attribution model.

### `company_tickers.json` — ticker ↔ CIK mapping
- **Size:** 1.25 MB (not parquet)
- **Use:** link 13F filings (keyed on CIK) to tickers.
- **Format:** JSON dict keyed by sequential integers; each value is
  `{cik_str, ticker, title}`.

## Files available but not yet used

### `stock_profile.parquet` — company metadata
- **Rows:** 10,408 | **Size:** 2.62 MB
- **Columns:** `symbol, address, city, country, phone, zip, industry, sector,
  long_business_summary, full_time_employees, web_site, report_date`
- **Use:** sector/industry filters; biotech-only FDA cohorts.

### `stock_officers.parquet` — executive roster
- **Rows:** 73,789 | **Size:** 1.29 MB
- **Columns:** `symbol, name, title, age, born, pay, exercised, unexercised`
  (all ints for the numeric fields)

### `stock_shares_outstanding.parquet` — share counts over time
- **Rows:** 1,014,408 | **Size:** 4.09 MB
- **Columns:** `symbol, report_date, shares_outstanding` (int64)
- **Use:** market-cap math; float-percent denominators for short interest.

### `stock_tailing_eps.parquet` — TTM and period EPS
- **Rows:** 466,868 | **Size:** 1.54 MB
- **Columns:** `symbol, report_date, tailing_eps, eps, update_time`
  (decimals cast to float64)

### `stock_statement.parquet` — long-format financial statements
- **Rows:** 26,887,146 | **Size:** 110 MB
- **Columns:** `symbol, report_date, item_name, item_value, finance_type, period_type`
- **Shape:** one row per (ticker, period, line item). Pivot on `item_name` to
  get wide-format statements. Filter by `finance_type` (statement family) and
  `period_type` (annual / quarterly).

### `stock_revenue_breakdown.parquet` — segment / geographic revenue splits
- **Rows:** 45,321 | **Size:** 274 kB
- **Columns:** `symbol, breakdown_type, report_date, item_name, item_value`

### `exchange_rate.parquet` — FX daily OHLC
- **Rows:** 237,351 | **Size:** 1.8 MB
- **Columns:** `symbol` (currency pair), `report_date, open, close, high, low`
  (decimal128 → float64)

### `daily_treasury_yield.parquet` — US Treasury constant-maturity yields
- **Rows:** 9,070 | **Size:** 138 kB
- **Columns:** `report_date`, `bc1_month`, `bc2_month`, `bc3_month`, `bc6_month`,
  `bc1_year`, `bc2_year`, `bc3_year`, `bc5_year`, `bc7_year`, `bc10_year`,
  `bc30_year` (all `decimal128(16, 4)` → float64)
- **Note:** macro, not per-ticker. No `symbol` column.

## Other folders in the repo (not schema'd here)

- `Structured_Data/SNE/USA_Factor_Returns/*.csv` — monthly USA factor return
  series (age-based, GICS, all-themes). Candidate for factor decomposition of
  price moves; not currently wired into ingestion.
- `Unstructured_Data/SNE/finance_bench/financebench_merged.jsonl` — FinanceBench
  QA eval set.
- `Unstructured_Data/SNE/financial_reports_sec/` — sharded JSONL of SEC filings,
  train/test splits.
- `Unstructured_Data/SNE/ yahoo_finance /stock_sec_filing.parquet` — note the
  spaces in the folder name; was recently moved out of `yahoo-finance-data/`.

Reprobe these with the footer idiom below if you need their schemas.

## Usage notes

- Footer probe idiom (no full download):
  ```python
  from huggingface_hub import HfFileSystem
  import pyarrow.parquet as pq
  fs = HfFileSystem()
  with fs.open("datasets/BridgewaterAIHackathon/BW-AI-Hackathon/.../stock_prices.parquet", "rb") as f:
      md = pq.read_metadata(f)
  ```
- For full reads, prefer `pq.read_table(path, filesystem=fs, columns=[...], filters=[...])`
  to push down projections and predicates where possible.
- `report_date` is a string on every file. Parse once via
  `pd.to_datetime(df["report_date"]).dt.date` and don't re-parse downstream.
- `decimal128(38, 2)` arrives as `decimal.Decimal` unless cast. Cast with
  `df["close"] = df["close"].astype("float64")` (Pandas handles the conversion).
