"""
Compare a model Attribution against an expected fixture. Returns a
ScoreResult; composite is a weighted sum normalized by the weights of
assertions actually present in the fixture.

Dimension-agnostic: the scorer discovers DimensionScore-typed fields on
Attribution via pydantic introspection, so adding/removing/renaming
dimensions on schema.Attribution does not require changes here.

Fixture assertions are all optional. A missing key = assertion skipped,
not failed. See eval/DESIGN.md for the full fixture format.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from eval.config import DEFAULT_CONFIG, ScorerConfig
from schema import (
    AblationConfig,
    Attribution,
    DimensionScore,
    SourceType,
)


# ---------- Fixture contract ----------

class ExpectedAttribution(BaseModel):
    """
    The `expected` block of a fixture file. Every field is optional; score()
    only evaluates assertions that are present.

    `dominant_dimension` is a list to allow genuine ambiguity in the expected
    answer (e.g. ["macro", "competitive"] when either is defensible).
    """
    dominant_dimension: Optional[list[str]] = None
    direction: Optional[Literal["positive", "negative", "neutral"]] = None
    move_character: Optional[Literal["structural", "transient", "mixed", "unclear"]] = None
    fade_or_lean: Optional[Literal["lean", "fade", "neutral"]] = None
    must_cite_source_type: Optional[list[SourceType]] = None
    must_not_be_dominant: Optional[list[str]] = None


# ---------- Result ----------

class ScoreResult(BaseModel):
    case_id: str                          # f"{ticker}_{move_date}"
    ablation_name: str
    composite: float = Field(ge=0.0, le=1.0)

    # Per-assertion outcomes. None = assertion not present in fixture (skipped).
    dim_match: Optional[bool] = None
    dir_match: Optional[bool] = None
    char_match: Optional[bool] = None
    fade_lean_match: Optional[bool] = None
    cite_source_match: Optional[bool] = None
    must_not_be_dominant_ok: Optional[bool] = None

    # Observed values, for debugging.
    observed_dominant_dimension: Optional[str] = None
    observed_direction: Optional[str] = None
    observed_move_character: Optional[str] = None
    observed_fade_or_lean: Optional[str] = None
    cited_source_types: list[str] = Field(default_factory=list)

    notes: list[str] = Field(default_factory=list)


# ---------- Dimension introspection ----------

def _dimension_fields(attribution: Attribution) -> dict[str, DimensionScore]:
    """Every DimensionScore-typed field on the Attribution, whatever it's called."""
    result: dict[str, DimensionScore] = {}
    for name in type(attribution).model_fields:
        value = getattr(attribution, name)
        if isinstance(value, DimensionScore):
            result[name] = value
    return result


def _dominant_dimension(attribution: Attribution) -> Optional[str]:
    dims = _dimension_fields(attribution)
    if not dims:
        return None
    return max(dims, key=lambda name: dims[name].weight)


def _direction_of(attribution: Attribution, dim_name: str) -> Optional[str]:
    dims = _dimension_fields(attribution)
    dim = dims.get(dim_name)
    return dim.direction if dim is not None else None


# ---------- Citation extraction ----------

def _source_type_from_chunk_id(chunk_id: str) -> Optional[SourceType]:
    """
    Parse source_type from the stable chunk_id prefix. Format is locked in
    CLAUDE.md: {source_type}_{ticker}_{YYYY-MM-DD}_{section}_{NNN}.
    """
    for st in SourceType:
        if chunk_id.startswith(f"{st.value}_"):
            return st
    return None


def _cited_source_types(attribution: Attribution) -> list[SourceType]:
    dims = _dimension_fields(attribution)
    seen: list[SourceType] = []
    for dim in dims.values():
        for cid in dim.evidence_chunk_ids:
            st = _source_type_from_chunk_id(cid)
            if st is not None and st not in seen:
                seen.append(st)
    return seen


# ---------- Scoring ----------

def score(
    attribution: Attribution,
    expected: ExpectedAttribution | dict,
    ablation: AblationConfig,
    config: ScorerConfig = DEFAULT_CONFIG,
) -> ScoreResult:
    """
    Score an Attribution against a fixture's expected block.

    Normalization: composite = sum(weight_i * match_i) / sum(weight_i) across
    the assertions that were actually evaluated. Assertions absent from the
    fixture are skipped entirely (no denominator contribution).
    """
    if isinstance(expected, dict):
        expected = ExpectedAttribution(**expected)

    weights = config.weights_map()
    numerator = 0.0
    denominator = 0.0
    notes: list[str] = []

    result = ScoreResult(
        case_id=f"{attribution.ticker}_{attribution.move_date}",
        ablation_name=ablation.name,
        composite=0.0,
    )

    # --- Dominant dimension ---
    dom = _dominant_dimension(attribution)
    result.observed_dominant_dimension = dom

    if expected.dominant_dimension is not None and weights["dim"] > 0:
        match = dom is not None and dom in expected.dominant_dimension
        result.dim_match = match
        numerator += weights["dim"] * (1.0 if match else 0.0)
        denominator += weights["dim"]
        if not match:
            notes.append(
                f"dominant_dimension: got {dom!r}, expected one of "
                f"{expected.dominant_dimension!r}"
            )

    # --- must_not_be_dominant ---
    if expected.must_not_be_dominant is not None:
        ok = dom is None or dom not in expected.must_not_be_dominant
        result.must_not_be_dominant_ok = ok
        # This is a veto, not a weighted assertion: if violated, treat as a
        # hard zero on the dim slot. Noted but not double-counted in composite.
        if not ok:
            notes.append(
                f"must_not_be_dominant violated: {dom!r} is in "
                f"{expected.must_not_be_dominant!r}"
            )
            # If dim was already counted as a match, downgrade it.
            if result.dim_match:
                numerator -= weights["dim"]
                result.dim_match = False

    # --- Direction ---
    # Uses the direction of whichever dimension the model said was dominant —
    # that's the move's net sign per the model. Fine for MVP; we can switch to
    # a weighted-sum-of-directions later if needed.
    observed_direction = _direction_of(attribution, dom) if dom else None
    result.observed_direction = observed_direction

    if expected.direction is not None and weights["dir"] > 0:
        match = observed_direction == expected.direction
        result.dir_match = match
        numerator += weights["dir"] * (1.0 if match else 0.0)
        denominator += weights["dir"]
        if not match:
            notes.append(
                f"direction: got {observed_direction!r}, expected "
                f"{expected.direction!r}"
            )

    # --- Move character ---
    result.observed_move_character = attribution.move_character

    if expected.move_character is not None and weights["char"] > 0:
        match = attribution.move_character == expected.move_character
        result.char_match = match
        numerator += weights["char"] * (1.0 if match else 0.0)
        denominator += weights["char"]
        if not match:
            notes.append(
                f"move_character: got {attribution.move_character!r}, expected "
                f"{expected.move_character!r}"
            )

    # --- Fade or lean ---
    # Derived via backtest.fade_or_follow, which uses move_character +
    # predicted_return_pct. Falls back to a simple structural->lean /
    # transient->fade mapping when predicted isn't populated.
    if expected.fade_or_lean is not None and weights["fade_lean"] > 0:
        from backtest import fade_or_follow
        try:
            observed = fade_or_follow(attribution, attribution.return_pct)
        except Exception as e:
            notes.append(f"fade_or_lean: fade_or_follow raised {type(e).__name__}: {e}")
        else:
            result.observed_fade_or_lean = observed
            match = observed == expected.fade_or_lean
            result.fade_lean_match = match
            numerator += weights["fade_lean"] * (1.0 if match else 0.0)
            denominator += weights["fade_lean"]
            if not match:
                notes.append(
                    f"fade_or_lean: got {observed!r}, expected "
                    f"{expected.fade_or_lean!r}"
                )

    # --- Citations ---
    cited = _cited_source_types(attribution)
    result.cited_source_types = [st.value for st in cited]

    if expected.must_cite_source_type is not None and weights["cite_source"] > 0:
        required = list(expected.must_cite_source_type)
        if config.auto_skip_unreachable_sources:
            dropped = [st for st in required if st not in ablation.sources]
            required = [st for st in required if st in ablation.sources]
            if dropped:
                notes.append(
                    f"cite_source: ablation {ablation.name!r} excludes "
                    f"{[st.value for st in dropped]}; dropped from requirement."
                )
        if required:
            missing = [st for st in required if st not in cited]
            match = len(missing) == 0
            result.cite_source_match = match
            numerator += weights["cite_source"] * (1.0 if match else 0.0)
            denominator += weights["cite_source"]
            if not match:
                notes.append(
                    f"cite_source: missing {[st.value for st in missing]}"
                )

    # --- Composite ---
    if denominator == 0.0:
        # Fixture asserted nothing the scorer scores, or all weights are 0.
        result.composite = 0.0
        notes.append(
            "No scorable assertions — composite is 0.0 by convention. "
            "Add assertions to the fixture or raise non-zero weights."
        )
    else:
        result.composite = max(0.0, min(1.0, numerator / denominator))

    result.notes = notes
    return result
