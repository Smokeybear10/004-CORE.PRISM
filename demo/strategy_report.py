"""
Demo orchestrator: run every structured ablation + every baseline, compute
dollar P&L, write a markdown verdict + equity-curve PNG.

    python -m demo.strategy_report
    python -m demo.strategy_report --events events_focal.parquet --notional 100000
    python -m demo.strategy_report --out /tmp/report --ablation +positioning
    python -m demo.strategy_report --raw                # raw forward returns

Demo-day deliverable: ONE markdown + ONE chart that says:
    "Structured strategy beat 3 of 4 baselines, made $X over N trades."

Design notes:
    - Calls `backtest.runner.run_ablation` and `run_baseline` individually so
      we can keep the per-trade pnl_df for each (run_all discards them).
    - Uses backtest._load_events fallback chain: prefer real parquet at
      args.events, fall back to events_focal_sample.parquet, final fallback
      synthetic. Same chain works on every developer machine.
    - Ablation pick for the "structured representative" defaults to the
      highest-Sharpe ablation. Override with --ablation NAME.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")  # headless: don't try to open a window
import matplotlib.pyplot as plt
import pandas as pd

from backtest.fixtures import ABLATION_BUNDLES
from backtest.baselines import BASELINES
from backtest.pnl import DEFAULT_NOTIONAL, equity_curve, total_pnl
from backtest.report import (
    BASELINE_PREFIX,
    STRUCTURED_PREFIX,
    compare_strategies,
    verdict_markdown,
)
from backtest.runner import _load_events, run_ablation, run_baseline
from schema import BacktestResult


def _dollars_key(r: BacktestResult) -> str:
    """Mirrors backtest.report._dollars_key — keep these in sync."""
    if r.strategy_name.startswith(STRUCTURED_PREFIX) and r.ablation_name:
        return f"{r.strategy_name}__{r.ablation_name}"
    return r.strategy_name


# ── Pipeline ────────────────────────────────────────────────────────────────

def run_every_strategy(
    events_df: pd.DataFrame,
    horizon: int,
    use_excess: bool,
    seed: int,
) -> tuple[list[BacktestResult], dict[str, pd.DataFrame]]:
    """Run all 7 ablations + all 4 baselines. Return (results, pnl_dfs).

    pnl_dfs is keyed by `_dollars_key(result)` so callers can join back.
    """
    results: list[BacktestResult] = []
    pnl_dfs: dict[str, pd.DataFrame] = {}

    for name in ABLATION_BUNDLES:
        r, pnl_df = run_ablation(
            events_df, name, horizon=horizon, use_excess=use_excess, seed=seed,
        )
        results.append(r)
        pnl_dfs[_dollars_key(r)] = pnl_df

    for name in BASELINES:
        r, pnl_df = run_baseline(
            events_df, name, horizon=horizon, use_excess=use_excess,
        )
        results.append(r)
        pnl_dfs[_dollars_key(r)] = pnl_df

    return results, pnl_dfs


def build_equity_curves(
    results: list[BacktestResult],
    pnl_dfs: dict[str, pd.DataFrame],
    events_df: pd.DataFrame,
    notional: float,
) -> dict[str, pd.DataFrame]:
    """Per-strategy equity curve (chronological cumulative dollars)."""
    return {
        _dollars_key(r): equity_curve(pnl_dfs[_dollars_key(r)], notional, events_df)
        for r in results
    }


# ── Chart ──────────────────────────────────────────────────────────────────

def render_equity_chart(
    curves: dict[str, pd.DataFrame],
    results: list[BacktestResult],
    structured_key: Optional[str],
    notional: float,
    out_path: Path,
) -> None:
    """One line per strategy. Structured rep = thick + bold; baselines thin."""
    fig, ax = plt.subplots(figsize=(11, 6))

    by_key = {_dollars_key(r): r for r in results}

    for key, curve in curves.items():
        if curve.empty:
            continue
        r = by_key[key]
        is_structured = r.strategy_name.startswith(STRUCTURED_PREFIX)
        is_rep = key == structured_key

        # x: chronological if entry_date set, else integer index
        if curve["entry_date"].notna().any():
            x = pd.to_datetime(curve["entry_date"])
        else:
            x = curve.index

        if is_rep:
            ax.plot(x, curve["equity"], linewidth=2.6, color="#2b7bba",
                    label=f"STRUCTURED ({r.ablation_name})", zorder=10)
        elif is_structured:
            ax.plot(x, curve["equity"], linewidth=0.8, color="#9bc1de",
                    alpha=0.6, label=None)
        else:
            label = r.strategy_name.replace(BASELINE_PREFIX, "baseline: ")
            ax.plot(x, curve["equity"], linewidth=1.4, alpha=0.85, label=label)

    ax.axhline(notional, color="black", linewidth=0.5, linestyle="--",
               label=f"start (${notional:,.0f})")
    ax.set_ylabel("Equity ($)")
    ax.set_xlabel("Trade entry date")
    ax.set_title("Equity curves: structured strategy vs baselines")
    ax.legend(loc="upper left", fontsize=9, frameon=False)
    ax.grid(True, alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


# ── CLI ────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--events", default="events_focal.parquet",
                   help="Path to events parquet. Falls back to "
                        "tests/fixtures/events_focal_sample.parquet, then "
                        "synthetic if missing.")
    p.add_argument("--universe", default="significant",
                   choices=["all", "significant", "focal", "focal_significant"])
    p.add_argument("--horizon", type=int, default=5)
    p.add_argument("--raw", action="store_true",
                   help="Use raw forward returns (default: SPY-excess)")
    p.add_argument("--notional", type=float, default=DEFAULT_NOTIONAL,
                   help=f"Per-trade notional in dollars (default: {DEFAULT_NOTIONAL:,.0f})")
    p.add_argument("--ablation", default=None,
                   help="Pick a specific ablation as the structured "
                        "representative. Default: highest-Sharpe ablation.")
    p.add_argument("--out", default="out/strategy_report",
                   help="Output directory for report.md + equity_curves.png")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)

    # Load events with the existing fallback chain.
    events_path = args.events
    sample_fallback = "tests/fixtures/events_focal_sample.parquet"
    if not Path(events_path).exists() and Path(sample_fallback).exists():
        print(f"NOTE: {events_path} missing — using {sample_fallback} instead.",
              file=sys.stderr)
        events_path = sample_fallback

    events_df = _load_events(events_path, args.universe, seed=args.seed)
    print(f"Universe: {args.universe}  |  events: {len(events_df):,}  "
          f"|  horizon: {args.horizon}d  "
          f"|  returns: {'raw' if args.raw else 'SPY-excess'}  "
          f"|  notional: ${args.notional:,.0f}")

    # Run everything.
    results, pnl_dfs = run_every_strategy(
        events_df, horizon=args.horizon, use_excess=not args.raw, seed=args.seed,
    )

    # Dollar P&L per strategy.
    dollars_by = {k: total_pnl(df, args.notional) for k, df in pnl_dfs.items()}

    # Verdict.
    comparison = compare_strategies(results, ablation=args.ablation,
                                    dollars_by_strategy=dollars_by)
    structured_key = (
        _dollars_key(comparison["structured"])
        if comparison["structured"] else None
    )

    # Equity curves.
    curves = build_equity_curves(results, pnl_dfs, events_df, args.notional)

    # Outputs.
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_md = out_dir / "report.md"
    chart_png = out_dir / "equity_curves.png"

    report_md.write_text(verdict_markdown(comparison, args.notional))
    render_equity_chart(curves, results, structured_key, args.notional, chart_png)

    print()
    print(comparison["verdict"])
    print()
    print(comparison["table"].to_string(index=False))
    print()
    print(f"Wrote {report_md}")
    print(f"Wrote {chart_png}")


if __name__ == "__main__":
    main()
