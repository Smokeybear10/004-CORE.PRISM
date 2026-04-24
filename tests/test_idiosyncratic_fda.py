"""
Tests for `ingestion.idiosyncratic.fda`.

openFDA is monkeypatched by replacing the module-level Session. The hand-
curated calendar seed is exercised from the real file on disk — the fixture
is not used to drive the API path.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ingestion.idiosyncratic import fda
from schema import FDAEvent, FDAEventType, SourceType, TextChunk


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "idiosyncratic"
FDA_FIXTURE = FIXTURE_DIR / "fda_events_sample.json"


# ---------- fixture conforms to schema ----------


def test_fda_fixture_parses_as_fda_events():
    raw = json.loads(FDA_FIXTURE.read_text())
    for row in raw:
        FDAEvent(**row)


# ---------- sponsor → ticker mapping ----------


def test_map_sponsor_direct_hit():
    assert fda.map_sponsor_to_ticker("Biogen") == "BIIB"
    assert fda.map_sponsor_to_ticker("Moderna") == "MRNA"
    assert fda.map_sponsor_to_ticker("Eli Lilly") == "LLY"


def test_map_sponsor_handles_corporate_suffixes():
    assert fda.map_sponsor_to_ticker("Moderna, Inc.") == "MRNA"
    assert fda.map_sponsor_to_ticker("BIOGEN INC") == "BIIB"
    assert fda.map_sponsor_to_ticker("Gilead Sciences, Inc.") == "GILD"


def test_map_sponsor_unknown_returns_none():
    assert fda.map_sponsor_to_ticker("Some Private Biotech") is None
    assert fda.map_sponsor_to_ticker("") is None
    assert fda.map_sponsor_to_ticker("   ") is None


def test_map_sponsor_subsidiary_routes_to_parent():
    # Janssen → JNJ, Genentech → RHHBY
    assert fda.map_sponsor_to_ticker("Janssen") == "JNJ"
    assert fda.map_sponsor_to_ticker("Genentech") == "RHHBY"


# ---------- calendar seed loading + as-of filter ----------


def test_calendar_seed_as_of_excludes_future_entries():
    before = fda.fetch_fda_calendar(date(2022, 1, 1))
    after = fda.fetch_fda_calendar(date(2025, 12, 31))
    assert len(before) < len(after)
    for ev in before:
        assert ev.event_date <= date(2022, 1, 1)


def test_calendar_seed_populates_known_events():
    seed = fda.fetch_fda_calendar(date(2025, 12, 31))
    ids = {e.event_id for e in seed}
    assert "fda_pdufa_BIIB_ADUHELM_2021-06-07" in ids
    assert "fda_pdufa_VRTX_CASGEVY_2023-12-08" in ids


# ---------- openFDA API path (mocked) ----------


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"status {self.status_code}")

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, pages: list[dict]):
        self.pages = pages
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        skip = (params or {}).get("skip", 0)
        limit = (params or {}).get("limit", 1000)
        idx = skip // max(limit, 1)
        if idx >= len(self.pages):
            return _FakeResp({"results": []})
        return _FakeResp({"results": self.pages[idx]})


def _openfda_entry(sponsor: str, app: str, brand: str, status: str, ymd: str,
                   sub_type: str = "ORIG", sub_num: str = "1") -> dict:
    return {
        "application_number": app,
        "sponsor_name": sponsor,
        "products": [{"brand_name": brand, "dosage_form": "TABLET", "route": "ORAL"}],
        "submissions": [
            {"submission_type": sub_type, "submission_number": sub_num,
             "submission_status": status, "submission_status_date": ymd},
        ],
    }


def test_fetch_actions_filters_by_as_of_and_status():
    entries = [
        _openfda_entry("BIOGEN INC", "BLA125569", "ADUHELM", "AP", "20210607"),
        _openfda_entry("PFIZER INC", "NDA777777", "FUTUREDRUG", "AP", "20260101"),
        _openfda_entry("MODERNA INC", "NDA888888", "NOTAPPROVED", "TA", "20240101"),
    ]
    sess = _FakeSession([entries])
    events = fda.fetch_fda_actions(ticker=None, as_of=date(2024, 12, 31),
                                   since=date(2015, 1, 1), session=sess)
    tickers = [e.sponsor_ticker for e in events]
    # Aduhelm is in range and AP → kept
    assert "BIIB" in tickers
    # Future approval is past as_of → dropped
    assert "PFE" not in tickers
    # Tentative Approval (TA) is not AP → dropped
    assert "MRNA" not in tickers


def test_fetch_actions_sends_openfda_date_range():
    sess = _FakeSession([[]])
    fda.fetch_fda_actions(None, as_of=date(2024, 6, 30),
                          since=date(2024, 1, 1), session=sess)
    search = sess.calls[0]["search"]
    assert "[20240101+TO+20240630]" in search
    assert "submission_status:AP" in search


def test_fetch_actions_paginates_until_short_page():
    page1 = [_openfda_entry("GILEAD SCIENCES INC", f"NDA{i}", f"DRUG{i}", "AP",
                            "20240101") for i in range(1000)]
    page2 = [_openfda_entry("GILEAD SCIENCES INC", "NDA-tail", "TAIL", "AP",
                            "20240201")]
    sess = _FakeSession([page1, page2])
    events = fda.fetch_fda_actions(None, as_of=date(2024, 12, 31),
                                   since=date(2020, 1, 1), session=sess)
    # At least 1001 events (all AP, all GILD)
    assert len(events) >= 1001
    assert len(sess.calls) == 2
    assert sess.calls[1]["skip"] == 1000


def test_fetch_actions_filters_by_ticker_post_fetch():
    entries = [
        _openfda_entry("BIOGEN INC", "BLA1", "A", "AP", "20240101"),
        _openfda_entry("MODERNA INC", "NDA2", "B", "AP", "20240201"),
    ]
    sess = _FakeSession([entries])
    events = fda.fetch_fda_actions(ticker="BIIB", as_of=date(2024, 12, 31),
                                   since=date(2020, 1, 1), session=sess)
    assert all(e.sponsor_ticker == "BIIB" for e in events)


def test_fetch_actions_uses_stable_event_ids():
    entries = [_openfda_entry("BIOGEN INC", "BLA125569", "ADUHELM", "AP", "20210607")]
    sess = _FakeSession([entries])
    a = fda.fetch_fda_actions(None, as_of=date(2024, 12, 31),
                              since=date(2015, 1, 1), session=sess)
    sess2 = _FakeSession([entries])
    b = fda.fetch_fda_actions(None, as_of=date(2024, 12, 31),
                              since=date(2015, 1, 1), session=sess2)
    assert [e.event_id for e in a] == [e.event_id for e in b]


def test_fetch_actions_404_means_no_results():
    class _Sess:
        def get(self, url, params=None, timeout=None):
            return _FakeResp({}, status_code=404)
    events = fda.fetch_fda_actions(None, as_of=date(2024, 12, 31),
                                   since=date(2020, 1, 1), session=_Sess())
    assert events == []


# ---------- combined fetch_fda_events: calendar + actions dedup ----------


def test_combined_events_dedup_calendar_wins_on_conflict():
    # Hand-curated seed has fda_pdufa_BIIB_LEQEMBI_2023-01-06 of event_type=PDUFA.
    # Make openFDA claim the same event_id but as APPROVAL. Calendar should win.
    colliding_id = "fda_pdufa_BIIB_LEQEMBI_2023-01-06"

    class _Sess:
        def get(self, url, params=None, timeout=None):
            return _FakeResp({"results": []})

    events = fda.fetch_fda_events(as_of=date(2024, 12, 31), session=_Sess())
    matches = [e for e in events if e.event_id == colliding_id]
    assert len(matches) == 1
    assert matches[0].event_type == FDAEventType.PDUFA  # from calendar seed


# ---------- Event wrapping + TextChunk ----------


def test_fda_events_to_events_uses_fda_source():
    f = FDAEvent(
        event_id="test_e1", event_type=FDAEventType.APPROVAL,
        event_date=date(2024, 6, 1), sponsor_ticker="BIIB",
        drug_name="Test Drug", description="desc",
    )
    events = fda.fda_events_to_events([f])
    assert len(events) == 1
    assert events[0].source == "FDA"
    assert events[0].event_type == "fda_approval"
    assert events[0].ticker == "BIIB"
    assert events[0].event_date == date(2024, 6, 1)


def test_unmapped_sponsor_emits_placeholder_ticker():
    f = FDAEvent(
        event_id="test_e2", event_type=FDAEventType.PDUFA,
        event_date=date(2024, 6, 1), sponsor_ticker=None,
        drug_name="Foreign Drug", description="desc",
    )
    events = fda.fda_events_to_events([f])
    assert events[0].ticker == "_UNMAPPED"


def test_events_to_chunks_uses_news_source_type():
    """SourceType has no FDA value yet — NEWS is the placeholder."""
    from schema import Event
    e = Event(event_id="x", ticker="BIIB", event_date=date(2024, 6, 1),
              event_type="fda_approval", source="FDA", payload_ref="x", text="hi")
    chunks = fda.events_to_text_chunks([e])
    assert chunks[0].source_type == SourceType.NEWS


# ---------- full pipeline writes parquet + JSONL ----------


def test_pipeline_writes_parquet_and_jsonl(tmp_path):
    class _Sess:
        def get(self, url, params=None, timeout=None):
            return _FakeResp({"results": []})

    fda_events, events, chunks = fda.run_fda_pipeline(
        as_of=date(2025, 12, 31), output_dir=tmp_path, session=_Sess(),
    )
    assert fda_events  # calendar seed has entries
    assert events and chunks

    records_path = tmp_path / "records_2025-12-31.parquet"
    events_path = tmp_path / "events_2025-12-31.parquet"
    chunks_path = tmp_path / "chunks_2025-12-31.jsonl"
    assert records_path.exists() and events_path.exists() and chunks_path.exists()

    recs_df = pd.read_parquet(records_path)
    assert {"event_id", "event_type", "event_date", "sponsor_ticker",
            "drug_name", "description"}.issubset(recs_df.columns)

    for line in chunks_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        TextChunk.model_validate_json(line)
