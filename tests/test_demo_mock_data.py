"""
Smoke tests for demo/mock_data.py.

The demo app is schema-valid today; these tests lock that in so we catch
drift as the real ingestion/model modules replace the mock factories.
"""

from __future__ import annotations

import pytest

from demo.mock_data import (
    ABLATIONS,
    all_chunks,
    sample_attributions,
    sample_backtest_results,
    sample_moves,
)
from schema import Attribution, BacktestResult, PriceMove, TextChunk


# ---------- Every mock object is schema-valid ----------

def test_moves_are_schema_valid():
    moves = sample_moves()
    assert moves, "expected at least one sample move"
    for m in moves:
        assert isinstance(m, PriceMove)
        assert m.is_significant, "mock moves represent flagged events"


def test_chunks_are_schema_valid_and_unique():
    chunks = all_chunks()
    assert chunks, "expected fixture + synthetic chunks"
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids)), "chunk_ids must be unique"
    for c in chunks:
        assert isinstance(c, TextChunk)
        assert c.text.strip(), "every chunk must carry meaningful text"


def test_attributions_are_schema_valid():
    attrs = sample_attributions()
    assert attrs, "expected at least one attribution"
    for a in attrs:
        assert isinstance(a, Attribution)
        assert a.ablation_name, "ablation_name must be set for the UI to group"
        assert a.sources_used, "sources_used must be set for the UI to show"


def test_backtests_are_schema_valid():
    results = sample_backtest_results()
    assert results, "expected at least one backtest result"
    for r in results:
        assert isinstance(r, BacktestResult)
        assert 0.0 <= r.hit_rate <= 1.0


# ---------- Cross-module contracts ----------

def test_every_attribution_cites_a_real_chunk():
    """
    CLAUDE.md non-negotiable: every DimensionScore.evidence_chunk_ids must
    reference a chunk that actually exists. Uncited scores get dropped.
    """
    chunk_ids = {c.chunk_id for c in all_chunks()}
    for attr in sample_attributions():
        for dim_name in ("demand", "pricing", "competitive",
                         "management_credibility", "macro"):
            score = getattr(attr, dim_name)
            assert score.evidence_chunk_ids, (
                f"{attr.move_date} · {attr.ablation_name} · {dim_name}: "
                "no evidence cited"
            )
            for cid in score.evidence_chunk_ids:
                assert cid in chunk_ids, (
                    f"{attr.move_date} · {attr.ablation_name} · {dim_name} "
                    f"cites missing chunk `{cid}`"
                )


def test_attribution_coverage_matches_moves_x_ablations():
    """
    The UI assumes one attribution per (move_date, ablation_name) combo.
    If the grid is sparse, the frontend shows a warning row instead of data.
    """
    moves = sample_moves()
    ablation_names = {a.name for a in ABLATIONS}
    keys = {(str(a.move_date), a.ablation_name) for a in sample_attributions()}
    expected = {(str(m.move_date), name)
                for m in moves for name in ablation_names}
    missing = expected - keys
    assert not missing, f"missing attribution grid cells: {sorted(missing)}"


def test_ablation_names_match_backtest_rows():
    """Ablation comparison chart pulls by ablation_name; keep them in sync."""
    ablation_names = {a.name for a in ABLATIONS}
    bt_names = {b.ablation_name for b in sample_backtest_results()}
    assert ablation_names == bt_names, (
        f"ablation/backtest name drift: "
        f"only-in-ABLATIONS={ablation_names - bt_names}, "
        f"only-in-backtests={bt_names - ablation_names}"
    )


@pytest.mark.parametrize("move_date", ["2024-08-15", "2024-10-17", "2024-11-01"])
def test_every_move_has_a_full_ablation_row(move_date: str):
    """For each move, every ABLATION must produce an Attribution."""
    attrs = [a for a in sample_attributions() if str(a.move_date) == move_date]
    assert {a.ablation_name for a in attrs} == {a.name for a in ABLATIONS}
