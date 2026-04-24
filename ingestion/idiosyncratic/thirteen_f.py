"""
13F institutional-holdings ingestion from SEC EDGAR.

Fetches a fund's 13F-HR filings, parses the information table XML (share
holdings only — principal-amount bonds are skipped), resolves CUSIPs to
tickers best-effort via SEC's company_tickers.json (name match), and emits
Event objects of type `13f_delta` on quarter-over-quarter position changes.

CLAUDE.md rule #2: event_date on emitted Events is the filing_date, not the
period_end — that's when the market sees the holdings.

Outputs saved under data/thirteen_f/ as parquet (+ JSONL for TextChunks).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

import pandas as pd
import requests

from schema import (
    Event,
    HoldingAction,
    HoldingDelta,
    HoldingRecord,
    SourceType,
    TextChunk,
)

SEC_USER_AGENT = "BW-Hackathon henryji327@gmail.com"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FILING_ROOT = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/"
INFO_TABLE_NS = "{http://www.sec.gov/edgar/document/thirteenf/informationtable}"
DATA_DIR = Path("data/thirteen_f")

# 13F filings must be submitted within 45 days after quarter end.
_FILING_WINDOW_DAYS = 60  # slack for late/amended filings

_name_ticker_cache: dict[str, str] = {}


# ---------- Public API ----------

def fetch_13f_holdings(fund_cik: str, as_of: date) -> list[HoldingRecord]:
    """
    Return holdings from the fund's most recent 13F-HR filed on or before
    `as_of`. Empty list if no filings match.
    """
    filings = _find_13f_filings(fund_cik, as_of)
    if not filings:
        return []
    return _load_filing_holdings(filings[0])


def compute_holding_deltas(
    fund_cik: str,
    current_quarter_end: date,
    prior_quarter_end: date,
) -> list[HoldingDelta]:
    """
    Compute quarter-over-quarter position changes for `fund_cik` between the
    13F-HR filings whose reportDate equals the two given quarter ends.

    Dedup is keyed on CUSIP, not ticker. Two CUSIPs can resolve to the same
    ticker string (e.g. Alphabet GOOG/GOOGL, Berkshire BRK-A/BRK-B, plus every
    case where name-based ticker resolution falls back to the raw CUSIP).
    Keying on ticker silently overwrote one of the two holdings — a data-loss
    bug. Ticker remains as a display field on the emitted `HoldingDelta`.
    """
    current_by_cusip = _load_quarter_holdings_by_cusip(fund_cik, current_quarter_end)
    prior_by_cusip = _load_quarter_holdings_by_cusip(fund_cik, prior_quarter_end)
    if not current_by_cusip:
        return []

    any_current = next(iter(current_by_cusip.values()))
    current_filing_date = any_current.filing_date
    fund_name = any_current.fund_name
    fund_cik_padded = any_current.fund_cik

    deltas: list[HoldingDelta] = []
    for cusip in sorted(set(current_by_cusip) | set(prior_by_cusip)):
        cur = current_by_cusip.get(cusip)
        prr = prior_by_cusip.get(cusip)
        cur_shares = cur.shares if cur else 0
        prior_shares = prr.shares if prr else 0
        cur_value = cur.market_value if cur else 0
        prior_value = prr.market_value if prr else 0
        # Display ticker comes from the current holding if present, else the prior.
        ticker = (cur or prr).ticker

        if prr is None:
            action = HoldingAction.NEW
            prior_shares_field: Optional[int] = None
        elif cur is None:
            action = HoldingAction.EXITED
            prior_shares_field = prior_shares
        elif cur_shares > prior_shares:
            action = HoldingAction.INCREASED
            prior_shares_field = prior_shares
        elif cur_shares < prior_shares:
            action = HoldingAction.REDUCED
            prior_shares_field = prior_shares
        else:
            continue  # no change

        deltas.append(
            HoldingDelta(
                fund_cik=fund_cik_padded,
                fund_name=fund_name,
                ticker=ticker,
                current_filing_date=current_filing_date,
                current_period_end=current_quarter_end,
                action=action,
                shares_change=cur_shares - prior_shares,
                market_value_change=cur_value - prior_value,
                prior_shares=prior_shares_field,
                current_shares=cur_shares,
            )
        )
    return deltas


def deltas_to_events(deltas: list[HoldingDelta]) -> list[Event]:
    """
    Emit `13f_delta` Events. event_date = filing_date (CLAUDE.md rule #2).

    If two deltas share (fund_cik, ticker, period_end) — which happens when
    two CUSIPs resolve to the same ticker — a numeric ordinal is appended to
    the event_id so IDs remain unique. Order follows the deltas list.
    """
    events: list[Event] = []
    base_id_counts: dict[str, int] = {}
    for d in deltas:
        base_id = (
            f"13f_delta_{d.fund_cik}_{d.ticker}_{d.current_period_end.isoformat()}"
        )
        seen = base_id_counts.get(base_id, 0)
        base_id_counts[base_id] = seen + 1
        event_id = base_id if seen == 0 else f"{base_id}_{seen}"
        events.append(
            Event(
                event_id=event_id,
                ticker=d.ticker,
                event_date=d.current_filing_date,  # when the market sees it
                event_type="13f_delta",
                source="SEC EDGAR",
                payload_ref=event_id,
                text=_delta_text(d),
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
                source_type=SourceType.THIRTEEN_F,
                publication_date=e.event_date,
                source_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=13F-HR",
                section_name=e.event_type,
                text=e.text,
                token_count=len(e.text.split()),
            )
        )
    return chunks


def run_thirteen_f_pipeline(
    fund_cik: str,
    current_quarter_end: date,
    prior_quarter_end: date,
    output_dir: Path | str = DATA_DIR,
) -> tuple[list[HoldingRecord], list[HoldingDelta], list[Event], list[TextChunk]]:
    """End-to-end: fetch both quarters, compute deltas, emit Events + TextChunks,
    write parquet + JSONL under output_dir."""
    current_holdings = _load_quarter_holdings(fund_cik, current_quarter_end)
    prior_holdings = _load_quarter_holdings(fund_cik, prior_quarter_end)
    deltas = compute_holding_deltas(fund_cik, current_quarter_end, prior_quarter_end)
    events = deltas_to_events(deltas)
    chunks = events_to_text_chunks(events)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cik_padded = str(int(fund_cik)).zfill(10)
    qtr = current_quarter_end.isoformat()

    save_holdings_to_parquet(
        current_holdings + prior_holdings,
        output_dir / f"holdings_{cik_padded}_{qtr}.parquet",
    )
    save_deltas_to_parquet(deltas, output_dir / f"deltas_{cik_padded}_{qtr}.parquet")
    _save_events_to_parquet(events, output_dir / f"events_{cik_padded}_{qtr}.parquet")
    _write_chunks_jsonl(chunks, output_dir / f"chunks_{cik_padded}_{qtr}.jsonl")

    return current_holdings + prior_holdings, deltas, events, chunks


# ---------- I/O helpers ----------

def save_holdings_to_parquet(
    holdings: list[HoldingRecord],
    filepath: Path | str,
) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    if not holdings:
        pd.DataFrame(
            columns=[
                "fund_cik",
                "fund_name",
                "ticker",
                "filing_date",
                "period_end",
                "shares",
                "market_value",
                "percent_of_portfolio",
            ]
        ).to_parquet(filepath, index=False)
        return
    df = pd.DataFrame([h.model_dump() for h in holdings])
    for col in ("filing_date", "period_end"):
        df[col] = df[col].astype(str)
    df.to_parquet(filepath, compression="snappy", index=False)


def save_deltas_to_parquet(
    deltas: list[HoldingDelta],
    filepath: Path | str,
) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    if not deltas:
        pd.DataFrame(
            columns=[
                "fund_cik",
                "fund_name",
                "ticker",
                "current_filing_date",
                "current_period_end",
                "action",
                "shares_change",
                "market_value_change",
                "prior_shares",
                "current_shares",
            ]
        ).to_parquet(filepath, index=False)
        return
    df = pd.DataFrame([d.model_dump() for d in deltas])
    for col in ("current_filing_date", "current_period_end"):
        df[col] = df[col].astype(str)
    df["action"] = df["action"].astype(str)
    df.to_parquet(filepath, compression="snappy", index=False)


def _save_events_to_parquet(events: list[Event], filepath: Path | str) -> None:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    if not events:
        pd.DataFrame(
            columns=["event_id", "ticker", "event_date", "event_type", "source", "payload_ref", "text"]
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


# ---------- EDGAR client ----------

def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = SEC_USER_AGENT
    s.headers["Accept-Encoding"] = "gzip, deflate"
    return s


def _find_13f_filings(fund_cik: str, as_of: date) -> list[dict]:
    """13F-HR filings with filing_date <= as_of, newest first."""
    cik_num = int(fund_cik)
    url = SUBMISSIONS_URL.format(cik=cik_num)
    r = _session().get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    recent = data["filings"]["recent"]
    name = data.get("name", "")

    filings = []
    for i, form in enumerate(recent["form"]):
        if form != "13F-HR":
            continue
        filing_date = date.fromisoformat(recent["filingDate"][i])
        if filing_date > as_of:
            continue
        filings.append(
            {
                "accession": recent["accessionNumber"][i],
                "filing_date": filing_date,
                "report_date": date.fromisoformat(recent["reportDate"][i]),
                "fund_cik": cik_num,
                "fund_name": name,
            }
        )
    filings.sort(key=lambda f: f["filing_date"], reverse=True)
    return filings


def _load_filing_holdings_by_cusip(filing: dict) -> dict[str, HoldingRecord]:
    """
    CUSIP-keyed map of HoldingRecords for a single 13F-HR filing.

    13Fs have multiple rows per issuer (one per investment manager); aggregate
    by CUSIP, summing shares and dollar value. Two CUSIPs can resolve to the
    same ticker — callers that need unique records should use this map rather
    than keying on ticker.
    """
    xml_text = _fetch_information_table_xml(filing["fund_cik"], filing["accession"])
    raw_rows = _parse_information_table(xml_text)

    by_cusip: dict[str, dict] = {}
    for row in raw_rows:
        if not row["cusip"]:
            continue
        agg = by_cusip.setdefault(
            row["cusip"],
            {
                "name_of_issuer": row["name_of_issuer"],
                "cusip": row["cusip"],
                "shares": 0,
                "value": 0,
            },
        )
        agg["shares"] += row["shares"]
        agg["value"] += row["value"]

    total_value = sum(v["value"] for v in by_cusip.values()) or 1
    cik_padded = str(filing["fund_cik"]).zfill(10)
    out: dict[str, HoldingRecord] = {}
    for cusip, info in by_cusip.items():
        ticker = _resolve_ticker(info["name_of_issuer"]) or info["cusip"]
        out[cusip] = HoldingRecord(
            fund_cik=cik_padded,
            fund_name=filing["fund_name"],
            ticker=ticker,
            filing_date=filing["filing_date"],
            period_end=filing["report_date"],
            shares=info["shares"],
            market_value=info["value"],
            percent_of_portfolio=round(100.0 * info["value"] / total_value, 3),
        )
    return out


def _load_filing_holdings(filing: dict) -> list[HoldingRecord]:
    return list(_load_filing_holdings_by_cusip(filing).values())


def _load_quarter_holdings_by_cusip(
    fund_cik: str, quarter_end: date,
) -> dict[str, HoldingRecord]:
    """CUSIP-keyed map of the 13F-HR whose reportDate equals quarter_end."""
    as_of = quarter_end + timedelta(days=_FILING_WINDOW_DAYS)
    filings = _find_13f_filings(fund_cik, as_of)
    for f in filings:
        if f["report_date"] == quarter_end:
            return _load_filing_holdings_by_cusip(f)
    return {}


def _load_quarter_holdings(fund_cik: str, quarter_end: date) -> list[HoldingRecord]:
    """All holdings from the 13F-HR whose reportDate equals quarter_end."""
    return list(_load_quarter_holdings_by_cusip(fund_cik, quarter_end).values())


def _fetch_information_table_xml(cik: int, accession: str) -> str:
    acc_nodash = accession.replace("-", "")
    folder = FILING_ROOT.format(cik=cik, acc_nodash=acc_nodash)
    sess = _session()
    r = sess.get(folder, timeout=30)
    r.raise_for_status()
    for match in re.finditer(r'href="([^"]+\.xml)"', r.text, re.IGNORECASE):
        path = match.group(1)
        if "primary_doc" in path.lower():
            continue
        url = "https://www.sec.gov" + path if path.startswith("/") else folder + path
        rr = sess.get(url, timeout=30)
        if rr.status_code != 200:
            continue
        if "informationTable" in rr.text[:400]:
            return rr.text
    raise ValueError(f"no information table XML found for accession {accession}")


def _parse_information_table(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    rows = []
    for info_table in root.iter(f"{INFO_TABLE_NS}infoTable"):
        shr_prt = info_table.find(f"{INFO_TABLE_NS}shrsOrPrnAmt")
        if shr_prt is None:
            continue
        prn_type = (shr_prt.findtext(f"{INFO_TABLE_NS}sshPrnamtType") or "SH").strip().upper()
        if prn_type != "SH":
            continue  # skip principal-amount (bond) holdings
        rows.append(
            {
                "name_of_issuer": (info_table.findtext(f"{INFO_TABLE_NS}nameOfIssuer") or "").strip(),
                "cusip": (info_table.findtext(f"{INFO_TABLE_NS}cusip") or "").strip(),
                "value": int(info_table.findtext(f"{INFO_TABLE_NS}value") or "0"),
                "shares": int(shr_prt.findtext(f"{INFO_TABLE_NS}sshPrnamt") or "0"),
            }
        )
    return rows


# ---------- ticker resolution (best-effort) ----------

def _load_name_to_ticker() -> dict[str, str]:
    global _name_ticker_cache
    if _name_ticker_cache:
        return _name_ticker_cache
    r = _session().get(COMPANY_TICKERS_URL, timeout=30)
    r.raise_for_status()
    data = r.json()
    mapping: dict[str, str] = {}
    for entry in data.values():
        mapping[_normalize_name(entry["title"])] = entry["ticker"]
    _name_ticker_cache = mapping
    return mapping


def _normalize_name(name: str) -> str:
    n = name.upper()
    n = re.sub(
        r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|LLC|"
        r"L\.?P|N\.?V|PLC|SA|AG|HOLDINGS?|GROUP|TRUST|CLASS\s+[A-Z])\b",
        "",
        n,
    )
    n = re.sub(r"[^A-Z0-9]", "", n)
    return n


def _resolve_ticker(name_of_issuer: str) -> Optional[str]:
    if not name_of_issuer:
        return None
    mapping = _load_name_to_ticker()
    return mapping.get(_normalize_name(name_of_issuer))


def _delta_text(d: HoldingDelta) -> str:
    mv_millions = d.market_value_change / 1_000_000
    if d.action == HoldingAction.NEW:
        return (
            f"{d.fund_name} opened new {d.ticker} position in "
            f"{d.current_period_end.isoformat()} (filed {d.current_filing_date.isoformat()}): "
            f"{d.current_shares:,} shares, ${mv_millions:+,.1f}M."
        )
    if d.action == HoldingAction.EXITED:
        return (
            f"{d.fund_name} exited {d.ticker} in {d.current_period_end.isoformat()} "
            f"(filed {d.current_filing_date.isoformat()}): sold "
            f"{abs(d.shares_change):,} shares, ${mv_millions:+,.1f}M."
        )
    return (
        f"{d.fund_name} {d.action.value} {d.ticker} in "
        f"{d.current_period_end.isoformat()} (filed {d.current_filing_date.isoformat()}): "
        f"{d.shares_change:+,} shares (now {d.current_shares:,}), "
        f"${mv_millions:+,.1f}M value change."
    )
