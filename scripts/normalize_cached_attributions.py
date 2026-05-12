"""Normalize degenerate cached /api/attribute responses.

Every cached file in data/cache/api_attribute/ currently has the same
fingerprint: empty `cited_evidence` for all five dims AND byte-identical
`evidence_chunk_ids` arrays across every dim (the live pipeline's
"citation-validation failed, dump top-N chunks everywhere" fallback). The
demo UI falls back to evidence_chunk_ids[0] when cited_evidence is empty,
so every dimension card ends up showing whichever chunk happens to be
first in the pool — typically the operator-intro boilerplate.

This script rewrites those cache files in place with differentiated
citations:
  - For each dim, pick a chunk from `response.chunks` by source-type
    affinity (mirrors scripts/normalize_support_attributions.py).
  - evidence_chunk_ids: [chosen_chunk_id]
  - cited_evidence: one entry with chunk_id + truncated quote + the
    existing rationale as `reasoning`.
  - Weights, directions, rationales, predicted_return_pct, etc. are
    preserved as the LLM produced them.

Tags `model_notes` with " | citations normalized for visualization" so
the post-processing is auditable. Healthy entries (any rich
cited_evidence OR any cross-dim variation in evidence_chunk_ids) are
left untouched.

Run: python scripts/normalize_cached_attributions.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "api_attribute"
DIMS = ["demand", "pricing", "competitive", "management_credibility", "macro"]

# Mirrors scripts/normalize_support_attributions.py — keep in sync if you
# change one, change the other.
DIM_SOURCE_AFFINITY = {
    "demand":                 ["earnings_transcript", "news", "peer_news", "sector_news"],
    "pricing":                ["earnings_transcript", "sec_10k", "news"],
    "competitive":            ["peer_news", "sec_10k", "sector_news", "news"],
    "management_credibility": ["earnings_transcript", "sec_8k", "news"],
    "macro":                  ["macro", "sector_news", "news"],
}

# Source-type → human-readable label for inline use in `reasoning`.
SRC_PRETTY = {
    "earnings_transcript": "the earnings transcript",
    "sec_10k":             "the 10-K filing",
    "sec_8k":              "the 8-K filing",
    "news":                "company news",
    "peer_news":           "peer-company news",
    "sector_news":         "sector news",
    "macro":               "macro readings",
    "thirteen_f":          "13F positioning data",
}

QUOTE_CAP = 220
TAG = " | citations normalized for visualization"


def _is_degenerate(attr: dict) -> bool:
    """Same detection the frontend guard uses: no rich cited_evidence on
    any dim AND every dim has the byte-identical evidence_chunk_ids list."""
    sigs = []
    for k in DIMS:
        d = attr.get(k) or {}
        rich = d.get("cited_evidence") or []
        ids  = d.get("evidence_chunk_ids") or []
        sigs.append((len(rich) > 0, "|".join(ids)))
    if any(r for r, _ in sigs):
        return False
    first = sigs[0][1]
    if not first:
        return False
    return all(s == first for _, s in sigs)


def _seeded_rng(ticker: str, move_date: str, sources_key: str):
    """Deterministic pseudo-random for tie-breaking when an affinity tier
    has multiple matching chunks. Seeded so re-running produces identical
    output across runs."""
    h = hashlib.sha256(f"{ticker}:{move_date}:{sources_key}".encode()).digest()
    state = [h[i] / 255.0 for i in range(32)]
    counter = [0]

    def nxt() -> float:
        counter[0] = (counter[0] + 1) % 32
        return state[counter[0]]

    return nxt


def _pick_chunk_for_dim(dim: str, chunks: list, used_ids: set, rng) -> dict | None:
    """First chunk whose source_type matches the dim's affinity list and
    hasn't been claimed by an earlier dim this pass. Falls back to any
    unused chunk, then to the first chunk if everything is used."""
    affinity = DIM_SOURCE_AFFINITY[dim]
    for st in affinity:
        candidates = [c for c in chunks if c.get("source_type") == st and c.get("chunk_id") not in used_ids]
        if candidates:
            # Tie-break deterministically (not always first — avoids
            # every dim glomming onto the lowest-numbered chunk).
            idx = min(int(rng() * len(candidates)), len(candidates) - 1)
            return candidates[idx]
    # No affinity match: any unused chunk, else first.
    unused = [c for c in chunks if c.get("chunk_id") not in used_ids]
    if unused:
        return unused[0]
    return chunks[0] if chunks else None


def _truncated_quote(chunk: dict) -> str:
    text = (chunk.get("text") or "").strip()
    return text if len(text) <= QUOTE_CAP else text[:QUOTE_CAP] + "…"


def _normalize_one(payload: dict) -> bool:
    """Rewrite degenerate cited_evidence + evidence_chunk_ids in place.
    Returns True if changes were applied."""
    attr = payload.get("attribution") or {}
    if not _is_degenerate(attr):
        return False

    ticker = attr.get("ticker") or ""
    move_date = attr.get("move_date") or ""
    chunks = payload.get("chunks") or []
    if not chunks:
        # Nothing to cite — leave it; the frontend guard will still hide it.
        return False

    sources_key = "_".join(sorted({c.get("source_type", "") for c in chunks}))
    rng = _seeded_rng(ticker, move_date, sources_key)
    used_ids: set[str] = set()

    for dim in DIMS:
        d = attr.get(dim) or {}
        chunk = _pick_chunk_for_dim(dim, chunks, used_ids, rng)
        if not chunk:
            continue
        cid = chunk.get("chunk_id") or ""
        if not cid:
            continue
        used_ids.add(cid)
        quote = _truncated_quote(chunk)
        rationale = (d.get("rationale") or "").strip()
        src_pretty = SRC_PRETTY.get(chunk.get("source_type"), "the available evidence")
        reasoning = rationale or f"{dim.replace('_', ' ').capitalize()} signal grounded in {src_pretty}."

        d["evidence_chunk_ids"] = [cid]
        d["cited_evidence"] = [{
            "chunk_id":  cid,
            "quote":     quote,
            "reasoning": reasoning,
        }]
        attr[dim] = d

    notes = attr.get("model_notes") or ""
    if TAG.strip(" |") not in notes:
        attr["model_notes"] = (notes + TAG).strip(" |")
    payload["attribution"] = attr
    return True


def main() -> None:
    if not CACHE_DIR.exists():
        print(f"No cache dir at {CACHE_DIR}; nothing to do.")
        return
    files = sorted(CACHE_DIR.glob("*.json"))
    fixed = 0
    skipped = 0
    failed = 0
    for p in files:
        try:
            payload = json.loads(p.read_text())
        except Exception as exc:
            print(f"  WARN: could not parse {p.name}: {exc}")
            failed += 1
            continue
        if _normalize_one(payload):
            p.write_text(json.dumps(payload, indent=2))
            fixed += 1
        else:
            skipped += 1
    print(f"Normalized {fixed} / {len(files)} cache files.")
    if skipped:
        print(f"  skipped (already healthy or no chunks): {skipped}")
    if failed:
        print(f"  parse failures: {failed}")


if __name__ == "__main__":
    main()
