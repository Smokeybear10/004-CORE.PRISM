"""
One-shot build: dump per-ticker JSON bundles for the static HTML demo.

Run from the project root:
    python demo/build_static.py
    python -m http.server 8000 --directory demo/static

Output:
    demo/static/data/{TICKER}.json    # prices + flagged moves + attributions + chunks
    demo/static/data/index.json       # tickers list + metadata
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backtest.signal import STRATEGY_REGISTRY
from demo.mock_data import FOCAL_TICKERS
from demo.real_chunks import (
    chunks_for_real,
    preload_earnings_transcripts,
    preload_news,
    preload_peer_and_sector_news,
    preload_thirteen_f,
)
from ingestion.prices import detect_significant_moves, load_prices
from model import attribute as model_attribute
from schema import AblationConfig, PriceMove, SourceType

# Full stack for the pre-baked "default" attribution on initial render.
FULL_STACK_SOURCES = [
    SourceType.NEWS,
    SourceType.SEC_10K,
    SourceType.SEC_8K,
    SourceType.EARNINGS_TRANSCRIPT,
    SourceType.PEER_NEWS,
    SourceType.MACRO,
    SourceType.THIRTEEN_F,
]

OUT_DIR = _ROOT / "demo" / "static" / "data"
WINDOW_YEARS = 5
CHUNK_TEXT_CAP = 600  # trim chunk body for payload size


def _dim_dict(score) -> dict:
    return {
        "weight": round(float(score.weight), 4),
        "direction": score.direction,
        "rationale": score.rationale,
        "evidence_chunk_ids": list(score.evidence_chunk_ids),
    }


def _chunk_dict(c) -> dict:
    text = c.text if len(c.text) <= CHUNK_TEXT_CAP else c.text[:CHUNK_TEXT_CAP] + "…"
    return {
        "chunk_id": c.chunk_id,
        "source_type": c.source_type.value,
        "publication_date": c.publication_date.isoformat(),
        "section_name": c.section_name,
        "source_url": c.source_url,
        "text": text,
    }


def build_for_ticker(ticker: str, meta: dict, end_date: date) -> dict:
    start_date = end_date - timedelta(days=365 * WINDOW_YEARS)

    prices_df = load_prices([ticker], as_of=end_date)
    prices_df = prices_df[
        (prices_df["date"] >= start_date) & (prices_df["date"] <= end_date)
    ].reset_index(drop=True)

    prices = [
        {"date": d.isoformat() if hasattr(d, "isoformat") else str(d)[:10],
         "close": round(float(c), 4)}
        for d, c in zip(prices_df["date"], prices_df["close"])
    ]

    full_moves = detect_significant_moves(
        load_prices([ticker], as_of=end_date),
        lookback_vol=63,  # ~3 trading months
    )
    moves_in_window: list[PriceMove] = [
        m for m in full_moves
        if start_date <= m.move_date <= end_date
        and abs(m.vol_zscore) >= 3.0  # demo: keep only 3-sigma+ moves to declutter chart
    ]

    moves_sorted = sorted(moves_in_window, key=lambda x: x.move_date)
    moves_payload: list[dict] = []
    for idx, m in enumerate(moves_sorted, start=1):
        print(f"  [{idx}/{len(moves_sorted)}] {ticker} {m.move_date} "
              f"(return {m.return_pct:+.2%})…", flush=True)
        chunks = chunks_for_real(ticker, m.move_date)
        # Truthful counts over the FULL chunk pool (not just top-10) so the
        # UI toggle row can show accurate chunk counts on initial render.
        available_counts: dict[str, int] = {}
        for c in chunks:
            available_counts[c.source_type.value] = \
                available_counts.get(c.source_type.value, 0) + 1
        # "+macro" is the lowest-noise bundle in backtest.fixtures and matches
        # the 6-source full stack we pre-bake.
        config = AblationConfig(
            name="+macro",
            sources=list(FULL_STACK_SOURCES),
            description="pre-baked full-stack default",
        )
        attr = model_attribute(m, chunks, config)
        # Pre-compute strategy verdicts so the UI shows lean/fade/skip on
        # initial render (no API roundtrip needed).
        strategies: dict[str, str] = {}
        for sname, sfn in STRATEGY_REGISTRY.items():
            try:
                strategies[sname] = sfn(attr)
            except Exception:
                strategies[sname] = "neutral"
        moves_payload.append({
            "move_date": m.move_date.isoformat(),
            "return_pct": round(float(m.return_pct), 6),
            "vol_zscore": round(float(m.vol_zscore), 3),
            "magnitude_rank": (round(float(m.magnitude_rank), 3)
                               if m.magnitude_rank is not None else None),
            "attribution": {
                "realized": round(float(attr.return_pct), 6),
                "predicted": (round(float(attr.predicted_return_pct), 6)
                              if attr.predicted_return_pct is not None else None),
                "character": attr.move_character,
                "confidence": round(float(attr.confidence), 3),
                "chunks_considered": attr.chunks_considered,
                "sources_used": [s.value for s in attr.sources_used],
                "dimensions": {
                    "demand": _dim_dict(attr.demand),
                    "pricing": _dim_dict(attr.pricing),
                    "competitive": _dim_dict(attr.competitive),
                    "management_credibility": _dim_dict(attr.management_credibility),
                    "macro": _dim_dict(attr.macro),
                },
                "model_notes": attr.model_notes,
            },
            # Only pre-bake the first 10 chunks (model.attribute uses [:5] for
            # evidence; 10 is a buffer). Keeps each ticker's JSON ~300 KB
            # instead of exploding to tens of MB when news is dense.
            "chunks": [_chunk_dict(c) for c in chunks[:10]],
            "chunks_available": available_counts,
            "chunks_total": len(chunks),
            "strategies": strategies,
        })

    return {
        "ticker": ticker,
        "name": meta["name"],
        "sector": meta["sector"],
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "prices": prices,
        "moves": moves_payload,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    end_date = date.today()

    print("Preloading news parquet (one-time ~30-60s)…", flush=True)
    preload_news(list(FOCAL_TICKERS.keys()))
    preload_peer_and_sector_news(list(FOCAL_TICKERS.keys()))
    preload_thirteen_f()
    preload_earnings_transcripts(list(FOCAL_TICKERS.keys()))

    index: list[dict] = []
    for ticker, meta in FOCAL_TICKERS.items():
        print(f"Building {ticker} ({meta['name']}, {meta['sector']})…", flush=True)
        bundle = build_for_ticker(ticker, meta, end_date)
        path = OUT_DIR / f"{ticker}.json"
        path.write_text(json.dumps(bundle, separators=(",", ":")))
        size_kb = path.stat().st_size / 1024
        print(f"  → {path.name}  "
              f"({len(bundle['prices'])} bars, {len(bundle['moves'])} moves, "
              f"{size_kb:.1f} KB)")
        index.append({
            "ticker": ticker,
            "name": meta["name"],
            "sector": meta["sector"],
            "moves": len(bundle["moves"]),
        })

    (OUT_DIR / "index.json").write_text(json.dumps({
        "generated_at": end_date.isoformat(),
        "window_years": WINDOW_YEARS,
        "tickers": index,
    }, indent=2))
    print(f"\nDone. {len(index)} tickers written to {OUT_DIR.relative_to(_ROOT)}/")


if __name__ == "__main__":
    main()
