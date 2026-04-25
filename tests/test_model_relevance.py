"""
Tests for the pre-LLM filter + weighting layer in model.relevance.

Covers:
  - source_quality with publisher overlay
  - recency_decay (half-life semantics, future-dated guard)
  - ticker_alignment (exact, peer, sector, macro, off-ticker)
  - score ordering: SEC_10K > same-ticker news > peer news > old news
  - filter_and_rank: keep_fraction, max_chunks, min_chunks, min_score
  - annotate_with_weights: tier mapping + chunk_id preservation
  - BW_DISABLE_CHUNK_FILTER env var bypass
  - Integration: model.attribute() applies the filter and tags model_notes
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

import model
from model.relevance import (
    DEFAULT_FILTER,
    DISABLE_ENV_VAR,
    FilterConfig,
    HALF_LIFE_DAYS,
    annotate_with_weights,
    filter_and_rank,
    recency_decay,
    score_chunk,
    score_chunks,
    source_quality,
    ticker_alignment,
)
from schema import (
    AblationConfig,
    PriceMove,
    SourceType,
    TextChunk,
)


# ---------- Helpers ----------

def _move(ticker: str = "AMD", d: date = date(2024, 1, 10)) -> PriceMove:
    return PriceMove(
        ticker=ticker, move_date=d,
        return_pct=-0.05, vol_zscore=-2.0, is_significant=True,
    )


def _chunk(
    *,
    chunk_id: str = "c_000",
    ticker: str = "AMD",
    source_type: SourceType = SourceType.NEWS,
    publication_date: date = date(2024, 1, 10),
    publisher: str | None = "Reuters",
    text: str = "body text",
) -> TextChunk:
    return TextChunk(
        chunk_id=chunk_id,
        ticker=ticker,
        source_type=source_type,
        publication_date=publication_date,
        section_name=publisher,
        text=text,
        token_count=len(text.split()),
    )


# ---------- source_quality ----------

def test_source_quality_10k_outranks_news():
    q_10k = source_quality(_chunk(source_type=SourceType.SEC_10K, publisher=None))
    q_news = source_quality(_chunk(source_type=SourceType.NEWS, publisher="Reuters"))
    assert q_10k > q_news


def test_source_quality_reuters_beats_seeking_alpha():
    q_reuters = source_quality(_chunk(publisher="Reuters"))
    q_sa = source_quality(_chunk(publisher="Seeking Alpha"))
    assert q_reuters > q_sa


def test_source_quality_unknown_publisher_is_penalized():
    q_known = source_quality(_chunk(publisher="Reuters"))
    q_unknown = source_quality(_chunk(publisher="MysteryBlog 2023"))
    assert q_unknown < q_known


def test_source_quality_non_news_ignores_publisher():
    """Publisher overlay only applies to news-family chunks."""
    q_10k_blog = source_quality(_chunk(
        source_type=SourceType.SEC_10K, publisher="Seeking Alpha",
    ))
    q_10k_reuters = source_quality(_chunk(
        source_type=SourceType.SEC_10K, publisher="Reuters",
    ))
    assert q_10k_blog == q_10k_reuters


# ---------- recency_decay ----------

def test_recency_same_day_is_one():
    c = _chunk(publication_date=date(2024, 1, 10))
    assert recency_decay(c, date(2024, 1, 10)) == pytest.approx(1.0)


def test_recency_half_life_news():
    """News half-life is 7 days, so a chunk 7 days old should score ~0.5."""
    c = _chunk(source_type=SourceType.NEWS, publication_date=date(2024, 1, 3))
    assert recency_decay(c, date(2024, 1, 10)) == pytest.approx(0.5, abs=0.02)


def test_recency_sec_10k_has_longer_memory():
    """A 30-day-old SEC 10K should score MUCH higher than a 30-day-old news
    article thanks to the longer half-life."""
    move_d = date(2024, 6, 1)
    old = date(2024, 5, 2)  # 30 days earlier
    news_score = recency_decay(
        _chunk(source_type=SourceType.NEWS, publication_date=old), move_d,
    )
    sec_score = recency_decay(
        _chunk(source_type=SourceType.SEC_10K, publication_date=old), move_d,
    )
    assert sec_score > news_score + 0.3


def test_recency_future_dated_returns_zero():
    """Foreknowledge guard."""
    c = _chunk(publication_date=date(2024, 6, 1))
    assert recency_decay(c, date(2024, 1, 1)) == 0.0


# ---------- ticker_alignment ----------

def test_alignment_exact_ticker():
    assert ticker_alignment(_chunk(ticker="AMD"), "AMD") == 1.0


def test_alignment_peer_news_partial():
    c = _chunk(ticker="NVDA", source_type=SourceType.PEER_NEWS)
    assert 0.5 <= ticker_alignment(c, "AMD") < 1.0


def test_alignment_macro_distinct():
    c = _chunk(ticker="_MACRO", source_type=SourceType.MACRO)
    assert ticker_alignment(c, "AMD") == pytest.approx(0.50)


def test_alignment_off_ticker_news_lowest():
    c = _chunk(ticker="XOM", source_type=SourceType.NEWS)
    assert ticker_alignment(c, "AMD") < ticker_alignment(
        _chunk(ticker="XOM", source_type=SourceType.PEER_NEWS), "AMD",
    )


# ---------- score_chunks ordering ----------

def test_score_ordering_ten_k_beats_stale_news():
    move = _move()
    chunks = [
        _chunk(
            chunk_id="sec_10k",
            source_type=SourceType.SEC_10K,
            publication_date=move.move_date - timedelta(days=60),
            publisher=None,
        ),
        _chunk(
            chunk_id="old_blog",
            source_type=SourceType.NEWS,
            publication_date=move.move_date - timedelta(days=30),
            publisher="Seeking Alpha",
        ),
    ]
    ranked = score_chunks(chunks, move)
    assert [c.chunk_id for c, _ in ranked] == ["sec_10k", "old_blog"]


def test_score_ordering_same_day_reuters_beats_week_old_blog():
    move = _move()
    chunks = [
        _chunk(chunk_id="today_reuters", publisher="Reuters",
               publication_date=move.move_date),
        _chunk(chunk_id="week_old_blog", publisher="Seeking Alpha",
               publication_date=move.move_date - timedelta(days=7)),
    ]
    ranked = score_chunks(chunks, move)
    assert ranked[0][0].chunk_id == "today_reuters"
    assert ranked[0][1] > ranked[1][1]


def test_score_peer_news_discounted_vs_focal():
    move = _move()
    same_day = move.move_date
    amd_news = _chunk(chunk_id="amd_news", ticker="AMD", publication_date=same_day)
    nvda_peer = _chunk(
        chunk_id="nvda_peer", ticker="NVDA",
        source_type=SourceType.PEER_NEWS, publication_date=same_day,
    )
    s1 = score_chunk(amd_news, move)
    s2 = score_chunk(nvda_peer, move)
    assert s1 > s2


# ---------- filter_and_rank ----------

def test_filter_empty_input_returns_empty():
    assert filter_and_rank([], _move()) == []


def test_filter_respects_min_chunks_on_small_pool():
    """Three chunks -> min_chunks fallback means all three survive."""
    move = _move()
    chunks = [
        _chunk(chunk_id=f"c_{i}", publication_date=move.move_date)
        for i in range(3)
    ]
    out = filter_and_rank(chunks, move)
    assert len(out) == 3


def test_filter_keep_fraction_drops_bottom_tier():
    """20 news chunks over two weeks; default keep_fraction=0.75 -> keep 15."""
    move = _move()
    chunks = [
        _chunk(
            chunk_id=f"c_{i:02d}",
            publication_date=move.move_date - timedelta(days=i),
            publisher="Reuters",
        )
        for i in range(20)
    ]
    out = filter_and_rank(chunks, move)
    assert len(out) == 15
    # Top-ranked chunks are the same-day / near-day ones
    ranked_ids = [c.chunk_id for c, _ in out]
    assert ranked_ids[0] == "c_00"


def test_filter_max_chunks_cap():
    move = _move()
    chunks = [
        _chunk(chunk_id=f"c_{i:03d}", publication_date=move.move_date)
        for i in range(200)
    ]
    out = filter_and_rank(chunks, move, FilterConfig(max_chunks=25, keep_fraction=1.0))
    assert len(out) == 25


def test_filter_min_score_drops_far_future_floor_but_keeps_min_chunks():
    """All-stale chunks (~90 days old news) — each scores ~exp(-ln(2)*90/7)
    ≈ 0.00015. Below min_score=0.02 -> ordinarily dropped, but min_chunks
    fallback guarantees at least min_chunks survive."""
    move = _move()
    chunks = [
        _chunk(
            chunk_id=f"stale_{i}",
            publication_date=move.move_date - timedelta(days=90),
            publisher="Reuters",
        )
        for i in range(10)
    ]
    out = filter_and_rank(chunks, move)
    assert len(out) >= DEFAULT_FILTER.min_chunks


def test_filter_disabled_via_env(monkeypatch):
    """BW_DISABLE_CHUNK_FILTER=1 turns the function into a passthrough."""
    monkeypatch.setenv(DISABLE_ENV_VAR, "1")
    move = _move()
    chunks = [_chunk(chunk_id=f"c_{i}") for i in range(30)]
    out = filter_and_rank(chunks, move)
    assert len(out) == 30
    # Every score is 1.0 when disabled — drops the weighting behavior too
    assert all(s == 1.0 for _, s in out)


# ---------- annotate_with_weights ----------

def test_annotate_preserves_chunk_id():
    move = _move()
    chunks = [_chunk(chunk_id="keep_this_id")]
    scored = score_chunks(chunks, move)
    annotated = annotate_with_weights(scored)
    assert annotated[0].chunk_id == "keep_this_id"
    assert annotated[0].source_type == chunks[0].source_type
    assert annotated[0].publication_date == chunks[0].publication_date


def test_annotate_prepends_tag_to_text():
    chunks = [_chunk(text="body")]
    scored = [(chunks[0], 0.92)]
    annotated = annotate_with_weights(scored)
    assert annotated[0].text.startswith("[EVIDENCE_WEIGHT HIGH (0.92)]")
    assert "body" in annotated[0].text


def test_annotate_tier_bins():
    base = _chunk(text="x")
    for score, tier in ((0.90, "HIGH"), (0.50, "MED"), (0.10, "LOW")):
        out = annotate_with_weights([(base, score)])
        assert f"[EVIDENCE_WEIGHT {tier}" in out[0].text


def test_annotate_idempotent():
    """Re-annotating strips the prior tag so we don't nest markers."""
    base = _chunk(text="body")
    once = annotate_with_weights([(base, 0.5)])[0]
    twice = annotate_with_weights([(once, 0.9)])[0]
    # Only one tag at the start
    assert twice.text.count("[EVIDENCE_WEIGHT") == 1
    assert twice.text.startswith("[EVIDENCE_WEIGHT HIGH (0.90)]")


# ---------- Integration with model.attribute() ----------

def test_attribute_applies_filter_and_notes_it(monkeypatch):
    """20 chunks in; only the top ~15 should reach the downstream attribution
    stub, and model_notes should call out that filtering happened."""
    monkeypatch.delenv(model.LIVE_ENV_VAR, raising=False)  # placeholder path

    move = _move(ticker="AMD")
    chunks = [
        _chunk(
            chunk_id=f"c_{i:02d}",
            publication_date=move.move_date - timedelta(days=i),
            publisher="Reuters",
        )
        for i in range(20)
    ]
    cfg = AblationConfig(name="base_news", sources=[SourceType.NEWS])
    attr = model.attribute(move, chunks, cfg)
    # Filter cut us down to 15
    assert attr.chunks_considered == 15
    assert "filtered to top" in (attr.model_notes or "")


def test_attribute_passes_weight_tagged_chunks_to_live_path(monkeypatch):
    """Live path: verify the chunks handed to _live_attribute carry
    [EVIDENCE_WEIGHT ...] tags AND still have their original chunk_ids
    (so the validator is happy)."""
    monkeypatch.setenv(model.LIVE_ENV_VAR, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-doesnotmatter")

    captured_chunks: list[TextChunk] = []

    def spy_live(mv, chs, cfg):
        captured_chunks.extend(chs)
        # Hand back a minimal valid Attribution
        from schema import Attribution, DimensionScore
        dim = lambda w: DimensionScore(
            weight=w, direction="negative", rationale="stub",
            evidence_chunk_ids=[chs[0].chunk_id],
        )
        return Attribution(
            ticker=mv.ticker, move_date=mv.move_date,
            return_pct=mv.return_pct, predicted_return_pct=mv.return_pct,
            demand=dim(0.6), pricing=dim(0.1), competitive=dim(0.1),
            management_credibility=dim(0.1), macro=dim(0.1),
            move_character="structural", confidence=0.9,
            ablation_name=cfg.name, sources_used=list(cfg.sources),
            chunks_considered=len(chs),
        )

    monkeypatch.setattr(model, "_live_attribute", spy_live)

    move = _move()
    chunks = [
        _chunk(
            chunk_id=f"c_{i:02d}",
            publication_date=move.move_date - timedelta(days=i),
            publisher="Reuters",
        )
        for i in range(10)
    ]
    cfg = AblationConfig(name="base_news", sources=[SourceType.NEWS])
    model.attribute(move, chunks, cfg)

    assert captured_chunks, "live path should have been called"
    for c in captured_chunks:
        assert c.text.startswith("[EVIDENCE_WEIGHT "), (
            f"live-path chunk lost its weight tag: {c.text[:80]!r}"
        )
        # chunk_id must be preserved so citation validation still works
        assert c.chunk_id.startswith("c_")


def test_attribute_no_notes_when_nothing_filtered(monkeypatch):
    """If min_chunks keeps everything, no filter note should be appended."""
    monkeypatch.delenv(model.LIVE_ENV_VAR, raising=False)
    move = _move()
    chunks = [_chunk(chunk_id="c_00", publication_date=move.move_date)]
    cfg = AblationConfig(name="base_news", sources=[SourceType.NEWS])
    attr = model.attribute(move, chunks, cfg)
    assert "filtered to top" not in (attr.model_notes or "")
    assert attr.chunks_considered == 1
