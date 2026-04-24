"""
Placeholder Attribution generator.

The real model (ingested text → Attribution) isn't ready yet, so for
scaffold purposes we synthesize Attributions from the event's realized
characteristics. The ONLY framework wired up right now is the mentors'
Fundamental vs. Non-fundamental split:

    Fundamentally driven  → move_character = "structural"  → Follow (lean)
    Non-fundamental       → move_character = "transient"   → Fade

The classification logic is a stub: it uses the realized magnitude + z-score
as a proxy signal that a real LLM would produce from reading text. When
Person B / the model module is ready, replace `generate_attribution()` with
the real classifier — downstream code is framework-agnostic as long as it
returns a schema.Attribution with `move_character` set.

Also provides per-ablation "source accuracy lift": each added data source
reduces classification noise, simulating what we expect the real model to do.
"""
from __future__ import annotations

import random
from datetime import date
from typing import List, Optional

from schema import (
    Attribution,
    DimensionScore,
    SourceType,
)


# ── Ablation → source-bundle mapping ────────────────────────────────────────
# Matches the plan's 7 bars. Each successive bundle ADDS sources to the base.
ABLATION_BUNDLES: dict[str, list[SourceType]] = {
    "base_news":       [SourceType.NEWS],
    "+sec":            [SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_10Q, SourceType.SEC_8K],
    "+earnings":       [SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_10Q, SourceType.SEC_8K,
                        SourceType.EARNINGS_TRANSCRIPT],
    "+peer_news":      [SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_10Q, SourceType.SEC_8K,
                        SourceType.EARNINGS_TRANSCRIPT, SourceType.PEER_NEWS],
    "+sector_news":    [SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_10Q, SourceType.SEC_8K,
                        SourceType.EARNINGS_TRANSCRIPT, SourceType.PEER_NEWS, SourceType.SECTOR_NEWS],
    "+macro":          [SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_10Q, SourceType.SEC_8K,
                        SourceType.EARNINGS_TRANSCRIPT, SourceType.PEER_NEWS, SourceType.SECTOR_NEWS,
                        SourceType.MACRO],
    "+positioning":    [SourceType.NEWS, SourceType.SEC_10K, SourceType.SEC_10Q, SourceType.SEC_8K,
                        SourceType.EARNINGS_TRANSCRIPT, SourceType.PEER_NEWS, SourceType.SECTOR_NEWS,
                        SourceType.MACRO, SourceType.RESEARCH_13F],
}


def _classify_fundamental(return_pct: float, zscore: float, noise: float,
                          rng: random.Random) -> tuple[str, float]:
    """
    Stub LLM: decide 'structural' (fundamental) vs 'transient' (non-fundamental).

    Real model reads text. This stub uses realized signal as a noisy proxy:
    large, high-z moves are more likely to be structural (fundamental driver).
    `noise` scales the amount of random mislabelling (lower noise = better model =
    more data sources in the ablation).

    Returns (move_character, confidence).
    """
    # "True" label proxy: big + high-z → structural
    magnitude_score = min(1.0, abs(return_pct) / 0.15) * 0.5 + min(1.0, abs(zscore) / 5.0) * 0.5
    # Flip the label with probability = noise (i.e. a pure-noise model is 50/50)
    if rng.random() < noise:
        magnitude_score = rng.random()

    if magnitude_score >= 0.65:
        return "structural", float(min(0.95, 0.5 + magnitude_score / 2))
    if magnitude_score <= 0.35:
        return "transient", float(min(0.95, 0.5 + (1 - magnitude_score) / 2))
    if rng.random() < 0.3:
        return "mixed", 0.5
    return "unclear", 0.4


def _dim(weight: float, sign: int, rationale: str,
         evidence_chunk_ids: Optional[List[str]] = None) -> DimensionScore:
    direction = "positive" if sign > 0 else ("negative" if sign < 0 else "neutral")
    return DimensionScore(
        weight=float(max(0.0, min(1.0, weight))),
        direction=direction,
        rationale=rationale,
        evidence_chunk_ids=evidence_chunk_ids or ["placeholder_chunk_0"],
    )


def generate_attribution(
    ticker: str,
    move_date: date,
    return_pct: float,
    vol_zscore: float,
    ablation_name: str = "base_news",
    seed: Optional[int] = None,
) -> Attribution:
    """
    Synthesize an Attribution for a single event.

    Ablation noise schedule: base_news is noisiest (55% of events mis-labelled);
    each added source reduces noise toward a floor of 15%.
    """
    rng = random.Random(seed if seed is not None else hash((ticker, move_date.toordinal())))

    noise_schedule = {
        "base_news":     0.55,
        "+sec":          0.45,
        "+earnings":     0.35,
        "+peer_news":    0.30,
        "+sector_news":  0.25,
        "+macro":        0.20,
        "+positioning":  0.15,
    }
    noise = noise_schedule.get(ablation_name, 0.5)

    move_character, confidence = _classify_fundamental(return_pct, vol_zscore, noise, rng)

    # Dimension weights — the user's chosen framework doesn't require these to be
    # meaningful yet; we fill in plausible placeholders so the schema is satisfied.
    sign = 1 if return_pct > 0 else -1
    weights = [rng.random() for _ in range(5)]
    total = sum(weights) or 1.0
    w_demand, w_pricing, w_comp, w_mgmt, w_macro = [w / total for w in weights]

    return Attribution(
        ticker=ticker,
        move_date=move_date,
        return_pct=return_pct,
        predicted_return_pct=return_pct * rng.uniform(0.4, 1.1) if move_character == "structural" else None,
        demand=_dim(w_demand, sign, "placeholder: demand-driven read from news"),
        pricing=_dim(w_pricing, sign, "placeholder: pricing/margin read"),
        competitive=_dim(w_comp, -sign, "placeholder: competitive pressure read"),
        management_credibility=_dim(w_mgmt, sign, "placeholder: management commentary read"),
        macro=_dim(w_macro, 0, "placeholder: macro backdrop read"),
        move_character=move_character,
        confidence=confidence,
        ablation_name=ablation_name,
        sources_used=ABLATION_BUNDLES[ablation_name],
        chunks_considered=rng.randint(5, 50),
        model_notes="synthetic fixture — replace with real attribution when model module is ready",
    )


def generate_attributions_for_events(
    events_df,
    ablation_name: str = "base_news",
    seed: int = 0,
) -> list[Attribution]:
    """
    Batch version. events_df has columns matching events_focal.parquet.
    """
    out: list[Attribution] = []
    for i, row in enumerate(events_df.itertuples(index=False)):
        out.append(generate_attribution(
            ticker=row.ticker,
            move_date=row.reaction_end.date() if hasattr(row.reaction_end, "date") else row.reaction_end,
            return_pct=float(row.reaction_return),
            vol_zscore=float(row.reaction_return_zscore),
            ablation_name=ablation_name,
            seed=seed * 100_000 + i,
        ))
    return out
