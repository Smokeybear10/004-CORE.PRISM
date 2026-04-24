"""
Tests for backtest.pnl: compute_pnl math and summarize -> BacktestResult.
"""
from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from schema import BacktestResult
from backtest.signal import Trade
from backtest.pnl import HORIZON_TO_COL, compute_pnl, summarize


def _trade(event_id: str, direction: int, size: float = 1.0,
           confidence: float = 0.8) -> Trade:
    return Trade(
        event_id=event_id, ticker=event_id.split("_")[0],
        action="lean" if direction > 0 else ("fade" if direction < 0 else "neutral"),
        direction=direction, size=size,
        entry_date=date(2024, 1, 5), exit_horizon_days=5,
        confidence=confidence, source="test",
    )


def _events_df(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    """rows: [(event_id, fwd_5d, fwd_5d_excess)]"""
    return pd.DataFrame([
        {"event_id": eid, "fwd_5d": fwd, "fwd_5d_excess": fwd_ex,
         "fwd_1d": fwd * 0.2, "fwd_1d_excess": fwd_ex * 0.2,
         "fwd_20d": fwd * 2, "fwd_20d_excess": fwd_ex * 2}
        for eid, fwd, fwd_ex in rows
    ])


# ---------- compute_pnl math ----------

def test_pnl_is_direction_times_size_times_fwd():
    events = _events_df([
        ("AAPL_1", 0.03, 0.02),
        ("AAPL_2", -0.05, -0.04),
        ("AAPL_3", 0.01, 0.015),
    ])
    trades = [
        _trade("AAPL_1", direction=+1, size=1.0),   # pnl = 1 * 1.0 * 0.02 = +0.02
        _trade("AAPL_2", direction=-1, size=2.0),   # pnl = -1 * 2.0 * -0.04 = +0.08
        _trade("AAPL_3", direction=0,  size=1.0),   # direction=0 still appears
    ]
    pnl_df = compute_pnl(trades, events, horizon=5, use_excess=True)
    assert len(pnl_df) == 3
    pnl_by_id = dict(zip(pnl_df["event_id"], pnl_df["pnl"]))
    assert pnl_by_id["AAPL_1"] == pytest.approx(0.02)
    assert pnl_by_id["AAPL_2"] == pytest.approx(0.08)
    assert pnl_by_id["AAPL_3"] == pytest.approx(0.0)


def test_pnl_raw_vs_excess_uses_different_column():
    events = _events_df([("AAPL_1", 0.03, 0.02)])
    trades = [_trade("AAPL_1", direction=+1)]

    pnl_raw = compute_pnl(trades, events, horizon=5, use_excess=False)
    pnl_excess = compute_pnl(trades, events, horizon=5, use_excess=True)

    assert pnl_raw["pnl"].iloc[0] == pytest.approx(0.03)
    assert pnl_excess["pnl"].iloc[0] == pytest.approx(0.02)


def test_pnl_drops_missing_event_id():
    events = _events_df([("AAPL_1", 0.03, 0.02)])
    trades = [_trade("AAPL_1", +1), _trade("UNKNOWN_2", +1)]
    pnl_df = compute_pnl(trades, events, horizon=5)
    assert list(pnl_df["event_id"]) == ["AAPL_1"]


def test_pnl_drops_nan_forward_return():
    events = _events_df([("AAPL_1", float("nan"), float("nan"))])
    trades = [_trade("AAPL_1", +1)]
    pnl_df = compute_pnl(trades, events, horizon=5)
    assert pnl_df.empty


def test_pnl_rejects_unknown_horizon():
    events = _events_df([("AAPL_1", 0.03, 0.02)])
    trades = [_trade("AAPL_1", +1)]
    with pytest.raises(ValueError):
        compute_pnl(trades, events, horizon=99)


def test_horizon_mapping_supports_1_5_20():
    assert set(HORIZON_TO_COL.keys()) == {1, 5, 20}


# ---------- summarize ----------

def test_summarize_on_mixed_pnl_produces_valid_result():
    events = _events_df([
        ("AAPL_1", 0.03, 0.02),
        ("AAPL_2", -0.05, -0.04),
        ("AAPL_3", 0.02, 0.01),
    ])
    trades = [
        _trade("AAPL_1", +1),
        _trade("AAPL_2", -1),
        _trade("AAPL_3", +1),
    ]
    pnl_df = compute_pnl(trades, events, horizon=5, use_excess=True)
    result = summarize(pnl_df, strategy_name="test_strat",
                       ablation_name="base_news", horizon_days=5)

    assert isinstance(result, BacktestResult)
    assert result.n_trades == 3
    assert result.strategy_name == "test_strat"
    assert result.ablation_name == "base_news"
    assert result.hit_rate == pytest.approx(1.0)  # all 3 trades positive pnl
    assert math.isfinite(result.sharpe)
    assert math.isfinite(result.max_drawdown)


def test_summarize_all_neutral_returns_zero_trades():
    events = _events_df([("AAPL_1", 0.03, 0.02), ("AAPL_2", -0.05, -0.04)])
    trades = [_trade("AAPL_1", 0), _trade("AAPL_2", 0)]
    pnl_df = compute_pnl(trades, events, horizon=5)
    result = summarize(pnl_df, strategy_name="all_neutral",
                       ablation_name=None, horizon_days=5)
    assert result.n_trades == 0
    assert result.sharpe == 0.0
    assert result.hit_rate == 0.0
    assert "zero active trades" in (result.notes or "")


def test_summarize_single_trade_has_zero_or_finite_sharpe():
    # std(ddof=1) is nan with 1 sample; summarize should handle it gracefully.
    events = _events_df([("AAPL_1", 0.03, 0.02)])
    trades = [_trade("AAPL_1", +1)]
    pnl_df = compute_pnl(trades, events, horizon=5)
    result = summarize(pnl_df, strategy_name="one_trade",
                       ablation_name="base_news", horizon_days=5)
    assert result.n_trades == 1
    assert result.sharpe == 0.0  # undefined vol falls through to 0
