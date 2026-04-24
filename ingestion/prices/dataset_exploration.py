"""
defeatbeta/yahoo-finance-data — Dataset Exploration Summary
All 15 parquet tables, schemas, and key findings for pipeline design.
"""
from huggingface_hub import HfFileSystem, hf_hub_download
import pyarrow.parquet as pq
import pandas as pd

REPO = "defeatbeta/yahoo-finance-data"
fs = HfFileSystem()


def sample_remote(fname, n=3):
    with fs.open(f"datasets/{REPO}/{fname}", "rb") as f:
        pf = pq.ParquetFile(f)
        batch = next(pf.iter_batches(batch_size=n))
    return batch.to_pandas()


def sample_local(fname, n=3):
    path = hf_hub_download(REPO, fname, repo_type="dataset")
    return pd.read_parquet(path).head(n)


# ────────────────────────────────────────────────────────────────────────────
# KEY TABLES FOR THE PIPELINE
# ────────────────────────────────────────────────────────────────────────────

# 1. PRICES — daily OHLCV (no adjusted close; needs split/dividend adjustment)
print("=== stock_prices ===")
print("Columns: symbol, report_date (str YYYY-MM-DD), open, close, high, low, volume")
print("NOTE: No adj_close column — must compute using stock_split_events + stock_dividend_events")
df = sample_remote("data/stock_prices.parquet")
print(df)

# 2. EARNINGS CALENDAR — separate table, keyed by symbol + report_date
print("\n=== stock_earning_calendar ===")
print("Columns: symbol, report_date, time (BMO/AMC/time-not-supplied), name, fiscal_quarter_ending")
print("Use report_date as the earnings announcement date for event windows")
df = sample_local("data/stock_earning_calendar.parquet")
print(df)

# 3. TRANSCRIPTS — keyed by symbol + fiscal_year + fiscal_quarter + report_date
print("\n=== stock_earning_call_transcripts ===")
print("Columns: symbol, fiscal_year (int), fiscal_quarter (int), report_date, transcripts (list of paragraphs), transcripts_id")
print("transcripts is a list<struct{paragraph_number, speaker, content}>")
df = sample_remote("data/stock_earning_call_transcripts.parquet")
print(df[["symbol", "fiscal_year", "fiscal_quarter", "report_date", "transcripts_id"]])

# 4. FINANCIALS — income statement, balance sheet, cash flow in long format
print("\n=== stock_statement ===")
print("Columns: symbol, report_date, item_name, item_value, finance_type (income_statement/balance_sheet/cash_flow), period_type (annual/quarterly)")
df = sample_remote("data/stock_statement.parquet")
print(df)

# 5. TRAILING EPS — quarterly trailing EPS series
print("\n=== stock_tailing_eps ===")
print("Columns: symbol, report_date, tailing_eps, eps, update_time")
df = sample_remote("data/stock_tailing_eps.parquet")
print(df)

# 6. DIVIDENDS + SPLITS — for price adjustment
print("\n=== stock_dividend_events ===")
df = sample_local("data/stock_dividend_events.parquet")
print(df)

print("\n=== stock_split_events ===")
print("split_factor format: '2:1' means 2-for-1 split")
df = sample_local("data/stock_split_events.parquet")
print(df)

# 7. NEWS — full article text, keyed by uuid + related_symbols
print("\n=== stock_news ===")
print("Columns: uuid, related_symbols (ticker string), title, publisher, report_date, type, link, news (list of paragraphs)")
df = sample_remote("data/stock_news.parquet")
print(df[["related_symbols", "title", "publisher", "report_date", "type"]])

# 8. MACRO
print("\n=== daily_treasury_yield ===")
print("Columns: report_date, bc1_month..bc30_year (decimal rates, not percentages)")
df = sample_local("data/daily_treasury_yield.parquet")
print(df)

print("\n=== exchange_rate ===")
df = sample_local("data/exchange_rate.parquet")
print(df)

# ────────────────────────────────────────────────────────────────────────────
# KEY FINDINGS
# ────────────────────────────────────────────────────────────────────────────
print("""
=== KEY FINDINGS FOR PIPELINE DESIGN ===

1. PRICE COLUMNS: symbol, report_date (string), open, close, high, low, volume
   - NO adjusted_close column — must compute via cumulative split/dividend adjustments
   - Dates are strings (YYYY-MM-DD), not timestamps

2. EARNINGS EVENTS: Separate stock_earning_calendar table
   - Keyed by: symbol + report_date
   - Time-of-day: 'time-not-supplied', 'BMO' (before market open), 'AMC' (after market close)
   - fiscal_quarter_ending gives the quarter the report covers

3. TRANSCRIPTS: Keyed by symbol + fiscal_year + fiscal_quarter + report_date
   - report_date matches earning_calendar.report_date — safe to join on (symbol, report_date)
   - Content stored as list<struct{paragraph_number, speaker, content}>
   - Need to flatten: [p['content'] for p in row['transcripts']]

4. FINANCIALS: stock_statement in long (EAV) format
   - pivot on item_name to get wide format
   - finance_type: 'income_statement', 'balance_sheet', 'cash_flow'
   - period_type: 'annual', 'quarterly'

5. ALL dates are strings — need pd.to_datetime() at load time

6. TICKER UNIVERSE: company_tickers.json in repo root (not a parquet file)
""")
