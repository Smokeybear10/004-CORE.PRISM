"""
Smoke tests for the demo's mock-data factory.

The real pipeline will replace `generate_attribution` when `model.attribute()`
lands. These tests lock in the schema contract today so drift is loud when
that swap happens.
"""

from __future__ import annotations

from datetime import date

import pytest

from demo.mock_data import (
    ABLATIONS,
    ABLATION_BY_NAME,
    FOCAL_TICKERS,
    chunks_for,
    generate_attribution,
    sample_backtest_results,
)
from schema import Attribution, BacktestResult, TextChunk


SAMPLE_DATE = date(2024, 2, 1)
SAMPLE_RETURN = -0.065


def test_focal_tickers_match_pr7():
    assert set(FOCAL_TICKERS.keys()) == {"ABT", "ACU", "AIR", "AMD", "APD"}
    for t, meta in FOCAL_TICKERS.items():
        assert meta["name"] and meta["sector"]


def test_chunks_for_returns_real_textchunks():
    chunks = chunks_for("AMD", SAMPLE_DATE)
    assert chunks, "chunks_for must return a non-empty pool"
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids)), "chunk_ids must be unique"
    for c in chunks:
        assert isinstance(c, TextChunk)
        assert c.text.strip()


@pytest.mark.parametrize("ticker", sorted(FOCAL_TICKERS.keys()))
@pytest.mark.parametrize("ablation", [a.name for a in ABLATIONS])
def test_generate_attribution_is_schema_valid(ticker: str, ablation: str):
    attr = generate_attribution(ticker, SAMPLE_DATE, SAMPLE_RETURN, ablation)
    assert isinstance(attr, Attribution)
    assert attr.ticker == ticker
    assert attr.move_date == SAMPLE_DATE
    assert attr.ablation_name == ablation
    assert attr.sources_used == ABLATION_BY_NAME[ablation].sources


@pytest.mark.parametrize("ticker", sorted(FOCAL_TICKERS.keys()))
@pytest.mark.parametrize("ablation", [a.name for a in ABLATIONS])
def test_every_citation_resolves(ticker: str, ablation: str):
    """
    CLAUDE.md rule #6: every DimensionScore.evidence_chunk_ids must reference
    a real chunk. The UI's expander flow errors visibly if citations miss.
    """
    attr = generate_attribution(ticker, SAMPLE_DATE, SAMPLE_RETURN, ablation)
    chunk_ids = {c.chunk_id for c in chunks_for(ticker, SAMPLE_DATE)}
    for dim_name in ("demand", "pricing", "competitive",
                     "management_credibility", "macro"):
        score = getattr(attr, dim_name)
        assert score.evidence_chunk_ids, f"{ticker}/{ablation}/{dim_name}: empty citation"
        for cid in score.evidence_chunk_ids:
            assert cid in chunk_ids, (
                f"{ticker}/{ablation}/{dim_name} cites missing `{cid}`"
            )


def test_confidence_grows_with_ablation_depth():
    """The additive story: confidence should rise as we layer in sources."""
    confidences = [
        generate_attribution("AMD", SAMPLE_DATE, SAMPLE_RETURN, a.name).confidence
        for a in ABLATIONS
    ]
    # Strictly monotonic not required (jitter is fine), but the endpoints
    # should clearly trend upward.
    assert confidences[-1] > confidences[0] + 0.2


def test_generate_attribution_is_deterministic():
    a = generate_attribution("AMD", SAMPLE_DATE, SAMPLE_RETURN, "+macro")
    b = generate_attribution("AMD", SAMPLE_DATE, SAMPLE_RETURN, "+macro")
    assert a.demand.weight == b.demand.weight
    assert a.confidence == b.confidence


def test_backtests_cover_every_ablation():
    ablation_names = {a.name for a in ABLATIONS}
    bt_names = {b.ablation_name for b in sample_backtest_results()}
    assert ablation_names == bt_names
    for b in sample_backtest_results():
        assert isinstance(b, BacktestResult)
        assert 0.0 <= b.hit_rate <= 1.0
