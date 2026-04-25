"""
Shared pytest fixtures.

The `patched_loader` fixture monkeypatches `ingestion.prices.hf_loader` so
tests never touch HuggingFace and never pollute the real data/cache
directory. The fake `_read_hf_parquet` reads local fixture parquets while
honoring pyarrow pushdown filters — same code path as production.

Also loads .env at the repo root so live-API tests (RUN_LIVE_API=1) can
see ANTHROPIC_API_KEY. .env is gitignored.
"""
from __future__ import annotations

# Load repo-root .env before anything imports anthropic/etc.
try:
    from dotenv import load_dotenv
    from pathlib import Path as _Path
    _env_path = _Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv not installed — env stays whatever the shell set

from pathlib import Path

import pyarrow.parquet as pq
import pytest

from ingestion.prices import hf_loader as yahoo_loader

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
