"""
Attribution runner.

    run_attribution(evidence, ablation_name) -> Attribution
    run_attribution_batch(evidence_list, ablation_name, concurrency=4) -> list[Attribution]

Both accept an optional `client` kwarg for test mocking. Any object whose
`.messages.create(**kwargs)` returns a response with `.content` = list of
content blocks (each with `.type`, and for tool_use blocks `.name` + `.input`)
is accepted — we never touch fields beyond that contract.

Error policy (per task brief): one manual retry on transient errors, then
raise. The Anthropic SDK also retries 429/5xx internally, so this is a
second, coarser layer.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import anthropic

from model.attribution import prompt as attribution_prompt
from model.attribution.validate import (
    AttributionValidationError,
    validate_attribution,
)
from schema import (
    Attribution,
    DimensionScore,
    JoinedEvidence,
    SourceType,
)

_DIM_FIELDS = (
    "demand",
    "pricing",
    "competitive",
    "management_credibility",
    "macro",
)


def run_attribution(
    evidence: JoinedEvidence,
    ablation_name: str = "full",
    *,
    client: Any | None = None,
    max_tokens: int = 4096,
    validate: bool = True,
) -> Attribution:
    """Run one attribution against the Anthropic API and return an Attribution.

    Args:
        evidence: the JoinedEvidence bundle for a single PriceMove.
        ablation_name: recorded on the output so ablation runs can be compared.
        client: optional stand-in for `anthropic.Anthropic()`; any object with
            a `.messages.create()` method matching the SDK's contract works.
        max_tokens: per-response output budget.
        validate: if True, run `validate_attribution` and raise
            `AttributionValidationError` on any issue.
    """
    client = client or anthropic.Anthropic()
    kwargs = attribution_prompt.build_request_kwargs(evidence, max_tokens=max_tokens)

    response = _call_with_one_retry(client, kwargs)
    tool_input = _extract_tool_input(response, attribution_prompt.ATTRIBUTION_TOOL_NAME)

    # Haiku frequently emits partial tool calls — one or two dimensions filled,
    # trailing scalar fields (move_character, predicted_return_pct, confidence)
    # missing. Detect that and retry once with a sharper user-turn nudge before
    # falling through to _assemble_attribution's default-fillers (which would
    # mask the partial as a confident "unclear, 0.5, predicted=null").
    if _is_incomplete_tool_input(tool_input):
        retry_kwargs = _kwargs_with_completeness_nudge(kwargs, tool_input)
        retry_response = _call_with_one_retry(client, retry_kwargs)
        retry_input = _extract_tool_input(
            retry_response, attribution_prompt.ATTRIBUTION_TOOL_NAME
        )
        # Take whichever response looks more complete; keep first if retry is no better.
        if _completeness_score(retry_input) > _completeness_score(tool_input):
            tool_input = retry_input

    attribution = _assemble_attribution(tool_input, evidence, ablation_name)

    if validate:
        issues = validate_attribution(attribution, evidence)
        if issues:
            raise AttributionValidationError(issues)
    return attribution


def run_attribution_batch(
    evidence_list: list[JoinedEvidence],
    ablation_name: str = "full",
    *,
    client: Any | None = None,
    concurrency: int = 4,
    max_tokens: int = 2048,
    validate: bool = True,
) -> list[Attribution]:
    """Run attributions for many JoinedEvidence bundles concurrently.

    Order of the returned list matches `evidence_list`. Exceptions from any
    one call propagate after other in-flight calls complete.
    """
    client = client or anthropic.Anthropic()

    def _one(ev: JoinedEvidence) -> Attribution:
        return run_attribution(
            ev,
            ablation_name,
            client=client,
            max_tokens=max_tokens,
            validate=validate,
        )

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        return list(ex.map(_one, evidence_list))


# ---------- internals ----------


def _call_with_one_retry(client: Any, kwargs: dict) -> Any:
    try:
        return client.messages.create(**kwargs)
    except (anthropic.APIConnectionError, anthropic.APITimeoutError):
        return client.messages.create(**kwargs)
    except anthropic.APIStatusError as e:
        if getattr(e, "status_code", 0) >= 500:
            return client.messages.create(**kwargs)
        raise


_REQUIRED_TRAILING_FIELDS = ("move_character", "confidence", "predicted_return_pct")


def _is_incomplete_tool_input(tool_input: dict) -> bool:
    """A tool call is incomplete if it omits any trailing required field, or
    has fewer than two dimensions with a real (>0) weight, or its dim weights
    sum to noticeably less than 1.0. Haiku exhibits all three patterns."""
    if any(tool_input.get(k) is None for k in _REQUIRED_TRAILING_FIELDS):
        return True
    weights = []
    real_dims = 0
    for name in _DIM_FIELDS:
        raw = tool_input.get(name)
        if not isinstance(raw, dict):
            continue
        w = raw.get("weight")
        if isinstance(w, (int, float)):
            weights.append(float(w))
            if w > 0.0:
                real_dims += 1
    if real_dims < 2:
        return True
    if weights and sum(weights) < 0.7:
        return True
    return False


def _completeness_score(tool_input: dict) -> int:
    """Higher = more complete. Used to pick the better of two responses."""
    score = 0
    for k in _REQUIRED_TRAILING_FIELDS:
        if tool_input.get(k) is not None:
            score += 2
    weight_sum = 0.0
    for name in _DIM_FIELDS:
        raw = tool_input.get(name)
        if isinstance(raw, dict):
            score += 1
            w = raw.get("weight")
            if isinstance(w, (int, float)):
                weight_sum += float(w)
    if 0.95 <= weight_sum <= 1.05:
        score += 3
    elif weight_sum >= 0.8:
        score += 1
    return score


def _kwargs_with_completeness_nudge(kwargs: dict, partial: dict) -> dict:
    """Re-run kwargs with an extra user-turn call-out listing the fields the
    first attempt skipped. Keeps the original system prompt + tools (cache-hit
    friendly), only mutates the messages list."""
    missing_dims = [
        name for name in _DIM_FIELDS
        if not isinstance(partial.get(name), dict)
    ]
    missing_trailing = [k for k in _REQUIRED_TRAILING_FIELDS if partial.get(k) is None]
    bullets = []
    if missing_dims:
        bullets.append(f"- Dimensions you omitted: {', '.join(missing_dims)}")
    if missing_trailing:
        bullets.append(f"- Trailing scalars you omitted: {', '.join(missing_trailing)}")
    if not bullets:
        bullets.append(
            "- Your dimension weights did not sum to 1.0; rebalance and resend."
        )
    nudge = (
        "Your previous emit_attribution call was incomplete. Re-emit the "
        "FULL tool call now. Required keys you must include this time:\n"
        + "\n".join(bullets)
        + "\nEmit weight=0 placeholders with direction=neutral and a brief "
        "rationale + one cited_evidence entry for any dimension you cannot "
        "score. The five dimension weights MUST sum to 1.0. Pick a definite "
        "move_character — \"transient\" is a valid choice when the move "
        "magnitude is not justified by the evidence content."
    )
    new_messages = list(kwargs.get("messages", []))
    new_messages.append({"role": "user", "content": nudge})
    return {**kwargs, "messages": new_messages}


def _extract_tool_input(response: Any, tool_name: str) -> dict:
    """Pull the tool_use input for `tool_name` from the response's content."""
    blocks = getattr(response, "content", None) or []
    for block in blocks:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
            tool_input = getattr(block, "input", None)
            if not isinstance(tool_input, dict):
                raise RuntimeError(
                    f"tool_use block for {tool_name!r} had non-dict input: {type(tool_input).__name__}"
                )
            return tool_input
    raise RuntimeError(
        f"response did not contain a tool_use block named {tool_name!r}; "
        f"stop_reason={getattr(response, 'stop_reason', None)!r}"
    )


def _assemble_attribution(
    tool_input: dict,
    evidence: JoinedEvidence,
    ablation_name: str,
) -> Attribution:
    """Merge the LLM's tool output with the fields the runner auto-fills."""
    # Default-fill missing dimensions: smaller / faster models occasionally
    # drop one when the evidence has no signal for it. The schema still needs
    # all five, so we fill with weight=0 + neutral + a citation pointing at
    # whatever chunk we have. Validation downstream will catch the rare case
    # where this would be wrong.
    fallback_chunk = (evidence.text_chunks[0].chunk_id
                      if evidence.text_chunks else "no_chunks_provided_0")
    valid_dirs = {"positive", "negative", "neutral"}
    dims = {}
    for name in _DIM_FIELDS:
        raw = tool_input.get(name)
        if not isinstance(raw, dict):
            # Model occasionally emits a bare string (or None) for a dimension —
            # treat that as "no signal" and fill with neutral defaults. Populate
            # cited_evidence too so the UI's rich shape always renders.
            from schema import CitedEvidence
            dims[name] = DimensionScore(
                weight=0.0,
                direction="neutral",
                rationale=(raw if isinstance(raw, str)
                           else f"model omitted {name}; no signal in evidence"),
                evidence_chunk_ids=[fallback_chunk],
                cited_evidence=[CitedEvidence(
                    chunk_id=fallback_chunk,
                    quote="",
                    reasoning=f"Model omitted {name}; falling back to top-relevance chunk.",
                )],
            )
            continue
        # Coerce out-of-schema fields the model occasionally returns:
        # direction='mixed' is a common Haiku slip; map to 'neutral'.
        if raw.get("direction") not in valid_dirs:
            raw = {**raw, "direction": "neutral"}
        # Reconcile new `cited_evidence` shape with the legacy
        # `evidence_chunk_ids` list. Either field can populate the other.
        cited = raw.get("cited_evidence") or []
        legacy_ids = raw.get("evidence_chunk_ids") or []
        if cited and not legacy_ids:
            legacy_ids = [
                e.get("chunk_id") for e in cited
                if isinstance(e, dict) and e.get("chunk_id")
            ]
        if not cited and legacy_ids:
            # Old prompt or fallback path: synthesize bare cited_evidence
            # entries so downstream code that prefers the rich shape still
            # sees a list it can iterate.
            cited = [{"chunk_id": cid, "quote": "", "reasoning": ""}
                     for cid in legacy_ids]
        if not legacy_ids:
            legacy_ids = [fallback_chunk]
            cited = [{"chunk_id": fallback_chunk, "quote": "", "reasoning": ""}]
        raw = {**raw, "evidence_chunk_ids": legacy_ids, "cited_evidence": cited}
        if not raw.get("rationale"):
            raw = {**raw, "rationale": f"no rationale for {name}"}
        dims[name] = DimensionScore(**raw)
    sources_used = sorted(
        {c.source_type for c in evidence.text_chunks},
        key=lambda s: s.value,
    )
    # Haiku occasionally wraps Literal-typed fields in stray whitespace
    # (e.g. "\nmixed\n"); strip before Pydantic's exact-match enum check
    # so we don't fall through to the placeholder on a cosmetic glitch.
    move_character_raw = tool_input.get("move_character") or "unclear"
    if isinstance(move_character_raw, str):
        move_character_raw = move_character_raw.strip()
    if move_character_raw not in ("structural", "transient", "mixed", "unclear"):
        move_character_raw = "unclear"
    return Attribution(
        ticker=evidence.move.ticker,
        move_date=evidence.move.move_date,
        return_pct=evidence.move.return_pct,
        predicted_return_pct=tool_input.get("predicted_return_pct"),
        **dims,
        move_character=move_character_raw,
        confidence=float(tool_input.get("confidence", 0.5)),
        ablation_name=ablation_name,
        sources_used=list(sources_used),
        chunks_considered=len(evidence.text_chunks),
        model_notes=tool_input.get("model_notes"),
    )
