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

    Live path: when ANTHROPIC_API_KEY is set, calls `model.attribution.run` to
    get a real Claude attribution with grounded rationales.

    Fallback path: when the key is unset OR the live call raises (network,
    validation, etc.), delegates to `backtest.fixtures.generate_attribution`
    so tests + offline demos still produce a properly-shaped Attribution.
    """
    import os

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _attribute_live(move, chunks, config)
        except Exception as exc:
            # Fall through to placeholder. We surface the failure in
            # model_notes so the UI can show why we degraded.
            attr = _attribute_placeholder(move, chunks, config)
            attr.model_notes = (
                f"live LLM call failed ({type(exc).__name__}: {exc}); "
                f"falling back to placeholder fixture"
            )
            return attr
    return _attribute_placeholder(move, chunks, config)


def _attribute_live(
    move: PriceMove,
    chunks: list[TextChunk],
    config: AblationConfig,
) -> Attribution:
    from datetime import timedelta
    from model.attribution.run import run_attribution
    from schema import JoinedEvidence

    if not chunks:
        # run_attribution would have nothing to cite; placeholder handles this.
        return _attribute_placeholder(move, chunks, config)

    pub_dates = [c.publication_date for c in chunks]
    evidence = JoinedEvidence(
        move=move,
        window_start=min(pub_dates),
        window_end=max(pub_dates) if max(pub_dates) > move.move_date
                   else move.move_date + timedelta(days=0),
        events=[],
        text_chunks=chunks,
        earnings_day=False,
    )
    attr = run_attribution(evidence, ablation_name=config.name, validate=False)
    attr.sources_used = list(config.sources)
    attr.chunks_considered = len(chunks)
    return attr


def _attribute_placeholder(
    move: PriceMove,
    chunks: list[TextChunk],
    config: AblationConfig,
) -> Attribution:
    from backtest.fixtures import generate_attribution

    chunk_sig = "|".join(sorted({c.source_type.value for c in chunks})) or "_empty"
    chunk_sig += f"|n={len(chunks)}"
    chunk_seed = hash((move.ticker, move.move_date.toordinal(), chunk_sig)) & 0x7FFFFFFF

    attr = generate_attribution(
        ticker=move.ticker,
        move_date=move.move_date,
        return_pct=move.return_pct,
        vol_zscore=move.vol_zscore,
        ablation_name=config.name,
        seed=chunk_seed,
        sources_used=list(config.sources),
    )
    real_ids = [c.chunk_id for c in chunks[:5]] or ["no_chunks_provided_0"]
    for ds in (attr.demand, attr.pricing, attr.competitive,
               attr.management_credibility, attr.macro):
        ds.evidence_chunk_ids = list(real_ids)
    attr.ablation_name = config.name
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
