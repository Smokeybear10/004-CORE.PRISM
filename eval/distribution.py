"""
Stage-2 Layer (d): Distributional sanity across the focal universe.

Shape-of-output checks on a population of Attributions (or per-trade PnL
rows). The harness raises if:

    collapse_rate_max        — more than X% of attributions share a single
                               move_character (model is collapsing)
    min_sharpe_if_positive_hit_rate
                             — positive hit rate with pocket-change Sharpe
                               means the structured bet is too small to matter
    min_coverage_per_bucket  — per-ticker / per-quarter / per-direction cells
                               with too few events aren't trustworthy

Output is a single `DistributionReport` with per-bucket breakdowns so the
demo can render them without re-running the model.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field

from schema import Attribution


def _quarter_of(d: date) -> str:
    return f"{d.year}Q{((d.month - 1) // 3) + 1}"


def _direction_bucket(attribution: Attribution) -> str:
    if attribution.return_pct > 0:
        return "up"
    if attribution.return_pct < 0:
        return "down"
    return "flat"


def _dominant(attribution: Attribution) -> str:
    dims = {
        name: getattr(attribution, name).weight
        for name in ("demand", "pricing", "competitive", "management_credibility", "macro")
    }
    return max(dims, key=dims.get)


# ---------- Per-bucket row ----------

class BucketStats(BaseModel):
    """Stats for one slice of the universe (e.g. one ticker, one quarter)."""
    bucket_key: str
    n: int
    structural_pct: float
    transient_pct: float
    mixed_pct: float
    unclear_pct: float
    dominant_mix: dict[str, int] = Field(default_factory=dict)
    avg_return_pct: float
    avg_predicted_return_pct: Optional[float] = None
    hit_rate: Optional[float] = None         # sign(predicted) == sign(realized)
    avg_confidence: float


def _bucket_stats(key: str, group: list[Attribution]) -> BucketStats:
    n = len(group)
    if n == 0:
        return BucketStats(
            bucket_key=key, n=0,
            structural_pct=0.0, transient_pct=0.0,
            mixed_pct=0.0, unclear_pct=0.0,
            avg_return_pct=0.0, avg_confidence=0.0,
        )
    chars = [a.move_character for a in group]
    dom_counts: dict[str, int] = {}
    for a in group:
        d = _dominant(a)
        dom_counts[d] = dom_counts.get(d, 0) + 1

    predicted = [a.predicted_return_pct for a in group if a.predicted_return_pct is not None]
    avg_predicted = sum(predicted) / len(predicted) if predicted else None

    pairs = [
        (a.predicted_return_pct, a.return_pct)
        for a in group if a.predicted_return_pct is not None
    ]
    hit_rate = None
    if pairs:
        hits = sum(
            1 for p, r in pairs
            if (p > 0 and r > 0) or (p < 0 and r < 0) or (p == 0 and r == 0)
        )
        hit_rate = hits / len(pairs)

    return BucketStats(
        bucket_key=key,
        n=n,
        structural_pct=chars.count("structural") / n,
        transient_pct=chars.count("transient") / n,
        mixed_pct=chars.count("mixed") / n,
        unclear_pct=chars.count("unclear") / n,
        dominant_mix=dom_counts,
        avg_return_pct=sum(a.return_pct for a in group) / n,
        avg_predicted_return_pct=avg_predicted,
        hit_rate=hit_rate,
        avg_confidence=sum(a.confidence for a in group) / n,
    )


# ---------- Breakdowns ----------

def breakdown_by_ticker(attributions: list[Attribution]) -> list[BucketStats]:
    groups: dict[str, list[Attribution]] = {}
    for a in attributions:
        groups.setdefault(a.ticker, []).append(a)
    return [_bucket_stats(k, v) for k, v in sorted(groups.items())]


def breakdown_by_quarter(attributions: list[Attribution]) -> list[BucketStats]:
    groups: dict[str, list[Attribution]] = {}
    for a in attributions:
        groups.setdefault(_quarter_of(a.move_date), []).append(a)
    return [_bucket_stats(k, v) for k, v in sorted(groups.items())]


def breakdown_by_direction(attributions: list[Attribution]) -> list[BucketStats]:
    groups: dict[str, list[Attribution]] = {}
    for a in attributions:
        groups.setdefault(_direction_bucket(a), []).append(a)
    return [_bucket_stats(k, v) for k, v in sorted(groups.items())]


# ---------- Overall distribution ----------

class CharacterDistribution(BaseModel):
    n: int
    structural_pct: float
    transient_pct: float
    mixed_pct: float
    unclear_pct: float
    dominant_mix_pct: dict[str, float] = Field(default_factory=dict)


def character_distribution(attributions: list[Attribution]) -> CharacterDistribution:
    n = len(attributions)
    if n == 0:
        return CharacterDistribution(
            n=0, structural_pct=0.0, transient_pct=0.0, mixed_pct=0.0, unclear_pct=0.0
        )
    chars = [a.move_character for a in attributions]
    dom_counts: dict[str, int] = {}
    for a in attributions:
        d = _dominant(a)
        dom_counts[d] = dom_counts.get(d, 0) + 1
    return CharacterDistribution(
        n=n,
        structural_pct=chars.count("structural") / n,
        transient_pct=chars.count("transient") / n,
        mixed_pct=chars.count("mixed") / n,
        unclear_pct=chars.count("unclear") / n,
        dominant_mix_pct={k: v / n for k, v in dom_counts.items()},
    )


# ---------- Sanity flags ----------

class SanityFlag(BaseModel):
    kind: str          # "collapse", "magnitude", "coverage"
    bucket_key: Optional[str] = None
    passed: bool
    observed: float
    threshold: float
    message: str


class DistributionReport(BaseModel):
    timestamp: datetime
    n_attributions: int
    overall: CharacterDistribution
    by_ticker: list[BucketStats] = Field(default_factory=list)
    by_quarter: list[BucketStats] = Field(default_factory=list)
    by_direction: list[BucketStats] = Field(default_factory=list)
    flags: list[SanityFlag] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(f.passed for f in self.flags)

    @property
    def failures(self) -> list[SanityFlag]:
        return [f for f in self.flags if not f.passed]

    def assert_healthy(self) -> None:
        fails = self.failures
        if not fails:
            return
        lines = [f"{len(fails)} distributional sanity flag(s) tripped:"]
        for f in fails:
            bk = f" @ {f.bucket_key!r}" if f.bucket_key else ""
            lines.append(
                f"  - {f.kind}{bk}: observed={f.observed:.3f} "
                f"threshold={f.threshold:.3f} — {f.message}"
            )
        raise AssertionError("\n".join(lines))


def check_distribution(
    attributions: list[Attribution],
    *,
    collapse_rate_max: float = 0.95,
    min_coverage_per_bucket: int = 3,
    pnl_df: Optional[pd.DataFrame] = None,
    min_sharpe_if_positive_hit_rate: Optional[float] = None,
    observed_sharpe: Optional[float] = None,
    observed_hit_rate: Optional[float] = None,
) -> DistributionReport:
    """
    Build the report and flag anything the MVP reviewer would call out:

    1. Collapse: if any `move_character` or dominant-dimension bucket exceeds
       `collapse_rate_max` (default 95%), the model is not discriminating.
    2. Magnitude: when `observed_hit_rate > 0.5` but `observed_sharpe` is
       below `min_sharpe_if_positive_hit_rate`, the bet sizes are too small
       to matter even when the direction is right.
    3. Coverage: per-ticker / per-quarter buckets with n < `min_coverage_per_bucket`
       are flagged (not failed) so downstream readers can discount them.

    `pnl_df` is optional: if provided and no `observed_sharpe` is passed, we
    derive a crude Sharpe from the `pnl` column for the magnitude check.
    """
    overall = character_distribution(attributions)
    by_t = breakdown_by_ticker(attributions)
    by_q = breakdown_by_quarter(attributions)
    by_d = breakdown_by_direction(attributions)

    flags: list[SanityFlag] = []

    # --- Collapse on move_character ---
    for label, pct in (
        ("structural", overall.structural_pct),
        ("transient", overall.transient_pct),
        ("mixed", overall.mixed_pct),
        ("unclear", overall.unclear_pct),
    ):
        if pct > collapse_rate_max:
            flags.append(SanityFlag(
                kind="collapse",
                bucket_key=f"move_character={label}",
                passed=False,
                observed=pct,
                threshold=collapse_rate_max,
                message=(
                    f"{pct:.1%} of attributions have move_character={label!r} — "
                    "model may be collapsing onto one label"
                ),
            ))

    # --- Collapse on dominant dimension ---
    for dim_name, pct in overall.dominant_mix_pct.items():
        if pct > collapse_rate_max:
            flags.append(SanityFlag(
                kind="collapse",
                bucket_key=f"dominant_dimension={dim_name}",
                passed=False,
                observed=pct,
                threshold=collapse_rate_max,
                message=(
                    f"{pct:.1%} of attributions have {dim_name} as dominant — "
                    "possible prompt bleaching"
                ),
            ))

    # --- Magnitude check ---
    sharpe = observed_sharpe
    if sharpe is None and pnl_df is not None and "pnl" in pnl_df.columns:
        active = pnl_df[pnl_df["direction"] != 0] if "direction" in pnl_df.columns else pnl_df
        if len(active) > 1 and float(active["pnl"].std(ddof=1)) > 0:
            sharpe = float(active["pnl"].mean() / active["pnl"].std(ddof=1))

    hit_rate = observed_hit_rate
    if hit_rate is None and pnl_df is not None and "pnl" in pnl_df.columns:
        active = pnl_df[pnl_df["direction"] != 0] if "direction" in pnl_df.columns else pnl_df
        if len(active) > 0:
            hit_rate = float((active["pnl"] > 0).mean())

    if (
        min_sharpe_if_positive_hit_rate is not None
        and hit_rate is not None
        and sharpe is not None
        and hit_rate > 0.5
        and sharpe < min_sharpe_if_positive_hit_rate
    ):
        flags.append(SanityFlag(
            kind="magnitude",
            passed=False,
            observed=sharpe,
            threshold=min_sharpe_if_positive_hit_rate,
            message=(
                f"hit_rate={hit_rate:.2f} but Sharpe={sharpe:.3f} — "
                "directionally right but magnitudes are too small to matter"
            ),
        ))

    # --- Coverage (warnings) ---
    for bucket_list, label in (
        (by_t, "ticker"), (by_q, "quarter"), (by_d, "direction"),
    ):
        for b in bucket_list:
            if b.n < min_coverage_per_bucket:
                flags.append(SanityFlag(
                    kind="coverage",
                    bucket_key=f"{label}={b.bucket_key}",
                    passed=False,
                    observed=float(b.n),
                    threshold=float(min_coverage_per_bucket),
                    message=(
                        f"{label}={b.bucket_key!r} has only {b.n} observations — "
                        "bucket stats are not trustworthy"
                    ),
                ))

    return DistributionReport(
        timestamp=datetime.now(),
        n_attributions=len(attributions),
        overall=overall,
        by_ticker=by_t,
        by_quarter=by_q,
        by_direction=by_d,
        flags=flags,
    )
