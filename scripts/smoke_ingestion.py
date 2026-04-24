"""
Phase 1 smoke test: verify each ingestion stream parses data correctly
and the as_of foreknowledge firewall holds. No mocks.

Run:
    python scripts/smoke_ingestion.py [--ticker AAPL] [--as-of 2024-11-02]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _fmt(n: int, max_: int = 8_000) -> str:
    return f"{n:,}" if n < max_ else f"{n:,} (truncated for display)"


def check_prices(ticker: str, as_of: date) -> None:
    from ingestion.prices import detect_significant_moves, load_prices

    print(f"\n=== prices ({ticker}, as_of={as_of}) ===")
    df = load_prices([ticker], as_of=as_of)
    print(f"rows={len(df):,}  date_min={df['date'].min()}  date_max={df['date'].max()}")
    assert df["date"].max() <= as_of, "as_of firewall broken: prices past as_of"

    moves = detect_significant_moves(df)
    moves = sorted(moves, key=lambda m: m.move_date)
    recent = [m for m in moves if m.move_date >= date(2024, 1, 1)]
    print(f"flagged moves total={len(moves)}  in-2024={len(recent)}")
    for m in recent[-5:]:
        print(f"  {m.move_date}  {m.return_pct:+.2%}  z={m.vol_zscore:+.2f}  rank={m.magnitude_rank}")


def check_news(ticker: str, as_of: date) -> None:
    from ingestion.earnings_news import get_news_as_of

    print(f"\n=== news ({ticker}, as_of={as_of}) ===")
    chunks = get_news_as_of(ticker, as_of)
    print(f"chunks={len(chunks):,}")
    if not chunks:
        print("  (empty — bundle may not tag this ticker)")
        return
    assert max(c.publication_date for c in chunks) <= as_of, "news as_of firewall broken"
    by_year = Counter(c.publication_date.year for c in chunks)
    print("  by year:", dict(sorted(by_year.items())))
    publishers = Counter((c.section_name or "?") for c in chunks)
    print("  top publishers:", publishers.most_common(5))
    late = sorted((c for c in chunks if c.publication_date.year == 2024), key=lambda c: c.publication_date)[-3:]
    for c in late:
        snippet = c.text[:120].replace("\n", " ")
        print(f"  {c.publication_date}  {c.chunk_id}  | {snippet}...")


def check_sec(ticker: str, as_of: date) -> None:
    from ingestion.sec import get_filings_as_of

    print(f"\n=== sec ({ticker}, as_of={as_of}) ===")
    chunks = get_filings_as_of(ticker, as_of)
    print(f"chunks={len(chunks):,}")
    if not chunks:
        print("  (empty — cache may need warming)")
        return
    assert max(c.publication_date for c in chunks) <= as_of, "sec as_of firewall broken"
    by_type = Counter(c.source_type.value for c in chunks)
    by_section = Counter(c.section_name for c in chunks)
    print("  by source_type:", dict(by_type))
    print("  by section:", dict(by_section.most_common(10)))
    recent = sorted(chunks, key=lambda c: c.publication_date)[-3:]
    for c in recent:
        snippet = c.text[:100].replace("\n", " ")
        print(f"  {c.publication_date}  {c.source_type.value:7}  {c.section_name:15}  | {snippet}...")


def check_13f(ticker: str, as_of: date) -> None:
    from ingestion.idiosyncratic.thirteen_f import fetch_13f_holdings

    # Berkshire Hathaway — big AAPL holder, useful sanity check
    print(f"\n=== 13F (Berkshire 0001067983, as_of={as_of}) ===")
    try:
        holdings = fetch_13f_holdings("0001067983", as_of)
    except Exception as e:
        print(f"  SKIP (network / EDGAR error: {type(e).__name__}: {e})")
        return
    print(f"positions={len(holdings)}")
    if not holdings:
        print("  (empty — may not have a filing on/before as_of)")
        return
    hit = [h for h in holdings if h.ticker.upper() == ticker.upper()]
    print(f"  {ticker} positions: {len(hit)}")
    top = sorted(holdings, key=lambda h: h.value_usd or 0, reverse=True)[:5]
    for h in top:
        val = h.value_usd or 0
        print(f"  {h.ticker:8}  shares={h.shares:>12,}  value=${val:>15,.0f}  filed={h.filing_date}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="AAPL")
    ap.add_argument("--as-of", default="2024-11-02")
    args = ap.parse_args()
    as_of = date.fromisoformat(args.as_of)

    check_prices(args.ticker, as_of)
    check_news(args.ticker, as_of)
    check_sec(args.ticker, as_of)
    check_13f(args.ticker, as_of)


if __name__ == "__main__":
    main()
