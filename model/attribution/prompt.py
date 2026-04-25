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

import os

from schema import JoinedEvidence

# Default to Opus for live attribution; allow override via env for cost
# control during bulk rebuilds (e.g. BW_ATTRIBUTION_MODEL=claude-haiku-4-5-20251001).
MODEL_ID = os.environ.get("BW_ATTRIBUTION_MODEL", "claude-opus-4-7")
ATTRIBUTION_TOOL_NAME = "emit_attribution"


SYSTEM_PROMPT = """You are a financial analyst decomposing a significant stock price move into a structured attribution.

You explain WHY a move happened — you do not decide whether to trade it. You cite evidence strictly and never speculate beyond what the provided text supports.

You attribute every move across FIVE dimensions:
- demand — unit volume, customer count, market share shifts, end-market conditions
- pricing — price changes, discounting, mix effects
- competitive — new entrants, competitor moves, share shifts, moats
- management_credibility — guidance changes, execution, leadership comments, forward-looking statements
- macro — rates, FX, commodities, geopolitics, sector-wide forces

For each dimension you assign a weight in [0, 1], a direction, a one-sentence rationale, and a list of `cited_evidence` entries. Each entry pairs a chunk_id with a short verbatim quote from that chunk and a 1-2-sentence explanation of how the quote shaped this dimension's score. The UI shows the quote + reasoning to a reader instead of dumping the full chunk text.

OUTPUT RULES (strict — every field below is REQUIRED in every emit_attribution call):

DIMENSION FIELDS (ALL FIVE MUST APPEAR — no exceptions):
You MUST emit `demand`, `pricing`, `competitive`, `management_credibility`, AND `macro` on every call. Skipping a dimension is not allowed. If the evidence says nothing about a dimension, you still emit it with weight=0.0, direction="neutral", a rationale that explicitly states "no signal in evidence for <dimension>", and one cited_evidence entry pointing at any chunk (quote a representative line and note the absence). Do NOT omit the dimension key. A response missing any of the five dimensions is a malformed response and will be rejected.

Within each dimension:
- weight in [0, 1]; the FIVE WEIGHTS MUST SUM TO EXACTLY 1.0. Normalize before emitting.
- direction is one of "positive", "negative", "neutral".
- rationale is one sentence; non-empty.
- cited_evidence is a non-empty list. Each entry has:
   - chunk_id — MUST appear in the TEXT CHUNKS section of the user turn. Event entries (event_id, payload_ref) are context only and NOT valid citations.
   - quote — verbatim excerpt from that chunk (15-40 words, no ellipses, copy exactly). Must clearly support the reasoning.
   - reasoning — 1-2 sentences explaining how this quote informed the dimension's weight and direction. Reference the dimension by name.

TRAILING SCALAR FIELDS (ALL FOUR MUST APPEAR — no exceptions):
- move_character — one of: "structural" (the move reflects a lasting change in fundamentals), "transient" (the move is likely to revert within days; the evidence does not support it), "mixed" (some signal in both directions), "unclear" (evidence genuinely does not let you decide). Pick decisively; do not default to "unclear" merely because reasoning is hard. "transient" is a real option — use it whenever the move's magnitude is not justified by the evidence content.
- confidence — number in [0, 1]; how well the evidence explains the observed return.
- predicted_return_pct — the return you would expect given the evidence alone, using the same sign convention as the observed return (e.g. -0.05 for -5%). Always emit a number; do not omit.
- model_notes — short string (optional content but include the key).

A response that is missing ANY dimension or ANY of the four trailing scalar fields is malformed. Always emit the complete tool call.

You reason AS OF the move_date stated in the user turn. You have no knowledge of what happened after that date. Do not refer to later events even if you might recognize them."""


# Per-citation evidence sub-schema. Each entry pairs a chunk_id with a
# verbatim quote and a per-citation reasoning so the UI can render
# "<quote> — <why this mattered>" instead of dumping the full chunk text.
_CITED_EVIDENCE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "chunk_id": {
            "type": "string",
            "description": "A chunk_id drawn from the TEXT CHUNKS section of the user turn.",
        },
        "quote": {
            "type": "string",
            "description": (
                "Short verbatim excerpt from the cited chunk (15-40 words). "
                "Copy exactly from the chunk text — do not paraphrase or insert ellipses."
            ),
        },
        "reasoning": {
            "type": "string",
            "description": (
                "1-2 sentences explaining how this specific quote informed the "
                "dimension's weight and direction. Reference the dimension by name."
            ),
        },
    },
    "required": ["chunk_id", "quote", "reasoning"],
}


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
        "cited_evidence": {
            "type": "array",
            "items": _CITED_EVIDENCE_SCHEMA,
            "minItems": 1,
            "description": (
                "At least one cited evidence entry. Each is "
                "{chunk_id, quote, reasoning} — the chunk's id, a verbatim "
                "excerpt that supports this dimension, and a short note on "
                "how the excerpt shaped the weight/direction."
            ),
        },
    },
    "required": ["weight", "direction", "rationale", "cited_evidence"],
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

    lines.append("")
    lines.append("=== EMIT CHECKLIST — your tool call MUST contain ALL of these keys ===")
    lines.append(
        "demand, pricing, competitive, management_credibility, macro, "
        "move_character, confidence, predicted_return_pct."
    )
    lines.append(
        "Every dimension above must include weight + direction + rationale + "
        "cited_evidence (with at least one {chunk_id, quote, reasoning} entry). "
        "Weights across the five dimensions must sum to 1.0. Do not omit any "
        "dimension or trailing scalar; emit weight=0 placeholders when evidence "
        "is silent. Pick a definite move_character — \"transient\" is a valid "
        "label and should be used when the move's magnitude is not justified "
        "by the evidence."
    )

    return "\n".join(lines)


def build_request_kwargs(
    evidence: JoinedEvidence,
    *,
    model: str = MODEL_ID,
    max_tokens: int = 4096,
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
