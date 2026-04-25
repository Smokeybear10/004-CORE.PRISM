"""
Attribution evaluation harness.

See eval/DESIGN.md for the full design. Short version:

    cases     → fixture files in tests/fixtures/*_expected.json
    runner    → iterates (case × ablation), calls model.attribute(), scores
    scorer    → compares Attribution to fixture, emits ScoreResult
    config    → ScorerConfig (weights, which assertions count)
    cache     → prompt-hash-keyed cache of raw Attribution JSON
    cli       → `python -m eval.run`

Public API (stable):
    - ScorerConfig          (config.py)
    - ExpectedAttribution   (scorer.py)  pydantic model of the fixture `expected` block
    - ScoreResult           (scorer.py)
    - score                 (scorer.py)
"""

from eval.accuracy import (
    AccuracyReport,
    CaseAccuracy,
    GROUND_TRUTHED_STRATEGIES,
    PRIMARY_STRATEGY,
    StrategyAccuracy,
    bundled_attribution_loader,
    format_report,
    live_attribution_loader,
    run_accuracy,
    score_one_case,
    write_report,
)
from eval.calibration import (
    CalibrationReport,
    FloorCheck,
    FLOOR_BASELINES,
    FrameworkComparison,
    FrameworkReport,
    compare_frameworks,
    compare_to_baselines,
)
from eval.config import ScorerConfig
from eval.distribution import (
    BucketStats,
    CharacterDistribution,
    DistributionReport,
    SanityFlag,
    breakdown_by_direction,
    breakdown_by_quarter,
    breakdown_by_ticker,
    character_distribution,
    check_distribution,
)
from eval.frozen import (
    FROZEN_PATH,
    FrozenAnchorReport,
    FrozenDiff,
    FrozenRunnerOptions,
    diff_case,
    load_frozen_cases,
    run_frozen_anchor,
)
from eval.perturbation import (
    DeterminismResult,
    JunkInjectionResult,
    PerturbationReport,
    ShuffleResult,
    check_determinism,
    check_junk_injection,
    check_shuffle_stability,
    run_perturbation_suite,
)
from eval.scorer import ExpectedAttribution, ScoreResult, score

__all__ = [
    # Accuracy harness (the X/N "did the model get it right" runner)
    "AccuracyReport",
    "CaseAccuracy",
    "GROUND_TRUTHED_STRATEGIES",
    "PRIMARY_STRATEGY",
    "StrategyAccuracy",
    "bundled_attribution_loader",
    "format_report",
    "live_attribution_loader",
    "run_accuracy",
    "score_one_case",
    "write_report",
    # Scoring primitives
    "ScorerConfig",
    "ExpectedAttribution",
    "ScoreResult",
    "score",
    # Layer (a) — frozen anchor
    "FROZEN_PATH",
    "FrozenAnchorReport",
    "FrozenDiff",
    "FrozenRunnerOptions",
    "diff_case",
    "load_frozen_cases",
    "run_frozen_anchor",
    # Layer (b) — calibration floor
    "CalibrationReport",
    "FloorCheck",
    "FLOOR_BASELINES",
    "FrameworkComparison",
    "FrameworkReport",
    "compare_frameworks",
    "compare_to_baselines",
    # Layer (c) — perturbation
    "DeterminismResult",
    "JunkInjectionResult",
    "PerturbationReport",
    "ShuffleResult",
    "check_determinism",
    "check_junk_injection",
    "check_shuffle_stability",
    "run_perturbation_suite",
    # Layer (d) — distributional sanity
    "BucketStats",
    "CharacterDistribution",
    "DistributionReport",
    "SanityFlag",
    "breakdown_by_direction",
    "breakdown_by_quarter",
    "breakdown_by_ticker",
    "character_distribution",
    "check_distribution",
]
