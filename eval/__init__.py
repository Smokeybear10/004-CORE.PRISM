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

from eval.config import ScorerConfig
from eval.scorer import ExpectedAttribution, ScoreResult, score

__all__ = ["ScorerConfig", "ExpectedAttribution", "ScoreResult", "score"]
