"""
CLI entry point.

Usage:
    python -m eval                               # all cases × all ablations
    python -m eval --tickers AMD
    python -m eval --cases AMD_2022-10-06
    python -m eval --ablations base_news,+sec
    python -m eval --no-cache
    python -m eval --report demo/eval_report.json

Exit code is 0 unless the run itself crashed. Per-cell errors land in the
report as `records[*].error`; they do not fail the CLI. This is a measurement
tool, not a gate.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from eval.cases import load_cases
from eval.config import ScorerConfig
from eval.runner import EvalReport, RunnerOptions, run_matrix


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = REPO_ROOT / "demo" / "eval_report.json"


def _resolve_prompt_version(override: Optional[str]) -> str:
    if override:
        return override
    # Try to pull a PROMPT_VERSION constant from model/; fall back to 'dev'.
    try:
        from model import PROMPT_VERSION  # type: ignore
        return str(PROMPT_VERSION)
    except (ImportError, AttributeError):
        return "dev"


def _resolve_ablations(names: Optional[list[str]]):
    from backtest import DEFAULT_ABLATIONS
    if not names:
        return DEFAULT_ABLATIONS
    wanted = set(names)
    picked = [a for a in DEFAULT_ABLATIONS if a.name in wanted]
    missing = wanted - {a.name for a in picked}
    if missing:
        raise SystemExit(
            f"Unknown ablation name(s): {sorted(missing)}. "
            f"Known: {[a.name for a in DEFAULT_ABLATIONS]}"
        )
    return picked


def _print_summary(report: EvalReport) -> None:
    print(f"\nprompt_version: {report.prompt_version}")
    print(f"cases: {report.n_cases}   ablations: {report.n_ablations}")
    print(f"total cells: {len(report.records)}")
    errs = [r for r in report.records if r.error]
    if errs:
        print(f"errors: {len(errs)} cells (see report JSON for detail)")

    print("\nComposite score by ablation (higher is better):")
    for name, mean in report.composite_by_ablation.items():
        dim_acc = report.dim_accuracy_by_ablation.get(name)
        dim_str = f"  dim_acc={dim_acc:.2f}" if dim_acc is not None else ""
        print(f"  {name:<16} composite={mean:.3f}{dim_str}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval")
    parser.add_argument("--tickers", type=str, default=None,
                        help="Comma-separated ticker filter, e.g. 'AMD' or 'AMD,AAPL'")
    parser.add_argument("--cases", type=str, default=None,
                        help="Comma-separated case_ids, e.g. 'AMD_2022-10-06'")
    parser.add_argument("--ablations", type=str, default=None,
                        help=f"Comma-separated ablation names. "
                             f"Default: all of DEFAULT_ABLATIONS.")
    parser.add_argument("--no-cache", action="store_true",
                        help="Bypass disk cache; force re-calling the model.")
    parser.add_argument("--prompt-version", type=str, default=None,
                        help="Override prompt version tag (otherwise reads model.PROMPT_VERSION).")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH,
                        help=f"Output path for the JSON report. Default: {DEFAULT_REPORT_PATH}")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else None
    case_ids = [c.strip() for c in args.cases.split(",")] if args.cases else None
    ablation_names = [a.strip() for a in args.ablations.split(",")] if args.ablations else None

    cases = load_cases(tickers=tickers, case_ids=case_ids)
    if not cases:
        print(
            "No cases loaded. Add fixture files to tests/fixtures/"
            "<TICKER>_<YYYY-MM-DD>_expected.json",
            file=sys.stderr,
        )
        return 0

    ablations = _resolve_ablations(ablation_names)

    options = RunnerOptions(
        use_cache=not args.no_cache,
        prompt_version=_resolve_prompt_version(args.prompt_version),
        scorer_config=ScorerConfig(),
    )

    report = run_matrix(cases=cases, ablations=ablations, options=options)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with open(args.report, "w") as f:
        f.write(report.model_dump_json(indent=2))

    _print_summary(report)
    print(f"\nwrote: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
