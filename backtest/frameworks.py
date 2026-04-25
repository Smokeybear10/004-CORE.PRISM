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
        score; positive -> lean, negative -> fade. Priors are
        research-grounded (see RESEARCH_GROUNDED_PERSISTENCE below); the
        `calibrate_persistence` helper replaces priors with empirical
        regression coefficients once real attribution data exists.

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

import numpy as np
import pandas as pd

from schema import Attribution, FadeFollow


# Per-dimension persistence prior on the [-1, +1] scale. +1 = moves driven
# by this dimension persist over the 5-day horizon (lean is correct);
# -1 = revert (fade is correct); 0 = no signal.
#
# Values below are research-grounded — see RESEARCH_REFERENCES for the
# papers each rests on. They are PRIORS only; replace with the output of
# calibrate_persistence(...) once enough real attribution data exists in
# the focal universe.
RESEARCH_GROUNDED_PERSISTENCE: dict[str, float] = {
    # Cash-flow / unit fundamentals — strongest persistence in the literature.
    # Bernard & Thomas (1989, 1990) PEAD; Foster/Olsen/Shevlin (1984);
    # Engelberg/McLean/Pontiff (2018) "Anomalies and News".
    "demand": +0.85,
    # Pricing/margin — same PEAD logic but slightly diluted because price
    # action can also signal competitive pressure (mixed signal).
    # Tetlock/Saar-Tsechansky/Macskassy (2008) on fundamental-words ->
    # earnings predictability.
    "pricing": +0.65,
    # Competitive dynamics — economic links predict returns over weeks
    # (Cohen & Frazzini 2008); slow news diffusion produces drift in low-
    # coverage names (Hong/Lim/Stein 2000). But narrative-driven competitive
    # panics tend to reverse (Bordalo et al. 2019). Net: moderate persistence.
    "competitive": +0.45,
    # Management credibility — diagnostic expectations cause analyst forecast
    # revisions to over-react to management surprises and reverse within 12
    # months (Bordalo, Gennaioli, La Porta, Shleifer 2019). Investors over-
    # react to private/narrative info (Daniel/Hirshleifer/Subrahmanyam 1998).
    # Net: slightly negative — single-quarter credibility hits mean-revert.
    "management_credibility": -0.15,
    # Macro / market-wide drivers — long-horizon reversal (De Bondt & Thaler
    # 1985); Tetlock (2007) sentiment fully reverses within a week; attention-
    # driven moves reverse within a year (Da/Engelberg/Gao 2011); investors
    # over-react to systemic narratives (Daniel et al. 1998). Strong reversion.
    "macro": -0.75,
}


# Citation -> brief (paper, year, finding, dimension affected).
# Kept here so a future researcher can audit each prior against its source
# without grep'ing comments.
RESEARCH_REFERENCES: dict[str, str] = {
    "Bernard_Thomas_1989": (
        "Bernard & Thomas (1989), 'Post-Earnings-Announcement Drift'. "
        "Top-decile SUE drifts ~+4.2% over 60 days. Affects: demand, pricing."
    ),
    "Foster_Olsen_Shevlin_1984": (
        "Foster, Olsen & Shevlin (1984), 'Earnings Releases, Anomalies, and "
        "the Behavior of Security Returns'. Earlier PEAD evidence. "
        "Affects: demand, pricing."
    ),
    "Engelberg_McLean_Pontiff_2018": (
        "Engelberg, McLean & Pontiff (2018), 'Anomalies and News'. Anomaly "
        "returns 7x larger on earnings days; fundamentals embed slowly. "
        "Affects: demand, pricing."
    ),
    "Tetlock_2007": (
        "Tetlock (2007), 'Giving Content to Investor Sentiment'. WSJ "
        "pessimism predicts -7bps next day that fully reverses within a week. "
        "Affects: macro (sentiment side)."
    ),
    "Tetlock_SaarTsechansky_Macskassy_2008": (
        "Tetlock, Saar-Tsechansky & Macskassy (2008), 'More Than Words'. "
        "Negative fundamental words predict earnings up to a quarter ahead; "
        "non-fundamental words don't. Affects: demand, pricing."
    ),
    "DeBondt_Thaler_1985": (
        "De Bondt & Thaler (1985), 'Does the Stock Market Overreact?'. "
        "3-year losers beat 3-year winners by ~8%/yr. Long-horizon reversal "
        "of broad-based moves. Affects: macro."
    ),
    "Daniel_Hirshleifer_Subrahmanyam_1998": (
        "Daniel, Hirshleifer & Subrahmanyam (1998), 'Investor Psychology "
        "and Security Market Under- and Overreactions'. Public info -> "
        "underreaction (drift); private/narrative info -> overreaction "
        "(reversal). Affects: management_credibility, macro."
    ),
    "Bordalo_Gennaioli_LaPorta_Shleifer_2019": (
        "Bordalo, Gennaioli, La Porta & Shleifer (2019), 'Diagnostic "
        "Expectations and Stock Returns'. Forecast revisions over-react to "
        "surprises; reverse within 12 months. Affects: management_credibility, "
        "competitive."
    ),
    "Cohen_Frazzini_2008": (
        "Cohen & Frazzini (2008), 'Economic Links and Predictable Returns'. "
        "Customer/supplier linkages predict cross-stock returns over weeks. "
        "Affects: competitive (real-link side), peer-news ablation."
    ),
    "Hong_Lim_Stein_2000": (
        "Hong, Lim & Stein (2000), 'Bad News Travels Slowly'. Slow diffusion "
        "drives momentum in low-coverage names. Affects: competitive."
    ),
    "Da_Engelberg_Gao_2011": (
        "Da, Engelberg & Gao (2011), 'In Search of Attention'. Attention-"
        "driven moves spike then reverse within a year. Affects: macro."
    ),
}


# DEFAULT_DIMENSION_PERSISTENCE preserves backward compatibility — callers
# that imported the original constant continue to work, but the underlying
# values are now the research-grounded priors.
DEFAULT_DIMENSION_PERSISTENCE: dict[str, float] = dict(RESEARCH_GROUNDED_PERSISTENCE)


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


# ---------- Empirical calibration ----------

DIMENSION_NAMES = (
    "demand",
    "pricing",
    "competitive",
    "management_credibility",
    "macro",
)


def _direction_sign(direction: str) -> int:
    if direction == "positive":
        return +1
    if direction == "negative":
        return -1
    return 0


def _build_design_matrix(
    attributions: list[Attribution],
) -> tuple[np.ndarray, np.ndarray]:
    """Return (X, sign_of_reaction) where X has one column per dimension and
    each row is the dim.weight (magnitude only) feature vector for one event.

    Direction is intentionally not folded into the feature — persistence is
    a direction-agnostic statement ("does the move continue?"), and the
    reaction's direction is captured by the target's sign(reaction) factor.
    Mixing both into the feature would conflate the two and flip coefficient
    signs in the common case (demand-driven decline with demand=negative).
    """
    rows: list[list[float]] = []
    move_signs: list[float] = []
    for attr in attributions:
        feature_row: list[float] = []
        for name in DIMENSION_NAMES:
            dim = getattr(attr, name, None)
            if dim is None or _direction_sign(dim.direction) == 0:
                feature_row.append(0.0)
                continue
            feature_row.append(float(dim.weight))
        rows.append(feature_row)
        move_signs.append(1.0 if attr.return_pct > 0 else (-1.0 if attr.return_pct < 0 else 0.0))
    return np.asarray(rows, dtype=float), np.asarray(move_signs, dtype=float)


def calibrate_persistence(
    events_df: pd.DataFrame,
    attributions: list[Attribution],
    *,
    horizon: int = 5,
    use_excess: bool = True,
    clip: float = 1.0,
) -> dict[str, float]:
    """
    Estimate per-dimension persistence coefficients empirically from history.

    Method (intentionally simple — research baseline, not over-engineered):
        For each event:
            features      = [dim.weight * dim_sign for dim in DIMENSION_NAMES]
            target        = sign(reaction_return) * fwd_return_excess

        target's sign meaning:
            > 0  ->  the move continued in the same direction (lean was right)
            < 0  ->  the move reversed                       (fade was right)

        We regress target on features by ordinary least squares. Each
        coefficient is the dimension's empirical persistence: positive ->
        lean, negative -> fade. The result is clipped to [-clip, +clip] so
        a noisy regression on a small universe can't produce coefficients
        that violate the [-1, +1] persistence semantics.

    Args:
        events_df: must contain `event_id`, `reaction_return`, and either
            `fwd_{horizon}d_excess` (when use_excess) or `fwd_{horizon}d`.
            Rows must be aligned 1:1 with `attributions` (same event order).
        attributions: one Attribution per event, in the same order as
            events_df.
        horizon: 1, 5, or 20 — picks the matching forward-return column.
        use_excess: subtract market drift from the forward return when True.
        clip: hard cap on the absolute value of each coefficient. 1.0 keeps
            outputs on the same [-1, +1] scale as the prior.

    Returns:
        dict[dim_name -> persistence_coefficient]. Pass directly to
        `strategy_dimension_weighted(persistence=...)` or
        `strategy_hybrid(persistence=...)` to use empirical instead of
        prior-based weights.

    When you don't have enough live attributions yet, keep using
    DEFAULT_DIMENSION_PERSISTENCE (the research-grounded prior). Do not
    calibrate on placeholder/RNG attributions — the regression will fit to
    noise and overwrite the literature priors with garbage.
    """
    if len(events_df) != len(attributions):
        raise ValueError(
            f"events_df and attributions must align 1:1 "
            f"(got {len(events_df)} vs {len(attributions)})"
        )
    if len(attributions) == 0:
        return dict(DEFAULT_DIMENSION_PERSISTENCE)

    if horizon not in (1, 5, 20):
        raise ValueError(f"horizon must be 1, 5, or 20 (got {horizon})")

    fwd_col = f"fwd_{horizon}d_excess" if use_excess else f"fwd_{horizon}d"
    if fwd_col not in events_df.columns:
        raise ValueError(
            f"events_df missing required forward-return column {fwd_col!r}"
        )
    if "reaction_return" not in events_df.columns:
        raise ValueError("events_df missing 'reaction_return' column")

    X, _ = _build_design_matrix(attributions)
    reaction = pd.to_numeric(events_df["reaction_return"], errors="coerce").to_numpy()
    fwd = pd.to_numeric(events_df[fwd_col], errors="coerce").to_numpy()

    # target[i] > 0 when the move continued (lean was right); < 0 when it
    # reversed (fade was right). Drop NaNs.
    move_sign = np.where(reaction > 0, 1.0, np.where(reaction < 0, -1.0, 0.0))
    y = move_sign * fwd
    mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1) & (move_sign != 0)
    Xm, ym = X[mask], y[mask]

    if Xm.shape[0] < len(DIMENSION_NAMES) + 1:
        # Underdetermined system — fall back to the prior rather than fit
        # noise. The +1 buffer keeps degrees of freedom non-trivial.
        return dict(DEFAULT_DIMENSION_PERSISTENCE)

    # OLS via lstsq: minimize ||Xm @ beta - ym||_2.
    coeffs, *_ = np.linalg.lstsq(Xm, ym, rcond=None)
    # The OLS coefficients have units of "fraction of next-horizon return per
    # unit of weight*sign feature". Normalize to the [-1, +1] scale by
    # rescaling by the max absolute coefficient (so the dominant dimension
    # lands at +/- 1 if it dominates), then clip.
    max_abs = float(np.max(np.abs(coeffs))) if len(coeffs) else 0.0
    if max_abs > 0:
        coeffs = coeffs / max_abs
    coeffs = np.clip(coeffs, -clip, clip)
    return {name: float(c) for name, c in zip(DIMENSION_NAMES, coeffs)}


# ---------- Registry-ready exports ----------

# Names match the keys we'll add to backtest.signal.STRATEGY_REGISTRY so
# `backtest.runner --strategy <name>` and `attribution_to_trade(strategy=<name>)`
# both resolve.
FRAMEWORK_STRATEGIES = {
    "expected_vs_realized": strategy_expected_vs_realized,
    "dimension_weighted": strategy_dimension_weighted,
    "hybrid": strategy_hybrid,
}
