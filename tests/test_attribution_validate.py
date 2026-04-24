"""
Unit tests for `model.attribution.validate.validate_attribution`.

Each test mutates one axis of a known-good Attribution and asserts that the
validator catches exactly that failure mode.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from model.attribution.validate import (
    WEIGHT_SUM_TOLERANCE,
    AttributionValidationError,
    validate_attribution,
)
from schema import Attribution, DimensionScore, JoinedEvidence

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "events" / "joined_evidence_sample.json"
)


def _load_evidence() -> JoinedEvidence:
    with open(FIXTURE_PATH) as f:
        return JoinedEvidence.model_validate(json.load(f))


def _make_valid_attribution(evidence: JoinedEvidence) -> Attribution:
    """Build an Attribution whose weights sum to 1.0 and whose citations all
    resolve. Used as the baseline that every test mutates."""
    chunks = [c.chunk_id for c in evidence.text_chunks]
    assert len(chunks) >= 2

    def _dim(weight: float, direction: str = "neutral", cites: list[str] | None = None) -> DimensionScore:
        return DimensionScore(
            weight=weight,
            direction=direction,
            rationale="baseline rationale",
            evidence_chunk_ids=cites or [chunks[0]],
        )

    return Attribution(
        ticker=evidence.move.ticker,
        move_date=evidence.move.move_date,
        return_pct=evidence.move.return_pct,
        predicted_return_pct=-0.03,
        demand=_dim(0.2, "negative"),
        pricing=_dim(0.1),
        competitive=_dim(0.4, "negative", cites=[chunks[1], chunks[0]]),
        management_credibility=_dim(0.2, "negative"),
        macro=_dim(0.1),
        move_character="mixed",
        confidence=0.7,
        ablation_name="test",
        sources_used=[c.source_type for c in evidence.text_chunks],
        chunks_considered=len(evidence.text_chunks),
    )


def test_wellformed_attribution_has_no_issues():
    evidence = _load_evidence()
    attr = _make_valid_attribution(evidence)
    assert validate_attribution(attr, evidence) == []


def test_hallucinated_chunk_id_is_flagged():
    evidence = _load_evidence()
    attr = _make_valid_attribution(evidence)
    attr.demand = attr.demand.model_copy(
        update={"evidence_chunk_ids": ["fake_chunk_id"]}
    )
    issues = validate_attribution(attr, evidence)
    assert any("hallucinated" in i and "fake_chunk_id" in i for i in issues)


def test_empty_evidence_chunk_ids_is_flagged():
    evidence = _load_evidence()
    attr = _make_valid_attribution(evidence)
    attr.pricing = attr.pricing.model_copy(update={"evidence_chunk_ids": []})
    issues = validate_attribution(attr, evidence)
    assert any("pricing" in i and "evidence_chunk_ids is empty" in i for i in issues)


def test_empty_rationale_is_flagged():
    evidence = _load_evidence()
    attr = _make_valid_attribution(evidence)
    attr.macro = attr.macro.model_copy(update={"rationale": "   "})
    issues = validate_attribution(attr, evidence)
    assert any("macro" in i and "rationale is empty" in i for i in issues)


def test_weights_not_summing_to_one_flagged_outside_tolerance():
    evidence = _load_evidence()
    attr = _make_valid_attribution(evidence)
    # push the sum away from 1.0 by more than the tolerance
    attr.demand = attr.demand.model_copy(update={"weight": 0.5})
    issues = validate_attribution(attr, evidence)
    assert any("weights sum" in i for i in issues)


def test_weights_within_tolerance_pass():
    """Floating-point rounding from the LLM shouldn't trip validation."""
    evidence = _load_evidence()
    attr = _make_valid_attribution(evidence)
    # bump one weight by less than the tolerance
    attr.demand = attr.demand.model_copy(
        update={"weight": 0.2 + WEIGHT_SUM_TOLERANCE * 0.5}
    )
    issues = validate_attribution(attr, evidence)
    assert not any("weights sum" in i for i in issues)


def test_ticker_mismatch_is_flagged():
    evidence = _load_evidence()
    attr = _make_valid_attribution(evidence)
    attr.ticker = "MSFT"
    issues = validate_attribution(attr, evidence)
    assert any("ticker mismatch" in i for i in issues)


def test_move_date_mismatch_is_flagged():
    from datetime import date

    evidence = _load_evidence()
    attr = _make_valid_attribution(evidence)
    attr.move_date = date(2020, 1, 1)
    issues = validate_attribution(attr, evidence)
    assert any("move_date mismatch" in i for i in issues)


def test_confidence_out_of_range_is_flagged():
    evidence = _load_evidence()
    attr = _make_valid_attribution(evidence)
    attr.confidence = 1.5
    issues = validate_attribution(attr, evidence)
    assert any("confidence" in i and "outside" in i for i in issues)


def test_multiple_issues_all_reported():
    """Validator must surface every failure, not short-circuit on the first."""
    evidence = _load_evidence()
    attr = _make_valid_attribution(evidence)
    attr.demand = attr.demand.model_copy(update={"evidence_chunk_ids": ["ghost"]})
    attr.pricing = attr.pricing.model_copy(update={"rationale": ""})
    attr.macro = attr.macro.model_copy(update={"weight": 0.9})  # pushes sum >> 1
    issues = validate_attribution(attr, evidence)

    assert any("demand" in i and "hallucinated" in i for i in issues)
    assert any("pricing" in i and "rationale" in i for i in issues)
    assert any("weights sum" in i for i in issues)


def test_validation_error_exposes_issue_list():
    """AttributionValidationError must keep the issues list machine-readable."""
    bad_issues = ["demand: hallucinated chunk_id 'x'", "weights sum to 1.2, not 1.0"]
    err = AttributionValidationError(bad_issues)
    assert err.issues == bad_issues
    assert "2 validation issue(s)" in str(err)
    assert isinstance(err, ValueError)
