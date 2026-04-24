"""
Unit tests for eval.scorer. No API calls. These run fast and catch scorer
regressions independent of model / ingestion implementation.
"""

from __future__ import annotations

from datetime import date

import pytest

from eval.config import ScorerConfig
from eval.scorer import (
    ExpectedAttribution,
    _dimension_fields,
    _dominant_dimension,
    _source_type_from_chunk_id,
    score,
)
from schema import (
    AblationConfig,
    Attribution,
    DimensionScore,
    SourceType,
)


# ---------- Helpers ----------

def _dim(
    weight: float,
    direction: str = "neutral",
    evidence: list[str] = None,
    rationale: str = "test",
) -> DimensionScore:
    return DimensionScore(
        weight=weight,
        direction=direction,
        rationale=rationale,
        evidence_chunk_ids=evidence or ["news_AMD_2022-10-06_headline_001"],
    )


def _attribution(
    ticker: str = "AMD",
    move_date: date = date(2022, 10, 6),
    weights: dict[str, float] = None,
    directions: dict[str, str] = None,
    move_character: str = "structural",
    evidence_by_dim: dict[str, list[str]] = None,
    ablation_name: str = "base_news",
) -> Attribution:
    """Build a synthetic Attribution with per-dimension weight/direction overrides."""
    defaults = {"demand": 0.1, "pricing": 0.1, "competitive": 0.1,
                "management_credibility": 0.1, "macro": 0.1}
    w = {**defaults, **(weights or {})}
    d = {k: "neutral" for k in defaults}
    if directions:
        d.update(directions)
    evidence_by_dim = evidence_by_dim or {}

    kwargs = {
        "ticker": ticker,
        "move_date": move_date,
        "return_pct": -0.05,
        "move_character": move_character,
        "confidence": 0.7,
        "ablation_name": ablation_name,
        "sources_used": [SourceType.NEWS],
        "chunks_considered": 5,
    }
    for dim_name in defaults:
        kwargs[dim_name] = _dim(
            w[dim_name],
            direction=d[dim_name],
            evidence=evidence_by_dim.get(dim_name),
        )
    return Attribution(**kwargs)


def _ablation(name: str = "base_news", sources: list[SourceType] = None) -> AblationConfig:
    return AblationConfig(
        name=name, sources=sources or [SourceType.NEWS],
    )


# ---------- Introspection helpers ----------

def test_dimension_fields_discovers_all_five():
    attr = _attribution()
    fields = _dimension_fields(attr)
    assert set(fields) == {"demand", "pricing", "competitive",
                           "management_credibility", "macro"}


def test_dominant_dimension_picks_highest_weight():
    attr = _attribution(weights={"demand": 0.6, "macro": 0.1})
    assert _dominant_dimension(attr) == "demand"


def test_source_type_from_chunk_id_handles_multi_word_prefixes():
    assert _source_type_from_chunk_id("sec_10k_AMD_2022-10-06_mda_001") == SourceType.SEC_10K
    assert _source_type_from_chunk_id(
        "earnings_transcript_AMD_2022-10-06_qa_014"
    ) == SourceType.EARNINGS_TRANSCRIPT
    assert _source_type_from_chunk_id("news_AMD_2022-10-06_headline_001") == SourceType.NEWS
    assert _source_type_from_chunk_id("bogus_prefix") is None


# ---------- Scoring: full fixture ----------

def test_score_all_match_gives_composite_one():
    attr = _attribution(
        weights={"demand": 0.7, "macro": 0.1},
        directions={"demand": "negative"},
        move_character="structural",
    )
    expected = ExpectedAttribution(
        dominant_dimension=["demand"],
        direction="negative",
        move_character="structural",
    )
    result = score(attr, expected, _ablation())
    assert result.composite == pytest.approx(1.0)
    assert result.dim_match is True
    assert result.dir_match is True
    assert result.char_match is True


def test_score_all_miss_gives_composite_zero():
    attr = _attribution(
        weights={"macro": 0.7, "demand": 0.05},
        directions={"macro": "positive"},
        move_character="transient",
    )
    expected = ExpectedAttribution(
        dominant_dimension=["demand"],
        direction="negative",
        move_character="structural",
    )
    result = score(attr, expected, _ablation())
    assert result.composite == pytest.approx(0.0)


# ---------- Scoring: partial fixture ----------

def test_missing_assertions_are_skipped_not_failed():
    """Fixture asserts only direction; score should be 1.0 if direction matches."""
    attr = _attribution(
        weights={"macro": 0.6, "demand": 0.05},
        directions={"macro": "negative"},
        move_character="transient",
    )
    expected = ExpectedAttribution(direction="negative")
    result = score(attr, expected, _ablation())
    assert result.composite == pytest.approx(1.0)
    assert result.dim_match is None   # not asserted → not scored
    assert result.char_match is None
    assert result.dir_match is True


def test_empty_fixture_is_unscored():
    expected = ExpectedAttribution()
    result = score(_attribution(), expected, _ablation())
    assert result.composite == 0.0
    assert any("No scorable assertions" in n for n in result.notes)


# ---------- Scoring: dominant_dimension as list ----------

def test_dominant_dimension_accepts_multiple_valid_answers():
    attr = _attribution(weights={"competitive": 0.5, "demand": 0.2})
    expected = ExpectedAttribution(dominant_dimension=["demand", "competitive"])
    result = score(attr, expected, _ablation())
    assert result.dim_match is True


# ---------- must_not_be_dominant veto ----------

def test_must_not_be_dominant_vetos_dim_match():
    """If the model picks a forbidden dominant dim, dim_match flips to False."""
    attr = _attribution(weights={"management_credibility": 0.5, "demand": 0.05})
    expected = ExpectedAttribution(
        dominant_dimension=["management_credibility"],   # would otherwise pass
        must_not_be_dominant=["management_credibility"],
    )
    result = score(attr, expected, _ablation())
    assert result.dim_match is False
    assert result.must_not_be_dominant_ok is False


# ---------- Ablation-aware citation scoring ----------

def test_auto_skip_unreachable_sources():
    """
    base_news ablation excludes macro. Fixture says must_cite_source_type=macro.
    With auto_skip_unreachable_sources=True, that requirement is dropped rather
    than counted as a miss — otherwise base_news always looks artificially bad
    on macro-driven cases.
    """
    attr = _attribution()
    expected = ExpectedAttribution(must_cite_source_type=[SourceType.MACRO])
    ablation = _ablation("base_news", sources=[SourceType.NEWS])
    cfg = ScorerConfig(cite_source_weight=1.0, auto_skip_unreachable_sources=True)
    result = score(attr, expected, ablation, cfg)
    assert result.cite_source_match is None   # stripped assertion
    assert any("dropped from requirement" in n for n in result.notes)


def test_must_cite_source_type_fails_when_reachable_but_missing():
    attr = _attribution(evidence_by_dim={"demand": ["news_AMD_2022-10-06_h_001"]})
    expected = ExpectedAttribution(must_cite_source_type=[SourceType.SEC_10K])
    ablation = _ablation(
        "+sec",
        sources=[SourceType.NEWS, SourceType.SEC_10K],
    )
    cfg = ScorerConfig(cite_source_weight=1.0)
    result = score(attr, expected, ablation, cfg)
    assert result.cite_source_match is False


# ---------- Fade-or-lean scoring ----------

def test_fade_or_lean_matches_structural_to_lean():
    attr = _attribution(move_character="structural")
    expected = ExpectedAttribution(fade_or_lean="lean")
    result = score(attr, expected, _ablation())
    assert result.fade_lean_match is True
    assert result.observed_fade_or_lean == "lean"


def test_fade_or_lean_matches_transient_to_fade():
    attr = _attribution(move_character="transient")
    expected = ExpectedAttribution(fade_or_lean="fade")
    result = score(attr, expected, _ablation())
    assert result.fade_lean_match is True
    assert result.observed_fade_or_lean == "fade"


def test_fade_or_lean_miss_noted():
    attr = _attribution(move_character="structural")  # -> lean
    expected = ExpectedAttribution(fade_or_lean="fade")
    result = score(attr, expected, _ablation())
    assert result.fade_lean_match is False
    assert any("fade_or_lean" in n for n in result.notes)


# ---------- Dimension-agnostic resilience ----------

def test_scorer_does_not_hardcode_dimension_names():
    """
    If the team drops management_credibility from the schema, score() still
    works on the remaining dimensions. We simulate by writing a fixture that
    references a dimension the Attribution doesn't have.
    """
    attr = _attribution(weights={"macro": 0.6, "demand": 0.05})
    expected = ExpectedAttribution(dominant_dimension=["regulatory"])  # not a field
    result = score(attr, expected, _ablation())
    # Doesn't raise; scores as a miss with a helpful note.
    assert result.dim_match is False
    assert result.observed_dominant_dimension == "macro"
