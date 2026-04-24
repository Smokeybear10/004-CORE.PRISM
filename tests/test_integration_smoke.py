"""
End-to-end smoke test: proves the pipeline composes today.

Touches every module on the critical path:
    ingestion.prices  (produces PriceMove-shaped rows in the sample parquet)
          ↓
    model.attribute   (placeholder fixture, swap in real LLM later)
          ↓
    backtest.run_ablation / fade_or_follow / evaluate
          ↓
    schema.BacktestResult

Runs on a tiny 340-row fixture so teammates can `pytest tests/` on a fresh
clone without running the 30-minute Yahoo pipeline first. When the real
model lands, this file is still the fastest way to check that producer and
consumer types haven't drifted apart.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from schema import AblationConfig, PriceMove, SourceType, TextChunk

FIXTURE = Path(__file__).parent / "fixtures" / "events_focal_sample.parquet"


def _events_to_moves(df: pd.DataFrame) -> list[PriceMove]:
    """Project the events_focal schema onto schema.PriceMove."""
    moves: list[PriceMove] = []
    for r in df.itertuples(index=False):
        moves.append(PriceMove(
            ticker=r.ticker,
            move_date=pd.Timestamp(r.reaction_end).date()
                      if not isinstance(r.reaction_end, date) else r.reaction_end,
            return_pct=float(r.reaction_return),
            vol_zscore=float(r.reaction_return_zscore) if pd.notna(r.reaction_return_zscore) else 0.0,
            is_significant=bool(r.is_significant),
        ))
    return moves


def _fake_chunks_for(moves: list[PriceMove]) -> dict[SourceType, list[TextChunk]]:
    """One throwaway chunk per (ticker, source) so the no-foreknowledge filter
    in run_ablation has something to return."""
    out: dict[SourceType, list[TextChunk]] = defaultdict(list)
    for m in moves:
        for s in (SourceType.NEWS, SourceType.SEC_8K, SourceType.EARNINGS_TRANSCRIPT):
            out[s].append(TextChunk(
                chunk_id=f"{s.value}_{m.ticker}_{m.move_date}_000",
                ticker=m.ticker,
                source_type=s,
                publication_date=m.move_date,
                text=f"placeholder {s.value} chunk for {m.ticker} on {m.move_date}",
            ))
    return dict(out)


def test_sample_fixture_loads():
    assert FIXTURE.exists(), "events_focal_sample.parquet missing"
    df = pd.read_parquet(FIXTURE)
    assert len(df) > 50
    # Sanity on the schema contract producing this fixture:
    required = {"ticker", "earnings_date", "reaction_end", "reaction_return",
                "reaction_return_zscore", "is_significant", "is_focal"}
    assert required.issubset(df.columns)


def test_fixture_rows_project_to_pricemove_cleanly():
    df = pd.read_parquet(FIXTURE).head(20)
    moves = _events_to_moves(df)
    assert len(moves) == 20
    assert all(isinstance(m, PriceMove) for m in moves)


def test_end_to_end_attribute_runs_on_sample():
    """PriceMove → model.attribute → Attribution with the required fields set."""
    from model import attribute

    df = pd.read_parquet(FIXTURE).head(5)
    moves = _events_to_moves(df)
    chunks = _fake_chunks_for(moves)
    cfg = AblationConfig(name="base_news", sources=[SourceType.NEWS])

    attrs = [attribute(m, chunks[SourceType.NEWS], cfg) for m in moves]
    assert len(attrs) == 5
    for a in attrs:
        assert a.ablation_name == "base_news"
        assert a.sources_used == [SourceType.NEWS]
        assert a.chunks_considered == len(chunks[SourceType.NEWS])
        # Evidence chunk IDs must reference chunks we actually passed in
        chunk_ids = {c.chunk_id for c in chunks[SourceType.NEWS]}
        for ds in (a.demand, a.pricing, a.competitive,
                   a.management_credibility, a.macro):
            assert ds.evidence_chunk_ids
            assert all(cid in chunk_ids for cid in ds.evidence_chunk_ids)


def test_end_to_end_backtest_produces_result():
    """Full pipeline: attributions → fade_or_follow → evaluate → BacktestResult."""
    from backtest import DEFAULT_ABLATIONS, evaluate, run_ablation
    from schema import BacktestResult

    df = pd.read_parquet(FIXTURE).head(30)
    moves = _events_to_moves(df)
    chunks = _fake_chunks_for(moves)

    results_by_ablation = run_ablation(moves, chunks, configs=DEFAULT_ABLATIONS[:2])
    assert set(results_by_ablation) == {"base_news", "+sec"}

    # Use the sample's 5-day forward returns where available, else 0.
    realized = {}
    for r in df.itertuples(index=False):
        md = pd.Timestamp(r.reaction_end).date()
        fwd = float(r.fwd_5d) if pd.notna(r.fwd_5d) else 0.0
        realized[f"{r.ticker}_{md}"] = fwd

    for name, attrs in results_by_ablation.items():
        bt = evaluate(attrs, realized)
        assert isinstance(bt, BacktestResult)
        assert bt.ablation_name == name
        assert bt.n_trades >= 0
        # BacktestResult fields are all finite numbers
        for fld in ("sharpe", "hit_rate", "avg_return", "max_drawdown"):
            v = getattr(bt, fld)
            assert v == v, f"{fld} is NaN for {name}"  # NaN-check via self-equality


def test_fade_or_follow_branches():
    """Unit-level check of the rule — catches regressions if schema or
    logic drifts."""
    from schema import Attribution, DimensionScore
    from backtest import fade_or_follow

    ds = DimensionScore(weight=0.2, direction="positive", rationale="x",
                        evidence_chunk_ids=["placeholder_0"])
    base = dict(ticker="AAPL", move_date=date(2024, 2, 1), return_pct=-0.08,
                demand=ds, pricing=ds, competitive=ds,
                management_credibility=ds, macro=ds,
                confidence=0.8, chunks_considered=1)

    transient = Attribution(**base, move_character="transient",
                            predicted_return_pct=-0.02)
    structural_aligned = Attribution(**base, move_character="structural",
                                     predicted_return_pct=-0.05)
    structural_flipped = Attribution(**base, move_character="structural",
                                     predicted_return_pct=+0.05)
    unclear = Attribution(**base, move_character="unclear")

    assert fade_or_follow(transient, -0.08) == "fade"
    assert fade_or_follow(structural_aligned, -0.08) == "lean"
    assert fade_or_follow(structural_flipped, -0.08) == "neutral"
    assert fade_or_follow(unclear, -0.08) == "neutral"
