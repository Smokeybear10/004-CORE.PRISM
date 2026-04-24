"""
Stage-2 Layer (c): Behavior-on-perturbation tests.

Treat the harness like a measuring instrument and push on it:

    shuffle_chunks_stable      — reorder identical chunks, the dominant
                                 dimension / character should NOT flip
    inject_junk_chunk          — add an unrelated-ticker chunk, coherence
                                 should catch it OR attribution should stay clean
    determinism_check          — same input twice, same output

Every check takes a callable `run(evidence) -> Attribution` so tests can pass
a fake client and live runs can pass `model.attribution.run_attribution`.
No module in this file imports `anthropic` directly — keeps the unit path
free of API cost.
"""

from __future__ import annotations

import copy
import random
from datetime import datetime
from typing import Callable, Optional

from pydantic import BaseModel, Field

from schema import (
    Attribution,
    CoherenceCheck,
    JoinedEvidence,
    SourceType,
    TextChunk,
)


AttributionRunner = Callable[[JoinedEvidence], Attribution]
CoherenceRunner = Callable[[Attribution, JoinedEvidence], CoherenceCheck]


# ---------- Helpers for comparing two attributions ----------

def _dominant_dimension(attr: Attribution) -> str:
    dims = {
        name: getattr(attr, name).weight
        for name in ("demand", "pricing", "competitive", "management_credibility", "macro")
    }
    return max(dims, key=dims.get)


def _max_weight_delta(a: Attribution, b: Attribution) -> float:
    """Largest absolute per-dimension weight change between two attributions."""
    deltas = []
    for name in ("demand", "pricing", "competitive", "management_credibility", "macro"):
        deltas.append(abs(getattr(a, name).weight - getattr(b, name).weight))
    return max(deltas) if deltas else 0.0


# ---------- Perturbation results ----------

class ShuffleResult(BaseModel):
    passed: bool
    dominant_before: str
    dominant_after: str
    character_before: str
    character_after: str
    max_weight_delta: float
    tolerance: float
    reason: Optional[str] = None


class JunkInjectionResult(BaseModel):
    """
    Either the attribution stayed clean (dominant dim + character unchanged AND
    the junk chunk was not cited) OR the coherence check flagged the junk.
    Passing = at least one of those two defensive mechanisms worked.
    """
    passed: bool
    dominant_before: str
    dominant_after: str
    character_before: str
    character_after: str
    junk_chunk_id: str
    junk_cited: bool
    coherence_caught: Optional[bool] = None
    reason: Optional[str] = None


class DeterminismResult(BaseModel):
    passed: bool
    n_runs: int
    dominant_dimensions: list[str] = Field(default_factory=list)
    move_characters: list[str] = Field(default_factory=list)
    max_weight_delta: float
    tolerance: float
    reason: Optional[str] = None


class PerturbationReport(BaseModel):
    timestamp: datetime
    shuffle: Optional[ShuffleResult] = None
    junk: Optional[JunkInjectionResult] = None
    determinism: Optional[DeterminismResult] = None

    @property
    def passed(self) -> bool:
        return all(
            r.passed for r in (self.shuffle, self.junk, self.determinism) if r is not None
        )


# ---------- Shuffle check ----------

def _shuffled_evidence(evidence: JoinedEvidence, seed: int) -> JoinedEvidence:
    rng = random.Random(seed)
    shuffled_chunks = list(evidence.text_chunks)
    rng.shuffle(shuffled_chunks)
    shuffled_events = list(evidence.events)
    rng.shuffle(shuffled_events)
    return evidence.model_copy(update={
        "text_chunks": shuffled_chunks,
        "events": shuffled_events,
    })


def check_shuffle_stability(
    evidence: JoinedEvidence,
    run: AttributionRunner,
    *,
    seed: int = 13,
    weight_tolerance: float = 0.15,
) -> ShuffleResult:
    """
    Run the attribution on `evidence` twice — once original, once with the
    chunks and events shuffled. The dominant dimension and move_character
    should NOT change; per-dimension weights may drift but not wildly.
    """
    before = run(evidence)
    after = run(_shuffled_evidence(evidence, seed=seed))

    dom_b = _dominant_dimension(before)
    dom_a = _dominant_dimension(after)
    char_b = before.move_character
    char_a = after.move_character
    max_delta = _max_weight_delta(before, after)

    dom_stable = dom_b == dom_a
    char_stable = char_b == char_a
    weights_stable = max_delta <= weight_tolerance

    reason = None
    if not dom_stable:
        reason = f"dominant dim flipped on shuffle: {dom_b!r} -> {dom_a!r}"
    elif not char_stable:
        reason = f"move_character flipped on shuffle: {char_b!r} -> {char_a!r}"
    elif not weights_stable:
        reason = (
            f"weight drift {max_delta:.3f} exceeds tolerance {weight_tolerance}"
        )

    return ShuffleResult(
        passed=dom_stable and char_stable and weights_stable,
        dominant_before=dom_b,
        dominant_after=dom_a,
        character_before=char_b,
        character_after=char_a,
        max_weight_delta=max_delta,
        tolerance=weight_tolerance,
        reason=reason,
    )


# ---------- Junk-chunk injection ----------

def _make_junk_chunk(evidence: JoinedEvidence, junk_chunk_id: str) -> TextChunk:
    """A news-looking chunk about a completely unrelated company."""
    return TextChunk(
        chunk_id=junk_chunk_id,
        ticker="_JUNK_UNRELATED",
        source_type=SourceType.NEWS,
        publication_date=evidence.move.move_date,
        section_name="junk_control",
        text=(
            "Acme Corp announced a record harvest of widgets in its Midwest "
            "factory today. The company also released a new flavor of jam "
            "called 'Raspberry Gusto'. Analysts were broadly positive on "
            "the jam lineup and cited improving widget yield."
        ),
        token_count=48,
    )


def check_junk_injection(
    evidence: JoinedEvidence,
    run: AttributionRunner,
    *,
    coherence_runner: Optional[CoherenceRunner] = None,
    junk_chunk_id: str = "news__JUNK_UNRELATED_junk_000",
    weight_tolerance: float = 0.20,
) -> JunkInjectionResult:
    """
    Add one junk chunk about an unrelated company to `evidence` and re-run.
    Pass if EITHER:
        (1) the attribution stayed clean — dominant dim + character unchanged
            AND the junk chunk was not cited by any dimension — OR
        (2) `coherence_runner` is provided and flags the junk chunk.
    """
    before = run(evidence)

    junk = _make_junk_chunk(evidence, junk_chunk_id)
    polluted = evidence.model_copy(update={
        "text_chunks": list(evidence.text_chunks) + [junk],
    })
    after = run(polluted)

    dom_b = _dominant_dimension(before)
    dom_a = _dominant_dimension(after)
    char_b = before.move_character
    char_a = after.move_character

    junk_cited = False
    for name in ("demand", "pricing", "competitive", "management_credibility", "macro"):
        if junk_chunk_id in getattr(after, name).evidence_chunk_ids:
            junk_cited = True
            break

    attribution_stayed_clean = (
        dom_b == dom_a
        and char_b == char_a
        and not junk_cited
        and _max_weight_delta(before, after) <= weight_tolerance
    )

    coherence_caught: Optional[bool] = None
    if coherence_runner is not None:
        try:
            check = coherence_runner(after, polluted)
            coherence_caught = not check.plausible
        except Exception:
            coherence_caught = None

    passed = attribution_stayed_clean or bool(coherence_caught)
    reason = None
    if not passed:
        bits = []
        if junk_cited:
            bits.append(f"junk_chunk_id {junk_chunk_id!r} was cited")
        if dom_b != dom_a:
            bits.append(f"dominant dim flipped {dom_b!r} -> {dom_a!r}")
        if char_b != char_a:
            bits.append(f"move_character flipped {char_b!r} -> {char_a!r}")
        if coherence_runner is not None and coherence_caught is False:
            bits.append("coherence check did not flag the junk")
        reason = "; ".join(bits) or "unspecified pollution"

    return JunkInjectionResult(
        passed=passed,
        dominant_before=dom_b,
        dominant_after=dom_a,
        character_before=char_b,
        character_after=char_a,
        junk_chunk_id=junk_chunk_id,
        junk_cited=junk_cited,
        coherence_caught=coherence_caught,
        reason=reason,
    )


# ---------- Determinism ----------

def check_determinism(
    evidence: JoinedEvidence,
    run: AttributionRunner,
    *,
    n_runs: int = 2,
    weight_tolerance: float = 0.02,
) -> DeterminismResult:
    """
    Run the model N times on an identical deep-copy of `evidence`. Dominant
    dimension and move_character must agree on every run; per-dimension weights
    must agree within `weight_tolerance`.
    """
    if n_runs < 2:
        raise ValueError("determinism check needs at least 2 runs")

    outputs = [run(copy.deepcopy(evidence)) for _ in range(n_runs)]

    dominants = [_dominant_dimension(a) for a in outputs]
    characters = [a.move_character for a in outputs]
    max_delta = 0.0
    for i in range(1, len(outputs)):
        max_delta = max(max_delta, _max_weight_delta(outputs[0], outputs[i]))

    dom_stable = len(set(dominants)) == 1
    char_stable = len(set(characters)) == 1
    weights_stable = max_delta <= weight_tolerance

    reason = None
    if not dom_stable:
        reason = f"dominant dim varies across runs: {dominants!r}"
    elif not char_stable:
        reason = f"move_character varies across runs: {characters!r}"
    elif not weights_stable:
        reason = f"weight drift across runs: {max_delta:.4f} > {weight_tolerance}"

    return DeterminismResult(
        passed=dom_stable and char_stable and weights_stable,
        n_runs=n_runs,
        dominant_dimensions=dominants,
        move_characters=characters,
        max_weight_delta=max_delta,
        tolerance=weight_tolerance,
        reason=reason,
    )


# ---------- Orchestrator ----------

def run_perturbation_suite(
    evidence: JoinedEvidence,
    run: AttributionRunner,
    *,
    coherence_runner: Optional[CoherenceRunner] = None,
    shuffle_seed: int = 13,
    shuffle_tolerance: float = 0.15,
    junk_tolerance: float = 0.20,
    determinism_runs: int = 2,
    determinism_tolerance: float = 0.02,
) -> PerturbationReport:
    """Run all three perturbation checks on one JoinedEvidence bundle."""
    return PerturbationReport(
        timestamp=datetime.now(),
        shuffle=check_shuffle_stability(
            evidence, run, seed=shuffle_seed, weight_tolerance=shuffle_tolerance
        ),
        junk=check_junk_injection(
            evidence, run,
            coherence_runner=coherence_runner,
            weight_tolerance=junk_tolerance,
        ),
        determinism=check_determinism(
            evidence, run,
            n_runs=determinism_runs,
            weight_tolerance=determinism_tolerance,
        ),
    )
