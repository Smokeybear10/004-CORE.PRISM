"""Mock-based wiring test for `demo.prep_ticker.prep_ticker`.

No real network calls — the three `run_*_pipeline` functions are
monkeypatched. The tests exercise the happy path, the recency-skip branch,
and the "one failure doesn't abort the others" guarantee.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from demo.prep_ticker import prep_ticker


def _noop_short(as_of, ticker=None, **kwargs):
    return [], [], []


def _noop_index(as_of, **kwargs):
    return [], [], []


def _noop_13f(cik, current_quarter_end, prior_quarter_end, **kwargs):
    return [], [], [], []


def _noop_news(ticker, as_of, **kwargs):
    return [], []


def _noop_earnings(as_of, **kwargs):
    return []


def _noop_sec(ticker, as_of, **kwargs):
    return [], []


def _patch_all_noop(monkeypatch):
    """Patch all six pipelines to harmless no-ops so tests that only care
    about subset behavior don't accidentally hit the real HF repo."""
    monkeypatch.setattr("demo.prep_ticker.run_short_interest_pipeline", _noop_short)
    monkeypatch.setattr("demo.prep_ticker.run_index_changes_pipeline", _noop_index)
    monkeypatch.setattr("demo.prep_ticker.run_thirteen_f_pipeline", _noop_13f)
    monkeypatch.setattr("demo.prep_ticker.run_news_pipeline", _noop_news)
    monkeypatch.setattr("demo.prep_ticker.run_earnings_calendar_pipeline", _noop_earnings)
    monkeypatch.setattr("demo.prep_ticker.run_sec_pipeline", _noop_sec)


@pytest.fixture
def _isolated(monkeypatch, tmp_path):
    """chdir to tmp_path so every recency check and write lands in-tree."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_fresh_run_calls_all_pipelines(_isolated, monkeypatch):
    calls = {"short": 0, "index": 0, "13f": [], "news": 0, "earnings": 0, "sec": 0}

    def _short(as_of, ticker=None, **kw):
        calls["short"] += 1
        return _noop_short(as_of, ticker=ticker, **kw)

    def _index(as_of, **kw):
        calls["index"] += 1
        return _noop_index(as_of, **kw)

    def _13f(cik, current_quarter_end, prior_quarter_end, **kw):
        calls["13f"].append((cik, current_quarter_end, prior_quarter_end))
        return _noop_13f(cik, current_quarter_end, prior_quarter_end, **kw)

    def _news(ticker, as_of, **kw):
        calls["news"] += 1
        return _noop_news(ticker, as_of, **kw)

    def _earnings(as_of, **kw):
        calls["earnings"] += 1
        return _noop_earnings(as_of, **kw)

    def _sec(ticker, as_of, **kw):
        calls["sec"] += 1
        return _noop_sec(ticker, as_of, **kw)

    monkeypatch.setattr("demo.prep_ticker.run_short_interest_pipeline", _short)
    monkeypatch.setattr("demo.prep_ticker.run_index_changes_pipeline", _index)
    monkeypatch.setattr("demo.prep_ticker.run_thirteen_f_pipeline", _13f)
    monkeypatch.setattr("demo.prep_ticker.run_news_pipeline", _news)
    monkeypatch.setattr("demo.prep_ticker.run_earnings_calendar_pipeline", _earnings)
    monkeypatch.setattr("demo.prep_ticker.run_sec_pipeline", _sec)

    summary = prep_ticker(
        "AMD",
        date(2026, 4, 24),
        fund_ciks=["0001067983", "0001364742"],
    )

    assert calls["short"] == 1
    assert calls["index"] == 1
    assert calls["news"] == 1
    assert calls["earnings"] == 1
    assert calls["sec"] == 1
    # as_of 2026-04-24 minus 45d lag puts current_qe at 2025-12-31, prior at 2025-09-30.
    assert calls["13f"] == [
        ("0001067983", date(2025, 12, 31), date(2025, 9, 30)),
        ("0001364742", date(2025, 12, 31), date(2025, 9, 30)),
    ]
    assert summary == {
        "short_interest": "ok",
        "index_changes": "ok",
        "thirteen_f:0001067983": "ok",
        "thirteen_f:0001364742": "ok",
        "news": "ok",
        "earnings_calendar": "ok",
        "sec": "ok",
    }


def test_second_call_skips_when_recent_files_exist(_isolated, monkeypatch):
    # Pre-populate files that should trigger the recency/existence checks.
    (_isolated / "data" / "short_interest").mkdir(parents=True)
    (_isolated / "data" / "short_interest" / "records_AMD_2026-04-22.parquet").touch()
    (_isolated / "data" / "index_changes").mkdir(parents=True)
    (_isolated / "data" / "index_changes" / "changes_2026-04-20.parquet").touch()
    (_isolated / "data" / "thirteen_f").mkdir(parents=True)
    (_isolated / "data" / "thirteen_f" / "deltas_0001067983_2025-12-31.parquet").touch()
    (_isolated / "data" / "news").mkdir(parents=True)
    (_isolated / "data" / "news" / "news_AMD_2026-04-22.parquet").touch()
    (_isolated / "data" / "earnings").mkdir(parents=True)
    (_isolated / "data" / "earnings" / "calendar_2026-04-23.parquet").touch()
    (_isolated / "data" / "sec").mkdir(parents=True)
    # SEC recency window is 30d; any date within 30 of 2026-04-24 qualifies.
    (_isolated / "data" / "sec" / "events_AMD_2026-04-01.parquet").touch()

    calls = {k: 0 for k in ("short", "index", "13f", "news", "earnings", "sec")}

    def _counter(name, return_value):
        def _impl(*a, **kw):
            calls[name] += 1
            return return_value
        return _impl

    monkeypatch.setattr("demo.prep_ticker.run_short_interest_pipeline", _counter("short", _noop_short(None)))
    monkeypatch.setattr("demo.prep_ticker.run_index_changes_pipeline", _counter("index", _noop_index(None)))
    monkeypatch.setattr("demo.prep_ticker.run_thirteen_f_pipeline", _counter("13f", _noop_13f(None, None, None)))
    monkeypatch.setattr("demo.prep_ticker.run_news_pipeline", _counter("news", _noop_news(None, None)))
    monkeypatch.setattr("demo.prep_ticker.run_earnings_calendar_pipeline", _counter("earnings", _noop_earnings(None)))
    monkeypatch.setattr("demo.prep_ticker.run_sec_pipeline", _counter("sec", _noop_sec(None, None)))

    summary = prep_ticker("AMD", date(2026, 4, 24), fund_ciks=["0001067983"])

    # Nothing should have been called.
    assert all(v == 0 for v in calls.values()), calls
    assert summary == {
        "short_interest": "skipped_recent",
        "index_changes": "skipped_recent",
        "thirteen_f:0001067983": "skipped_recent",
        "news": "skipped_recent",
        "earnings_calendar": "skipped_recent",
        "sec": "skipped_recent",
    }


def test_failure_in_one_does_not_stop_others(_isolated, monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("network blip")

    counts = {"index": 0, "13f": 0, "news": 0, "earnings": 0, "sec": 0}

    def _index(as_of, **kw):
        counts["index"] += 1
        return _noop_index(as_of, **kw)

    def _13f(cik, current_quarter_end, prior_quarter_end, **kw):
        counts["13f"] += 1
        return _noop_13f(cik, current_quarter_end, prior_quarter_end, **kw)

    def _news(ticker, as_of, **kw):
        counts["news"] += 1
        return _noop_news(ticker, as_of, **kw)

    def _earnings(as_of, **kw):
        counts["earnings"] += 1
        return _noop_earnings(as_of, **kw)

    def _sec(ticker, as_of, **kw):
        counts["sec"] += 1
        return _noop_sec(ticker, as_of, **kw)

    monkeypatch.setattr("demo.prep_ticker.run_short_interest_pipeline", _boom)
    monkeypatch.setattr("demo.prep_ticker.run_index_changes_pipeline", _index)
    monkeypatch.setattr("demo.prep_ticker.run_thirteen_f_pipeline", _13f)
    monkeypatch.setattr("demo.prep_ticker.run_news_pipeline", _news)
    monkeypatch.setattr("demo.prep_ticker.run_earnings_calendar_pipeline", _earnings)
    monkeypatch.setattr("demo.prep_ticker.run_sec_pipeline", _sec)

    summary = prep_ticker("AMD", date(2026, 4, 24), fund_ciks=["0001067983"])

    assert summary["short_interest"].startswith("failed: ")
    assert "network blip" in summary["short_interest"]
    # Every other fetcher must have run even though short_interest blew up.
    assert counts == {"index": 1, "13f": 1, "news": 1, "earnings": 1, "sec": 1}
    assert summary["index_changes"] == "ok"
    assert summary["thirteen_f:0001067983"] == "ok"
    assert summary["news"] == "ok"
    assert summary["earnings_calendar"] == "ok"
    assert summary["sec"] == "ok"


def test_force_flag_overrides_recency_skip(_isolated, monkeypatch):
    """`--force` should re-run even when recent files exist."""
    (_isolated / "data" / "short_interest").mkdir(parents=True)
    (_isolated / "data" / "short_interest" / "records_AMD_2026-04-22.parquet").touch()
    (_isolated / "data" / "index_changes").mkdir(parents=True)
    (_isolated / "data" / "index_changes" / "changes_2026-04-20.parquet").touch()
    (_isolated / "data" / "thirteen_f").mkdir(parents=True)
    (_isolated / "data" / "thirteen_f" / "deltas_0001067983_2025-12-31.parquet").touch()
    (_isolated / "data" / "news").mkdir(parents=True)
    (_isolated / "data" / "news" / "news_AMD_2026-04-22.parquet").touch()
    (_isolated / "data" / "earnings").mkdir(parents=True)
    (_isolated / "data" / "earnings" / "calendar_2026-04-23.parquet").touch()
    (_isolated / "data" / "sec").mkdir(parents=True)
    (_isolated / "data" / "sec" / "events_AMD_2026-04-01.parquet").touch()

    counts = {k: 0 for k in ("short", "index", "13f", "news", "earnings", "sec")}

    def _bump(name, payload):
        def _impl(*a, **kw):
            counts[name] += 1
            return payload
        return _impl

    monkeypatch.setattr("demo.prep_ticker.run_short_interest_pipeline", _bump("short", _noop_short(None)))
    monkeypatch.setattr("demo.prep_ticker.run_index_changes_pipeline", _bump("index", _noop_index(None)))
    monkeypatch.setattr("demo.prep_ticker.run_thirteen_f_pipeline", _bump("13f", _noop_13f(None, None, None)))
    monkeypatch.setattr("demo.prep_ticker.run_news_pipeline", _bump("news", _noop_news(None, None)))
    monkeypatch.setattr("demo.prep_ticker.run_earnings_calendar_pipeline", _bump("earnings", _noop_earnings(None)))
    monkeypatch.setattr("demo.prep_ticker.run_sec_pipeline", _bump("sec", _noop_sec(None, None)))

    summary = prep_ticker(
        "AMD", date(2026, 4, 24), fund_ciks=["0001067983"], force=True
    )

    assert counts == {"short": 1, "index": 1, "13f": 1, "news": 1, "earnings": 1, "sec": 1}
    assert summary == {
        "short_interest": "ok",
        "index_changes": "ok",
        "thirteen_f:0001067983": "ok",
        "news": "ok",
        "earnings_calendar": "ok",
        "sec": "ok",
    }
