"""
One-time generator for the price-side HF fixtures:

  tests/fixtures/prices_sample.parquet      — OHLCV, decimal128 OHLC
  tests/fixtures/splits_sample.parquet      — stock splits
  tests/fixtures/dividends_sample.parquet   — dividend events, decimal128 amount

Schemas mirror the upstream HF files exactly so tests exercise the real
decimal-to-float conversion path.

Run with:
    .venv/Scripts/python.exe tests/fixtures/_generate_prices_sample.py
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

OUT_DIR = Path(__file__).parent

N_TICKERS = 10
N_DAYS = 90
START = date(2024, 1, 1)
SPIKE_TICKER = "TCK000"
SPIKE_DAY_INDEX = 75


# ---------- prices ----------

_PRICES_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("report_date", pa.string()),
        ("open", pa.decimal128(38, 2)),
        ("close", pa.decimal128(38, 2)),
        ("high", pa.decimal128(38, 2)),
        ("low", pa.decimal128(38, 2)),
        ("volume", pa.int64()),
    ]
)


def _build_prices() -> pa.Table:
    rng = np.random.default_rng(seed=42)
    tickers = [f"TCK{i:03d}" for i in range(N_TICKERS)]

    symbols: list[str] = []
    dates: list[str] = []
    opens: list[Decimal] = []
    closes: list[Decimal] = []
    highs: list[Decimal] = []
    lows: list[Decimal] = []
    volumes: list[int] = []

    for t in tickers:
        price = 100.0
        for i in range(N_DAYS):
            d = START + timedelta(days=i)
            r = float(rng.normal(loc=0.0002, scale=0.01))
            if t == SPIKE_TICKER and i == SPIKE_DAY_INDEX:
                r = 0.15
            price *= 1.0 + r
            o = price * (1.0 + float(rng.normal(0, 0.002)))
            c = price
            h = max(o, c) * (1.0 + abs(float(rng.normal(0, 0.002))))
            l = min(o, c) * (1.0 - abs(float(rng.normal(0, 0.002))))
            v = int(rng.integers(1_000_000, 5_000_000))

            symbols.append(t)
            dates.append(d.isoformat())
            opens.append(Decimal(f"{o:.2f}"))
            closes.append(Decimal(f"{c:.2f}"))
            highs.append(Decimal(f"{h:.2f}"))
            lows.append(Decimal(f"{l:.2f}"))
            volumes.append(v)

    return pa.table(
        {
            "symbol": symbols,
            "report_date": dates,
            "open": opens,
            "close": closes,
            "high": highs,
            "low": lows,
            "volume": volumes,
        },
        schema=_PRICES_SCHEMA,
    )


# ---------- splits ----------

_SPLITS_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("report_date", pa.string()),
        ("split_factor", pa.string()),
    ]
)


def _build_splits() -> pa.Table:
    # A mix of forward and reverse splits across a couple of tickers, plus
    # an off-fixture ticker so filters can prove they narrow the universe.
    rows = [
        ("TCK000", "2024-02-15", "2:1"),    # forward, mid-fixture
        ("TCK003", "2024-01-20", "2:1"),    # forward, early
        ("TCK003", "2024-03-10", "3:1"),    # second split, compounds
        ("TCK007", "2024-03-25", "1:10"),   # reverse split
        ("TCKOTH", "2024-02-01", "2:1"),    # ticker not in prices fixture
    ]
    return pa.table(
        {
            "symbol": [r[0] for r in rows],
            "report_date": [r[1] for r in rows],
            "split_factor": [r[2] for r in rows],
        },
        schema=_SPLITS_SCHEMA,
    )


# ---------- dividends ----------

_DIVIDENDS_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("report_date", pa.string()),
        ("amount", pa.decimal128(38, 2)),
    ]
)


def _build_dividends() -> pa.Table:
    rows = [
        ("TCK000", "2024-01-15", Decimal("0.25")),
        ("TCK000", "2024-04-15", Decimal("0.25")),  # quarterly cadence
        ("TCK000", "2024-07-15", Decimal("0.30")),  # raised
        ("TCK005", "2024-02-10", Decimal("1.50")),
        ("TCK005", "2024-05-10", Decimal("1.50")),
    ]
    return pa.table(
        {
            "symbol": [r[0] for r in rows],
            "report_date": [r[1] for r in rows],
            "amount": [r[2] for r in rows],
        },
        schema=_DIVIDENDS_SCHEMA,
    )


def main() -> None:
    pq.write_table(_build_prices(), OUT_DIR / "prices_sample.parquet")
    pq.write_table(_build_splits(), OUT_DIR / "splits_sample.parquet")
    pq.write_table(_build_dividends(), OUT_DIR / "dividends_sample.parquet")
    for name in ("prices_sample", "splits_sample", "dividends_sample"):
        print(f"wrote {OUT_DIR / (name + '.parquet')}")


if __name__ == "__main__":
    main()
