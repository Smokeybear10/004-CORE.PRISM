"""
Tests for `model.attribution.run.run_attribution`.

Uses `tests/fixtures/events/joined_evidence_sample.json` as input and mocks
the Anthropic client with a stub so no API key is required. One integration
test hits the real API; it is gated behind env var `RUN_LIVE_API=1`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from model.attribution import run_attribution, run_attribution_batch
from model.attribution.prompt import ATTRIBUTION_TOOL_NAME, SYSTEM_PROMPT
from model.attribution.validate import (
    AttributionValidationError,
    validate_attribution,
)
from schema import Attribution, JoinedEvidence

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "events" / "joined_evidence_sample.json"
)


# ---------- helpers ----------


def _load_evidence() -> JoinedEvidence:
    with open(FIXTURE_PATH) as f:
        return JoinedEvidence.model_validate(json.load(f))


def _make_stub_response(tool_input: dict, *, tool_name: str = ATTRIBUTION_TOOL_NAME):
    """Mimic the Anthropic SDK shape: a response object whose .content is a
    list of content blocks. Only `type`, `name`, `input` are read."""
    block = SimpleNamespace(type="tool_use", name=tool_name, input=tool_input)
    return SimpleNamespace(content=[block], stop_reason="tool_use")


class _StubClient:
    """Stand-in for `anthropic.Anthropic`. Records every `.messages.create` call."""

    def __init__(self, responses):
        # `responses` is a list; each call pops the first item.
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("stub client: no more canned responses")
        return self._responses.pop(0)


def _valid_tool_input(evidence: JoinedEvidence) -> dict:
    """A complete `emit_attribution` tool input whose citations resolve to the
    fixture's chunks and whose weights sum to 1.0."""
    chunks = [c.chunk_id for c in evidence.text_chunks]
    assert len(chunks) >= 2, "fixture expected to provide >= 2 chunks for these tests"
    first, second = chunks[0], chunks[1]
    return {
        "demand": {
            "weight": 0.15,
            "direction": "negative",
            "rationale": "Greater China unit softness mentioned on the call.",
            "evidence_chunk_ids": [first],
        },
        "pricing": {
            "weight": 0.05,
            "direction": "neutral",
            "rationale": "No pricing commentary in the evidence.",
            "evidence_chunk_ids": [first],
        },
        "competitive": {
            "weight": 0.55,
            "direction": "negative",
            "rationale": "Huawei resurgence flagged by analysts as the main disappointment.",
            "evidence_chunk_ids": [second, first],
        },
        "management_credibility": {
            "weight": 0.15,
            "direction": "negative",
            "rationale": "Flat-revenue Q2 guidance was softer than the Street expected.",
            "evidence_chunk_ids": [first],
        },
        "macro": {
            "weight": 0.10,
            "direction": "negative",
            "rationale": "FX headwind called out on the earnings call.",
            "evidence_chunk_ids": [first],
        },
        "move_character": "mixed",
        "confidence": 0.7,
        "predicted_return_pct": -0.03,
        "model_notes": "Competitive pressure is the dominant signal.",
    }


# ---------- happy path ----------


def test_returns_attribution_with_autofilled_identifiers():
    evidence = _load_evidence()
    client = _StubClient([_make_stub_response(_valid_tool_input(evidence))])

    attr = run_attribution(evidence, ablation_name="full_stack", client=client)

    assert isinstance(attr, Attribution)
    assert attr.ticker == evidence.move.ticker
    assert attr.move_date == evidence.move.move_date
    assert attr.return_pct == evidence.move.return_pct
    assert attr.ablation_name == "full_stack"
    assert attr.chunks_considered == len(evidence.text_chunks)
    # sources_used populated from the chunks in the evidence
    assert set(s.value for s in attr.sources_used) == {
        c.source_type.value for c in evidence.text_chunks
    }


def test_all_citations_resolve_to_real_chunks():
    evidence = _load_evidence()
    client = _StubClient([_make_stub_response(_valid_tool_input(evidence))])

    attr = run_attribution(evidence, client=client)

    valid_ids = {c.chunk_id for c in evidence.text_chunks}
    for name in ("demand", "pricing", "competitive", "management_credibility", "macro"):
        dim = getattr(attr, name)
        assert dim.evidence_chunk_ids
        for cid in dim.evidence_chunk_ids:
            assert cid in valid_ids


def test_validation_passes_on_wellformed_output():
    evidence = _load_evidence()
    client = _StubClient([_make_stub_response(_valid_tool_input(evidence))])
    attr = run_attribution(evidence, client=client)
    assert validate_attribution(attr, evidence) == []


# ---------- request shape ----------


def test_request_uses_correct_model_and_forces_tool_choice():
    evidence = _load_evidence()
    client = _StubClient([_make_stub_response(_valid_tool_input(evidence))])

    run_attribution(evidence, client=client)

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "claude-opus-4-7"
    assert call["max_tokens"] == 2048
    assert call["tool_choice"] == {"type": "tool", "name": "emit_attribution"}
    # Sampling parameters must NOT be present (Opus 4.7 rejects them)
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in call, f"{banned} must not be sent to Opus 4.7"


def test_system_prompt_has_cache_control_breakpoint():
    evidence = _load_evidence()
    client = _StubClient([_make_stub_response(_valid_tool_input(evidence))])

    run_attribution(evidence, client=client)

    call = client.calls[0]
    assert isinstance(call["system"], list) and len(call["system"]) == 1
    sys_block = call["system"][0]
    assert sys_block["text"] == SYSTEM_PROMPT
    # cache_control on the last system block caches tools + system together
    assert sys_block["cache_control"] == {"type": "ephemeral"}


def test_user_message_includes_every_chunk_id():
    evidence = _load_evidence()
    client = _StubClient([_make_stub_response(_valid_tool_input(evidence))])

    run_attribution(evidence, client=client)

    call = client.calls[0]
    user_text = call["messages"][0]["content"]
    for chunk in evidence.text_chunks:
        assert chunk.chunk_id in user_text, f"chunk {chunk.chunk_id} missing from user turn"


def test_user_message_contains_citation_rules():
    evidence = _load_evidence()
    client = _StubClient([_make_stub_response(_valid_tool_input(evidence))])
    run_attribution(evidence, client=client)
    user_text = client.calls[0]["messages"][0]["content"]
    assert "TEXT CHUNKS" in user_text
    assert "EVENTS" in user_text


# ---------- validation ----------


def test_runner_raises_on_hallucinated_chunk_id():
    evidence = _load_evidence()
    bad = _valid_tool_input(evidence)
    bad["demand"]["evidence_chunk_ids"] = ["not_a_real_chunk"]
    client = _StubClient([_make_stub_response(bad)])

    with pytest.raises(AttributionValidationError) as exc:
        run_attribution(evidence, client=client)
    assert any("hallucinated" in i for i in exc.value.issues)


def test_runner_raises_on_weights_not_summing_to_one():
    evidence = _load_evidence()
    bad = _valid_tool_input(evidence)
    bad["demand"]["weight"] = 0.9
    bad["competitive"]["weight"] = 0.9
    client = _StubClient([_make_stub_response(bad)])

    with pytest.raises(AttributionValidationError) as exc:
        run_attribution(evidence, client=client)
    assert any("weights sum" in i for i in exc.value.issues)


def test_validate_false_skips_validation():
    """validate=False lets bad output through so callers can inspect and decide."""
    evidence = _load_evidence()
    bad = _valid_tool_input(evidence)
    bad["demand"]["evidence_chunk_ids"] = ["not_a_real_chunk"]
    client = _StubClient([_make_stub_response(bad)])

    attr = run_attribution(evidence, client=client, validate=False)
    issues = validate_attribution(attr, evidence)
    assert issues, "validator should still find issues when called explicitly"


# ---------- error handling ----------


def test_one_retry_on_transient_error():
    import anthropic

    evidence = _load_evidence()
    good = _make_stub_response(_valid_tool_input(evidence))

    call_log: list[int] = []

    def create(**kwargs):
        call_log.append(1)
        if len(call_log) == 1:
            raise anthropic.APIConnectionError(request=None)
        return good

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    attr = run_attribution(evidence, client=client)
    assert isinstance(attr, Attribution)
    assert len(call_log) == 2  # one failure + one retry


def test_raises_after_retry_exhausted():
    import anthropic

    evidence = _load_evidence()

    def create(**kwargs):
        raise anthropic.APIConnectionError(request=None)

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    with pytest.raises(anthropic.APIConnectionError):
        run_attribution(evidence, client=client)


def test_raises_on_missing_tool_use_block():
    """If the model returned text instead of a tool_use block, runner must
    not silently swallow it."""
    evidence = _load_evidence()
    text_block = SimpleNamespace(type="text", text="I can't answer that.")
    no_tool = SimpleNamespace(content=[text_block], stop_reason="end_turn")
    client = _StubClient([no_tool])

    with pytest.raises(RuntimeError, match="tool_use"):
        run_attribution(evidence, client=client)


# ---------- batch ----------


def test_batch_preserves_order_and_runs_concurrently():
    evidence = _load_evidence()
    n = 5
    responses = [
        _make_stub_response({**_valid_tool_input(evidence), "confidence": 0.1 * (i + 1)})
        for i in range(n)
    ]
    client = _StubClient(responses)

    results = run_attribution_batch(
        [evidence] * n, ablation_name="batch_test", client=client, concurrency=2
    )

    assert len(results) == n
    # Each call lands exactly once; order is preserved by ThreadPoolExecutor.map
    assert [r.ablation_name for r in results] == ["batch_test"] * n


# ---------- live integration (gated) ----------


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_API") != "1",
    reason="live API test — set RUN_LIVE_API=1 and ANTHROPIC_API_KEY to run",
)
def test_live_api_end_to_end_with_aapl_fixture():
    """Actual Anthropic API round-trip using the AAPL fixture.

    Not run by default — CI and tooling that lacks an API key should stay green.
    """
    evidence = _load_evidence()
    attr = run_attribution(evidence, ablation_name="live_smoke")

    assert attr.ticker == "AAPL"
    assert attr.move_date.isoformat() == "2024-02-02"
    # Validator must pass — if the live model produced a bad citation we want
    # to fail loudly, not skip.
    issues = validate_attribution(attr, evidence)
    assert issues == [], f"live attribution failed validation: {issues}"
