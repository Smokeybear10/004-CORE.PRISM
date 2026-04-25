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


# ---------- Research-grounded priors ----------


def test_research_priors_match_literature_directions():
    """Each dimension's prior must agree with the bibliography in
    backtest.frameworks.RESEARCH_REFERENCES.

      - PEAD literature (Bernard & Thomas 1989, Foster et al. 1984,
        Engelberg/McLean/Pontiff 2018): demand and pricing PERSIST.
      - Cohen & Frazzini 2008 economic links: competitive PERSISTS (modestly).
      - Bordalo et al. 2019 diagnostic expectations: management_credibility
        REVERTS.
      - De Bondt & Thaler 1985, Tetlock 2007, Daniel et al. 1998: macro REVERTS.
    """
    from backtest.frameworks import RESEARCH_GROUNDED_PERSISTENCE
    p = RESEARCH_GROUNDED_PERSISTENCE
    # Persists (lean is right):
    assert p["demand"] > 0.5, "PEAD literature demands strong demand-side persistence"
    assert p["pricing"] > 0.4, "PEAD literature implies pricing persists"
    assert p["competitive"] > 0.0, "Cohen & Frazzini: real competitive shifts persist"
    # Reverts (fade is right):
    assert p["management_credibility"] < 0.0, (
        "Bordalo et al. 2019: management surprises overreact and revert"
    )
    assert p["macro"] < -0.5, (
        "DeBondt & Thaler 1985 + Tetlock 2007: macro/sentiment reverts strongly"
    )


def test_research_priors_ordering_matches_evidence_strength():
    """Strongest persistence belongs to demand (most direct PEAD evidence).
    Strongest reversion belongs to macro (multiple converging studies)."""
    from backtest.frameworks import RESEARCH_GROUNDED_PERSISTENCE
    p = RESEARCH_GROUNDED_PERSISTENCE
    persisters = [p["demand"], p["pricing"], p["competitive"]]
    assert max(persisters) == p["demand"], (
        "demand must have the strongest persistence — PEAD is the most-cited "
        "anomaly in the literature"
    )
    assert p["macro"] < p["management_credibility"] < 0, (
        "macro reverts more strongly than management_credibility per the lit"
    )


def test_research_references_present_for_each_prior():
    """Every dimension whose prior is non-zero must be backed by at least one
    citation tagged with that dimension in RESEARCH_REFERENCES."""
    from backtest.frameworks import (
        RESEARCH_GROUNDED_PERSISTENCE,
        RESEARCH_REFERENCES,
    )
    blob = " ".join(RESEARCH_REFERENCES.values()).lower()
    for dim, val in RESEARCH_GROUNDED_PERSISTENCE.items():
        if val == 0.0:
            continue
        assert dim.lower().split("_")[0] in blob, (
            f"no citation in RESEARCH_REFERENCES mentions {dim}"
        )


# ---------- Empirical calibration ----------


def _events_frame_for(attributions: list[Attribution], fwd_returns_5d: list[float]):
    """Build a 1:1-aligned events_df for calibrate_persistence."""
    import pandas as pd
    rows = []
    for i, (a, fwd) in enumerate(zip(attributions, fwd_returns_5d)):
        rows.append({
            "event_id": f"ev_{i}",
            "ticker": a.ticker,
            "reaction_return": float(a.return_pct),
            "fwd_5d": float(fwd),
            "fwd_5d_excess": float(fwd),
        })
    return pd.DataFrame(rows)


def test_calibrate_persistence_recovers_demand_persists_macro_reverts():
    """Construct a synthetic dataset where demand-driven moves continue and
    macro-driven moves reverse; the calibrator must produce
    persistence[demand] > 0 and persistence[macro] < 0."""
    from backtest.frameworks import calibrate_persistence

    n = 80
    attrs: list[Attribution] = []
    fwd: list[float] = []
    for i in range(n):
        is_demand = (i % 2 == 0)
        # Demand-driven: dim.weight=0.7 on demand, sign matches a -3% move,
        # forward 5d return continues at -1% (move persists -> lean right).
        # Macro-driven: dim.weight=0.7 on macro, sign matches a +3% move,
        # forward 5d return reverses at -2% (overreaction -> fade right).
        if is_demand:
            attrs.append(_attr(
                weights={"demand": 0.7, "macro": 0.05},
                directions={"demand": "negative"},
                move_character="structural",
                return_pct=-0.03, predicted_return_pct=-0.025,
            ))
            fwd.append(-0.012)  # continues -> sign(reaction)=-1, target = +0.012
        else:
            attrs.append(_attr(
                weights={"macro": 0.7, "demand": 0.05},
                directions={"macro": "positive"},
                move_character="transient",
                return_pct=+0.03, predicted_return_pct=+0.005,
            ))
            fwd.append(-0.020)  # reverses -> sign(reaction)=+1, target = -0.020

    events = _events_frame_for(attrs, fwd)
    coeffs = calibrate_persistence(events, attrs, horizon=5, use_excess=True)
    assert coeffs["demand"] > 0, f"demand should persist: {coeffs}"
    assert coeffs["macro"] < 0, f"macro should revert: {coeffs}"
    # Outputs must respect the [-1, +1] clip
    for v in coeffs.values():
        assert -1.0 <= v <= 1.0


def test_calibrate_persistence_returns_prior_when_underdetermined():
    """With fewer rows than features+1, regression is unstable and we should
    fall back to the prior."""
    from backtest.frameworks import (
        DEFAULT_DIMENSION_PERSISTENCE,
        calibrate_persistence,
    )
    attrs = [_attr() for _ in range(2)]  # underdetermined: 5 features, 2 rows
    events = _events_frame_for(attrs, [0.01, -0.01])
    coeffs = calibrate_persistence(events, attrs)
    assert coeffs == DEFAULT_DIMENSION_PERSISTENCE


def test_calibrate_persistence_misaligned_inputs_raise():
    """Mismatched lengths must raise — a silent zip-truncation would fit on
    a quiet partial dataset."""
    from backtest.frameworks import calibrate_persistence
    attrs = [_attr() for _ in range(5)]
    events = _events_frame_for(attrs, [0.0] * 3)
    events = events.iloc[:3].reset_index(drop=True)
    with pytest.raises(ValueError, match="align 1:1"):
        calibrate_persistence(events, attrs)


def test_calibrate_persistence_missing_column_raises():
    """An events_df without the requested forward-return column must raise."""
    import pandas as pd
    from backtest.frameworks import calibrate_persistence
    attrs = [_attr() for _ in range(10)]
    bad = pd.DataFrame([{"event_id": f"ev_{i}", "reaction_return": 0.01} for i in range(10)])
    with pytest.raises(ValueError, match="fwd_5d_excess"):
        calibrate_persistence(bad, attrs, horizon=5, use_excess=True)


def test_calibrate_persistence_invalid_horizon_raises():
    from backtest.frameworks import calibrate_persistence
    attrs = [_attr() for _ in range(10)]
    events = _events_frame_for(attrs, [0.0] * 10)
    with pytest.raises(ValueError, match="horizon"):
        calibrate_persistence(events, attrs, horizon=42)


def test_calibrate_persistence_empty_returns_prior():
    from backtest.frameworks import (
        DEFAULT_DIMENSION_PERSISTENCE,
        calibrate_persistence,
    )
    import pandas as pd
    out = calibrate_persistence(pd.DataFrame(), [])
    assert out == DEFAULT_DIMENSION_PERSISTENCE


def test_calibrate_persistence_output_plugs_into_dimension_weighted():
    """The dict returned by calibrate_persistence must be drop-in compatible
    with strategy_dimension_weighted(persistence=...)."""
    from backtest.frameworks import calibrate_persistence
    n = 40
    attrs = [
        _attr(
            weights={"demand": 0.7, "macro": 0.05},
            directions={"demand": "negative"},
            return_pct=-0.05, predicted_return_pct=-0.04,
        )
        for _ in range(n)
    ]
    events = _events_frame_for(attrs, [-0.02] * n)
    coeffs = calibrate_persistence(events, attrs)
    # No exception when plugged in
    out = strategy_dimension_weighted(attrs[0], persistence=coeffs)
    assert out in ("lean", "fade", "neutral")
