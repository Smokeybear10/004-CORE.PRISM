"""
Steps 3 + 5: Attribution, expected-return prediction, coherence evaluation.

Public API:
    - attribute(move, chunks, config) -> Attribution              (Steps 3+4)
    - predict_expected_return(move, chunks) -> float              (used by attribute)
    - check_coherence(attribution) -> CoherenceCheck              (Step 5 gate)

Prompt-iteration rule (mentor, explicit):
    Iterate prompts using the FROZEN test case:
        tests/fixtures/aapl_march2020_expected.json
    Don't swap the test input and the prompt at the same time - you lose the
    ability to isolate what changed. Write down the expected output BEFORE
    running so a regression is obvious.

Foreknowledge defense:
    When constructing the LLM prompt, include a system instruction along the
    lines of: "Reason as an investor AS OF {move.move_date}. You have no
    knowledge of events after this date. Cite only provided evidence."
    This is imperfect (the model's pretraining is post-event) but helps at the
    margin. Mentor said: don't rabbit-hole on this.
"""

from __future__ import annotations

from schema import (
    AblationConfig,
    Attribution,
    CoherenceCheck,
    PriceMove,
    TextChunk,
)


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

    TODO: Claude prompt + structured output (pydantic-ai or raw JSON schema).
    """
    raise NotImplementedError("attribute - implement me")


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
