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


# ---------- Rate-limit retry ----------


def _rate_limit_error(retry_after: str | None = None):
    """Construct a RateLimitError without going through the SDK's strict init."""
    import anthropic

    err = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
    Exception.__init__(err, "stub rate limit")
    if retry_after is not None:
        from types import SimpleNamespace
        err.response = SimpleNamespace(headers={"retry-after": retry_after})
    else:
        err.response = None
    return err


def test_rate_limit_retried_then_succeeds(monkeypatch):
    """A single 429 followed by success → retry, return live result."""
    monkeypatch.setenv(model.LIVE_ENV_VAR, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-doesnotmatter")

    sleeps: list[float] = []
    monkeypatch.setattr(model.time, "sleep", lambda s: sleeps.append(s))

    call_count = {"n": 0}

    def stub_run_attribution(evidence, ablation_name="full", **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _rate_limit_error()
        # second call: return a hand-built Attribution
        cid = evidence.text_chunks[0].chunk_id
        dim = lambda w: DimensionScore(
            weight=w, direction="negative", rationale="stub",
            evidence_chunk_ids=[cid],
        )
        return Attribution(
            ticker=evidence.move.ticker,
            move_date=evidence.move.move_date,
            return_pct=evidence.move.return_pct,
            predicted_return_pct=evidence.move.return_pct,
            demand=dim(0.6), pricing=dim(0.10), competitive=dim(0.10),
            management_credibility=dim(0.10), macro=dim(0.10),
            move_character="structural", confidence=0.85,
            ablation_name=ablation_name, sources_used=[SourceType.NEWS],
            chunks_considered=len(evidence.text_chunks),
        )

    import model.attribution as ma
    monkeypatch.setattr(ma, "run_attribution", stub_run_attribution)

    attr = model.attribute(_move(), _chunks(), _config())
    assert attr.model_notes.startswith(model.LIVE_NOTE_PREFIX)
    assert call_count["n"] == 2
    assert sleeps and sleeps[0] >= model.RATE_LIMIT_BASE_DELAY_S


def test_rate_limit_exhausted_falls_back_to_placeholder(monkeypatch):
    """Every attempt 429s → falls back with honest model_notes."""
    monkeypatch.setenv(model.LIVE_ENV_VAR, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-doesnotmatter")

    sleeps: list[float] = []
    monkeypatch.setattr(model.time, "sleep", lambda s: sleeps.append(s))

    call_count = {"n": 0}

    def always_429(evidence, ablation_name="full", **kwargs):
        call_count["n"] += 1
        raise _rate_limit_error()

    import model.attribution as ma
    monkeypatch.setattr(ma, "run_attribution", always_429)

    attr = model.attribute(_move(), _chunks(), _config())
    # All attempts exhausted; placeholder fired.
    assert attr.model_notes.startswith(model.PLACEHOLDER_NOTE_PREFIX)
    assert "RateLimitError" in attr.model_notes
    # MAX_RETRIES + 1 attempts total = 3 calls, 2 sleeps between them.
    assert call_count["n"] == model.RATE_LIMIT_MAX_RETRIES + 1
    assert len(sleeps) == model.RATE_LIMIT_MAX_RETRIES


def test_retry_delay_honors_retry_after_header():
    err = _rate_limit_error(retry_after="7")
    assert model._retry_delay_for(err, attempt=0) == 7.0


def test_retry_delay_caps_retry_after_at_max():
    err = _rate_limit_error(retry_after=str(int(model.RATE_LIMIT_MAX_DELAY_S * 10)))
    assert model._retry_delay_for(err, attempt=0) == model.RATE_LIMIT_MAX_DELAY_S


def test_retry_delay_falls_back_to_exponential_backoff():
    err = _rate_limit_error(retry_after=None)
    d0 = model._retry_delay_for(err, attempt=0)
    d1 = model._retry_delay_for(err, attempt=1)
    assert d0 == model.RATE_LIMIT_BASE_DELAY_S
    assert d1 == min(model.RATE_LIMIT_BASE_DELAY_S * 2, model.RATE_LIMIT_MAX_DELAY_S)


def test_non_rate_limit_errors_do_not_retry(monkeypatch):
    """Random RuntimeError must NOT be retried (only 429s are)."""
    monkeypatch.setenv(model.LIVE_ENV_VAR, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-doesnotmatter")

    sleeps: list[float] = []
    monkeypatch.setattr(model.time, "sleep", lambda s: sleeps.append(s))

    call_count = {"n": 0}

    def boom(evidence, ablation_name="full", **kwargs):
        call_count["n"] += 1
        raise RuntimeError("unrelated failure")

    import model.attribution as ma
    monkeypatch.setattr(ma, "run_attribution", boom)

    attr = model.attribute(_move(), _chunks(), _config())
    assert attr.model_notes.startswith(model.PLACEHOLDER_NOTE_PREFIX)
    assert call_count["n"] == 1
    assert sleeps == []


# ---------- Hallucinated-chunk sanitizer ----------


def _evidence_for(move, chunks):
    """Bridge helper: build a JoinedEvidence consistent with model._build_evidence."""
    return model._build_evidence(move, chunks)


def _attr_with_citations(citations: dict[str, list[str]]):
    """Hand-build an Attribution where each dim cites whatever IDs the test wants
    (real or hallucinated)."""
    move = _move()
    dim = lambda cids: DimensionScore(
        weight=0.2, direction="negative",
        rationale="stub", evidence_chunk_ids=list(cids),
    )
    return Attribution(
        ticker=move.ticker,
        move_date=move.move_date,
        return_pct=move.return_pct,
        predicted_return_pct=move.return_pct,
        demand=dim(citations.get("demand", [])),
        pricing=dim(citations.get("pricing", [])),
        competitive=dim(citations.get("competitive", [])),
        management_credibility=dim(citations.get("management_credibility", [])),
        macro=dim(citations.get("macro", [])),
        move_character="structural", confidence=0.8,
        ablation_name="test", sources_used=[SourceType.NEWS],
        chunks_considered=2,
    )


def test_sanitize_drops_hallucinated_ids_and_keeps_real_ones():
    chunks = _chunks(2)
    real_ids = [c.chunk_id for c in chunks]
    evidence = _evidence_for(_move(), chunks)
    attr = _attr_with_citations({
        "demand":                  [real_ids[0], "sec_8k_AMD_2025_fake_003"],
        "pricing":                 [real_ids[1]],
        "competitive":             ["sec_8k_AMD_2025_fake_004", real_ids[0]],
        "management_credibility":  ["completely_made_up"],
        "macro":                   real_ids,
    })
    dropped = model._sanitize_chunk_citations(attr, evidence)
    assert dropped == 3  # 1 + 0 + 1 + 1 + 0
    assert attr.demand.evidence_chunk_ids == [real_ids[0]]
    assert attr.pricing.evidence_chunk_ids == [real_ids[1]]
    assert attr.competitive.evidence_chunk_ids == [real_ids[0]]
    # management_credibility had only fake — should fall back to first real chunk
    assert attr.management_credibility.evidence_chunk_ids == [real_ids[0]]
    assert attr.macro.evidence_chunk_ids == real_ids


def test_sanitize_returns_zero_when_all_ids_real():
    chunks = _chunks(2)
    real_ids = [c.chunk_id for c in chunks]
    evidence = _evidence_for(_move(), chunks)
    attr = _attr_with_citations({name: [real_ids[0]] for name in (
        "demand","pricing","competitive","management_credibility","macro"
    )})
    dropped = model._sanitize_chunk_citations(attr, evidence)
    assert dropped == 0


def test_sanitize_handles_zero_chunks_gracefully():
    """If evidence is empty there's no fallback — preserve current (empty) IDs
    rather than crash."""
    move = _move()
    evidence = _evidence_for(move, [])
    attr = _attr_with_citations({"demand": ["fake_id"]})
    dropped = model._sanitize_chunk_citations(attr, evidence)
    assert dropped == 1
    # All hallucinated stripped, no fallback available -> empty list
    assert attr.demand.evidence_chunk_ids == []


def test_live_path_runs_sanitizer_and_notes_dropped(monkeypatch):
    """End-to-end: live runner returns hallucinated IDs; bridge scrubs them
    and appends a count to model_notes."""
    monkeypatch.setenv(model.LIVE_ENV_VAR, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-doesnotmatter")

    chunks_in = _chunks(2)
    real_ids = [c.chunk_id for c in chunks_in]

    def stub_run_attribution(evidence, ablation_name="full", **kwargs):
        # Model invents 2 hallucinated IDs across the 5 dims.
        dim = lambda cids: DimensionScore(
            weight=0.2, direction="negative", rationale="stub",
            evidence_chunk_ids=list(cids),
        )
        return Attribution(
            ticker=evidence.move.ticker,
            move_date=evidence.move.move_date,
            return_pct=evidence.move.return_pct,
            predicted_return_pct=evidence.move.return_pct,
            demand=dim([real_ids[0], "fake_id_001"]),  # 1 hallucinated
            pricing=dim([real_ids[1]]),                # clean
            competitive=dim([real_ids[0]]),            # clean
            management_credibility=dim(["fake_id_002"]),  # 1 hallucinated -> falls back
            macro=dim([real_ids[1]]),                  # clean
            move_character="structural", confidence=0.8,
            ablation_name=ablation_name,
            sources_used=[SourceType.NEWS],
            chunks_considered=len(evidence.text_chunks),
        )

    import model.attribution as ma
    monkeypatch.setattr(ma, "run_attribution", stub_run_attribution)

    attr = model.attribute(_move(), chunks_in, _config())
    # Live path tagged
    assert attr.model_notes.startswith(model.LIVE_NOTE_PREFIX)
    # Sanitizer note appended
    assert "scrubbed 2 hallucinated chunk_id" in attr.model_notes
    # No dim has invalid citations anymore
    for name in ("demand","pricing","competitive","management_credibility","macro"):
        for cid in getattr(attr, name).evidence_chunk_ids:
            assert cid in real_ids
    # The fully-hallucinated dim got the fallback
    assert attr.management_credibility.evidence_chunk_ids == [real_ids[0]]


def test_live_path_no_notes_when_nothing_dropped(monkeypatch):
    """If the model returns clean citations, model_notes shouldn't accuse the
    model of hallucinating."""
    monkeypatch.setenv(model.LIVE_ENV_VAR, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-doesnotmatter")

    chunks_in = _chunks(2)
    real_ids = [c.chunk_id for c in chunks_in]

    def clean_run(evidence, ablation_name="full", **kwargs):
        dim = lambda: DimensionScore(
            weight=0.2, direction="negative", rationale="stub",
            evidence_chunk_ids=[real_ids[0]],
        )
        return Attribution(
            ticker=evidence.move.ticker,
            move_date=evidence.move.move_date,
            return_pct=evidence.move.return_pct,
            predicted_return_pct=evidence.move.return_pct,
            demand=dim(), pricing=dim(), competitive=dim(),
            management_credibility=dim(), macro=dim(),
            move_character="structural", confidence=0.8,
            ablation_name=ablation_name,
            sources_used=[SourceType.NEWS],
            chunks_considered=len(evidence.text_chunks),
        )

    import model.attribution as ma
    monkeypatch.setattr(ma, "run_attribution", clean_run)

    attr = model.attribute(_move(), chunks_in, _config())
    assert "hallucinated" not in (attr.model_notes or "")
