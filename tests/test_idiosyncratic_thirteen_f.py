"""
Tests for `ingestion.idiosyncratic.thirteen_f`.

Network (EDGAR fetches) is monkeypatched via direct replacement of the
internal `_load_quarter_holdings_by_cusip` so we don't hit SEC. Covers the
CUSIP-keyed dedup bugfix, as-of filtering semantics (via filing_date), and
deterministic event IDs / parquet + JSONL pipeline output.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ingestion.idiosyncratic import thirteen_f as tf
from schema import HoldingAction, HoldingDelta, HoldingRecord, SourceType


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "idiosyncratic"


def _rec(cusip: str, ticker: str, shares: int, value: int,
         filing: date = date(2025, 2, 14), period: date = date(2024, 12, 31)) -> HoldingRecord:
    return HoldingRecord(
        fund_cik="0001067983",
        fund_name="BERKSHIRE HATHAWAY INC",
        ticker=ticker,
        filing_date=filing,
        period_end=period,
        shares=shares,
        market_value=value,
        percent_of_portfolio=10.0,
    )


# ---------- fixtures parse under the schema ----------


def test_holdings_fixture_parses_as_holding_records():
    with (FIXTURE_DIR / "thirteen_f_sample.json").open() as f:
        raw = json.load(f)
    for row in raw:
        HoldingRecord(**row)


def test_deltas_fixture_parses_as_holding_deltas():
    with (FIXTURE_DIR / "holding_deltas_sample.json").open() as f:
        raw = json.load(f)
    for row in raw:
        HoldingDelta(**row)


# ---------- CUSIP-keyed dedup (the bugfix) ----------


def test_same_ticker_different_cusips_both_survive_delta(monkeypatch):
    """
    Two CUSIPs that resolve to the same ticker must produce two HoldingDelta
    records, not overwrite each other. Simulates Alphabet GOOG/GOOGL, or any
    case where name→ticker resolution yields collisions.
    """
    cur_map = {
        "02079K107": _rec("02079K107", "GOOG", shares=1_000, value=1_000_000),
        "02079K305": _rec("02079K305", "GOOG", shares=500, value=500_000),
    }
    prior_map = {
        "02079K107": _rec("02079K107", "GOOG", shares=600, value=600_000,
                          filing=date(2024, 11, 14), period=date(2024, 9, 30)),
        "02079K305": _rec("02079K305", "GOOG", shares=500, value=500_000,
                          filing=date(2024, 11, 14), period=date(2024, 9, 30)),
    }

    def _fake_load(cik, quarter_end):
        return cur_map if quarter_end == date(2024, 12, 31) else prior_map

    monkeypatch.setattr(tf, "_load_quarter_holdings_by_cusip", _fake_load)
    deltas = tf.compute_holding_deltas(
        "0001067983",
        current_quarter_end=date(2024, 12, 31),
        prior_quarter_end=date(2024, 9, 30),
    )
    # The 02079K107 CUSIP increased (600 → 1000); the 02079K305 CUSIP was flat
    # (500 → 500, filtered as no-change). Pre-bugfix the by-ticker dedup
    # would have overwritten one and returned zero changes.
    assert len(deltas) == 1
    assert deltas[0].action == HoldingAction.INCREASED
    assert deltas[0].shares_change == 400


def test_colliding_ticker_event_ids_get_ordinal_suffix():
    d1 = HoldingDelta(
        fund_cik="0001067983", fund_name="BRK", ticker="GOOG",
        current_filing_date=date(2025, 2, 14), current_period_end=date(2024, 12, 31),
        action=HoldingAction.INCREASED, shares_change=100,
        market_value_change=100_000, prior_shares=900, current_shares=1000,
    )
    d2 = d1.model_copy(update={"shares_change": -50, "current_shares": 450,
                               "prior_shares": 500, "action": HoldingAction.REDUCED})
    events = tf.deltas_to_events([d1, d2])
    assert len(events) == 2
    assert events[0].event_id == "13f_delta_0001067983_GOOG_2024-12-31"
    assert events[1].event_id == "13f_delta_0001067983_GOOG_2024-12-31_1"


# ---------- delta classification ----------


def test_new_exited_increased_reduced_all_classified(monkeypatch):
    cur = {
        "NEWCUS": _rec("NEWCUS", "NEWT", 100, 10_000),
        "INCCUS": _rec("INCCUS", "INCT", 300, 30_000),
        "DECCUS": _rec("DECCUS", "DECT", 50, 5_000),
    }
    prior = {
        "INCCUS": _rec("INCCUS", "INCT", 100, 10_000,
                       filing=date(2024, 11, 14), period=date(2024, 9, 30)),
        "DECCUS": _rec("DECCUS", "DECT", 200, 20_000,
                       filing=date(2024, 11, 14), period=date(2024, 9, 30)),
        "OLDCUS": _rec("OLDCUS", "OLDT", 100, 10_000,
                       filing=date(2024, 11, 14), period=date(2024, 9, 30)),
    }

    def _fake_load(cik, quarter_end):
        return cur if quarter_end == date(2024, 12, 31) else prior

    monkeypatch.setattr(tf, "_load_quarter_holdings_by_cusip", _fake_load)
    deltas = tf.compute_holding_deltas(
        "0001067983", date(2024, 12, 31), date(2024, 9, 30),
    )
    by_action = {d.action: d for d in deltas}
    assert set(by_action) == {
        HoldingAction.NEW, HoldingAction.INCREASED,
        HoldingAction.REDUCED, HoldingAction.EXITED,
    }
    assert by_action[HoldingAction.NEW].prior_shares is None
    assert by_action[HoldingAction.EXITED].current_shares == 0
    assert by_action[HoldingAction.EXITED].prior_shares == 100


def test_compute_deltas_rejects_fund_cik_mismatch_between_quarters(monkeypatch):
    """Guards against silently ascribing prior fund's positions to current fund."""
    cur = {"C": _rec("C", "T", 200, 20_000)}  # fund_cik = 0001067983
    prior_rec = HoldingRecord(
        fund_cik="0000000123",  # Different fund
        fund_name="OTHER FUND",
        ticker="T",
        filing_date=date(2024, 11, 14),
        period_end=date(2024, 9, 30),
        shares=100,
        market_value=10_000,
    )
    prior = {"C": prior_rec}

    def _fake_load(cik, quarter_end):
        return cur if quarter_end == date(2024, 12, 31) else prior

    monkeypatch.setattr(tf, "_load_quarter_holdings_by_cusip", _fake_load)
    with pytest.raises(ValueError, match="fund_cik mismatch"):
        tf.compute_holding_deltas(
            "0001067983", date(2024, 12, 31), date(2024, 9, 30),
        )


def test_no_change_positions_are_omitted(monkeypatch):
    same = _rec("C", "T", 100, 10_000)
    monkeypatch.setattr(
        tf, "_load_quarter_holdings_by_cusip",
        lambda cik, q: {"C": same},
    )
    deltas = tf.compute_holding_deltas(
        "0001067983", date(2024, 12, 31), date(2024, 9, 30),
    )
    # Same CUSIP, same shares → no delta emitted
    assert deltas == []


# ---------- deltas_to_events + text chunks ----------


def test_events_use_filing_date_not_period_end(monkeypatch):
    """CLAUDE.md rule #2: event_date is when the market sees the filing."""
    cur = {"C": _rec("C", "T", 200, 20_000,
                     filing=date(2025, 2, 14), period=date(2024, 12, 31))}
    prior = {"C": _rec("C", "T", 100, 10_000,
                       filing=date(2024, 11, 14), period=date(2024, 9, 30))}

    def _fake_load(cik, quarter_end):
        return cur if quarter_end == date(2024, 12, 31) else prior

    monkeypatch.setattr(tf, "_load_quarter_holdings_by_cusip", _fake_load)
    deltas = tf.compute_holding_deltas(
        "0001067983", date(2024, 12, 31), date(2024, 9, 30),
    )
    events = tf.deltas_to_events(deltas)
    assert len(events) == 1
    assert events[0].event_date == date(2025, 2, 14)


def test_events_to_chunks_marks_thirteen_f_source_type():
    d = HoldingDelta(
        fund_cik="0001067983", fund_name="BRK", ticker="AAPL",
        current_filing_date=date(2025, 2, 14), current_period_end=date(2024, 12, 31),
        action=HoldingAction.INCREASED, shares_change=100,
        market_value_change=25_000, prior_shares=900, current_shares=1000,
    )
    events = tf.deltas_to_events([d])
    chunks = tf.events_to_text_chunks(events)
    assert len(chunks) == 1
    assert chunks[0].source_type == SourceType.THIRTEEN_F
    assert chunks[0].publication_date == date(2025, 2, 14)


# ---------- stable event IDs ----------


def test_event_ids_are_deterministic():
    d = HoldingDelta(
        fund_cik="0001067983", fund_name="BRK", ticker="AAPL",
        current_filing_date=date(2025, 2, 14), current_period_end=date(2024, 12, 31),
        action=HoldingAction.INCREASED, shares_change=100,
        market_value_change=25_000, prior_shares=900, current_shares=1000,
    )
    a = tf.deltas_to_events([d])[0].event_id
    b = tf.deltas_to_events([d])[0].event_id
    assert a == b == "13f_delta_0001067983_AAPL_2024-12-31"


# ---------- as-of filter: filings after as_of are excluded ----------


def test_find_13f_filings_respects_as_of(monkeypatch):
    sample_submissions = {
        "name": "BERKSHIRE HATHAWAY INC",
        "filings": {
            "recent": {
                "form": ["13F-HR", "10-K", "13F-HR"],
                "filingDate": ["2025-02-14", "2025-03-01", "2024-11-14"],
                "reportDate": ["2024-12-31", "2024-12-31", "2024-09-30"],
                "accessionNumber": ["acc-A", "acc-B", "acc-C"],
            }
        },
    }

    class _Resp:
        status_code = 200
        def raise_for_status(self): return None
        def json(self): return sample_submissions

    class _Sess:
        headers = {}
        def get(self, url, timeout=None): return _Resp()

    monkeypatch.setattr(tf, "_session", lambda: _Sess())

    # Before 2025-02-14, only the 2024-11-14 filing is visible
    filings = tf._find_13f_filings("0001067983", as_of=date(2025, 1, 1))
    assert [f["filing_date"] for f in filings] == [date(2024, 11, 14)]

    # After 2025-02-14, both 13F filings are visible (10-K is excluded by form)
    filings = tf._find_13f_filings("0001067983", as_of=date(2025, 2, 28))
    assert {f["filing_date"] for f in filings} == {date(2024, 11, 14), date(2025, 2, 14)}
