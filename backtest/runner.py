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
            seed: int = 0,
            strategy: str = "fundamental_vs_nonfundamental") -> list[BacktestResult]:
    """Run every ablation and every baseline, return combined list of results.

    `strategy` selects the fade-or-follow framework for structured ablations:
    one of `STRATEGY_REGISTRY`'s keys (see backtest/frameworks.py for the
    additions). Baselines are framework-independent.
    """
    results: list[BacktestResult] = []
    for name in ABLATION_BUNDLES:
        r, _ = run_ablation(events_df, name, horizon=horizon, use_excess=use_excess,
                            strategy=strategy, seed=seed)
        results.append(r)
    for name in BASELINES:
        r, _ = run_baseline(events_df, name, horizon=horizon, use_excess=use_excess)
        results.append(r)
    return results


def results_to_frame(results: list[BacktestResult]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in results])[
        ["strategy_name", "ablation_name", "n_trades", "sharpe",
         "hit_rate", "avg_return", "max_drawdown", "notes"]
    ]


def main(argv: Optional[list[str]] = None):
    from backtest.signal import STRATEGY_REGISTRY
    p = argparse.ArgumentParser()
    p.add_argument("--events",    default="events_focal.parquet")
    p.add_argument("--universe",  default="significant",
                   choices=["all", "significant", "focal", "focal_significant"])
    p.add_argument("--ablation",  default=None,
                   help="run only this ablation (default: all)")
    p.add_argument("--baseline",  default=None,
                   help="run only this baseline (default: all)")
    p.add_argument("--strategy",  default="fundamental_vs_nonfundamental",
                   choices=sorted(STRATEGY_REGISTRY),
                   help="fade-or-follow framework for structured ablations "
                        "(does not affect baselines)")
    p.add_argument("--horizon",   type=int, default=5)
    p.add_argument("--raw",       action="store_true",
                   help="use raw forward returns (default: SPY-excess)")
    p.add_argument("--write-csv", default=None)
    p.add_argument("--seed",      type=int, default=0)
    args = p.parse_args(argv)

    events_df = _load_events(args.events, args.universe, seed=args.seed)
    print(f"Universe: {args.universe}  |  events: {len(events_df):,}  |  horizon: {args.horizon}d"
          f"  |  returns: {'raw' if args.raw else 'SPY-excess'}  "
          f"|  strategy: {args.strategy}")

    use_excess = not args.raw
    results: list[BacktestResult] = []

    if args.ablation:
        r, _ = run_ablation(events_df, args.ablation, horizon=args.horizon,
                            use_excess=use_excess, strategy=args.strategy,
                            seed=args.seed)
        results.append(r)
    if args.baseline:
        r, _ = run_baseline(events_df, args.baseline, horizon=args.horizon, use_excess=use_excess)
        results.append(r)
    if not args.ablation and not args.baseline:
        results = run_all(events_df, horizon=args.horizon, use_excess=use_excess,
                          seed=args.seed, strategy=args.strategy)

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
