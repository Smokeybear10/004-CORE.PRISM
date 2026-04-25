"""
Shared pytest fixtures.

The `patched_loader` fixture monkeypatches `ingestion.prices.hf_loader` so
tests never touch HuggingFace and never pollute the real data/cache
directory. The fake `_read_hf_parquet` reads local fixture parquets while
honoring pyarrow pushdown filters — same code path as production.

Also: a session-scoped autouse fixture force-disables LIVE attribution
mode in `model.attribute()` for the entire test run, regardless of what's
in the developer's `.env`. Reason: importing `demo.analyze_ticker`
(which `tests/test_orchestrator.py` does) calls `load_dotenv()` at import
time, which would otherwise leak `BW_USE_LIVE_ATTRIBUTION=1` and
`ANTHROPIC_API_KEY` into the test environment and cause `model.attribute`
to hit the real Anthropic API mid-suite. Tests that need to exercise live
mode use `monkeypatch.setenv` per-test (see test_model_attribute_bridge.py).
"""
from __future__ import annotations

import os
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from ingestion.prices import hf_loader as yahoo_loader


@pytest.fixture(autouse=True, scope="session")
def _disable_live_attribution_for_tests():
    """Force model.attribute() into placeholder mode for the whole session."""
    prior_live = os.environ.pop("BW_USE_LIVE_ATTRIBUTION", None)
    prior_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ["BW_USE_LIVE_ATTRIBUTION"] = "0"
    yield
    os.environ.pop("BW_USE_LIVE_ATTRIBUTION", None)
    if prior_live is not None:
        os.environ["BW_USE_LIVE_ATTRIBUTION"] = prior_live
    if prior_key is not None:
        os.environ["ANTHROPIC_API_KEY"] = prior_key

FIXTURE_DIR = Path(__file__).parent / "fixtures"

_FIXTURE_FOR_HF_FILE = {
    "stock_prices.parquet": FIXTURE_DIR / "prices_sample.parquet",
    "stock_split_events.parquet": FIXTURE_DIR / "splits_sample.parquet",
    "stock_dividend_events.parquet": FIXTURE_DIR / "dividends_sample.parquet",
}


def _fake_read_hf_parquet(filename: str, filters):
    path = _FIXTURE_FOR_HF_FILE.get(filename)
    if path is None:
        raise AssertionError(f"no fixture registered for HF file {filename!r}")
    return pq.read_table(path, filters=filters).to_pandas()


@pytest.fixture
def patched_loader(monkeypatch, tmp_path):
    """Yahoo_loader with HF reads redirected to fixtures and cache in tmp_path."""
    monkeypatch.setattr(yahoo_loader, "_read_hf_parquet", _fake_read_hf_parquet)
    monkeypatch.setattr(yahoo_loader, "CACHE_DIR", tmp_path / "cache")
    return yahoo_loader


@pytest.fixture
def spy_reader(monkeypatch, tmp_path):
    """
    Like patched_loader, but records every call to _read_hf_parquet so tests
    can assert cache-hit/miss behavior.
    """
    calls: list[tuple[str, list | None]] = []

    def spy(filename, filters):
        calls.append((filename, filters))
        return _fake_read_hf_parquet(filename, filters)

    monkeypatch.setattr(yahoo_loader, "_read_hf_parquet", spy)
    monkeypatch.setattr(yahoo_loader, "CACHE_DIR", tmp_path / "cache")
    return yahoo_loader, calls
