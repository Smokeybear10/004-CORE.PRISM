# Hackathon: Equity Price Action Tagger — Lean or Fade?

## Project goal (read this first)
Given a significant stock price move, extract a structured attribution from surrounding text
(earnings transcripts, news, SEC filings) across five dimensions: demand, pricing,
competitive dynamics, management credibility, macro exposure. Then test whether
attribution category predicts whether the move persists (structural) or fades (transient).

**The deliverable is a backtest + a demo chart, not a product.** Scope accordingly.

## Repo layout — respect module boundaries
- `schema.py` — shared data contracts. Do NOT modify without team sign-off.
- `ingestion/sec/` — SEC 10-K / 10-Q filings (owner: [YOU])
- `ingestion/earnings_news/` — earnings transcripts + news articles (owner: Person 2)
- `model/` — price move detection + LLM attribution (owner: Person 3)
- `backtest/` — event study + signal evaluation (owner: Person 4)
- `demo/` — final notebook and charts (owner: Person 4)
- `tests/fixtures/` — sample data files all modules test against

**Claude Code: when editing inside one module, do NOT touch files in other modules.**
Use `/freeze` to lock scope to the current directory when debugging.

## Non-negotiable rules
1. **No foreknowledge.** Any function that retrieves text for a given date MUST filter by
   `publication_date <= as_of`. This is THE most common way hackathon projects accidentally
   leak future information into backtests. Every retrieval function must take an `as_of` parameter.
2. **Filing date != period end.** A 10-K for fiscal year ending Dec 31 might be filed in March.
   The market reacts on the filing date, not the period end. Store both; use `filing_date`
   for as-of queries.
3. **Stable IDs.** Every text chunk has a stable `chunk_id` so the model can cite evidence.
4. **Fixtures before code.** Before implementing a parser, write a fixture that matches the
   schema. Downstream teammates can build against fixtures while you build the real thing.
5. **One schema, enforced.** Use the Pydantic models in `schema.py`. No loose dicts crossing
   module boundaries.

## The attribution dimensions (used by the model)
- `demand` — unit volume, customer count, market share shifts
- `pricing` — price changes, mix, discounting
- `competitive` — new entrants, competitor moves, moats
- `management_credibility` — guidance changes, execution, leadership comments
- `macro` — rates, FX, commodities, geopolitics

## Definitions to lock in hour 1
- **Significant price move**: |1-day return| > 2x trailing 30-day realized vol, OR top 5%
  absolute return in trailing 60 days. Pick ONE and stick with it.
- **Fade window**: did the move reverse by >50% within 5 trading days?
- **Persist**: no reversal, or extension of the move, over 5 trading days.

## Workflow
- Each person works on their own git branch: `person1-sec`, `person2-earnings`, etc.
- Merge to `main` only after a teammate reviews the PR.
- `python -m pytest tests/` must pass before any merge.
- If you touch `schema.py`, post in team chat BEFORE merging.

## Known traps
- Yahoo Finance ticker data has survivorship bias — delisted companies disappear.
  For a hackathon this is fine; note it in the demo.
- SEC filings can be huge (200+ pages). Chunk aggressively, store only relevant sections.
- The model will hallucinate attributions. Always require it to cite a `chunk_id`; drop
  any attribution without a valid citation.
