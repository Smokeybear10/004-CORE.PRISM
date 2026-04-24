"""
Tests for `ingestion.idiosyncratic.short_interest`.

Network is monkeypatched out — every test replaces `requests.post` on the
module with a fake that returns canned FINRA rows. The fixture at
tests/fixtures/idiosyncratic/short_interest_sample.json seeds records for
the pipeline/as-of/parquet tests.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from ingestion.idiosyncratic import short_interest as si
from schema import Event, ShortInterestRecord, SourceType

FIXTURE = Path(__file__).parent / "fixtures" / "idiosyncratic" / "short_interest_sample.json"


def _load_fixture() -> list[ShortInterestRecord]:
    with FIXTURE.open() as f:
        return [ShortInterestRecord(**row) for row in json.load(f)]


# ---------- _row_to_record: bugfix coverage ----------


def test_days_to_cover_string_cast_before_sentinel():
    """FINRA returns daysToCoverQuantity as a string in CSV; must cast first."""
    row = {
        "symbolCode": "foo",
        "settlementDate": "2024-01-31",
        "currentShortPositionQuantity": "1000",
        "averageDailyVolumeQuantity": "500",
        "daysToCoverQuantity": "2.25",  # string, not float
    }
    rec = si._row_to_record(row)
    assert rec.days_to_cover == 2.25
    assert rec.avg_daily_volume == 500


def test_days_to_cover_sentinel_999_becomes_none():
    row = {
        "symbolCode": "X",
        "settlementDate": "2024-01-31",
        "currentShortPositionQuantity": "1",
        "daysToCoverQuantity": "999.99",
    }
    rec = si._row_to_record(row)
    assert rec.days_to_cover is None


def test_days_to_cover_garbage_string_becomes_none():
    row = {
        "symbolCode": "X",
        "settlementDate": "2024-01-31",
        "currentShortPositionQuantity": "1",
        "daysToCoverQuantity": "",
    }
    rec = si._row_to_record(row)
    assert rec.days_to_cover is None


def test_avg_vol_zero_is_preserved_not_treated_as_missing():
    """Bugfix: `int(x) or None` conflated 0 with missing. 0 is a real value."""
    row = {
        "symbolCode": "X",
        "settlementDate": "2024-01-31",
        "currentShortPositionQuantity": "100",
        "averageDailyVolumeQuantity": "0",
    }
    rec = si._row_to_record(row)
    assert rec.avg_daily_volume == 0


def test_avg_vol_missing_maps_to_none():
    row = {
        "symbolCode": "X",
        "settlementDate": "2024-01-31",
        "currentShortPositionQuantity": "100",
    }
    rec = si._row_to_record(row)
    assert rec.avg_daily_volume is None


# ---------- fetch_short_interest: as-of filter + pagination ----------


def _fake_post_factory(rows: list[dict]):
    """Returns a callable that mimics requests.post for FINRA."""
    calls: list[dict] = []

    class _Resp:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def _post(url, json=None, timeout=None, headers=None):  # noqa: A002
        body = json or {}
        calls.append(body)
        compare = body.get("compareFilters", [])
        # Server-side filter reproduction so we can test the pagination loop
        settlement_cutoff = None
        ticker = None
        for f in compare:
            if f["fieldName"] == "settlementDate" and f["compareType"] == "LTE":
                settlement_cutoff = f["fieldValue"]
            if f["fieldName"] == "symbolCode" and f["compareType"] == "EQUAL":
                ticker = f["fieldValue"]
        filtered = [
            r for r in rows
            if (settlement_cutoff is None or r["settlementDate"] <= settlement_cutoff)
            and (ticker is None or r["symbolCode"] == ticker)
        ]
        offset = body.get("offset", 0)
        limit = body.get("limit", 5000)
        return _Resp(filtered[offset : offset + limit])

    return _post, calls


def test_fetch_filters_settlement_by_as_of_minus_publication_lag(monkeypatch):
    rows = [
        {"symbolCode": "TEST", "settlementDate": "2024-01-31",
         "currentShortPositionQuantity": 100, "averageDailyVolumeQuantity": 50,
         "daysToCoverQuantity": 2.0},
        {"symbolCode": "TEST", "settlementDate": "2024-03-15",
         "currentShortPositionQuantity": 200, "averageDailyVolumeQuantity": 50,
         "daysToCoverQuantity": 4.0},
    ]
    fake_post, calls = _fake_post_factory(rows)
    monkeypatch.setattr(si.requests, "post", fake_post)

    as_of = date(2024, 2, 10)  # 10 days after 2024-01-31 → lag of 14 days means 01-31 NOT visible yet
    recs = si.fetch_short_interest("TEST", as_of=as_of)
    assert recs == []

    # Now push as_of forward enough to include 2024-01-31 but not 2024-03-15
    recs = si.fetch_short_interest("TEST", as_of=date(2024, 2, 20))
    assert [r.settlement_date for r in recs] == [date(2024, 1, 31)]


def test_fetch_sends_lte_comparetype_and_uppercased_ticker(monkeypatch):
    fake_post, calls = _fake_post_factory([])
    monkeypatch.setattr(si.requests, "post", fake_post)
    si.fetch_short_interest("aapl", as_of=date(2024, 12, 31))
    body = calls[0]
    filters_by_field = {f["fieldName"]: f for f in body["compareFilters"]}
    # Bugfix regression guard: LTE is the correct FINRA Data API compareType
    assert filters_by_field["settlementDate"]["compareType"] == "LTE"
    assert filters_by_field["symbolCode"]["fieldValue"] == "AAPL"
    assert filters_by_field["symbolCode"]["compareType"] == "EQUAL"


def test_fetch_paginates_until_empty_response(monkeypatch):
    # Two full pages + an empty page. Each ticker/settlement pair is unique
    # so dedup doesn't collapse them.
    rows = [
        {"symbolCode": f"T{i:04d}", "settlementDate": "2024-01-01",
         "currentShortPositionQuantity": i, "averageDailyVolumeQuantity": 1,
         "daysToCoverQuantity": 1.0}
        for i in range(5000)
    ] + [
        {"symbolCode": "BIG", "settlementDate": "2024-01-02",
         "currentShortPositionQuantity": 9999, "averageDailyVolumeQuantity": 1,
         "daysToCoverQuantity": 1.0}
    ]
    fake_post, calls = _fake_post_factory(rows)
    monkeypatch.setattr(si.requests, "post", fake_post)
    recs = si.fetch_short_interest(None, as_of=date(2030, 1, 1))
    assert len(recs) == 5001
    # FINRA returns 400 when offset exceeds the matching rowcount, so the
    # loop breaks on a partial page (len < page_size). Two calls:
    # [0..5000) full page, then [5000..5001) partial.
    assert len(calls) == 2
    assert calls[0]["offset"] == 0
    assert calls[1]["offset"] == 5000


def test_fetch_dedups_revised_records_last_write_wins(monkeypatch):
    """FINRA emits revisions for the same (ticker, settlement) — dedup on key."""
    rows = [
        {"symbolCode": "GME", "settlementDate": "2024-01-31",
         "currentShortPositionQuantity": 50_000_000, "averageDailyVolumeQuantity": 100,
         "daysToCoverQuantity": 1.0},
        # A revised row for the same (GME, 2024-01-31). Last one wins.
        {"symbolCode": "GME", "settlementDate": "2024-01-31",
         "currentShortPositionQuantity": 59_673_027, "averageDailyVolumeQuantity": 100,
         "daysToCoverQuantity": 1.0},
    ]
    fake_post, _ = _fake_post_factory(rows)
    monkeypatch.setattr(si.requests, "post", fake_post)
    recs = si.fetch_short_interest("GME", as_of=date(2030, 1, 1))
    assert len(recs) == 1
    assert recs[0].shares_short == 59_673_027


# ---------- detect_short_interest_spikes ----------


def test_spike_detector_fires_above_threshold_and_uses_publication_date():
    # AMC rose from 24.1M → 28.6M shares short (+18.7%) → below 20% threshold
    # GME barely changed — neither should fire a spike.
    # Induce a clear spike with a synthetic prior.
    synth = [
        ShortInterestRecord(
            ticker="SPIKY", settlement_date=date(2024, 1, 15),
            shares_short=1_000_000, avg_daily_volume=100_000, days_to_cover=10.0,
        ),
        ShortInterestRecord(
            ticker="SPIKY", settlement_date=date(2024, 1, 31),
            shares_short=1_500_000, avg_daily_volume=100_000, days_to_cover=15.0,
        ),
    ]
    events = si.detect_short_interest_spikes(synth)
    assert len(events) == 1
    assert events[0].event_type == "short_interest_spike"
    assert events[0].ticker == "SPIKY"
    # event_date is publication date (settlement + lag), not settlement itself
    assert events[0].event_date == date(2024, 1, 31) + timedelta(days=si.PUBLICATION_LAG_DAYS)


def test_spike_event_id_is_deterministic():
    synth = [
        ShortInterestRecord(ticker="X", settlement_date=date(2024, 1, 1),
                            shares_short=100, avg_daily_volume=1, days_to_cover=1.0),
        ShortInterestRecord(ticker="X", settlement_date=date(2024, 1, 15),
                            shares_short=200, avg_daily_volume=1, days_to_cover=1.0),
    ]
    a = si.detect_short_interest_spikes(synth)
    b = si.detect_short_interest_spikes(synth)
    assert a[0].event_id == b[0].event_id == "short_interest_spike_X_2024-01-15"


def test_spike_skips_nonpositive_prior():
    synth = [
        ShortInterestRecord(ticker="X", settlement_date=date(2024, 1, 1),
                            shares_short=0, avg_daily_volume=1, days_to_cover=None),
        ShortInterestRecord(ticker="X", settlement_date=date(2024, 1, 15),
                            shares_short=1000, avg_daily_volume=1, days_to_cover=None),
    ]
    assert si.detect_short_interest_spikes(synth) == []


# ---------- events_to_text_chunks ----------


def test_events_to_chunks_produces_short_interest_source_type():
    events = [
        Event(event_id="e1", ticker="X", event_date=date(2024, 2, 14),
              event_type="short_interest_spike", source="FINRA",
              payload_ref="e1", text="X short interest rose 50%"),
    ]
    chunks = si.events_to_text_chunks(events)
    assert len(chunks) == 1
    assert chunks[0].source_type == SourceType.SHORT_INTEREST
    assert chunks[0].chunk_id == "e1"
    assert chunks[0].publication_date == date(2024, 2, 14)


def test_events_with_no_text_are_dropped():
    events = [
        Event(event_id="e1", ticker="X", event_date=date(2024, 2, 14),
              event_type="short_interest_spike", source="FINRA",
              payload_ref="e1", text=None),
    ]
    assert si.events_to_text_chunks(events) == []


# ---------- run_short_interest_pipeline: parquet + JSONL ----------


def test_pipeline_writes_parquet_and_jsonl(monkeypatch, tmp_path):
    # Feed the fake FINRA two settlement dates for SPIKY so the spike detector fires
    rows = [
        {"symbolCode": "SPIKY", "settlementDate": "2024-01-15",
         "currentShortPositionQuantity": 1_000_000, "averageDailyVolumeQuantity": 100_000,
         "daysToCoverQuantity": 10.0},
        {"symbolCode": "SPIKY", "settlementDate": "2024-01-31",
         "currentShortPositionQuantity": 2_000_000, "averageDailyVolumeQuantity": 100_000,
         "daysToCoverQuantity": 20.0},
    ]
    fake_post, _ = _fake_post_factory(rows)
    monkeypatch.setattr(si.requests, "post", fake_post)

    records, events, chunks = si.run_short_interest_pipeline(
        as_of=date(2030, 1, 1), ticker="SPIKY", output_dir=tmp_path,
    )
    assert len(records) == 2
    assert len(events) == 1
    assert len(chunks) == 1

    records_path = tmp_path / "records_SPIKY_2030-01-01.parquet"
    events_path = tmp_path / "events_SPIKY_2030-01-01.parquet"
    chunks_path = tmp_path / "chunks_SPIKY_2030-01-01.jsonl"
    assert records_path.exists() and events_path.exists() and chunks_path.exists()

    recs_df = pd.read_parquet(records_path)
    assert set(["ticker", "settlement_date", "shares_short", "avg_daily_volume",
                "days_to_cover", "float_short_percent"]).issubset(recs_df.columns)

    ev_df = pd.read_parquet(events_path)
    assert set(["event_id", "ticker", "event_date", "event_type",
                "source", "payload_ref", "text"]).issubset(ev_df.columns)

    # JSONL round-trips through the pydantic model
    for line in chunks_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        from schema import TextChunk
        TextChunk.model_validate_json(line)
