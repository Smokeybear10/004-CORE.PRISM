# Hackathon: Equity Price Action Tagger — Lean or Fade?

## Project goal (read this first)
Given a significant stock price move, extract a structured attribution from surrounding
text (news, SEC filings, earnings calls, macro context, peer news) across five
dimensions: demand, pricing, competitive, management credibility, macro. Then predict
an expected move and decide lean-or-fade vs. the realized move.

**The deliverable is a clickable demo of the pipeline's reasoning, plus ablation
results, not a trading product.** Scope accordingly.

## The six-step pipeline (mentor framework)
Every module should be a pure function with a clear input/output type so we can
parallelize and swap implementations with fixtures.

1. **Identify significant price moves.** `ingestion/prices/` — input: ticker; output: flagged dates where |return| is large relative to trailing vol AND/OR volume spike.
2. **Ingest news / filings / earnings.** `ingestion/sec/`, `ingestion/earnings_news/` — input: ticker + date range; output: `list[TextChunk]` per source type.
3. **Attribute moves to text.** `model/` — input: PriceMove + chunks; output: `Attribution` across the 5 dimensions.
4. **Add macro / peer / sector drivers.** `ingestion/macro/` + peer-ticker news; additive ablation. Mentor: "if an energy company moves on day X, maybe it's not the news article, maybe the Suez Canal closed."
5. **Evaluate coherence.** `model/check_coherence` — is the reasoning plausible? Catches "crude oil moved Apple" before it reaches backtest.
6. **Fade-or-follow framework.** `backtest/` — compare predicted return vs realized. Keep simple: lean on structural, fade on transient.

## The three strategic messages (from mentor meeting)
1. **Sprint to a crappy end-to-end MVP before polishing anything.** Don't perfect Step 1 for 5 hours. Get AAPL end-to-end first, then iterate.
2. **Demo the thought process and ablations, not just the final result.** The demo sells "here's what happens when we add 10-Ks, then peer news, then macro." Each addition is a talking point.
3. **Put energy into data-source additive testing + interpretability, not into a sophisticated trading strategy.** Lean/fade logic should be sensible, not clever.

## MVP scope (hour 0 → next mentor check-in)
- **Universe:** one company (AAPL recommended — COVID drop is a known event with obvious attribution, so we can spot-check the model). Expand to 5 after MVP works.
- **History:** 2–5 years. Not 20. Longer burns compute without adding MVP value.
- **Sources for MVP:** company news + SEC 10-K/10-Q. Add peer/macro as ablations *after* MVP is end-to-end.
- **Caching:** always. Write fetched data to `*/\.cache/` (gitignored). Re-running the pipeline must not re-hit APIs.

## Non-negotiable rules
1. **No foreknowledge leak.** Every retrieval function takes an `as_of` date and MUST filter by `publication_date <= as_of`. Mentor note: model foreknowledge (trained on post-event journalism) is harder than data foreknowledge — don't rabbit-hole on it, but always make sure you're feeding dates that match actual publication dates.
2. **Filing date != period end.** A 10-K for FY ending Dec 31 might be filed in March. Market reacts on filing date. Store both; as-of queries use `publication_date`.
3. **Stable chunk IDs.** Format is locked: `{source_type}_{ticker}_{YYYY-MM-DD}_{section}_{NNN}`. Citations reference these.
4. **Fixtures before code.** Write a fixture matching the schema before implementing a fetcher. Downstream modules build against fixtures.
5. **One schema, enforced.** Use the Pydantic models in `schema.py`. No loose dicts crossing module boundaries. Post in team chat BEFORE editing `schema.py`.

## The attribution dimensions (5, fixed)
- `demand` — unit volume, customer count, market share shifts
- `pricing` — price changes, mix, discounting
- `competitive` — new entrants, competitor moves, moats
- `management_credibility` — guidance changes, execution, leadership comments
- `macro` — rates, FX, commodities, geopolitics (evidence can come from MACRO / SECTOR / PEER_NEWS chunks)

## Frozen test case (use for all prompt iteration)
Canonical: **AAPL, March 2020 COVID drop.**
- Expected dominant dimension: `macro` (negative).
- Expected `move_character`: `mixed` or `transient` — market recovered by summer.
- Use this case while iterating prompts. If a prompt change breaks AAPL-COVID attribution, revert. Don't swap the test case mid-iteration — you lose the ability to isolate what changed.
- See `tests/fixtures/aapl_march2020_expected.json` for the exact expected output contract.

## Ablation configs (the demo goldmine)
Mentor emphasized additive testing. Build these runs and compare side-by-side in `demo/`:
1. `base_news` — only company-specific news.
2. `+sec` — add 10-K / 10-Q text.
3. `+earnings` — add earnings-call transcripts.
4. `+peer_news` — add news about peer tickers (reuse news fetcher with different tickers — cheap).
5. `+sector` — sector-wide stories.
6. `+macro` — Fed, commodities, geopolitics.

Each step is a bar on the demo chart: hit rate / plausibility / Sharpe as we add sources. "We found that 10-K language is the biggest driver, peer news adds 15% more, WSJ sentiment was basically noise" is the demo money quote.

## Workflow
- Each person works on their own git branch. Current split:
  - `person1-sec` — Sophia — SEC ingestion
  - `person2-yahoo` — Srilekha — Yahoo Finance / prices + news
  - `person3-research` — Henry — 13F + macro + open research
  - `person4-news` — [TBD] — news (WSJ / CNBC / general) + peer news
- Merge to `main` only after a teammate reviews the PR.
- `pytest tests/` must pass before any merge.
- If you touch `schema.py`, post in team chat BEFORE merging.
- While your piece is in progress, other people build against fixtures — don't block.

## Known traps
- Yahoo Finance has survivorship bias (delisted companies disappear). Note in demo.
- SEC filings can be 200+ pages. Chunk aggressively, keep MD&A and Risk Factors.
- The model will hallucinate attributions. Always require a valid `evidence_chunk_ids` citation; drop any DimensionScore without one.
- Paywalled news (WSJ, CNBC, Bloomberg) — mentor raised this. MVP: work from what's public (Yahoo Finance headlines, press releases). Note the limitation in demo.
- Don't refetch APIs on every run — cache to disk.

## Claude Code house rules
- When editing inside one module, do NOT touch other modules.
- Use `/freeze` to lock scope to the current directory when debugging.
- Respect the "one company, short history" MVP scope — don't quietly broaden it.
