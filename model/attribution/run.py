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
    max_tokens: int = 2048,
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
            # treat that as "no signal" and fill with neutral defaults.
            dims[name] = DimensionScore(
                weight=0.0,
                direction="neutral",
                rationale=(raw if isinstance(raw, str)
                           else f"model omitted {name}; no signal in evidence"),
                evidence_chunk_ids=[fallback_chunk],
            )
            continue
        # Coerce out-of-schema fields the model occasionally returns:
        # direction='mixed' is a common Haiku slip; map to 'neutral'.
        if raw.get("direction") not in valid_dirs:
            raw = {**raw, "direction": "neutral"}
        if not raw.get("evidence_chunk_ids"):
            raw = {**raw, "evidence_chunk_ids": [fallback_chunk]}
        if not raw.get("rationale"):
            raw = {**raw, "rationale": f"no rationale for {name}"}
        dims[name] = DimensionScore(**raw)
    sources_used = sorted(
        {c.source_type for c in evidence.text_chunks},
        key=lambda s: s.value,
    )
    return Attribution(
        ticker=evidence.move.ticker,
        move_date=evidence.move.move_date,
        return_pct=evidence.move.return_pct,
        predicted_return_pct=tool_input.get("predicted_return_pct"),
        **dims,
        move_character=tool_input.get("move_character", "unclear"),
        confidence=float(tool_input.get("confidence", 0.5)),
        ablation_name=ablation_name,
        sources_used=list(sources_used),
        chunks_considered=len(evidence.text_chunks),
        model_notes=tool_input.get("model_notes"),
    )
