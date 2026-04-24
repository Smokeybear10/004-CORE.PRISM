"""
Scoring configuration. Keep weights here so tuning them doesn't touch
scorer.py logic.

Setting a weight to 0.0 logs-but-excludes that assertion from the composite.
Handy for A/B-ing whether a metric matters before deleting it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScorerConfig:
    # Per-assertion weights. Do not need to sum to 1 — score() normalizes by
    # the sum of weights for assertions actually present in the fixture.
    dim_weight: float = 0.5
    dir_weight: float = 0.25
    char_weight: float = 0.15
    fade_lean_weight: float = 0.10
    cite_source_weight: float = 0.0  # default off: entangled with ablation sources

    # If True, when the fixture asserts must_cite_source_type but the active
    # ablation excludes one of those sources, that entry is stripped from the
    # assertion rather than counted as a failure. See DESIGN.md open question 2.
    auto_skip_unreachable_sources: bool = True

    def weights_map(self) -> dict[str, float]:
        return {
            "dim": self.dim_weight,
            "dir": self.dir_weight,
            "char": self.char_weight,
            "fade_lean": self.fade_lean_weight,
            "cite_source": self.cite_source_weight,
        }


DEFAULT_CONFIG = ScorerConfig()
