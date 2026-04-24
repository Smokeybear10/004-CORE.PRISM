"""
Tests for `prices.load_prices` — decimal handling, column contracts, as_of
filtering, ticker filtering, and cache round-trip behavior.

All tests use the `patched_loader` / `spy_reader` fixtures from conftest,
which redirect HF reads to local fixture parquets and route the cache to
tmp_path.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pytest


# ---------- dtype and schema contract ----------


def test_decimal_ohlc_cast_to_float64(patched_loader):
    df = patched_loader.load_prices(tickers=["TCK000"], as_of=date(2024, 3, 30))
    assert not df.empty
    for col in ("open", "close", "high", "low"):
        assert df[col].dtype == np.float64
        assert not isinstance(df[col].iloc[0], Decimal)
    assert df["volume"].dtype == np.int64


def test_columns_are_renamed_and_ordered(patched_loader):
    df = patched_loader.load_prices(tickers=["TCK000"], as_of=date(2024, 3, 30))
    assert list(df.columns) == ["ticker", "date", "open", "close", "high", "low", "volume"]
    assert df["ticker"].iloc[0] == "TCK000"


def test_dates_are_python_date_objects(patched_loader):
    df = patched_loader.load_prices(tickers=["TCK000"], as_of=date(2024, 3, 30))
    assert isinstance(df["date"].iloc[0], date)


# ---------- as_of semantics (no foreknowledge) ----------


def test_as_of_filter_excludes_future_rows(patched_loader):
    as_of = date(2024, 2, 15)
    df = patched_loader.load_prices(tickers=None, as_of=as_of)
    assert not df.empty
    assert df["date"].max() <= as_of

    # Fixture definitely has data past the cutoff.
    later = patched_loader.load_prices(tickers=None, as_of=date(2024, 12, 31))
    assert later["date"].max() > as_of


def test_as_of_is_end_inclusive(patched_loader):
    as_of = date(2024, 1, 10)
    df = patched_loader.load_prices(tickers=["TCK000"], as_of=as_of)
    assert as_of in set(df["date"])


def test_very_early_as_of_returns_empty(patched_loader):
    df = patched_loader.load_prices(tickers=["TCK000"], as_of=date(2020, 1, 1))
    assert df.empty
    # Even empty, the column contract holds.
    assert list(df.columns) == ["ticker", "date", "open", "close", "high", "low", "volume"]


# ---------- ticker filtering ----------


def test_ticker_filter_narrows_universe(patched_loader):
    df = patched_loader.load_prices(tickers=["TCK003", "TCK007"], as_of=date(2024, 3, 30))
    assert set(df["ticker"].unique()) == {"TCK003", "TCK007"}


def test_tickers_none_returns_full_universe(patched_loader):
    df = patched_loader.load_prices(tickers=None, as_of=date(2024, 12, 31))
    assert set(df["ticker"].unique()) == {f"TCK{i:03d}" for i in range(10)}


def test_ticker_filter_is_case_insensitive(patched_loader):
    df = patched_loader.load_prices(tickers=["tck000", "TcK003"], as_of=date(2024, 3, 30))
    assert set(df["ticker"].unique()) == {"TCK000", "TCK003"}


def test_unknown_ticker_yields_empty_frame(patched_loader):
    df = patched_loader.load_prices(tickers=["NOPE"], as_of=date(2024, 12, 31))
    assert df.empty
    assert list(df.columns) == ["ticker", "date", "open", "close", "high", "low", "volume"]


def test_duplicate_tickers_deduped_in_results(patched_loader):
    df = patched_loader.load_prices(tickers=["TCK000", "TCK000"], as_of=date(2024, 3, 30))
    # One date per ticker per day — duplicates in the arg shouldn't produce duplicate rows.
    assert df.duplicated(subset=["ticker", "date"]).sum() == 0


# ---------- cache behavior ----------


def test_full_read_populates_cache(spy_reader):
    loader, calls = spy_reader
    as_of = date(2024, 6, 30)

    first = loader.load_prices(tickers=None, as_of=as_of)
    cache_path = loader.CACHE_DIR / f"stock_prices_{as_of.isoformat()}.parquet"
    assert cache_path.exists(), "full-universe read should write the cache file"
    assert len(calls) == 1  # exactly one HF read so far

    # Second call should hit the cache, not call the reader again.
    second = loader.load_prices(tickers=None, as_of=as_of)
    assert len(calls) == 1, "second call must be a cache hit, not an HF read"

    assert first.shape == second.shape
    assert first.equals(second)


def test_partial_read_does_not_write_cache(spy_reader):
    loader, calls = spy_reader
    as_of = date(2024, 6, 30)

    loader.load_prices(tickers=["TCK000"], as_of=as_of)
    cache_path = loader.CACHE_DIR / f"stock_prices_{as_of.isoformat()}.parquet"
    assert not cache_path.exists(), "partial reads must not poison the cache"
    assert len(calls) == 1

    # A subsequent full read must actually hit HF (no stale partial cache to mislead it).
    loader.load_prices(tickers=None, as_of=as_of)
    assert len(calls) == 2
    assert cache_path.exists()


def test_cache_filter_still_respects_tickers(spy_reader):
    loader, calls = spy_reader
    as_of = date(2024, 6, 30)

    loader.load_prices(tickers=None, as_of=as_of)  # populate cache
    cached_narrow = loader.load_prices(tickers=["TCK003"], as_of=as_of)
    assert len(calls) == 1, "narrowed read off the cache must not hit HF"
    assert set(cached_narrow["ticker"].unique()) == {"TCK003"}


# ---------- value sanity ----------


def test_ohlc_ordering_invariant_holds(patched_loader):
    df = patched_loader.load_prices(tickers=["TCK000"], as_of=date(2024, 3, 30))
    assert (df["low"] <= df["high"]).all()
    assert (df["low"] <= df["open"]).all()
    assert (df["low"] <= df["close"]).all()
    assert (df["high"] >= df["open"]).all()
    assert (df["high"] >= df["close"]).all()


@pytest.mark.parametrize("col", ["open", "close", "high", "low", "volume"])
def test_no_nulls_in_core_columns(patched_loader, col):
    df = patched_loader.load_prices(tickers=None, as_of=date(2024, 6, 30))
    assert df[col].notna().all(), f"column {col} should not contain nulls in the fixture"
