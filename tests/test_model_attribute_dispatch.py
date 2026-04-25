"""
Tests for model.attribute() dispatch logic — the placeholder vs live-Claude
fork added when wiring eval to the real Anthropic API.

These tests do NOT hit Anthropic. They monkey-patch `run_attribution` so we
can verify the adapter (PriceMove + chunks + config -> JoinedEvidence) is
shaped correctly, the env-flag gate behaves as designed, and the contract
fields the eval consumer expects are pinned correctly.

For the actual end-to-end live test, see:
  RUN_LIVE_API=1 pytest tests/test_attribution.py::test_live_api_end_to_end_with_aapl_fixture
"""

from __future__ import annotations

from datetime import date

import pytest

import model as _model_pkg
from backtest import DEFAULT_ABLATIONS
from schema import (
    Attribution,
    DimensionScore,
    JoinedEvidence,
    PriceMove,
    SourceType,
    TextChunk,
)


# ── helpers ────────────────────────────────────────────────────────────────

def _move() -> PriceMove:
    return PriceMove(
        ticker="AAPL", move_date=date(2024, 5, 3),
        return_pct=-0.083, vol_zscore=-3.7, is_significant=True,
    )


def _chunk(chunk_id: str, ticker: str, day: date,
           src: SourceType = SourceType.NEWS, text: str = "x") -> TextChunk:
    return TextChunk(
        chunk_id=chunk_id, ticker=ticker, source_type=src,
        publication_date=day, text=text, token_count=1,
    )


def _stub_dim(chunk_ids: list[str]) -> DimensionScore:
    return DimensionScore(weight=0.2, direction="neutral",
                          rationale="stub", evidence_chunk_ids=chunk_ids)


def _stub_attribution(evidence: JoinedEvidence, ablation_name: str) -> Attribution:
    """A minimal valid Attribution to return from the patched run_attribution."""
    cids = [c.chunk_id for c in evidence.text_chunks][:3] or ["fake_0"]
    return Attribution(
        ticker=evidence.move.ticker,
        move_date=evidence.move.move_date,
        return_pct=evidence.move.return_pct,
        predicted_return_pct=-0.05,
        demand=_stub_dim(cids),
        pricing=_stub_dim(cids),
        competitive=_stub_dim(cids),
        management_credibility=_stub_dim(cids),
        macro=_stub_dim(cids),
        move_character="structural", confidence=0.8,
        ablation_name=ablation_name, sources_used=[SourceType.NEWS],
        chunks_considered=len(evidence.text_chunks),
    )


# ── env-flag gate ──────────────────────────────────────────────────────────

def test_default_path_does_not_call_run_attribution(monkeypatch):
    """RUN_LIVE_API unset (default) → placeholder runs, never imports run_attribution."""
    monkeypatch.delenv("RUN_LIVE_API", raising=False)
    called = {"n": 0}

    def fake_run_attribution(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("run_attribution should not be called when RUN_LIVE_API unset")

    monkeypatch.setattr("model.attribution.run_attribution", fake_run_attribution)

    chunks = [_chunk("news_AAPL_2024-05-02_article_001", "AAPL", date(2024, 5, 2))]
    attr = _model_pkg.attribute(_move(), chunks, DEFAULT_ABLATIONS[-1])

    assert called["n"] == 0
    assert isinstance(attr, Attribution)
    assert attr.ablation_name == DEFAULT_ABLATIONS[-1].name


def test_live_flag_with_empty_chunks_falls_back_to_placeholder(monkeypatch):
    """RUN_LIVE_API=1 but chunks=[] → still placeholder. No API waste, and
    the frozen-case test (which uses chunks=[]) keeps working."""
    monkeypatch.setenv("RUN_LIVE_API", "1")
    called = {"n": 0}

    def fake_run_attribution(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("run_attribution should not be called with empty chunks")

    monkeypatch.setattr("model.attribution.run_attribution", fake_run_attribution)

    attr = _model_pkg.attribute(_move(), chunks=[], config=DEFAULT_ABLATIONS[-1])

    assert called["n"] == 0
    assert isinstance(attr, Attribution)


def test_live_flag_with_only_future_chunks_falls_back(monkeypatch):
    """All chunks published AFTER move.move_date → no visible evidence after
    the foreknowledge filter → fall back to placeholder, no API call."""
    monkeypatch.setenv("RUN_LIVE_API", "1")
    called = {"n": 0}

    def fake_run_attribution(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("should not call API when no visible chunks remain")

    monkeypatch.setattr("model.attribution.run_attribution", fake_run_attribution)

    move = _move()
    future = [_chunk("news_AAPL_2099-01-01_article_001", "AAPL", date(2099, 1, 1))]
    attr = _model_pkg.attribute(move, future, DEFAULT_ABLATIONS[-1])

    assert called["n"] == 0
    assert isinstance(attr, Attribution)


# ── live path: adapter shape ────────────────────────────────────────────────

def test_live_path_passes_correct_joined_evidence(monkeypatch):
    """RUN_LIVE_API=1 + visible chunks → run_attribution called with a
    JoinedEvidence whose move/chunks/window are derived correctly."""
    monkeypatch.setenv("RUN_LIVE_API", "1")
    captured: dict = {}

    def fake_run_attribution(evidence, ablation_name, **kwargs):
        captured["evidence"] = evidence
        captured["ablation_name"] = ablation_name
        captured["kwargs"] = kwargs
        return _stub_attribution(evidence, ablation_name)

    monkeypatch.setattr("model.attribution.run_attribution", fake_run_attribution)

    move = _move()
    chunks = [
        _chunk("news_AAPL_2024-05-01_article_001", "AAPL", date(2024, 5, 1)),
        _chunk("news_AAPL_2024-05-02_article_001", "AAPL", date(2024, 5, 2)),
        _chunk("news_AAPL_2024-04-15_article_001", "AAPL", date(2024, 4, 15),
               src=SourceType.SEC_8K),
    ]
    cfg = DEFAULT_ABLATIONS[-1]  # +positioning
    _model_pkg.attribute(move, chunks, cfg)

    ev: JoinedEvidence = captured["evidence"]
    assert isinstance(ev, JoinedEvidence)
    assert ev.move == move
    assert len(ev.text_chunks) == 3      # all chunks before move_date
    assert ev.window_start == date(2024, 4, 15)  # earliest chunk
    assert ev.window_end == move.move_date
    assert ev.events == []                # eval flow has no idiosyncratic events
    assert captured["ablation_name"] == cfg.name


def test_live_path_filters_chunks_after_move_date(monkeypatch):
    """Defensive foreknowledge filter: chunks published AFTER move_date are
    dropped from the JoinedEvidence even though the contract says caller
    pre-filters."""
    monkeypatch.setenv("RUN_LIVE_API", "1")
    captured: dict = {}

    def fake_run_attribution(evidence, ablation_name, **kwargs):
        captured["evidence"] = evidence
        return _stub_attribution(evidence, ablation_name)

    monkeypatch.setattr("model.attribution.run_attribution", fake_run_attribution)

    move = _move()
    mixed = [
        _chunk("news_AAPL_2024-05-02_article_001", "AAPL", date(2024, 5, 2)),  # before
        _chunk("news_AAPL_2024-05-04_article_001", "AAPL", date(2024, 5, 4)),  # AFTER
    ]
    _model_pkg.attribute(move, mixed, DEFAULT_ABLATIONS[-1])

    ev = captured["evidence"]
    visible_dates = [c.publication_date for c in ev.text_chunks]
    assert date(2024, 5, 4) not in visible_dates
    assert visible_dates == [date(2024, 5, 2)]


def test_live_path_pins_contract_fields(monkeypatch):
    """sources_used and chunks_considered are pinned by model.attribute even
    if run_attribution returned different values."""
    monkeypatch.setenv("RUN_LIVE_API", "1")

    def fake_run_attribution(evidence, ablation_name, **kwargs):
        # Deliberately return WRONG values for sources_used/chunks_considered
        # to prove model.attribute overrides them.
        attr = _stub_attribution(evidence, ablation_name)
        attr.sources_used = []
        attr.chunks_considered = 999
        return attr

    monkeypatch.setattr("model.attribution.run_attribution", fake_run_attribution)

    chunks = [
        _chunk(f"news_AAPL_2024-05-02_article_{i:03d}", "AAPL", date(2024, 5, 2))
        for i in range(4)
    ]
    cfg = DEFAULT_ABLATIONS[-1]
    attr = _model_pkg.attribute(_move(), chunks, cfg)

    assert attr.sources_used == list(cfg.sources)
    assert attr.chunks_considered == 4


# ── public API surface ─────────────────────────────────────────────────────

def test_live_api_flag_constant_is_RUN_LIVE_API():
    """Lock the env var name so a refactor doesn't silently break the gate
    that test_attribution.py and test_eval_frozen.py also rely on."""
    assert _model_pkg.LIVE_API_FLAG == "RUN_LIVE_API"


def test_live_api_enabled_helper(monkeypatch):
    monkeypatch.delenv("RUN_LIVE_API", raising=False)
    assert _model_pkg._live_api_enabled() is False
    monkeypatch.setenv("RUN_LIVE_API", "1")
    assert _model_pkg._live_api_enabled() is True
    monkeypatch.setenv("RUN_LIVE_API", "0")
    assert _model_pkg._live_api_enabled() is False
    monkeypatch.setenv("RUN_LIVE_API", "true")  # only "1" counts
    assert _model_pkg._live_api_enabled() is False
