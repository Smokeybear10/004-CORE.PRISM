"""
FDA regulatory-event ingestion.

Two sources, combined into a single `FDAEvent` stream:

1. **openFDA `/drug/drugsfda.json`** — programmatic queries for drug approvals
   (submission_status="AP") and tentative approvals. No API key required at
   low volumes; documented limit is 240 requests/min (1000/hour without key).
   CRLs are not in this dataset — FDA does not publish Complete Response
   Letters, so CRLs only appear via the hand-curated calendar seed below.

2. **Hand-curated PDUFA / AdComm calendar seed** — `fda_calendar_seed.json`
   alongside this module. ~15 well-known historical PDUFA dates, AdComm
   meetings, and the handful of publicly reported CRLs. Documented as an
   MVP limitation: replace with a live calendar scraper post-hackathon.

Pipeline shape mirrors `short_interest.py` / `thirteen_f.py`:

    fetch_fda_events(as_of)            -> list[FDAEvent]
    fda_events_to_events(fda_events)   -> list[Event]
    events_to_text_chunks(events)      -> list[TextChunk]
    run_fda_pipeline(as_of, ...)       -> tuple[records, events, chunks]
        writes parquet + JSONL under data/fda/

as_of rule (CLAUDE.md #1): for PDUFA / AdComm calendar entries, event_date IS
the publication date — FDA publishes the calendar in advance, so the market
sees the upcoming target date. For approvals and CRLs, event_date is the
decision-announcement date.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from schema import Event, FDAEvent, FDAEventType, SourceType, TextChunk

LOG = logging.getLogger(__name__)

OPENFDA_URL = "https://api.fda.gov/drug/drugsfda.json"
USER_AGENT = "BW-Hackathon henryji327@gmail.com"
DATA_DIR = Path("data/fda")
CALENDAR_SEED_PATH = Path(__file__).parent / "fda_calendar_seed.json"


# Top ~40 biotech / pharma sponsor → ticker map. openFDA's sponsor_name is
# upper-cased and sometimes abbreviated ("LILLY", "PFIZER INC"); we normalize
# both sides before lookup. Private companies / foreign sponsors / subsidiaries
# we don't recognize return None (documented limitation). Entries with None
# as the value are "known private" sponsors — listed explicitly so a future
# contributor doesn't spend time tracking down a ticker that doesn't exist.
SPONSOR_TO_TICKER: dict[str, Optional[str]] = {
    # Big pharma
    "pfizer": "PFE",
    "merck": "MRK",
    "eli lilly": "LLY",
    "lilly": "LLY",
    "johnson & johnson": "JNJ",
    "janssen": "JNJ",
    "bristol myers squibb": "BMY",
    "bristol-myers squibb": "BMY",
    "abbvie": "ABBV",
    "novartis": "NVS",
    "roche": "RHHBY",
    "genentech": "RHHBY",  # Roche subsidiary
    "sanofi": "SNY",
    "glaxosmithkline": "GSK",
    "gsk": "GSK",
    "astrazeneca": "AZN",
    "novo nordisk": "NVO",
    "takeda": "TAK",
    "bayer": "BAYRY",
    "boehringer ingelheim": None,  # private
    # US biotech (large/mid)
    "biogen": "BIIB",
    "gilead": "GILD",
    "gilead sciences": "GILD",
    "moderna": "MRNA",
    "regeneron": "REGN",
    "vertex": "VRTX",
    "vertex pharmaceuticals": "VRTX",
    "amgen": "AMGN",
    "alnylam": "ALNY",
    "alnylam pharmaceuticals": "ALNY",
    "ionis": "IONS",
    "ionis pharmaceuticals": "IONS",
    "sage therapeutics": "SAGE",
    "sage": "SAGE",
    "incyte": "INCY",
    "illumina": "ILMN",
    "biomarin": "BMRN",
    "biomarin pharmaceutical": "BMRN",
    "alexion": "AZN",  # acquired by AstraZeneca 2021
    "seagen": "PFE",  # acquired by Pfizer 2023
    "horizon therapeutics": "AMGN",  # acquired by Amgen 2023
    # Gene editing / next-gen
    "beam therapeutics": "BEAM",
    "editas medicine": "EDIT",
    "intellia therapeutics": "NTLA",
    "crispr therapeutics": "CRSP",
    # Well-known smaller
    "sarepta": "SRPT",
    "sarepta therapeutics": "SRPT",
    "bluebird bio": "BLUE",
    "united therapeutics": "UTHR",
    "jazz pharmaceuticals": "JAZZ",
    "neurocrine": "NBIX",
    "neurocrine biosciences": "NBIX",
    "mirati therapeutics": "BMY",  # acquired by BMS 2024
    "blueprint medicines": "BPMC",
    "exelixis": "EXEL",
}


# ---------- Public API ----------

def fetch_fda_calendar(as_of: date) -> list[FDAEvent]:
    """
    Load hand-curated PDUFA / AdComm / CRL calendar entries from the seed
    JSON and filter to `event_date <= as_of`. For calendar entries, event_date
    IS the publication date (FDA publishes upcoming target dates in advance).
    """
    return _load_calendar_seed(as_of)


def fetch_fda_actions(
    ticker: Optional[str],
    as_of: date,
    since: Optional[date] = None,
    *,
    limit: int = 1000,
    session: Optional[requests.Session] = None,
) -> list[FDAEvent]:
    """
    Fetch FDA regulatory actions (approvals) from openFDA's drugsfda endpoint.

    Args:
        ticker: If provided, filter to sponsors that map to this ticker.
            openFDA has no ticker field, so we apply this post-fetch against
            SPONSOR_TO_TICKER.
        as_of: Only return events with event_date <= as_of. No foreknowledge.
        since: Optional lower bound on event_date (defaults to 2015-01-01).
        limit: Max records per request page (openFDA caps at 1000).
        session: Optional requests.Session for connection reuse / tests.

    Returns:
        List of FDAEvent(event_type=APPROVAL) records.

    openFDA does NOT publish CRLs, denials, or AdComm outcomes via
    `drugsfda` — the submission status enum only exposes AP (approved) and
    TA (tentative approval). Those other `FDAEventType`s only come from the
    hand-curated calendar seed.
    """
    since = since or date(2015, 1, 1)
    if since > as_of:
        return []

    sess = session or _session()
    # openFDA's search grammar uses spaces as AND separators. requests
    # URL-encodes spaces to "+" and "+" to "%2B", so we MUST use real
    # spaces here — passing literal "+" makes openFDA return HTTP 500.
    params = {
        "search": (
            f"submissions.submission_status:AP AND "
            f"submissions.submission_status_date:[{_fda_date(since)} TO {_fda_date(as_of)}]"
        ),
        "limit": limit,
        "skip": 0,
    }

    events: list[FDAEvent] = []
    seen_ids: set[str] = set()
    # openFDA paginates via `skip`; stop when a page returns < limit or errors.
    while True:
        resp = sess.get(OPENFDA_URL, params=params, timeout=60)
        if resp.status_code == 404:
            # openFDA returns 404 when no results remain in the range
            break
        resp.raise_for_status()
        payload = resp.json()
        results = payload.get("results", [])
        if not results:
            break
        for entry in results:
            for ev in _openfda_entry_to_events(entry, as_of, since):
                if ev.event_id in seen_ids:
                    continue
                if ticker and ev.sponsor_ticker != ticker.upper():
                    continue
                seen_ids.add(ev.event_id)
                events.append(ev)
        if len(results) < limit:
            break
        params["skip"] += limit
        # openFDA caps skip at 25,000 in the no-key tier — bail out before then
        if params["skip"] >= 25_000:
            LOG.warning("fetch_fda_actions hit openFDA skip cap (25k); truncating")
            break
    return events


def fetch_fda_events(
    as_of: date,
    ticker: Optional[str] = None,
    since: Optional[date] = None,
    *,
    session: Optional[requests.Session] = None,
) -> list[FDAEvent]:
    """
    Combined calendar + live approvals, filtered to `event_date <= as_of`.

    Calendar and openFDA event_ids don't follow the same format, so we can't
    dedup on event_id. Instead we compute a canonical key per event —
    (sponsor_ticker, drug_slug, event_date) — and let the hand-curated
    calendar win on collision. Drugs with long brand names where the slug
    diverges between sources will still produce duplicates; that's an
    accepted MVP limitation.

    `since` is applied to openFDA results only. The hand-curated calendar
    is small and fully filtered by `as_of`; adding a `since` filter there
    would have little payoff.
    """
    calendar = fetch_fda_calendar(as_of)
    actions = fetch_fda_actions(ticker, as_of, since=since, session=session)

    by_key: dict[tuple[Optional[str], str, date], FDAEvent] = {}
    # Insert openFDA first; calendar entries then overwrite on collision.
    for ev in actions:
        by_key[_canonical_event_key(ev)] = ev
    for ev in calendar:
        by_key[_canonical_event_key(ev)] = ev

    out = list(by_key.values())
    if ticker:
        out = [v for v in out if v.sponsor_ticker == ticker.upper()]
    return sorted(out, key=lambda e: e.event_date)


def _canonical_event_key(ev: FDAEvent) -> tuple[Optional[str], str, date]:
    """
    Canonical dedup key for an FDAEvent: (ticker, drug_brand_slug, event_date).

    Seed and openFDA name the same drug differently:
      seed:    "Leqembi (lecanemab)"
      openFDA: "LEQEMBI"
    We normalize by taking only the first parenthetical-free token, so
    "Leqembi (lecanemab)" and "LEQEMBI" both key on "LEQEMBI".
    """
    return (ev.sponsor_ticker, _brand_slug(ev.drug_name), ev.event_date)


def _brand_slug(drug_name: str) -> str:
    """First bracket-free token of a drug name, slugged. Empty → 'UNKNOWN'."""
    if not drug_name:
        return "UNKNOWN"
    # Strip anything in parentheses or brackets (generic name suffixes)
    head = drug_name.split("(")[0].split("[")[0].strip()
    # First whitespace-separated token
    first = head.split()[0] if head.split() else ""
    return _slug(first) or "UNKNOWN"


def fda_events_to_events(fda_events: list[FDAEvent]) -> list[Event]:
    """
    Wrap each FDAEvent in the unified Event envelope.
    event_date follows the FDAEvent's event_date (which is already the
    publication date per CLAUDE.md rule #1 — see module docstring).
    """
    events: list[Event] = []
    for f in fda_events:
        events.append(
            Event(
                event_id=f.event_id,
                ticker=f.sponsor_ticker or "_UNMAPPED",
                event_date=f.event_date,
                event_type=f"fda_{f.event_type.value}",
                source="FDA",
                payload_ref=f.event_id,
                text=_event_text(f),
            )
        )
    return events


def events_to_text_chunks(
    events: list[Event],
    fda_events: Optional[list[FDAEvent]] = None,
) -> list[TextChunk]:
    """
    Materialize Events as TextChunks the attribution model can cite.

    `fda_events` is an optional index of the upstream FDAEvent records keyed
    by event_id. When provided, we preserve the FDAEvent's `source_url`
    (FDA press-release URL, openFDA application URL, etc.) on the chunk. The
    unified `Event` envelope has no source_url field, so without this we
    lose provenance for the attribution model.

    NOTE: SourceType has no FDA-specific enum value, so we use NEWS as the
    closest fit (FDA press releases ARE news). Flag this in commit message
    as a schema gap — adding FDA to SourceType requires team sign-off per
    CLAUDE.md rule #5.
    """
    url_by_id: dict[str, Optional[str]] = {}
    if fda_events is not None:
        url_by_id = {f.event_id: f.source_url for f in fda_events}

    chunks: list[TextChunk] = []
    for e in events:
        if not e.text:
            continue
        chunks.append(
            TextChunk(
                chunk_id=e.event_id,
                ticker=e.ticker,
                source_type=SourceType.NEWS,  # SourceType.FDA not defined — see note above
                publication_date=e.event_date,
                source_url=url_by_id.get(e.event_id) or OPENFDA_URL,
                section_name=e.event_type,
                text=e.text,
                token_count=len(e.text.split()),
            )
        )
    return chunks


def run_fda_pipeline(
    as_of: date,
    ticker: Optional[str] = None,
    since: Optional[date] = None,
    output_dir: Path | str = DATA_DIR,
    *,
    session: Optional[requests.Session] = None,
) -> tuple[list[FDAEvent], list[Event], list[TextChunk]]:
    """End-to-end: fetch, wrap, chunk, write parquet + JSONL."""
    fda_events = fetch_fda_events(as_of, ticker=ticker, since=since, session=session)
    events = fda_events_to_events(fda_events)
    chunks = events_to_text_chunks(events, fda_events=fda_events)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = as_of.isoformat()
    suffix = f"_{ticker.upper()}" if ticker else ""

    save_fda_events_to_parquet(fda_events, output_dir / f"records{suffix}_{stamp}.parquet")
    _save_events_to_parquet(events, output_dir / f"events{suffix}_{stamp}.parquet")
    _write_chunks_jsonl(chunks, output_dir / f"chunks{suffix}_{stamp}.jsonl")
    return fda_events, events, chunks


# ---------- Sponsor → ticker ----------

def map_sponsor_to_ticker(sponsor_name: str) -> Optional[str]:
    """Map an openFDA sponsor_name to a public-equity ticker, or None."""
    key = _normalize_sponsor(sponsor_name)
    if not key:
        return None
    # Direct hit
    if key in SPONSOR_TO_TICKER:
        return SPONSOR_TO_TICKER[key]
    # Startswith fallback: "pfizer inc" → "pfizer"
    for sponsor, ticker in SPONSOR_TO_TICKER.items():
        if key.startswith(sponsor):
            return ticker
    return None


def _normalize_sponsor(name: str) -> str:
    if not name:
        return ""
    lowered = name.strip().lower()
    # Strip common corporate suffixes
    for suffix in (
        " incorporated", " inc.", " inc", " corporation", " corp.", " corp",
        " ltd.", " ltd", " llc", " plc", " ag", " sa", " nv", " se",
        " co.", " co", " company", " holdings",
    ):
        if lowered.endswith(suffix):
            lowered = lowered[: -len(suffix)]
    # Collapse ampersand spacing
    return " ".join(lowered.split())


# ---------- I/O helpers ----------

def save_fda_events_to_parquet(
    fda_events: list[FDAEvent],
    filepath: Path | str,
) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "event_id", "event_type", "event_date", "sponsor_ticker",
        "drug_name", "indication", "description", "source_url",
    ]
    if not fda_events:
        pd.DataFrame(columns=columns).to_parquet(filepath, index=False)
        return
    df = pd.DataFrame([f.model_dump() for f in fda_events])
    df["event_type"] = df["event_type"].astype(str)
    df["event_date"] = df["event_date"].astype(str)
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

def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    s.headers["Accept"] = "application/json"
    return s


def _fda_date(d: date) -> str:
    """openFDA expects YYYYMMDD in search ranges."""
    return d.strftime("%Y%m%d")


def _parse_fda_date(raw: str) -> Optional[date]:
    if not raw or len(raw) < 8:
        return None
    try:
        return datetime.strptime(raw[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _load_calendar_seed(as_of: date) -> list[FDAEvent]:
    if not CALENDAR_SEED_PATH.exists():
        LOG.warning("FDA calendar seed missing at %s", CALENDAR_SEED_PATH)
        return []
    raw = json.loads(CALENDAR_SEED_PATH.read_text(encoding="utf-8"))
    out: list[FDAEvent] = []
    for row in raw:
        ev = FDAEvent(**row)
        if ev.event_date > as_of:
            continue
        out.append(ev)
    return out


def _openfda_entry_to_events(
    entry: dict, as_of: date, since: date,
) -> list[FDAEvent]:
    """One application entry can yield multiple FDAEvents (ORIG + SUPPL approvals)."""
    sponsor = entry.get("sponsor_name", "")
    ticker = map_sponsor_to_ticker(sponsor)
    app_num = entry.get("application_number", "UNKNOWN")
    products = entry.get("products") or []
    drug_name = _best_drug_name(products) or app_num

    out: list[FDAEvent] = []
    for sub in entry.get("submissions", []):
        if sub.get("submission_status") != "AP":
            continue
        event_date = _parse_fda_date(sub.get("submission_status_date", ""))
        if event_date is None or event_date < since or event_date > as_of:
            continue
        sub_type = sub.get("submission_type", "")
        sub_num = sub.get("submission_number", "")
        ticker_part = ticker or "UNMAPPED"
        drug_slug = _slug(drug_name)
        event_id = (
            f"fda_approval_{ticker_part}_{drug_slug}_{event_date.isoformat()}"
            f"_{app_num}_{sub_type}_{sub_num}"
        )
        indication = _indication_from_products(products)
        description = (
            f"FDA approval ({sub_type} submission) for {drug_name} "
            f"by {sponsor}. Application {app_num}."
        )
        out.append(
            FDAEvent(
                event_id=event_id,
                event_type=FDAEventType.APPROVAL,
                event_date=event_date,
                sponsor_ticker=ticker,
                drug_name=drug_name,
                indication=indication,
                description=description,
                source_url=f"{OPENFDA_URL}?search=application_number:{app_num}",
            )
        )
    return out


def _best_drug_name(products: list[dict]) -> Optional[str]:
    # Prefer the first unique brand_name
    seen = []
    for p in products:
        name = (p.get("brand_name") or "").strip()
        if name and name not in seen:
            seen.append(name)
    return seen[0] if seen else None


def _indication_from_products(products: list[dict]) -> Optional[str]:
    # openFDA doesn't expose indication on drugsfda; fall back to dosage form + route
    for p in products:
        route = p.get("route")
        form = p.get("dosage_form")
        if route or form:
            return ", ".join(x for x in (form, route) if x)
    return None


def _slug(text: str) -> str:
    """Uppercase alnum slug; collapse everything else to underscores."""
    out = []
    for ch in text:
        if ch.isalnum():
            out.append(ch.upper())
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_") or "UNKNOWN"


def _event_text(f: FDAEvent) -> str:
    ticker_part = f.sponsor_ticker or "unmapped sponsor"
    indication = f" in {f.indication}" if f.indication else ""
    return (
        f"FDA {f.event_type.value.upper()}: {f.drug_name} ({ticker_part})"
        f"{indication} on {f.event_date.isoformat()}. {f.description}"
    )
