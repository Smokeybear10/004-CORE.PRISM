"""
FastAPI backend for the static HTML demo.

Serves:
    GET  /                 → demo/static/index.html
    GET  /styles.css, /app.js, /data/*.json → static assets
    POST /api/attribute    → live attribution with user-selected sources

On POST /api/attribute the server:
    1. Reconstructs the PriceMove from the request payload.
    2. Pulls REAL chunks via `demo.real_chunks.chunks_for_real(ticker, move_date)`
       which combines `ingestion.sec.get_filings_as_of` + an in-memory
       pre-indexed slice of the bundled news parquet.
    3. Filters those chunks to the enabled SourceTypes.
    4. Builds an `AblationConfig(name="custom", sources=...)` and calls
       `model.attribute()` — the same path the backtest harness uses.
    5. Returns the `Attribution` + filtered chunks as JSON.

Startup cost: ~30-60s to load and pre-index the 628 MB news parquet per
focal ticker. Per-request cost: milliseconds.

Run from the project root:
    uvicorn demo.server:app --host 127.0.0.1 --port 2004
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from demo.mock_data import FOCAL_TICKERS  # noqa: E402
from demo.real_chunks import (  # noqa: E402
    chunks_for_real,
    preload_earnings_transcripts,
    preload_finnhub_news,
    preload_news,
    preload_peer_and_sector_news,
    preload_thirteen_f,
)
from backtest.signal import STRATEGY_REGISTRY  # noqa: E402
from model import attribute as model_attribute  # noqa: E402
from schema import (  # noqa: E402
    AblationConfig,
    Attribution,
    PriceMove,
    SourceType,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
PRESENTATION_DIR = _ROOT / "presentation"

# Disk cache for /api/attribute responses, keyed by (ticker, move_date,
# sorted-enabled-sources-tuple). Pre-warmed by demo/prewarm_cache.py so
# every common toggle combination is free at demo time.
#
# CACHE_ONLY=1 makes the server return 503 on miss instead of falling
# through to model.attribute() — useful when presenting and you want a
# hard guarantee that no API credit is burned.
ATTR_CACHE_DIR = _ROOT / "data" / "cache" / "api_attribute"
CACHE_ONLY = os.environ.get("BW_CACHE_ONLY", "").strip() in {"1", "true", "yes"}


def _cache_key(ticker: str, move_date: date, sources: list[str]) -> str:
    src_part = "_".join(sorted(sources)) or "none"
    return f"{ticker}_{move_date.isoformat()}_{src_part}.json"


def _cache_path(ticker: str, move_date: date, sources: list[str]) -> Path:
    return ATTR_CACHE_DIR / _cache_key(ticker, move_date, sources)


def _cache_read(ticker: str, move_date: date, sources: list[str]) -> dict | None:
    p = _cache_path(ticker, move_date, sources)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _cache_write(
    ticker: str, move_date: date, sources: list[str], payload: dict
) -> None:
    ATTR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(ticker, move_date, sources).write_text(json.dumps(payload))

# backtest.fixtures.generate_attribution KeyErrors on unknown ablation names.
# Map the number of user-enabled sources to the closest pre-defined bundle so
# the noise schedule stays monotonic (more sources → less noise) without
# patching backtest/ or model/.
_COUNT_TO_BUNDLE = {
    0: "base_news",
    1: "base_news",
    2: "+sec",
    3: "+earnings",
    4: "+peer_news",
    5: "+sector_news",
    6: "+macro",
    7: "+positioning",
}

app = FastAPI(title="Price Action Tagger", version="0.3")


@app.on_event("startup")
def _warm_caches() -> None:
    """Load the 628 MB news parquet once and pre-index for the 5 focal tickers,
    plus the pre-fetched 13F chunks JSONL.
    """
    print("[server] warming news parquet (this is the one-time startup cost)…", flush=True)
    preload_news(list(FOCAL_TICKERS.keys()))
    print("[server] news parquet indexed", flush=True)
    print("[server] indexing peer + sector news…", flush=True)
    preload_peer_and_sector_news(list(FOCAL_TICKERS.keys()))
    print("[server] peer + sector news indexed", flush=True)
    preload_thirteen_f()
    print("[server] 13F chunks loaded", flush=True)
    print("[server] loading earnings-call transcripts…", flush=True)
    preload_earnings_transcripts(list(FOCAL_TICKERS.keys()))
    print("[server] earnings transcripts loaded", flush=True)
    # Optional: pull historical news from Finnhub when FINNHUB_API_KEY is
    # set in .env. No-op without the key. Disk-cached so subsequent starts
    # are free.
    print("[server] checking Finnhub supplemental news…", flush=True)
    preload_finnhub_news(list(FOCAL_TICKERS.keys()))
    from ingestion.earnings_news.finnhub import is_finnhub_available
    if is_finnhub_available():
        print("[server] Finnhub news indexed", flush=True)
    else:
        print("[server] Finnhub disabled (FINNHUB_API_KEY not set)", flush=True)


# ---------- Schemas ----------

class AttributeRequest(BaseModel):
    ticker: str
    move_date: date
    return_pct: float
    vol_zscore: float = 0.0
    volume_zscore: Optional[float] = None
    magnitude_rank: Optional[float] = None
    enabled_sources: List[str] = Field(default_factory=list)


class AttributeResponse(BaseModel):
    attribution: dict
    chunks: list[dict]
    chunks_considered: int
    chunks_available: dict[str, int]
    enabled_sources: list[str]
    strategies: dict[str, str] = Field(default_factory=dict)


def _compute_strategies(attr: Attribution) -> dict[str, str]:
    """Run every registered fade-or-follow strategy on the same Attribution
    and return {strategy_name: "lean"|"fade"|"neutral"}.

    Each entry is a verdict the demo can show without an extra round trip.
    """
    out: dict[str, str] = {}
    for name, fn in STRATEGY_REGISTRY.items():
        try:
            out[name] = fn(attr)
        except Exception:  # noqa: BLE001 — strategy bugs shouldn't 500 the demo
            out[name] = "neutral"
    return out


# ---------- /api/attribute ----------

@app.post("/api/attribute", response_model=AttributeResponse)
def compute_attribution(req: AttributeRequest) -> AttributeResponse:
    if req.ticker not in FOCAL_TICKERS:
        raise HTTPException(status_code=400, detail=f"Unknown ticker: {req.ticker}")

    try:
        enabled = [SourceType(s) for s in req.enabled_sources]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown source_type: {exc}")

    cached = _cache_read(req.ticker, req.move_date, req.enabled_sources)
    if cached is not None:
        return AttributeResponse(**cached)
    if CACHE_ONLY:
        raise HTTPException(
            status_code=503,
            detail=(
                "BW_CACHE_ONLY set and this (ticker, move, sources) combo "
                "isn't pre-warmed. Run demo/prewarm_cache.py to fill it."
            ),
        )

    move = PriceMove(
        ticker=req.ticker,
        move_date=req.move_date,
        return_pct=req.return_pct,
        vol_zscore=req.vol_zscore,
        volume_zscore=req.volume_zscore,
        magnitude_rank=req.magnitude_rank,
        is_significant=True,
    )

    all_chunks = chunks_for_real(req.ticker, req.move_date)
    chunks_available: dict[str, int] = {}
    for c in all_chunks:
        chunks_available[c.source_type.value] = chunks_available.get(c.source_type.value, 0) + 1

    filtered = [c for c in all_chunks if c.source_type in enabled]

    if not filtered:
        # Zero sources (or zero-chunk selection under the enabled types) —
        # model.attribute would fall back to "no_chunks_provided_0". Return a
        # shaped "empty" response so the client can warn without an exception.
        return AttributeResponse(
            attribution={},
            chunks=[],
            chunks_considered=0,
            chunks_available=chunks_available,
            enabled_sources=[s.value for s in enabled],
            strategies={},
        )

    bundle_name = _COUNT_TO_BUNDLE.get(len(enabled), "+macro")
    config = AblationConfig(
        name=bundle_name,
        sources=enabled,
        description="user-selected",
    )
    attr = model_attribute(move, filtered, config)

    # Report the ablation as "custom" in the response so the UI reflects the
    # user's live toggle state, not the internal noise-bundle name.
    attr_dump = attr.model_dump(mode="json")
    attr_dump["ablation_name"] = "custom"

    # Collect every chunk_id the model cited so the UI can resolve all
    # citations. Without this, citations to chunks past the top-10 by
    # relevance show as "Missing chunk" in the UI, even though the IDs are
    # genuine — the frontend just didn't receive them.
    cited_ids: set[str] = set()
    for dim_name in (
        "demand", "pricing", "competitive", "management_credibility", "macro",
    ):
        dim = (attr_dump.get(dim_name) or {})
        for cid in (dim.get("evidence_chunk_ids") or []):
            cited_ids.add(cid)

    chunk_by_id = {c.chunk_id: c for c in filtered}
    # Top-10 by relevance + every cited chunk that wasn't already in the top-10.
    payload_set: dict[str, "TextChunk"] = {}
    for c in filtered[:10]:
        payload_set[c.chunk_id] = c
    for cid in cited_ids:
        if cid in chunk_by_id and cid not in payload_set:
            payload_set[cid] = chunk_by_id[cid]
    chunks_payload = [c.model_dump(mode="json") for c in payload_set.values()]

    response = AttributeResponse(
        attribution=attr_dump,
        chunks=chunks_payload,
        chunks_considered=len(filtered),
        chunks_available=chunks_available,
        enabled_sources=[s.value for s in enabled],
        strategies=_compute_strategies(attr),
    )
    _cache_write(req.ticker, req.move_date, req.enabled_sources, response.model_dump(mode="json"))
    return response


# ---------- Static files (mount LAST so /api routes win) ----------

@app.get("/")
def _index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# Pitch deck (intro slides) lives at /presentation/. Mount BEFORE the
# catch-all static mount so it wins routing for that prefix.
if PRESENTATION_DIR.is_dir():
    app.mount(
        "/presentation",
        StaticFiles(directory=PRESENTATION_DIR, html=True),
        name="presentation",
    )

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
