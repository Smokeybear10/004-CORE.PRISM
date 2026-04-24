# CLAUDE.md

## Project: Equity Price-Action Tagger

Goal: ingest multi-source financial data, feed it into a model, and produce
natural-language attributions of historical equity moves — e.g.
"AAPL fell 4% on 2026-02-14 because of [X]." A causal labeler for past price
action, not a forecaster.

Built for the Bridgewater AI Hackathon.

## Phase

**Ingestion.** Team is pulling raw data into the HF repo. Modeling and
attribution come after coverage is acceptable.

## Data sources & ownership

| Source                              | Owner    | Notes |
|-------------------------------------|----------|-------|
| Yahoo Finance — prices & earnings   | teammate | Structured_Data/SNE/yahoo-finance-data/*.parquet |
| SEC filings (10-K/Q, 8-K, Form 4)   | teammate | Include Form 4 insider txns |
| Dated news headlines                | teammate | Event timestamps are the join key for attribution |
| FRED macro releases                 | teammate | CPI, NFP, GDP, etc. |
| 13F hedge-fund holdings             | Henry    | SEC EDGAR; quarterly, 45-day lag. Consider 13f.info for clean parses |
| Short-seller research               | Henry    | Scrape Scorpion, Hindenburg, Muddy Waters, Citron, Kerrisdale, Spruce Point (sites + X) |
| FDA calendar + approvals/CRLs       | Henry    | Biotech mover attribution; FDA.gov calendars + Drugs@FDA |
| FINRA short interest                | Henry    | Bi-monthly, free from FINRA; squeezes & short ramps |
| Index rebalances                    | Henry    | S&P, Russell, MSCI add/delete announcements — mechanical flow |
| FOMC speeches & minutes             | Henry    | Fed calendar + transcripts; rates-sensitive names |
| Credit spreads / CDS                | Henry    | FRED for IG/HY OAS; Markit CDX if accessible. Equity-credit divergence flags stress |

## Repository layout

- `test.py` — scratch script for verifying HF data access
- `.venv/` — local Python env (Windows)

(Update as ingestion modules land.)

## Data access

- HF repo: `BridgewaterAIHackathon/BW-AI-Hackathon` (**private**)
- Auth: run `huggingface-cli login` once, then pass `token=True` to `load_dataset`
- Example: `data_files="Structured_Data/SNE/yahoo-finance-data/stock_split_events.parquet"`
- Files sit directly under each source folder — **no `data/` subfolder** (unlike the public `defeatbeta/yahoo-finance-data` mirror)

## Conventions

- Ingestion writes parquet (not CSV) for anything non-trivial
- Timestamps in UTC, ISO-8601
- One script per source; keep ingestion decoupled from modeling
- Don't commit HF tokens or raw bulk data — stage to the HF repo instead

## Commands

```bash
python test.py                         # smoke-test HF access
huggingface-cli login                  # one-time auth
pip install datasets pandas torch numpy pyarrow
```