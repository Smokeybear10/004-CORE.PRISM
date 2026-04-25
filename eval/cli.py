"""
CLI entry point.

Default mode (the one the demo cares about): accuracy. Iterates fixtures,
calls the model on each, compares to the fixture's ground-truth verdict,
and prints "X / N cases correct" plus per-case detail.

    python -m eval                              # accuracy on every fixture
    python -m eval --tickers AMD                # accuracy on AMD fixtures
    python -m eval --cases AMD_2022-10-07
    python -m eval --live                       # re-run attribution via API
                                                #   (vs reading the demo bundle)
    python -m eval --report demo/static/data/eval_report.json

Other modes (kept for the harness layers that already exist):

    python -m eval --mode matrix                # ablation × case scoring matrix
                                                #   (the older composite scorer)
    python -m eval --mode harness --harness calibration   # Layer (b) baselines
    python -m eval --mode harness --harness distribution  # Layer (d) sanity
    python -m eval --mode harness --harness frozen        # Layer (a) anchor

Exit code is 0 unless the run itself crashed OR `--gate` was passed and the
selected mode flagged a failure (a missed case in accuracy mode, a baseline
violation in calibration mode, etc.).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from eval.accuracy import (
    AccuracyReport,
    bundled_attribution_loader,
    format_report,
    live_attribution_loader,
    run_accuracy,
    write_report,
)
from eval.cases import load_cases
from eval.config import ScorerConfig
from eval.runner import EvalReport, RunnerOptions, run_matrix


REPO_ROOT = Path(__file__).resolve().parent.parent
# The accuracy report lives next to the per-ticker bundles so the static
# frontend can fetch it with the same relative path it uses for AMD.json.
DEFAULT_ACCURACY_REPORT_PATH = REPO_ROOT / "demo" / "static" / "data" / "eval_report.json"
# Older callers may still write the matrix report to demo/eval_report.json;
# leave that path alone so we don't surprise existing tooling.
DEFAULT_MATRIX_REPORT_PATH = REPO_ROOT / "demo" / "eval_report.json"


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


# ---------- Mode: accuracy (default) ----------

def _run_accuracy_mode(
    tickers: Optional[list[str]],
    case_ids: Optional[list[str]],
    use_live: bool,
    prompt_version: str,
    report_path: Path,
    gate: bool,
) -> int:
    cases = load_cases(tickers=tickers, case_ids=case_ids)
    if not cases:
        print(
            "No cases loaded. Add fixture files to tests/fixtures/"
            "<TICKER>_<YYYY-MM-DD>_expected.json",
            file=sys.stderr,
        )
        return 0

    if use_live:
        loader = live_attribution_loader()
        loader_name = "live"
    else:
        loader = bundled_attribution_loader()
        loader_name = "bundled"

    report = run_accuracy(
        cases,
        attribution_loader=loader,
        prompt_version=prompt_version,
        loader_name=loader_name,
    )
    write_report(report, report_path)
    print(format_report(report))
    print(f"  wrote: {report_path.relative_to(REPO_ROOT) if report_path.is_relative_to(REPO_ROOT) else report_path}")

    if gate and report.primary_n_scored and report.primary_n_correct < report.primary_n_scored:
        return 1
    return 0


# ---------- Mode: matrix (ablation × case composite scoring) ----------

def _print_matrix_summary(report: EvalReport) -> None:
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


def _run_matrix_mode(
    tickers: Optional[list[str]],
    case_ids: Optional[list[str]],
    ablation_names: Optional[list[str]],
    use_cache: bool,
    prompt_version: str,
    report_path: Path,
) -> int:
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
        use_cache=use_cache,
        prompt_version=prompt_version,
        scorer_config=ScorerConfig(),
    )
    report = run_matrix(cases=cases, ablations=ablations, options=options)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report.model_dump_json(indent=2))
    _print_matrix_summary(report)
    print(f"\nwrote: {report_path}")
    return 0


# ---------- Mode: harness (legacy stage-2 layers) ----------

def _run_harness_calibration(gate: bool) -> int:
    from backtest.fixtures import make_synthetic_events_df
    from eval.calibration import compare_to_baselines

    events = make_synthetic_events_df(n=80, seed=0)
    report = compare_to_baselines(events, metric="sharpe", margin_required=0.0)
    print(f"\n[Layer b] Calibration vs baselines (metric={report.metric}, "
          f"margin_required={report.margin_required}):")
    for fc in report.floor_checks:
        flag = "PASS" if fc.passed else "FAIL"
        print(f"  {flag}  {fc.ablation_name:<14} vs {fc.baseline_name:<20} "
              f"structured={fc.structured_value:+.3f}  "
              f"baseline={fc.baseline_value:+.3f}  delta={fc.delta:+.3f}")
    out = REPO_ROOT / "demo" / "calibration_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=2))
    print(f"\nwrote: {out}")
    if not report.passed and gate:
        return 1
    return 0


def _run_harness_distribution(gate: bool) -> int:
    from backtest.fixtures import generate_attributions_for_events, make_synthetic_events_df
    from eval.distribution import check_distribution

    events = make_synthetic_events_df(n=80, seed=0)
    attrs = generate_attributions_for_events(events, ablation_name="+macro", seed=0)
    report = check_distribution(attrs, collapse_rate_max=0.95, min_coverage_per_bucket=3)
    print(f"\n[Layer d] Distributional sanity (n={report.n_attributions}):")
    print(f"  structural={report.overall.structural_pct:.1%}  "
          f"transient={report.overall.transient_pct:.1%}  "
          f"mixed={report.overall.mixed_pct:.1%}  "
          f"unclear={report.overall.unclear_pct:.1%}")
    if report.failures:
        print(f"  {len(report.failures)} flag(s):")
        for f in report.failures:
            bk = f" @ {f.bucket_key!r}" if f.bucket_key else ""
            print(f"    - {f.kind}{bk}: {f.message}")
    else:
        print("  no sanity flags tripped")
    out = REPO_ROOT / "demo" / "distribution_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=2))
    print(f"\nwrote: {out}")
    if not report.passed and gate:
        return 1
    return 0


def _run_harness_frozen() -> int:
    from eval.frozen import load_frozen_cases
    cases = load_frozen_cases()
    print(f"\n[Layer a] Frozen anchor: {len(cases)} case(s) loaded from "
          "tests/fixtures/frozen_attributions.json")
    for c in cases:
        print(f"  - {c.case_id}: {c.known_cause[:80] if c.known_cause else ''}...")
    print("\nLive diff requires an evidence_provider — wire one to run_frozen_anchor() "
          "from your own entry point, or see tests/test_eval_frozen.py for the fake-client "
          "pattern.")
    return 0


# ---------- Main ----------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval")
    parser.add_argument("--mode", type=str, default="accuracy",
                        choices=["accuracy", "matrix", "harness"],
                        help="accuracy: per-strategy X/N (default). "
                             "matrix: composite scoring across ablations × cases. "
                             "harness: stage-2 layers (calibration / distribution / frozen).")
    parser.add_argument("--harness", type=str, default=None,
                        choices=["calibration", "distribution", "frozen"],
                        help="With --mode harness, pick which layer to run.")
    parser.add_argument("--live", action="store_true",
                        help="Accuracy mode: re-run attribution via the live "
                             "model.attribute pipeline instead of reading "
                             "demo/static/data/<TICKER>.json. Use after a "
                             "prompt change. Set BW_USE_LIVE_ATTRIBUTION=1 + "
                             "ANTHROPIC_API_KEY to actually hit the API.")
    parser.add_argument("--gate", action="store_true",
                        help="Exit 1 if the chosen mode flags a failure "
                             "(missed cases in accuracy mode, baseline "
                             "violations in calibration mode, etc.).")
    parser.add_argument("--tickers", type=str, default=None,
                        help="Comma-separated ticker filter, e.g. 'AMD' or 'AMD,AAPL'")
    parser.add_argument("--cases", type=str, default=None,
                        help="Comma-separated case_ids, e.g. 'AMD_2022-10-07'")
    parser.add_argument("--ablations", type=str, default=None,
                        help="Matrix mode only: comma-separated ablation names. "
                             "Default: all of DEFAULT_ABLATIONS.")
    parser.add_argument("--no-cache", action="store_true",
                        help="Matrix mode: bypass disk cache; force re-call.")
    parser.add_argument("--prompt-version", type=str, default=None,
                        help="Override prompt version tag (otherwise reads model.PROMPT_VERSION).")
    parser.add_argument("--report", type=Path, default=None,
                        help="Output path for the JSON report. Default depends on mode: "
                             f"accuracy → {DEFAULT_ACCURACY_REPORT_PATH.relative_to(REPO_ROOT)}, "
                             f"matrix → {DEFAULT_MATRIX_REPORT_PATH.relative_to(REPO_ROOT)}.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else None
    case_ids = [c.strip() for c in args.cases.split(",")] if args.cases else None
    prompt_version = _resolve_prompt_version(args.prompt_version)

    if args.mode == "harness":
        if args.harness == "calibration":
            return _run_harness_calibration(gate=args.gate)
        if args.harness == "distribution":
            return _run_harness_distribution(gate=args.gate)
        if args.harness == "frozen":
            return _run_harness_frozen()
        raise SystemExit("--mode harness requires --harness {calibration|distribution|frozen}")

    if args.mode == "matrix":
        report_path = args.report or DEFAULT_MATRIX_REPORT_PATH
        ablation_names = (
            [a.strip() for a in args.ablations.split(",")] if args.ablations else None
        )
        return _run_matrix_mode(
            tickers=tickers,
            case_ids=case_ids,
            ablation_names=ablation_names,
            use_cache=not args.no_cache,
            prompt_version=prompt_version,
            report_path=report_path,
        )

    # Default: accuracy
    report_path = args.report or DEFAULT_ACCURACY_REPORT_PATH
    return _run_accuracy_mode(
        tickers=tickers,
        case_ids=case_ids,
        use_live=args.live,
        prompt_version=prompt_version,
        report_path=report_path,
        gate=args.gate,
    )


if __name__ == "__main__":
    raise SystemExit(main())
