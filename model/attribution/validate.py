"""
Post-hoc validation for an Attribution against the evidence it was generated
from.

Checks (non-negotiable per CLAUDE.md):
  - every DimensionScore.evidence_chunk_ids is non-empty
  - every cited chunk_id resolves to a chunk in evidence.text_chunks
  - every rationale is non-empty (after strip)
  - weights across the five dimensions sum to ~1.0 (tolerance 0.02)
  - ticker and move_date on the attribution match the PriceMove
  - confidence is in [0, 1]

`validate_attribution` is a pure inspection — it returns a list of issue
strings and never raises. Callers that want exception semantics wrap it with
`raise AttributionValidationError(issues)`; the runner does this by default.
"""
from __future__ import annotations

from schema import Attribution, DimensionScore, JoinedEvidence

WEIGHT_SUM_TOLERANCE = 0.02

_DIM_NAMES = (
    "demand",
    "pricing",
    "competitive",
    "management_credibility",
    "macro",
)


class AttributionValidationError(ValueError):
    """Raised when one or more attribution validation issues are found."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = list(issues)
        joined = "; ".join(self.issues) if self.issues else "(no issues?)"
        super().__init__(f"{len(self.issues)} validation issue(s): {joined}")


def validate_attribution(attr: Attribution, evidence: JoinedEvidence) -> list[str]:
    """Return a list of issue strings describing any validation failures.

    Empty list means the attribution is valid.
    """
    issues: list[str] = []
    valid_chunk_ids = {c.chunk_id for c in evidence.text_chunks}

    if attr.ticker != evidence.move.ticker:
        issues.append(
            f"ticker mismatch: attribution={attr.ticker!r} vs move={evidence.move.ticker!r}"
        )
    if attr.move_date != evidence.move.move_date:
        issues.append(
            f"move_date mismatch: attribution={attr.move_date} vs move={evidence.move.move_date}"
        )
    if not (0.0 <= attr.confidence <= 1.0):
        issues.append(f"confidence {attr.confidence} is outside [0, 1]")

    total_weight = 0.0
    for name in _DIM_NAMES:
        dim: DimensionScore = getattr(attr, name)
        total_weight += dim.weight
        issues.extend(_check_dimension(name, dim, valid_chunk_ids))

    if abs(total_weight - 1.0) > WEIGHT_SUM_TOLERANCE:
        issues.append(
            f"dimension weights sum to {total_weight:.4f}, not 1.0 "
            f"(tolerance {WEIGHT_SUM_TOLERANCE})"
        )

    return issues


def _check_dimension(
    name: str,
    dim: DimensionScore,
    valid_chunk_ids: set[str],
) -> list[str]:
    issues: list[str] = []
    if not dim.rationale or not dim.rationale.strip():
        issues.append(f"{name}: rationale is empty")
    if not dim.evidence_chunk_ids:
        issues.append(f"{name}: evidence_chunk_ids is empty")
    else:
        for cid in dim.evidence_chunk_ids:
            if cid not in valid_chunk_ids:
                issues.append(
                    f"{name}: hallucinated chunk_id {cid!r} (not in evidence.text_chunks)"
                )
    return issues
