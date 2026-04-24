"""
The four mandated baselines. The plan says the structured signal must beat
all of them; our job here is to produce apples-to-apples BacktestResults.

Baselines:
  1. always_lean   — follow every significant move (direction = sign(reaction))
  2. always_fade   — fade every big move        (direction = -sign(reaction)
                                                 if |reaction| ≥ threshold else 0)
  3. random_attr   — shuffle the model's structural/transient labels across
                     events, then run the same signal logic → captures what
                     happens when attribution is noise
  4. sentiment     — one scalar per event (placeholder = sign of a random
                     sentiment score) → captures what you get from a blunt
                     polarity classifier without structured dimensions
"""
from __future__ import annotations

import random
from typing import List

import pandas as pd

from schema import Attribution
from backtest.signal import Trade, attribution_to_trade
from backtest.fixtures import generate_attribution

FADE_THRESHOLD = 0.05  # |reaction| ≥ 5% = "big move"


# ── 1. Always-lean ─────────────────────────────────────────────────────────
def baseline_always_lean(events_df: pd.DataFrame, horizon: int = 5) -> list[Trade]:
    trades = []
    for row in events_df.itertuples(index=False):
        sign = 1 if row.reaction_return > 0 else (-1 if row.reaction_return < 0 else 0)
        trades.append(Trade(
            event_id=row.event_id, ticker=row.ticker,
            action="lean", direction=sign, size=1.0,
            entry_date=row.reaction_end.date() if hasattr(row.reaction_end, "date") else row.reaction_end,
            exit_horizon_days=horizon, confidence=1.0,
            source="baseline_always_lean",
        ))
    return trades


# ── 2. Always-fade-big-moves ───────────────────────────────────────────────
def baseline_always_fade(events_df: pd.DataFrame, horizon: int = 5,
                         threshold: float = FADE_THRESHOLD) -> list[Trade]:
    trades = []
    for row in events_df.itertuples(index=False):
        if abs(row.reaction_return) < threshold:
            direction = 0
        else:
            direction = -1 if row.reaction_return > 0 else 1
        trades.append(Trade(
            event_id=row.event_id, ticker=row.ticker,
            action="fade" if direction != 0 else "neutral",
            direction=direction, size=1.0,
            entry_date=row.reaction_end.date() if hasattr(row.reaction_end, "date") else row.reaction_end,
            exit_horizon_days=horizon, confidence=1.0,
            source="baseline_always_fade",
        ))
    return trades


# ── 3. Random attribution ──────────────────────────────────────────────────
def baseline_random_attribution(events_df: pd.DataFrame, horizon: int = 5,
                                seed: int = 42) -> list[Trade]:
    """
    For each event, assign a random move_character, then run the same
    fundamental-vs-nonfundamental signal logic. This measures how much of
    the strategy's edge comes from the attribution vs. from structure in
    the event data itself.
    """
    rng = random.Random(seed)
    choices = ["structural", "transient", "mixed", "unclear"]
    trades = []
    for row in events_df.itertuples(index=False):
        # Build a minimal, shuffled Attribution
        from backtest.fixtures import _dim
        mc = rng.choice(choices)
        attr = Attribution(
            ticker=row.ticker,
            move_date=row.reaction_end.date() if hasattr(row.reaction_end, "date") else row.reaction_end,
            return_pct=float(row.reaction_return),
            demand=_dim(0.2, 0, "random"),
            pricing=_dim(0.2, 0, "random"),
            competitive=_dim(0.2, 0, "random"),
            management_credibility=_dim(0.2, 0, "random"),
            macro=_dim(0.2, 0, "random"),
            move_character=mc, confidence=0.5,
            ablation_name="random", sources_used=[],
            chunks_considered=0,
            model_notes="random-attribution baseline",
        )
        trades.append(attribution_to_trade(
            attr, event_id=row.event_id,
            reaction_return=float(row.reaction_return),
            exit_horizon_days=horizon,
        ))
        # tag source for the BacktestResult
        trades[-1].source = "baseline_random_attribution"
    return trades


# ── 4. Sentiment-only ──────────────────────────────────────────────────────
def baseline_sentiment_only(events_df: pd.DataFrame, horizon: int = 5,
                            seed: int = 7) -> list[Trade]:
    """
    Replace the 5-dimension attribution with a single polarity scalar.
    Placeholder: random sentiment in [-1, 1]. If positive → lean, negative →
    fade, near zero → neutral. When the text pipeline is wired up, swap the
    random score for a real sentiment classifier run on the same text.
    """
    rng = random.Random(seed)
    trades = []
    for row in events_df.itertuples(index=False):
        sentiment = rng.uniform(-1, 1)
        move_sign = 1 if row.reaction_return > 0 else (-1 if row.reaction_return < 0 else 0)
        if sentiment > 0.33:
            action, direction = "lean", move_sign
        elif sentiment < -0.33:
            action, direction = "fade", -move_sign
        else:
            action, direction = "neutral", 0
        trades.append(Trade(
            event_id=row.event_id, ticker=row.ticker,
            action=action, direction=direction, size=1.0,
            entry_date=row.reaction_end.date() if hasattr(row.reaction_end, "date") else row.reaction_end,
            exit_horizon_days=horizon, confidence=abs(sentiment),
            source="baseline_sentiment_only",
        ))
    return trades


BASELINES = {
    "always_lean":        baseline_always_lean,
    "always_fade":        baseline_always_fade,
    "random_attribution": baseline_random_attribution,
    "sentiment_only":     baseline_sentiment_only,
}
