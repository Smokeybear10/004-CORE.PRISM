"""
Tests for backtest.pnl dollar/equity helpers and backtest.report verdict layer.
"""

from __future__ import annotations

import pandas as pd
import pytest

from schema import BacktestResult
from backtest.fixtures import make_synthetic_events_df
from backtest.pnl import DEFAULT_NOTIONAL, equity_curve, total_pnl
from backtest.report import (
    compare_strategies,
    pick_structured_representative,
    verdict_markdown,
)


# ── Fixture helpers ─────────────────────────────────────────────────────────

def _pnl_row(event_id: str, pnl_pct: float, direction: int = 1,
             size: float = 1.0) -> dict:
    """One row in the shape `compute_pnl` produces."""
    return {
        "event_id": event_id,
        "ticker": event_id.split("_")[0],
        "action": "lean" if direction > 0 else "fade",
        "direction": direction,
        "size": size,
        "horizon_days": 5,
        "realized_fwd_return": pnl_pct,
        "pnl": pnl_pct,                # direction × size × fwd_return == pnl_pct
        "confidence": 0.7,
    }


def _result(strategy_name: str, sharpe: float, n_trades: int = 10,
            ablation_name: str | None = None,
            hit_rate: float = 0.55, avg_return: float = 0.001,
            max_drawdown: float = -0.02) -> BacktestResult:
    return BacktestResult(
        strategy_name=strategy_name,
        ablation_name=ablation_name,
        n_trades=n_trades,
        sharpe=sharpe,
        hit_rate=hit_rate,
        avg_return=avg_return,
        max_drawdown=max_drawdown,
    )


# ── total_pnl ───────────────────────────────────────────────────────────────

def test_total_pnl_zero_on_empty_frame():
    assert total_pnl(pd.DataFrame(), notional=100_000) == 0.0


def test_total_pnl_math_on_known_returns():
    # Three trades: +2%, -1%, +1.5% on $100k notional each
    # Expected: 100_000 * (0.02 - 0.01 + 0.015) = 100_000 * 0.025 = $2,500
    df = pd.DataFrame([
        _pnl_row("AAPL_1", 0.02),
        _pnl_row("AAPL_2", -0.01),
        _pnl_row("AAPL_3", 0.015),
    ])
    assert total_pnl(df, notional=100_000) == pytest.approx(2_500.0)


def test_total_pnl_scales_linearly_with_notional():
    df = pd.DataFrame([_pnl_row("AAPL_1", 0.03)])
    assert total_pnl(df, notional=100_000) == pytest.approx(3_000.0)
    assert total_pnl(df, notional=1_000_000) == pytest.approx(30_000.0)
    assert total_pnl(df, notional=50_000) == pytest.approx(1_500.0)


# ── equity_curve ────────────────────────────────────────────────────────────

def test_equity_curve_returns_expected_columns():
    df = pd.DataFrame([_pnl_row("AAPL_1", 0.02)])
    out = equity_curve(df, notional=100_000)
    assert list(out.columns) == [
        "event_id", "entry_date", "pnl_dollars",
        "cumulative_dollars", "equity",
    ]


def test_equity_curve_starts_at_notional_and_accumulates():
    # Three trades: +1k, -500, +300 on $100k notional
    df = pd.DataFrame([
        _pnl_row("E_1", 0.010),    # +$1,000
        _pnl_row("E_2", -0.005),   # -$500
        _pnl_row("E_3", 0.003),    # +$300
    ])
    out = equity_curve(df, notional=100_000)
    assert list(out["pnl_dollars"]) == pytest.approx([1_000, -500, 300])
    assert list(out["cumulative_dollars"]) == pytest.approx([1_000, 500, 800])
    assert list(out["equity"]) == pytest.approx([101_000, 100_500, 100_800])


def test_equity_curve_chronological_when_events_provided():
    # Build pnl in event-id order but events out-of-order chronologically.
    pnl = pd.DataFrame([
        _pnl_row("E_A", 0.01),
        _pnl_row("E_B", 0.02),
        _pnl_row("E_C", -0.01),
    ])
    events = pd.DataFrame([
        {"event_id": "E_A", "reaction_end": pd.Timestamp("2024-03-15")},
        {"event_id": "E_B", "reaction_end": pd.Timestamp("2024-01-10")},
        {"event_id": "E_C", "reaction_end": pd.Timestamp("2024-02-20")},
    ])
    out = equity_curve(pnl, notional=100_000, events_df=events)
    # Order should be B (Jan), C (Feb), A (Mar).
    assert list(out["event_id"]) == ["E_B", "E_C", "E_A"]
    assert list(out["pnl_dollars"]) == pytest.approx([2_000, -1_000, 1_000])
    assert list(out["cumulative_dollars"]) == pytest.approx([2_000, 1_000, 2_000])


def test_equity_curve_empty_frame_returns_empty_with_correct_columns():
    out = equity_curve(pd.DataFrame(), notional=100_000)
    assert out.empty
    assert list(out.columns) == [
        "event_id", "entry_date", "pnl_dollars",
        "cumulative_dollars", "equity",
    ]


# ── compare_strategies / verdict ────────────────────────────────────────────

def _standard_results() -> list[BacktestResult]:
    """Synthetic: one structured run + 4 baselines, structured ranks 2nd."""
    return [
        _result("struct_fundamental_vs_nonfundamental", 0.50,
                ablation_name="+positioning"),
        _result("baseline_always_lean",        -0.20),
        _result("baseline_always_fade",         0.10),
        _result("baseline_random_attribution",  0.30),
        _result("baseline_sentiment_only",      0.70),
    ]


def test_pick_structured_representative_picks_highest_sharpe():
    results = [
        _result("struct_X", 0.10, ablation_name="base_news"),
        _result("struct_X", 0.50, ablation_name="+positioning"),
        _result("struct_X", 0.30, ablation_name="+sec"),
        _result("baseline_always_lean", 0.20),
    ]
    rep = pick_structured_representative(results)
    assert rep.ablation_name == "+positioning"
    assert rep.sharpe == 0.50


def test_pick_structured_representative_honors_ablation_filter():
    results = [
        _result("struct_X", 0.10, ablation_name="base_news"),
        _result("struct_X", 0.50, ablation_name="+positioning"),
    ]
    rep = pick_structured_representative(results, ablation="base_news")
    assert rep.ablation_name == "base_news"
    assert rep.sharpe == 0.10


def test_pick_structured_representative_returns_none_when_no_structured():
    rep = pick_structured_representative([_result("baseline_always_lean", 0.5)])
    assert rep is None


def test_compare_strategies_ranks_correctly():
    comp = compare_strategies(_standard_results())
    # Sharpes: sentiment 0.70 > structured 0.50 > random 0.30 > fade 0.10 > lean -0.20
    assert comp["structured_rank"] == 2
    assert set(comp["beats"]) == {"always_lean", "always_fade", "random_attribution"}
    assert comp["loses_to"] == ["sentiment_only"]
    assert "beat 3/4" in comp["verdict"].lower() or "beat 3 " in comp["verdict"]


def test_compare_strategies_handles_all_baselines_better():
    results = [
        _result("struct_fundamental_vs_nonfundamental", -0.50,
                ablation_name="+positioning"),
        _result("baseline_always_lean",        0.20),
        _result("baseline_always_fade",        0.10),
        _result("baseline_random_attribution", 0.30),
        _result("baseline_sentiment_only",     0.70),
    ]
    comp = compare_strategies(results)
    assert comp["structured_rank"] == 5
    assert comp["beats"] == []
    assert set(comp["loses_to"]) == {
        "always_lean", "always_fade", "random_attribution", "sentiment_only",
    }
    assert "lost to" in comp["verdict"].lower()


def test_compare_strategies_handles_zero_baselines():
    """No baselines run alongside the structured strategy — verdict should
    still produce a clean message, not crash."""
    results = [_result("struct_fundamental_vs_nonfundamental", 0.50,
                       ablation_name="+positioning")]
    comp = compare_strategies(results)
    assert comp["structured_rank"] == 1
    assert comp["beats"] == []
    assert comp["loses_to"] == []
    assert "can't say" in comp["verdict"].lower() or "no baseline" in comp["verdict"].lower()


def test_compare_strategies_with_dollars_includes_dollar_clause():
    dollars = {
        "struct_fundamental_vs_nonfundamental__+positioning": 4_127.55,
        "baseline_always_lean": -1_200.00,
        "baseline_always_fade": 800.00,
        "baseline_random_attribution": 1_300.00,
        "baseline_sentiment_only": 5_500.00,
    }
    comp = compare_strategies(_standard_results(), dollars_by_strategy=dollars)
    assert "$+4,127.55" in comp["verdict"] or "$4,127.55" in comp["verdict"]
    # Table includes a 'dollars' column when dollars_by_strategy is passed.
    assert "dollars" in comp["table"].columns
    structured_row = comp["table"][comp["table"]["label"].str.startswith("struct_")]
    assert structured_row["dollars"].iloc[0] == pytest.approx(4_127.55)


def test_compare_strategies_returns_no_results_message_when_empty():
    comp = compare_strategies([])
    assert comp["structured"] is None
    assert "no structured" in comp["verdict"].lower()


def test_compare_strategies_invalid_ablation_raises():
    with pytest.raises(ValueError, match="no structured result for ablation"):
        compare_strategies(_standard_results(), ablation="not_a_real_ablation")


# ── verdict_markdown smoke ──────────────────────────────────────────────────

def test_verdict_markdown_contains_verdict_and_table():
    comp = compare_strategies(_standard_results())
    md = verdict_markdown(comp, notional=100_000)
    assert "# Strategy P/L Report" in md
    assert "$100,000" in md
    assert comp["verdict"] in md
    # Table renders as markdown table — check for at least one column header
    assert "| label" in md or "|label" in md or "label " in md


# ── End-to-end smoke against synthetic events ───────────────────────────────

def test_end_to_end_synthetic_events():
    """Run the orchestrator's pipeline (run_ablation + run_baseline) against
    synthetic events and assert the verdict layer composes cleanly."""
    from backtest.runner import run_ablation, run_baseline

    events_df = make_synthetic_events_df(n=30, seed=0)
    r_struct, _ = run_ablation(events_df, "+positioning", seed=0)
    r_lean, _ = run_baseline(events_df, "always_lean")
    r_fade, _ = run_baseline(events_df, "always_fade")
    r_rand, _ = run_baseline(events_df, "random_attribution")
    r_sent, _ = run_baseline(events_df, "sentiment_only")

    comp = compare_strategies([r_struct, r_lean, r_fade, r_rand, r_sent])

    # Structural sanity: structured exists; rank is 1..5; table has 5 rows.
    assert comp["structured"] is not None
    assert 1 <= comp["structured_rank"] <= 5
    assert len(comp["table"]) == 5
    assert len(comp["beats"]) + len(comp["loses_to"]) == 4
