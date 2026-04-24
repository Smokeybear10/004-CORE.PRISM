"""
Tests for Stage-2 Layer (b) — baseline floor PASS/FAIL contract.
"""
from __future__ import annotations

import pytest

from backtest.fixtures import make_synthetic_events_df
from eval.calibration import (
    CalibrationReport,
    FLOOR_BASELINES,
    compare_to_baselines,
)


def test_compare_to_baselines_produces_full_matrix():
    events = make_synthetic_events_df(n=60, seed=1)
    report = compare_to_baselines(
        events,
        ablations=["base_news", "+sec"],
        baselines=("random_attribution", "sentiment_only"),
        metric="sharpe",
        margin_required=-1e9,  # force PASS so we can examine shape
    )
    assert isinstance(report, CalibrationReport)
    assert len(report.ablation_results) == 2
    assert len(report.baseline_results) == 2
    # 2 ablations * 2 baselines = 4 floor checks
    assert len(report.floor_checks) == 4
    assert all(fc.metric == "sharpe" for fc in report.floor_checks)


def test_floor_baselines_are_the_two_noise_baselines():
    assert "random_attribution" in FLOOR_BASELINES
    assert "sentiment_only" in FLOOR_BASELINES


def test_unknown_baseline_raises():
    events = make_synthetic_events_df(n=10, seed=1)
    with pytest.raises(ValueError, match="unknown baseline"):
        compare_to_baselines(events, baselines=("not_a_real_baseline",))


def test_assert_beats_floor_raises_on_failure():
    """
    Force a failure by requiring an absurdly large margin. The structured
    strategy cannot beat the baseline by +1e9 Sharpe, so every check fails.
    """
    events = make_synthetic_events_df(n=50, seed=3)
    report = compare_to_baselines(
        events,
        ablations=["base_news"],
        baselines=("random_attribution",),
        metric="sharpe",
        margin_required=1e9,
    )
    assert report.passed is False
    assert len(report.failures) == 1
    with pytest.raises(AssertionError, match="floor violation"):
        report.assert_beats_floor()


def test_assert_beats_floor_quiet_on_pass():
    events = make_synthetic_events_df(n=50, seed=2)
    report = compare_to_baselines(
        events,
        ablations=["base_news"],
        baselines=("random_attribution",),
        metric="sharpe",
        margin_required=-1e9,  # trivial pass
    )
    assert report.passed is True
    # Must NOT raise
    report.assert_beats_floor()


def test_failure_reason_calls_out_known_failure_modes():
    events = make_synthetic_events_df(n=40, seed=4)
    report = compare_to_baselines(
        events,
        ablations=["base_news"],
        baselines=("random_attribution", "sentiment_only"),
        metric="sharpe",
        margin_required=1e9,  # force failure so reasons populate
    )
    reasons = " ".join(fc.reason or "" for fc in report.failures)
    assert "attribution may be noise" in reasons
    assert "polarity may be doing all the work" in reasons
