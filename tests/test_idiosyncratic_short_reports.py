"""
Tests for `ingestion.idiosyncratic.short_reports`.

The six publisher scrapers all share a single _scrape_generic implementation
plus one dedicated Scorpion path. We monkeypatch requests.Session.get to
return canned HTML — real network never touched.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import requests

from ingestion.idiosyncratic import short_reports as sr
from schema import ShortReport, SourceType


FIXTURE = Path(__file__).parent / "fixtures" / "idiosyncratic" / "short_reports_sample.json"


# ---------- fixture conforms to schema ----------


def test_fixture_parses_as_short_reports():
    raw = json.loads(FIXTURE.read_text())
    for row in raw:
        ShortReport(**row)


# ---------- chunk_id + ticker extraction ----------


def test_make_chunk_id_is_deterministic():
    a = sr.make_chunk_id("Hindenburg Research", "NKLA", date(2024, 2, 28))
    b = sr.make_chunk_id("Hindenburg Research", "nkla", date(2024, 2, 28))
    assert a == b == "short_report_hindenburg_NKLA_2024-02-28"


def test_make_chunk_id_unknown_publisher_slugs_fallback():
    cid = sr.make_chunk_id("Some New Short Seller", "AAPL", date(2024, 1, 1))
    assert cid == "short_report_some_new_short_seller_AAPL_2024-01-01"


def test_extract_ticker_dollar_prefix():
    assert sr.extract_ticker("Why $NKLA is a mirage") == "NKLA"
    assert sr.extract_ticker("$RIVN: Production Hell 2.0") == "RIVN"


def test_extract_ticker_dollar_prefix_reads_body_when_title_clean():
    assert sr.extract_ticker("A deep dive", "We analyzed $TSLA...") == "TSLA"


def test_extract_ticker_name_lookup(monkeypatch):
    """Fallback to SEC name→ticker when no $TICKER in title."""
    # Seed the cache with a known name map so the test stays offline.
    monkeypatch.setattr(
        sr, "_NAME_MAP_CACHE",
        {"ALIBABA": "BABA", "TESLA": "TSLA"},
    )
    assert sr.extract_ticker("Alibaba: The Party's Over") == "BABA"
    assert sr.extract_ticker("Tesla") == "TSLA"


def test_extract_ticker_none_when_unresolvable(monkeypatch):
    monkeypatch.setattr(sr, "_NAME_MAP_CACHE", {})
    assert sr.extract_ticker("Random post with no ticker") is None


# ---------- dispatcher validation ----------


def test_fetch_short_reports_unknown_publisher_raises():
    with pytest.raises(ValueError):
        sr.fetch_short_reports("Some Fake LLC", as_of=date(2024, 12, 31))


def test_dispatcher_catches_scraper_exceptions(monkeypatch):
    """If scraping blows up, return [], don't raise."""

    def _bad_scraper(as_of, session):
        raise RuntimeError("boom")

    monkeypatch.setitem(sr._SCRAPERS, "Hindenburg Research", _bad_scraper)
    out = sr.fetch_short_reports("Hindenburg Research", as_of=date(2024, 12, 31))
    assert out == []


# ---------- generic WordPress-ish scraper ----------


class _FakeResp:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class _FakeSession:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        text = self.pages.get(url, "")
        return _FakeResp(text, 200 if text else 404)


_HINDENBURG_INDEX = """
<html><body>
<article>
  <a href="https://hindenburgresearch.com/2024/02/28/nikola-technology-mirage">
    $NKLA: Nikola Technology Mirage Continues
  </a>
</article>
<article>
  <a href="https://hindenburgresearch.com/2023/08/15/another-short-call">
    $ADBE: Adobe Flawed Analytics
  </a>
</article>
<nav><a href="https://hindenburgresearch.com/about">About</a></nav>
</body></html>
"""


def test_generic_scraper_extracts_reports(monkeypatch):
    sess = _FakeSession({
        "https://hindenburgresearch.com/": _HINDENBURG_INDEX,
        # Article bodies not fetched because titles already contain $TICKERs
    })
    monkeypatch.setattr(sr, "_NAME_MAP_CACHE", {})
    reports = sr.fetch_short_reports(
        "Hindenburg Research", as_of=date(2024, 12, 31), session=sess,
    )
    assert len(reports) == 2
    tickers = sorted(r.target_ticker for r in reports)
    assert tickers == ["ADBE", "NKLA"]
    nkla = next(r for r in reports if r.target_ticker == "NKLA")
    assert nkla.publication_date == date(2024, 2, 28)
    assert nkla.chunk_id == "short_report_hindenburg_NKLA_2024-02-28"
    assert nkla.source_url.startswith("https://hindenburgresearch.com/2024/02/28/")


def test_generic_scraper_respects_as_of(monkeypatch):
    sess = _FakeSession({"https://hindenburgresearch.com/": _HINDENBURG_INDEX})
    monkeypatch.setattr(sr, "_NAME_MAP_CACHE", {})
    reports = sr.fetch_short_reports(
        "Hindenburg Research", as_of=date(2024, 1, 1), session=sess,
    )
    # 2024-02-28 is future relative to as_of
    assert all(r.publication_date <= date(2024, 1, 1) for r in reports)
    assert "NKLA" not in {r.target_ticker for r in reports}
    assert "ADBE" in {r.target_ticker for r in reports}


def test_generic_scraper_skips_untickered_entries(monkeypatch):
    html = """
    <html><body>
      <article><a href="https://hindenburgresearch.com/2024/03/01/no-ticker-here">
        Generic random thoughts about finance
      </a></article>
    </body></html>
    """
    sess = _FakeSession({
        "https://hindenburgresearch.com/": html,
        "https://hindenburgresearch.com/2024/03/01/no-ticker-here": "<html></html>",
    })
    monkeypatch.setattr(sr, "_NAME_MAP_CACHE", {})
    reports = sr.fetch_short_reports(
        "Hindenburg Research", as_of=date(2024, 12, 31), session=sess,
    )
    assert reports == []


def test_generic_scraper_network_failure_returns_empty(monkeypatch):
    class _BadSession:
        def get(self, url, timeout=None):
            raise requests.ConnectionError("dns failure")
    monkeypatch.setattr(sr, "_NAME_MAP_CACHE", {})
    reports = sr.fetch_short_reports(
        "Muddy Waters Research", as_of=date(2024, 12, 31), session=_BadSession(),
    )
    assert reports == []


# ---------- Scorpion JS-rendered path ----------


def test_scorpion_detected_as_js_rendered_returns_empty(monkeypatch):
    empty_shell = "<html><body><div id='root'></div></body></html>"
    sess = _FakeSession({"https://www.scorpioncapital.com/reports": empty_shell})
    monkeypatch.setattr(sr, "_NAME_MAP_CACHE", {})
    reports = sr.fetch_short_reports(
        "Scorpion Capital", as_of=date(2024, 12, 31), session=sess,
    )
    assert reports == []


# ---------- fetch_all + dedup ----------


def test_fetch_all_aggregates_and_dedupes(monkeypatch):
    # Inject two fake scrapers that overlap on a chunk_id
    shared = ShortReport(
        chunk_id="short_report_hindenburg_NKLA_2024-02-28",
        publisher="Hindenburg Research", target_ticker="NKLA",
        publication_date=date(2024, 2, 28),
        title="A", thesis_text="a", source_url=None, token_count=1,
    )
    distinct = ShortReport(
        chunk_id="short_report_muddy_waters_BABA_2024-01-15",
        publisher="Muddy Waters Research", target_ticker="BABA",
        publication_date=date(2024, 1, 15),
        title="B", thesis_text="b", source_url=None, token_count=1,
    )
    fake_scrapers = {
        "Hindenburg Research": lambda as_of, s: [shared],
        "Muddy Waters Research": lambda as_of, s: [shared, distinct],
        "Citron Research": lambda as_of, s: [],
        "Kerrisdale Capital": lambda as_of, s: [],
        "Spruce Point Capital": lambda as_of, s: [],
        "Scorpion Capital": lambda as_of, s: [],
    }
    monkeypatch.setattr(sr, "_SCRAPERS", fake_scrapers)
    all_reports = sr.fetch_all_short_reports(date(2024, 12, 31))
    # Dedup by chunk_id keeps first-seen publisher; newest first
    ids = [r.chunk_id for r in all_reports]
    assert ids == [
        "short_report_hindenburg_NKLA_2024-02-28",
        "short_report_muddy_waters_BABA_2024-01-15",
    ]


# ---------- reports_to_events + events_to_text_chunks + pipeline ----------


def test_reports_to_events_shape():
    r = ShortReport(
        chunk_id="short_report_hindenburg_NKLA_2024-02-28",
        publisher="Hindenburg Research", target_ticker="NKLA",
        publication_date=date(2024, 2, 28),
        title="Nikola", thesis_text="thesis", source_url=None, token_count=1,
    )
    events = sr.reports_to_events([r])
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "short_report"
    assert ev.source == "Hindenburg Research"
    assert ev.event_date == date(2024, 2, 28)
    chunks = sr.events_to_text_chunks(events)
    assert chunks[0].source_type == SourceType.NEWS


def test_pipeline_writes_parquet_and_jsonl(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sr, "_SCRAPERS",
        {"Hindenburg Research": lambda as_of, s: [
            ShortReport(
                chunk_id="short_report_hindenburg_NKLA_2024-02-28",
                publisher="Hindenburg Research", target_ticker="NKLA",
                publication_date=date(2024, 2, 28),
                title="Nikola", thesis_text="thesis",
                source_url=None, token_count=1,
            ),
        ]},
    )
    reports, events, chunks = sr.run_short_reports_pipeline(
        as_of=date(2024, 12, 31), publisher="Hindenburg Research",
        output_dir=tmp_path,
    )
    assert len(reports) == 1

    records_path = tmp_path / "records_hindenburg_2024-12-31.parquet"
    events_path = tmp_path / "events_hindenburg_2024-12-31.parquet"
    chunks_path = tmp_path / "chunks_hindenburg_2024-12-31.jsonl"
    assert records_path.exists() and events_path.exists() and chunks_path.exists()

    recs = pd.read_parquet(records_path)
    assert {"chunk_id", "publisher", "target_ticker",
            "publication_date", "title", "thesis_text"}.issubset(recs.columns)
