"""
Pre-warm the /api/attribute disk cache so the live demo runs free.

Walks every (focal ticker × flagged move) in demo/static/data/*.json and
fires one HTTP request per ablation bundle defined in
demo.build_static.DEMO_ABLATION_BUNDLES. Each successful response is
cached under data/cache/api_attribute/ by the server's cache layer.

Usage:
    # 1. start the server with live mode on
    BW_USE_LIVE_ATTRIBUTION=1 uvicorn demo.server:app --port 2004 &

    # 2. run this script — it pulls move lists from the static JSONs and
    #    POSTs to the running server, populating the cache as it goes
    python demo/prewarm_cache.py

    # 3. for the demo, restart with cache-only mode so misses 503 instead
    #    of silently billing
    pkill -f "uvicorn demo.server"
    BW_CACHE_ONLY=1 uvicorn demo.server:app --port 2004

Env knobs:
    BW_PREWARM_HOST       default http://127.0.0.1:2004
    BW_PREWARM_TICKERS    comma-separated subset (default: all focal)
    BW_PREWARM_FORCE      "1" to re-POST even if the cache file already
                          exists. Default skips cached entries.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import urllib.request
import urllib.error

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from demo.build_static import DEMO_ABLATION_BUNDLES  # noqa: E402
from demo.mock_data import FOCAL_TICKERS  # noqa: E402

HOST = os.environ.get("BW_PREWARM_HOST", "http://127.0.0.1:2004").rstrip("/")
DATA_DIR = _ROOT / "demo" / "static" / "data"
CACHE_DIR = _ROOT / "data" / "cache" / "api_attribute"
FORCE = os.environ.get("BW_PREWARM_FORCE", "").strip() in {"1", "true", "yes"}


def _cache_filename(ticker: str, move_date: str, sources: list[str]) -> str:
    src_part = "_".join(sorted(sources)) or "none"
    return f"{ticker}_{move_date}_{src_part}.json"


def _selected_tickers() -> list[str]:
    raw = os.environ.get("BW_PREWARM_TICKERS", "").strip()
    if not raw:
        return list(FOCAL_TICKERS.keys())
    requested = [t.strip().upper() for t in raw.split(",") if t.strip()]
    return [t for t in requested if t in FOCAL_TICKERS]


def _post(payload: dict) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{HOST}/api/attribute",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, r.read().decode("utf-8", errors="replace")[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")[:200]
    except Exception as e:
        return -1, repr(e)[:200]


def main() -> int:
    tickers = _selected_tickers()
    bundles = list(DEMO_ABLATION_BUNDLES.items())
    print(f"[prewarm] host={HOST} tickers={tickers} bundles={[b[0] for b in bundles]}")
    print(f"[prewarm] force={FORCE} cache_dir={CACHE_DIR}")

    total_planned = 0
    total_skipped = 0
    total_ok = 0
    total_fail = 0
    t0 = time.time()

    for ticker in tickers:
        bundle_path = DATA_DIR / f"{ticker}.json"
        if not bundle_path.exists():
            print(f"[prewarm] {ticker}: no bundle at {bundle_path}, skipping")
            continue
        bundle = json.loads(bundle_path.read_text())
        moves = bundle.get("moves", [])
        print(f"[prewarm] {ticker}: {len(moves)} moves × {len(bundles)} bundles = "
              f"{len(moves) * len(bundles)} requests")

        for move in moves:
            move_date = move["move_date"]
            for bundle_name, sources in bundles:
                src_strs = [s.value for s in sources]
                cache_file = CACHE_DIR / _cache_filename(ticker, move_date, src_strs)
                total_planned += 1
                if cache_file.exists() and not FORCE:
                    total_skipped += 1
                    continue
                payload = {
                    "ticker": ticker,
                    "move_date": move_date,
                    "return_pct": move["return_pct"],
                    "vol_zscore": move.get("vol_zscore", 0.0),
                    "magnitude_rank": move.get("magnitude_rank"),
                    "enabled_sources": src_strs,
                }
                code, body_preview = _post(payload)
                if code == 200:
                    total_ok += 1
                    print(f"  OK  {ticker} {move_date} {bundle_name}")
                else:
                    total_fail += 1
                    print(f"  FAIL {ticker} {move_date} {bundle_name} → {code} {body_preview}")

    dt = time.time() - t0
    print(f"\n[prewarm] done in {dt:.1f}s — planned={total_planned} ok={total_ok} "
          f"skipped={total_skipped} fail={total_fail}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
