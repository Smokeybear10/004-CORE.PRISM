"""
One-command ablation chart regenerator.

    python -m demo.ablation_chart
    python -m demo.ablation_chart --metric hit_rate --write out/ablation.png

Produces a horizontal bar chart comparing the 7 ablation configurations
(base_news, +sec, +earnings, +peer_news, +sector_news, +macro, +positioning)
plus the 4 mandated baselines, on one metric (default: Sharpe ratio).

Works with placeholder attributions today; the same chart regenerates
automatically once the real Attribution module lands.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from backtest.runner import _load_events, run_all, results_to_frame


METRIC_LABELS = {
    "sharpe":       "Annualized Sharpe",
    "hit_rate":     "Hit rate (fraction positive)",
    "avg_return":   "Average P&L per trade",
    "max_drawdown": "Max drawdown",
}


def build_chart(results_df: pd.DataFrame, metric: str = "sharpe") -> plt.Figure:
    df = results_df.copy()
    # Label: ablations use their ablation_name; baselines use the part after "baseline_"
    df["label"] = df.apply(
        lambda r: r["ablation_name"] if pd.notna(r["ablation_name"])
                  else r["strategy_name"].replace("baseline_", "baseline: "),
        axis=1,
    )
    # Order: ablations first (in bundle order), then baselines
    ablation_order = ["base_news", "+sec", "+earnings", "+peer_news",
                      "+sector_news", "+macro", "+positioning"]
    rank = {n: i for i, n in enumerate(ablation_order)}
    df["_rank"] = df["label"].map(rank).fillna(100 + df.index.to_series())
    df = df.sort_values("_rank")

    colors = ["#2b7bba" if pd.notna(a) else "#d35400"
              for a in df["ablation_name"]]

    fig, ax = plt.subplots(figsize=(10, max(4, 0.45 * len(df))))
    ax.barh(df["label"], df[metric], color=colors, edgecolor="black", linewidth=0.4)
    ax.axvline(0, color="black", linewidth=0.6)
    ax.set_xlabel(METRIC_LABELS.get(metric, metric))
    ax.set_title(f"Ablation & Baseline Comparison — {METRIC_LABELS.get(metric, metric)}")
    ax.invert_yaxis()
    # Annotate values
    for i, v in enumerate(df[metric].values):
        ax.text(v, i, f"  {v:+.3f}", va="center",
                ha="left" if v >= 0 else "right", fontsize=9)
    # Legend
    from matplotlib.patches import Patch
    ax.legend(handles=[
        Patch(color="#2b7bba", label="structured (ablation)"),
        Patch(color="#d35400", label="baseline"),
    ], loc="lower right", frameon=False)
    fig.tight_layout()
    return fig


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--events",   default="events_focal.parquet")
    p.add_argument("--universe", default="significant",
                   choices=["all", "significant", "focal", "focal_significant"])
    p.add_argument("--metric",   default="sharpe",
                   choices=list(METRIC_LABELS))
    p.add_argument("--horizon",  type=int, default=5)
    p.add_argument("--raw",      action="store_true",
                   help="use raw forward returns (default: SPY-excess)")
    p.add_argument("--seed",     type=int, default=0)
    p.add_argument("--write",    default="out/ablation_chart.png")
    args = p.parse_args()

    events_df = _load_events(args.events, args.universe)
    print(f"Universe: {args.universe}  |  events: {len(events_df):,}")

    results = run_all(events_df, horizon=args.horizon,
                      use_excess=not args.raw, seed=args.seed)
    frame = results_to_frame(results)
    print(frame.to_string(index=False))

    fig = build_chart(frame, metric=args.metric)
    out = Path(args.write)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140, bbox_inches="tight")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
