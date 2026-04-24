"""
End-to-end orchestrator: ticker + as_of -> annotated-chart JSON payload.

Linear pipeline, no caching, no concurrency. First-pass wiring only.

    python -m demo.analyze_ticker AMD 2024-10-01
    python -m demo.analyze_ticker AMD 2024-10-01 --out /tmp/amd.json

Library use:
    from demo.analyze_ticker import analyze_ticker
    payload = analyze_ticker("AMD", date(2024, 10, 1))
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

# Load ANTHROPIC_API_KEY from .env at project root (best-effort).
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import pandas as pd

from demo.prep_ticker import prep_ticker
from ingestion.events import build_events_parquet, join_evidence
from ingestion.prices import detect_significant_moves, load_prices
from model.attribution import (
    AttributionValidationError,
    check_coherence,
    run_attribution,
)
from schema import PriceMove

EVENTS_CACHE = Path("data/cache/events.parquet")
CHUNKS_CACHE = Path("data/cache/text_chunks.parquet")
EARNINGS_GLOB_DIR = Path("data/earnings")
DEFAULT_ANALYSIS_DIR = Path("data/analysis")

# Column schemas used when the parquet files aren't present. Mirror the
# aggregator's empty-frame columns so `join_evidence` is happy either way.
_EMPTY_EVENTS_COLS = [
    "event_id", "ticker", "event_date", "event_type",
    "source", "payload_ref", "text",
]
_EMPTY_CHUNKS_COLS = [
    "chunk_id", "ticker", "source_type", "publication_date",
    "period_end", "source_url", "section_name", "text", "token_count",
]


def analyze_ticker(
    ticker: str,
    as_of: date,
    out_path: Path | None = None,
    client: Any | None = None,
) -> dict:
    """Run the end-to-end pipeline for `ticker` as of `as_of`.

    Writes the annotated-chart JSON to `out_path` (default
    `data/analysis/<TICKER>.json`) and returns the same dict.
    """
    ticker = ticker.upper()
    out_path = Path(out_path) if out_path is not None else DEFAULT_ANALYSIS_DIR / f"{ticker}.json"

    _log(f"prepping source data for {ticker} as_of={as_of.isoformat()}")
    try:
        prep_summary = prep_ticker(ticker, as_of)
        _log(f"prep summary: {prep_summary}")
    except Exception as e:
        _log(f"prep_ticker failed (continuing with whatever's on disk): {e}")

    _log(f"loading prices for {ticker} as_of={as_of.isoformat()}")
    prices = load_prices([ticker], as_of)

    if prices.empty:
        _log(f"no price data for {ticker}; writing empty payload")
        payload = _empty_payload(ticker, as_of)
        _write_json(out_path, payload)
        return payload

    _log(f"detecting significant moves over {len(prices)} rows")
    moves = detect_significant_moves(prices)
    _log(f"detected {len(moves)} moves")

    events_df, chunks_df = _load_or_build_events(as_of)
    earnings_cal = _load_earnings_calendar()

    move_payloads = [
        _analyze_move(move, events_df, chunks_df, earnings_cal, client=client, idx=i, total=len(moves))
        for i, move in enumerate(moves, start=1)
    ]

    payload = {
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "n_moves": len(moves),
        "price_series": _price_series(prices, ticker),
        "moves": move_payloads,
    }

    _write_json(out_path, payload)
    _log(f"wrote {out_path}")
    return payload


# ---------- internals ----------


def _analyze_move(
    move: PriceMove,
    events_df: pd.DataFrame,
    chunks_df: pd.DataFrame,
    earnings_cal: pd.DataFrame,
    *,
    client: Any | None,
    idx: int,
    total: int,
) -> dict:
    _log(f"  [{idx}/{total}] joining evidence for {move.ticker} on {move.move_date}")
    evidence = join_evidence(move, events_df, chunks_df, earnings_cal)

    attribution_payload: dict | None = None
    coherence_payload: dict | None = None
    error: str | None = None

    try:
        attribution = run_attribution(evidence, ablation_name="full", client=client)
        attribution_payload = attribution.model_dump(mode="json")
    except AttributionValidationError as e:
        error = str(e)
        _log(f"  [{idx}/{total}] attribution failed validation: {error}")

    if attribution_payload is not None:
        coherence = check_coherence(attribution, evidence, client=client)
        coherence_payload = coherence.model_dump(mode="json")

    return {
        "move_date": move.move_date.isoformat(),
        "return_pct": float(move.return_pct),
        "vol_zscore": float(move.vol_zscore),
        "magnitude_rank": None if move.magnitude_rank is None else float(move.magnitude_rank),
        "earnings_day": bool(evidence.earnings_day),
        "attribution": attribution_payload,
        "coherence": coherence_payload,
        "evidence": {
            "events": [e.model_dump(mode="json") for e in evidence.events],
            "chunks": [c.model_dump(mode="json") for c in evidence.text_chunks],
        },
        "error": error,
    }


def _load_or_build_events(as_of: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read `events.parquet` + `text_chunks.parquet`; if missing, build once
    via `build_events_parquet`, then read. If STILL missing (no source data
    landed), fall back to empty DataFrames so the joiner still works."""
    if not EVENTS_CACHE.exists() or not CHUNKS_CACHE.exists():
        _log("events cache missing; running build_events_parquet")
        build_events_parquet(as_of, EVENTS_CACHE)

    if EVENTS_CACHE.exists():
        events_df = pd.read_parquet(EVENTS_CACHE)
    else:
        _log("events.parquet still missing; using empty frame")
        events_df = pd.DataFrame(columns=_EMPTY_EVENTS_COLS)

    if CHUNKS_CACHE.exists():
        chunks_df = pd.read_parquet(CHUNKS_CACHE)
    else:
        _log("text_chunks.parquet still missing; using empty frame")
        chunks_df = pd.DataFrame(columns=_EMPTY_CHUNKS_COLS)

    return events_df, chunks_df


def _load_earnings_calendar() -> pd.DataFrame:
    """Glob `data/earnings/calendar_*.parquet`, normalize to what
    `join_evidence` expects. `join_evidence` tolerates an empty frame."""
    files = sorted(EARNINGS_GLOB_DIR.glob("calendar_*.parquet"))
    if not files:
        return pd.DataFrame(columns=["ticker", "report_date"])
    pieces = [pd.read_parquet(f) for f in files]
    df = pd.concat(pieces, ignore_index=True)
    # Upstream Yahoo schema uses `symbol` for the ticker column.
    if "ticker" not in df.columns and "symbol" in df.columns:
        df = df.rename(columns={"symbol": "ticker"})
    return df


def _price_series(prices: pd.DataFrame, ticker: str) -> list[dict]:
    df = prices[prices["ticker"] == ticker]
    return [
        {
            "date": d.isoformat() if hasattr(d, "isoformat") else str(d)[:10],
            "close": float(c),
            "volume": int(v),
        }
        for d, c, v in zip(
            df["date"].tolist(),
            df["close"].tolist(),
            df["volume"].tolist(),
        )
    ]


def _empty_payload(ticker: str, as_of: date) -> dict:
    return {
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "n_moves": 0,
        "price_series": [],
        "moves": [],
    }


def _write_json(out_path: Path, payload: dict) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))


def _log(msg: str) -> None:
    print(f"[demo] {msg}", file=sys.stderr)


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="demo.analyze_ticker",
        description="Run the end-to-end attribution pipeline for one ticker.",
    )
    parser.add_argument("ticker", help="Ticker symbol, e.g. AMD")
    parser.add_argument("as_of", help="ISO date, e.g. 2024-10-01")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Output JSON path (default: {DEFAULT_ANALYSIS_DIR}/<TICKER>.json)",
    )
    args = parser.parse_args(argv)
    analyze_ticker(
        ticker=args.ticker,
        as_of=date.fromisoformat(args.as_of),
        out_path=args.out,
    )


if __name__ == "__main__":
    main()
