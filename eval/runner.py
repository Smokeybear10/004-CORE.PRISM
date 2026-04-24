"""
Matrix runner: iterate (case × ablation), call model.attribute(), score,
aggregate into an EvalReport that demo/ reads to build the bar chart.

Chunk loading is pluggable via ChunkProvider so the runner doesn't block on
stubbed ingestion modules. Default provider wires to ingestion/*, but tests
and demos can pass any callable that returns dict[SourceType, list[TextChunk]].
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

from pydantic import BaseModel, Field

from eval import cache as eval_cache
from eval.cases import EvalCase, load_cases
from eval.config import DEFAULT_CONFIG, ScorerConfig
from eval.scorer import ScoreResult, score
from schema import (
    AblationConfig,
    Attribution,
    PriceMove,
    SourceType,
    TextChunk,
)


log = logging.getLogger(__name__)


ChunkProvider = Callable[[str, date], dict[SourceType, list[TextChunk]]]


# ---------- Default chunk provider: call real ingestion ----------

def default_chunk_provider(ticker: str, as_of: date) -> dict[SourceType, list[TextChunk]]:
    """
    Load chunks from every ingestion module, catching NotImplementedError so
    partially-scaffolded pipelines still produce partial reports. Each source
    that fails is logged; the runner tags missing sources in the report so
    ablation comparisons stay honest.
    """
    out: dict[SourceType, list[TextChunk]] = {}

    # SEC
    try:
        from ingestion.sec import get_filings_as_of
        chunks = get_filings_as_of(ticker, as_of)
        for c in chunks:
            out.setdefault(c.source_type, []).append(c)
    except NotImplementedError:
        log.warning("ingestion.sec.get_filings_as_of not implemented; SEC chunks skipped")
    except Exception as e:
        log.warning(f"ingestion.sec failed: {e}")

    # Company + peer news (+ earnings transcripts)
    try:
        from ingestion.earnings_news import get_news_as_of
        chunks = get_news_as_of(ticker, as_of)
        for c in chunks:
            out.setdefault(c.source_type, []).append(c)
    except NotImplementedError:
        log.warning("ingestion.earnings_news.get_news_as_of not implemented; news chunks skipped")
    except Exception as e:
        log.warning(f"ingestion.earnings_news failed: {e}")

    # Macro
    try:
        from ingestion.macro import get_macro_as_of  # type: ignore
        chunks = get_macro_as_of(as_of)
        for c in chunks:
            out.setdefault(c.source_type, []).append(c)
    except (ImportError, AttributeError, NotImplementedError):
        log.warning("ingestion.macro.get_macro_as_of unavailable; macro chunks skipped")
    except Exception as e:
        log.warning(f"ingestion.macro failed: {e}")

    return out


# ---------- Report ----------

class RunRecord(BaseModel):
    """One cell of the (case × ablation) matrix."""
    case_id: str
    ablation_name: str
    score: ScoreResult
    live_sources: list[str] = Field(default_factory=list)          # actually-present chunk source types
    missing_sources: list[str] = Field(default_factory=list)       # configured but zero chunks
    chunks_used: int = 0
    cache_hit: bool = False
    error: Optional[str] = None


class EvalReport(BaseModel):
    prompt_version: str
    timestamp: datetime
    records: list[RunRecord]

    # Aggregates (computed at build time so demo/chart.py stays trivial)
    composite_by_ablation: dict[str, float] = Field(default_factory=dict)
    dim_accuracy_by_ablation: dict[str, float] = Field(default_factory=dict)
    n_cases: int = 0
    n_ablations: int = 0


# ---------- Main orchestration ----------

@dataclass
class RunnerOptions:
    use_cache: bool = True
    prompt_version: str = "dev"
    scorer_config: ScorerConfig = None  # filled in __post_init__

    def __post_init__(self):
        if self.scorer_config is None:
            self.scorer_config = DEFAULT_CONFIG


def _price_move_for(case: EvalCase) -> PriceMove:
    """
    Runner-local PriceMove stub for calling model.attribute(). We do not need
    the real returns / z-scores for scoring — the scorer only inspects the
    Attribution, not the PriceMove. Real price data can be patched in later
    from ingestion/prices if the model prompt wants it.
    """
    return PriceMove(
        ticker=case.ticker,
        move_date=case.move_date,
        return_pct=0.0,
        vol_zscore=0.0,
        is_significant=True,
    )


def _filter_chunks(
    chunks_by_source: dict[SourceType, list[TextChunk]],
    ablation: AblationConfig,
    as_of: date,
) -> tuple[list[TextChunk], list[str], list[str]]:
    """
    Restrict chunks to this ablation's sources AND to publication_date <= as_of
    (no-foreknowledge rule). Returns (filtered_chunks, live_sources, missing_sources).
    """
    allowed = set(ablation.sources)
    live: list[str] = []
    missing: list[str] = []
    selected: list[TextChunk] = []
    for st in ablation.sources:
        bucket = chunks_by_source.get(st, [])
        kept = [c for c in bucket if c.publication_date <= as_of]
        if kept:
            live.append(st.value)
            selected.extend(kept)
        else:
            missing.append(st.value)
    _ = allowed  # silence linter; kept for future stricter checks
    return selected, live, missing


def _attribute_one(
    case: EvalCase,
    ablation: AblationConfig,
    chunks: list[TextChunk],
    options: RunnerOptions,
) -> tuple[Attribution, bool]:
    """
    Return (attribution, cache_hit). Tries cache first; on miss, calls
    model.attribute() and writes the result.
    """
    if options.use_cache:
        cached = eval_cache.read(
            case.ticker, case.move_date, ablation.name, options.prompt_version
        )
        if cached is not None:
            return cached, True

    from model import attribute  # local import — model module may still be stubbed

    move = _price_move_for(case)
    attribution = attribute(move, chunks, ablation)

    # Guardrail: ensure the model tagged the ablation correctly, because a lot
    # of the report depends on this field being right.
    if attribution.ablation_name != ablation.name:
        attribution.ablation_name = ablation.name
    if not attribution.sources_used:
        attribution.sources_used = list(ablation.sources)

    if options.use_cache:
        eval_cache.write(attribution, ablation.name, options.prompt_version)

    return attribution, False


def run_matrix(
    cases: Optional[list[EvalCase]] = None,
    ablations: Optional[list[AblationConfig]] = None,
    chunk_provider: ChunkProvider = default_chunk_provider,
    options: Optional[RunnerOptions] = None,
) -> EvalReport:
    """
    Iterate every (case × ablation) pair, produce a scored RunRecord per cell,
    aggregate into an EvalReport.

    Errors inside one cell (NotImplementedError from a model stub, malformed
    output, etc.) do not abort the run — the offending record is marked with
    `error` and composite=0.0 so the rest of the matrix still renders.
    """
    if options is None:
        options = RunnerOptions()
    if cases is None:
        cases = load_cases()
    if ablations is None:
        from backtest import DEFAULT_ABLATIONS
        ablations = DEFAULT_ABLATIONS

    # Chunks are loaded once per case and filtered per ablation.
    chunks_cache: dict[str, dict[SourceType, list[TextChunk]]] = {}

    records: list[RunRecord] = []
    for case in cases:
        if case.case_id not in chunks_cache:
            try:
                chunks_cache[case.case_id] = chunk_provider(case.ticker, case.move_date)
            except Exception as e:
                log.error(f"chunk_provider failed for {case.case_id}: {e}")
                chunks_cache[case.case_id] = {}

        for ablation in ablations:
            chunks, live, missing = _filter_chunks(
                chunks_cache[case.case_id], ablation, case.move_date
            )
            record = RunRecord(
                case_id=case.case_id,
                ablation_name=ablation.name,
                score=ScoreResult(
                    case_id=case.case_id,
                    ablation_name=ablation.name,
                    composite=0.0,
                ),
                live_sources=live,
                missing_sources=missing,
                chunks_used=len(chunks),
            )

            try:
                attribution, cache_hit = _attribute_one(case, ablation, chunks, options)
                record.cache_hit = cache_hit
                record.score = score(
                    attribution, case.expected, ablation, options.scorer_config
                )
            except NotImplementedError as e:
                record.error = f"NotImplementedError: {e}"
                log.warning(f"{case.case_id} / {ablation.name}: {record.error}")
            except Exception as e:
                record.error = f"{type(e).__name__}: {e}"
                log.warning(f"{case.case_id} / {ablation.name}: {record.error}")

            records.append(record)

    return _build_report(records, options, cases, ablations)


def _build_report(
    records: list[RunRecord],
    options: RunnerOptions,
    cases: list[EvalCase],
    ablations: list[AblationConfig],
) -> EvalReport:
    composite_by_ab: dict[str, list[float]] = {}
    dim_hits_by_ab: dict[str, list[int]] = {}
    for r in records:
        composite_by_ab.setdefault(r.ablation_name, []).append(r.score.composite)
        if r.score.dim_match is not None:
            dim_hits_by_ab.setdefault(r.ablation_name, []).append(
                1 if r.score.dim_match else 0
            )

    composite_mean = {
        name: statistics.mean(vals) if vals else 0.0
        for name, vals in composite_by_ab.items()
    }
    dim_accuracy = {
        name: statistics.mean(vals) if vals else 0.0
        for name, vals in dim_hits_by_ab.items()
    }

    return EvalReport(
        prompt_version=options.prompt_version,
        timestamp=datetime.now(),
        records=records,
        composite_by_ablation=composite_mean,
        dim_accuracy_by_ablation=dim_accuracy,
        n_cases=len(cases),
        n_ablations=len(ablations),
    )
