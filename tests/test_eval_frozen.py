"""
Tests for Stage-2 Layer (a) — frozen-case anchor.

Unit path uses a fake Anthropic client; no API calls, no network. One live
test is gated behind RUN_LIVE_API=1 so it only runs in manual triage.
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.frozen import (
    FROZEN_PATH,
    FrozenDiff,
    FrozenRunnerOptions,
    diff_case,
    load_frozen_cases,
    run_frozen_anchor,
)

# Inlined to avoid pulling model.attribution (and its anthropic import) into
# the collection path. This must match model.attribution.prompt.ATTRIBUTION_TOOL_NAME.
ATTRIBUTION_TOOL_NAME = "emit_attribution"

from schema import (
    AblationConfig,
    Attribution,
    DimensionScore,
    JoinedEvidence,
    PriceMove,
    SourceType,
    TextChunk,
)


# ---------- helpers ----------


def _attribution_for(
    ticker: str,
    move_date: date,
    *,
    dominant: str,
    direction: str,
    move_character: str,
    return_pct: float,
) -> Attribution:
    """Hand-build an Attribution matching the expected block of a frozen case."""
    dims = {}
    for name in ("demand", "pricing", "competitive", "management_credibility", "macro"):
        weight = 0.7 if name == dominant else 0.075
        dims[name] = DimensionScore(
            weight=weight,
            direction=direction if name == dominant else "neutral",
            rationale=f"{name} rationale",
            evidence_chunk_ids=[f"news_{ticker}_{move_date.isoformat()}_h_001"],
        )
    return Attribution(
        ticker=ticker,
        move_date=move_date,
        return_pct=return_pct,
        predicted_return_pct=return_pct,
        **dims,
        move_character=move_character,
        confidence=0.8,
        ablation_name="frozen_anchor",
        sources_used=[SourceType.NEWS],
        chunks_considered=3,
    )


def _evidence_for(ticker: str, move_date: date) -> JoinedEvidence:
    chunk = TextChunk(
        chunk_id=f"news_{ticker}_{move_date.isoformat()}_h_001",
        ticker=ticker,
        source_type=SourceType.NEWS,
        publication_date=move_date,
        section_name="p0",
        text="Fake news text for frozen-anchor test.",
        token_count=10,
    )
    move = PriceMove(
        ticker=ticker,
        move_date=move_date,
        return_pct=-0.10,
        vol_zscore=-3.0,
        is_significant=True,
    )
    return JoinedEvidence(
        move=move,
        window_start=move_date,
        window_end=move_date,
        events=[],
        text_chunks=[chunk],
    )


def _tool_input_matching_expected(
    evidence: JoinedEvidence,
    *,
    dominant: str,
    direction: str,
    move_character: str,
) -> dict:
    chunk_id = evidence.text_chunks[0].chunk_id
    base = {
        name: {
            "weight": 0.7 if name == dominant else 0.075,
            "direction": direction if name == dominant else "neutral",
            "rationale": f"{name} rationale",
            "evidence_chunk_ids": [chunk_id],
        }
        for name in ("demand", "pricing", "competitive", "management_credibility", "macro")
    }
    return {
        **base,
        "move_character": move_character,
        "confidence": 0.8,
        "predicted_return_pct": evidence.move.return_pct,
        "model_notes": "test-only",
    }


class _StubClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("stub client: no more canned responses")
        return self._responses.pop(0)


def _tool_use_response(tool_input: dict):
    block = SimpleNamespace(type="tool_use", name=ATTRIBUTION_TOOL_NAME, input=tool_input)
    return SimpleNamespace(content=[block], stop_reason="tool_use")


# ---------- loader ----------


def test_load_frozen_cases_parses_all_entries():
    cases = load_frozen_cases()
    assert len(cases) >= 3
    case_ids = [c.case_id for c in cases]
    assert "AMD_2022-10-07" in case_ids
    assert "AMD_2023-05-25" in case_ids


def test_frozen_path_exists():
    assert FROZEN_PATH.exists(), "frozen_attributions.json must ship with the repo"


def test_frozen_file_has_schema_hint():
    raw = json.loads(FROZEN_PATH.read_text())
    assert "_schema" in raw and "cases" in raw


# ---------- diff ----------


def test_diff_case_passes_on_matching_attribution():
    cases = load_frozen_cases()
    case = next(c for c in cases if c.case_id == "AMD_2022-10-07")
    attribution = _attribution_for(
        "AMD", date(2022, 10, 7),
        dominant="demand", direction="negative",
        move_character="structural", return_pct=-0.135,
    )
    diff = diff_case(case, attribution)
    assert diff.passed is True
    assert diff.composite == pytest.approx(1.0)
    assert diff.score_result.dim_match is True


def test_diff_case_fails_on_wrong_dimension():
    cases = load_frozen_cases()
    case = next(c for c in cases if c.case_id == "AMD_2022-10-07")
    attribution = _attribution_for(
        "AMD", date(2022, 10, 7),
        dominant="macro", direction="negative",
        move_character="transient", return_pct=-0.135,
    )
    diff = diff_case(case, attribution)
    assert diff.passed is False
    assert diff.regressed is True
    assert diff.score_result.dim_match is False


# ---------- live runner (with fake client) ----------


def test_run_frozen_anchor_passes_with_canned_expected_responses():
    """Fake client returns the exact expected output for each case → all pass."""
    cases = load_frozen_cases()

    def provider(ticker: str, move_date: date) -> JoinedEvidence:
        return _evidence_for(ticker, move_date)

    # Build canned responses keyed by (ticker, date) in the order run_frozen_anchor
    # will process them (sorted by ticker then date; load_frozen_cases enforces this).
    canned = []
    for case in cases:
        exp = case.expected
        dominant = exp.dominant_dimension[0] if exp.dominant_dimension else "demand"
        canned.append(_tool_use_response(_tool_input_matching_expected(
            _evidence_for(case.ticker, case.move_date),
            dominant=dominant,
            direction=exp.direction or "neutral",
            move_character=exp.move_character or "mixed",
        )))

    client = _StubClient(canned)
    report = run_frozen_anchor(evidence_provider=provider, client=client)
    assert report.n_errored == 0
    assert report.n_regressed == 0
    assert report.n_passed == len(cases)
    # assert_no_regressions must not raise
    report.assert_no_regressions()


def test_run_frozen_anchor_surfaces_regressions():
    cases = load_frozen_cases()

    def provider(ticker: str, move_date: date) -> JoinedEvidence:
        return _evidence_for(ticker, move_date)

    # All responses pin move_character='unclear' and dominant=management_credibility —
    # guaranteed to fail every frozen case.
    canned = [
        _tool_use_response(_tool_input_matching_expected(
            _evidence_for(c.ticker, c.move_date),
            dominant="management_credibility",
            direction="neutral",
            move_character="unclear",
        ))
        for c in cases
    ]
    client = _StubClient(canned)
    report = run_frozen_anchor(evidence_provider=provider, client=client)
    assert report.n_regressed == len(cases)
    with pytest.raises(AssertionError, match="regressed"):
        report.assert_no_regressions()


def test_run_frozen_anchor_tolerates_per_case_errors():
    cases = load_frozen_cases()

    def provider(ticker: str, move_date: date) -> JoinedEvidence:
        # First case errors out, others succeed.
        if ticker == cases[0].ticker and move_date == cases[0].move_date:
            raise RuntimeError("simulated chunk provider failure")
        return _evidence_for(ticker, move_date)

    canned = []
    for case in cases[1:]:
        exp = case.expected
        dominant = exp.dominant_dimension[0] if exp.dominant_dimension else "demand"
        canned.append(_tool_use_response(_tool_input_matching_expected(
            _evidence_for(case.ticker, case.move_date),
            dominant=dominant,
            direction=exp.direction or "neutral",
            move_character=exp.move_character or "mixed",
        )))
    client = _StubClient(canned)
    report = run_frozen_anchor(evidence_provider=provider, client=client)
    assert report.n_errored == 1
    assert report.diffs[0].error is not None


# ---------- live API (gated) ----------


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_API") != "1",
    reason="live API test — set RUN_LIVE_API=1 and ANTHROPIC_API_KEY to run",
)
def test_live_frozen_anchor_smoke():
    """
    Manual-triage smoke test: build evidence from the bundled fixture for
    every frozen case and ensure the live runner produces a FrozenAnchorReport.
    This is not asserting all cases pass — the whole point of the anchor is to
    surface regressions. It only asserts the runner completes and every diff
    is a valid FrozenDiff.
    """
    fixture_path = Path(__file__).parent / "fixtures" / "events" / "joined_evidence_sample.json"
    with open(fixture_path) as f:
        raw = json.load(f)

    def provider(ticker: str, move_date: date) -> JoinedEvidence:
        # Minimal: reuse the AAPL sample but re-label for the case's ticker+date.
        ev = JoinedEvidence.model_validate(raw)
        ev.move.ticker = ticker
        ev.move.move_date = move_date
        for c in ev.text_chunks:
            c.ticker = ticker
        return ev

    report = run_frozen_anchor(
        evidence_provider=provider,
        options=FrozenRunnerOptions(threshold=0.50),
    )
    assert len(report.diffs) == len(load_frozen_cases())
    for d in report.diffs:
        assert isinstance(d, FrozenDiff)
