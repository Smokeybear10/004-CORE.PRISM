"""
Tests for `load_splits` and `load_dividends` — column contracts, as_of
filtering, ordering, and (for dividends) decimal-to-float conversion.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd


# ---------- load_splits ----------


def test_load_splits_columns_and_order(patched_loader):
    df = patched_loader.load_splits("TCK003", as_of=date(2024, 12, 31))
    assert list(df.columns) == ["ticker", "date", "split_factor"]
    # Fixture: TCK003 has splits on 2024-01-20 and 2024-03-10 — sorted ascending.
    assert list(df["date"]) == [date(2024, 1, 20), date(2024, 3, 10)]
    assert list(df["split_factor"]) == ["2:1", "3:1"]
    assert (df["ticker"] == "TCK003").all()


def test_load_splits_as_of_filters_future(patched_loader):
    df = patched_loader.load_splits("TCK003", as_of=date(2024, 2, 1))
    assert len(df) == 1  # only the 2024-01-20 split qualifies
    assert df["date"].iloc[0] == date(2024, 1, 20)


def test_load_splits_as_of_is_inclusive(patched_loader):
    df = patched_loader.load_splits("TCK003", as_of=date(2024, 1, 20))
    assert len(df) == 1
    assert df["date"].iloc[0] == date(2024, 1, 20)


def test_load_splits_empty_for_unknown_ticker(patched_loader):
    df = patched_loader.load_splits("NOPE", as_of=date(2024, 12, 31))
    assert df.empty
    assert list(df.columns) == ["ticker", "date", "split_factor"]


def test_load_splits_is_case_insensitive(patched_loader):
    df = patched_loader.load_splits("tck003", as_of=date(2024, 12, 31))
    assert (df["ticker"] == "TCK003").all()
    assert len(df) == 2


def test_load_splits_does_not_leak_other_tickers(patched_loader):
    # Splits fixture includes TCKOTH which is not in prices — confirm isolation.
    df = patched_loader.load_splits("TCK000", as_of=date(2024, 12, 31))
    assert set(df["ticker"].unique()) == {"TCK000"}


# ---------- load_dividends ----------


def test_load_dividends_columns_and_dtypes(patched_loader):
    df = patched_loader.load_dividends("TCK000", as_of=date(2024, 12, 31))
    assert list(df.columns) == ["ticker", "date", "amount"]
    assert df["amount"].dtype == np.float64
    # Round-trip: Decimal('0.25') → 0.25 float.
    assert not isinstance(df["amount"].iloc[0], Decimal)
    assert df["amount"].iloc[0] == 0.25


def test_load_dividends_sorted_by_date(patched_loader):
    df = patched_loader.load_dividends("TCK000", as_of=date(2024, 12, 31))
    assert list(df["date"]) == sorted(df["date"])


def test_load_dividends_as_of_filters_future(patched_loader):
    df = patched_loader.load_dividends("TCK000", as_of=date(2024, 5, 1))
    # Fixture: TCK000 dividends on 2024-01-15 and 2024-04-15; 2024-07-15 is in the future.
    assert list(df["date"]) == [date(2024, 1, 15), date(2024, 4, 15)]


def test_load_dividends_empty_for_unknown_ticker(patched_loader):
    df = patched_loader.load_dividends("NOPE", as_of=date(2024, 12, 31))
    assert df.empty
    assert list(df.columns) == ["ticker", "date", "amount"]


def test_load_dividends_isolates_tickers(patched_loader):
    tck000 = patched_loader.load_dividends("TCK000", as_of=date(2024, 12, 31))
    tck005 = patched_loader.load_dividends("TCK005", as_of=date(2024, 12, 31))
    assert set(tck000["ticker"].unique()) == {"TCK000"}
    assert set(tck005["ticker"].unique()) == {"TCK005"}
    # Amounts differ — confirms no cross-contamination.
    assert tck000["amount"].iloc[0] != tck005["amount"].iloc[0]
