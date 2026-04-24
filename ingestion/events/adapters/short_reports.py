"""
Short-seller report adapter: ShortReport -> Event + parallel TextChunk.

A short report is inherently text. The Event carries the title + thesis
summary; the TextChunk carries the full thesis_text so the attribution
LLM can quote from it.

Per spec: reuse SourceType.SHORT_INTEREST for the parallel chunk to avoid
introducing a new source type for short-seller reports.
"""
from __future__ import annotations

from schema import Event, ShortReport, SourceType, TextChunk


def to_events(records: list[ShortReport]) -> list[Event]:
    events: list[Event] = []
    for r in records:
        events.append(
            Event(
                event_id=r.chunk_id,
                ticker=r.target_ticker,
                event_date=r.publication_date,
                event_type="short_report",
                source=r.publisher,
                payload_ref=r.chunk_id,
                text=r.thesis_text,
            )
        )
    return events


def to_chunks(records: list[ShortReport]) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for r in records:
        chunks.append(
            TextChunk(
                chunk_id=r.chunk_id,
                ticker=r.target_ticker,
                source_type=SourceType.SHORT_INTEREST,
                publication_date=r.publication_date,
                source_url=r.source_url,
                section_name="short_report",
                text=r.thesis_text,
                token_count=r.token_count,
            )
        )
    return chunks
