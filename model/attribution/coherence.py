"""
Step 5 coherence check.

    check_coherence(attribution, evidence) -> CoherenceCheck

A second LLM call with a different prompt: asks whether the attribution's
reasoning is *plausible* given the evidence. Catches category errors like
"crude oil cited for a software company" or a one-off litigation hit labeled
structural. It does NOT re-score the attribution — it only flags issues.

Same mockable client contract as `run_attribution`: pass any object whose
`.messages.create()` returns a response with a `tool_use` content block
named `emit_coherence_check`.
"""
from __future__ import annotations

from typing import Any

import anthropic

from schema import Attribution, CoherenceCheck, JoinedEvidence

COHERENCE_MODEL_ID = "claude-opus-4-7"
COHERENCE_TOOL_NAME = "emit_coherence_check"


COHERENCE_SYSTEM_PROMPT = """You are a senior review analyst evaluating whether an attribution's reasoning is plausible given the evidence.

You do NOT re-attribute. You judge plausibility and flag clear errors ONLY:
- Does the evidence actually support the dimension weights? (e.g. a dimension dominating the attribution with thin or missing supporting chunks)
- Category errors (e.g. crude oil cited for a software company, FX cited for a domestic-only business)
- Move-character mismatch (e.g. a one-off litigation hit labeled "structural")
- Obvious dominant drivers in the evidence that the attribution ignored
- Sign mismatches (dimension direction inconsistent with the observed return)

You do NOT need to agree with every weight. You flag only clear mistakes.

OUTPUT RULES:
1. Return your answer by calling the emit_coherence_check tool.
2. `plausible` is true if you find NO significant issues; false if you find any.
3. `issues` is a list of short sentences describing each issue you found. Empty list when plausible.
4. `reviewer_notes` is optional free text for anything useful to the human reviewer."""


COHERENCE_TOOL: dict = {
    "name": COHERENCE_TOOL_NAME,
    "description": (
        "Emit a plausibility review of an attribution. `plausible` is true if "
        "there are no significant issues; false otherwise. `issues` lists each "
        "issue as a short sentence."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "plausible": {"type": "boolean"},
            "issues": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Each issue as a single short sentence. Empty when plausible.",
            },
            "reviewer_notes": {"type": "string"},
        },
        "required": ["plausible", "issues"],
    },
}


def check_coherence(
    attr: Attribution,
    evidence: JoinedEvidence,
    *,
    client: Any | None = None,
    max_tokens: int = 1024,
) -> CoherenceCheck:
    """Review `attr` for plausibility given `evidence`."""
    client = client or anthropic.Anthropic()
    kwargs = _build_request_kwargs(attr, evidence, max_tokens=max_tokens)

    try:
        response = client.messages.create(**kwargs)
    except (anthropic.APIConnectionError, anthropic.APITimeoutError):
        response = client.messages.create(**kwargs)
    except anthropic.APIStatusError as e:
        if getattr(e, "status_code", 0) >= 500:
            response = client.messages.create(**kwargs)
        else:
            raise

    tool_input = _extract_tool_input(response)
    return CoherenceCheck(
        ticker=attr.ticker,
        move_date=attr.move_date,
        ablation_name=attr.ablation_name,
        plausible=bool(tool_input["plausible"]),
        issues=list(tool_input.get("issues") or []),
        reviewer_notes=tool_input.get("reviewer_notes"),
    )


# ---------- internals ----------


def _build_request_kwargs(
    attr: Attribution,
    evidence: JoinedEvidence,
    *,
    max_tokens: int,
) -> dict:
    return {
        "model": COHERENCE_MODEL_ID,
        "max_tokens": max_tokens,
        "system": [
            {
                "type": "text",
                "text": COHERENCE_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "tools": [COHERENCE_TOOL],
        "tool_choice": {"type": "tool", "name": COHERENCE_TOOL_NAME},
        "messages": [
            {"role": "user", "content": _render_review_body(attr, evidence)}
        ],
    }


def _render_review_body(attr: Attribution, evidence: JoinedEvidence) -> str:
    dims = []
    for name in ("demand", "pricing", "competitive", "management_credibility", "macro"):
        d = getattr(attr, name)
        dims.append(
            f"  - {name}: weight={d.weight:.2f} direction={d.direction} "
            f"cites={list(d.evidence_chunk_ids)}\n    rationale: {d.rationale}"
        )

    chunk_summaries = [
        f'  - id="{c.chunk_id}" source={c.source_type.value}: {_preview(c.text)}'
        for c in evidence.text_chunks
    ]

    lines = [
        f"PRICE MOVE: {attr.ticker} on {attr.move_date.isoformat()} "
        f"returned {attr.return_pct:+.3%}.",
        f"Predicted return: {attr.predicted_return_pct}",
        f"move_character={attr.move_character}, confidence={attr.confidence}",
        "",
        "ATTRIBUTION:",
        *dims,
        "",
        "AVAILABLE TEXT CHUNKS:",
        *(chunk_summaries or ["  (none)"]),
    ]
    return "\n".join(lines)


def _preview(text: str, *, max_chars: int = 200) -> str:
    flat = " ".join(text.split())
    if len(flat) <= max_chars:
        return flat
    return flat[: max_chars - 1] + "..."


def _extract_tool_input(response: Any) -> dict:
    blocks = getattr(response, "content", None) or []
    for block in blocks:
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", None) == COHERENCE_TOOL_NAME
        ):
            tool_input = getattr(block, "input", None)
            if not isinstance(tool_input, dict):
                raise RuntimeError(
                    f"tool_use block for {COHERENCE_TOOL_NAME!r} had non-dict input"
                )
            return tool_input
    raise RuntimeError(
        f"response did not contain a tool_use block named {COHERENCE_TOOL_NAME!r}; "
        f"stop_reason={getattr(response, 'stop_reason', None)!r}"
    )
