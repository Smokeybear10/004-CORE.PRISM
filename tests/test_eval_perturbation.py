"""
Tests for Stage-2 Layer (c) — perturbation suite.

A fake "attribution runner" is a callable that takes a JoinedEvidence and
returns an Attribution. Tests vary its behavior (deterministic vs. flaky,
clean vs. junk-citing) to exercise each check's pass/fail logic.
"""
from __future__ import annotations

import copy
from datetime import date

import pytest

from eval.perturbation import (
    check_determinism,
    check_junk_injection,
    check_shuffle_stability,
    run_perturbation_suite,
)
from schema import (
    Attribution,
    CoherenceCheck,
    DimensionScore,
    JoinedEvidence,
    PriceMove,
    SourceType,
    TextChunk,
)


# ---------- fixtures ----------


def _chunk(chunk_id: str, source_type: SourceType = SourceType.NEWS,
           ticker: str = "AMD") -> TextChunk:
    return TextChunk(
        chunk_id=chunk_id,
        ticker=ticker,
        source_type=source_type,
        publication_date=date(2022, 10, 7),
        section_name="p0",
        text=f"fake text for {chunk_id}",
        token_count=10,
    )


def _evidence(n_chunks: int = 3) -> JoinedEvidence:
    chunks = [_chunk(f"news_AMD_2022-10-07_h_{i:03d}") for i in range(n_chunks)]
    move = PriceMove(
        ticker="AMD",
        move_date=date(2022, 10, 7),
        return_pct=-0.135,
        vol_zscore=-4.2,
        is_significant=True,
    )
    return JoinedEvidence(
        move=move, window_start=date(2022, 10, 1), window_end=date(2022, 10, 14),
        events=[], text_chunks=chunks,
    )


def _build_attribution(
    evidence: JoinedEvidence,
    *,
    dominant: str = "demand",
    move_character: str = "structural",
    weights: dict[str, float] | None = None,
    cite_ids: list[str] | None = None,
) -> Attribution:
    defaults = {
        "demand": 0.1, "pricing": 0.1, "competitive": 0.1,
        "management_credibility": 0.1, "macro": 0.1,
    }
    if weights is None:
        weights = dict(defaults)
        weights[dominant] = 0.6
    cites = cite_ids or [evidence.text_chunks[0].chunk_id]
    dims = {
        name: DimensionScore(
            weight=weights[name],
            direction="negative" if name == dominant else "neutral",
            rationale=f"{name} rationale",
            evidence_chunk_ids=list(cites),
        )
        for name in defaults
    }
    return Attribution(
        ticker=evidence.move.ticker,
        move_date=evidence.move.move_date,
        return_pct=evidence.move.return_pct,
        predicted_return_pct=evidence.move.return_pct,
        **dims,
        move_character=move_character,
        confidence=0.8,
        ablation_name="test",
        sources_used=[SourceType.NEWS],
        chunks_considered=len(evidence.text_chunks),
    )


# ---------- Shuffle ----------


def test_shuffle_passes_when_runner_is_order_invariant():
    def run(ev: JoinedEvidence) -> Attribution:
        return _build_attribution(ev, dominant="demand", move_character="structural")
    result = check_shuffle_stability(_evidence(), run)
    assert result.passed
    assert result.dominant_before == "demand"
    assert result.dominant_after == "demand"


def test_shuffle_fails_when_runner_flips_dominant_on_reorder():
    call_count = {"n": 0}

    def run(ev: JoinedEvidence) -> Attribution:
        call_count["n"] += 1
        # First call (original) → demand dominant. Second (shuffled) → macro.
        if call_count["n"] == 1:
            return _build_attribution(ev, dominant="demand")
        return _build_attribution(ev, dominant="macro")

    result = check_shuffle_stability(_evidence(), run)
    assert result.passed is False
    assert "flipped on shuffle" in (result.reason or "")


def test_shuffle_fails_on_weight_drift_beyond_tolerance():
    call_count = {"n": 0}

    def run(ev: JoinedEvidence) -> Attribution:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _build_attribution(
                ev, dominant="demand",
                weights={"demand": 0.6, "pricing": 0.1, "competitive": 0.1,
                         "management_credibility": 0.1, "macro": 0.1},
            )
        # Same dominant dim, same character — but macro weight swings by 0.25
        # (well over 0.15 tolerance) with demand still on top.
        return _build_attribution(
            ev, dominant="demand",
            weights={"demand": 0.5, "pricing": 0.05, "competitive": 0.05,
                     "management_credibility": 0.05, "macro": 0.35},
        )

    result = check_shuffle_stability(_evidence(), run, weight_tolerance=0.15)
    assert result.passed is False
    assert "weight drift" in (result.reason or "")


# ---------- Junk injection ----------


def test_junk_passes_when_attribution_ignores_junk():
    def run(ev: JoinedEvidence) -> Attribution:
        # Always cite the original first chunk, never the junk
        return _build_attribution(ev, dominant="demand",
                                  cite_ids=[ev.text_chunks[0].chunk_id])
    result = check_junk_injection(_evidence(), run)
    assert result.passed is True
    assert result.junk_cited is False


def test_junk_fails_when_model_cites_junk_and_no_coherence_catch():
    def run(ev: JoinedEvidence) -> Attribution:
        # If the junk chunk is in evidence, cite it; otherwise cite the first.
        junk_ids = [c.chunk_id for c in ev.text_chunks if c.ticker == "_JUNK_UNRELATED"]
        if junk_ids:
            return _build_attribution(ev, dominant="demand", cite_ids=[junk_ids[0]])
        return _build_attribution(ev, dominant="demand",
                                  cite_ids=[ev.text_chunks[0].chunk_id])
    result = check_junk_injection(_evidence(), run)
    assert result.passed is False
    assert result.junk_cited is True
    assert "was cited" in (result.reason or "")


def test_junk_passes_when_coherence_flags_junk():
    def run(ev: JoinedEvidence) -> Attribution:
        junk_ids = [c.chunk_id for c in ev.text_chunks if c.ticker == "_JUNK_UNRELATED"]
        if junk_ids:
            return _build_attribution(ev, dominant="demand", cite_ids=[junk_ids[0]])
        return _build_attribution(ev, dominant="demand",
                                  cite_ids=[ev.text_chunks[0].chunk_id])

    def coherence(attr: Attribution, ev: JoinedEvidence) -> CoherenceCheck:
        return CoherenceCheck(
            ticker=attr.ticker,
            move_date=attr.move_date,
            ablation_name=attr.ablation_name,
            plausible=False,
            issues=["cites unrelated company — category error"],
        )

    result = check_junk_injection(_evidence(), run, coherence_runner=coherence)
    assert result.passed is True
    assert result.coherence_caught is True


# ---------- Determinism ----------


def test_determinism_passes_when_runner_is_pure():
    def run(ev: JoinedEvidence) -> Attribution:
        return _build_attribution(ev, dominant="demand", move_character="structural")
    result = check_determinism(_evidence(), run, n_runs=3)
    assert result.passed is True
    assert result.dominant_dimensions == ["demand"] * 3


def test_determinism_fails_when_runner_is_flaky():
    call_count = {"n": 0}

    def run(ev: JoinedEvidence) -> Attribution:
        call_count["n"] += 1
        return _build_attribution(
            ev,
            dominant="demand" if call_count["n"] % 2 == 1 else "macro",
        )

    result = check_determinism(_evidence(), run, n_runs=3)
    assert result.passed is False
    assert set(result.dominant_dimensions) == {"demand", "macro"}


def test_determinism_requires_at_least_two_runs():
    def run(ev: JoinedEvidence) -> Attribution:
        return _build_attribution(ev)
    with pytest.raises(ValueError, match="at least 2"):
        check_determinism(_evidence(), run, n_runs=1)


# ---------- Orchestrator ----------


def test_run_perturbation_suite_aggregates_all_three():
    def run(ev: JoinedEvidence) -> Attribution:
        return _build_attribution(ev, dominant="demand", move_character="structural")
    report = run_perturbation_suite(_evidence(), run)
    assert report.shuffle is not None
    assert report.junk is not None
    assert report.determinism is not None
    assert report.passed is True


def test_run_perturbation_suite_reports_failure_when_any_subcheck_fails():
    call_count = {"n": 0}

    def run(ev: JoinedEvidence) -> Attribution:
        # Flaky runner — determinism will fail.
        call_count["n"] += 1
        return _build_attribution(
            ev,
            dominant="demand" if call_count["n"] % 2 == 1 else "pricing",
        )

    report = run_perturbation_suite(_evidence(), run)
    assert report.passed is False
