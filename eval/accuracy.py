"""
Eval harness — accuracy mode.

This is the *measurement instrument* that the Verdict Console is not.

Purpose
-------
The Verdict Console (demo/static/) is a strategy *explorer*: it shows what
each of the four lean/fade/neutral frameworks decides for a single move.
It has no ground truth and no scoring — it's a demo artifact for talking
about how strategy choice affects a verdict.

This module is the *eval harness*: it loads the hand-authored fixtures in
``tests/fixtures/<TICKER>_<DATE>_expected.json``, runs the model on each,
compares the model's verdict to the fixture's ground-truth verdict, and
emits a report whose headline is one number: "X / N cases correct".

Scoring scope (hard constraint, see CLAUDE.md)
----------------------------------------------
Our AMD fixtures only carry one ground-truth label — fundamental vs
non-fundamental, encoded as ``expected.fade_or_lean`` in the JSON. The
other three frameworks (expected_vs_realized, dimension_weighted, hybrid)
need richer expected labels (predicted move size, dimension weights, etc.)
that no one has authored yet. So this harness scores ONLY the
``fundamental_vs_nonfundamental`` strategy against ground truth. The other
three are still computed and shown in the report but flagged as
``scored=False`` and explicitly explained as exploration-only.

If a future fixture adds an ``expected.predicted_return_pct`` or
``expected.dim_weight_persistence``, drop the corresponding strategy
into ``GROUND_TRUTHED_STRATEGIES`` below — no other change required.

Two attribution loaders (fast and slow path)
--------------------------------------------
1. ``bundled_attribution_loader`` — reads pre-baked Attributions out of
   ``demo/static/data/<TICKER>.json``. This is the loop the mentor asked
   for: re-runs in seconds against cached real model outputs, never hits
   the API.
2. ``live_attribution_loader`` — calls ``demo.real_chunks.chunks_for_real``
   + ``model.attribute`` to produce fresh Attributions. Use after a prompt
   change or when adding a new fixture.

Both produce the same ``Attribution`` shape, so the rest of the harness
doesn't care which path produced the data.

Output
------
``AccuracyReport`` is written to ``demo/static/data/eval_report.json``.
The frontend's Eval Harness panel reads it and renders the X/N headline
plus per-case detail. The CLI also prints the headline so a developer
sees the number without opening the file.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from backtest.signal import STRATEGY_REGISTRY
from eval.cases import EvalCase
from schema import (
    AblationConfig,
    Attribution,
    DimensionScore,
    SourceType,
)

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_DATA_DIR = REPO_ROOT / "demo" / "static" / "data"

# Strategies whose verdicts we currently have ground truth for. Every other
# registered strategy is computed and shown in the report (so the Verdict
# Console comparison is honest end-to-end) but marked unscored. To start
# scoring a new strategy, add a fixture field, teach _expected_for_strategy
# how to read it, and add the strategy name here.
GROUND_TRUTHED_STRATEGIES: tuple[str, ...] = ("fundamental_vs_nonfundamental",)
PRIMARY_STRATEGY: str = "fundamental_vs_nonfundamental"

UNSCORED_NOTE = (
    "Fixtures only carry ground truth for the fundamental-vs-non strategy. "
    "Other strategies are computed and surfaced for comparison but cannot be "
    "scored against ground truth until fixtures add the matching expected "
    "fields (predicted move size, per-dimension persistence, etc.)."
)


# ---------- Result shapes ----------

class CaseAccuracy(BaseModel):
    """One (case × strategy) cell — the unit of the harness's output."""
    case_id: str
    ticker: str
    move_date: date
    known_cause: Optional[str] = None

    expected_verdict: Optional[str] = None  # lean / fade / neutral
    expected_label: Optional[str] = None    # human label, e.g. "fundamental"

    model_verdict: Optional[str] = None     # whatever the strategy returned
    model_label: Optional[str] = None       # human label

    match: Optional[bool] = None            # None when not scored
    scored: bool = False                    # whether this case has ground truth

    # Diagnostic context — useful when a case fails so you don't have to
    # rerun to see what the model saw.
    model_character: Optional[str] = None
    predicted_return: Optional[float] = None
    realized_return: Optional[float] = None
    confidence: Optional[float] = None
    dominant_dimension: Optional[str] = None
    chunks_considered: Optional[int] = None
    sources_used: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class StrategyAccuracy(BaseModel):
    """Per-strategy roll-up across every case."""
    strategy: str
    scored: bool                             # ground truth available?
    n_cases: int = 0
    n_scored: int = 0
    n_correct: int = 0
    accuracy: float = 0.0
    cases: list[CaseAccuracy] = Field(default_factory=list)
    note: Optional[str] = None


class AccuracyReport(BaseModel):
    """The harness's full output. demo/ reads this to render the panel."""
    timestamp: datetime
    prompt_version: str
    universe: list[str]                      # tickers exercised
    n_cases: int
    primary_strategy: str = PRIMARY_STRATEGY
    primary_n_correct: int = 0
    primary_n_scored: int = 0
    primary_accuracy: float = 0.0
    strategies: list[StrategyAccuracy] = Field(default_factory=list)
    unscored_explanation: str = UNSCORED_NOTE
    loader: str = "bundled"                  # which attribution_loader fed it


# ---------- Verdict ↔ label mapping ----------

# strategy_fundamental_vs_nonfundamental maps move_character to a verdict;
# this maps a verdict back to the language the fixtures use ("FUNDAMENTAL"
# vs "NON-FUNDAMENTAL") so the report reads naturally.
VERDICT_TO_LABEL: dict[str, str] = {
    "lean": "fundamental",
    "fade": "non-fundamental",
    "neutral": "unclear",
}


def _label_for(verdict: Optional[str]) -> Optional[str]:
    if verdict is None:
        return None
    return VERDICT_TO_LABEL.get(verdict, verdict)


def _expected_for_strategy(case: EvalCase, strategy: str) -> Optional[str]:
    """
    Return the fixture's ground-truth verdict for ``strategy``, or None if
    no ground truth exists for this (case, strategy) pair.

    Today, only ``fundamental_vs_nonfundamental`` is ground-truthed via
    ``expected.fade_or_lean``. Future strategies plug in here.
    """
    if strategy == "fundamental_vs_nonfundamental":
        return case.expected.fade_or_lean
    return None


# ---------- Attribution loaders ----------

AttributionLoader = Callable[[str, date], Optional[Attribution]]


def _attribution_from_bundle(bundle: dict, move_date: date) -> Optional[Attribution]:
    """Reconstruct an Attribution from a demo/static/data/<TICKER>.json move row.

    The bundle stores a slimmed-down form of the Attribution (no full
    DimensionScore types), so we reassemble the contract pydantic expects.
    Fields not present in the bundle (ablation_name, model_notes) are
    filled with sensible defaults.
    """
    iso = move_date.isoformat()
    move = next((m for m in bundle.get("moves", []) if m.get("move_date") == iso), None)
    if move is None:
        return None
    attr_raw = move.get("attribution") or {}
    dims_raw = attr_raw.get("dimensions") or {}

    def _dim(name: str) -> DimensionScore:
        d = dims_raw.get(name) or {}
        return DimensionScore(
            weight=float(d.get("weight", 0.0)),
            direction=d.get("direction", "neutral"),
            rationale=d.get("rationale", "") or "no rationale",
            evidence_chunk_ids=list(d.get("evidence_chunk_ids", []))
            or ["bundled_no_citation_0"],
        )

    sources = [SourceType(s) for s in attr_raw.get("sources_used", []) if s in SourceType._value2member_map_]
    return Attribution(
        ticker=bundle["ticker"],
        move_date=move_date,
        return_pct=float(attr_raw.get("realized", move.get("return_pct", 0.0))),
        predicted_return_pct=attr_raw.get("predicted"),
        demand=_dim("demand"),
        pricing=_dim("pricing"),
        competitive=_dim("competitive"),
        management_credibility=_dim("management_credibility"),
        macro=_dim("macro"),
        move_character=attr_raw.get("character", "unclear"),
        confidence=float(attr_raw.get("confidence", 0.5)),
        ablation_name=move.get("ablation_name"),
        sources_used=sources,
        chunks_considered=int(attr_raw.get("chunks_considered", 0)),
        model_notes=attr_raw.get("model_notes"),
    )


def bundled_attribution_loader(
    bundle_dir: Path = BUNDLED_DATA_DIR,
) -> AttributionLoader:
    """
    Load Attributions out of pre-baked ``demo/static/data/<TICKER>.json``.

    These are the same Attributions the Verdict Console is showing on the
    page right now. Scoring against this loader answers the question the
    user actually cares about at demo time: "is what the demo says
    correct?".

    Returns a callable so the loader is reusable across runs without
    re-reading the bundle from disk for every (ticker, date) lookup.
    """
    cache: dict[str, Optional[dict]] = {}

    def _load(ticker: str) -> Optional[dict]:
        if ticker not in cache:
            path = bundle_dir / f"{ticker}.json"
            if not path.exists():
                cache[ticker] = None
            else:
                with open(path) as f:
                    cache[ticker] = json.load(f)
        return cache[ticker]

    def loader(ticker: str, move_date: date) -> Optional[Attribution]:
        bundle = _load(ticker)
        if bundle is None:
            return None
        return _attribution_from_bundle(bundle, move_date)

    return loader


def live_attribution_loader(
    ablation: Optional[AblationConfig] = None,
) -> AttributionLoader:
    """
    Run real attribution against the live ingestion pipeline.

    Mirrors what ``demo/build_static.py`` does — pulls evidence via
    ``demo.real_chunks.chunks_for_real`` and calls ``model.attribute``.
    Useful when:
        - A new fixture has been added that the bundle was built before.
        - The prompt has changed and bundled attributions are stale.
        - You want to verify the bundle is consistent with current code.

    Set BW_USE_LIVE_ATTRIBUTION=1 + ANTHROPIC_API_KEY to actually hit the
    API; otherwise model.attribute falls back to its synthetic placeholder
    and the harness will still run end-to-end with a clear note in
    model_notes.
    """
    from demo.real_chunks import chunks_for_real  # local import: heavy parquet
    from model import attribute as model_attribute
    from schema import PriceMove

    cfg = ablation or AblationConfig(
        name="+macro",
        sources=[
            SourceType.NEWS,
            SourceType.SEC_10K,
            SourceType.SEC_8K,
            SourceType.EARNINGS_TRANSCRIPT,
            SourceType.PEER_NEWS,
            SourceType.MACRO,
            SourceType.THIRTEEN_F,
        ],
        description="harness full stack",
    )

    def loader(ticker: str, move_date: date) -> Optional[Attribution]:
        chunks = chunks_for_real(ticker, move_date)
        if not chunks:
            log.warning("live loader: no chunks for %s %s", ticker, move_date)
        # PriceMove is required by the API but the strategy verdicts only
        # depend on the Attribution, so synthesize a stub move record.
        move = PriceMove(
            ticker=ticker,
            move_date=move_date,
            return_pct=0.0,
            vol_zscore=0.0,
            is_significant=True,
        )
        return model_attribute(move, chunks, cfg)

    return loader


# ---------- Core scoring ----------

def _verdict_from_strategy(attr: Attribution, strategy: str) -> Optional[str]:
    """Run a strategy from STRATEGY_REGISTRY and return its verdict, or None
    if the strategy raised — so a single buggy strategy doesn't sink the run."""
    fn = STRATEGY_REGISTRY.get(strategy)
    if fn is None:
        return None
    try:
        return fn(attr)
    except Exception as e:
        log.warning("strategy %s raised on %s %s: %s", strategy, attr.ticker, attr.move_date, e)
        return None


def _dominant_dimension(attr: Attribution) -> Optional[str]:
    dims = {
        "demand": attr.demand.weight,
        "pricing": attr.pricing.weight,
        "competitive": attr.competitive.weight,
        "management_credibility": attr.management_credibility.weight,
        "macro": attr.macro.weight,
    }
    if not dims:
        return None
    return max(dims, key=lambda k: dims[k])


def score_one_case(
    case: EvalCase,
    attr: Optional[Attribution],
    strategy: str,
) -> CaseAccuracy:
    """Score one (case, strategy) cell. Pure function — all I/O has happened
    by the time this is called. Returns a populated CaseAccuracy even on
    error so the report stays uniform."""
    expected = _expected_for_strategy(case, strategy)
    is_scored = expected is not None

    if attr is None:
        return CaseAccuracy(
            case_id=case.case_id,
            ticker=case.ticker,
            move_date=case.move_date,
            known_cause=case.known_cause,
            expected_verdict=expected,
            expected_label=_label_for(expected),
            scored=is_scored,
            error="no attribution available for this (ticker, move_date)",
        )

    model_verdict = _verdict_from_strategy(attr, strategy)
    match = (model_verdict == expected) if (is_scored and model_verdict is not None) else None

    return CaseAccuracy(
        case_id=case.case_id,
        ticker=case.ticker,
        move_date=case.move_date,
        known_cause=case.known_cause,
        expected_verdict=expected,
        expected_label=_label_for(expected),
        model_verdict=model_verdict,
        model_label=_label_for(model_verdict),
        match=match,
        scored=is_scored,
        model_character=attr.move_character,
        predicted_return=attr.predicted_return_pct,
        realized_return=attr.return_pct,
        confidence=attr.confidence,
        dominant_dimension=_dominant_dimension(attr),
        chunks_considered=attr.chunks_considered,
        sources_used=[s.value for s in attr.sources_used],
    )


def _roll_up(strategy: str, cases: list[CaseAccuracy]) -> StrategyAccuracy:
    """Compute the X / N accuracy summary for one strategy across cases."""
    scored = [c for c in cases if c.scored and c.match is not None]
    n_correct = sum(1 for c in scored if c.match is True)
    n_scored = len(scored)
    accuracy = (n_correct / n_scored) if n_scored else 0.0
    is_scored = strategy in GROUND_TRUTHED_STRATEGIES
    note: Optional[str] = None
    if not is_scored:
        note = "exploration-only — no ground-truth label in fixtures"
    elif n_scored == 0:
        note = "no scorable cases: every case missing ground truth or attribution"
    return StrategyAccuracy(
        strategy=strategy,
        scored=is_scored,
        n_cases=len(cases),
        n_scored=n_scored,
        n_correct=n_correct,
        accuracy=round(accuracy, 4),
        cases=cases,
        note=note,
    )


def run_accuracy(
    cases: list[EvalCase],
    *,
    attribution_loader: Optional[AttributionLoader] = None,
    strategies: Optional[list[str]] = None,
    prompt_version: str = "dev",
    loader_name: str = "bundled",
) -> AccuracyReport:
    """
    Iterate every case, look up its Attribution via ``attribution_loader``,
    and score each strategy in ``strategies`` against fixture ground truth.

    Returns an ``AccuracyReport`` whose headline numbers
    (``primary_n_correct`` / ``primary_n_scored`` / ``primary_accuracy``)
    refer to the primary, ground-truthed strategy. The other strategies
    appear in ``strategies[]`` with ``scored=False``.

    The loader is pluggable so tests can pass a fake loader and the CLI
    can swap bundled vs live without touching the runner. Default loader
    is ``bundled_attribution_loader()``.
    """
    if attribution_loader is None:
        attribution_loader = bundled_attribution_loader()
    if strategies is None:
        # Score the primary strategy first; surface the rest for comparison.
        strategies = [PRIMARY_STRATEGY] + [
            s for s in STRATEGY_REGISTRY if s != PRIMARY_STRATEGY
        ]

    # Load every Attribution once per case so each strategy reuses it.
    by_case: list[tuple[EvalCase, Optional[Attribution]]] = []
    for case in cases:
        try:
            attr = attribution_loader(case.ticker, case.move_date)
        except Exception as e:
            log.warning("attribution loader failed on %s: %s", case.case_id, e)
            attr = None
        by_case.append((case, attr))

    strat_results: list[StrategyAccuracy] = []
    for strat in strategies:
        strat_cases = [score_one_case(case, attr, strat) for case, attr in by_case]
        strat_results.append(_roll_up(strat, strat_cases))

    primary = next(
        (s for s in strat_results if s.strategy == PRIMARY_STRATEGY),
        None,
    )
    return AccuracyReport(
        timestamp=datetime.now(),
        prompt_version=prompt_version,
        universe=sorted({c.ticker for c in cases}),
        n_cases=len(cases),
        primary_strategy=PRIMARY_STRATEGY,
        primary_n_correct=primary.n_correct if primary else 0,
        primary_n_scored=primary.n_scored if primary else 0,
        primary_accuracy=primary.accuracy if primary else 0.0,
        strategies=strat_results,
        loader=loader_name,
    )


# ---------- Pretty printing ----------

def _verdict_glyph(match: Optional[bool], scored: bool) -> str:
    if not scored:
        return "·"
    if match is True:
        return "✓"
    if match is False:
        return "✗"
    return "?"


def format_report(report: AccuracyReport) -> str:
    """Single-string CLI summary. Designed for terminal width ~88 cols."""
    lines: list[str] = []
    lines.append("")
    lines.append(f"Eval harness — {report.primary_strategy}")
    lines.append(f"prompt_version={report.prompt_version}  loader={report.loader}  "
                 f"tickers={','.join(report.universe) or '—'}")
    headline = (
        f"{report.primary_n_correct} / {report.primary_n_scored} cases correct"
        f"  ({report.primary_accuracy:.0%})"
        if report.primary_n_scored
        else "no scorable cases (no fixtures with ground truth)"
    )
    lines.append("")
    lines.append(f"  HEADLINE → {headline}")
    lines.append("")

    primary = next(
        (s for s in report.strategies if s.strategy == PRIMARY_STRATEGY),
        None,
    )
    if primary and primary.cases:
        lines.append("  Per-case detail (primary strategy):")
        for c in primary.cases:
            glyph = _verdict_glyph(c.match, c.scored)
            mv = c.model_verdict or "—"
            ev = c.expected_verdict or "—"
            cause = (c.known_cause or "").split(":", 1)[0][:40]
            lines.append(
                f"    {glyph}  {c.case_id:<20}  model={mv:<7} expected={ev:<7}  "
                f"[{cause}]"
            )
        lines.append("")

    lines.append("  Other strategies (exploration-only — no ground truth in fixtures):")
    for s in report.strategies:
        if s.strategy == PRIMARY_STRATEGY:
            continue
        verdicts = [c.model_verdict or "—" for c in s.cases]
        lines.append(f"    {s.strategy:<32} verdicts: {', '.join(verdicts)}")
    lines.append("")
    lines.append("  " + report.unscored_explanation)
    lines.append("")
    return "\n".join(lines)


# ---------- Convenience entry points ----------

def write_report(report: AccuracyReport, path: Path) -> None:
    """Write the report as JSON. Caller is responsible for the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2))
