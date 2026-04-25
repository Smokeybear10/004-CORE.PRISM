"""
Alternative fade-or-follow frameworks (mentor expansion ask).

Every framework answers the same question — "will this move persist or
revert?" — but uses a different signal:

    fundamental_vs_nonfundamental  (existing, in signal.py)
        Uses move_character. structural -> lean, transient -> fade.

    expected_vs_realized           (this module)
        Compares predicted_return_pct against return_pct.
        realized >> predicted -> overreaction -> fade.
        realized << predicted -> underreaction -> lean (still room).
        realized ~= predicted  -> already priced -> neutral.

    dimension_weighted             (this module)
        Each of the 5 attribution dimensions has a "persistence" coefficient
        in [-1, +1]. dim.weight * persistence summed across dims gives a
        score; positive -> lean, negative -> fade.

    hybrid                         (this module)
        Layered combination — fundamentality gate, then expected-vs-realized,
        then dimension-sanity downweight. The "additive demo" layout the
        mentor asked for.

All strategies have signature `(attribution: Attribution) -> FadeFollow` so
they drop into `STRATEGY_REGISTRY` and are usable through
`attribution_to_trade(strategy=...)` and `backtest.runner --strategy ...`.
"""

from __future__ import annotations

from typing import Optional

from schema import Attribution, FadeFollow


# Persistence coefficient per attribution dimension. +1.0 = strongly lean
# (this driver historically persists), -1.0 = strongly fade (reverts), 0.0
# = mixed / no prior. Defaults below are the mentor's own framing in the
# expansion brief. Override via `persistence=` on the strategy call.
DEFAULT_DIMENSION_PERSISTENCE: dict[str, float] = {
    "demand": +0.80,
    "pricing": +0.60,
    "competitive": +0.40,
    "management_credibility": 0.0,
    "macro": -0.70,
}


def dominant_dimension(attribution: Attribution) -> Optional[str]:
    """Return the name of the highest-weighted dimension, or None when no
    dimensions are populated."""
    dims = {
        name: getattr(attribution, name).weight
        for name in DEFAULT_DIMENSION_PERSISTENCE
    }
    if not dims:
        return None
    return max(dims, key=dims.get)


# ---------- Option 2: expected vs realized ----------

def strategy_expected_vs_realized(
    attribution: Attribution,
    *,
    overshoot_factor: float = 1.5,
    undershoot_factor: float = 0.5,
) -> FadeFollow:
    """
    Compare the model's predicted return against the realized return.

    overshoot   |realized| > overshoot_factor * |predicted|  -> fade
    undershoot  |realized| < undershoot_factor * |predicted| -> lean
    aligned     within the band                              -> neutral

    Returns "neutral" when:
      - predicted_return_pct is None,
      - predicted is exactly 0 (no baseline to compare against),
      - or predicted and realized have opposite signs (the move went the
        opposite way from the news; calling fade-or-lean here would be
        guessing — leave it to another framework).
    """
    pred = attribution.predicted_return_pct
    real = attribution.return_pct
    if pred is None or pred == 0.0:
        return "neutral"
    if pred * real < 0:
        return "neutral"
    ratio = abs(real) / abs(pred)
    if ratio >= overshoot_factor:
        return "fade"
    if ratio <= undershoot_factor:
        return "lean"
    return "neutral"


# ---------- Option 3: dimension-weighted ----------

def strategy_dimension_weighted(
    attribution: Attribution,
    *,
    persistence: dict[str, float] = DEFAULT_DIMENSION_PERSISTENCE,
    lean_threshold: float = 0.20,
    fade_threshold: float = -0.20,
) -> FadeFollow:
    """
    Weighted sum across dimensions: sum(dim.weight * persistence[dim]).

    A move dominated by demand (high persistence) -> lean.
    A move dominated by macro (negative persistence) -> fade.
    Mixed or low-signal -> neutral.

    `persistence` defaults to DEFAULT_DIMENSION_PERSISTENCE; pass a custom
    map to swap in your own historical priors.
    """
    score = 0.0
    for name, coeff in persistence.items():
        dim = getattr(attribution, name, None)
        if dim is None:
            continue
        score += dim.weight * coeff
    if score >= lean_threshold:
        return "lean"
    if score <= fade_threshold:
        return "fade"
    return "neutral"


# ---------- Option 6: hybrid (layered) ----------

def strategy_hybrid(
    attribution: Attribution,
    *,
    overshoot_factor: float = 1.5,
    persistence: dict[str, float] = DEFAULT_DIMENSION_PERSISTENCE,
) -> FadeFollow:
    """
    Layered decision per the mentor's recommended stack:

        Layer 2 (fundamentality gate):
            move_character == "transient"           -> fade
            move_character in ("mixed","unclear")   -> neutral

        Layer 3 (expected vs realized, only when structural):
            same-sign realized > overshoot_factor * predicted -> fade
            otherwise (aligned or undershoot)                 -> tentative lean

        Layer 4 (dimension sanity):
            if dominant dimension's persistence < 0, downgrade lean -> neutral.

    This is the "stack the bars" demo path: each layer is something you can
    turn off to show its incremental contribution.
    """
    char = attribution.move_character
    if char == "transient":
        return "fade"
    if char in ("mixed", "unclear"):
        return "neutral"

    # Structural beyond here.
    pred = attribution.predicted_return_pct
    real = attribution.return_pct
    if pred is not None and pred != 0.0 and pred * real > 0:
        if abs(real) >= overshoot_factor * abs(pred):
            return "fade"

    # Dimension sanity: don't lean into a dimension whose history reverts.
    dom = dominant_dimension(attribution)
    if dom is not None and persistence.get(dom, 0.0) < 0:
        return "neutral"

    return "lean"


# ---------- Registry-ready exports ----------

# Names match the keys we'll add to backtest.signal.STRATEGY_REGISTRY so
# `backtest.runner --strategy <name>` and `attribution_to_trade(strategy=<name>)`
# both resolve.
FRAMEWORK_STRATEGIES = {
    "expected_vs_realized": strategy_expected_vs_realized,
    "dimension_weighted": strategy_dimension_weighted,
    "hybrid": strategy_hybrid,
}
