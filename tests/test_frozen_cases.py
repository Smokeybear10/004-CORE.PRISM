"""
Frozen-case regression test (Stage 2 evaluation harness, item 1).

Runs the placeholder model against every hand-authored fixture in
tests/fixtures/*_expected.json and prints a per-case, per-assertion diff
table. Always passes — this is a REPORT, not a gate.

The CLAUDE.md frozen-case rule (from mentor meeting #1):
    Write the expected answer BEFORE running the model. Don't swap the test
    input and the prompt at the same time — you lose the ability to isolate
    what changed. The diff this test prints is the signal that a prompt or
    schema change broke a case the team had agreed on.

Why loud diff, no fail:
    During hackathon-speed prompt iteration, CI blocking on every
    per-assertion flip is the wrong tool. You want eyes on the table on
    every run without it gating merges. Promote to a hard-fail gate later
    once the attribution model stabilizes.

Placeholder model path:
    model.attribute() currently delegates to backtest.fixtures.generate_attribution.
    That path is deterministic given (ticker, move_date) via seeded RNG,
    uses no network, needs no API key. CI-safe. When the real LLM
    attribution lands and model.attribute() starts calling Anthropic, this
    test keeps working with the same call site — only the underlying source
    of truth changes.

How to read the output (run: pytest -s tests/test_frozen_cases.py -v):

    AMD_2022-10-07  (ablation=+positioning)
        known_cause: FUNDAMENTAL (down): AMD issued a preliminary Q3...
        composite score: 0.417
        dominant_dimension    expected=['demand']        observed=competitive   FAIL
        direction (of dom)    expected=negative          observed=negative      PASS
        move_character        expected=structural        observed=transient     FAIL
        ...

    Every case prints its expected vs observed vs pass/fail. Look at the
    composite score trend across prompt edits. A case that flips from PASS
    to FAIL is the signal you care about.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from backtest import DEFAULT_ABLATIONS
from eval.cases import EvalCase, load_cases
from eval.scorer import ScoreResult, score
from model import attribute
from schema import AblationConfig, Attribution, PriceMove


# Fullest ablation: lowest noise in the stub classifier, matches the demo's
# "all sources on" config, so the placeholder gives the expected fixtures
# their best shot.
FULL_CONFIG: AblationConfig = next(
    c for c in DEFAULT_ABLATIONS if c.name == "+positioning"
)


# ---------- Helpers ----------

def _signed_reaction(direction: Optional[str]) -> float:
    """Derive a reaction_return sign from the fixture's expected direction.

    The placeholder classifier in backtest.fixtures reads return_pct to
    decide structural vs transient. Fixtures don't carry a reaction
    magnitude, so we synthesize one from expected.direction. Magnitude is
    fixed (0.05) — only the sign matters for the seeded classifier.
    """
    if direction == "positive":
        return 0.05
    if direction == "negative":
        return -0.05
    return 0.0


def _signed_zscore(direction: Optional[str]) -> float:
    if direction == "positive":
        return 3.0
    if direction == "negative":
        return -3.0
    return 0.0


def _run_placeholder(case: EvalCase) -> Attribution:
    move = PriceMove(
        ticker=case.ticker,
        move_date=case.move_date,
        return_pct=_signed_reaction(case.expected.direction),
        vol_zscore=_signed_zscore(case.expected.direction),
        is_significant=True,
    )
    # Placeholder doesn't read chunks for classification — empty list is fine.
    return attribute(move, chunks=[], config=FULL_CONFIG)


def _status(ok: Optional[bool]) -> str:
    if ok is None:
        return "SKIP"
    return "PASS" if ok else "FAIL"


def _show(val: Any) -> str:
    if val is None:
        return "(not asserted)"
    if isinstance(val, list):
        return "[" + ", ".join(str(x) for x in val) + "]"
    return str(val)


def _format_diff(case: EvalCase, result: ScoreResult) -> str:
    """Per-case diff table with every assertion and the composite score."""
    lines: list[str] = []
    lines.append("")
    lines.append(f"{case.case_id}  (ablation={result.ablation_name})")
    if case.known_cause:
        trunc = case.known_cause[:120].replace("\n", " ")
        lines.append(f"    known_cause: {trunc}{'...' if len(case.known_cause) > 120 else ''}")
    lines.append(f"    composite score: {result.composite:.3f}")

    rows: list[tuple[str, Any, Any, Optional[bool]]] = [
        ("dominant_dimension",
         case.expected.dominant_dimension,
         result.observed_dominant_dimension,
         result.dim_match),
        ("direction (of dom)",
         case.expected.direction,
         result.observed_direction,
         result.dir_match),
        ("move_character",
         case.expected.move_character,
         result.observed_move_character,
         result.char_match),
        ("fade_or_lean",
         case.expected.fade_or_lean,
         result.observed_fade_or_lean,
         result.fade_lean_match),
        ("must_cite_source_type",
         case.expected.must_cite_source_type,
         result.cited_source_types or None,
         result.cite_source_match),
        ("must_not_be_dominant",
         case.expected.must_not_be_dominant,
         result.observed_dominant_dimension,
         result.must_not_be_dominant_ok),
    ]
    for label, expected, observed, ok in rows:
        lines.append(
            f"    {label:<22} "
            f"expected={_show(expected):<28} "
            f"observed={_show(observed):<22} "
            f"{_status(ok)}"
        )
    if result.notes:
        lines.append("    notes:")
        for n in result.notes:
            lines.append(f"      - {n}")
    return "\n".join(lines)


# Load once at module import so the parametrize ids are stable.
_CASES = load_cases()


# ---------- Tests ----------

def test_at_least_one_frozen_case_exists():
    """Without fixtures the parametrized test below silently runs zero cases
    and 'passes' — catch that specific failure mode so 'all green' actually
    means something."""
    assert len(_CASES) > 0, (
        "No *_expected.json fixtures found in tests/fixtures/. Without "
        "frozen cases, this test provides no regression coverage. Author "
        "at least one (see tests/fixtures/AMD_TEMPLATE_expected.json)."
    )


@pytest.mark.parametrize("case", _CASES, ids=[c.case_id for c in _CASES])
def test_frozen_case_diff(case: EvalCase):
    """Run the placeholder model on this frozen case and print the diff.

    This test always passes. Run with `pytest -s -v` to see the table.
    Read the composite score and per-assertion PASS/FAIL to spot regressions.
    """
    attr = _run_placeholder(case)
    result = score(attr, case.expected, ablation=FULL_CONFIG)
    print(_format_diff(case, result))


def test_frozen_cases_summary():
    """Compact one-row-per-case summary. Run with `pytest -s` to see it.

    Scan this table across prompt edits; drill into any flipped row by
    reading the per-case diff from test_frozen_case_diff above.
    """
    if not _CASES:
        pytest.skip("no fixtures loaded")

    print()
    header = (
        f"{'case_id':<22} {'composite':>9} "
        f"{'dim':>5} {'dir':>5} {'char':>5} {'lean':>5} {'cite':>5} {'veto':>5}"
    )
    print(header)
    print("-" * len(header))

    def _cell(x: Optional[bool]) -> str:
        return "PASS" if x is True else ("FAIL" if x is False else "-")

    composites: list[float] = []
    for case in _CASES:
        attr = _run_placeholder(case)
        result = score(attr, case.expected, ablation=FULL_CONFIG)
        composites.append(result.composite)
        print(
            f"{case.case_id:<22} {result.composite:>9.3f} "
            f"{_cell(result.dim_match):>5} "
            f"{_cell(result.dir_match):>5} "
            f"{_cell(result.char_match):>5} "
            f"{_cell(result.fade_lean_match):>5} "
            f"{_cell(result.cite_source_match):>5} "
            f"{_cell(result.must_not_be_dominant_ok):>5}"
        )
    mean_comp = sum(composites) / len(composites)
    print("-" * len(header))
    print(f"{'MEAN':<22} {mean_comp:>9.3f}")
