"""
Index-rebalance ingestion.

Covers S&P 500 adds/deletes sourced from the Wikipedia "List of S&P 500
companies" changes table — the de facto canonical source used by
QuantConnect, Kaggle indexing datasets, and most academic work.
(S&P Global and FTSE Russell press pages are JS-heavy and not scrapable
without a headless browser; Wikipedia aggregates the same content with
citations back to S&P's press releases. Documented pivot.)

Emits:
- IndexChange records (one ADD and one DELETE per row)
- Two Events per IndexChange:
    * `index_change_announcement` on announcement_date
    * `index_change_effective`    on effective_date
  (S&P typically announces changes 2-5 business days before they take
  effect; since Wikipedia only gives effective_date, we estimate
  announcement_date = effective_date - 5 calendar days. Document caveat.)

Outputs saved under data/index_changes/.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

from schema import (
    Event,
    IndexChange,
    IndexChangeAction,
    SourceType,
    TextChunk,
)

WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
USER_AGENT = "BW-Hackathon henryji327@gmail.com"
DATA_DIR = Path("data/index_changes")

# S&P typically pre-announces index changes ~3-5 business days before effective.
ANNOUNCEMENT_LEAD_DAYS = 5

# Earliest year we bother collecting.
MIN_YEAR = 2015


# ---------- Public API ----------

def fetch_index_changes(as_of: date) -> list[IndexChange]:
    """
    Return S&P 500 index changes with effective_date <= as_of and
    effective_date.year >= MIN_YEAR.

    Each Wikipedia changes-table row becomes up to two IndexChange records
    (one ADD and one DELETE). A row with no added ticker yields only a DELETE,
    and vice versa.
    """
    rows = _fetch_changes_rows()
    changes: list[IndexChange] = []
    for row in rows:
        effective = row["effective_date"]
        if effective > as_of or effective.year < MIN_YEAR:
            continue
        announcement = _estimate_announcement_date(effective)

        add_tkr = row.get("add_ticker") or ""
        add_sec = row.get("add_security") or ""
        rem_tkr = row.get("remove_ticker") or ""
        rem_sec = row.get("remove_security") or ""

        if add_tkr:
            change_id = f"sp500_add_{add_tkr}_{effective.isoformat()}"
            changes.append(
                IndexChange(
                    change_id=change_id,
                    index_name="S&P 500",
                    action=IndexChangeAction.ADD,
                    ticker=add_tkr,
                    company_name=add_sec or add_tkr,
                    announcement_date=announcement,
                    effective_date=effective,
                    replacing_ticker=rem_tkr or None,
                    source_url=WIKIPEDIA_URL,
                )
            )
        if rem_tkr:
            change_id = f"sp500_del_{rem_tkr}_{effective.isoformat()}"
            changes.append(
                IndexChange(
                    change_id=change_id,
                    index_name="S&P 500",
                    action=IndexChangeAction.DELETE,
                    ticker=rem_tkr,
                    company_name=rem_sec or rem_tkr,
                    announcement_date=announcement,
                    effective_date=effective,
                    replacing_ticker=add_tkr or None,
                    source_url=WIKIPEDIA_URL,
                )
            )
    return changes


def changes_to_events(changes: list[IndexChange]) -> list[Event]:
    """Two Events per change: announcement and effective."""
    events: list[Event] = []
    for c in changes:
        base_text = _change_text(c)
        events.append(
            Event(
                event_id=f"{c.change_id}_announcement",
                ticker=c.ticker,
                event_date=c.announcement_date,
                event_type="index_change_announcement",
                source="S&P Global (via Wikipedia)",
                payload_ref=f"{c.change_id}_announcement",
                text=f"Announced: {base_text}",
            )
        )
        events.append(
            Event(
                event_id=f"{c.change_id}_effective",
                ticker=c.ticker,
                event_date=c.effective_date,
                event_type="index_change_effective",
                source="S&P Global (via Wikipedia)",
                payload_ref=f"{c.change_id}_effective",
                text=f"Effective: {base_text}",
            )
        )
    return events


def events_to_text_chunks(events: list[Event]) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for e in events:
        if not e.text:
            continue
        chunks.append(
            TextChunk(
                chunk_id=e.event_id,
                ticker=e.ticker,
                source_type=SourceType.INDEX_CHANGE,
                publication_date=e.event_date,
                source_url=WIKIPEDIA_URL,
                section_name=e.event_type,
                text=e.text,
                token_count=len(e.text.split()),
            )
        )
    return chunks


def run_index_changes_pipeline(
    as_of: date,
    output_dir: Path | str = DATA_DIR,
) -> tuple[list[IndexChange], list[Event], list[TextChunk]]:
    """End-to-end: scrape, filter, emit, write parquet + JSONL."""
    changes = fetch_index_changes(as_of)
    events = changes_to_events(changes)
    chunks = events_to_text_chunks(events)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = as_of.isoformat()

    save_changes_to_parquet(changes, output_dir / f"changes_{stamp}.parquet")
    _save_events_to_parquet(events, output_dir / f"events_{stamp}.parquet")
    _write_chunks_jsonl(chunks, output_dir / f"chunks_{stamp}.jsonl")
    return changes, events, chunks


# ---------- I/O helpers ----------

def save_changes_to_parquet(
    changes: list[IndexChange],
    filepath: Path | str,
) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    if not changes:
        pd.DataFrame(
            columns=[
                "change_id",
                "index_name",
                "action",
                "ticker",
                "company_name",
                "announcement_date",
                "effective_date",
                "replacing_ticker",
                "source_url",
            ]
        ).to_parquet(filepath, index=False)
        return
    df = pd.DataFrame([c.model_dump() for c in changes])
    df["action"] = df["action"].astype(str)
    for col in ("announcement_date", "effective_date"):
        df[col] = df[col].astype(str)
    df.to_parquet(filepath, compression="snappy", index=False)


def _save_events_to_parquet(events: list[Event], filepath: Path | str) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    if not events:
        pd.DataFrame(
            columns=[
                "event_id",
                "ticker",
                "event_date",
                "event_type",
                "source",
                "payload_ref",
                "text",
            ]
        ).to_parquet(filepath, index=False)
        return
    df = pd.DataFrame([e.model_dump() for e in events])
    df["event_date"] = df["event_date"].astype(str)
    df.to_parquet(filepath, compression="snappy", index=False)


def _write_chunks_jsonl(chunks: list[TextChunk], filepath: Path | str) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(
        "\n".join(c.model_dump_json() for c in chunks),
        encoding="utf-8",
    )


# ---------- Scraper internals ----------

def _fetch_changes_rows() -> list[dict]:
    """
    Parse the 'Selected changes' wikitable into dicts:
        {effective_date, add_ticker, add_security, remove_ticker, remove_security, reason}
    """
    r = requests.get(
        WIKIPEDIA_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    tables = soup.find_all("table", class_="wikitable")
    if len(tables) < 2:
        raise ValueError("Wikipedia page structure changed — changes table not found")

    changes_table = tables[1]
    rows = changes_table.find_all("tr")
    # Skip the two header rows (main + sub-header).
    out: list[dict] = []
    for tr in rows[2:]:
        cells = tr.find_all(["td", "th"])
        if len(cells) < 6:
            continue
        texts = [_clean_cell(c.get_text(" ", strip=True)) for c in cells]
        try:
            effective = _parse_date(texts[0])
        except Exception:
            continue
        out.append(
            {
                "effective_date": effective,
                "add_ticker": texts[1] or None,
                "add_security": texts[2] or None,
                "remove_ticker": texts[3] or None,
                "remove_security": texts[4] or None,
                "reason": texts[5] or None,
            }
        )
    return out


_REF_FOOTNOTE_RE = re.compile(r"\[\s*[\w\s,]*?\]")


def _clean_cell(text: str) -> str:
    """Strip footnote markers like [6] or [ a ] and collapse whitespace."""
    text = _REF_FOOTNOTE_RE.sub("", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _parse_date(text: str) -> date:
    """Accept 'April 9, 2026', 'April 09, 2026', '2026-04-09'."""
    text = text.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {text!r}")


def _estimate_announcement_date(effective: date) -> date:
    """S&P pre-announces ~5 calendar days before effective."""
    return effective - timedelta(days=ANNOUNCEMENT_LEAD_DAYS)


def _change_text(c: IndexChange) -> str:
    verb = "added to" if c.action == IndexChangeAction.ADD else "removed from"
    if c.action == IndexChangeAction.ADD and c.replacing_ticker:
        pair = f" (replacing {c.replacing_ticker})"
    elif c.action == IndexChangeAction.DELETE and c.replacing_ticker:
        pair = f" (replaced by {c.replacing_ticker})"
    else:
        pair = ""
    return (
        f"{c.company_name} ({c.ticker}) {verb} {c.index_name}{pair}. "
        f"Effective {c.effective_date.isoformat()}."
    )
