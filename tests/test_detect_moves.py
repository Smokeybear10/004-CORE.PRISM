"""
Tests for `detect_moves` — the no-foreknowledge invariants are the load-
bearing ones here (vol + rank windows end at move_date - 1), plus the
multi-ticker / multi-trigger edge cases.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from prices.price_moves import detect_moves
from schema import PriceMove


def _quiet_then_spike(
    n_quiet: int, spike_ret: float, seed: int = 0
) -> pd.DataFrame:
    """Return prices for one ticker 'SPK': a bootstrap close, then `n_quiet`
    days of ~0.1% noisy returns, then a single spike day. Total rows =
    `n_quiet + 2`, which gives the detector `n_quiet` non-NaN prior returns
    at the spike row — enough for a `lookback_vol=n_quiet` window."""
    rng = np.random.default_rng(seed=seed)
    n_rows = n_quiet + 2
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(n_rows)]
    closes = [100.0]
    for _ in range(n_quiet):
        r = 0.001 + float(rng.normal(0, 0.0005))
        closes.append(closes[-1] * (1.0 + r))
    closes.append(closes[-1] * (1.0 + spike_ret))
    assert len(closes) == n_rows
    return pd.DataFrame({"ticker": "SPK", "date": dates, "close": closes})


# ---------- no-foreknowledge invariants ----------


def test_vol_window_excludes_move_date_itself():
    """A huge spike must still be flagged — vol window used for its z-score
    must see only the quiet period BEFORE the spike."""
    prices = _quiet_then_spike(n_quiet=30, spike_ret=0.15)
    moves = detect_moves(prices, lookback_vol=30, lookback_rank=60)

    spike_date = prices["date"].iloc[-1]
    spike_moves = [m for m in moves if m.move_date == spike_date]
    assert len(spike_moves) == 1
    # Proof the window was quiet-only: z-score should be huge.
    assert spike_moves[0].vol_zscore > 20.0


def test_implied_vol_matches_prior_only_stdev():
    """Derive the vol the detector used from (ret / z-score) and confirm it
    matches the std of prior returns — not one that absorbs the spike."""
    prices = _quiet_then_spike(n_quiet=30, spike_ret=0.15)
    moves = detect_moves(prices, lookback_vol=30, lookback_rank=60)
    m = next(mv for mv in moves if mv.move_date == prices["date"].iloc[-1])

    rets = prices["close"].pct_change().to_numpy()
    prior_vol = float(np.nanstd(rets[1:-1], ddof=1))       # excludes spike
    inclusive_vol = float(np.nanstd(rets[1:], ddof=1))     # includes spike
    implied_vol = abs(m.return_pct) / abs(m.vol_zscore)

    assert implied_vol == pytest.approx(prior_vol, rel=1e-6)
    # Guard: the two vols actually differ, so this test is meaningful.
    assert not np.isclose(prior_vol, inclusive_vol, rtol=1e-3)


def test_rank_window_excludes_move_date():
    """
    Build a series where all prior |returns| are ~1% and today's |return|
    is 5%. Today must be top 5% (>=95%) of prior, not 50% (which is what
    it would be if today were included in the window).
    """
    prices = _quiet_then_spike(n_quiet=70, spike_ret=0.05)
    moves = detect_moves(prices, lookback_vol=30, lookback_rank=60)
    m = next(mv for mv in moves if mv.move_date == prices["date"].iloc[-1])
    assert m.magnitude_rank is not None
    assert m.magnitude_rank >= 0.95


def test_constant_price_series_yields_no_moves():
    """Zero returns → vol = 0 (vol_trigger guard fires) and |ret| = 0 never
    reaches the top-5% rank threshold."""
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(100)]
    prices = pd.DataFrame({"ticker": "C", "date": dates, "close": [100.0] * 100})
    assert detect_moves(prices, lookback_vol=30, lookback_rank=60) == []


def test_noisy_quiet_series_rarely_fires():
    """A normal quiet series WILL have occasional 2-sigma days — that's what
    the detector is supposed to catch. Just confirm the hit rate is low
    (~<15%), not zero, and that the detector doesn't flag every row."""
    rng = np.random.default_rng(seed=1)
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(200)]
    closes = [100.0]
    for _ in range(199):
        closes.append(closes[-1] * (1.0 + float(rng.normal(0, 0.001))))
    prices = pd.DataFrame({"ticker": "Q", "date": dates, "close": closes})

    moves = detect_moves(prices, lookback_vol=30, lookback_rank=60)
    # At most 15% of eligible rows should fire; the rest should be quiet.
    eligible_rows = len(prices) - 60  # rows with both vol + rank windows defined
    assert len(moves) <= 0.15 * eligible_rows


# ---------- output shape ----------


def test_returns_pydantic_price_move_instances():
    prices = _quiet_then_spike(n_quiet=30, spike_ret=0.15)
    moves = detect_moves(prices, lookback_vol=30, lookback_rank=60)
    assert moves
    for m in moves:
        assert isinstance(m, PriceMove)
        assert isinstance(m.move_date, date)
        assert isinstance(m.return_pct, float)
        assert isinstance(m.vol_zscore, float)


def test_negative_returns_produce_negative_zscores():
    """Sign of the z-score must match the sign of the return."""
    prices = _quiet_then_spike(n_quiet=30, spike_ret=-0.15)
    moves = detect_moves(prices, lookback_vol=30, lookback_rank=60)
    m = next(mv for mv in moves if mv.move_date == prices["date"].iloc[-1])
    assert m.return_pct < 0
    assert m.vol_zscore < 0


def test_moves_sorted_within_ticker():
    prices = _quiet_then_spike(n_quiet=40, spike_ret=0.15)
    moves = detect_moves(prices, lookback_vol=30, lookback_rank=60)
    dates = [m.move_date for m in moves if m.ticker == "SPK"]
    assert dates == sorted(dates)


# ---------- multi-ticker ----------


def test_tickers_are_evaluated_independently():
    """
    Ticker A's vol/rank windows must be computed from A's returns alone —
    B's returns (even when B also spikes) must not leak in. Verify by
    comparing A's z-score when computed jointly vs in isolation: they
    should be identical.
    """
    a = _quiet_then_spike(n_quiet=30, spike_ret=0.15, seed=0)
    a["ticker"] = "A"
    b = _quiet_then_spike(n_quiet=30, spike_ret=0.20, seed=5)
    b["ticker"] = "B"
    a_spike_date = a["date"].iloc[-1]

    joint = detect_moves(pd.concat([a, b], ignore_index=True), lookback_vol=30, lookback_rank=60)
    alone = detect_moves(a, lookback_vol=30, lookback_rank=60)

    z_joint = next(
        m.vol_zscore for m in joint if m.ticker == "A" and m.move_date == a_spike_date
    )
    z_alone = next(
        m.vol_zscore for m in alone if m.ticker == "A" and m.move_date == a_spike_date
    )
    assert z_joint == pytest.approx(z_alone)


def test_multi_ticker_unsorted_input_still_works():
    """Input arriving in interleaved ticker order must not break the grouping."""
    a = _quiet_then_spike(n_quiet=30, spike_ret=0.15, seed=0)
    a["ticker"] = "A"
    b = _quiet_then_spike(n_quiet=30, spike_ret=0.001, seed=1)
    b["ticker"] = "B"
    # Interleave rows by shuffling.
    interleaved = pd.concat([a, b], ignore_index=True).sample(
        frac=1, random_state=42
    ).reset_index(drop=True)

    moves = detect_moves(interleaved, lookback_vol=30, lookback_rank=60)
    a_spike_moves = [m for m in moves if m.ticker == "A" and m.move_date == a["date"].iloc[-1]]
    assert len(a_spike_moves) == 1


# ---------- input validation ----------


@pytest.mark.parametrize("missing_col", ["ticker", "date", "close"])
def test_missing_required_columns_raises(missing_col):
    df = pd.DataFrame(
        {
            "ticker": ["A"],
            "date": [date(2024, 1, 1)],
            "close": [100.0],
        }
    ).drop(columns=[missing_col])
    with pytest.raises(ValueError, match="missing required columns"):
        detect_moves(df)


def test_empty_frame_returns_empty_list():
    df = pd.DataFrame({"ticker": [], "date": [], "close": []})
    assert detect_moves(df) == []


def test_short_history_produces_no_moves():
    """Fewer rows than lookback_vol means vol is undefined — nothing should fire."""
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(10)]
    closes = [100.0 * (1 + 0.1) ** i for i in range(10)]  # 10%/day growth, wildly noisy
    prices = pd.DataFrame({"ticker": "S", "date": dates, "close": closes})
    moves = detect_moves(prices, lookback_vol=30, lookback_rank=60)
    assert moves == []
