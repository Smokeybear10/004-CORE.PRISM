"""
Stage-2 Layer (a): Frozen-case anchor.

Loads hand-written expected attributions from
``tests/fixtures/frozen_attributions.json`` and runs the live model (or a
mockable client) against them, reporting per-case diffs.

Purpose: detect silent regressions from prompt or schema changes. A green
anchor does NOT mean the model is "right" in absolute terms — only that the
cases the team already blessed still pass.

Usage:

    # Fake-client path (used by tests; no API credits)
    from eval.frozen import load_frozen_cases, diff_case
    cases = load_frozen_cases()
    result = diff_case(cases[0], attribution)   # Attribution from anywhere

    # Live-model path (hits the Anthropic API via evidence_provider)
    from eval.frozen import run_frozen_anchor
    report = run_frozen_anchor(evidence_provider=my_joiner, client=my_client)
    report.assert_no_regressions()
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from eval.cases import EvalCase
from eval.config import DEFAULT_CONFIG, ScorerConfig
from eval.scorer import ExpectedAttribution, ScoreResult, score
from schema import AblationConfig, Attribution, JoinedEvidence, SourceType


REPO_ROOT = Path(__file__).resolve().parent.parent
FROZEN_PATH = REPO_ROOT / "tests" / "fixtures" / "frozen_attributions.json"


# ---------- Loading ----------

def load_frozen_cases(path: Path = FROZEN_PATH) -> list[EvalCase]:
    """Parse the consolidated frozen_attributions.json into EvalCase objects."""
    if not path.exists():
        raise FileNotFoundError(
            f"frozen_attributions.json not found at {path}. "
            "Run the harness scaffold or restore the fixture."
        )
    with open(path) as f:
        raw = json.load(f)
    if "cases" not in raw:
        raise ValueError(f"{path} missing top-level 'cases' key")
    cases: list[EvalCase] = []
    for entry in raw["cases"]:
        cases.append(EvalCase(**{k: v for k, v in entry.items() if not k.startswith("_")}))
    cases.sort(key=lambda c: (c.ticker, c.move_date))
    return cases


# ---------- Diff ----------

class FrozenDiff(BaseModel):
    """Diff between a hand-written expected case and a live attribution."""
    case_id: str
    composite: float = Field(ge=0.0, le=1.0)
    passed: bool
    score_result: ScoreResult
    error: Optional[str] = None

    @property
    def regressed(self) -> bool:
        return not self.passed and self.error is None


class FrozenAnchorReport(BaseModel):
    """Aggregate report over every frozen case."""
    timestamp: datetime
    prompt_version: str
    threshold: float
    diffs: list[FrozenDiff]

    @property
    def n_passed(self) -> int:
        return sum(1 for d in self.diffs if d.passed)

    @property
    def n_regressed(self) -> int:
        return sum(1 for d in self.diffs if d.regressed)

    @property
    def n_errored(self) -> int:
        return sum(1 for d in self.diffs if d.error is not None)

    def assert_no_regressions(self) -> None:
        """Raise AssertionError listing every regressed case."""
        bad = [d for d in self.diffs if d.regressed]
        if not bad:
            return
        lines = [f"{len(bad)}/{len(self.diffs)} frozen cases regressed:"]
        for d in bad:
            lines.append(
                f"  - {d.case_id}: composite={d.composite:.2f} (<{self.threshold})"
            )
            for note in d.score_result.notes:
                lines.append(f"      {note}")
        raise AssertionError("\n".join(lines))


def diff_case(
    case: EvalCase,
    attribution: Attribution,
    ablation: Optional[AblationConfig] = None,
    *,
    config: ScorerConfig = DEFAULT_CONFIG,
    threshold: float = 0.67,
) -> FrozenDiff:
    """Score one attribution against one frozen case."""
    ab = ablation or AblationConfig(
        name=attribution.ablation_name or "frozen_anchor",
        sources=list(attribution.sources_used or [SourceType.NEWS]),
    )
    sr = score(attribution, case.expected, ab, config)
    return FrozenDiff(
        case_id=case.case_id,
        composite=sr.composite,
        passed=sr.composite >= threshold,
        score_result=sr,
    )


# ---------- Live runner ----------

EvidenceProvider = Callable[[str, date], JoinedEvidence]


@dataclass
class FrozenRunnerOptions:
    threshold: float = 0.67
    prompt_version: str = "dev"
    ablation_name: str = "frozen_anchor"
    scorer_config: ScorerConfig = None  # filled in __post_init__

    def __post_init__(self) -> None:
        if self.scorer_config is None:
            self.scorer_config = DEFAULT_CONFIG


def run_frozen_anchor(
    evidence_provider: EvidenceProvider,
    *,
    client: Any | None = None,
    cases: Optional[list[EvalCase]] = None,
    options: Optional[FrozenRunnerOptions] = None,
) -> FrozenAnchorReport:
    """
    Run the live attribution model on every frozen case via `evidence_provider`
    and report a diff. Errors inside one case do not abort the rest — they land
    as `FrozenDiff.error`.
    """
    from model.attribution import run_attribution

    options = options or FrozenRunnerOptions()
    cases = cases or load_frozen_cases()
    diffs: list[FrozenDiff] = []
    for case in cases:
        try:
            evidence = evidence_provider(case.ticker, case.move_date)
            attribution = run_attribution(
                evidence,
                ablation_name=options.ablation_name,
                client=client,
            )
            diff = diff_case(
                case,
                attribution,
                threshold=options.threshold,
                config=options.scorer_config,
            )
        except Exception as e:
            diff = FrozenDiff(
                case_id=case.case_id,
                composite=0.0,
                passed=False,
                score_result=ScoreResult(
                    case_id=case.case_id,
                    ablation_name=options.ablation_name,
                    composite=0.0,
                    notes=[f"{type(e).__name__}: {e}"],
                ),
                error=f"{type(e).__name__}: {e}",
            )
        diffs.append(diff)
    return FrozenAnchorReport(
        timestamp=datetime.now(),
        prompt_version=options.prompt_version,
        threshold=options.threshold,
        diffs=diffs,
    )
