"""
Analyst rating + price-target change ingestion.

Source: yfinance's `Ticker(t).upgrades_downgrades` DataFrame. Columns:
    Firm, ToGrade, FromGrade, Action, priceTargetAction,
    currentPriceTarget, priorPriceTarget
Indexed by GradeDate (datetime).

Both `AnalystRating` and `PriceTargetChange` are derived from the same
DataFrame so the underlying yfinance call happens once. When yfinance doesn't
expose a price target (0.0 or missing), we emit the AnalystRating only.

Pipeline shape mirrors short_interest.py / thirteen_f.py:

    fetch_rating_changes(ticker, as_of)              -> list[AnalystRating]
    fetch_price_target_changes(ticker, as_of)        -> list[PriceTargetChange]
    fetch_all_analyst_actions(ticker, as_of)         -> (ratings, targets)
    ratings_to_events(ratings)                       -> list[Event]
    targets_to_events(targets)                       -> list[Event]
    events_to_text_chunks(events)                    -> list[TextChunk]
    run_analyst_actions_pipeline(ticker, as_of, ...) -> (ratings, targets, events, chunks)

as_of rule: action_date <= as_of. Analyst actions are public same-day, so
action_date IS publication date.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from schema import (
    AnalystRating,
    Event,
    PriceTargetChange,
    RatingAction,
    SourceType,
    TextChunk,
)

LOG = logging.getLogger(__name__)

DATA_DIR = Path("data/analyst_actions")


# Raw-grade → normalized-bucket map. Keys are lowercased / stripped.
RATING_MAP: dict[str, str] = {
    # Buy bucket
    "buy": "Buy",
    "strong buy": "Buy",
    "outperform": "Buy",
    "overweight": "Buy",
    "positive": "Buy",
    "accumulate": "Buy",
    "add": "Buy",
    "conviction buy": "Buy",
    "top pick": "Buy",
    "sector outperform": "Buy",
    # Hold bucket
    "hold": "Hold",
    "neutral": "Hold",
    "market perform": "Hold",
    "equal weight": "Hold",
    "equal-weight": "Hold",
    "peer perform": "Hold",
    "sector perform": "Hold",
    "in-line": "Hold",
    "inline": "Hold",
    "mixed": "Hold",
    # Sell bucket
    "sell": "Sell",
    "strong sell": "Sell",
    "underperform": "Sell",
    "underweight": "Sell",
    "negative": "Sell",
    "reduce": "Sell",
    "sector underperform": "Sell",
}

# yfinance's Action column → RatingAction hint.
_YF_ACTION_HINTS: dict[str, RatingAction] = {
    "up": RatingAction.UPGRADE,
    "down": RatingAction.DOWNGRADE,
    "init": RatingAction.INITIATE,
    "reit": RatingAction.REITERATE,
    "main": RatingAction.REITERATE,
}


# Injectable Ticker factory: defaults to the real yfinance import. Tests swap
# this out via the `ticker_factory` kwarg on the public functions.
def _default_ticker_factory(ticker: str):  # pragma: no cover — trivial
    import yfinance as yf
    return yf.Ticker(ticker)


# ---------- Public API ----------

def fetch_rating_changes(
    ticker: str,
    as_of: date,
    *,
    ticker_factory: Callable[[str], object] = _default_ticker_factory,
) -> list[AnalystRating]:
    """All AnalystRating rows for `ticker` with action_date <= as_of."""
    ratings, _ = fetch_all_analyst_actions(ticker, as_of, ticker_factory=ticker_factory)
    return ratings


def fetch_price_target_changes(
    ticker: str,
    as_of: date,
    *,
    ticker_factory: Callable[[str], object] = _default_ticker_factory,
) -> list[PriceTargetChange]:
    """All PriceTargetChange rows for `ticker` with action_date <= as_of."""
    _, targets = fetch_all_analyst_actions(ticker, as_of, ticker_factory=ticker_factory)
    return targets


def fetch_all_analyst_actions(
    ticker: str,
    as_of: date,
    *,
    ticker_factory: Callable[[str], object] = _default_ticker_factory,
) -> tuple[list[AnalystRating], list[PriceTargetChange]]:
    """One yfinance call, split into (ratings, targets)."""
    df = _fetch_upgrades_downgrades(ticker, ticker_factory)
    if df is None or df.empty:
        return [], []

    ratings: list[AnalystRating] = []
    targets: list[PriceTargetChange] = []
    for action_date, row in _iter_rows(df, as_of):
        firm = _safe_str(row.get("Firm"))
        if not firm:
            continue
        to_grade = _safe_str(row.get("ToGrade"))
        from_grade = _safe_str(row.get("FromGrade")) or None
        yf_action = _safe_str(row.get("Action")).lower() or None

        rating_action = classify_action(from_grade, to_grade, yf_action)
        rating_id = generate_rating_id(ticker, firm, action_date, rating_action)
        ratings.append(
            AnalystRating(
                rating_id=rating_id,
                ticker=ticker.upper(),
                analyst_firm=firm,
                analyst_name=None,  # yfinance doesn't expose it
                action=rating_action,
                new_rating=to_grade or None,
                prior_rating=from_grade,
                action_date=action_date,
                source_url=None,
            )
        )

        new_target = _safe_float(row.get("currentPriceTarget"))
        prior_target = _safe_float(row.get("priorPriceTarget"))
        if new_target is None:
            continue  # no target → AnalystRating only, no PriceTargetChange

        change_pct: Optional[float] = None
        if prior_target is not None and prior_target > 0:
            change_pct = (new_target - prior_target) / prior_target

        targets.append(
            PriceTargetChange(
                target_id=generate_target_id(ticker, firm, action_date, new_target, prior_target),
                ticker=ticker.upper(),
                analyst_firm=firm,
                analyst_name=None,
                new_target=new_target,
                prior_target=prior_target,
                change_pct=change_pct,
                action_date=action_date,
                source_url=None,
            )
        )
    return ratings, targets


def ratings_to_events(ratings: list[AnalystRating]) -> list[Event]:
    events: list[Event] = []
    for r in ratings:
        events.append(
            Event(
                event_id=r.rating_id,
                ticker=r.ticker,
                event_date=r.action_date,
                event_type=f"analyst_{r.action.value}",
                source=r.analyst_firm,
                payload_ref=r.rating_id,
                text=_rating_text(r),
            )
        )
    return events


def targets_to_events(targets: list[PriceTargetChange]) -> list[Event]:
    events: list[Event] = []
    for t in targets:
        events.append(
            Event(
                event_id=t.target_id,
                ticker=t.ticker,
                event_date=t.action_date,
                event_type="analyst_price_target",
                source=t.analyst_firm,
                payload_ref=t.target_id,
                text=_target_text(t),
            )
        )
    return events


def events_to_text_chunks(events: list[Event]) -> list[TextChunk]:
    """
    Materialize Events as TextChunks.

    SourceType has no ANALYST value; use NEWS as the closest fit (analyst
    calls are publicly reported events). Flag in commit message — adding
    SourceType.ANALYST requires team sign-off per CLAUDE.md rule #5.
    """
    chunks: list[TextChunk] = []
    for e in events:
        if not e.text:
            continue
        chunks.append(
            TextChunk(
                chunk_id=e.event_id,
                ticker=e.ticker,
                source_type=SourceType.NEWS,  # SourceType.ANALYST not defined
                publication_date=e.event_date,
                source_url=None,
                section_name=e.event_type,
                text=e.text,
                token_count=len(e.text.split()),
            )
        )
    return chunks


def run_analyst_actions_pipeline(
    ticker: str,
    as_of: date,
    output_dir: Path | str = DATA_DIR,
    *,
    ticker_factory: Callable[[str], object] = _default_ticker_factory,
) -> tuple[list[AnalystRating], list[PriceTargetChange], list[Event], list[TextChunk]]:
    """End-to-end: yfinance → ratings + targets → events → chunks → parquet + JSONL."""
    ratings, targets = fetch_all_analyst_actions(
        ticker, as_of, ticker_factory=ticker_factory,
    )
    events = ratings_to_events(ratings) + targets_to_events(targets)
    chunks = events_to_text_chunks(events)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = as_of.isoformat()
    suffix = f"_{ticker.upper()}"

    save_ratings_to_parquet(ratings, output_dir / f"ratings{suffix}_{stamp}.parquet")
    save_targets_to_parquet(targets, output_dir / f"targets{suffix}_{stamp}.parquet")
    _save_events_to_parquet(events, output_dir / f"events{suffix}_{stamp}.parquet")
    _write_chunks_jsonl(chunks, output_dir / f"chunks{suffix}_{stamp}.jsonl")
    return ratings, targets, events, chunks


# ---------- Rating normalization + action classification ----------

def normalize_rating(rating: Optional[str]) -> Optional[str]:
    """
    Map a firm-specific rating string to a standard Buy / Hold / Sell bucket.

    Unknown ratings return the raw string (stripped) and log an info line so
    we can curate RATING_MAP over time without exploding on new input.
    """
    if not rating:
        return None
    key = rating.strip().lower()
    if key in RATING_MAP:
        return RATING_MAP[key]
    LOG.info("unknown analyst rating %r — passing through", rating)
    return rating.strip()


def classify_action(
    prior_rating: Optional[str],
    new_rating: Optional[str],
    yf_action_hint: Optional[str] = None,
) -> RatingAction:
    """
    Derive a RatingAction from prior/new rating buckets.

    If prior_rating is missing → INITIATE.
    If normalized buckets move Buy↔Hold/Sell (or Sell→Hold/Buy) → UP/DOWNGRADE.
    If buckets match → REITERATE.
    yf_action_hint ('up'/'down'/'init'/'reit'/'main') is used as a tiebreaker
    when bucket inference is ambiguous (e.g. unknown raw ratings).
    """
    if not prior_rating:
        return RatingAction.INITIATE

    prior_bucket = normalize_rating(prior_rating)
    new_bucket = normalize_rating(new_rating) if new_rating else None

    bucket_rank = {"Sell": 0, "Hold": 1, "Buy": 2}
    if prior_bucket in bucket_rank and new_bucket in bucket_rank:
        if bucket_rank[new_bucket] > bucket_rank[prior_bucket]:
            return RatingAction.UPGRADE
        if bucket_rank[new_bucket] < bucket_rank[prior_bucket]:
            return RatingAction.DOWNGRADE
        return RatingAction.REITERATE

    # Bucket lookup failed → fall back to yfinance's Action hint
    if yf_action_hint and yf_action_hint in _YF_ACTION_HINTS:
        return _YF_ACTION_HINTS[yf_action_hint]
    # Final fallback: unchanged strings → REITERATE, else UPGRADE as a
    # non-erroring default (kept consistent so downstream never sees None).
    if (prior_rating or "").strip().lower() == (new_rating or "").strip().lower():
        return RatingAction.REITERATE
    return RatingAction.REITERATE


def generate_rating_id(
    ticker: str, firm: str, action_date: date, action: RatingAction,
) -> str:
    """Stable rating_id: rating_{firm_slug}_{TICKER}_{YYYY-MM-DD}_{action}."""
    firm_slug = _slug(firm)
    return (
        f"rating_{firm_slug}_{ticker.upper()}_{action_date.isoformat()}_{action.value}"
    )


def generate_target_id(
    ticker: str, firm: str, action_date: date,
    new_target: Optional[float], prior_target: Optional[float],
) -> str:
    """Stable target_id: target_{firm_slug}_{TICKER}_{YYYY-MM-DD}_{direction}."""
    if prior_target and new_target and new_target > prior_target:
        direction = "raise"
    elif prior_target and new_target and new_target < prior_target:
        direction = "lower"
    elif not prior_target and new_target:
        direction = "initiate"
    else:
        direction = "maintain"
    firm_slug = _slug(firm)
    return f"target_{firm_slug}_{ticker.upper()}_{action_date.isoformat()}_{direction}"


# ---------- I/O helpers ----------

def save_ratings_to_parquet(ratings: list[AnalystRating], filepath: Path | str) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "rating_id", "ticker", "analyst_firm", "analyst_name",
        "action", "new_rating", "prior_rating", "action_date", "source_url",
    ]
    if not ratings:
        pd.DataFrame(columns=columns).to_parquet(filepath, index=False)
        return
    df = pd.DataFrame([r.model_dump() for r in ratings])
    df["action"] = df["action"].astype(str)
    df["action_date"] = df["action_date"].astype(str)
    df[columns].to_parquet(filepath, compression="snappy", index=False)


def save_targets_to_parquet(targets: list[PriceTargetChange], filepath: Path | str) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "target_id", "ticker", "analyst_firm", "analyst_name",
        "new_target", "prior_target", "change_pct", "action_date", "source_url",
    ]
    if not targets:
        pd.DataFrame(columns=columns).to_parquet(filepath, index=False)
        return
    df = pd.DataFrame([t.model_dump() for t in targets])
    df["action_date"] = df["action_date"].astype(str)
    df[columns].to_parquet(filepath, compression="snappy", index=False)


def _save_events_to_parquet(events: list[Event], filepath: Path | str) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    columns = ["event_id", "ticker", "event_date", "event_type",
               "source", "payload_ref", "text"]
    if not events:
        pd.DataFrame(columns=columns).to_parquet(filepath, index=False)
        return
    df = pd.DataFrame([e.model_dump() for e in events])
    df["event_date"] = df["event_date"].astype(str)
    df[columns].to_parquet(filepath, compression="snappy", index=False)


def _write_chunks_jsonl(chunks: list[TextChunk], filepath: Path | str) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(
        "\n".join(c.model_dump_json() for c in chunks),
        encoding="utf-8",
    )


# ---------- internals ----------

def _fetch_upgrades_downgrades(
    ticker: str, factory: Callable[[str], object],
) -> Optional[pd.DataFrame]:
    try:
        yft = factory(ticker)
        df = getattr(yft, "upgrades_downgrades", None)
    except Exception as e:  # noqa: BLE001
        LOG.warning("yfinance fetch for %s failed: %s", ticker, e)
        return None
    if df is None:
        return None
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    return df


def _iter_rows(df: pd.DataFrame, as_of: date):
    """Yield (action_date, row_dict) pairs with action_date <= as_of."""
    for raw_idx, row in df.iterrows():
        action_date = _to_date(raw_idx)
        if action_date is None or action_date > as_of:
            continue
        yield action_date, row


def _to_date(raw) -> Optional[date]:
    if isinstance(raw, date) and not isinstance(raw, pd.Timestamp):
        return raw
    try:
        return pd.Timestamp(raw).date()
    except Exception:  # noqa: BLE001
        return None


def _safe_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    # yfinance uses 0.0 to mean "no prior target"; treat as missing.
    if f <= 0.0:
        return None
    return f


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", text.strip()).strip("_").lower()
    return s or "unknown"


def _rating_text(r: AnalystRating) -> str:
    prior_part = f" (from {r.prior_rating})" if r.prior_rating else ""
    return (
        f"{r.analyst_firm} {r.action.value} {r.ticker} to "
        f"{r.new_rating or 'n/a'}{prior_part} on {r.action_date.isoformat()}."
    )


def _target_text(t: PriceTargetChange) -> str:
    pct = f" ({t.change_pct:+.1%})" if t.change_pct is not None else ""
    prior_part = f" from ${t.prior_target:.2f}" if t.prior_target else ""
    new_part = f"${t.new_target:.2f}" if t.new_target else "n/a"
    return (
        f"{t.analyst_firm} price target on {t.ticker}: "
        f"{new_part}{prior_part}{pct} on {t.action_date.isoformat()}."
    )
