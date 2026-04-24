"""
Tests for `ingestion.idiosyncratic.analyst_actions`.

yfinance is monkeypatched via the `ticker_factory` kwarg: every test builds
a DataFrame in the shape yfinance returns, wraps it in a fake Ticker, and
passes the factory through.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ingestion.idiosyncratic import analyst_actions as aa
from schema import (
    AnalystRating,
    PriceTargetChange,
    RatingAction,
    SourceType,
    TextChunk,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "idiosyncratic"


# ---------- fixtures conform to schema ----------


def test_analyst_ratings_fixture_parses():
    raw = json.loads((FIXTURE_DIR / "analyst_ratings_sample.json").read_text())
    for row in raw:
        AnalystRating(**row)


def test_price_targets_fixture_parses():
    raw = json.loads((FIXTURE_DIR / "price_targets_sample.json").read_text())
    for row in raw:
        PriceTargetChange(**row)


# ---------- normalize_rating ----------


@pytest.mark.parametrize("raw,expected", [
    ("Buy", "Buy"),
    ("STRONG BUY", "Buy"),
    ("Outperform", "Buy"),
    ("Overweight", "Buy"),
    ("Neutral", "Hold"),
    ("Equal Weight", "Hold"),
    ("Market Perform", "Hold"),
    ("Sell", "Sell"),
    ("Underperform", "Sell"),
    ("Underweight", "Sell"),
])
def test_normalize_rating_covers_common_firm_scales(raw, expected):
    assert aa.normalize_rating(raw) == expected


def test_normalize_rating_unknown_passes_through():
    assert aa.normalize_rating("Tactical Add") == "Tactical Add"


def test_normalize_rating_none_returns_none():
    assert aa.normalize_rating(None) is None
    assert aa.normalize_rating("") is None


# ---------- classify_action ----------


def test_classify_action_initiate_when_prior_missing():
    assert aa.classify_action(None, "Buy") == RatingAction.INITIATE
    assert aa.classify_action("", "Overweight") == RatingAction.INITIATE


def test_classify_action_upgrade_buy_from_hold():
    assert aa.classify_action("Neutral", "Overweight") == RatingAction.UPGRADE
    assert aa.classify_action("Hold", "Buy") == RatingAction.UPGRADE
    # Sell → Hold is an upgrade
    assert aa.classify_action("Underperform", "Neutral") == RatingAction.UPGRADE


def test_classify_action_downgrade_sell_from_buy():
    assert aa.classify_action("Buy", "Hold") == RatingAction.DOWNGRADE
    assert aa.classify_action("Overweight", "Underperform") == RatingAction.DOWNGRADE


def test_classify_action_reiterate_same_bucket():
    assert aa.classify_action("Buy", "Buy") == RatingAction.REITERATE
    assert aa.classify_action("Overweight", "Buy") == RatingAction.REITERATE


def test_classify_action_uses_yf_hint_for_unknown_buckets():
    # Unknown ratings on both sides → fall through to yfinance's hint
    assert aa.classify_action("Special Buy", "Top Pick", "up") == RatingAction.UPGRADE
    assert aa.classify_action("Special Buy", "Top Pick", "down") == RatingAction.DOWNGRADE


def test_classify_action_unresolvable_logs_warning_and_defaults_reiterate(caplog):
    """When both bucket lookup and yf hint fail on differing strings, warn."""
    with caplog.at_level("WARNING", logger="ingestion.idiosyncratic.analyst_actions"):
        result = aa.classify_action("Tactical Add", "Regional Pick")
    assert result == RatingAction.REITERATE
    assert any("unclassifiable rating change" in rec.message for rec in caplog.records)


def test_classify_action_unresolvable_matching_strings_stays_silent(caplog):
    """Same raw string on both sides is a legit REITERATE — no warning."""
    with caplog.at_level("WARNING", logger="ingestion.idiosyncratic.analyst_actions"):
        result = aa.classify_action("Tactical Add", "Tactical Add")
    assert result == RatingAction.REITERATE
    assert not any("unclassifiable" in rec.message for rec in caplog.records)


# ---------- rating + target ID generation ----------


def test_generate_rating_id_is_deterministic():
    a = aa.generate_rating_id("AAPL", "JPMorgan Chase & Co", date(2024, 1, 15),
                              RatingAction.UPGRADE)
    b = aa.generate_rating_id("aapl", "JPMorgan Chase & Co", date(2024, 1, 15),
                              RatingAction.UPGRADE)
    assert a == b == "rating_jpmorgan_chase_co_AAPL_2024-01-15_upgrade"


def test_generate_target_id_direction_reflects_change():
    raise_id = aa.generate_target_id("NVDA", "GS", date(2024, 2, 28), 800.0, 600.0)
    lower_id = aa.generate_target_id("TSLA", "MS", date(2024, 3, 10), 180.0, 250.0)
    init_id = aa.generate_target_id("AMZN", "BAML", date(2024, 4, 5), 200.0, None)
    maint_id = aa.generate_target_id("NVDA", "GS", date(2024, 2, 28), 600.0, 600.0)
    assert raise_id.endswith("_raise")
    assert lower_id.endswith("_lower")
    assert init_id.endswith("_initiate")
    assert maint_id.endswith("_maintain")


# ---------- yfinance → AnalystRating + PriceTargetChange ----------


def _yf_frame(rows: list[dict]) -> pd.DataFrame:
    """Build a DataFrame in yfinance.upgrades_downgrades shape."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.set_index("GradeDate")
    df.index = pd.to_datetime(df.index)
    df.index.name = "GradeDate"
    return df


class _FakeTicker:
    def __init__(self, df):
        self.upgrades_downgrades = df


def _factory(df):
    return lambda ticker: _FakeTicker(df)


def test_fetch_produces_analyst_rating_and_price_target_from_same_row():
    df = _yf_frame([{
        "GradeDate": "2024-01-15 10:30:00",
        "Firm": "JPMorgan Chase & Co",
        "ToGrade": "Overweight",
        "FromGrade": "Neutral",
        "Action": "up",
        "priceTargetAction": "Raises",
        "currentPriceTarget": 225.0,
        "priorPriceTarget": 200.0,
    }])
    ratings, targets = aa.fetch_all_analyst_actions(
        "AAPL", as_of=date(2024, 12, 31), ticker_factory=_factory(df),
    )
    assert len(ratings) == 1 and len(targets) == 1
    r = ratings[0]
    assert r.ticker == "AAPL"
    assert r.action == RatingAction.UPGRADE  # Neutral → Overweight
    assert r.new_rating == "Overweight"
    assert r.prior_rating == "Neutral"
    assert r.action_date == date(2024, 1, 15)
    t = targets[0]
    assert t.new_target == 225.0 and t.prior_target == 200.0
    assert t.change_pct == pytest.approx(0.125)


def test_missing_price_target_omits_price_target_record():
    """currentPriceTarget=0 means yfinance doesn't have it. AnalystRating still emitted."""
    df = _yf_frame([{
        "GradeDate": "2024-02-01",
        "Firm": "Benchmark",
        "ToGrade": "Buy",
        "FromGrade": "Buy",
        "Action": "reit",
        "priceTargetAction": "",
        "currentPriceTarget": 0.0,
        "priorPriceTarget": 0.0,
    }])
    ratings, targets = aa.fetch_all_analyst_actions(
        "NVDA", as_of=date(2024, 12, 31), ticker_factory=_factory(df),
    )
    assert len(ratings) == 1 and targets == []
    assert ratings[0].action == RatingAction.REITERATE


def test_as_of_filter_excludes_future_rows():
    df = _yf_frame([
        {"GradeDate": "2024-01-15", "Firm": "JPM", "ToGrade": "Buy",
         "FromGrade": "Hold", "Action": "up", "priceTargetAction": "Raises",
         "currentPriceTarget": 100, "priorPriceTarget": 80},
        {"GradeDate": "2025-03-01", "Firm": "JPM", "ToGrade": "Buy",
         "FromGrade": "Buy", "Action": "reit", "priceTargetAction": "Maintains",
         "currentPriceTarget": 110, "priorPriceTarget": 100},
    ])
    ratings, targets = aa.fetch_all_analyst_actions(
        "AAPL", as_of=date(2024, 12, 31), ticker_factory=_factory(df),
    )
    assert [r.action_date for r in ratings] == [date(2024, 1, 15)]
    assert [t.action_date for t in targets] == [date(2024, 1, 15)]


def test_initiate_when_from_grade_empty():
    df = _yf_frame([{
        "GradeDate": "2024-04-05",
        "Firm": "Bank of America Merrill Lynch",
        "ToGrade": "Buy",
        "FromGrade": "",  # initiate
        "Action": "init",
        "priceTargetAction": "Announces",
        "currentPriceTarget": 200.0,
        "priorPriceTarget": 0.0,
    }])
    ratings, targets = aa.fetch_all_analyst_actions(
        "AMZN", as_of=date(2024, 12, 31), ticker_factory=_factory(df),
    )
    assert len(ratings) == 1 and ratings[0].action == RatingAction.INITIATE
    assert ratings[0].prior_rating is None
    assert len(targets) == 1
    assert targets[0].prior_target is None
    assert targets[0].change_pct is None


def test_empty_yfinance_result_returns_empty_lists():
    ratings, targets = aa.fetch_all_analyst_actions(
        "XYZ", as_of=date(2024, 12, 31), ticker_factory=_factory(pd.DataFrame()),
    )
    assert ratings == [] and targets == []


def test_yfinance_exception_returns_empty(monkeypatch):
    def _bad_factory(ticker):
        raise RuntimeError("network down")
    ratings, targets = aa.fetch_all_analyst_actions(
        "XYZ", as_of=date(2024, 12, 31), ticker_factory=_bad_factory,
    )
    assert ratings == [] and targets == []


# ---------- Event + TextChunk + pipeline ----------


def test_ratings_to_events_and_chunks():
    r = AnalystRating(
        rating_id="rating_jpm_AAPL_2024-01-15_upgrade",
        ticker="AAPL", analyst_firm="JPM", analyst_name=None,
        action=RatingAction.UPGRADE, new_rating="Buy", prior_rating="Hold",
        action_date=date(2024, 1, 15), source_url=None,
    )
    events = aa.ratings_to_events([r])
    assert events[0].event_type == "analyst_upgrade"
    assert events[0].source == "JPM"
    chunks = aa.events_to_text_chunks(events)
    assert chunks[0].source_type == SourceType.NEWS
    assert chunks[0].publication_date == date(2024, 1, 15)


def test_targets_to_events():
    t = PriceTargetChange(
        target_id="target_gs_NVDA_2024-02-28_raise",
        ticker="NVDA", analyst_firm="Goldman Sachs",
        new_target=800.0, prior_target=625.0, change_pct=0.28,
        action_date=date(2024, 2, 28), source_url=None,
    )
    events = aa.targets_to_events([t])
    assert events[0].event_type == "analyst_price_target"
    assert events[0].ticker == "NVDA"


def test_pipeline_writes_parquet_and_jsonl(tmp_path):
    df = _yf_frame([{
        "GradeDate": "2024-01-15",
        "Firm": "JPMorgan Chase & Co",
        "ToGrade": "Overweight",
        "FromGrade": "Neutral",
        "Action": "up",
        "priceTargetAction": "Raises",
        "currentPriceTarget": 225.0,
        "priorPriceTarget": 200.0,
    }])
    ratings, targets, events, chunks = aa.run_analyst_actions_pipeline(
        ticker="AAPL", as_of=date(2024, 12, 31),
        output_dir=tmp_path, ticker_factory=_factory(df),
    )
    assert len(ratings) == len(targets) == 1
    assert len(events) == 2  # one rating event + one target event

    ratings_path = tmp_path / "ratings_AAPL_2024-12-31.parquet"
    targets_path = tmp_path / "targets_AAPL_2024-12-31.parquet"
    events_path = tmp_path / "events_AAPL_2024-12-31.parquet"
    chunks_path = tmp_path / "chunks_AAPL_2024-12-31.jsonl"
    assert all(p.exists() for p in (ratings_path, targets_path, events_path, chunks_path))

    rt = pd.read_parquet(ratings_path)
    assert {"rating_id", "ticker", "analyst_firm", "action",
            "new_rating", "prior_rating", "action_date"}.issubset(rt.columns)

    for line in chunks_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        TextChunk.model_validate_json(line)
