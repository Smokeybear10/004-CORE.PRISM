"""
Load evaluation cases from tests/fixtures/*_expected.json.

Fixture filename convention (from CLAUDE.md):
    tests/fixtures/<TICKER>_<YYYY-MM-DD>_expected.json

Fixture file shape (all `expected.*` keys optional):
    {
      "ticker": "AMD",
      "move_date": "2022-10-06",
      "known_cause": "One-line team-defensible explanation of the move.",
      "expected": {
        "dominant_dimension": ["demand"],
        "direction": "negative",
        "move_character": "structural",
        "fade_or_lean": "fade",
        "must_cite_source_type": ["news", "sec_10q"],
        "must_not_be_dominant": ["macro"]
      }
    }
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from eval.scorer import ExpectedAttribution


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


class EvalCase(BaseModel):
    ticker: str
    move_date: date
    known_cause: Optional[str] = None
    expected: ExpectedAttribution

    @property
    def case_id(self) -> str:
        return f"{self.ticker}_{self.move_date.isoformat()}"

    @property
    def source_path(self) -> Optional[Path]:
        return getattr(self, "_source_path", None)


def _is_expected_fixture(path: Path) -> bool:
    """Only files matching *_expected.json. Skip TEMPLATE files and samples."""
    if not path.name.endswith("_expected.json"):
        return False
    if "TEMPLATE" in path.name:
        return False
    return True


def load_cases(
    tickers: Optional[list[str]] = None,
    case_ids: Optional[list[str]] = None,
    fixtures_dir: Path = FIXTURES_DIR,
) -> list[EvalCase]:
    """
    Load all fixture cases. Optional filters:
        tickers   — only include cases whose ticker is in this list
        case_ids  — only include cases whose f"{ticker}_{move_date}" matches

    Ordering is deterministic: sorted by (ticker, move_date).
    """
    cases: list[EvalCase] = []
    if not fixtures_dir.exists():
        return cases

    for path in sorted(fixtures_dir.glob("*_expected.json")):
        if not _is_expected_fixture(path):
            continue
        with open(path) as f:
            raw = json.load(f)
        case = EvalCase(**raw)
        if tickers and case.ticker not in tickers:
            continue
        if case_ids and case.case_id not in case_ids:
            continue
        # stash source path for debugging; model_config allows extra attrs
        object.__setattr__(case, "_source_path", path)
        cases.append(case)

    cases.sort(key=lambda c: (c.ticker, c.move_date))
    return cases


def load_case_by_id(case_id: str, fixtures_dir: Path = FIXTURES_DIR) -> EvalCase:
    """Load one case by f'{ticker}_{YYYY-MM-DD}' id. Raises if not found."""
    found = load_cases(case_ids=[case_id], fixtures_dir=fixtures_dir)
    if not found:
        raise FileNotFoundError(
            f"No fixture for case_id={case_id!r} in {fixtures_dir}"
        )
    return found[0]
