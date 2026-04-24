"""
Dataset exploration: defeatbeta/yahoo-finance-data
Uses HfFileSystem for schema-only reads on large files (no full download).
"""
from huggingface_hub import HfFileSystem, hf_hub_download
import pyarrow.parquet as pq
import pandas as pd

REPO = "defeatbeta/yahoo-finance-data"
fs = HfFileSystem()

# Files small enough to have already downloaded (cached):
CACHED = [
    "data/daily_treasury_yield.parquet",
    "data/exchange_rate.parquet",
    "data/stock_dividend_events.parquet",
    "data/stock_earning_calendar.parquet",
    "data/stock_officers.parquet",
    "data/stock_revenue_breakdown.parquet",
    "data/stock_split_events.parquet",
]

# Files too large to download fully — read schema + 3 rows via streaming:
LARGE = [
    "data/stock_prices.parquet",
    "data/stock_profile.parquet",
    "data/stock_shares_outstanding.parquet",
    "data/stock_tailing_eps.parquet",
    "data/stock_statement.parquet",
    "data/stock_sec_filing.parquet",
    "data/stock_earning_call_transcripts.parquet",
    "data/stock_news.parquet",
]


def inspect_parquet_remote(hf_path):
    """Read parquet schema and first 3 rows without downloading the whole file."""
    full_path = f"datasets/{REPO}/{hf_path}"
    with fs.open(full_path, "rb") as f:
        pf = pq.ParquetFile(f)
        schema = pf.schema_arrow
        # Read just first row group (or first batch)
        batch = next(pf.iter_batches(batch_size=3))
        df = batch.to_pandas()
    return schema, df


def inspect_parquet_local(local_path):
    schema = pq.read_schema(local_path)
    df = pd.read_parquet(local_path).head(3)
    return schema, df


print("=" * 70)
print("DATASET: defeatbeta/yahoo-finance-data")
print("=" * 70)

# ── Cached (small) files ──────────────────────────────────────────────────────
print("\n\n### SMALL FILES (cached locally) ###\n")
for fname in CACHED:
    print(f"\n{'─'*60}")
    print(f"FILE: {fname}")
    try:
        local = hf_hub_download(REPO, fname, repo_type="dataset")
        schema, df = inspect_parquet_local(local)
        cols = [(s.name, str(s.type)) for s in schema]
        print(f"  Columns: {cols}")
        print(f"  Row count (sample): {len(df)}")
        print(df.to_string(max_colwidth=50))
    except Exception as e:
        print(f"  ERROR: {e}")

# ── Large files — schema + 3 rows via HfFileSystem ───────────────────────────
print("\n\n### LARGE FILES (remote schema + 3-row sample) ###\n")
for fname in LARGE:
    print(f"\n{'─'*60}")
    print(f"FILE: {fname}")
    try:
        schema, df = inspect_parquet_remote(fname)
        cols = [(s.name, str(s.type)) for s in schema]
        print(f"  Columns: {cols}")
        print(df.to_string(max_colwidth=80))
    except Exception as e:
        print(f"  ERROR: {e}")
