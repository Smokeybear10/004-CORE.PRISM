"""
Per-source adapters: record → Event (and for news / short_reports, parallel TextChunk).

Every adapter exposes `to_events(records) -> list[Event]`. Adapters for
text-bearing sources (news, short reports) additionally expose
`to_chunks(records) -> list[TextChunk]` so the joiner can surface
citeable evidence in `JoinedEvidence.text_chunks`.
"""
from __future__ import annotations

from ingestion.events.adapters import (
    analyst,
    earnings,
    fda,
    index_changes,
    news,
    short_interest,
    short_reports,
    thirteen_f,
)

__all__ = [
    "analyst",
    "earnings",
    "fda",
    "index_changes",
    "news",
    "short_interest",
    "short_reports",
    "thirteen_f",
]
