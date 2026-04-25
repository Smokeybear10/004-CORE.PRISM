"""
Tests for the LIVE/placeholder bridge in model.attribute().

The bridge:
    1. Returns the synthetic placeholder when BW_USE_LIVE_ATTRIBUTION!=1
       (default test environment).
    2. Returns the synthetic placeholder when ANTHROPIC_API_KEY is missing.
    3. Returns the synthetic placeholder when chunks is empty.
    4. Calls model.attribution.run_attribution when LIVE mode is enabled and
       chunks are present, propagates the result, and tags model_notes with
       a `live attribution` prefix.
    5. Falls back to the placeholder (with an honest model_notes tag) when
       run_attribution raises.

No test in this file actually hits the Anthropic API — the live path is
exercised through monkeypatching `model._live_attribute` so the bridge logic
is what's under test, not the network layer.
"""
from __future__ import annotations

from datetime import date

import pytest

import model
from schema import (
    AblationConfig,
    Attribution,
    DimensionScore,
    PriceMove,
    SourceType,
    TextChunk,
)


# ---------- helpers ----------


def _move() -> PriceMove:
    return PriceMove(
        ticker="AMD",
        move_date=date(2024, 1, 1),
        return_pct=-0.05,
        vol_zscore=-2.0,
        is_significant=True,
    )


def _chunks(n: int = 2) -> list[TextChunk]:
    return [
        TextChunk(
            chunk_id=f"news_AMD_2024-01-01_h_{i:03d}",
            ticker="AMD",
            source_type=SourceType.NEWS,
            publication_date=date(2024, 1, 1),
            section_name="p0",
            text="placeholder text",
            token_count=5,
        )
        for i in range(n)
    ]


def _config() -> AblationConfig:
    return AblationConfig(name="base_news", sources=[SourceType.NEWS])


def _stub_live_attribution(move: PriceMove, chunks: list[TextChunk],
                           cfg: AblationConfig) -> Attribution:
    """A stand-in for model._live_attribute that returns a deterministic
    Attribution carrying a clear 'live attribution' tag. Used to verify the
    bridge dispatches to live without hitting the real API."""
    cid = chunks[0].chunk_id
    dim = lambda w, d="negative": DimensionScore(  # noqa: E731
        weight=w, direction=d, rationale="stub-live", evidence_chunk_ids=[cid],
    )
    return Attribution(
        ticker=move.ticker,
        move_date=move.move_date,
        return_pct=move.return_pct,
        predicted_return_pct=move.return_pct,
        demand=dim(0.6),
        pricing=dim(0.10),
        competitive=dim(0.10),
        management_credibility=dim(0.10),
        macro=dim(0.10),
        move_character="structural",
        confidence=0.9,
        ablation_name=cfg.name,
        sources_used=list(cfg.sources),
        chunks_considered=len(chunks),
        model_notes="live attribution: stub",
    )


# ---------- Gate behavior ----------


def test_gate_off_when_env_unset(monkeypatch):
    monkeypatch.delenv(model.LIVE_ENV_VAR, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-doesnotmatter")
    use_live, reason = model._should_use_live(_chunks())
    assert use_live is False
    assert "BW_USE_LIVE_ATTRIBUTION" in (reason or "")


def test_gate_off_when_env_not_one(monkeypatch):
    monkeypatch.setenv(model.LIVE_ENV_VAR, "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-doesnotmatter")
    use_live, reason = model._should_use_live(_chunks())
    assert use_live is False


def test_gate_off_when_api_key_missing(monkeypatch):
    monkeypatch.setenv(model.LIVE_ENV_VAR, "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    use_live, reason = model._should_use_live(_chunks())
    assert use_live is False
    assert "ANTHROPIC_API_KEY" in (reason or "")


def test_gate_off_when_chunks_empty(monkeypatch):
    monkeypatch.setenv(model.LIVE_ENV_VAR, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-doesnotmatter")
    use_live, reason = model._should_use_live([])
    assert use_live is False
    assert "no chunks" in (reason or "")


def test_gate_on_when_all_three_satisfied(monkeypatch):
    monkeypatch.setenv(model.LIVE_ENV_VAR, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-doesnotmatter")
    use_live, reason = model._should_use_live(_chunks())
    assert use_live is True
    assert reason is None


# ---------- Default (LIVE off) → placeholder ----------


def test_default_returns_placeholder_with_skip_reason(monkeypatch):
    monkeypatch.delenv(model.LIVE_ENV_VAR, raising=False)
    attr = model.attribute(_move(), _chunks(), _config())
    assert isinstance(attr, Attribution)
    assert attr.model_notes is not None
    assert attr.model_notes.startswith(model.PLACEHOLDER_NOTE_PREFIX)
    assert "BW_USE_LIVE_ATTRIBUTION" in attr.model_notes
    # Contract fields the demo / runner depend on
    assert attr.ablation_name == "base_news"
    assert attr.chunks_considered == 2


def test_empty_chunks_returns_placeholder_even_with_live_on(monkeypatch):
    monkeypatch.setenv(model.LIVE_ENV_VAR, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-doesnotmatter")
    attr = model.attribute(_move(), [], _config())
    assert attr.model_notes.startswith(model.PLACEHOLDER_NOTE_PREFIX)
    assert "no chunks" in attr.model_notes


# ---------- LIVE on → run_attribution dispatched ----------


def test_live_on_dispatches_to_live_attribute(monkeypatch):
    monkeypatch.setenv(model.LIVE_ENV_VAR, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-doesnotmatter")
    monkeypatch.setattr(model, "_live_attribute", _stub_live_attribution)
    attr = model.attribute(_move(), _chunks(), _config())
    assert attr.model_notes.startswith(model.LIVE_NOTE_PREFIX)
    assert attr.move_character == "structural"
    assert attr.confidence == pytest.approx(0.9)


def test_live_failure_falls_back_to_placeholder_with_note(monkeypatch):
    monkeypatch.setenv(model.LIVE_ENV_VAR, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-doesnotmatter")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated transient API error")

    monkeypatch.setattr(model, "_live_attribute", boom)
    attr = model.attribute(_move(), _chunks(), _config())
    assert attr.model_notes.startswith(model.PLACEHOLDER_NOTE_PREFIX)
    assert "live call failed" in attr.model_notes
    assert "RuntimeError" in attr.model_notes


# ---------- _build_evidence ----------


def test_build_evidence_caps_window_at_move_date():
    move = _move()
    # One chunk with a publication_date AFTER move_date — should be capped.
    chunks = [
        TextChunk(
            chunk_id="news_AMD_2024-02-01_h_001",
            ticker="AMD",
            source_type=SourceType.NEWS,
            publication_date=date(2024, 2, 1),  # after move_date
            text="future",
            token_count=2,
        ),
        TextChunk(
            chunk_id="news_AMD_2023-12-15_h_001",
            ticker="AMD",
            source_type=SourceType.NEWS,
            publication_date=date(2023, 12, 15),
            text="past",
            token_count=2,
        ),
    ]
    evidence = model._build_evidence(move, chunks)
    assert evidence.window_start == date(2023, 12, 15)
    assert evidence.window_end == move.move_date  # capped, not 2024-02-01
    assert evidence.events == []
    assert len(evidence.text_chunks) == 2


def test_build_evidence_with_empty_chunks_uses_move_date():
    move = _move()
    evidence = model._build_evidence(move, [])
    assert evidence.window_start == move.move_date
    assert evidence.window_end == move.move_date
    assert evidence.text_chunks == []
