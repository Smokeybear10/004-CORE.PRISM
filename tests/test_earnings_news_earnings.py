"""HF-backed earnings calendar pipeline: mock HF read, assert shared parquet."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pyarrow as pa
import pytest


def _fake_cal_table(rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(
        rows,
        schema=pa.schema(
            [
                ("symbol", pa.string()),
                ("report_date", pa.string()),
                ("time", pa.string()),
                ("name", pa.string()),
                ("fiscal_quarter_ending", pa.string()),
            ]
        ),
    )


_ROWS = [
    {"symbol": "AMD", "report_date": "2026-04-30", "time": "post",
     "name": "Advanced Micro Devices", "fiscal_quarter_ending": "2026-03-31"},
    {"symbol": "NVDA", "report_date": "2026-02-20", "time": "post",
     "name": "NVIDIA Corp", "fiscal_quarter_ending": "2026-01-31"},
    {"symbol": "AAPL", "report_date": "2026-05-01", "time": "post",
     "name": "Apple Inc.", "fiscal_quarter_ending": "2026-03-31"},
]


@pytest.fixture
def _fake_pq(monkeypatch):
    import ingestion.earnings_news.earnings as earnings_mod

    def _fake_read(filters=None):
        rows = list(_ROWS)
        if filters:
            for col, op, val in filters:
                if col == "report_date" and op == "<=":
                    rows = [r for r in rows if r["report_date"] <= val]
        return _fake_cal_table(rows)

    monkeypatch.setattr(earnings_mod, "_read_calendar_table", _fake_read)
    return earnings_mod


def test_run_earnings_calendar_pipeline_writes_calendar_parquet(_fake_pq, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    events = _fake_pq.run_earnings_calendar_pipeline(date(2026, 4, 30))

    # Canonical shared filename.
    cal_path = tmp_path / "data" / "earnings" / "calendar_2026-04-30.parquet"
    assert cal_path.exists()
    df = pd.read_parquet(cal_path)
    # AAPL is after as_of; should be excluded.
    assert set(df["symbol"]) == {"AMD", "NVDA"}

    # Two Events (AMD + NVDA); AAPL filtered out.
    tickers = sorted(e.ticker for e in events)
    assert tickers == ["AMD", "NVDA"]
    assert all(e.event_type == "earnings_release" for e in events)


def test_run_earnings_calendar_pipeline_respects_as_of(_fake_pq, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    events = _fake_pq.run_earnings_calendar_pipeline(date(2026, 3, 1))
    # Only NVDA has report_date <= 2026-03-01.
    assert [e.ticker for e in events] == ["NVDA"]


def test_run_earnings_calendar_pipeline_is_idempotent(_fake_pq, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    events_first = _fake_pq.run_earnings_calendar_pipeline(date(2026, 4, 30))
    events_second = _fake_pq.run_earnings_calendar_pipeline(date(2026, 4, 30))
    assert sorted(e.event_id for e in events_first) == sorted(e.event_id for e in events_second)
