"""
Attribution → Trade.

Implements the mentor-suggested Fundamental-vs-Non-fundamental framework:

    move_character = "structural" (fundamental) → lean  (follow the move)
    move_character = "transient"  (non-fund.)   → fade  (opposite direction)
    move_character = "mixed" / "unclear"        → neutral (no trade)

Future: swap in other frameworks by subclassing `Strategy` and overriding
`attribution_to_direction()`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np

from schema import Attribution, FadeFollow


@dataclass
class Trade:
    """One directional bet on a single event. Dates are trading-day indices
    into the price panel — the pnl module resolves them."""
    event_id: str
    ticker: str
    action: FadeFollow            # "lean", "fade", "neutral"
    direction: int                # +1 long, -1 short, 0 no trade
    size: float                   # unit position size; start constant, refine later
    entry_date: date              # = reaction_end (we enter AFTER the reaction)
    exit_horizon_days: int        # trading days held
    confidence: float             # passthrough from Attribution
    source: str                   # strategy name for bookkeeping


# ── Strategies ──────────────────────────────────────────────────────────────

def strategy_fundamental_vs_nonfundamental(attr: Attribution) -> FadeFollow:
    """The user's chosen framework (primary for now)."""
    if attr.move_character == "structural":
        return "lean"
    if attr.move_character == "transient":
        return "fade"
    return "neutral"


# Future frameworks can be added here and selected via runner --strategy flag.
STRATEGY_REGISTRY = {
    "fundamental_vs_nonfundamental": strategy_fundamental_vs_nonfundamental,
}


# ── Trade construction ─────────────────────────────────────────────────────

def attribution_to_trade(
    attr: Attribution,
    event_id: str,
    reaction_return: float,
    exit_horizon_days: int = 5,
    size: float = 1.0,
    strategy: str = "fundamental_vs_nonfundamental",
) -> Trade:
    """
    Map a single Attribution to a Trade.

    The direction depends on BOTH the strategy call (lean/fade/neutral) AND
    the sign of the reaction:
        lean    → follow the move    → direction = sign(reaction)
        fade    → against the move   → direction = -sign(reaction)
        neutral → no trade           → direction = 0
    """
    action = STRATEGY_REGISTRY[strategy](attr)
    move_sign = int(np.sign(reaction_return)) if reaction_return != 0 else 0

    if action == "lean":
        direction = move_sign
    elif action == "fade":
        direction = -move_sign
    else:
        direction = 0

    return Trade(
        event_id=event_id,
        ticker=attr.ticker,
        action=action,
        direction=direction,
        size=float(size),
        entry_date=attr.move_date,
        exit_horizon_days=exit_horizon_days,
        confidence=attr.confidence,
        source=strategy,
    )
