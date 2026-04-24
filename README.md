# Equity Price Action Tagger

Bridgewater hackathon — Track 1. See `CLAUDE.md` for project spec and rules.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest tests/
```

## Module ownership

| Directory | Owner | Status |
|---|---|---|
| `ingestion/sec/` | Person 1 | stubs + fixtures in place |
| `ingestion/earnings_news/` | Person 2 | TODO |
| `model/` | Person 3 | TODO |
| `backtest/` + `demo/` | Person 4 | TODO |

## Workflow

1. Each person works on their own branch (`person1-sec`, etc.).
2. Build against fixtures in `tests/fixtures/` while real data ingestion is in progress.
3. Run `pytest` before every push.
4. Merge to `main` only after a teammate reviews.
5. **If you touch `schema.py`, tell the team in chat first.**

## Demo story (design backward from this)

> Here's a 1-day price move of -8% in NVDA on 2025-02-27.
> Our ingestion pipeline pulls the SEC 10-Q filed the day before and the earnings call transcript.
> Our model attributes the move: 60% demand (negative), 30% management credibility (negative), 10% macro.
> Character: **structural**. Predicted: persist. Actual next 5d return: -6.2%. ✓

Build toward this exact chart.
