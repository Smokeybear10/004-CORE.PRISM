"""
Tests for demo.pnl_summary — the PnL block baked into static ticker bundles.

Locks in:
  - serialized shape (5 strategies, required keys)
  - skip-without-forward-window behavior (don't crash on too-recent moves)
  - model strategy actually dispatches to fundamental_vs_nonfundamental
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from demo.pnl_summary import (
    DEFAULT_HORIZON,
    MODEL_STRATEGY,
    build_pnl_summary,
)


def _prices_df(start: date, n: int, step: float = 1.0, base: float = 100.0) -> pd.DataFrame:
    """Synthetic monotonically-increasing close panel, one bar per calendar day.

    For PnL tests the trading-vs-calendar distinction doesn't matter as long
    as `move_date` and the forward-window indices both sit inside the panel.
    """
    rows = []
    for i in range(n):
        rows.append({"date": start + timedelta(days=i), "close": base + i * step})
    return pd.DataFrame(rows)


def _move(d: date, ret: float, character: str = "structural", confidence: float = 0.8) -> dict:
    return {
        "move_date": d.isoformat(),
        "return_pct": ret,
        "attribution": {
            "character": character,
            "confidence": confidence,
            "chunks_considered": 12,
        },
    }


def test_build_pnl_summary_shape():
    start = date(2024, 1, 1)
    prices = _prices_df(start, 60)
    moves = [
        _move(start + timedelta(days=i * 5), 0.05 if i % 2 == 0 else -0.04)
        for i in range(5)
    ]

    out = build_pnl_summary("AMD", moves, prices)
    assert out is not None
    assert out["notional_per_trade"] == 10_000
    assert out["horizon_days"] == DEFAULT_HORIZON
    assert out["uses_market_neutral"] is False
    assert out["n_events"] == 5

    names = [s["name"] for s in out["strategies"]]
    assert names[0] == "model"
    assert set(names) == {
        "model", "always_lean", "always_fade",
        "random_attribution", "sentiment_only",
    }

    required_keys = {
        "name", "label", "n_trades", "n_wins",
        "total_pnl_dollars", "hit_rate", "avg_return_pct",
        "sharpe", "equity_curve",
    }
    for s in out["strategies"]:
        assert required_keys.issubset(s.keys()), f"missing keys in {s['name']}"
        assert isinstance(s["equity_curve"], list)


def test_build_pnl_summary_skips_moves_without_forward_window():
    """A move at the tail of the price panel has no 5-day forward; it should
    drop silently rather than crash the bundle."""
    start = date(2024, 1, 1)
    prices = _prices_df(start, 10)  # only 10 bars
    # Move at bar 8 → can't look forward 5 bars (would land at bar 13)
    moves = [_move(start + timedelta(days=8), 0.05)]

    out = build_pnl_summary("AMD", moves, prices)
    # No usable events → helper returns None so the UI hides the card.
    assert out is None


def test_build_pnl_summary_partial_window_drops_late_move():
    start = date(2024, 1, 1)
    prices = _prices_df(start, 30)
    moves = [
        _move(start + timedelta(days=2), 0.05),   # has fwd window
        _move(start + timedelta(days=28), -0.06), # no fwd window (only 1 bar after)
    ]
    out = build_pnl_summary("AMD", moves, prices)
    assert out is not None
    assert out["n_events"] == 1, "late move without 5-day window must be dropped"


def test_model_strategy_uses_fundamental_vs_nonfundamental():
    """All 'structural'-character moves under our model should trade lean
    (direction follows the move sign), so n_trades == n_events."""
    start = date(2024, 1, 1)
    prices = _prices_df(start, 60)
    moves = [
        _move(start + timedelta(days=i * 5), 0.05, character="structural")
        for i in range(5)
    ]
    out = build_pnl_summary("AMD", moves, prices)
    assert out is not None

    model = next(s for s in out["strategies"] if s["name"] == "model")
    assert model["n_trades"] == 5, (
        "structural attributions should produce a trade per event under the "
        f"{MODEL_STRATEGY} strategy"
    )

    # All moves were positive returns + structural → lean → direction = +1.
    # Synthetic prices climb monotonically, so every 5-day forward return is
    # positive → model should be 100% hit rate and net dollar P&L > 0.
    assert model["hit_rate"] == pytest.approx(1.0)
    assert model["total_pnl_dollars"] > 0


def test_neutral_character_skips_trades():
    """'unclear' character → neutral → no trade. n_trades for the model row
    should drop to zero even though n_events is positive."""
    start = date(2024, 1, 1)
    prices = _prices_df(start, 60)
    moves = [
        _move(start + timedelta(days=i * 5), 0.05, character="unclear")
        for i in range(5)
    ]
    out = build_pnl_summary("AMD", moves, prices)
    assert out is not None
    model = next(s for s in out["strategies"] if s["name"] == "model")
    assert model["n_trades"] == 0
    assert model["total_pnl_dollars"] == 0.0
