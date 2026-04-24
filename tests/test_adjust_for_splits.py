"""
Tests for `adjust_for_splits` — backward-adjustment arithmetic, compounding,
idempotence guarantees, and isolation between tickers.

Convention (see prices/yahoo_loader.adjust_for_splits):
    "a:b"  means a new shares per b old shares
    factor = b / a   applied to prices STRICTLY BEFORE ex-date
    on/after the ex-date: unchanged
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from prices.yahoo_loader import adjust_for_splits


def _make_prices(ticker: str, start: date, n_days: int, base: float = 100.0) -> pd.DataFrame:
    dates = [start + timedelta(days=i) for i in range(n_days)]
    return pd.DataFrame(
        {
            "ticker": ticker,
            "date": dates,
            "open": [base] * n_days,
            "close": [base] * n_days,
            "high": [base] * n_days,
            "low": [base] * n_days,
            "volume": list(range(n_days)),
        }
    )


def test_empty_splits_returns_unchanged_copy():
    prices = _make_prices("A", date(2024, 1, 1), 5)
    splits = pd.DataFrame(columns=["ticker", "date", "split_factor"])

    out = adjust_for_splits(prices, splits)

    pd.testing.assert_frame_equal(out, prices)
    # Returned frame must NOT share memory with input — the function should
    # be safe to mutate downstream without back-propagating changes.
    assert out is not prices


def test_single_forward_split_halves_pre_split_prices():
    prices = _make_prices("A", date(2024, 1, 1), 10, base=200.0)
    splits = pd.DataFrame(
        {"ticker": ["A"], "date": [date(2024, 1, 6)], "split_factor": ["2:1"]}
    )

    out = adjust_for_splits(prices, splits)

    # Pre-split (dates < 2024-01-06): factor = 1/2 → 100.0
    pre = out[out["date"] < date(2024, 1, 6)]
    assert (pre["close"] == 100.0).all()
    # On and after ex-date: unchanged → 200.0
    post = out[out["date"] >= date(2024, 1, 6)]
    assert (post["close"] == 200.0).all()


def test_reverse_split_scales_pre_split_prices_up():
    # "1:10" — one new share per ten old → reverse split 10-for-1.
    prices = _make_prices("A", date(2024, 1, 1), 10, base=5.0)
    splits = pd.DataFrame(
        {"ticker": ["A"], "date": [date(2024, 1, 6)], "split_factor": ["1:10"]}
    )

    out = adjust_for_splits(prices, splits)
    pre = out[out["date"] < date(2024, 1, 6)]
    post = out[out["date"] >= date(2024, 1, 6)]
    # factor = 10/1 = 10 → pre-split prices multiplied by 10
    assert (pre["close"] == 50.0).all()
    assert (post["close"] == 5.0).all()


def test_multiple_splits_compound():
    # Two forward splits: 2:1 on day 3, 3:1 on day 6.
    # Before day 3: both apply → factor 0.5 * (1/3) = 0.1666...
    # Day 3 to 5: only the 3:1 still-future applies → factor 1/3
    # Day 6+: none apply → factor 1.0
    prices = _make_prices("A", date(2024, 1, 1), 10, base=60.0)
    splits = pd.DataFrame(
        {
            "ticker": ["A", "A"],
            "date": [date(2024, 1, 4), date(2024, 1, 7)],
            "split_factor": ["2:1", "3:1"],
        }
    )

    out = adjust_for_splits(prices, splits)
    early = out[out["date"] < date(2024, 1, 4)]
    middle = out[(out["date"] >= date(2024, 1, 4)) & (out["date"] < date(2024, 1, 7))]
    late = out[out["date"] >= date(2024, 1, 7)]

    assert early["close"].unique() == pytest.approx([60.0 * 0.5 / 3.0])
    assert middle["close"].unique() == pytest.approx([60.0 / 3.0])
    assert (late["close"] == 60.0).all()


def test_split_does_not_affect_other_tickers():
    a = _make_prices("A", date(2024, 1, 1), 5, base=100.0)
    b = _make_prices("B", date(2024, 1, 1), 5, base=100.0)
    prices = pd.concat([a, b], ignore_index=True)
    splits = pd.DataFrame(
        {"ticker": ["A"], "date": [date(2024, 1, 3)], "split_factor": ["2:1"]}
    )

    out = adjust_for_splits(prices, splits)
    # B untouched.
    assert (out[out["ticker"] == "B"]["close"] == 100.0).all()
    # A's early rows adjusted.
    pre = out[(out["ticker"] == "A") & (out["date"] < date(2024, 1, 3))]
    assert (pre["close"] == 50.0).all()


def test_volume_is_not_adjusted():
    prices = _make_prices("A", date(2024, 1, 1), 5, base=100.0)
    original_volumes = prices["volume"].tolist()
    splits = pd.DataFrame(
        {"ticker": ["A"], "date": [date(2024, 1, 3)], "split_factor": ["2:1"]}
    )

    out = adjust_for_splits(prices, splits)
    assert out["volume"].tolist() == original_volumes


def test_input_frame_is_not_mutated():
    prices = _make_prices("A", date(2024, 1, 1), 5, base=100.0)
    before = prices.copy(deep=True)
    splits = pd.DataFrame(
        {"ticker": ["A"], "date": [date(2024, 1, 3)], "split_factor": ["2:1"]}
    )

    adjust_for_splits(prices, splits)
    pd.testing.assert_frame_equal(prices, before)


def test_all_ohlc_columns_adjusted_together():
    """Open/high/low must be adjusted by the same factor as close, to keep
    the low <= open, close <= high invariant."""
    prices = pd.DataFrame(
        {
            "ticker": ["A"] * 3,
            "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
            "open": [99.0, 99.0, 99.0],
            "close": [101.0, 101.0, 101.0],
            "high": [102.0, 102.0, 102.0],
            "low": [98.0, 98.0, 98.0],
            "volume": [10, 10, 10],
        }
    )
    splits = pd.DataFrame(
        {"ticker": ["A"], "date": [date(2024, 1, 3)], "split_factor": ["2:1"]}
    )

    out = adjust_for_splits(prices, splits)
    pre = out[out["date"] < date(2024, 1, 3)]
    # Each OHLC column should be scaled by 0.5 (factor = 1/2).
    assert (pre["open"] == 49.5).all()
    assert (pre["close"] == 50.5).all()
    assert (pre["high"] == 51.0).all()
    assert (pre["low"] == 49.0).all()
    # Invariant still holds post-adjustment.
    assert (pre["low"] <= pre["open"]).all()
    assert (pre["open"] <= pre["high"]).all()
    assert (pre["low"] <= pre["close"]).all()
    assert (pre["close"] <= pre["high"]).all()
