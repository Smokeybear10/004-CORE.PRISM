"""
Tests for `model.attribution.coherence.check_coherence`.

Mirrors the stub pattern in test_attribution.py: a hand-built client object
returns a canned `emit_coherence_check` tool_use response.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from model.attribution.coherence import (
    COHERENCE_TOOL_NAME,
    check_coherence,
)
from schema import Attribution, CoherenceCheck, DimensionScore, JoinedEvidence

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "events" / "joined_evidence_sample.json"
)


def _load_evidence() -> JoinedEvidence:
    with open(FIXTURE_PATH) as f:
        return JoinedEvidence.model_validate(json.load(f))


def _make_attribution(evidence: JoinedEvidence) -> Attribution:
    chunks = [c.chunk_id for c in evidence.text_chunks]
    assert chunks, "fixture expected to contain at least one text chunk"

    def _dim(weight: float, direction: str = "neutral") -> DimensionScore:
        return DimensionScore(
            weight=weight,
            direction=direction,
            rationale="fixture rationale",
            evidence_chunk_ids=[chunks[0]],
        )

    return Attribution(
        ticker=evidence.move.ticker,
        move_date=evidence.move.move_date,
        return_pct=evidence.move.return_pct,
        predicted_return_pct=-0.03,
        demand=_dim(0.2, "negative"),
        pricing=_dim(0.1),
        competitive=_dim(0.4, "negative"),
        management_credibility=_dim(0.2, "negative"),
        macro=_dim(0.1),
        move_character="mixed",
        confidence=0.7,
        ablation_name="test_ablation",
        sources_used=[],
        chunks_considered=len(evidence.text_chunks),
    )


def _stub_response(tool_input: dict):
    block = SimpleNamespace(type="tool_use", name=COHERENCE_TOOL_NAME, input=tool_input)
    return SimpleNamespace(content=[block], stop_reason="tool_use")


class _StubClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


# ---------- happy path ----------


def test_plausible_true_passes_through():
    evidence = _load_evidence()
    attr = _make_attribution(evidence)
    client = _StubClient([_stub_response({"plausible": True, "issues": []})])

    result = check_coherence(attr, evidence, client=client)

    assert isinstance(result, CoherenceCheck)
    assert result.plausible is True
    assert result.issues == []
    assert result.ticker == attr.ticker
    assert result.move_date == attr.move_date
    assert result.ablation_name == attr.ablation_name


def test_plausible_false_surfaces_issues():
    evidence = _load_evidence()
    attr = _make_attribution(evidence)
    issues = [
        "crude oil cited on a consumer-tech move",
        "demand weight dominates but no demand chunks in evidence",
    ]
    client = _StubClient(
        [_stub_response({"plausible": False, "issues": issues, "reviewer_notes": "see above"})]
    )

    result = check_coherence(attr, evidence, client=client)

    assert result.plausible is False
    assert result.issues == issues
    assert result.reviewer_notes == "see above"


def test_missing_reviewer_notes_is_optional():
    evidence = _load_evidence()
    attr = _make_attribution(evidence)
    client = _StubClient([_stub_response({"plausible": True, "issues": []})])

    result = check_coherence(attr, evidence, client=client)
    assert result.reviewer_notes is None


# ---------- request shape ----------


def test_request_uses_emit_coherence_check_tool():
    evidence = _load_evidence()
    attr = _make_attribution(evidence)
    client = _StubClient([_stub_response({"plausible": True, "issues": []})])

    check_coherence(attr, evidence, client=client)

    call = client.calls[0]
    assert call["model"] == "claude-haiku-4-5-20251001"
    assert call["tool_choice"] == {"type": "tool", "name": "emit_coherence_check"}
    assert call["tools"][0]["name"] == "emit_coherence_check"
    # Sampling params must not be sent
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in call


def test_system_prompt_has_cache_breakpoint():
    evidence = _load_evidence()
    attr = _make_attribution(evidence)
    client = _StubClient([_stub_response({"plausible": True, "issues": []})])

    check_coherence(attr, evidence, client=client)

    sys_block = client.calls[0]["system"][0]
    assert sys_block["cache_control"] == {"type": "ephemeral"}
    assert "plausibility" in sys_block["text"].lower()


def test_user_message_includes_attribution_and_chunks():
    evidence = _load_evidence()
    attr = _make_attribution(evidence)
    client = _StubClient([_stub_response({"plausible": True, "issues": []})])

    check_coherence(attr, evidence, client=client)

    user_text = client.calls[0]["messages"][0]["content"]
    assert attr.ticker in user_text
    assert attr.move_date.isoformat() in user_text
    assert "ATTRIBUTION" in user_text
    assert "AVAILABLE TEXT CHUNKS" in user_text
    # Reviewer should be able to see every cited chunk_id
    for chunk in evidence.text_chunks:
        assert chunk.chunk_id in user_text


# ---------- error handling ----------


def test_one_retry_on_transient_error():
    import anthropic

    evidence = _load_evidence()
    attr = _make_attribution(evidence)
    good = _stub_response({"plausible": True, "issues": []})

    call_log: list[int] = []

    def create(**kwargs):
        call_log.append(1)
        if len(call_log) == 1:
            raise anthropic.APIConnectionError(request=None)
        return good

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    result = check_coherence(attr, evidence, client=client)
    assert result.plausible is True
    assert len(call_log) == 2


def test_raises_on_missing_tool_use_block():
    evidence = _load_evidence()
    attr = _make_attribution(evidence)
    text_only = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="The attribution looks fine.")],
        stop_reason="end_turn",
    )
    client = _StubClient([text_only])

    with pytest.raises(RuntimeError, match="emit_coherence_check"):
        check_coherence(attr, evidence, client=client)


# ---------- live integration (gated) ----------


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_API") != "1",
    reason="live API test — set RUN_LIVE_API=1 and ANTHROPIC_API_KEY to run",
)
def test_live_api_coherence_on_aapl_fixture():
    evidence = _load_evidence()
    attr = _make_attribution(evidence)
    result = check_coherence(attr, evidence)
    assert isinstance(result, CoherenceCheck)
    # Plausibility can legitimately go either way depending on the model's
    # read of the fixture; just assert that the response parsed.
    assert result.ticker == attr.ticker
    assert isinstance(result.plausible, bool)
    assert isinstance(result.issues, list)
