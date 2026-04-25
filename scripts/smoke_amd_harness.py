"""
Manual smoke: run every Stage-2 harness layer against AMD.

Usage:
    python3 scripts/smoke_amd_harness.py

Simulates the production path with a deterministic fake Anthropic client
(no API credits), prints per-layer PASS/FAIL, exits non-zero on any failure.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.fixtures import (
    generate_attributions_for_events,
    make_synthetic_events_df,
)
from eval import (
    check_distribution,
    compare_to_baselines,
    load_frozen_cases,
    run_frozen_anchor,
    run_perturbation_suite,
)
from eval.frozen import FrozenRunnerOptions
from schema import JoinedEvidence, PriceMove, SourceType, TextChunk

# Inlined to avoid pulling anthropic into the top-level import path for any
# reader browsing this file. Must track model.attribution.prompt constant.
ATTRIBUTION_TOOL_NAME = "emit_attribution"


# ---------- Fake evidence provider + fake Anthropic client ----------

def amd_evidence(ticker: str, move_date: date) -> JoinedEvidence:
    """Hand-built evidence: two news chunks attributing the move cleanly."""
    chunks = [
        TextChunk(
            chunk_id=f"news_{ticker}_{move_date.isoformat()}_h_001",
            ticker=ticker,
            source_type=SourceType.NEWS,
            publication_date=move_date,
            section_name="headline",
            text=f"AMD announced material news on {move_date.isoformat()}.",
            token_count=10,
        ),
        TextChunk(
            chunk_id=f"news_{ticker}_{move_date.isoformat()}_h_002",
            ticker=ticker,
            source_type=SourceType.NEWS,
            publication_date=move_date,
            section_name="body",
            text="Analysts reacted to the news with updated models.",
            token_count=10,
        ),
    ]
    return JoinedEvidence(
        move=PriceMove(
            ticker=ticker,
            move_date=move_date,
            return_pct=-0.05 if move_date.year == 2022 else 0.10,
            vol_zscore=-3.2 if move_date.year == 2022 else 3.8,
            is_significant=True,
        ),
        window_start=move_date,
        window_end=move_date,
        events=[],
        text_chunks=chunks,
    )


def _tool_use(tool_input: dict):
    block = SimpleNamespace(type="tool_use", name=ATTRIBUTION_TOOL_NAME, input=tool_input)
    return SimpleNamespace(content=[block], stop_reason="tool_use")


def _attribution_input_matching_expected(
    evidence: JoinedEvidence,
    *,
    dominant: str,
    direction: str,
    move_character: str,
) -> dict:
    """Produce a tool_use payload that would match the case's expected block."""
    cid = evidence.text_chunks[0].chunk_id
    dims = {
        name: {
            "weight": 0.7 if name == dominant else 0.075,
            "direction": direction if name == dominant else "neutral",
            "rationale": f"{name} rationale grounded in provided evidence.",
            "evidence_chunk_ids": [cid],
        }
        for name in ("demand", "pricing", "competitive", "management_credibility", "macro")
    }
    return {
        **dims,
        "move_character": move_character,
        "confidence": 0.82,
        "predicted_return_pct": evidence.move.return_pct,
        "model_notes": "smoke: programmed to match the frozen expected block.",
    }


class ProgrammableClient:
    """Fake Anthropic client that returns canned responses in order."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("no more canned responses")
        return self._responses.pop(0)


# ---------- Layer (a): frozen anchor on AMD ----------

def smoke_layer_a() -> bool:
    print("\n[Layer a] Frozen-case anchor — AMD")
    cases = load_frozen_cases()
    amd_cases = [c for c in cases if c.ticker == "AMD"]
    print(f"  loaded {len(amd_cases)} AMD case(s): {[c.case_id for c in amd_cases]}")

    canned = []
    for case in amd_cases:
        exp = case.expected
        canned.append(_tool_use(_attribution_input_matching_expected(
            amd_evidence(case.ticker, case.move_date),
            dominant=(exp.dominant_dimension or ["demand"])[0],
            direction=exp.direction or "neutral",
            move_character=exp.move_character or "mixed",
        )))
    client = ProgrammableClient(canned)

    report = run_frozen_anchor(
        evidence_provider=amd_evidence,
        client=client,
        cases=amd_cases,
        options=FrozenRunnerOptions(threshold=0.67, prompt_version="smoke-amd"),
    )
    for d in report.diffs:
        flag = "PASS" if d.passed else "FAIL"
        print(f"  {flag}  {d.case_id}  composite={d.composite:.3f}  "
              f"dim_match={d.score_result.dim_match}  "
              f"char_match={d.score_result.char_match}")
        if d.error:
            print(f"        error: {d.error}")
    ok = report.n_regressed == 0 and report.n_errored == 0
    print(f"  summary: passed={report.n_passed}/{len(report.diffs)}  "
          f"regressed={report.n_regressed}  errored={report.n_errored}")
    return ok


# ---------- Layer (b): calibration floor on AMD-only events ----------

def smoke_layer_b() -> bool:
    print("\n[Layer b] Calibration floor — AMD-only synthetic events")
    # make_synthetic_events_df produces multiple tickers; filter to AMD.
    events = make_synthetic_events_df(n=80, seed=0)
    amd_events = events[events["ticker"] == "AMD"].reset_index(drop=True)
    print(f"  AMD events: {len(amd_events)}")
    report = compare_to_baselines(
        amd_events,
        ablations=["base_news", "+macro", "+positioning"],
        metric="sharpe",
        margin_required=0.0,
    )
    for fc in report.floor_checks:
        flag = "PASS" if fc.passed else "FAIL"
        print(f"  {flag}  {fc.ablation_name:<14} vs {fc.baseline_name:<20} "
              f"structured={fc.structured_value:+.3f}  "
              f"baseline={fc.baseline_value:+.3f}  delta={fc.delta:+.3f}")
    if report.failures:
        reasons = {fc.reason for fc in report.failures if fc.reason}
        for r in reasons:
            print(f"    reason: {r}")
    return report.passed


# ---------- Layer (c): perturbation on one AMD event ----------

def smoke_layer_c() -> bool:
    print("\n[Layer c] Perturbation suite — AMD 2022-10-07")
    evidence = amd_evidence("AMD", date(2022, 10, 7))

    # Programmable fake runner: deterministic, order-invariant, ignores junk.
    def fake_run(ev: JoinedEvidence):
        from schema import Attribution, DimensionScore
        # Stable answer regardless of chunk order / junk — proving the runner
        # isn't contributing noise of its own in this smoke.
        dims = {}
        for name in ("demand", "pricing", "competitive", "management_credibility", "macro"):
            dims[name] = DimensionScore(
                weight=0.7 if name == "demand" else 0.075,
                direction="negative" if name == "demand" else "neutral",
                rationale=f"{name} rationale",
                evidence_chunk_ids=[ev.text_chunks[0].chunk_id],
            )
        return Attribution(
            ticker=ev.move.ticker,
            move_date=ev.move.move_date,
            return_pct=ev.move.return_pct,
            predicted_return_pct=ev.move.return_pct,
            **dims,
            move_character="structural",
            confidence=0.8,
            ablation_name="smoke",
            sources_used=[SourceType.NEWS],
            chunks_considered=len(ev.text_chunks),
        )

    report = run_perturbation_suite(evidence, fake_run)
    for name, result in (("shuffle", report.shuffle),
                         ("junk", report.junk),
                         ("determinism", report.determinism)):
        flag = "PASS" if result.passed else "FAIL"
        reason = f"  ({result.reason})" if not result.passed and result.reason else ""
        print(f"  {flag}  {name}{reason}")
    return report.passed


# ---------- Layer (d): distributional sanity on AMD-only attributions ----------

def smoke_layer_d() -> bool:
    print("\n[Layer d] Distributional sanity — AMD-only attributions")
    events = make_synthetic_events_df(n=80, seed=0)
    amd_events = events[events["ticker"] == "AMD"].reset_index(drop=True)
    attrs = generate_attributions_for_events(amd_events, ablation_name="+macro", seed=0)
    print(f"  AMD attributions: {len(attrs)}")
    report = check_distribution(attrs, collapse_rate_max=0.95, min_coverage_per_bucket=3)
    o = report.overall
    print(f"  overall: structural={o.structural_pct:.1%}  transient={o.transient_pct:.1%}  "
          f"mixed={o.mixed_pct:.1%}  unclear={o.unclear_pct:.1%}")
    coverage_flags = [f for f in report.failures if f.kind == "coverage"]
    collapse_flags = [f for f in report.failures if f.kind == "collapse"]
    print(f"  flags: collapse={len(collapse_flags)}  coverage={len(coverage_flags)}")
    # Collapse is the hard-fail condition for this layer; coverage is advisory
    # on a small AMD-only slice (expected to fire, informational).
    return len(collapse_flags) == 0


# ---------- Main ----------

def main() -> int:
    print("=" * 60)
    print("Stage-2 harness smoke — ticker: AMD")
    print("=" * 60)
    results = {
        "a_frozen": smoke_layer_a(),
        "b_calibration": smoke_layer_b(),
        "c_perturbation": smoke_layer_c(),
        "d_distribution": smoke_layer_d(),
    }
    print("\n" + "=" * 60)
    print("Summary:")
    for k, v in results.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print("=" * 60)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
