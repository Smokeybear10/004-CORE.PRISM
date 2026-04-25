"""
Steps 3 + 5: Attribution, expected-return prediction, coherence evaluation.

Public API:
    - attribute(move, chunks, config) -> Attribution              (Steps 3+4)
    - predict_expected_return(move, chunks) -> float              (used by attribute)
    - check_coherence(attribution) -> CoherenceCheck              (Step 5 gate)

`attribute()` is the demo / runner entry point. It will call the real
Claude-backed `model.attribution.run_attribution` when LIVE mode is enabled
(see `_should_use_live` below) and fall back to the synthetic placeholder
otherwise. The returned Attribution always sets `model_notes` to indicate
which path produced it so downstream reports can stay honest about what's
real and what's placeholder.

LIVE mode opt-in:
    export BW_USE_LIVE_ATTRIBUTION=1
    export ANTHROPIC_API_KEY=sk-ant-...

Default is OFF — running the test suite or the synthetic-events backtest
should never silently bill the Anthropic account.

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

import logging
import os
import time
from typing import Optional

from schema import (
    AblationConfig,
    Attribution,
    CoherenceCheck,
    JoinedEvidence,
    PriceMove,
    TextChunk,
)

log = logging.getLogger(__name__)


# ---------- LIVE / placeholder gate ----------

LIVE_ENV_VAR = "BW_USE_LIVE_ATTRIBUTION"
LIVE_NOTE_PREFIX = "live attribution"
PLACEHOLDER_NOTE_PREFIX = "synthetic fixture"

# Rate-limit retry config. The Anthropic SDK retries 429s once internally,
# but for batch jobs (build_static, eval matrix) we want to ride through
# brief cap windows instead of immediately falling back to placeholder.
# The base delay is doubled per attempt (30s, 60s, …) and is capped at
# RATE_LIMIT_MAX_DELAY_S. If the 429 response carries a Retry-After header
# we honor that instead, also capped.
RATE_LIMIT_MAX_RETRIES = 2
RATE_LIMIT_BASE_DELAY_S = 30.0
RATE_LIMIT_MAX_DELAY_S = 90.0


def _should_use_live(chunks: list[TextChunk]) -> tuple[bool, Optional[str]]:
    """
    Return (use_live, skip_reason). When use_live is False, skip_reason is the
    short string we attach to model_notes so reports show why the placeholder
    fired instead of guessing.
    """
    if os.environ.get(LIVE_ENV_VAR, "").strip() != "1":
        return False, f"{LIVE_ENV_VAR} not set"
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return False, "ANTHROPIC_API_KEY missing"
    if not chunks:
        return False, "no chunks to ground the call"
    return True, None


LIVE_MAX_CHUNKS = 30


def _build_evidence(move: PriceMove, chunks: list[TextChunk]) -> JoinedEvidence:
    """
    Bridge `(move, chunks)` → `JoinedEvidence` for the run_attribution API.
    `events=[]` because callers of `attribute()` don't currently carry the
    unified Event records — only TextChunks. window_start/end are derived
    from chunk publication dates, capped at move.move_date so we never
    advertise foreknowledge.

    Chunks are capped at LIVE_MAX_CHUNKS to stay under Claude's 1M-token
    context window. `chunks_for_real()` already round-robins by source_type,
    so taking the prefix preserves source diversity.
    """
    capped = list(chunks[:LIVE_MAX_CHUNKS])
    if capped:
        dates = [c.publication_date for c in capped if c.publication_date is not None]
        window_start = min(dates) if dates else move.move_date
        window_end = max(dates) if dates else move.move_date
    else:
        window_start = window_end = move.move_date
    if window_end > move.move_date:
        window_end = move.move_date
    return JoinedEvidence(
        move=move,
        window_start=window_start,
        window_end=window_end,
        events=[],
        text_chunks=capped,
    )


def _retry_delay_for(err: "Exception", attempt: int) -> float:
    """Honor a Retry-After response header when present; otherwise back off
    exponentially from RATE_LIMIT_BASE_DELAY_S. Always capped at
    RATE_LIMIT_MAX_DELAY_S so a misbehaving server can't park us forever."""
    response = getattr(err, "response", None)
    if response is not None:
        try:
            ra = response.headers.get("retry-after") or response.headers.get("Retry-After")
            if ra:
                seconds = float(ra)
                if seconds > 0:
                    return min(seconds, RATE_LIMIT_MAX_DELAY_S)
        except (AttributeError, ValueError):
            pass
    return min(RATE_LIMIT_BASE_DELAY_S * (2 ** attempt), RATE_LIMIT_MAX_DELAY_S)


def _live_attribute(
    move: PriceMove,
    chunks: list[TextChunk],
    config: AblationConfig,
) -> Attribution:
    """Wrap run_attribution, retry on 429s, and pin contract fields the
    demo / runner expect.

    On `anthropic.RateLimitError` we sleep (Retry-After header if present,
    else exponential backoff) and retry up to RATE_LIMIT_MAX_RETRIES times.
    On the final attempt the error propagates and the outer `attribute()`
    falls back to the placeholder with an honest model_notes tag.
    """
    import anthropic

    from model.attribution import run_attribution

    evidence = _build_evidence(move, chunks)

    attempts = RATE_LIMIT_MAX_RETRIES + 1
    attr = None
    for attempt in range(attempts):
        try:
            # validate=False — tolerate weight-sum drift from smaller models;
            # the demo just needs a shaped Attribution, not lab-grade output.
            attr = run_attribution(evidence, ablation_name=config.name, validate=False)
            break
        except anthropic.RateLimitError as e:
            if attempt >= attempts - 1:
                raise
            delay = _retry_delay_for(e, attempt)
            log.warning(
                "rate-limited on %s %s; sleeping %.0fs before retry %d/%d",
                move.ticker, move.move_date, delay, attempt + 2, attempts,
            )
            time.sleep(delay)

    assert attr is not None  # loop either set it or raised

    attr.ablation_name = config.name
    attr.sources_used = list(config.sources)
    attr.chunks_considered = len(chunks)
    attr.model_notes = (attr.model_notes or LIVE_NOTE_PREFIX)
    if not attr.model_notes.startswith(LIVE_NOTE_PREFIX):
        attr.model_notes = f"{LIVE_NOTE_PREFIX}: {attr.model_notes}"
    return attr


def _placeholder_attribute(
    move: PriceMove,
    chunks: list[TextChunk],
    config: AblationConfig,
    *,
    note_suffix: Optional[str] = None,
) -> Attribution:
    from backtest.fixtures import generate_attribution

    attr = generate_attribution(
        ticker=move.ticker,
        move_date=move.move_date,
        return_pct=move.return_pct,
        vol_zscore=move.vol_zscore,
        ablation_name=config.name,
    )
    real_ids = [c.chunk_id for c in chunks[:5]] or ["no_chunks_provided_0"]
    for ds in (attr.demand, attr.pricing, attr.competitive,
               attr.management_credibility, attr.macro):
        ds.evidence_chunk_ids = list(real_ids)
    attr.ablation_name = config.name
    attr.sources_used = list(config.sources)
    attr.chunks_considered = len(chunks)
    notes = attr.model_notes or PLACEHOLDER_NOTE_PREFIX
    if note_suffix:
        notes = f"{PLACEHOLDER_NOTE_PREFIX}: {note_suffix}"
    attr.model_notes = notes
    return attr


def attribute(
    move: PriceMove,
    chunks: list[TextChunk],
    config: AblationConfig,
) -> Attribution:
    """
    Attribute `move` across the 5 dimensions using `chunks` (already filtered
    to source types in config.sources and to publication_date <= move.move_date).

    PRE-LLM WEIGHTING (mentor ask):
        Before any attribution path runs, chunks are scored by source quality
        + recency decay + ticker alignment (see model.relevance). The bottom
        tier is dropped; the survivors are re-ranked and get an
        `[EVIDENCE_WEIGHT ...]` tag prepended to their text so the LLM sees
        which evidence to trust most. Set BW_DISABLE_CHUNK_FILTER=1 to run
        the unfiltered baseline for ablation.

    LIVE PATH (default OFF; enable with BW_USE_LIVE_ATTRIBUTION=1):
        Builds a JoinedEvidence and calls model.attribution.run_attribution,
        which hits the Anthropic API via the schema's tool_use contract and
        runs validate_attribution before returning.

    PLACEHOLDER PATH:
        Delegates to backtest.fixtures.generate_attribution — RNG-driven
        labels with a per-ablation noise schedule. Useful for plumbing tests
        and synthetic-data backtests; *not* a real attribution.

    Either way, attribution.model_notes carries a short tag explaining which
    path produced the result. Downstream reports should surface that tag.
    """
    from model.relevance import annotate_with_weights, filter_and_rank

    raw_count = len(chunks)
    scored = filter_and_rank(chunks, move)
    annotated = annotate_with_weights(scored)
    filter_note = (
        None if len(annotated) == raw_count
        else f"filtered to top {len(annotated)} of {raw_count} chunks by relevance"
    )

    use_live, skip_reason = _should_use_live(annotated)
    if use_live:
        try:
            attr = _live_attribute(move, annotated, config)
        except Exception as e:  # noqa: BLE001 — fall back on any live error
            log.warning(
                "live attribute failed for %s %s (%s: %s); using placeholder",
                move.ticker, move.move_date, type(e).__name__, e,
            )
            attr = _placeholder_attribute(
                move, annotated, config,
                note_suffix=f"live call failed ({type(e).__name__})",
            )
    else:
        attr = _placeholder_attribute(move, annotated, config, note_suffix=skip_reason)

    if filter_note:
        attr.model_notes = (
            f"{attr.model_notes}; {filter_note}"
            if attr.model_notes else filter_note
        )
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
