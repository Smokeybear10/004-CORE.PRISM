"""
Pre-fetch 13F chunks for demo focal-ticker coverage.

Runs Henry's `run_thirteen_f_pipeline` once per (fund, current_quarter_end)
pair — typically slow (EDGAR rate-limited XML fetches) — and writes the
focal-ticker subset to a single JSONL the demo server can load instantly.

Output:
    data/thirteen_f/focal_chunks.jsonl   # one TextChunk per line (filtered)

Run from the project root:
    python demo/build_13f_chunks.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from demo.mock_data import FOCAL_TICKERS  # noqa: E402
from ingestion.idiosyncratic.thirteen_f import run_thirteen_f_pipeline  # noqa: E402

# A small universe of large, broadly-holding managers. All three are
# guaranteed to hold S&P 500 names (AMD, ABT, APD), very likely AIR, and
# possibly ACU via index tracking.
FUNDS = [
    ("0000102909", "VANGUARD GROUP INC"),
    ("0001364742", "BLACKROCK INC"),
    ("0000093751", "STATE STREET CORP"),
]

# Two most-recent completed calendar quarters. Quarterly 13Fs are due within
# 45 days of quarter end, so Q4 2024 (period_end 2024-12-31) filings cluster
# around 2025-02-14.
QUARTERS = [
    (date(2024, 12, 31), date(2024, 9, 30)),
    (date(2024, 9, 30), date(2024, 6, 30)),
    (date(2024, 6, 30), date(2024, 3, 31)),
    (date(2024, 3, 31), date(2023, 12, 31)),
]

OUT_PATH = _ROOT / "data" / "thirteen_f" / "focal_chunks.jsonl"


def main() -> None:
    focal_set = {t.upper() for t in FOCAL_TICKERS}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    kept: list[dict] = []
    seen_ids: set[str] = set()

    for cik, name in FUNDS:
        for current_q, prior_q in QUARTERS:
            print(f"Fetching {name} ({cik}) {prior_q} → {current_q}…", flush=True)
            try:
                _, _, _, chunks = run_thirteen_f_pipeline(cik, current_q, prior_q)
            except Exception as exc:
                print(f"  SKIP ({type(exc).__name__}: {exc})")
                continue

            hits = [c for c in chunks if c.ticker.upper() in focal_set]
            added = 0
            for c in hits:
                if c.chunk_id in seen_ids:
                    continue
                seen_ids.add(c.chunk_id)
                kept.append(c.model_dump(mode="json"))
                added += 1
            print(f"  → {added} focal-ticker chunk{'s' if added != 1 else ''} "
                  f"(from {len(chunks)} total deltas)")

    with OUT_PATH.open("w") as f:
        for rec in kept:
            f.write(json.dumps(rec) + "\n")

    by_ticker: dict[str, int] = {}
    for rec in kept:
        by_ticker[rec["ticker"]] = by_ticker.get(rec["ticker"], 0) + 1
    print(f"\nWrote {len(kept)} chunks to {OUT_PATH.relative_to(_ROOT)}")
    print("By ticker:", by_ticker)


if __name__ == "__main__":
    main()
