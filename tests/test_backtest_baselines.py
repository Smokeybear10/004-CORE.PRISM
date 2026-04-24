"""
Tests for backtest.baselines. Each baseline must:
  - produce one Trade per event,
  - encode direction correctly (lean with the move, fade against, etc.),
  - be deterministic given the random seed (for the random ones).
"""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.baselines import (
    BASELINES,
    FADE_THRESHOLD,
    baseline_always_fade,
    baseline_always_lean,
    baseline_random_attribution,
    baseline_sentiment_only,
)
from backtest.fixtures import make_synthetic_events_df


@pytest.fixture
def events_df():
    return make_synthetic_events_df(n=20, seed=42)


def test_registry_has_four_baselines():
    assert set(BASELINES.keys()) == {
        "always_lean", "always_fade", "random_attribution", "sentiment_only",
    }


def test_always_lean_one_trade_per_event(events_df):
    trades = baseline_always_lean(events_df, horizon=5)
    assert len(trades) == len(events_df)
    for t in trades:
        assert t.action == "lean"
        # direction matches sign of reaction_return
        reaction = float(events_df.loc[events_df.event_id == t.event_id,
                                       "reaction_return"].iloc[0])
        expected_dir = 1 if reaction > 0 else (-1 if reaction < 0 else 0)
        assert t.direction == expected_dir
        assert t.source == "baseline_always_lean"


def test_always_fade_below_threshold_is_neutral(events_df):
    trades = baseline_always_fade(events_df, horizon=5, threshold=FADE_THRESHOLD)
    assert len(trades) == len(events_df)
    for t in trades:
        reaction = float(events_df.loc[events_df.event_id == t.event_id,
                                       "reaction_return"].iloc[0])
        if abs(reaction) < FADE_THRESHOLD:
            assert t.direction == 0
            assert t.action == "neutral"
        else:
            # fade: opposite sign of reaction
            assert t.direction == (-1 if reaction > 0 else 1)
            assert t.action == "fade"


def test_always_fade_high_threshold_zeros_everything(events_df):
    # threshold larger than any reaction in the fixture -> all neutral
    trades = baseline_always_fade(events_df, horizon=5, threshold=1.0)
    assert all(t.direction == 0 for t in trades)


def test_random_attribution_deterministic(events_df):
    a = baseline_random_attribution(events_df, horizon=5, seed=7)
    b = baseline_random_attribution(events_df, horizon=5, seed=7)
    assert [t.direction for t in a] == [t.direction for t in b]
    assert [t.action for t in a] == [t.action for t in b]


def test_random_attribution_different_seed_differs(events_df):
    a = baseline_random_attribution(events_df, horizon=5, seed=1)
    b = baseline_random_attribution(events_df, horizon=5, seed=2)
    # Not identical across a 20-event fixture with 4 labels.
    assert [t.direction for t in a] != [t.direction for t in b]


def test_sentiment_only_deterministic(events_df):
    a = baseline_sentiment_only(events_df, horizon=5, seed=11)
    b = baseline_sentiment_only(events_df, horizon=5, seed=11)
    assert [t.direction for t in a] == [t.direction for t in b]


def test_sentiment_only_actions_are_valid(events_df):
    trades = baseline_sentiment_only(events_df, horizon=5, seed=11)
    assert len(trades) == len(events_df)
    for t in trades:
        assert t.action in ("lean", "fade", "neutral")
        assert t.direction in (-1, 0, 1)
        assert 0.0 <= t.confidence <= 1.0


def test_all_baselines_return_non_empty(events_df):
    # Sanity: every registered baseline produces trades on a 20-event panel.
    for name, fn in BASELINES.items():
        trades = fn(events_df, horizon=5)
        assert len(trades) == 20, f"{name} returned {len(trades)} trades"
