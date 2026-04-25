"""
One command to run the whole thing.

    python -m backtest.runner                  # all ablations + all baselines, prints table
    python -m backtest.runner --ablation base_news
    python -m backtest.runner --baseline always_fade
    python -m backtest.runner --write-csv out/backtest.csv

Pipeline per configuration:
    events_focal.parquet
        → [filter to is_significant & is_focal (or peers)]
        → [generate_attributions_for_events(ablation_name)]
        → [attribution_to_trade] per event
        → [compute_pnl] using fwd_5d_excess
        → [summarize] to a BacktestResult
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

import pandas as pd

from schema import BacktestResult
from backtest.fixtures import (
    ABLATION_BUNDLES,
    generate_attributions_for_events,
    make_synthetic_events_df,
)
from backtest.signal import attribution_to_trade
from backtest.pnl import compute_pnl, summarize
from backtest.baselines import BASELINES


def _load_events(path: str, universe: str, seed: int = 0) -> pd.DataFrame:
    if not Path(path).exists():
        print(
            f"WARNING: {path} not found — falling back to synthetic events "
            f"(make_synthetic_events_df). This is fine for smoke tests and "
            f"demo rehearsals but NOT for real backtests.",
            file=sys.stderr,
        )
        df = make_synthetic_events_df(n=50, seed=seed)
    else:
        df = pd.read_parquet(path)
    df["earnings_date"] = pd.to_datetime(df["earnings_date"])
    df["reaction_end"]  = pd.to_datetime(df["reaction_end"])

    if universe == "significant":
        df = df[df["is_significant"]]
    elif universe == "focal":
        df = df[df["is_focal"]]
    elif universe == "focal_significant":
        df = df[df["is_focal"] & df["is_significant"]]
    elif universe == "all":
        pass
    else:
        raise ValueError(f"unknown universe: {universe}")
    return df.reset_index(drop=True)


def run_ablation(events_df: pd.DataFrame, ablation_name: str,
                 horizon: int = 5, use_excess: bool = True,
                 strategy: str = "fundamental_vs_nonfundamental",
                 seed: int = 0) -> tuple[BacktestResult, pd.DataFrame]:
    """Returns (BacktestResult, per-trade PnL dataframe)."""
    attrs = generate_attributions_for_events(events_df, ablation_name=ablation_name, seed=seed)
    trades = [
        attribution_to_trade(
            attr=a,
            event_id=ev.event_id,
            reaction_return=float(ev.reaction_return),
            exit_horizon_days=horizon,
            strategy=strategy,
        )
        for a, ev in zip(attrs, events_df.itertuples(index=False))
    ]
    pnl_df = compute_pnl(trades, events_df, horizon=horizon, use_excess=use_excess)
    result = summarize(pnl_df, strategy_name=f"struct_{strategy}",
                       ablation_name=ablation_name, horizon_days=horizon)
    return result, pnl_df


def run_baseline(events_df: pd.DataFrame, baseline_name: str,
                 horizon: int = 5, use_excess: bool = True) -> tuple[BacktestResult, pd.DataFrame]:
    trades = BASELINES[baseline_name](events_df, horizon=horizon)
    pnl_df = compute_pnl(trades, events_df, horizon=horizon, use_excess=use_excess)
    result = summarize(pnl_df, strategy_name=f"baseline_{baseline_name}",
                       ablation_name=None, horizon_days=horizon)
    return result, pnl_df


def run_all(events_df: pd.DataFrame, horizon: int = 5, use_excess: bool = True,
            seed: int = 0) -> list[BacktestResult]:
    """Run every ablation and every baseline, return combined list of results."""
    results: list[BacktestResult] = []
    for name in ABLATION_BUNDLES:
        r, _ = run_ablation(events_df, name, horizon=horizon, use_excess=use_excess, seed=seed)
        results.append(r)
    for name in BASELINES:
        r, _ = run_baseline(events_df, name, horizon=horizon, use_excess=use_excess)
        results.append(r)
    return results


def results_to_frame(
    results: list[BacktestResult],
    dollars_by_strategy: Optional[dict[str, float]] = None,
) -> pd.DataFrame:
    """Standard result columns; if dollars_by_strategy is passed, append a
    `total_dollars` column joined on the same key shape used by report.py:
    `{strategy_name}__{ablation_name}` for structured runs, `{strategy_name}`
    for baselines."""
    df = pd.DataFrame([r.model_dump() for r in results])[
        ["strategy_name", "ablation_name", "n_trades", "sharpe",
         "hit_rate", "avg_return", "max_drawdown", "notes"]
    ]
    if dollars_by_strategy is not None:
        def _key(row):
            if str(row["strategy_name"]).startswith("struct_") and pd.notna(row["ablation_name"]):
                return f"{row['strategy_name']}__{row['ablation_name']}"
            return row["strategy_name"]
        df["total_dollars"] = df.apply(
            lambda r: round(float(dollars_by_strategy.get(_key(r), 0.0)), 2),
            axis=1,
        )
    return df


def main(argv: Optional[list[str]] = None):
    p = argparse.ArgumentParser()
    p.add_argument("--events",    default="events_focal.parquet")
    p.add_argument("--universe",  default="significant",
                   choices=["all", "significant", "focal", "focal_significant"])
    p.add_argument("--ablation",  default=None,
                   help="run only this ablation (default: all)")
    p.add_argument("--baseline",  default=None,
                   help="run only this baseline (default: all)")
    p.add_argument("--horizon",   type=int, default=5)
    p.add_argument("--raw",       action="store_true",
                   help="use raw forward returns (default: SPY-excess)")
    p.add_argument("--write-csv", default=None)
    p.add_argument("--seed",      type=int, default=0)
    p.add_argument("--with-dollars", action="store_true",
                   help="Append a total_dollars column (per-trade notional × cumulative return)")
    p.add_argument("--notional", type=float, default=100_000.0,
                   help="Notional per trade in dollars when --with-dollars is set")
    args = p.parse_args(argv)

    events_df = _load_events(args.events, args.universe, seed=args.seed)
    print(f"Universe: {args.universe}  |  events: {len(events_df):,}  |  horizon: {args.horizon}d"
          f"  |  returns: {'raw' if args.raw else 'SPY-excess'}")

    use_excess = not args.raw
    results: list[BacktestResult] = []
    pnl_dfs: dict[str, pd.DataFrame] = {}

    def _key(r: BacktestResult) -> str:
        if r.strategy_name.startswith("struct_") and r.ablation_name:
            return f"{r.strategy_name}__{r.ablation_name}"
        return r.strategy_name

    def _record(r: BacktestResult, pnl_df: pd.DataFrame) -> None:
        results.append(r)
        pnl_dfs[_key(r)] = pnl_df

    if args.ablation:
        _record(*run_ablation(events_df, args.ablation, horizon=args.horizon,
                              use_excess=use_excess, seed=args.seed))
    if args.baseline:
        _record(*run_baseline(events_df, args.baseline, horizon=args.horizon,
                              use_excess=use_excess))
    if not args.ablation and not args.baseline:
        for name in ABLATION_BUNDLES:
            _record(*run_ablation(events_df, name, horizon=args.horizon,
                                  use_excess=use_excess, seed=args.seed))
        for name in BASELINES:
            _record(*run_baseline(events_df, name, horizon=args.horizon,
                                  use_excess=use_excess))

    if args.with_dollars:
        from backtest.pnl import total_pnl
        dollars_by = {k: total_pnl(df, args.notional) for k, df in pnl_dfs.items()}
        frame = results_to_frame(results, dollars_by_strategy=dollars_by)
    else:
        frame = results_to_frame(results)

    print()
    print(frame.to_string(index=False))

    if args.write_csv:
        out = Path(args.write_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(out, index=False)
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
