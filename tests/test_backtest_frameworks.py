"""
Tests for the alternative fade-or-follow frameworks added in
`backtest.frameworks`.

Covers each strategy's pass/fail logic on hand-built Attributions and
verifies the runner-facing wiring (STRATEGY_REGISTRY discovery,
attribution_to_trade dispatch, runner --strategy flag).
"""
from __future__ import annotations

from datetime import date

import pytest

from backtest.frameworks import (
    DEFAULT_DIMENSION_PERSISTENCE,
    FRAMEWORK_STRATEGIES,
    dominant_dimension,
    strategy_dimension_weighted,
    strategy_expected_vs_realized,
    strategy_hybrid,
)
from backtest.signal import STRATEGY_REGISTRY, attribution_to_trade
from schema import Attribution, DimensionScore, SourceType


# ---------- Helpers ----------

def _dim(weight: float, direction: str = "neutral") -> DimensionScore:
    return DimensionScore(
        weight=weight,
        direction=direction,
        rationale="t",
        evidence_chunk_ids=["news_AMD_2024-01-01_h_001"],
    )


def _attr(
    *,
    weights: dict[str, float] | None = None,
    directions: dict[str, str] | None = None,
    move_character: str = "structural",
    return_pct: float = -0.05,
    predicted_return_pct: float | None = -0.04,
) -> Attribution:
    base = {"demand": 0.1, "pricing": 0.1, "competitive": 0.1,
            "management_credibility": 0.1, "macro": 0.1}
    w = {**base, **(weights or {})}
    d = {k: "neutral" for k in base}
    if directions:
        d.update(directions)
    return Attribution(
        ticker="AMD",
        move_date=date(2024, 1, 1),
        return_pct=return_pct,
        predicted_return_pct=predicted_return_pct,
        demand=_dim(w["demand"], d["demand"]),
        pricing=_dim(w["pricing"], d["pricing"]),
        competitive=_dim(w["competitive"], d["competitive"]),
        management_credibility=_dim(w["management_credibility"], d["management_credibility"]),
        macro=_dim(w["macro"], d["macro"]),
        move_character=move_character,
        confidence=0.8,
        ablation_name="test",
        sources_used=[SourceType.NEWS],
        chunks_considered=3,
    )


# ---------- Option 2: expected vs realized ----------

def test_expected_vs_realized_overshoot_returns_fade():
    # realized -10% vs predicted -4% -> ratio 2.5 >= 1.5
    assert strategy_expected_vs_realized(
        _attr(return_pct=-0.10, predicted_return_pct=-0.04)
    ) == "fade"


def test_expected_vs_realized_undershoot_returns_lean():
    # realized -2% vs predicted -10% -> ratio 0.2 <= 0.5
    assert strategy_expected_vs_realized(
        _attr(return_pct=-0.02, predicted_return_pct=-0.10)
    ) == "lean"


def test_expected_vs_realized_aligned_returns_neutral():
    # realized -5% vs predicted -5% -> ratio 1.0 in (0.5, 1.5)
    assert strategy_expected_vs_realized(
        _attr(return_pct=-0.05, predicted_return_pct=-0.05)
    ) == "neutral"


def test_expected_vs_realized_none_predicted_returns_neutral():
    assert strategy_expected_vs_realized(
        _attr(return_pct=-0.05, predicted_return_pct=None)
    ) == "neutral"


def test_expected_vs_realized_zero_predicted_returns_neutral():
    assert strategy_expected_vs_realized(
        _attr(return_pct=-0.05, predicted_return_pct=0.0)
    ) == "neutral"


def test_expected_vs_realized_opposite_signs_return_neutral():
    # The market went the opposite way from the news; framework punts.
    assert strategy_expected_vs_realized(
        _attr(return_pct=-0.10, predicted_return_pct=+0.05)
    ) == "neutral"


def test_expected_vs_realized_custom_overshoot_threshold():
    # ratio 1.4 — between defaults but above a 1.2 threshold
    assert strategy_expected_vs_realized(
        _attr(return_pct=-0.07, predicted_return_pct=-0.05),
        overshoot_factor=1.2,
    ) == "fade"


# ---------- Option 3: dimension-weighted ----------

def test_dimension_weighted_demand_dominant_leans():
    attr = _attr(weights={"demand": 0.7, "macro": 0.05})
    # 0.7 * 0.8 + 0.05 * -0.7 + small others = ~0.55, well above lean_threshold
    assert strategy_dimension_weighted(attr) == "lean"


def test_dimension_weighted_macro_dominant_fades():
    attr = _attr(weights={"macro": 0.7, "demand": 0.05})
    # 0.7 * -0.7 + small positives = ~-0.42, well below fade_threshold
    assert strategy_dimension_weighted(attr) == "fade"


def test_dimension_weighted_balanced_returns_neutral():
    attr = _attr()  # all weights = 0.1 -> score = 0.1 * (0.8+0.6+0.4+0+(-0.7)) = ~0.11
    assert strategy_dimension_weighted(attr) == "neutral"


def test_dimension_weighted_respects_custom_persistence():
    # Override: make macro the persistent dim, demand the reverting one.
    custom = {"demand": -0.8, "macro": +0.8, "pricing": 0.0,
              "competitive": 0.0, "management_credibility": 0.0}
    attr_macro = _attr(weights={"macro": 0.7, "demand": 0.05})
    assert strategy_dimension_weighted(attr_macro, persistence=custom) == "lean"
    attr_demand = _attr(weights={"demand": 0.7, "macro": 0.05})
    assert strategy_dimension_weighted(attr_demand, persistence=custom) == "fade"


def test_dominant_dimension_finds_max_weight():
    attr = _attr(weights={"demand": 0.55, "macro": 0.10})
    assert dominant_dimension(attr) == "demand"


# ---------- Option 6: hybrid ----------

def test_hybrid_transient_fades():
    attr = _attr(move_character="transient")
    assert strategy_hybrid(attr) == "fade"


def test_hybrid_mixed_neutral():
    assert strategy_hybrid(_attr(move_character="mixed")) == "neutral"


def test_hybrid_unclear_neutral():
    assert strategy_hybrid(_attr(move_character="unclear")) == "neutral"


def test_hybrid_structural_aligned_leans():
    attr = _attr(
        weights={"demand": 0.7, "macro": 0.05},
        move_character="structural",
        return_pct=-0.05, predicted_return_pct=-0.05,  # aligned
    )
    assert strategy_hybrid(attr) == "lean"


def test_hybrid_structural_overshoot_fades():
    attr = _attr(
        weights={"demand": 0.7, "macro": 0.05},
        move_character="structural",
        return_pct=-0.10, predicted_return_pct=-0.04,  # ratio 2.5 -> fade overshoot
    )
    assert strategy_hybrid(attr) == "fade"


def test_hybrid_structural_undershoot_leans():
    attr = _attr(
        weights={"demand": 0.7, "macro": 0.05},
        move_character="structural",
        return_pct=-0.02, predicted_return_pct=-0.10,  # ratio 0.2 -> still room, lean
    )
    assert strategy_hybrid(attr) == "lean"


def test_hybrid_macro_dominant_downgrades_lean_to_neutral():
    # Structural + aligned would normally lean, but macro is dominant and
    # has negative persistence -> Layer 4 downgrades to neutral.
    attr = _attr(
        weights={"macro": 0.7, "demand": 0.05},
        move_character="structural",
        return_pct=-0.05, predicted_return_pct=-0.05,
    )
    assert strategy_hybrid(attr) == "neutral"


def test_hybrid_no_predicted_skips_layer_3():
    # No predicted -> Layer 3 is skipped, falls through to Layer 4 then lean.
    attr = _attr(
        weights={"demand": 0.7, "macro": 0.05},
        move_character="structural",
        predicted_return_pct=None,
    )
    assert strategy_hybrid(attr) == "lean"


# ---------- Registry wiring ----------

def test_all_frameworks_registered():
    for name in FRAMEWORK_STRATEGIES:
        assert name in STRATEGY_REGISTRY, f"{name} missing from STRATEGY_REGISTRY"
    # The original strategy is still there
    assert "fundamental_vs_nonfundamental" in STRATEGY_REGISTRY


def test_attribution_to_trade_dispatches_to_each_framework():
    attr = _attr(
        weights={"demand": 0.7, "macro": 0.05},
        move_character="structural",
        return_pct=-0.05, predicted_return_pct=-0.05,
    )
    for strat in STRATEGY_REGISTRY:
        trade = attribution_to_trade(
            attr, event_id="ev1", reaction_return=-0.05,
            exit_horizon_days=5, strategy=strat,
        )
        assert trade.source == strat
        assert trade.action in ("lean", "fade", "neutral")


def test_unknown_strategy_raises():
    attr = _attr()
    with pytest.raises(KeyError):
        attribution_to_trade(
            attr, event_id="ev1", reaction_return=-0.05,
            strategy="not_a_real_framework",
        )


# ---------- Defaults are consistent with the brief ----------

def test_default_persistence_macro_negative_demand_positive():
    """Mentor brief: macro tends to revert, demand tends to persist."""
    assert DEFAULT_DIMENSION_PERSISTENCE["macro"] < 0
    assert DEFAULT_DIMENSION_PERSISTENCE["demand"] > 0
