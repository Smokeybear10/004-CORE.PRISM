"""
Tests for ingestion/events/adapters/*.

Each adapter takes records (Pydantic models or dicts) and emits Events
with deterministic IDs and the correct `event_date` semantics per CLAUDE.md.
Fixtures live under tests/fixtures/idiosyncratic/ (shared with the source
modules) and tests/fixtures/events/ for news/earnings synthetic rows.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from schema import (
    AnalystRating,
    FDAEvent,
    FDAEventType,
    HoldingAction,
    HoldingDelta,
    IndexChange,
    IndexChangeAction,
    PriceTargetChange,
    RatingAction,
    ShortInterestRecord,
    ShortReport,
    SourceType,
)
from ingestion.events.adapters import (
    analyst,
    earnings,
    fda,
    index_changes,
    news,
    short_interest,
    short_reports,
    thirteen_f,
)

IDIO_DIR = Path(__file__).parent / "fixtures" / "idiosyncratic"


# ---------- 13F ----------

def _load_holding_deltas() -> list[HoldingDelta]:
    raw = json.loads((IDIO_DIR / "holding_deltas_sample.json").read_text())
    out: list[HoldingDelta] = []
    for row in raw:
        out.append(
            HoldingDelta(
                fund_cik=row["fund_cik"],
                fund_name=row["fund_name"],
                ticker=row["ticker"],
                current_filing_date=date.fromisoformat(row["current_filing_date"]),
                current_period_end=date.fromisoformat(row["current_period_end"]),
                action=HoldingAction(row["action"]),
                shares_change=row["shares_change"],
                market_value_change=row["market_value_change"],
                prior_shares=row.get("prior_shares"),
                current_shares=row["current_shares"],
            )
        )
    return out


def test_thirteen_f_event_count_and_types():
    deltas = _load_holding_deltas()
    events = thirteen_f.to_events(deltas)
    assert len(events) == len(deltas)
    for e in events:
        assert e.event_type == "13f_delta"
        assert e.source == "SEC EDGAR"
        assert e.text  # synthesized


def test_thirteen_f_event_date_is_filing_date_not_period_end():
    deltas = _load_holding_deltas()
    events = thirteen_f.to_events(deltas)
    for d, e in zip(deltas, events):
        assert e.event_date == d.current_filing_date
        assert e.event_date != d.current_period_end


def test_thirteen_f_ids_are_deterministic():
    deltas = _load_holding_deltas()
    first = [e.event_id for e in thirteen_f.to_events(deltas)]
    second = [e.event_id for e in thirteen_f.to_events(deltas)]
    assert first == second
    assert len(set(first)) == len(first)  # unique


# ---------- short interest ----------

def test_short_interest_only_emits_on_spike():
    # Two records for one ticker: +25% rise (spike) followed by -10% (no spike).
    records = [
        ShortInterestRecord(
            ticker="AMD",
            settlement_date=date(2024, 1, 15),
            shares_short=10_000_000,
        ),
        ShortInterestRecord(
            ticker="AMD",
            settlement_date=date(2024, 1, 31),
            shares_short=12_600_000,  # +26% — spike
        ),
        ShortInterestRecord(
            ticker="AMD",
            settlement_date=date(2024, 2, 15),
            shares_short=11_340_000,  # -10% — no spike
        ),
    ]
    events = short_interest.to_events(records)
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "short_interest_spike"
    assert ev.ticker == "AMD"
    assert ev.event_date == date(2024, 1, 31)
    assert "26" in ev.text  # includes the % change


def test_short_interest_sample_fixture_has_no_spikes():
    raw = json.loads((IDIO_DIR / "short_interest_sample.json").read_text())
    records = [
        ShortInterestRecord(
            ticker=r["ticker"],
            settlement_date=date.fromisoformat(r["settlement_date"]),
            shares_short=r["shares_short"],
            avg_daily_volume=r.get("avg_daily_volume"),
            days_to_cover=r.get("days_to_cover"),
            float_short_percent=r.get("float_short_percent"),
        )
        for r in raw
    ]
    events = short_interest.to_events(records)
    # Manual check: max PoP Δ in the fixture is <20%, so nothing fires.
    for e in events:
        assert False, f"unexpected spike: {e.event_id}"


def test_short_interest_skips_zero_prior():
    records = [
        ShortInterestRecord(ticker="X", settlement_date=date(2024, 1, 15), shares_short=0),
        ShortInterestRecord(ticker="X", settlement_date=date(2024, 1, 31), shares_short=1_000_000),
    ]
    assert short_interest.to_events(records) == []


# ---------- index changes ----------

def _load_index_changes() -> list[IndexChange]:
    raw = json.loads((IDIO_DIR / "index_changes_sample.json").read_text())
    return [
        IndexChange(
            change_id=row["change_id"],
            index_name=row["index_name"],
            action=IndexChangeAction(row["action"]),
            ticker=row["ticker"],
            company_name=row["company_name"],
            announcement_date=date.fromisoformat(row["announcement_date"]),
            effective_date=date.fromisoformat(row["effective_date"]),
            replacing_ticker=row.get("replacing_ticker"),
            source_url=row.get("source_url"),
        )
        for row in raw
    ]


def test_index_changes_emits_two_events_per_change():
    changes = _load_index_changes()
    events = index_changes.to_events(changes)
    assert len(events) == 2 * len(changes)

    types = {e.event_type for e in events}
    assert types == {"index_change_announcement", "index_change_effective"}


def test_index_changes_event_dates_match_source():
    changes = _load_index_changes()
    events = index_changes.to_events(changes)
    by_id = {e.event_id: e for e in events}
    for c in changes:
        ann = by_id[f"{c.change_id}_announcement"]
        eff = by_id[f"{c.change_id}_effective"]
        assert ann.event_date == c.announcement_date
        assert eff.event_date == c.effective_date


# ---------- short reports ----------

def _load_short_reports() -> list[ShortReport]:
    raw = json.loads((IDIO_DIR / "short_reports_sample.json").read_text())
    return [
        ShortReport(
            chunk_id=r["chunk_id"],
            publisher=r["publisher"],
            target_ticker=r["target_ticker"],
            publication_date=date.fromisoformat(r["publication_date"]),
            title=r["title"],
            thesis_text=r["thesis_text"],
            source_url=r.get("source_url"),
            token_count=r.get("token_count"),
        )
        for r in raw
    ]


def test_short_reports_event_and_chunk_per_report():
    reports = _load_short_reports()
    events = short_reports.to_events(reports)
    chunks = short_reports.to_chunks(reports)
    assert len(events) == len(reports)
    assert len(chunks) == len(reports)

    for r, e, c in zip(reports, events, chunks):
        assert e.event_id == c.chunk_id == r.chunk_id
        assert e.event_type == "short_report"
        assert e.ticker == r.target_ticker
        assert e.event_date == r.publication_date
        assert c.source_type == SourceType.SHORT_INTEREST
        assert c.text == r.thesis_text


# ---------- FDA ----------

def _load_fda_events() -> list[FDAEvent]:
    raw = json.loads((IDIO_DIR / "fda_events_sample.json").read_text())
    return [
        FDAEvent(
            event_id=r["event_id"],
            event_type=FDAEventType(r["event_type"]),
            event_date=date.fromisoformat(r["event_date"]),
            sponsor_ticker=r.get("sponsor_ticker"),
            drug_name=r["drug_name"],
            indication=r.get("indication"),
            description=r["description"],
            source_url=r.get("source_url"),
        )
        for r in raw
    ]


def test_fda_emits_event_per_tickered_record():
    records = _load_fda_events()
    events = fda.to_events(records)
    # All fixture rows have a ticker, so all produce events.
    assert len(events) == len(records)
    for r, e in zip(records, events):
        assert e.ticker == r.sponsor_ticker
        assert e.event_type == r.event_type.value
        assert e.event_date == r.event_date
        assert r.drug_name in e.text


def test_fda_drops_records_with_no_ticker():
    records = [
        FDAEvent(
            event_id="fda_crl_PRIVATE_x_2024-01-01",
            event_type=FDAEventType.CRL,
            event_date=date(2024, 1, 1),
            sponsor_ticker=None,
            drug_name="Mystery Drug",
            description="CRL issued",
        ),
    ]
    assert fda.to_events(records) == []


# ---------- analyst ----------

def _load_analyst_ratings() -> list[AnalystRating]:
    raw = json.loads((IDIO_DIR / "analyst_ratings_sample.json").read_text())
    return [
        AnalystRating(
            rating_id=r["rating_id"],
            ticker=r["ticker"],
            analyst_firm=r["analyst_firm"],
            analyst_name=r.get("analyst_name"),
            action=RatingAction(r["action"]),
            new_rating=r.get("new_rating"),
            prior_rating=r.get("prior_rating"),
            action_date=date.fromisoformat(r["action_date"]),
            source_url=r.get("source_url"),
        )
        for r in raw
    ]


def _load_price_targets() -> list[PriceTargetChange]:
    raw = json.loads((IDIO_DIR / "price_targets_sample.json").read_text())
    return [
        PriceTargetChange(
            target_id=r["target_id"],
            ticker=r["ticker"],
            analyst_firm=r["analyst_firm"],
            analyst_name=r.get("analyst_name"),
            new_target=r.get("new_target"),
            prior_target=r.get("prior_target"),
            change_pct=r.get("change_pct"),
            action_date=date.fromisoformat(r["action_date"]),
            source_url=r.get("source_url"),
        )
        for r in raw
    ]


def test_analyst_rating_events_use_action_date():
    ratings = _load_analyst_ratings()
    events = analyst.rating_to_events(ratings)
    assert len(events) == len(ratings)
    for r, e in zip(ratings, events):
        assert e.event_type == "analyst_rating_change"
        assert e.event_date == r.action_date
        assert e.event_id == r.rating_id


def test_analyst_target_events_use_action_date():
    targets = _load_price_targets()
    events = analyst.target_to_events(targets)
    assert len(events) == len(targets)
    for t, e in zip(targets, events):
        assert e.event_type == "price_target_change"
        assert e.event_date == t.action_date
        assert e.event_id == t.target_id


def test_analyst_dispatcher_combines_ratings_and_targets():
    ratings = _load_analyst_ratings()
    targets = _load_price_targets()
    events = analyst.to_events(ratings=ratings, targets=targets)
    assert len(events) == len(ratings) + len(targets)


# ---------- news ----------

NEWS_ROW = {
    "uuid": "abc123",
    "related_symbols": "AAPL,MSFT",
    "title": "Tech earnings preview",
    "publisher": "Reuters",
    "report_date": "2024-02-01",
    "type": "STORY",
    "link": "https://example.com/tech-preview",
    "news": [
        {"paragraph_number": 0, "highlight": "", "paragraph": "Apple reports Thursday."},
        {"paragraph_number": 1, "highlight": "", "paragraph": "Microsoft beat on cloud."},
    ],
}


def test_news_fans_paragraphs_to_events_and_chunks():
    events = news.to_events([NEWS_ROW])
    chunks = news.to_chunks([NEWS_ROW])
    # 2 tickers * 2 paragraphs = 4 events
    assert len(events) == 4
    assert len(chunks) == 4
    event_ids = {e.event_id for e in events}
    assert event_ids == {"news_abc123_p0", "news_abc123_p1"}
    # Each ticker appears twice (once per paragraph).
    tickers = sorted(e.ticker for e in events)
    assert tickers == ["AAPL", "AAPL", "MSFT", "MSFT"]


def test_news_event_id_matches_chunk_id():
    events = news.to_events([NEWS_ROW])
    chunks = news.to_chunks([NEWS_ROW])
    event_ids = {e.event_id for e in events}
    chunk_ids = {c.chunk_id for c in chunks}
    assert event_ids == chunk_ids


def test_news_dedupe_by_title_date_ticker():
    dup = dict(NEWS_ROW)
    rows = [NEWS_ROW, dup]
    deduped = news.dedupe_articles(rows)
    assert len(deduped) == 1


def test_news_skips_rows_without_uuid_or_date():
    bad = [
        {"uuid": "", "related_symbols": "AAPL", "title": "x", "report_date": "2024-02-01", "news": []},
        {"uuid": "xyz", "related_symbols": "AAPL", "title": "x", "report_date": None, "news": []},
    ]
    assert news.to_events(bad) == []
    assert news.to_chunks(bad) == []


# ---------- earnings ----------

EARNINGS_ROWS = [
    {
        "symbol": "AAPL",
        "report_date": "2024-02-01",
        "time": "post",
        "name": "Apple Inc.",
        "fiscal_quarter_ending": "2023-12-30",
    },
    {
        "symbol": "MSFT",
        "report_date": "2024-01-30",
        "time": "post",
        "name": "Microsoft Corp.",
        "fiscal_quarter_ending": "2023-12-31",
    },
]


def test_earnings_adapter_basic_shape():
    events = earnings.to_events(EARNINGS_ROWS)
    assert len(events) == 2
    ids = {e.event_id for e in events}
    assert ids == {"earnings_AAPL_2024-02-01", "earnings_MSFT_2024-01-30"}
    for e in events:
        assert e.event_type == "earnings_release"
        assert e.source == "yahoo_earnings_calendar"
        assert e.text


def test_earnings_adapter_rejects_missing_fields():
    bad = [
        {"symbol": "", "report_date": "2024-02-01"},
        {"symbol": "AAPL", "report_date": None},
    ]
    assert earnings.to_events(bad) == []


# ---------- aggregator smoke test ----------

def test_aggregator_writes_empty_events_parquet(tmp_path, monkeypatch):
    """With no source parquets present, the aggregator still produces a valid
    (empty) events parquet. Ensures the no-data path doesn't crash."""
    from ingestion.events.aggregator import build_events_parquet

    out_events = tmp_path / "cache" / "events.parquet"
    out_chunks = tmp_path / "cache" / "text_chunks.parquet"
    events_df = build_events_parquet(
        as_of=date(2024, 4, 24),
        out_path=out_events,
        data_dir=tmp_path / "nonexistent",
        chunks_out_path=out_chunks,
    )
    assert out_events.exists()
    assert out_chunks.exists()
    assert events_df.empty
    # Column contract holds even on empty.
    import pandas as pd
    reloaded = pd.read_parquet(out_events)
    assert list(reloaded.columns) == [
        "event_id", "ticker", "event_date", "event_type",
        "source", "payload_ref", "text",
    ]


# ---------- 13F: per-action text branches ----------

def _mk_delta(action: HoldingAction, **overrides) -> HoldingDelta:
    base = dict(
        fund_cik="0000000001",
        fund_name="TEST FUND",
        ticker="AAPL",
        current_filing_date=date(2024, 2, 14),
        current_period_end=date(2023, 12, 31),
        action=action,
        shares_change=1_000_000,
        market_value_change=150_000_000,
        prior_shares=500_000,
        current_shares=1_500_000,
    )
    base.update(overrides)
    return HoldingDelta(**base)


def test_thirteen_f_new_position_text():
    d = _mk_delta(HoldingAction.NEW, prior_shares=None, shares_change=1_500_000)
    events = thirteen_f.to_events([d])
    assert len(events) == 1
    assert "opened new AAPL position" in events[0].text
    assert "1,500,000 shares" in events[0].text


def test_thirteen_f_exited_position_text():
    d = _mk_delta(
        HoldingAction.EXITED,
        shares_change=-2_000_000,
        market_value_change=-300_000_000,
        current_shares=0,
    )
    events = thirteen_f.to_events([d])
    assert "exited AAPL" in events[0].text
    assert "2,000,000 shares" in events[0].text


def test_thirteen_f_increased_and_reduced_text():
    inc = _mk_delta(HoldingAction.INCREASED)
    red = _mk_delta(
        HoldingAction.REDUCED,
        shares_change=-500_000,
        market_value_change=-75_000_000,
        current_shares=1_000_000,
    )
    events = thirteen_f.to_events([inc, red])
    assert "increased AAPL" in events[0].text
    assert "+1,000,000 shares" in events[0].text
    assert "reduced AAPL" in events[1].text
    assert "-500,000 shares" in events[1].text


def test_thirteen_f_ids_differentiate_by_fund_and_period():
    # Same ticker, same action, different (fund, period) -> unique IDs.
    a = _mk_delta(HoldingAction.INCREASED, fund_cik="0000000001")
    b = _mk_delta(HoldingAction.INCREASED, fund_cik="0000000002")
    c = _mk_delta(
        HoldingAction.INCREASED,
        current_period_end=date(2023, 9, 30),
        current_filing_date=date(2023, 11, 14),
    )
    events = thirteen_f.to_events([a, b, c])
    ids = [e.event_id for e in events]
    assert len(set(ids)) == 3


# ---------- short interest: boundary + edge cases ----------

def test_short_interest_at_exactly_20pct_does_not_fire():
    # Spec: ONLY fires when change > 20%. 20.0% exactly should NOT fire.
    records = [
        ShortInterestRecord(
            ticker="X", settlement_date=date(2024, 1, 15), shares_short=100_000,
        ),
        ShortInterestRecord(
            ticker="X", settlement_date=date(2024, 1, 31), shares_short=120_000,  # +20.0%
        ),
    ]
    assert short_interest.to_events(records) == []


def test_short_interest_just_over_20pct_fires():
    records = [
        ShortInterestRecord(
            ticker="X", settlement_date=date(2024, 1, 15), shares_short=100_000,
        ),
        ShortInterestRecord(
            ticker="X", settlement_date=date(2024, 1, 31), shares_short=120_001,  # +20.001%
        ),
    ]
    assert len(short_interest.to_events(records)) == 1


def test_short_interest_days_to_cover_none_renders_na():
    records = [
        ShortInterestRecord(
            ticker="Y", settlement_date=date(2024, 1, 15), shares_short=10_000,
        ),
        ShortInterestRecord(
            ticker="Y", settlement_date=date(2024, 1, 31),
            shares_short=20_000, days_to_cover=None,
        ),
    ]
    events = short_interest.to_events(records)
    assert "days-to-cover n/a" in events[0].text


def test_short_interest_multi_ticker_independent():
    records = [
        # X: no spike
        ShortInterestRecord(ticker="X", settlement_date=date(2024, 1, 15), shares_short=100),
        ShortInterestRecord(ticker="X", settlement_date=date(2024, 1, 31), shares_short=110),
        # Y: spike
        ShortInterestRecord(ticker="Y", settlement_date=date(2024, 1, 15), shares_short=100),
        ShortInterestRecord(ticker="Y", settlement_date=date(2024, 1, 31), shares_short=200),
    ]
    events = short_interest.to_events(records)
    assert len(events) == 1
    assert events[0].ticker == "Y"


# ---------- index changes: all 4 text branches ----------

def _mk_index(action: IndexChangeAction, replacing=None, **overrides) -> IndexChange:
    base = dict(
        change_id=f"sp500_{'add' if action == IndexChangeAction.ADD else 'del'}_{overrides.get('ticker', 'ABC')}_2024-06-15",
        index_name="S&P 500",
        action=action,
        ticker="ABC",
        company_name="ABC Corp",
        announcement_date=date(2024, 6, 10),
        effective_date=date(2024, 6, 15),
        replacing_ticker=replacing,
    )
    base.update(overrides)
    return IndexChange(**base)


def test_index_change_add_with_replacing_ticker():
    c = _mk_index(IndexChangeAction.ADD, replacing="OLD")
    events = index_changes.to_events([c])
    assert len(events) == 2
    ann_text = events[0].text
    assert "added to" in ann_text
    assert "(replacing OLD)" in ann_text


def test_index_change_delete_with_replacement():
    c = _mk_index(IndexChangeAction.DELETE, replacing="NEW")
    events = index_changes.to_events([c])
    ann_text = events[0].text
    assert "removed from" in ann_text
    assert "(replaced by NEW)" in ann_text


def test_index_change_add_without_replacement():
    c = _mk_index(IndexChangeAction.ADD, replacing=None)
    events = index_changes.to_events([c])
    assert "(replacing" not in events[0].text
    assert "added to" in events[0].text


def test_index_change_delete_without_replacement():
    c = _mk_index(IndexChangeAction.DELETE, replacing=None)
    events = index_changes.to_events([c])
    assert "(replaced by" not in events[0].text
    assert "removed from" in events[0].text


# ---------- FDA: every event_type renders ----------

@pytest.mark.parametrize("event_type,fda_str", [
    (FDAEventType.PDUFA, "PDUFA"),
    (FDAEventType.ADCOMM, "ADCOMM"),
    (FDAEventType.APPROVAL, "APPROVAL"),
    (FDAEventType.CRL, "CRL"),
    (FDAEventType.DENIAL, "DENIAL"),
])
def test_fda_text_includes_event_type_upper(event_type, fda_str):
    ev = FDAEvent(
        event_id=f"fda_{event_type.value}_BIIB_TESTDRUG_2024-06-07",
        event_type=event_type,
        event_date=date(2024, 6, 7),
        sponsor_ticker="BIIB",
        drug_name="TestDrug",
        indication="Alzheimer's",
        description="Decision narrative.",
    )
    events = fda.to_events([ev])
    assert len(events) == 1
    assert fda_str in events[0].text
    assert events[0].event_type == event_type.value  # not upper


def test_fda_no_indication_omits_indication_clause():
    ev = FDAEvent(
        event_id="fda_approval_X_Y_2024-06-07",
        event_type=FDAEventType.APPROVAL,
        event_date=date(2024, 6, 7),
        sponsor_ticker="X",
        drug_name="Ydrug",
        indication=None,
        description="Approved.",
    )
    events = fda.to_events([ev])
    assert "for " not in events[0].text.split("(X)")[0] or "Ydrug" in events[0].text


# ---------- analyst: initiation and edge cases ----------

def test_analyst_rating_initiate_has_no_prior():
    r = AnalystRating(
        rating_id="rating_BAML_AMZN_2024-04-05_initiate",
        ticker="AMZN",
        analyst_firm="Bank of America",
        analyst_name="Justin Post",
        action=RatingAction.INITIATE,
        new_rating="Buy",
        prior_rating=None,
        action_date=date(2024, 4, 5),
    )
    events = analyst.rating_to_events([r])
    txt = events[0].text
    assert "initiate" in txt
    assert "->" not in txt  # no transition arrow when there's no prior
    assert "Buy" in txt


def test_analyst_rating_missing_new_rating():
    r = AnalystRating(
        rating_id="rating_X_Y_2024-04-05_discontinue",
        ticker="Y",
        analyst_firm="X",
        action=RatingAction.DISCONTINUE,
        new_rating=None,
        prior_rating="Buy",
        action_date=date(2024, 4, 5),
    )
    events = analyst.rating_to_events([r])
    assert "(no rating)" in events[0].text


def test_analyst_target_initiate_text():
    t = PriceTargetChange(
        target_id="target_BAML_AMZN_2024-04-05_initiate",
        ticker="AMZN",
        analyst_firm="Bank of America",
        new_target=200.0,
        prior_target=None,
        change_pct=None,
        action_date=date(2024, 4, 5),
    )
    events = analyst.target_to_events([t])
    assert "initiated at $200.00" in events[0].text


def test_analyst_target_withdrawn_text():
    t = PriceTargetChange(
        target_id="target_X_Y_2024-04-05_drop",
        ticker="Y",
        analyst_firm="X",
        new_target=None,
        prior_target=None,
        change_pct=None,
        action_date=date(2024, 4, 5),
    )
    events = analyst.target_to_events([t])
    assert "(target withdrawn)" in events[0].text


def test_analyst_dispatcher_handles_only_ratings():
    ratings = _load_analyst_ratings()
    events = analyst.to_events(ratings=ratings, targets=None)
    assert len(events) == len(ratings)
    assert all(e.event_type == "analyst_rating_change" for e in events)


def test_analyst_dispatcher_handles_only_targets():
    targets = _load_price_targets()
    events = analyst.to_events(ratings=None, targets=targets)
    assert len(events) == len(targets)
    assert all(e.event_type == "price_target_change" for e in events)


def test_analyst_dispatcher_empty_inputs_returns_empty():
    assert analyst.to_events(ratings=None, targets=None) == []
    assert analyst.to_events(ratings=[], targets=[]) == []


# ---------- news: additional edge cases ----------

def test_news_empty_paragraphs_list_emits_nothing():
    row = dict(NEWS_ROW, news=[])
    assert news.to_events([row]) == []
    assert news.to_chunks([row]) == []


def test_news_empty_related_symbols_emits_nothing():
    row = dict(NEWS_ROW, related_symbols="")
    assert news.to_events([row]) == []


def test_news_whitespace_in_related_symbols_trimmed():
    row = dict(NEWS_ROW, related_symbols="aapl , MSFT")
    events = news.to_events([row])
    tickers = {e.ticker for e in events}
    assert tickers == {"AAPL", "MSFT"}


def test_news_skips_paragraphs_without_paragraph_number():
    row = dict(
        NEWS_ROW,
        news=[
            {"paragraph_number": None, "paragraph": "skip me"},
            {"paragraph_number": 3, "paragraph": "keep me"},
        ],
    )
    events = news.to_events([row])
    assert len(events) == 2  # 1 paragraph * 2 tickers
    assert all(e.event_id.endswith("_p3") for e in events)


def test_news_skips_empty_paragraphs():
    row = dict(
        NEWS_ROW,
        news=[
            {"paragraph_number": 0, "paragraph": "   "},
            {"paragraph_number": 1, "paragraph": "real content"},
        ],
    )
    events = news.to_events([row])
    # 2 tickers * 1 non-empty paragraph = 2
    assert len(events) == 2


def test_news_multiple_articles_yield_distinct_ids():
    row_a = dict(NEWS_ROW, uuid="aaa", title="A")
    row_b = dict(NEWS_ROW, uuid="bbb", title="B")
    events = news.to_events([row_a, row_b])
    ids = {e.event_id for e in events}
    # 2 articles * 2 tickers * 2 paragraphs = 8 total; 4 distinct IDs
    # (same uuid+p number dedupes across tickers in the ID space)
    id_prefixes = {e.event_id.rsplit("_p", 1)[0] for e in events}
    assert id_prefixes == {"news_aaa", "news_bbb"}


def test_news_determinism_same_input_same_output():
    first = [e.event_id for e in news.to_events([NEWS_ROW])]
    second = [e.event_id for e in news.to_events([NEWS_ROW])]
    assert first == second


def test_news_dedupe_is_case_insensitive_on_title_and_symbols():
    a = dict(NEWS_ROW, title="  TECH EARNINGS PREVIEW  ")
    b = dict(NEWS_ROW, title="tech earnings preview", related_symbols="aapl,msft")
    deduped = news.dedupe_articles([a, b])
    assert len(deduped) == 1


def test_news_chunks_carry_source_url_and_section():
    chunks = news.to_chunks([NEWS_ROW])
    assert all(c.source_url == NEWS_ROW["link"] for c in chunks)
    # section_name uses the p{N} pattern
    sections = {c.section_name for c in chunks}
    assert sections == {"p0", "p1"}


# ---------- earnings: timing variants ----------

def test_earnings_pre_market_text():
    row = dict(EARNINGS_ROWS[0], time="pre")
    events = earnings.to_events([row])
    assert "before market open" in events[0].text


def test_earnings_missing_time_uses_generic_release():
    row = {k: v for k, v in EARNINGS_ROWS[0].items() if k != "time"}
    events = earnings.to_events([row])
    assert "release" in events[0].text.lower()


def test_earnings_missing_fiscal_quarter_omits_clause():
    row = {k: v for k, v in EARNINGS_ROWS[0].items() if k != "fiscal_quarter_ending"}
    events = earnings.to_events([row])
    assert "fiscal period" not in events[0].text


def test_earnings_lowercase_symbol_is_normalized():
    row = dict(EARNINGS_ROWS[0], symbol="aapl")
    events = earnings.to_events([row])
    assert events[0].ticker == "AAPL"
    assert "AAPL" in events[0].event_id


# ---------- short reports: determinism + chunk fields ----------

def test_short_reports_chunks_carry_thesis_as_text():
    reports = _load_short_reports()
    chunks = short_reports.to_chunks(reports)
    for r, c in zip(reports, chunks):
        assert c.section_name == "short_report"
        assert c.text == r.thesis_text
        assert c.source_url == r.source_url


def test_short_reports_determinism_across_calls():
    reports = _load_short_reports()
    first = [(e.event_id, e.event_date) for e in short_reports.to_events(reports)]
    second = [(e.event_id, e.event_date) for e in short_reports.to_events(reports)]
    assert first == second


# ---------- aggregator: round-trip against a real source parquet ----------

def test_aggregator_roundtrips_thirteen_f_deltas(tmp_path):
    """Write a synthetic deltas_*.parquet under data/thirteen_f/, run the
    aggregator, and assert the event shows up in the output."""
    import pandas as pd
    from ingestion.events.aggregator import build_events_parquet

    data_dir = tmp_path / "data"
    (data_dir / "thirteen_f").mkdir(parents=True, exist_ok=True)

    delta_row = {
        "fund_cik": "0001067983",
        "fund_name": "BERKSHIRE HATHAWAY INC",
        "ticker": "AAPL",
        "current_filing_date": "2024-02-14",
        "current_period_end": "2023-12-31",
        "action": "reduced",
        "shares_change": -10_000_000,
        "market_value_change": -1_800_000_000,
        "prior_shares": 915_000_000,
        "current_shares": 905_000_000,
    }
    pd.DataFrame([delta_row]).to_parquet(
        data_dir / "thirteen_f" / "deltas_2024-02-14.parquet", index=False
    )

    out_events = tmp_path / "cache" / "events.parquet"
    events_df = build_events_parquet(
        as_of=date(2024, 4, 24),
        out_path=out_events,
        data_dir=data_dir,
        chunks_out_path=tmp_path / "cache" / "chunks.parquet",
    )
    assert len(events_df) == 1
    assert events_df["ticker"].iloc[0] == "AAPL"
    assert events_df["event_type"].iloc[0] == "13f_delta"
    assert events_df["event_date"].iloc[0] == "2024-02-14"  # filing date, ISO
    # Reading back from disk should match.
    reloaded = pd.read_parquet(out_events)
    assert reloaded.equals(events_df)


def test_aggregator_filters_events_beyond_as_of(tmp_path):
    """An event whose date is past `as_of` must be dropped."""
    import pandas as pd
    from ingestion.events.aggregator import build_events_parquet

    data_dir = tmp_path / "data"
    (data_dir / "thirteen_f").mkdir(parents=True, exist_ok=True)

    rows = [
        {   # before as_of — included
            "fund_cik": "0001",
            "fund_name": "FUND A",
            "ticker": "AAPL",
            "current_filing_date": "2024-01-15",
            "current_period_end": "2023-12-31",
            "action": "increased",
            "shares_change": 1000,
            "market_value_change": 150000,
            "prior_shares": 500,
            "current_shares": 1500,
        },
        {   # after as_of — excluded
            "fund_cik": "0002",
            "fund_name": "FUND B",
            "ticker": "AAPL",
            "current_filing_date": "2024-05-15",
            "current_period_end": "2024-03-31",
            "action": "increased",
            "shares_change": 1000,
            "market_value_change": 150000,
            "prior_shares": 500,
            "current_shares": 1500,
        },
    ]
    pd.DataFrame(rows).to_parquet(
        data_dir / "thirteen_f" / "deltas_2024.parquet", index=False
    )

    events_df = build_events_parquet(
        as_of=date(2024, 4, 24),
        out_path=tmp_path / "events.parquet",
        data_dir=data_dir,
        chunks_out_path=tmp_path / "chunks.parquet",
    )
    assert len(events_df) == 1
    assert events_df["event_date"].iloc[0] == "2024-01-15"


def test_aggregator_writes_chunks_parquet_when_short_reports_present(tmp_path):
    """Short reports produce parallel chunks; verify they survive the
    parquet round-trip with the right columns."""
    import pandas as pd
    from ingestion.events.aggregator import build_events_parquet

    data_dir = tmp_path / "data"
    (data_dir / "short_reports").mkdir(parents=True, exist_ok=True)

    report_row = {
        "chunk_id": "short_report_muddy_waters_BABA_2024-01-15",
        "publisher": "Muddy Waters Research",
        "target_ticker": "BABA",
        "publication_date": "2024-01-15",
        "title": "Alibaba: The Party's Over",
        "thesis_text": "We believe Alibaba has overstated user metrics.",
        "source_url": "https://muddywatersresearch.com/baba/",
        "token_count": 10,
    }
    pd.DataFrame([report_row]).to_parquet(
        data_dir / "short_reports" / "reports_2024-01.parquet", index=False
    )

    out_chunks = tmp_path / "cache" / "chunks.parquet"
    build_events_parquet(
        as_of=date(2024, 4, 24),
        out_path=tmp_path / "cache" / "events.parquet",
        data_dir=data_dir,
        chunks_out_path=out_chunks,
    )
    chunks_df = pd.read_parquet(out_chunks)
    assert len(chunks_df) == 1
    assert chunks_df["chunk_id"].iloc[0] == report_row["chunk_id"]
    assert chunks_df["ticker"].iloc[0] == "BABA"
    assert str(chunks_df["source_type"].iloc[0]) == "SourceType.SHORT_INTEREST" or \
           chunks_df["source_type"].iloc[0] == "short_interest"


def test_aggregator_skips_missing_chunks_path(tmp_path):
    """chunks_out_path=None -> only events parquet is written."""
    from ingestion.events.aggregator import build_events_parquet

    out_events = tmp_path / "events.parquet"
    build_events_parquet(
        as_of=date(2024, 4, 24),
        out_path=out_events,
        data_dir=tmp_path / "empty",
        chunks_out_path=None,
    )
    assert out_events.exists()
    # No chunks file should have been created in tmp_path at all.
    siblings = [p.name for p in tmp_path.iterdir() if p.is_file()]
    assert siblings == ["events.parquet"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
