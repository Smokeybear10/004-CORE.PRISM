"""
Tests for demo.live_attribution.get_attribution.

Covers four fallback paths per the task spec:
    1. live path with a fake client → source="live"
    2. use_mock=True → source="mock_fallback"
    3. empty evidence frames → source="mock_fallback", error says "no evidence"
    4. client raising anthropic.AuthenticationError → source="mock_fallback", error says "auth"

No network, no Streamlit, no file I/O. Fake-client pattern matches
tests/test_attribution.py::_StubClient.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import anthropic
import pandas as pd
import pytest

from demo.live_attribution import AttributionResult, get_attribution
from model.attribution.coherence import COHERENCE_TOOL_NAME
from model.attribution.prompt import ATTRIBUTION_TOOL_NAME
from schema import Attribution, CoherenceCheck, PriceMove

# ---------- common test fixtures ----------

MOVE = PriceMove(
    ticker="AAPL",
    move_date=date(2024, 2, 2),
    return_pct=-0.037,
    vol_zscore=-2.8,
    magnitude_rank=0.97,
)


def _events_df_with_one_event(chunk_id: str) -> pd.DataFrame:
    """An events frame with a single in-window event whose payload_ref equals
    the supplied chunk_id — so the joiner pulls that chunk into evidence."""
    return pd.DataFrame([{
        "event_id": chunk_id,
        "ticker": "AAPL",
        "event_date": date(2024, 2, 1).isoformat(),
        "event_type": "news",
        "source": "test",
        "payload_ref": chunk_id,
        "text": "Apple Q1 FY24 earnings commentary.",
    }])


def _chunks_df_with_one(chunk_id: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "chunk_id": chunk_id,
        "ticker": "AAPL",
        "source_type": "news",
        "publication_date": date(2024, 2, 1).isoformat(),
        "period_end": None,
        "source_url": "https://example.com/aapl-q1",
        "section_name": "p0",
        "text": "Apple Q1 FY24 revenue grew 2% YoY; iPhone down 1%.",
        "token_count": 10,
    }])


def _empty_events_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "event_id", "ticker", "event_date", "event_type",
        "source", "payload_ref", "text",
    ])


def _empty_chunks_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "chunk_id", "ticker", "source_type", "publication_date",
        "period_end", "source_url", "section_name", "text", "token_count",
    ])


def _empty_earnings() -> pd.DataFrame:
    return pd.DataFrame(columns=["ticker", "report_date"])


# ---------- fake client ----------

def _tool_use_block(tool_name: str, tool_input: dict):
    return SimpleNamespace(type="tool_use", name=tool_name, input=tool_input)


def _response(block):
    return SimpleNamespace(content=[block], stop_reason="tool_use")


def _valid_attribution_tool_input(cited_chunk_id: str) -> dict:
    """A tool_use input whose citations resolve to a real chunk and whose
    weights sum to 1.0 — passes validate_attribution."""
    dim = {
        "weight": 0.2,
        "direction": "negative",
        "rationale": "Evidence indicates a negative driver in this dimension.",
        "evidence_chunk_ids": [cited_chunk_id],
    }
    return {
        "demand": dim,
        "pricing": dim,
        "competitive": dim,
        "management_credibility": dim,
        "macro": dim,
        "move_character": "mixed",
        "confidence": 0.7,
        "predicted_return_pct": -0.03,
        "model_notes": "Test attribution.",
    }


class _StubClient:
    """Returns canned responses; records every .messages.create call."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("stub client: no more canned responses")
        return self._responses.pop(0)


class _RaisingAuthClient:
    """A client whose messages.create raises AuthenticationError."""
    def __init__(self):
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        raise _StubAuthError()


class _StubAuthError(anthropic.AuthenticationError):
    """Bypass AuthenticationError's strict __init__ (needs a real response)."""
    def __init__(self):
        Exception.__init__(self, "stub auth error")


# ---------- tests ----------

def test_live_path_returns_live_source_with_attribution_and_coherence():
    chunk_id = "news_test_p0"
    events_df = _events_df_with_one_event(chunk_id)
    chunks_df = _chunks_df_with_one(chunk_id)

    client = _StubClient([
        _response(_tool_use_block(ATTRIBUTION_TOOL_NAME,
                                  _valid_attribution_tool_input(chunk_id))),
        _response(_tool_use_block(COHERENCE_TOOL_NAME,
                                  {"plausible": True, "issues": []})),
    ])

    result = get_attribution(
        ticker="AAPL",
        move=MOVE,
        ablation_name="+macro",
        events_df=events_df,
        chunks_df=chunks_df,
        earnings_calendar=_empty_earnings(),
        use_mock=False,
        client=client,
    )

    assert isinstance(result, AttributionResult)
    assert result.source == "live"
    assert result.error is None
    assert isinstance(result.attribution, Attribution)
    assert isinstance(result.coherence, CoherenceCheck)
    assert result.coherence.plausible is True
    assert result.chunks  # non-empty
    assert len(client.calls) == 2  # attribution + coherence


def test_use_mock_true_returns_mock_fallback():
    result = get_attribution(
        ticker="AMD",
        move=MOVE.model_copy(update={"ticker": "AMD"}),
        ablation_name="+macro",
        events_df=_empty_events_df(),
        chunks_df=_empty_chunks_df(),
        earnings_calendar=_empty_earnings(),
        use_mock=True,
        client=None,
    )
    assert result.source == "mock_fallback"
    assert result.error is None
    assert isinstance(result.attribution, Attribution)
    assert result.chunks  # mock always provides chunks


def test_empty_evidence_short_circuits_to_mock():
    # Frames that contain nothing for AAPL in-window → evidence.text_chunks empty.
    result = get_attribution(
        ticker="AAPL",
        move=MOVE,
        ablation_name="+macro",
        events_df=_empty_events_df(),
        chunks_df=_empty_chunks_df(),
        earnings_calendar=_empty_earnings(),
        use_mock=False,
        client=None,   # would crash if called — proves short-circuit
    )
    assert result.source == "mock_fallback"
    assert result.error is not None
    assert "no evidence" in result.error.lower()


def test_authentication_error_falls_back_to_mock_with_auth_reason():
    chunk_id = "news_test_p0"
    events_df = _events_df_with_one_event(chunk_id)
    chunks_df = _chunks_df_with_one(chunk_id)

    result = get_attribution(
        ticker="AAPL",
        move=MOVE,
        ablation_name="+macro",
        events_df=events_df,
        chunks_df=chunks_df,
        earnings_calendar=_empty_earnings(),
        use_mock=False,
        client=_RaisingAuthClient(),
    )
    assert result.source == "mock_fallback"
    assert result.error is not None
    assert "auth" in result.error.lower()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
