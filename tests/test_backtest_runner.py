"""
End-to-end smoke tests for the backtest runner. No parquet on disk, no API,
no network. All runs use make_synthetic_events_df.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from schema import BacktestResult
from backtest.fixtures import ABLATION_BUNDLES, make_synthetic_events_df
from backtest.baselines import BASELINES
from backtest.runner import (
    _load_events,
    main,
    run_ablation,
    run_all,
    run_baseline,
    results_to_frame,
)


@pytest.fixture
def events_df():
    return make_synthetic_events_df(n=30, seed=0)


# ---------- individual runs ----------

def test_run_ablation_returns_valid_result(events_df):
    result, pnl_df = run_ablation(events_df, "base_news",
                                  horizon=5, use_excess=True, seed=0)
    assert isinstance(result, BacktestResult)
    assert result.ablation_name == "base_news"
    assert result.strategy_name.startswith("struct_")
    assert len(pnl_df) == len(events_df)
    # At least some non-neutral trades given 30 events on a wide reaction distribution.
    assert (pnl_df["direction"] != 0).sum() > 0


def test_run_baseline_returns_valid_result(events_df):
    result, pnl_df = run_baseline(events_df, "always_lean",
                                  horizon=5, use_excess=True)
    assert isinstance(result, BacktestResult)
    assert result.strategy_name == "baseline_always_lean"
    assert len(pnl_df) == len(events_df)


def test_run_all_produces_one_row_per_ablation_and_baseline(events_df):
    results = run_all(events_df, horizon=5, use_excess=True, seed=0)
    assert len(results) == len(ABLATION_BUNDLES) + len(BASELINES)

    ablation_names = {r.ablation_name for r in results if r.ablation_name}
    assert ablation_names == set(ABLATION_BUNDLES.keys())

    baseline_names = {r.strategy_name for r in results
                      if r.strategy_name.startswith("baseline_")}
    assert baseline_names == {f"baseline_{n}" for n in BASELINES}


def test_results_to_frame_has_expected_columns(events_df):
    results = run_all(events_df, horizon=5, use_excess=True, seed=0)
    frame = results_to_frame(results)
    expected_cols = {
        "strategy_name", "ablation_name", "n_trades", "sharpe",
        "hit_rate", "avg_return", "max_drawdown", "notes",
    }
    assert expected_cols.issubset(frame.columns)
    assert len(frame) == len(results)


# ---------- loader fallback ----------

def test_load_events_fallback_when_file_missing(tmp_path, capfd):
    missing = tmp_path / "does_not_exist.parquet"
    df = _load_events(str(missing), universe="all", seed=0)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 50  # matches synthetic fallback
    err = capfd.readouterr().err
    assert "synthetic events" in err


def test_load_events_universe_filters(tmp_path):
    df_all = _load_events(str(tmp_path / "missing.parquet"),
                          universe="all", seed=0)
    df_focal = _load_events(str(tmp_path / "missing.parquet"),
                            universe="focal", seed=0)
    df_sig = _load_events(str(tmp_path / "missing.parquet"),
                          universe="significant", seed=0)
    assert len(df_focal) <= len(df_all)
    assert len(df_sig) <= len(df_all)
    if len(df_focal) > 0:
        assert df_focal["is_focal"].all()
    if len(df_sig) > 0:
        assert df_sig["is_significant"].all()


def test_load_events_rejects_unknown_universe(tmp_path):
    with pytest.raises(ValueError, match="unknown universe"):
        _load_events(str(tmp_path / "missing.parquet"),
                     universe="not_a_universe", seed=0)


# ---------- CLI ----------

def test_cli_writes_csv_end_to_end(tmp_path, capfd):
    csv_path = tmp_path / "out.csv"
    main([
        "--events", str(tmp_path / "missing.parquet"),
        "--universe", "all",
        "--write-csv", str(csv_path),
        "--seed", "0",
    ])
    assert csv_path.exists()
    frame = pd.read_csv(csv_path)
    # 7 ablations + 4 baselines = 11 rows
    assert len(frame) == len(ABLATION_BUNDLES) + len(BASELINES)
