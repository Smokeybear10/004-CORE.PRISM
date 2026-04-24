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


@pytest.fixture
def _isolated(monkeypatch, tmp_path):
    """chdir to tmp_path so every recency check and write lands in-tree."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_fresh_run_calls_all_three_pipelines(_isolated, monkeypatch):
    calls = {"short": 0, "index": 0, "13f": []}

    def _short(as_of, ticker=None, **kw):
        calls["short"] += 1
        return _noop_short(as_of, ticker=ticker, **kw)

    def _index(as_of, **kw):
        calls["index"] += 1
        return _noop_index(as_of, **kw)

    def _13f(cik, current_quarter_end, prior_quarter_end, **kw):
        calls["13f"].append((cik, current_quarter_end, prior_quarter_end))
        return _noop_13f(cik, current_quarter_end, prior_quarter_end, **kw)

    monkeypatch.setattr("demo.prep_ticker.run_short_interest_pipeline", _short)
    monkeypatch.setattr("demo.prep_ticker.run_index_changes_pipeline", _index)
    monkeypatch.setattr("demo.prep_ticker.run_thirteen_f_pipeline", _13f)

    summary = prep_ticker(
        "AMD",
        date(2026, 4, 24),
        fund_ciks=["0001067983", "0001364742"],
    )

    assert calls["short"] == 1
    assert calls["index"] == 1
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
    }


def test_second_call_skips_when_recent_files_exist(_isolated, monkeypatch):
    # Pre-populate files that should trigger the recency/existence checks.
    # Short interest: filename-encoded as_of within 14d of requested.
    (_isolated / "data" / "short_interest").mkdir(parents=True)
    (_isolated / "data" / "short_interest" / "records_AMD_2026-04-22.parquet").touch()
    # Index changes: any changes_*.parquet with fresh mtime counts.
    (_isolated / "data" / "index_changes").mkdir(parents=True)
    (_isolated / "data" / "index_changes" / "changes_2026-04-20.parquet").touch()
    # 13F: deltas file for current quarter (as_of - 45d rounds to 2025-12-31).
    (_isolated / "data" / "thirteen_f").mkdir(parents=True)
    (_isolated / "data" / "thirteen_f" / "deltas_0001067983_2025-12-31.parquet").touch()

    calls = {"short": 0, "index": 0, "13f": 0}

    def _short(*a, **kw):
        calls["short"] += 1
        return _noop_short(*a, **kw)

    def _index(*a, **kw):
        calls["index"] += 1
        return _noop_index(*a, **kw)

    def _13f(*a, **kw):
        calls["13f"] += 1
        return _noop_13f(*a, **kw)

    monkeypatch.setattr("demo.prep_ticker.run_short_interest_pipeline", _short)
    monkeypatch.setattr("demo.prep_ticker.run_index_changes_pipeline", _index)
    monkeypatch.setattr("demo.prep_ticker.run_thirteen_f_pipeline", _13f)

    summary = prep_ticker("AMD", date(2026, 4, 24), fund_ciks=["0001067983"])

    assert (calls["short"], calls["index"], calls["13f"]) == (0, 0, 0)
    assert summary == {
        "short_interest": "skipped_recent",
        "index_changes": "skipped_recent",
        "thirteen_f:0001067983": "skipped_recent",
    }


def test_failure_in_one_does_not_stop_others(_isolated, monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("network blip")

    counts = {"index": 0, "13f": 0}

    def _index(as_of, **kw):
        counts["index"] += 1
        return _noop_index(as_of, **kw)

    def _13f(cik, current_quarter_end, prior_quarter_end, **kw):
        counts["13f"] += 1
        return _noop_13f(cik, current_quarter_end, prior_quarter_end, **kw)

    monkeypatch.setattr("demo.prep_ticker.run_short_interest_pipeline", _boom)
    monkeypatch.setattr("demo.prep_ticker.run_index_changes_pipeline", _index)
    monkeypatch.setattr("demo.prep_ticker.run_thirteen_f_pipeline", _13f)

    summary = prep_ticker("AMD", date(2026, 4, 24), fund_ciks=["0001067983"])

    assert summary["short_interest"].startswith("failed: ")
    assert "network blip" in summary["short_interest"]
    # Index + 13F must have run even though short_interest blew up.
    assert counts["index"] == 1
    assert counts["13f"] == 1
    assert summary["index_changes"] == "ok"
    assert summary["thirteen_f:0001067983"] == "ok"


def test_force_flag_overrides_recency_skip(_isolated, monkeypatch):
    """`--force` should re-run even when recent files exist."""
    (_isolated / "data" / "short_interest").mkdir(parents=True)
    (_isolated / "data" / "short_interest" / "records_AMD_2026-04-22.parquet").touch()
    (_isolated / "data" / "index_changes").mkdir(parents=True)
    (_isolated / "data" / "index_changes" / "changes_2026-04-20.parquet").touch()
    (_isolated / "data" / "thirteen_f").mkdir(parents=True)
    (_isolated / "data" / "thirteen_f" / "deltas_0001067983_2025-12-31.parquet").touch()

    calls = {"short": 0, "index": 0, "13f": 0}
    monkeypatch.setattr(
        "demo.prep_ticker.run_short_interest_pipeline",
        lambda *a, **kw: (calls.__setitem__("short", calls["short"] + 1), _noop_short(*a, **kw))[1],
    )
    monkeypatch.setattr(
        "demo.prep_ticker.run_index_changes_pipeline",
        lambda *a, **kw: (calls.__setitem__("index", calls["index"] + 1), _noop_index(*a, **kw))[1],
    )
    monkeypatch.setattr(
        "demo.prep_ticker.run_thirteen_f_pipeline",
        lambda *a, **kw: (calls.__setitem__("13f", calls["13f"] + 1), _noop_13f(*a, **kw))[1],
    )

    summary = prep_ticker(
        "AMD", date(2026, 4, 24), fund_ciks=["0001067983"], force=True
    )

    assert (calls["short"], calls["index"], calls["13f"]) == (1, 1, 1)
    assert summary == {
        "short_interest": "ok",
        "index_changes": "ok",
        "thirteen_f:0001067983": "ok",
    }
