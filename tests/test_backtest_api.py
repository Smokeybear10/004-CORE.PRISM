"""
Public API tests for `backtest` package.

Covers:
  - fade_or_follow: matches signal.strategy_fundamental_vs_nonfundamental
    when predicted_return_pct is None; engages the magnitude rule when it isn't.
  - run_ablation: produces one Attribution-list per AblationConfig.
  - evaluate: reduces a list of Attributions + realized returns to a BacktestResult.
"""
from __future__ import annotations

from datetime import date

import pytest

from schema import (
    AblationConfig,
    Attribution,
    BacktestResult,
    DimensionScore,
    PriceMove,
    SourceType,
)
from backtest import DEFAULT_ABLATIONS, evaluate, fade_or_follow, run_ablation
from backtest.signal import strategy_fundamental_vs_nonfundamental


def _dim() -> DimensionScore:
    return DimensionScore(
        weight=0.2, direction="neutral", rationale="t",
        evidence_chunk_ids=["c0"],
    )


def _attr(move_character: str = "structural",
          return_pct: float = 0.05,
          predicted_return_pct: float | None = None,
          ticker: str = "AAPL",
          move_date: date = date(2024, 1, 5),
          ablation_name: str = "base_news") -> Attribution:
    return Attribution(
        ticker=ticker, move_date=move_date,
        return_pct=return_pct, predicted_return_pct=predicted_return_pct,
        demand=_dim(), pricing=_dim(), competitive=_dim(),
        management_credibility=_dim(), macro=_dim(),
        move_character=move_character, confidence=0.7,
        ablation_name=ablation_name, sources_used=[], chunks_considered=5,
    )


# ---------- public imports ----------

def test_public_imports_do_not_raise():
    from backtest import (  # noqa: F401
        DEFAULT_ABLATIONS,
        evaluate,
        fade_or_follow,
        run_ablation,
    )


# ---------- fade_or_follow ----------

@pytest.mark.parametrize("character", ["structural", "transient", "mixed", "unclear"])
def test_fade_or_follow_matches_strategy_when_no_prediction(character):
    attr = _attr(character, predicted_return_pct=None)
    assert fade_or_follow(attr) == strategy_fundamental_vs_nonfundamental(attr)


def test_transient_fade_when_realized_exceeds_1_5x_predicted():
    attr = _attr("transient", return_pct=0.10, predicted_return_pct=0.05)
    # |0.10| > 1.5 * |0.05| -> fade
    assert fade_or_follow(attr) == "fade"


def test_transient_neutral_when_realized_below_1_5x():
    attr = _attr("transient", return_pct=0.06, predicted_return_pct=0.05)
    # |0.06| <= 1.5 * |0.05| -> neutral
    assert fade_or_follow(attr) == "neutral"


def test_structural_lean_when_signs_match():
    attr = _attr("structural", return_pct=0.04, predicted_return_pct=0.05)
    assert fade_or_follow(attr) == "lean"


def test_structural_neutral_when_signs_disagree():
    attr = _attr("structural", return_pct=-0.04, predicted_return_pct=0.05)
    assert fade_or_follow(attr) == "neutral"


def test_fade_or_follow_respects_explicit_realized_argument():
    attr = _attr("transient", return_pct=0.01, predicted_return_pct=0.05)
    # Using attribution.return_pct it would be neutral; override pushes it over 1.5x.
    assert fade_or_follow(attr, realized_return_pct=0.10) == "fade"


# ---------- run_ablation ----------

def test_run_ablation_returns_one_list_per_config():
    moves = [
        PriceMove(ticker="AAPL", move_date=date(2024, 1, 5),
                  return_pct=0.06, vol_zscore=3.0, is_significant=True),
        PriceMove(ticker="NVDA", move_date=date(2024, 1, 8),
                  return_pct=-0.05, vol_zscore=-2.8, is_significant=True),
    ]
    out = run_ablation(moves, chunks_by_source={}, configs=DEFAULT_ABLATIONS)
    assert set(out.keys()) == {c.name for c in DEFAULT_ABLATIONS}
    for name, attrs in out.items():
        assert len(attrs) == len(moves)
        for a in attrs:
            assert a.ablation_name == name
            assert a.move_character in ("structural", "transient", "mixed", "unclear")


def test_run_ablation_with_custom_configs():
    moves = [PriceMove(ticker="AAPL", move_date=date(2024, 1, 5),
                       return_pct=0.05, vol_zscore=2.5, is_significant=True)]
    configs = [
        AblationConfig(name="only_news", sources=[SourceType.NEWS]),
        AblationConfig(name="with_sec",
                       sources=[SourceType.NEWS, SourceType.SEC_10K]),
    ]
    out = run_ablation(moves, chunks_by_source={}, configs=configs)
    assert set(out.keys()) == {"only_news", "with_sec"}


# ---------- evaluate ----------

def test_evaluate_empty_returns_zero_trade_result():
    result = evaluate([], realized_next5_returns={})
    assert isinstance(result, BacktestResult)
    assert result.n_trades == 0


def test_evaluate_happy_path_produces_backtest_result():
    attrs = [
        _attr("structural", return_pct=0.06,
              ticker="AAPL", move_date=date(2024, 1, 5)),
        _attr("transient", return_pct=-0.04,
              ticker="NVDA", move_date=date(2024, 1, 8)),
        _attr("structural", return_pct=0.03,
              ticker="AMD", move_date=date(2024, 1, 12)),
    ]
    realized = {
        "AAPL_20240105": 0.02,
        "NVDA_20240108": -0.01,
        "AMD_20240112":  0.015,
    }
    result = evaluate(attrs, realized)
    assert isinstance(result, BacktestResult)
    assert result.ablation_name == "base_news"
    assert result.n_trades > 0  # at least one non-neutral trade expected


def test_evaluate_skips_attributions_without_matching_realized():
    attrs = [_attr("structural", ticker="AAPL", move_date=date(2024, 1, 5))]
    # realized dict has a different key -> attribution is skipped
    result = evaluate(attrs, realized_next5_returns={"NVDA_20240108": 0.01})
    assert result.n_trades == 0
    assert "no attributions matched" in (result.notes or "")
