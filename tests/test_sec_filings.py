"""HF-backed SEC pipeline: mock the shard stream, assert chunk + event output."""
from __future__ import annotations

import io
import json
from datetime import date

import pandas as pd
import pytest

from schema import SourceType


# One synthetic "company row" per shard, matching the real JSONL schema.
_COMPANY_AMD = {
    "cik": "0000002488",
    "name": "ADVANCED MICRO DEVICES INC",
    "tickers": ["AMD"],
    "exchanges": ["NASDAQ"],
    "filings": [
        {
            "form": "10-K",
            "filingDate": "2024-02-01",
            "reportDate": "2023-12-30",
            "report": {
                "section_1": ["AMD designs processors.", "It competes with Intel."],
                "section_1A": ["Supply chain risk is material."],
                "section_7": ["Revenue grew 20% YoY driven by data center."],
            },
            "labels": {}, "returns": {},
        },
        {
            "form": "10-Q",
            "filingDate": "2024-05-01",
            "reportDate": "2024-03-31",
            "report": {
                "section_1": ["Q1 commentary."],
                "section_7": ["Client segment rebounded."],
            },
            "labels": {}, "returns": {},
        },
        {
            "form": "8-K",
            "filingDate": "2025-06-15",  # after as_of=2024-12-31 in test
            "reportDate": "2025-06-15",
            "report": {"section_1": ["Material event."]},
            "labels": {}, "returns": {},
        },
    ],
}

_COMPANY_NVDA = {
    "cik": "0001045810",
    "name": "NVIDIA CORP",
    "tickers": ["NVDA"],
    "exchanges": ["NASDAQ"],
    "filings": [
        {
            "form": "10-K",
            "filingDate": "2024-03-01",
            "reportDate": "2024-01-28",
            "report": {"section_1": ["NVIDIA designs GPUs."]},
            "labels": {}, "returns": {},
        },
    ],
}


class _FakeFS:
    """Minimal stand-in for HfFileSystem covering the two methods the pipeline uses."""

    def __init__(self, companies_by_shard: dict[str, list[dict]]):
        self._shards = companies_by_shard

    def ls(self, path, detail=False):
        # Return only shards under the requested path (train vs test).
        return [p for p in self._shards.keys() if p.startswith(path)]

    def open(self, path, mode="r", encoding="utf-8"):
        companies = self._shards[path]
        lines = "\n".join(json.dumps(c) for c in companies)
        return io.StringIO(lines)


@pytest.fixture
def _fake_fs(monkeypatch):
    """Inject the fake filesystem + shard contents into the pipeline."""
    import ingestion.sec.filings as sec_mod

    shards = {
        f"{sec_mod.HF_BASE}/train/shard_0.jsonl": [_COMPANY_AMD],
        f"{sec_mod.HF_BASE}/test/shard_0.jsonl": [_COMPANY_NVDA],
    }
    fake_fs = _FakeFS(shards)
    monkeypatch.setattr(sec_mod, "HfFileSystem", lambda: fake_fs)
    return sec_mod


def test_run_sec_pipeline_emits_filing_events_and_section_chunks(_fake_fs, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    events, chunks = _fake_fs.run_sec_pipeline("AMD", date(2024, 12, 31))

    out_dir = tmp_path / "data" / "sec"
    assert (out_dir / "events_AMD_2024-12-31.parquet").exists()
    assert (out_dir / "chunks_AMD_2024-12-31.jsonl").exists()

    # 10-K + 10-Q within as_of; 8-K is after as_of and must be excluded.
    # NVDA company must not leak in either.
    event_types = sorted(e.event_type for e in events)
    assert event_types == ["10k_filing", "10q_filing"]
    assert all(e.ticker == "AMD" for e in events)

    # chunk_ids are deterministic and namespaced.
    chunk_ids = {c.chunk_id for c in chunks}
    assert "sec_10k_AMD_2024-02-01_sec1_001" in chunk_ids
    assert "sec_10k_AMD_2024-02-01_sec1a_001" in chunk_ids
    assert "sec_10k_AMD_2024-02-01_sec7_001" in chunk_ids
    # 10-Q picks up whatever preferred sections exist.
    assert "sec_10q_AMD_2024-05-01_sec7_001" in chunk_ids
    # Collision-free.
    assert len(chunk_ids) == len(list(chunks))

    # source_type is enum-valid.
    assert {c.source_type for c in chunks} == {SourceType.SEC_10K, SourceType.SEC_10Q}


def test_run_sec_pipeline_filters_ticker(_fake_fs, tmp_path, monkeypatch):
    """Requesting NVDA must pick up the NVDA row from test/ shard but skip AMD."""
    monkeypatch.chdir(tmp_path)
    events, chunks = _fake_fs.run_sec_pipeline("NVDA", date(2024, 12, 31))
    assert len(events) == 1
    assert events[0].ticker == "NVDA"
    assert all(c.ticker == "NVDA" for c in chunks)


def test_run_sec_pipeline_empty_ticker_is_graceful(_fake_fs, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    events, chunks = _fake_fs.run_sec_pipeline("NOPE", date(2024, 12, 31))
    assert events == [] and chunks == []
    # Empty parquet still written so skip-if-exists logic works later.
    assert (tmp_path / "data" / "sec" / "events_NOPE_2024-12-31.parquet").exists()


def test_run_sec_pipeline_is_idempotent(_fake_fs, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _, chunks_first = _fake_fs.run_sec_pipeline("AMD", date(2024, 12, 31))
    _, chunks_second = _fake_fs.run_sec_pipeline("AMD", date(2024, 12, 31))
    assert sorted(c.chunk_id for c in chunks_first) == sorted(c.chunk_id for c in chunks_second)
