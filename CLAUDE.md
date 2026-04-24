# CLAUDE.md

Shared project brief. Multiple Claude instances read and edit this file — keep
it accurate and avoid stepping on each other. If a section describes code,
skim the code first and fix the section if it's drifted.

## Project: Equity Price-Action Tagger

Given a significant historical stock move, extract a structured attribution
from surrounding text (earnings transcripts, news, SEC filings, and a wide
set of alternative sources) across five dimensions. Then test whether
attribution category predicts whether the move persists (structural) or fades
(transient).

This is a causal labeler for past price action, not a forecaster. The
deliverable is a backtest + demo chart, not a product. Built for the
Bridgewater AI Hackathon (Track 1).

## Phase (as of 2026-04-24)

**Ingestion + early backtest.** Sources are being scaffolded; most ingestion
functions are stubs. The backtest harness has a first working function
(`backtest.basket.backtest_basket`). Modeling layer (`model/`) is empty —
starts once ingestion coverage is acceptable.

## Data sources & ownership

| Source                              | Owner    | Module                              | Status |
|-------------------------------------|----------|-------------------------------------|--------|
| Yahoo Finance — prices, earnings    | teammate | (Yahoo used directly in backtest)   | prices working via yfinance |
| SEC filings (10-K/Q, 8-K, Form 4)   | teammate | `ingestion/sec/`                    | stubs + fixtures |
| Dated news headlines                | teammate | `ingestion/earnings_news/`          | empty |
| Earnings transcripts                | teammate | `ingestion/earnings_news/`          | empty |
| FRED macro releases                 | teammate | (not scaffolded yet)                | — |
| 13F hedge-fund holdings             | Henry    | `ingestion/idiosyncratic/thirteen_f.py` | stub + fixture |
| Short-seller research               | Henry    | `ingestion/idiosyncratic/short_reports.py` | stub + fixture |
| FDA calendar + approvals/CRLs       | Henry    | `ingestion/idiosyncratic/fda.py`    | stub + fixture |
| FINRA short interest                | Henry    | `ingestion/idiosyncratic/short_interest.py` | **partially implemented** |
| Index rebalances                    | Henry    | `ingestion/idiosyncratic/index_changes.py` | stub + fixture |
| Analyst ratings & price targets     | Henry    | `ingestion/idiosyncratic/analyst_actions.py` | stub + fixtures |
| FOMC speeches & minutes             | Henry    | (not scaffolded yet)                | — |
| Credit spreads / CDS                | Henry    | (not scaffolded yet)                | — |

## Repository layout

```
schema.py                     Shared Pydantic contracts — see below
backtest/
  basket.py                   Equal-weight long/short basket; PnL + Sharpe + drawdown
  __init__.py                 (empty)
ingestion/
  sec/__init__.py             fetch_filings, get_filings_as_of, chunk_text (stubs)
                              make_chunk_id (done)
  earnings_news/__init__.py   (empty)
  idiosyncratic/              Henry's alt-data sources
    __init__.py
    thirteen_f.py             13F holdings + quarter-over-quarter deltas
    short_reports.py          short-seller publisher scrapers
    fda.py                    PDUFA / AdComm / approvals / CRLs
    short_interest.py         FINRA bi-monthly short interest (partial impl)
    index_changes.py          S&P / Russell / MSCI add/delete
    analyst_actions.py        rating changes + price-target changes
model/__init__.py             (empty — price-move detection + LLM attribution goes here)
tests/
  test_schema.py              Fixture + chunk_id tests (pytest)
  fixtures/
    sec_chunks_sample.json
    idiosyncratic/            sample JSONs for all 8 idiosyncratic record types
```

## Schema contracts (`schema.py`)

Every module's inputs/outputs conform to the Pydantic models here. **Post in
team chat before modifying `schema.py`** — downstream modules depend on field
names and types being stable.

Types currently defined:

- **Text**: `TextChunk` (atomic unit for model citations), `SourceType` enum
- **Price**: `PriceMove`
- **Attribution**: `DimensionScore`, `Attribution`, with `move_character` ∈
  {structural, transient, mixed, unclear}
- **Backtest**: `BacktestResult` (strategy_name, n_trades, sharpe, hit_rate,
  avg_return, max_drawdown, notes)
- **Unified event envelope**: `Event` — common shape for anything that can
  drive a move (used by idiosyncratic sources)
- **Idiosyncratic**: `HoldingRecord`, `HoldingDelta` + `HoldingAction`;
  `ShortReport`; `FDAEvent` + `FDAEventType`; `ShortInterestRecord`;
  `IndexChange` + `IndexChangeAction`; `AnalystRating` + `RatingAction`;
  `PriceTargetChange`

## Non-negotiable rules

1. **No foreknowledge.** Any function that retrieves data for a given date
   MUST filter by `publication_date <= as_of` (or equivalent: `filing_date`,
   `settlement_date`, `event_date`, `announcement_date`). Every retrieval
   function takes an `as_of` parameter. This is THE most common way
   hackathon projects accidentally leak future information into backtests.
2. **Filing date != period end.** A 10-K for fiscal year ending Dec 31 might
   be filed in March. Markets react on the filing date. Store both; use the
   filing/publication date for as-of queries.
3. **Stable IDs.** Every text chunk, event, rating, etc. has a stable,
   deterministic ID so the model can cite evidence. Format lives in the
   corresponding ingestion module (e.g. `make_chunk_id` in `ingestion/sec`).
4. **Fixtures before code.** Before implementing a parser, write a fixture
   under `tests/fixtures/` that matches the schema. Downstream teammates can
   build against fixtures while the real fetcher is in progress.
5. **One schema, enforced.** Use the Pydantic models in `schema.py`. No loose
   dicts crossing module boundaries.
6. **Evidence required.** The attribution model will hallucinate. Every
   `DimensionScore` must include non-empty `evidence_chunk_ids` that resolve
   to real chunks — drop attributions without valid citations.

## Module boundaries

When editing inside one module, do NOT touch files in other modules. If a
cross-module change is needed, raise it explicitly before making it. This
matters more than usual because multiple people + Claude instances work in
parallel.

## The 5 attribution dimensions

- `demand` — unit volume, customer count, market share shifts
- `pricing` — price changes, mix, discounting
- `competitive` — new entrants, competitor moves, moats
- `management_credibility` — guidance changes, execution, leadership comments
- `macro` — rates, FX, commodities, geopolitics

## Definitions (locked)

- **Significant price move**: `|1-day return| > 2x trailing 30-day realized
  vol`, OR top 5% absolute return in trailing 60 days. Pick ONE and stick
  with it across the whole pipeline.
- **Fade window**: did the move reverse by >50% within 5 trading days?
- **Persist**: no reversal, or extension of the move, over 5 trading days.

## Data access (HuggingFace)

- Private HF repo: `BridgewaterAIHackathon/BW-AI-Hackathon`
- Auth: `huggingface-cli login` once, then pass `token=True` to `load_dataset`
- Files sit directly under each source folder (e.g.
  `Structured_Data/SNE/yahoo-finance-data/*.parquet`) — **no `data/`
  subfolder** (unlike the public `defeatbeta/yahoo-finance-data` mirror)

## Conventions

- Ingestion writes **parquet** (not CSV) for anything non-trivial
- Timestamps in **UTC, ISO-8601**
- One script per source; keep ingestion decoupled from modeling
- Do NOT commit HF tokens or raw bulk data — stage to the HF repo instead
- SEC filings can be huge (200+ pages). Chunk aggressively (~800 tokens with
  ~100 token overlap), store only relevant sections (MD&A, Risk Factors)

## Workflow

- Feature branches, e.g. `henry-idiosyncratic`, `person1-sec`, etc.
- Merge to `main` only after a teammate reviews the PR
- `python -m pytest tests/` must pass before any merge
- If you touch `schema.py`, post in team chat BEFORE merging

## Commands

```bash
# Setup (once)
python -m venv .venv
.venv/Scripts/activate          # Windows
pip install -r requirements.txt
huggingface-cli login           # for HF access

# Tests
.venv/Scripts/python.exe -m pytest tests/

# Smoke-test basket backtest
.venv/Scripts/python.exe -c "
from backtest.basket import backtest_basket
r, p = backtest_basket(longs=['NVDA'], start_date='2026-02-24', end_date='2026-04-24')
print(r.model_dump_json(indent=2))"
```

## Known traps

- **Yahoo Finance has survivorship bias** — delisted tickers silently
  disappear. Fine for a hackathon; note it in the demo.
- **yfinance `end_date` is exclusive.** Pass the day after to include it.
- **13F has a 45-day lag** after quarter-end — filings land ~Feb 14, May 15,
  Aug 14, Nov 14. Don't join on quarter-end; join on filing date.
- **FINRA short interest is bi-monthly**, settled ~mid-month and ~end-of-month,
  published ~8 business days later. Use publication date for as-of, not
  settlement date.
- **FDA sponsor → ticker mapping is messy** — handle subsidiaries, M&A, and
  private sponsors (which yield no ticker).
- **Multiple Claude instances are active.** Before editing schema.py or
  CLAUDE.md, pull latest. Expect to occasionally see files you didn't create.
