# Equity Price Action Tagger

Bridgewater hackathon — Track 1. See `CLAUDE.md` for full project spec, the 6-step
pipeline, and the MVP scope.

## Quickstart

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pytest tests/
```

## Pipeline (6 steps, mentor framework)

| Step | Module | Input → Output |
|---|---|---|
| 1. Flag significant moves | `ingestion/prices/` | ticker → `list[PriceMove]` |
| 2. Ingest text | `ingestion/sec/`, `ingestion/earnings_news/` | ticker + date range → `list[TextChunk]` |
| 3. Attribute | `model/` | PriceMove + chunks → `Attribution` |
| 4. Add peer / macro / sector | `ingestion/macro/` + peer tickers | ablation inputs |
| 5. Coherence check | `model/check_coherence` | `Attribution` → `CoherenceCheck` |
| 6. Fade-or-follow | `backtest/` | Attribution + realized → lean/fade, backtest result |

## Module ownership

| Directory | Owner | Status |
|---|---|---|
| `ingestion/prices/` | Srilekha (yfinance-based) | stub |
| `ingestion/sec/` | Sophia | stubs + fixtures in place |
| `ingestion/earnings_news/` | [News lead TBD] | stub |
| `ingestion/macro/` | Henry (+ 13F via research) | stub |
| `model/` | shared — attribute/predict/coherence | stub |
| `backtest/` + `demo/` | shared — fade/follow + ablation chart | stub |

Team branches: `person1-sec`, `person2-yahoo`, `person3-research`, `person4-news`.

## Workflow (MVP first)

1. **Sprint to a crappy end-to-end MVP on ONE ticker before polishing anything.** Target for the next mentor check-in: one ticker, big moves flagged, company news + 10-Ks pulled, model produces `Attribution`, basic fade-or-follow output. No peer / macro yet.
2. After MVP works end-to-end, **add sources one at a time as ablations** — each is a demo talking point.
3. Each person works on their own branch. Build against `tests/fixtures/` while your real data source is in progress.
4. `pytest tests/` before every push.
5. Merge to `main` only after a teammate reviews.
6. **If you touch `schema.py`, post in team chat first.**

## Frozen test case

Once tickers are chosen, pick one (ticker, date) where the team knows what caused
the move. Store expected outputs in `tests/fixtures/<ticker>_<event>_expected.json`.
Don't change the test case mid-iteration — freeze inputs so you can isolate which
prompt change caused which output change. See CLAUDE.md for the detailed rules.

## Demo story (design backward from this)

The demo is **the journey, not just the result.** For one flagged move, walk the
audience through each ablation in order:

- **Run 1 — base news only:** attribution + confidence. Often wrong on macro-driven moves.
- **Run 2 — + SEC 10-K/10-Q:** does the dominant dimension shift? Does confidence change?
- **Run 3 — + peer news:** can the model now see sector-wide weakness?
- **Run 4 — + macro (Fed / VIX / commodities):** does `predicted_return_pct` align with realized?

Each ablation bar on the chart is a claim we can defend. "We found that 10-K
language moves the dominant dimension in N% of cases; peer news adds X% to hit
rate; macro flips the sign on Y% of moves." Whatever the numbers show, that's
the story.
