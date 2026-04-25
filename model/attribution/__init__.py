"""
LLM attribution layer (Steps 3 + 5 of the mentor pipeline).

Pipeline:
    evidence (JoinedEvidence)
        -> run_attribution    -> Attribution
        -> validate_attribution -> raises AttributionValidationError on issues
        -> check_coherence    -> CoherenceCheck

The attribution call is structured via an Anthropic `tool_use` block named
`emit_attribution`, so every field the model emits is parsed against a
deterministic schema. Runner auto-fills the fields it already knows from the
input (`ticker`, `move_date`, `return_pct`, `ablation_name`, `sources_used`,
`chunks_considered`).

End-to-end example:
    from ingestion.prices import detect_significant_moves
    from ingestion.events import join_evidence   # teammate's module
    from model.attribution import run_attribution, check_coherence
    from model.attribution.validate import validate_attribution, AttributionValidationError

    moves = detect_significant_moves(prices_df)
    for move in moves:
        evidence = join_evidence(move)                       # -> JoinedEvidence
        attribution = run_attribution(evidence, "full")      # validates by default
        coherence = check_coherence(attribution, evidence)
        if not coherence.plausible:
            log.warning("coherence failed for %s on %s: %s",
                        attribution.ticker, attribution.move_date, coherence.issues)

Testing:
    Both `run_attribution` and `check_coherence` accept `client=...`, so tests
    pass a stand-in object whose `.messages.create()` returns a pre-canned
    response carrying a `tool_use` content block. No API key required.
    See tests/test_attribution.py for the stub pattern.

The live round-trip test (env `RUN_LIVE_API=1`) hits the Anthropic API with
the AAPL fixture; skipped by default so CI does not need an API key.
"""
from __future__ import annotations

# Load repo-root .env once so ANTHROPIC_API_KEY is visible to the SDK
# regardless of how the process was started (pytest, demo CLI, eval CLI,
# direct python -c, ...). Idempotent — load_dotenv won't overwrite vars
# that are already set in the shell.
try:
    from dotenv import load_dotenv as _load_dotenv
    from pathlib import Path as _Path
    _env_path = _Path(__file__).resolve().parents[2] / ".env"
    if _env_path.exists():
        _load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv missing — fall back to whatever the shell set

from model.attribution.coherence import check_coherence
from model.attribution.run import run_attribution, run_attribution_batch
from model.attribution.validate import (
    AttributionValidationError,
    validate_attribution,
)

__all__ = [
    "run_attribution",
    "run_attribution_batch",
    "check_coherence",
    "validate_attribution",
    "AttributionValidationError",
]
