"""
Tests for backtest.signal: Attribution -> Trade mapping and the
fundamental-vs-nonfundamental strategy.
"""
from __future__ import annotations

from datetime import date

import pytest

from schema import Attribution, DimensionScore, FadeFollow
from backtest.signal import (
    STRATEGY_REGISTRY,
    Trade,
    attribution_to_trade,
    strategy_fundamental_vs_nonfundamental,
)


def _dim(w: float = 0.2, direction: str = "neutral") -> DimensionScore:
    return DimensionScore(
        weight=w, direction=direction, rationale="test",
        evidence_chunk_ids=["chunk_0"],
    )


def _attr(move_character: str = "structural", return_pct: float = 0.05) -> Attribution:
    return Attribution(
        ticker="AAPL", move_date=date(2024, 1, 5),
        return_pct=return_pct, predicted_return_pct=None,
        demand=_dim(), pricing=_dim(), competitive=_dim(),
        management_credibility=_dim(), macro=_dim(),
        move_character=move_character, confidence=0.7,
        ablation_name="base_news", sources_used=[], chunks_considered=5,
    )


# ---------- strategy_fundamental_vs_nonfundamental ----------

@pytest.mark.parametrize("character,expected", [
    ("structural", "lean"),
    ("transient", "fade"),
    ("mixed", "neutral"),
    ("unclear", "neutral"),
])
def test_strategy_maps_character_to_action(character, expected):
    assert strategy_fundamental_vs_nonfundamental(_attr(character)) == expected


def test_registry_contains_default_strategy():
    assert "fundamental_vs_nonfundamental" in STRATEGY_REGISTRY
    # Every registered strategy returns a valid FadeFollow literal.
    for strat in STRATEGY_REGISTRY.values():
        for char in ("structural", "transient", "mixed", "unclear"):
            result = strat(_attr(char))
            assert result in ("lean", "fade", "neutral")


# ---------- attribution_to_trade ----------

def test_lean_follows_move_sign_positive():
    trade = attribution_to_trade(_attr("structural"), "AAPL_20240105",
                                 reaction_return=0.08, exit_horizon_days=5)
    assert trade.action == "lean"
    assert trade.direction == 1      # positive reaction, lean -> long
    assert trade.exit_horizon_days == 5
    assert isinstance(trade, Trade)


def test_lean_follows_move_sign_negative():
    trade = attribution_to_trade(_attr("structural"), "AAPL_20240105",
                                 reaction_return=-0.08)
    assert trade.action == "lean"
    assert trade.direction == -1     # negative reaction, lean -> short


def test_fade_opposes_move_sign():
    trade = attribution_to_trade(_attr("transient"), "AAPL_20240105",
                                 reaction_return=0.08)
    assert trade.action == "fade"
    assert trade.direction == -1     # positive reaction, fade -> short


def test_fade_opposes_move_sign_negative():
    trade = attribution_to_trade(_attr("transient"), "AAPL_20240105",
                                 reaction_return=-0.08)
    assert trade.action == "fade"
    assert trade.direction == 1      # negative reaction, fade -> long


def test_neutral_is_zero_direction():
    trade = attribution_to_trade(_attr("mixed"), "AAPL_20240105",
                                 reaction_return=0.08)
    assert trade.action == "neutral"
    assert trade.direction == 0


def test_zero_reaction_returns_zero_direction():
    # No reaction to follow -> no trade even if the strategy says "lean"
    trade = attribution_to_trade(_attr("structural"), "AAPL_20240105",
                                 reaction_return=0.0)
    assert trade.direction == 0


def test_unknown_strategy_raises():
    with pytest.raises(KeyError):
        attribution_to_trade(_attr(), "AAPL_20240105",
                             reaction_return=0.05, strategy="not_a_strategy")
