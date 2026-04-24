"""
Live-attribution helper used by `demo/app.py`.

Pure module — no Streamlit imports, no module-level network or file I/O. The
`get_attribution` entry point builds a `JoinedEvidence` from the passed-in
frames, runs attribution + coherence, validates, and returns an
`AttributionResult`. On any of the following it falls back to
`demo.mock_data.generate_attribution` + `chunks_for` and records the reason:

    - use_mock=True
    - empty evidence (no citeable chunks for the move)
    - AttributionValidationError
    - anthropic.AuthenticationError / RateLimitError / other API exception
    - any other unexpected error

This lets the demo stay clickable even when the live API is unavailable,
with an honest status indicator upstream showing whether the result is
live, live-from-cache, or a mock fallback.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from schema import Attribution, CoherenceCheck, PriceMove, TextChunk

logger = logging.getLogger(__name__)


@dataclass
class AttributionResult:
    """Handoff from live_attribution to the demo UI."""
    attribution: Attribution
    coherence: Optional[CoherenceCheck]
    chunks: list[TextChunk]   # matches the chunks the attribution could cite
    source: str               # "live" | "live_cached" | "mock_fallback"
    error: Optional[str] = None


def get_attribution(
    ticker: str,
    move: PriceMove,
    ablation_name: str,
    *,
    events_df: pd.DataFrame,
    chunks_df: pd.DataFrame,
    earnings_calendar: pd.DataFrame,
    use_mock: bool,
    client: Any | None = None,
) -> AttributionResult:
    """
    Resolve an attribution for `move`, falling back to mock on any failure path.

    Args:
        ticker: the stock ticker (used for the mock fallback).
        move: the flagged PriceMove.
        ablation_name: which ablation config to record on the output.
        events_df / chunks_df / earnings_calendar: frames for `join_evidence`.
        use_mock: if True, skip the live path entirely.
        client: optional Anthropic-SDK-shaped client (duck-typed on
            `.messages.create()`); None means the real SDK.
    """
    if use_mock:
        return _mock_result(ticker, move, ablation_name, reason=None)

    # Join evidence. This is cheap and pure-Python, not an API call.
    try:
        from ingestion.events import join_evidence
        evidence = join_evidence(
            move=move,
            events_df=events_df,
            chunks_df=chunks_df,
            earnings_calendar=earnings_calendar,
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("join_evidence raised unexpectedly")
        return _mock_result(
            ticker, move, ablation_name,
            reason=f"join_evidence failed: {type(e).__name__}",
        )

    if not evidence.text_chunks:
        return _mock_result(
            ticker, move, ablation_name,
            reason="no evidence — zero citeable chunks in window",
        )

    # Run live attribution. Exceptions here = fallback.
    try:
        from model.attribution import run_attribution
        attribution = run_attribution(evidence, ablation_name, client=client)
    except Exception as e:
        reason = _reason_from_exception(e)
        logger.warning("live attribution failed, falling back to mock: %s", reason)
        return _mock_result(ticker, move, ablation_name, reason=reason)

    # Coherence is a second API call; failure is non-fatal — we still return
    # the (validated) live attribution, just without a plausibility badge.
    coherence = None
    try:
        from model.attribution import check_coherence
        coherence = check_coherence(attribution, evidence, client=client)
    except Exception as e:
        logger.warning("coherence check failed (non-fatal): %s", e)

    return AttributionResult(
        attribution=attribution,
        coherence=coherence,
        chunks=list(evidence.text_chunks),
        source="live",
        error=None,
    )


# ---------- fallback path ----------

def _mock_result(
    ticker: str,
    move: PriceMove,
    ablation_name: str,
    *,
    reason: Optional[str],
) -> AttributionResult:
    from demo.mock_data import chunks_for, generate_attribution
    attribution = generate_attribution(
        ticker=ticker,
        move_date=move.move_date,
        return_pct=move.return_pct,
        ablation_name=ablation_name,
    )
    return AttributionResult(
        attribution=attribution,
        coherence=None,
        chunks=chunks_for(ticker, move.move_date),
        source="mock_fallback",
        error=reason,
    )


def _reason_from_exception(e: Exception) -> str:
    """Produce a short human-readable reason for the source='mock_fallback' badge."""
    # Order matters — check more specific types first.
    try:
        from model.attribution import AttributionValidationError
        if isinstance(e, AttributionValidationError):
            first = e.issues[0] if getattr(e, "issues", None) else str(e)
            return f"attribution validation failed: {first}"
    except ImportError:  # pragma: no cover
        pass

    try:
        import anthropic
        if isinstance(e, anthropic.AuthenticationError):
            return "API auth error — check ANTHROPIC_API_KEY"
        if isinstance(e, anthropic.RateLimitError):
            return "API rate limit exceeded"
        if isinstance(e, anthropic.APIStatusError):
            return f"API error: status {getattr(e, 'status_code', '?')}"
        if isinstance(e, anthropic.APIConnectionError):
            return "API connection error"
    except ImportError:  # pragma: no cover
        pass

    return f"live attribution failed: {type(e).__name__}: {e}"
