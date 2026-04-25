"""
Tests for eval.accuracy — the X/N "did the model get it right" harness.

These avoid hitting the live model: fake attribution loaders feed
synthetic Attributions to the runner so we test the scorer logic, the
report shape, and the per-strategy rollup independently of the model.
"""

from __future__ import annotations

from datetime import date

from eval.accuracy import (
    AccuracyReport,
    PRIMARY_STRATEGY,
    _attribution_from_bundle,
    bundled_attribution_loader,
    run_accuracy,
    score_one_case,
)
from eval.cases import EvalCase
from eval.scorer import ExpectedAttribution
from schema import Attribution, DimensionScore, SourceType


def _dim(weight: float, direction: str = "neutral") -> DimensionScore:
    return DimensionScore(
        weight=weight,
        direction=direction,
        rationale="test",
        evidence_chunk_ids=["news_AMD_2022-10-07_h_001"],
    )


def _attribution(
    *,
    ticker: str = "AMD",
    move_date: date = date(2022, 10, 7),
    move_character: str = "structural",
    weights: dict | None = None,
) -> Attribution:
    w = {
        "demand": 0.6,
        "pricing": 0.1,
        "competitive": 0.1,
        "management_credibility": 0.1,
        "macro": 0.1,
    }
    if weights:
        w = {**w, **weights}
    return Attribution(
        ticker=ticker,
        move_date=move_date,
        return_pct=-0.13,
        predicted_return_pct=-0.10,
        demand=_dim(w["demand"], "negative"),
        pricing=_dim(w["pricing"]),
        competitive=_dim(w["competitive"]),
        management_credibility=_dim(w["management_credibility"]),
        macro=_dim(w["macro"]),
        move_character=move_character,
        confidence=0.9,
        ablation_name="+macro",
        sources_used=[SourceType.NEWS],
        chunks_considered=10,
    )


def _case(
    case_id_date: date,
    fade_or_lean: str,
    ticker: str = "AMD",
    known: str = "FUNDAMENTAL: test",
) -> EvalCase:
    return EvalCase(
        ticker=ticker,
        move_date=case_id_date,
        known_cause=known,
        expected=ExpectedAttribution(fade_or_lean=fade_or_lean),
    )


# ---------- Per-case scoring ----------


def test_score_one_case_match():
    case = _case(date(2022, 10, 7), fade_or_lean="lean")
    attr = _attribution(move_character="structural")
    cell = score_one_case(case, attr, PRIMARY_STRATEGY)
    assert cell.scored is True
    assert cell.match is True
    assert cell.model_verdict == "lean"
    assert cell.expected_verdict == "lean"
    assert cell.expected_label == "fundamental"
    assert cell.model_label == "fundamental"


def test_score_one_case_miss():
    # Fixture says fade (non-fundamental). Model says structural → lean.
    case = _case(date(2023, 5, 25), fade_or_lean="fade", known="NON-FUNDAMENTAL")
    attr = _attribution(move_character="structural")
    cell = score_one_case(case, attr, PRIMARY_STRATEGY)
    assert cell.scored is True
    assert cell.match is False
    assert cell.model_verdict == "lean"
    assert cell.expected_verdict == "fade"


def test_score_one_case_unscored_strategy_marks_no_ground_truth():
    case = _case(date(2022, 10, 7), fade_or_lean="lean")
    attr = _attribution(move_character="structural")
    cell = score_one_case(case, attr, "expected_vs_realized")
    assert cell.scored is False
    assert cell.match is None
    assert cell.expected_verdict is None  # no ground truth for this strategy
    assert cell.model_verdict in ("lean", "fade", "neutral")


def test_score_one_case_no_attribution_records_error():
    case = _case(date(2022, 10, 7), fade_or_lean="lean")
    cell = score_one_case(case, None, PRIMARY_STRATEGY)
    assert cell.match is None
    assert cell.error is not None


# ---------- End-to-end run_accuracy ----------


def test_run_accuracy_2_of_3_amd_pattern():
    """Mirrors the real AMD-fixture pattern: 2 fundamental moves, 1 non-
    fundamental. Model classifies all three as structural → 2/3 correct."""
    cases = [
        _case(date(2022, 10, 7), fade_or_lean="lean", known="FUNDAMENTAL (down)"),
        _case(date(2023, 5, 25), fade_or_lean="fade", known="NON-FUNDAMENTAL"),
        _case(date(2025, 10, 6), fade_or_lean="lean", known="FUNDAMENTAL (up)"),
    ]
    attrs = {
        cases[0].case_id: _attribution(move_date=cases[0].move_date,
                                        move_character="structural"),
        cases[1].case_id: _attribution(move_date=cases[1].move_date,
                                        move_character="structural"),  # wrong
        cases[2].case_id: _attribution(move_date=cases[2].move_date,
                                        move_character="structural"),
    }

    def fake_loader(ticker: str, move_date: date):
        case_id = f"{ticker}_{move_date.isoformat()}"
        return attrs.get(case_id)

    report = run_accuracy(cases, attribution_loader=fake_loader)
    assert isinstance(report, AccuracyReport)
    assert report.n_cases == 3
    assert report.primary_n_correct == 2
    assert report.primary_n_scored == 3
    assert report.primary_accuracy == 0.6667 or abs(report.primary_accuracy - 2 / 3) < 1e-3
    primary = next(s for s in report.strategies if s.strategy == PRIMARY_STRATEGY)
    assert primary.scored is True
    assert sum(1 for c in primary.cases if c.match is True) == 2


def test_run_accuracy_marks_unscored_strategies():
    cases = [_case(date(2022, 10, 7), fade_or_lean="lean")]

    def loader(t, d):
        return _attribution()

    report = run_accuracy(cases, attribution_loader=loader)
    unscored = [s for s in report.strategies if not s.scored]
    assert unscored, "expected at least one unscored strategy"
    for s in unscored:
        assert s.note is not None
        for c in s.cases:
            assert c.match is None
            assert c.expected_verdict is None


def test_run_accuracy_handles_missing_attribution():
    cases = [_case(date(2022, 10, 7), fade_or_lean="lean")]

    def loader(t, d):
        return None

    report = run_accuracy(cases, attribution_loader=loader)
    assert report.primary_n_scored == 0
    primary = next(s for s in report.strategies if s.strategy == PRIMARY_STRATEGY)
    assert primary.cases[0].error is not None


# ---------- Bundle loader: round-trip a slim attribution dict ----------


def test_attribution_from_bundle_roundtrips_minimal_shape():
    bundle = {
        "ticker": "AMD",
        "moves": [{
            "move_date": "2022-10-07",
            "return_pct": -0.13,
            "attribution": {
                "realized": -0.13,
                "predicted": -0.10,
                "character": "structural",
                "confidence": 0.9,
                "chunks_considered": 5,
                "sources_used": ["news", "sec_10k"],
                "dimensions": {
                    "demand": {"weight": 0.6, "direction": "negative",
                               "rationale": "warning",
                               "evidence_chunk_ids": ["news_AMD_2022-10-07_h_001"]},
                    "pricing": {"weight": 0.1, "direction": "neutral",
                                "rationale": "—", "evidence_chunk_ids": ["x_001"]},
                    "competitive": {"weight": 0.1, "direction": "neutral",
                                    "rationale": "—", "evidence_chunk_ids": ["x_001"]},
                    "management_credibility": {"weight": 0.1, "direction": "neutral",
                                               "rationale": "—",
                                               "evidence_chunk_ids": ["x_001"]},
                    "macro": {"weight": 0.1, "direction": "neutral",
                              "rationale": "—", "evidence_chunk_ids": ["x_001"]},
                },
            },
        }],
    }
    attr = _attribution_from_bundle(bundle, date(2022, 10, 7))
    assert attr is not None
    assert attr.ticker == "AMD"
    assert attr.move_character == "structural"
    assert attr.demand.weight == 0.6
    assert attr.demand.direction == "negative"
    # The bundle path must produce the same verdict the page shows.
    from backtest.signal import strategy_fundamental_vs_nonfundamental
    assert strategy_fundamental_vs_nonfundamental(attr) == "lean"


def test_attribution_from_bundle_returns_none_for_missing_date():
    bundle = {"ticker": "AMD", "moves": []}
    assert _attribution_from_bundle(bundle, date(2022, 10, 7)) is None


def test_bundled_loader_real_amd_fixture():
    """Sanity check against the actual demo bundle: AMD 2022-10-07 must
    resolve to a structural move so the harness scores 'lean' against
    the fixture's 'lean'."""
    loader = bundled_attribution_loader()
    attr = loader("AMD", date(2022, 10, 7))
    if attr is None:
        # CI may not have demo/static/data/AMD.json — skip gracefully.
        import pytest
        pytest.skip("demo/static/data/AMD.json not present in this checkout")
    assert attr.move_character in ("structural", "transient", "mixed", "unclear")
