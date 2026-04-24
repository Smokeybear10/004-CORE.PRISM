"""
Schema validation tests. Run these early and often - they catch the most
common bug in this kind of project: one teammate changing schema.py and
breaking everyone else's code silently.

Usage: pytest tests/
"""

import json
from pathlib import Path

from schema import TextChunk

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_sec_chunk_fixtures_conform_to_schema():
    """Every fixture chunk must parse as a valid TextChunk."""
    with open(FIXTURES_DIR / "sec_chunks_sample.json") as f:
        raw = json.load(f)
    for record in raw:
        chunk = TextChunk(**record)
        assert chunk.chunk_id
        assert chunk.publication_date
        assert chunk.text


def test_chunk_id_is_stable():
    """Chunk IDs must be deterministic given the same inputs."""
    from datetime import date
    from ingestion.sec import make_chunk_id
    from schema import SourceType

    a = make_chunk_id(SourceType.SEC_10K, "AAPL", date(2024, 11, 1), "mda", 1)
    b = make_chunk_id(SourceType.SEC_10K, "AAPL", date(2024, 11, 1), "mda", 1)
    assert a == b
    assert a == "sec_10k_AAPL_2024-11-01_mda_001"


def test_default_ablations_are_monotonically_additive():
    """
    Each successive AblationConfig in DEFAULT_ABLATIONS must be a superset of
    the previous one. That's what makes the demo chart 'additive' and
    interpretable - each bar shows what one more source type added.
    """
    from backtest import DEFAULT_ABLATIONS

    for prev, curr in zip(DEFAULT_ABLATIONS, DEFAULT_ABLATIONS[1:]):
        prev_set = set(prev.sources)
        curr_set = set(curr.sources)
        assert prev_set.issubset(curr_set), (
            f"Ablation '{curr.name}' must include all sources from '{prev.name}'. "
            f"Missing: {prev_set - curr_set}"
        )
