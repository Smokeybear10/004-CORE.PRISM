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
    uvicorn demo.server:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

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
    preload_news,
    preload_thirteen_f,
)
from model import attribute as model_attribute  # noqa: E402
from schema import (  # noqa: E402
    AblationConfig,
    PriceMove,
    SourceType,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

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
    preload_thirteen_f()
    print("[server] 13F chunks loaded", flush=True)
    print("[server] loading earnings-call transcripts…", flush=True)
    preload_earnings_transcripts(list(FOCAL_TICKERS.keys()))
    print("[server] earnings transcripts loaded", flush=True)


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


# ---------- /api/attribute ----------

@app.post("/api/attribute", response_model=AttributeResponse)
def compute_attribution(req: AttributeRequest) -> AttributeResponse:
    if req.ticker not in FOCAL_TICKERS:
        raise HTTPException(status_code=400, detail=f"Unknown ticker: {req.ticker}")

    try:
        enabled = [SourceType(s) for s in req.enabled_sources]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown source_type: {exc}")

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

    # Send only the first 10 filtered chunks over the wire — `model.attribute`
    # uses `chunks[:5]` for evidence, so 10 is a comfortable buffer. Keeps
    # responses small even when `filtered` is thousands of news items.
    chunks_payload = [c.model_dump(mode="json") for c in filtered[:10]]

    return AttributeResponse(
        attribution=attr_dump,
        chunks=chunks_payload,
        chunks_considered=len(filtered),
        chunks_available=chunks_available,
        enabled_sources=[s.value for s in enabled],
    )


# ---------- Static files (mount LAST so /api routes win) ----------

@app.get("/")
def _index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
