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

1. **Sprint to a crappy end-to-end MVP on AAPL before polishing anything.** The target for the next mentor check-in is: AAPL, big moves flagged, company news + 10-Ks pulled, model produces `Attribution`, basic fade-or-follow output. No peer / macro yet.
2. After MVP works end-to-end, **add sources one at a time as ablations** — each is a demo talking point.
3. Each person works on their own branch. Build against `tests/fixtures/` while your real data source is in progress.
4. `pytest tests/` before every push.
5. Merge to `main` only after a teammate reviews.
6. **If you touch `schema.py`, post in team chat first.**

## Frozen test case

`tests/fixtures/aapl_march2020_expected.json` — AAPL COVID drop. Use this as the
fixed prompt-iteration target. Don't change the test case mid-iteration — freeze
inputs so you can isolate which prompt change caused which output change.

## Demo story (design backward from this)

The demo is **the journey, not just the result.**

> Here's AAPL in March 2020 — a 12% one-day drop.
> **Run 1 (news only):** model says "pricing, negative" — wrong.
> **Run 2 (+ 10-K risk factors):** model says "macro + competitive" — closer.
> **Run 3 (+ peer news — MSFT, GOOGL):** model sees sector-wide weakness, says "macro, high confidence" — right.
> **Run 4 (+ macro: Fed + VIX):** confidence jumps, coherence check passes, predicted return matches realized within 2pp.
> Move character: `transient`. Next-5d return: reversal. Fade signal: ✓.

Build toward this exact narrative. Each ablation bar on the chart is a claim we can defend.
