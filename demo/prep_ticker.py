"""
Populate `data/` for a single ticker so `demo.analyze_ticker` and `demo/app.py`
have events to reason over.

First-pass wrapper around three idiosyncratic fetchers — no concurrency, no
retries, no caching beyond "skip if a recent-enough file already exists".
One source failing must not abort the others.

    python -m demo.prep_ticker AMD 2026-04-24
    python -m demo.prep_ticker AMD 2026-04-24 --funds 0001067983,0001364742
    python -m demo.prep_ticker AMD 2026-04-24 --force
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from ingestion.earnings_news.earnings import run_earnings_calendar_pipeline
from ingestion.earnings_news.news import run_news_pipeline
from ingestion.idiosyncratic.index_changes import run_index_changes_pipeline
from ingestion.idiosyncratic.short_interest import run_short_interest_pipeline
from ingestion.idiosyncratic.thirteen_f import run_thirteen_f_pipeline
from ingestion.sec.filings import run_sec_pipeline

# Berkshire is a known-good CIK: already has data under data/thirteen_f/ and
# is a huge AAPL holder — useful for sanity-checking the joined evidence.
DEFAULT_FUND_CIKS: list[str] = [
    "0001067983",  # Berkshire Hathaway
]

SHORT_INTEREST_DIR = Path("data/short_interest")
INDEX_CHANGES_DIR = Path("data/index_changes")
THIRTEEN_F_DIR = Path("data/thirteen_f")
NEWS_DIR = Path("data/news")
EARNINGS_DIR = Path("data/earnings")
SEC_DIR = Path("data/sec")

SHORT_INTEREST_RECENCY_DAYS = 14
INDEX_CHANGES_RECENCY_DAYS = 7
THIRTEEN_F_FILING_LAG_DAYS = 45
NEWS_RECENCY_DAYS = 7
EARNINGS_RECENCY_DAYS = 7
SEC_RECENCY_DAYS = 30


def prep_ticker(
    ticker: str,
    as_of: date,
    fund_ciks: list[str] | None = None,
    force: bool = False,
) -> dict:
    """Run all available fetchers for `ticker`. Returns a summary dict
    mapping source name → status (`ok` / `skipped_recent` / `failed: <msg>`)."""
    ticker = ticker.upper()
    ciks = list(fund_ciks) if fund_ciks is not None else list(DEFAULT_FUND_CIKS)

    summary: dict[str, str] = {}
    summary["short_interest"] = _run_short_interest(ticker, as_of, force=force)
    summary["index_changes"] = _run_index_changes(as_of, force=force)
    summary.update(_run_thirteen_f(ciks, as_of, force=force))
    summary["news"] = _run_news(ticker, as_of, force=force)
    summary["earnings_calendar"] = _run_earnings_calendar(as_of, force=force)
    summary["sec"] = _run_sec(ticker, as_of, force=force)

    _log("=== summary ===")
    for source, status in summary.items():
        _log(f"  {source}: {status}")
    return summary


# ---------- per-source wrappers ----------


def _run_short_interest(ticker: str, as_of: date, *, force: bool) -> str:
    if not force and _short_interest_recent(ticker, as_of):
        _log(f"short_interest: skipped (recent file within {SHORT_INTEREST_RECENCY_DAYS}d)")
        return "skipped_recent"
    _log(f"short_interest: running for {ticker} as_of={as_of.isoformat()}")
    try:
        run_short_interest_pipeline(as_of, ticker=ticker)
    except Exception as e:  # noqa: BLE001 — driver MUST keep going on any failure
        _log(f"short_interest: FAILED: {e}")
        return f"failed: {e}"
    return "ok"


def _run_index_changes(as_of: date, *, force: bool) -> str:
    if not force and _index_changes_recent():
        _log(f"index_changes: skipped (recent file within {INDEX_CHANGES_RECENCY_DAYS}d)")
        return "skipped_recent"
    _log(f"index_changes: running as_of={as_of.isoformat()}")
    try:
        run_index_changes_pipeline(as_of)
    except Exception as e:  # noqa: BLE001
        _log(f"index_changes: FAILED: {e}")
        return f"failed: {e}"
    return "ok"


def _run_news(ticker: str, as_of: date, *, force: bool) -> str:
    if not force and _news_recent(ticker, as_of):
        _log(f"news: skipped (recent file within {NEWS_RECENCY_DAYS}d)")
        return "skipped_recent"
    _log(f"news: running for {ticker} as_of={as_of.isoformat()}")
    try:
        run_news_pipeline(ticker, as_of)
    except Exception as e:  # noqa: BLE001
        _log(f"news: FAILED: {e}")
        return f"failed: {e}"
    return "ok"


def _run_earnings_calendar(as_of: date, *, force: bool) -> str:
    if not force and _earnings_recent(as_of):
        _log(f"earnings_calendar: skipped (recent file within {EARNINGS_RECENCY_DAYS}d)")
        return "skipped_recent"
    _log(f"earnings_calendar: running as_of={as_of.isoformat()}")
    try:
        run_earnings_calendar_pipeline(as_of)
    except Exception as e:  # noqa: BLE001
        _log(f"earnings_calendar: FAILED: {e}")
        return f"failed: {e}"
    return "ok"


def _run_sec(ticker: str, as_of: date, *, force: bool) -> str:
    if not force and _sec_recent(ticker, as_of):
        _log(f"sec: skipped (recent file within {SEC_RECENCY_DAYS}d)")
        return "skipped_recent"
    _log(f"sec: running for {ticker} as_of={as_of.isoformat()}")
    try:
        run_sec_pipeline(ticker, as_of)
    except Exception as e:  # noqa: BLE001
        _log(f"sec: FAILED: {e}")
        return f"failed: {e}"
    return "ok"


def _run_thirteen_f(
    ciks: list[str],
    as_of: date,
    *,
    force: bool,
) -> dict[str, str]:
    """One summary entry per CIK so a single bad CIK is still visible."""
    current_qe = _most_recent_quarter_end(as_of - timedelta(days=THIRTEEN_F_FILING_LAG_DAYS))
    prior_qe = _previous_quarter_end(current_qe)
    _log(
        f"13f: current_quarter_end={current_qe.isoformat()} "
        f"prior_quarter_end={prior_qe.isoformat()}"
    )

    out: dict[str, str] = {}
    for cik in ciks:
        key = f"thirteen_f:{cik}"
        if not force and _thirteen_f_deltas_exists(cik, current_qe):
            _log(f"{key}: skipped (deltas file exists for {current_qe})")
            out[key] = "skipped_recent"
            continue
        _log(f"{key}: running for qtr={current_qe.isoformat()}")
        try:
            run_thirteen_f_pipeline(cik, current_qe, prior_qe)
        except Exception as e:  # noqa: BLE001
            _log(f"{key}: FAILED: {e}")
            out[key] = f"failed: {e}"
            continue
        out[key] = "ok"
    return out


# ---------- recency checks ----------


def _short_interest_recent(ticker: str, as_of: date) -> bool:
    """True if any `records_<TICKER>_<as_of>.parquet` has an encoded as_of
    within `SHORT_INTEREST_RECENCY_DAYS` of the requested as_of."""
    if not SHORT_INTEREST_DIR.exists():
        return False
    pattern = f"records_{ticker.upper()}_*.parquet"
    for f in SHORT_INTEREST_DIR.glob(pattern):
        stem = f.stem
        parts = stem.rsplit("_", 1)  # ["records_AMD", "2026-04-20"]
        if len(parts) != 2:
            continue
        try:
            file_as_of = date.fromisoformat(parts[1])
        except ValueError:
            continue
        if abs((as_of - file_as_of).days) <= SHORT_INTEREST_RECENCY_DAYS:
            return True
    return False


def _index_changes_recent() -> bool:
    """True if any `changes_*.parquet` under `data/index_changes/` has mtime
    within `INDEX_CHANGES_RECENCY_DAYS` of now. Uses file mtime, not the
    encoded as_of — index changes are a shared resource we refresh on wall
    time, not per-request."""
    if not INDEX_CHANGES_DIR.exists():
        return False
    threshold = (datetime.now() - timedelta(days=INDEX_CHANGES_RECENCY_DAYS)).timestamp()
    for f in INDEX_CHANGES_DIR.glob("changes_*.parquet"):
        if f.stat().st_mtime >= threshold:
            return True
    return False


def _thirteen_f_deltas_exists(cik: str, current_quarter_end: date) -> bool:
    cik10 = str(int(cik)).zfill(10)
    path = THIRTEEN_F_DIR / f"deltas_{cik10}_{current_quarter_end.isoformat()}.parquet"
    return path.exists()


def _news_recent(ticker: str, as_of: date) -> bool:
    """True if any `news_<TICKER>_<date>.parquet` has an encoded as_of within
    `NEWS_RECENCY_DAYS` of the requested as_of."""
    return _recent_by_encoded_as_of(
        NEWS_DIR, f"news_{ticker.upper()}_*.parquet", as_of, NEWS_RECENCY_DAYS
    )


def _earnings_recent(as_of: date) -> bool:
    """True if any shared `calendar_<date>.parquet` has an encoded as_of
    within `EARNINGS_RECENCY_DAYS` of the requested as_of."""
    return _recent_by_encoded_as_of(
        EARNINGS_DIR, "calendar_*.parquet", as_of, EARNINGS_RECENCY_DAYS
    )


def _sec_recent(ticker: str, as_of: date) -> bool:
    """True if any `events_<TICKER>_<date>.parquet` has an encoded as_of
    within `SEC_RECENCY_DAYS` of the requested as_of."""
    return _recent_by_encoded_as_of(
        SEC_DIR, f"events_{ticker.upper()}_*.parquet", as_of, SEC_RECENCY_DAYS
    )


def _recent_by_encoded_as_of(
    dir_: Path, pattern: str, as_of: date, within_days: int
) -> bool:
    """Generic helper: does any file matching `pattern` under `dir_` have an
    ISO-date suffix (before `.parquet`) within `within_days` of `as_of`?"""
    if not dir_.exists():
        return False
    for f in dir_.glob(pattern):
        parts = f.stem.rsplit("_", 1)
        if len(parts) != 2:
            continue
        try:
            file_as_of = date.fromisoformat(parts[1])
        except ValueError:
            continue
        if abs((as_of - file_as_of).days) <= within_days:
            return True
    return False


# ---------- quarter-end math ----------


def _most_recent_quarter_end(d: date) -> date:
    """Most recent calendar quarter-end strictly <= `d`. Quarter-ends are
    Mar 31, Jun 30, Sep 30, Dec 31."""
    if d.month == 12 and d.day == 31:
        return d
    if d.month >= 10:
        return date(d.year, 9, 30)
    if d.month >= 7:
        return date(d.year, 6, 30)
    if d.month >= 4:
        return date(d.year, 3, 31)
    return date(d.year - 1, 12, 31)


def _previous_quarter_end(qe: date) -> date:
    """Given a quarter-end, return the previous quarter-end."""
    if qe.month == 3:
        return date(qe.year - 1, 12, 31)
    if qe.month == 6:
        return date(qe.year, 3, 31)
    if qe.month == 9:
        return date(qe.year, 6, 30)
    return date(qe.year, 9, 30)  # qe.month == 12


# ---------- logging ----------


def _log(msg: str) -> None:
    print(f"[prep] {msg}", file=sys.stderr)


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="demo.prep_ticker",
        description="Populate data/ for a single ticker (short interest, index changes, 13F).",
    )
    parser.add_argument("ticker", help="Ticker symbol, e.g. AMD")
    parser.add_argument("as_of", help="ISO date, e.g. 2026-04-24")
    parser.add_argument(
        "--funds",
        default=None,
        help=f"Comma-separated fund CIKs (default: {','.join(DEFAULT_FUND_CIKS)})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run all fetchers even if recent output already exists.",
    )
    args = parser.parse_args(argv)

    funds = [c.strip() for c in args.funds.split(",")] if args.funds else None
    prep_ticker(
        ticker=args.ticker,
        as_of=date.fromisoformat(args.as_of),
        fund_ciks=funds,
        force=args.force,
    )


if __name__ == "__main__":
    main()
