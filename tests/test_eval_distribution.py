"""
Tests for Stage-2 Layer (d) — distributional sanity breakdowns.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from eval.distribution import (
    breakdown_by_direction,
    breakdown_by_quarter,
    breakdown_by_ticker,
    character_distribution,
    check_distribution,
)
from schema import (
    Attribution,
    DimensionScore,
    SourceType,
)


def _dim(weight: float, direction: str = "neutral") -> DimensionScore:
    return DimensionScore(
        weight=weight,
        direction=direction,
        rationale="test",
        evidence_chunk_ids=["news_TICK_2024-01-01_h_001"],
    )


def _attr(
    ticker: str,
    move_date: date,
    *,
    dominant: str = "demand",
    move_character: str = "structural",
    return_pct: float = -0.05,
    predicted_return_pct: float | None = -0.04,
) -> Attribution:
    weights = {"demand": 0.1, "pricing": 0.1, "competitive": 0.1,
               "management_credibility": 0.1, "macro": 0.1}
    weights[dominant] = 0.6
    return Attribution(
        ticker=ticker,
        move_date=move_date,
        return_pct=return_pct,
        predicted_return_pct=predicted_return_pct,
        demand=_dim(weights["demand"], "negative" if dominant == "demand" else "neutral"),
        pricing=_dim(weights["pricing"]),
        competitive=_dim(weights["competitive"]),
        management_credibility=_dim(weights["management_credibility"]),
        macro=_dim(weights["macro"]),
        move_character=move_character,
        confidence=0.75,
        ablation_name="test",
        sources_used=[SourceType.NEWS],
        chunks_considered=5,
    )


# ---------- Breakdowns ----------


def test_breakdown_by_ticker_groups_correctly():
    attrs = [
        _attr("AMD", date(2024, 1, 1)),
        _attr("AMD", date(2024, 4, 1)),
        _attr("NVDA", date(2024, 5, 1)),
    ]
    buckets = breakdown_by_ticker(attrs)
    keyed = {b.bucket_key: b for b in buckets}
    assert keyed["AMD"].n == 2
    assert keyed["NVDA"].n == 1


def test_breakdown_by_quarter_bins_on_calendar_quarters():
    attrs = [
        _attr("AMD", date(2024, 1, 15)),   # 2024Q1
        _attr("AMD", date(2024, 3, 31)),   # 2024Q1
        _attr("AMD", date(2024, 4, 1)),    # 2024Q2
        _attr("NVDA", date(2024, 10, 12)), # 2024Q4
    ]
    buckets = breakdown_by_quarter(attrs)
    keyed = {b.bucket_key: b.n for b in buckets}
    assert keyed["2024Q1"] == 2
    assert keyed["2024Q2"] == 1
    assert keyed["2024Q4"] == 1


def test_breakdown_by_direction_splits_up_down_flat():
    attrs = [
        _attr("AMD", date(2024, 1, 1), return_pct=0.10),
        _attr("AMD", date(2024, 2, 1), return_pct=-0.10),
        _attr("AMD", date(2024, 3, 1), return_pct=0.0),
    ]
    keyed = {b.bucket_key: b.n for b in breakdown_by_direction(attrs)}
    assert keyed["up"] == 1
    assert keyed["down"] == 1
    assert keyed["flat"] == 1


# ---------- Character distribution ----------


def test_character_distribution_reports_collapse_correctly():
    attrs = [_attr("AMD", date(2024, 1, i + 1), move_character="structural") for i in range(10)]
    dist = character_distribution(attrs)
    assert dist.n == 10
    assert dist.structural_pct == 1.0
    assert dist.transient_pct == 0.0


# ---------- Sanity flags ----------


def test_collapse_flag_trips_when_single_character_dominates():
    attrs = [_attr("AMD", date(2024, 1, i + 1), move_character="structural") for i in range(20)]
    report = check_distribution(attrs, collapse_rate_max=0.95)
    # 100% structural > 95% threshold
    assert report.passed is False
    kinds = {f.kind for f in report.failures}
    assert "collapse" in kinds
    with pytest.raises(AssertionError, match="distributional"):
        report.assert_healthy()


def test_healthy_distribution_has_no_collapse_or_magnitude_flags():
    attrs = []
    for i in range(8):
        attrs.append(_attr("AMD", date(2024, 1, i + 1), move_character="structural",
                           dominant="demand"))
        attrs.append(_attr("AMD", date(2024, 2, i + 1), move_character="transient",
                           dominant="macro"))
        attrs.append(_attr("NVDA", date(2024, 3, i + 1), move_character="mixed",
                           dominant="competitive"))
    report = check_distribution(
        attrs,
        collapse_rate_max=0.95,
        min_coverage_per_bucket=1,
    )
    # No collapse, no coverage warnings. Magnitude check inactive (no Sharpe passed).
    assert all(f.kind not in ("collapse", "magnitude") for f in report.failures)


def test_magnitude_flag_trips_on_positive_hit_rate_with_tiny_sharpe():
    attrs = [_attr("AMD", date(2024, 1, i + 1)) for i in range(5)]
    report = check_distribution(
        attrs,
        min_sharpe_if_positive_hit_rate=0.5,
        observed_sharpe=0.05,
        observed_hit_rate=0.6,
    )
    kinds = [f.kind for f in report.failures]
    assert "magnitude" in kinds


def test_magnitude_flag_quiet_when_sharpe_is_adequate():
    attrs = [_attr("AMD", date(2024, 1, i + 1)) for i in range(5)]
    report = check_distribution(
        attrs,
        min_sharpe_if_positive_hit_rate=0.5,
        observed_sharpe=1.2,
        observed_hit_rate=0.6,
    )
    assert not any(f.kind == "magnitude" for f in report.failures)


def test_magnitude_flag_derived_from_pnl_df_when_no_explicit_sharpe():
    attrs = [_attr("AMD", date(2024, 1, i + 1)) for i in range(5)]
    # Many small wins and a few small losses → positive hit rate, tiny Sharpe.
    pnl_df = pd.DataFrame([
        {"direction": 1, "pnl": 0.001},
        {"direction": 1, "pnl": 0.001},
        {"direction": 1, "pnl": 0.001},
        {"direction": 1, "pnl": -0.0005},
        {"direction": 1, "pnl": 0.0015},
    ])
    report = check_distribution(
        attrs,
        pnl_df=pnl_df,
        min_sharpe_if_positive_hit_rate=10.0,  # deliberately high to force flag
    )
    assert any(f.kind == "magnitude" for f in report.failures)


def test_coverage_flag_trips_on_thin_buckets():
    # One attribution per ticker → coverage < 3 on every ticker bucket
    attrs = [_attr(t, date(2024, 1, 1)) for t in ("AMD", "NVDA", "AAPL")]
    report = check_distribution(attrs, min_coverage_per_bucket=3)
    coverage_flags = [f for f in report.failures if f.kind == "coverage"]
    assert len(coverage_flags) >= 3
