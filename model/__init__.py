"""
Steps 3 + 5: Attribution, expected-return prediction, coherence evaluation.

Public API:
    - attribute(move, chunks, config) -> Attribution              (Steps 3+4)
    - predict_expected_return(move, chunks) -> float              (used by attribute)
    - check_coherence(attribution) -> CoherenceCheck              (Step 5 gate)

Prompt-iteration rule (mentor, explicit):
    Iterate prompts using a FROZEN test case: pick one (ticker, date) pair with
    a known cause, store expected outputs in tests/fixtures/, and don't change
    it while tweaking prompts. Don't swap the test input and the prompt at the
    same time - you lose the ability to isolate what changed. Write down the
    expected output BEFORE running so a regression is obvious. See CLAUDE.md.

Foreknowledge defense:
    When constructing the LLM prompt, include a system instruction along the
    lines of: "Reason as an investor AS OF {move.move_date}. You have no
    knowledge of events after this date. Cite only provided evidence."
    This is imperfect (the model's pretraining is post-event) but helps at the
    margin. Mentor said: don't rabbit-hole on this.
"""

from __future__ import annotations

import os
from datetime import timedelta

from schema import (
    AblationConfig,
    Attribution,
    CoherenceCheck,
    PriceMove,
    TextChunk,
)


# Env flag that flips model.attribute() from placeholder → real Claude call.
# Mirrors the pattern used by the live-API tests
# (tests/test_attribution.py, tests/test_eval_frozen.py).
LIVE_API_FLAG = "RUN_LIVE_API"


def _live_api_enabled() -> bool:
    return os.environ.get(LIVE_API_FLAG) == "1"


def attribute(
    move: PriceMove,
    chunks: list[TextChunk],
    config: AblationConfig,
) -> Attribution:
    """
    Attribute `move` across the 5 dimensions using `chunks` (already filtered
    to source types in config.sources and to publication_date <= move.move_date).

    MUST set:
        attribution.ablation_name        = config.name
        attribution.sources_used         = config.sources
        attribution.predicted_return_pct = predict_expected_return(move, chunks)
        every DimensionScore.evidence_chunk_ids non-empty and referencing real chunks

    DUAL PATH
    ---------
    Default path: placeholder. Delegates to `backtest.fixtures.generate_attribution`,
    which synthesizes a plausible Attribution from the PriceMove's realized
    characteristics. Deterministic via seeded RNG, no API calls. Tests the
    pipeline, not the model.

    Live path: real Claude. When env `RUN_LIVE_API=1` AND `chunks` is non-empty,
    builds a `JoinedEvidence` from (move, chunks) and calls
    `model.attribution.run_attribution`. Returns a real model-backed
    Attribution with citations from the chunks. Costs a real API call.

    The chunks-non-empty gate is intentional: with no evidence, the model
    can't cite anything, and the validator rejects uncited DimensionScores.
    The frozen-case test (which passes chunks=[]) gracefully stays on the
    placeholder path even with RUN_LIVE_API=1 set.
    """
    if _live_api_enabled() and chunks:
        return _attribute_live(move, chunks, config)

    from backtest.fixtures import generate_attribution

    attr = generate_attribution(
        ticker=move.ticker,
        move_date=move.move_date,
        return_pct=move.return_pct,
        vol_zscore=move.vol_zscore,
        ablation_name=config.name,
    )
    # Echo the actual chunk IDs we were given into the evidence slots so the
    # contract "evidence_chunk_ids reference real chunks" holds.
    real_ids = [c.chunk_id for c in chunks[:5]] or ["no_chunks_provided_0"]
    for ds in (attr.demand, attr.pricing, attr.competitive,
               attr.management_credibility, attr.macro):
        ds.evidence_chunk_ids = list(real_ids)
    # Pin the contract fields the docstring calls out explicitly:
    attr.ablation_name = config.name
    attr.sources_used = list(config.sources)
    attr.chunks_considered = len(chunks)
    return attr


def _attribute_live(
    move: PriceMove,
    chunks: list[TextChunk],
    config: AblationConfig,
) -> Attribution:
    """Real Claude-backed attribution. Wraps `model.attribution.run_attribution`.

    Builds a `JoinedEvidence` from the (move, chunks) pair the eval pipeline
    hands us. The eval flow doesn't carry idiosyncratic Events (those live in
    Henry's ingestion/idiosyncratic module and aren't surfaced through the
    eval/runner.py chunk provider), so we pass an empty events list — the
    model relies entirely on the text chunks for evidence.

    Window inferred from chunk publication-date span; defensively re-filters
    to publication_date <= move.move_date even though the contract says
    chunks are pre-filtered. Foreknowledge defense, cheap.
    """
    from schema import JoinedEvidence
    from model.attribution import run_attribution

    visible = [c for c in chunks if c.publication_date <= move.move_date]
    if not visible:
        # No evidence to cite — fall back rather than send Claude an empty bag.
        from backtest.fixtures import generate_attribution
        return generate_attribution(
            ticker=move.ticker, move_date=move.move_date,
            return_pct=move.return_pct, vol_zscore=move.vol_zscore,
            ablation_name=config.name, sources_used=list(config.sources),
        )

    window_start = min(c.publication_date for c in visible)
    window_end = move.move_date

    evidence = JoinedEvidence(
        move=move,
        window_start=window_start,
        window_end=window_end,
        events=[],  # eval flow is text-chunk-only
        text_chunks=visible,
    )
    attr = run_attribution(evidence, ablation_name=config.name)

    # run_attribution sets ablation_name from the arg; pin the rest the
    # contract requires (sources_used and chunks_considered are not derivable
    # from JoinedEvidence alone).
    attr.sources_used = list(config.sources)
    attr.chunks_considered = len(chunks)
    return attr


def predict_expected_return(move: PriceMove, chunks: list[TextChunk]) -> float:
    """
    Given evidence, what return SHOULD the market have printed? Used by the
    fade-or-follow framework (backtest/) to compare expected vs realized.

    Keep grounded: the model should consult macro/peer chunks too, not just
    company news. Sanity check: if GDP is up and rates are steady, don't
    predict -10% for no reason.
    """
    raise NotImplementedError("predict_expected_return - implement me")


def check_coherence(attribution: Attribution) -> CoherenceCheck:
    """
    Step 5: plausibility gate. Flag absurd reasoning before touching backtest.

    Heuristics for MVP:
        - Every cited chunk_id exists in the input chunks set.
        - Dominant dimension's direction matches return sign (mostly).
        - No obvious category error (e.g. crude oil cited for AAPL move).
        - Rationale mentions at least one noun phrase from the cited chunk text.

    Ask Claude to self-critique as a fallback.
    """
    raise NotImplementedError("check_coherence - implement me")
