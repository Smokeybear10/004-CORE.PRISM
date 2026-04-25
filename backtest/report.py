"""
Verdict layer: turn a list of BacktestResult into a human-readable
"did we win money" summary.

The CLI table from `backtest.runner` is just numbers. A reviewer reading
`Sharpe 0.42 vs 0.39 vs -0.18 vs 0.55 vs 0.21` has to do the comparison in
their head. This module produces a one-paragraph verdict, a beats/loses
breakdown, and a ranked table — all keyed off a representative "structured"
strategy chosen from across the ablation runs.

Public API:
    pick_structured_representative(results, ablation=None) -> BacktestResult
    compare_strategies(results, ablation=None) -> dict
    verdict_markdown(comparison, notional) -> str
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from schema import BacktestResult


STRUCTURED_PREFIX = "struct_"
BASELINE_PREFIX = "baseline_"


# ── Strategy selection ─────────────────────────────────────────────────────

def _is_structured(r: BacktestResult) -> bool:
    return r.strategy_name.startswith(STRUCTURED_PREFIX)


def _is_baseline(r: BacktestResult) -> bool:
    return r.strategy_name.startswith(BASELINE_PREFIX)


def pick_structured_representative(
    results: list[BacktestResult],
    ablation: Optional[str] = None,
) -> Optional[BacktestResult]:
    """Pick one structured run to compare against baselines.

    If `ablation` is named, return that exact ablation. Otherwise return the
    structured ablation with the highest Sharpe. None if no structured
    results exist.
    """
    structured = [r for r in results if _is_structured(r)]
    if not structured:
        return None
    if ablation is not None:
        match = [r for r in structured if r.ablation_name == ablation]
        if not match:
            raise ValueError(
                f"no structured result for ablation={ablation!r}; "
                f"available: {sorted(r.ablation_name for r in structured)}"
            )
        return match[0]
    return max(structured, key=lambda r: r.sharpe)


# ── Comparison ─────────────────────────────────────────────────────────────

def compare_strategies(
    results: list[BacktestResult],
    ablation: Optional[str] = None,
    dollars_by_strategy: Optional[dict[str, float]] = None,
) -> dict:
    """Rank all strategies by Sharpe; identify what the structured rep beat.

    Args:
        results: every BacktestResult from `backtest.runner.run_all`.
        ablation: which structured ablation to use as the representative.
            Default: pick the highest-Sharpe structured ablation.
        dollars_by_strategy: optional map from `strategy_name` (with
            `_<ablation>` suffix for structured) to total dollar P&L. If
            present, the ranked table includes a `dollars` column and the
            verdict reports a dollar number.

    Returns dict with:
        verdict          str  — one-paragraph human-readable summary
        structured_rank  int  — rank of structured rep (1 = best)
        structured       BacktestResult or None
        beats            list[str] — baselines beaten on Sharpe
        loses_to         list[str] — baselines that beat us
        table            pd.DataFrame — ranked, one row per (strategy_name)
    """
    structured = pick_structured_representative(results, ablation=ablation)
    baselines = [r for r in results if _is_baseline(r)]

    rep_label = (
        f"{structured.strategy_name} ({structured.ablation_name})"
        if structured else "(none)"
    )

    # Build the ranked table over [structured_rep, *baselines].
    rows: list[dict] = []
    if structured is not None:
        rows.append(_row_for(structured, label=rep_label,
                             dollars_by_strategy=dollars_by_strategy))
    for b in baselines:
        rows.append(_row_for(b, label=b.strategy_name.replace(BASELINE_PREFIX, ""),
                             dollars_by_strategy=dollars_by_strategy))

    if rows:
        table = pd.DataFrame(rows).sort_values("sharpe", ascending=False).reset_index(drop=True)
        table.insert(0, "rank", table.index + 1)
    else:
        cols = ["rank", "label", "n_trades", "sharpe", "hit_rate",
                "avg_return", "max_drawdown"]
        if dollars_by_strategy is not None:
            cols.append("dollars")
        table = pd.DataFrame(columns=cols)

    # Structured rank + beats/loses on Sharpe.
    if structured is None:
        return {
            "verdict": "No structured strategy results to evaluate.",
            "structured_rank": None,
            "structured": None,
            "beats": [],
            "loses_to": [],
            "table": table,
        }

    structured_sharpe = structured.sharpe
    beats = [b.strategy_name.replace(BASELINE_PREFIX, "")
             for b in baselines if structured_sharpe > b.sharpe]
    loses_to = [b.strategy_name.replace(BASELINE_PREFIX, "")
                for b in baselines if structured_sharpe <= b.sharpe]

    structured_rank = int(table.loc[table["label"] == rep_label, "rank"].iloc[0])

    structured_dollars: Optional[float] = None
    if dollars_by_strategy is not None:
        key = _dollars_key(structured)
        structured_dollars = dollars_by_strategy.get(key)

    verdict = _format_verdict(
        structured=structured,
        rep_label=rep_label,
        beats=beats,
        loses_to=loses_to,
        structured_rank=structured_rank,
        n_strategies=len(table),
        structured_dollars=structured_dollars,
    )

    return {
        "verdict": verdict,
        "structured_rank": structured_rank,
        "structured": structured,
        "beats": beats,
        "loses_to": loses_to,
        "table": table,
    }


def _dollars_key(r: BacktestResult) -> str:
    """Stable lookup key for dollars_by_strategy. Structured results need an
    ablation suffix; baselines are unique by strategy_name."""
    if _is_structured(r) and r.ablation_name:
        return f"{r.strategy_name}__{r.ablation_name}"
    return r.strategy_name


def _row_for(
    r: BacktestResult,
    label: str,
    dollars_by_strategy: Optional[dict[str, float]],
) -> dict:
    row = {
        "label": label,
        "n_trades": r.n_trades,
        "sharpe": round(float(r.sharpe), 3),
        "hit_rate": round(float(r.hit_rate), 3),
        "avg_return": round(float(r.avg_return), 5),
        "max_drawdown": round(float(r.max_drawdown), 5),
    }
    if dollars_by_strategy is not None:
        row["dollars"] = round(float(dollars_by_strategy.get(_dollars_key(r), 0.0)), 2)
    return row


def _format_verdict(
    structured: BacktestResult,
    rep_label: str,
    beats: list[str],
    loses_to: list[str],
    structured_rank: int,
    n_strategies: int,
    structured_dollars: Optional[float],
) -> str:
    n_baselines = len(beats) + len(loses_to)
    if n_baselines == 0:
        return (
            f"Structured strategy {rep_label} ran on {structured.n_trades} trades "
            f"(Sharpe {structured.sharpe:+.2f}). No baselines were run alongside, "
            "so we can't say whether this beats noise."
        )

    win_word = "beat" if len(beats) >= len(loses_to) else "lost to"
    n_won = len(beats)
    dollar_clause = (
        f" Realized P&L: ${structured_dollars:+,.2f}."
        if structured_dollars is not None else ""
    )
    parts = [
        f"Structured strategy {rep_label} ranked {structured_rank}/{n_strategies} "
        f"(Sharpe {structured.sharpe:+.2f}, hit rate {structured.hit_rate:.0%}, "
        f"{structured.n_trades} trades).{dollar_clause}",
        f"It {win_word} {n_won}/{n_baselines} baselines on Sharpe.",
    ]
    if beats:
        parts.append(f"Beat: {', '.join(beats)}.")
    if loses_to:
        parts.append(f"Lost to: {', '.join(loses_to)}.")
    return " ".join(parts)


# ── Markdown formatting ────────────────────────────────────────────────────

def _df_to_markdown(df: pd.DataFrame) -> str:
    """Render DataFrame as a GitHub-flavored markdown table.

    Inline because pandas.to_markdown requires the optional `tabulate`
    package, and we don't want a hidden dependency for a 12-line render.
    """
    if df.empty:
        return "(no rows)"
    cols = list(df.columns)
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body_lines = []
    for _, row in df.iterrows():
        body_lines.append("| " + " | ".join(str(row[c]) for c in cols) + " |")
    return "\n".join([header, sep, *body_lines])


def verdict_markdown(comparison: dict, notional: float) -> str:
    """Render the verdict + ranked table as a single markdown report."""
    lines: list[str] = []
    lines.append("# Strategy P/L Report — Win or Lose?")
    lines.append("")
    lines.append(f"**Notional per trade:** ${notional:,.0f}")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append(comparison["verdict"])
    lines.append("")
    lines.append("## Ranked strategies (by Sharpe)")
    lines.append("")
    table: pd.DataFrame = comparison["table"]
    if not table.empty:
        lines.append(_df_to_markdown(table))
    else:
        lines.append("(no results)")
    lines.append("")
    if comparison["beats"] or comparison["loses_to"]:
        lines.append("## Head-to-head vs baselines")
        lines.append("")
        if comparison["beats"]:
            lines.append("**Beat on Sharpe:** " + ", ".join(comparison["beats"]))
        if comparison["loses_to"]:
            lines.append("")
            lines.append("**Lost to on Sharpe:** " + ", ".join(comparison["loses_to"]))
        lines.append("")
    return "\n".join(lines)
