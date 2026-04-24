"""
Stage-2 Layer (b): Calibration anchors via baselines.

The four baselines in `backtest.baselines` are not decoration — they are the
floor. If the structured strategy doesn't beat them, the harness has either a
bug or the structured model is meaningless:

    structured <= random_attribution  => attribution signal is noise
    structured <= sentiment_only      => sentiment is doing all the work
                                         (the prompt-bleaching concern)

This module runs the matrix (structured ablations × baselines), reports
per-metric deltas, and raises on floor violations so the comparison is a
PASS/FAIL contract, not just a chart.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field

from backtest.baselines import BASELINES
from backtest.fixtures import ABLATION_BUNDLES
from backtest.runner import run_ablation, run_baseline
from schema import BacktestResult


# Baselines the structured strategy MUST beat. `always_lean` / `always_fade`
# are price-math strawmen — passing or failing them is an interesting signal
# but not a hard floor. `random_attribution` and `sentiment_only` test the two
# specific failure modes the reviewer flagged.
FLOOR_BASELINES: tuple[str, ...] = ("random_attribution", "sentiment_only")

# Which numeric field on BacktestResult we treat as the comparison metric.
CalibrationMetric = Literal["sharpe", "hit_rate", "avg_return"]


class FloorCheck(BaseModel):
    """One (ablation, baseline, metric) comparison."""
    ablation_name: str
    baseline_name: str
    metric: CalibrationMetric
    structured_value: float
    baseline_value: float
    margin_required: float
    delta: float                      # structured - baseline
    passed: bool
    reason: Optional[str] = None


class CalibrationReport(BaseModel):
    """PASS/FAIL report across the full ablation × baseline matrix."""
    timestamp: datetime
    metric: CalibrationMetric
    margin_required: float
    ablation_results: list[BacktestResult] = Field(default_factory=list)
    baseline_results: list[BacktestResult] = Field(default_factory=list)
    floor_checks: list[FloorCheck] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(fc.passed for fc in self.floor_checks)

    @property
    def failures(self) -> list[FloorCheck]:
        return [fc for fc in self.floor_checks if not fc.passed]

    def assert_beats_floor(self) -> None:
        """Raise AssertionError summarizing every floor violation."""
        fails = self.failures
        if not fails:
            return
        lines = [
            f"{len(fails)} floor violation(s) on metric={self.metric!r} "
            f"(margin={self.margin_required}):"
        ]
        for fc in fails:
            lines.append(
                f"  - {fc.ablation_name} vs {fc.baseline_name}: "
                f"structured={fc.structured_value:+.4f}  "
                f"baseline={fc.baseline_value:+.4f}  "
                f"delta={fc.delta:+.4f}"
            )
        raise AssertionError("\n".join(lines))


def _metric_of(result: BacktestResult, metric: CalibrationMetric) -> float:
    return float(getattr(result, metric))


def compare_to_baselines(
    events_df: pd.DataFrame,
    *,
    ablations: Optional[list[str]] = None,
    baselines: tuple[str, ...] = FLOOR_BASELINES,
    metric: CalibrationMetric = "sharpe",
    margin_required: float = 0.0,
    horizon: int = 5,
    use_excess: bool = True,
    seed: int = 0,
) -> CalibrationReport:
    """
    Run each structured ablation and each floor baseline on `events_df`, then
    compare structured vs. baseline on `metric`. A structured run PASSES a
    floor check when `structured_metric - baseline_metric >= margin_required`.

    Tip: for hackathon MVP, `metric="sharpe"` with `margin_required=0.0` is
    the right contract. Tighten the margin once the universe is stable.
    """
    ablation_names = list(ablations) if ablations is not None else list(ABLATION_BUNDLES)

    ablation_results: list[BacktestResult] = []
    for name in ablation_names:
        res, _ = run_ablation(events_df, name, horizon=horizon, use_excess=use_excess, seed=seed)
        ablation_results.append(res)

    baseline_results: list[BacktestResult] = []
    for name in baselines:
        if name not in BASELINES:
            raise ValueError(f"unknown baseline {name!r}; known: {sorted(BASELINES)}")
        res, _ = run_baseline(events_df, name, horizon=horizon, use_excess=use_excess)
        baseline_results.append(res)

    floor_checks: list[FloorCheck] = []
    for ab in ablation_results:
        s_val = _metric_of(ab, metric)
        for bl_name, bl in zip(baselines, baseline_results):
            b_val = _metric_of(bl, metric)
            delta = s_val - b_val
            passed = delta >= margin_required
            reason = None
            if not passed:
                if bl_name == "random_attribution":
                    reason = "structured <= random_attribution: attribution may be noise"
                elif bl_name == "sentiment_only":
                    reason = "structured <= sentiment_only: polarity may be doing all the work"
                else:
                    reason = f"structured did not clear {bl_name} by the required margin"
            floor_checks.append(FloorCheck(
                ablation_name=ab.ablation_name or "unknown",
                baseline_name=bl_name,
                metric=metric,
                structured_value=s_val,
                baseline_value=b_val,
                margin_required=margin_required,
                delta=delta,
                passed=passed,
                reason=reason,
            ))

    return CalibrationReport(
        timestamp=datetime.now(),
        metric=metric,
        margin_required=margin_required,
        ablation_results=ablation_results,
        baseline_results=baseline_results,
        floor_checks=floor_checks,
    )
