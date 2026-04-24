"""
Prompt template + tool schema for the attribution LLM call.

Design notes:
  - SYSTEM_PROMPT and the tool schema are both frozen / deterministic so that
    `cache_control` on the last system block caches tools + system together
    (render order is tools -> system -> messages; a breakpoint on the last
    system block covers everything before it).
  - Volatile content (move_date, evidence bundle) lives in the user message
    AFTER the cache boundary.
  - `temperature` is NOT set on the request: Opus 4.7 rejects sampling
    parameters (`temperature`, `top_p`, `top_k`) with a 400. The task brief
    mentioned `temperature=0.3`, but that would fail against this model.
"""
from __future__ import annotations

from schema import JoinedEvidence

MODEL_ID = "claude-opus-4-7"
ATTRIBUTION_TOOL_NAME = "emit_attribution"


SYSTEM_PROMPT = """You are a financial analyst decomposing a significant stock price move into a structured attribution.

You explain WHY a move happened — you do not decide whether to trade it. You cite evidence strictly and never speculate beyond what the provided text supports.

You attribute every move across FIVE dimensions:
- demand — unit volume, customer count, market share shifts, end-market conditions
- pricing — price changes, discounting, mix effects
- competitive — new entrants, competitor moves, share shifts, moats
- management_credibility — guidance changes, execution, leadership comments, forward-looking statements
- macro — rates, FX, commodities, geopolitics, sector-wide forces

For each dimension you assign a weight in [0, 1], a direction, a one-sentence rationale, and at least one evidence citation.

OUTPUT RULES (strict):
1. Return your answer by calling the emit_attribution tool. Do not answer in prose.
2. The five dimension weights MUST sum to exactly 1.0. Normalize before emitting if your first pass does not sum. A dimension that the evidence does not speak to gets weight 0.0, direction "neutral", a rationale noting the absence of signal, and at least one citation (the chunk that most-clearly demonstrates the absence of that signal).
3. Every DimensionScore.evidence_chunk_ids list MUST be non-empty.
4. Every chunk_id you cite MUST appear in the TEXT CHUNKS section of the user turn. Event entries (event_id, payload_ref) are context only and are NOT valid citations.
5. Classify move_character as one of: structural (the move reflects a lasting change), transient (likely to revert within days), mixed, or unclear.
6. predicted_return_pct is the return you would expect given the evidence alone — the return the model implies the market "should" have printed. Use the same sign convention as the observed return (e.g. -0.05 for -5%).
7. confidence is in [0, 1] and reflects how well the evidence explains the observed return.

You reason AS OF the move_date stated in the user turn. You have no knowledge of what happened after that date. Do not refer to later events even if you might recognize them."""


# Dimension sub-schema is reused for all five dimensions. Inlined rather than
# JSON-Schema $ref'd so cache-key bytes are identical regardless of how any
# downstream library handles refs.
_DIM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "weight": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Normalized weight in [0, 1]. Sum of all five dimension weights MUST equal 1.0.",
        },
        "direction": {
            "type": "string",
            "enum": ["positive", "negative", "neutral"],
        },
        "rationale": {
            "type": "string",
            "description": "One sentence. Must be non-empty.",
        },
        "evidence_chunk_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "At least one chunk_id drawn from the TEXT CHUNKS section of the user turn.",
        },
    },
    "required": ["weight", "direction", "rationale", "evidence_chunk_ids"],
}


ATTRIBUTION_TOOL: dict = {
    "name": ATTRIBUTION_TOOL_NAME,
    "description": (
        "Emit the structured attribution for a stock price move across five "
        "dimensions. Weights must sum to 1.0. Every evidence_chunk_ids entry "
        "must be a real chunk_id from the TEXT CHUNKS section."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "demand": _DIM_SCHEMA,
            "pricing": _DIM_SCHEMA,
            "competitive": _DIM_SCHEMA,
            "management_credibility": _DIM_SCHEMA,
            "macro": _DIM_SCHEMA,
            "move_character": {
                "type": "string",
                "enum": ["structural", "transient", "mixed", "unclear"],
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "predicted_return_pct": {
                "type": "number",
                "description": "Expected return given the evidence alone, e.g. -0.05 for -5%.",
            },
            "model_notes": {"type": "string"},
        },
        "required": [
            "demand",
            "pricing",
            "competitive",
            "management_credibility",
            "macro",
            "move_character",
            "confidence",
            "predicted_return_pct",
        ],
    },
}


def serialize_evidence(evidence: JoinedEvidence) -> str:
    """Render a `JoinedEvidence` as the user-turn text.

    The format separates:
      - PRICE MOVE header (ticker, date, return, vol/rank stats)
      - EVENTS block (narrative context, not citable)
      - TEXT CHUNKS block (citable; each chunk wrapped with a stable id tag)
    """
    move = evidence.move
    lines: list[str] = [
        f"PRICE MOVE: {move.ticker} on {move.move_date.isoformat()} "
        f"returned {move.return_pct:+.3%} "
        f"(vol_zscore={move.vol_zscore:.2f}, magnitude_rank={move.magnitude_rank}).",
        f"Evidence window: {evidence.window_start.isoformat()} to {evidence.window_end.isoformat()}.",
        f"Earnings day: {'yes' if evidence.earnings_day else 'no'}.",
        "",
        f"Reason AS OF {move.move_date.isoformat()}. You have no knowledge of later events.",
    ]

    lines.append("")
    lines.append("=== EVENTS (context only — NOT citable) ===")
    if evidence.events:
        for ev in evidence.events:
            lines.append(
                f"[{ev.event_type} @ {ev.event_date.isoformat()} source={ev.source}]"
            )
            lines.append(ev.text or f"(no text; payload_ref={ev.payload_ref})")
            lines.append("")
    else:
        lines.append("(no events in window)")
        lines.append("")

    lines.append("=== TEXT CHUNKS (citable evidence — these chunk_ids are the ONLY valid citations) ===")
    if evidence.text_chunks:
        for ch in evidence.text_chunks:
            section = f" section={ch.section_name}" if ch.section_name else ""
            lines.append(
                f'<chunk id="{ch.chunk_id}" source={ch.source_type.value} '
                f"published={ch.publication_date.isoformat()}{section}>"
            )
            lines.append(ch.text)
            lines.append("</chunk>")
            lines.append("")
    else:
        lines.append("(no text chunks available)")

    return "\n".join(lines)


def build_request_kwargs(
    evidence: JoinedEvidence,
    *,
    model: str = MODEL_ID,
    max_tokens: int = 2048,
) -> dict:
    """Assemble kwargs for `client.messages.create(...)`.

    System prompt carries `cache_control`; because tools render before system,
    a breakpoint on the last system block caches tools + system together
    (see shared/prompt-caching.md in the claude-api skill).
    """
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "tools": [ATTRIBUTION_TOOL],
        "tool_choice": {"type": "tool", "name": ATTRIBUTION_TOOL_NAME},
        "messages": [
            {"role": "user", "content": serialize_evidence(evidence)}
        ],
    }
