"""End-to-end wiring test for `demo.analyze_ticker.analyze_ticker`.

Exercises the orchestrator with:
  - a synthetic 100-day OHLCV frame that plants a deterministic spike,
  - a monkeypatched `build_events_parquet` that writes tiny events +
    text_chunks parquets into the cwd's `data/cache/`,
  - a fake Anthropic client that dispatches on `tool_choice.name`.

Does NOT test attribution quality — just that the pieces connect.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from demo.analyze_ticker import analyze_ticker

_CHUNK_ID = "test_chunk_001"


def _fake_prices(ticker: str, n: int = 100, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    closes = [100.0]
    for i in range(1, n):
        r = 0.15 if i == 75 else float(rng.normal(0.0005, 0.01))
        closes.append(closes[-1] * (1.0 + r))
    return pd.DataFrame({
        "ticker": ticker, "date": dates,
        "open": closes, "close": closes, "high": closes, "low": closes,
        "volume": [1_000_000] * n,
    })


def _stub_build(as_of, out_path=Path("data/cache/events.parquet"), *args, **kwargs):
    out_path = Path(out_path)
    chunks_path = Path(kwargs.get("chunks_out_path") or "data/cache/text_chunks.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "event_id": ["e1"], "ticker": ["TEST"], "event_date": ["2024-03-15"],
        "event_type": ["news"], "source": ["stub"], "payload_ref": [_CHUNK_ID],
        "text": ["stub event"],
    }).to_parquet(out_path, index=False)
    pd.DataFrame({
        "chunk_id": [_CHUNK_ID], "ticker": ["TEST"], "source_type": ["news"],
        "publication_date": ["2024-03-15"], "period_end": [None],
        "source_url": [None], "section_name": [None],
        "text": ["stub chunk text"], "token_count": [10],
    }).to_parquet(chunks_path, index=False)


def _attribution_tool_input() -> dict:
    dim = {"weight": 0.2, "direction": "neutral", "rationale": "stub",
           "evidence_chunk_ids": [_CHUNK_ID]}
    return {"demand": dim, "pricing": dim, "competitive": dim,
            "management_credibility": dim, "macro": dim,
            "move_character": "unclear", "confidence": 0.5,
            "predicted_return_pct": 0.0}


class _FakeClient:
    def __init__(self):
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        name = kwargs["tool_choice"]["name"]
        if name == "emit_attribution":
            block = SimpleNamespace(type="tool_use", name=name, input=_attribution_tool_input())
        elif name == "emit_coherence_check":
            block = SimpleNamespace(type="tool_use", name=name,
                                    input={"plausible": True, "issues": []})
        else:
            raise AssertionError(f"unexpected tool: {name}")
        return SimpleNamespace(content=[block], stop_reason="tool_use")


def test_happy_path_writes_payload(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("demo.analyze_ticker.load_prices",
                        lambda tickers, as_of: _fake_prices(tickers[0]))
    monkeypatch.setattr("demo.analyze_ticker.build_events_parquet", _stub_build)

    result = analyze_ticker("TEST", date(2024, 4, 15), client=_FakeClient())

    assert result["ticker"] == "TEST"
    assert result["as_of"] == "2024-04-15"
    assert result["n_moves"] == len(result["moves"]) > 0
    assert len(result["price_series"]) == 100
    for m in result["moves"]:
        assert m["attribution"] is not None or m["error"] is not None
        assert "events" in m["evidence"] and "chunks" in m["evidence"]

    out_file = tmp_path / "data" / "analysis" / "TEST.json"
    assert out_file.exists()
    assert json.loads(out_file.read_text())["ticker"] == "TEST"


def test_empty_prices_returns_empty_payload(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    empty = pd.DataFrame(columns=["ticker", "date", "open", "close", "high", "low", "volume"])
    monkeypatch.setattr("demo.analyze_ticker.load_prices", lambda tickers, as_of: empty)

    result = analyze_ticker("NOPE", date(2024, 1, 1))

    assert result == {"ticker": "NOPE", "as_of": "2024-01-01",
                      "n_moves": 0, "price_series": [], "moves": []}
    assert (tmp_path / "data" / "analysis" / "NOPE.json").exists()
