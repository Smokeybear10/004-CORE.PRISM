# Demo UI Guide

Walk through every element of the Streamlit demo at `demo/app.py`. This is what a
viewer (teammate, mentor, judge) sees and what each part means.

## What the site does, in one paragraph

Pick a stock ticker from the sidebar. See its price chart with significant
price moves highlighted as red (down) or green (up) dots. Click a move to see
how the attribution model decomposed *why* that move happened — across five
dimensions, with supporting evidence chunks from SEC filings and news — plus
a **lean vs. fade** verdict on whether the move will persist or revert.

## Running it

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...         # for live attribution
streamlit run demo/app.py
```

Opens at `http://localhost:8501`. If no API key is set, the UI still runs — it
falls back to synthetic attribution and flags that in the source badge.

---

## Screen walkthrough (top to bottom)

### Sidebar

| Element | Meaning |
|---|---|
| Title: **Price Action Tagger** | App name. Subtitle calls out the core claim: "Structural vs transient — attribution-driven." |
| **Ticker** dropdown | One of the 5 focal tickers (ABT, ACU, AIR, AMD, APD). Changing this reloads everything below. |
| Sector caption | Metadata about the selected ticker (e.g. "Sector: **Semiconductors**"). |
| **Window** slider | How many days of history to show on the price chart (default ~1 year). |
| **Ablation** dropdown | Which data-source mix to feed the attribution: `base_news`, `+sec`, `+earnings`, `+peer_news`, `+sector`, `+macro`. Each adds a source on top of the previous. |
| **Use mock data** checkbox | Forces the synthetic path, bypassing the Claude API call. A yellow warning appears when on. |

### Page header

```
AMD · Advanced Micro Devices, Inc.
```
Ticker, separator (`·`), company name.

### Price chart ("Price with flagged moves")

An interactive Plotly chart.

- **Blue line** — daily close price.
- **Red dot** (🔴) — a flagged significant move with negative return.
- **Green dot** (🟢) — a flagged significant move with positive return.
- **Hover** — shows the date, close price, return %, and volatility z-score.

A move is flagged as "significant" by Srilekha's `detect_significant_moves()`
when either:
1. `|return| > 2 × trailing 30-day realized vol`, OR
2. `|return|` is in the top 5% of the trailing 60-day absolute-return distribution.

Both lookback windows end the day before the move — no peeking at future data.

### Summary metric row (three cards below the chart)

| Card | Meaning |
|---|---|
| **Trading days shown** | How many daily bars are in the chart window. |
| **Flagged moves in window** | Count of red + green dots. |
| **Down / Up** — `N ↓ / N ↑` | How many of the flagged moves were negative vs positive. |

### Move selector ("Pick a flagged move to inspect")

A dropdown with one entry per flagged move, formatted:

```
2024-03-04 · -2.54% · z=-2.78
```

- **Date** — when the move happened.
- **Return %** — the day's close-to-close price change (sign tells direction).
- **z=X** — the volatility z-score. `z=-2.78` means the move was 2.78 standard
  deviations below this stock's normal daily move. Larger `|z|` = more anomalous.

The default selection is the **largest-magnitude move** in the window.

---

## Attribution panel

This is the main event. Everything below the move selector is the model's
explanation of the selected move.

### Source badge

A caption right under the "Attribution" header:

- `Attribution source: **live**` — Claude API was just called and returned a real answer.
- `Attribution source: **live (cached)**` — Claude was called earlier this session; answer served from Streamlit's cache.
- `Attribution source: **mock fallback**` — the synthetic fallback fired. Either the API key is missing, the call failed, or the mock toggle is on.
- `Attribution source: **mock fallback — [error message]**` — as above, with the reason spelled out.

**This badge exists for demo honesty.** A viewer should always be able to tell
whether they're looking at a real model output or a synthetic one.

### Four metric cards

```
┌ Realized ──┬ Predicted ─┬ Character ──┬ Confidence ──┐
│  -5.20%    │  -2.80%    │  transient  │  78%         │
│            │  +2.40% gap│             │  ✓ plausible │
└────────────┴────────────┴─────────────┴──────────────┘
```

| Card | Meaning |
|---|---|
| **Realized** | The actual price return on the flagged day (ground truth). |
| **Predicted** | What the attribution model *thinks* the return should have been given the text evidence. The delta line shows `predicted − realized`, i.e. the gap. |
| **Character** | One of `structural`, `transient`, `mixed`, `unclear`. This is the single bit that drives the lean vs fade call (see below). |
| **Confidence** | The model's self-reported confidence (0–100%). Below it: the **coherence badge**. |

**Coherence badge** sits under the Confidence metric:
- `✓ plausible` — the coherence check passed.
- `⚠ [first issue]` — coherence flagged a problem (e.g. "crude oil cited as driver for Apple"). Full list appears at the bottom of the page if any issues fire.

### The `Character` label — structural vs transient

This is the project's money-shot classification. It's the single label that
turns attribution into a trade.

- **`structural`** → the move reflects a real, durable change in the business
  (demand shift, competitive crack, credibility damage, genuine macro exposure).
  The market is right. **Lean** (bet the move continues).
- **`transient`** → the move is overreaction or noise. Thin evidence, one-time
  factors, vague macro handwave. The market overreacted. **Fade** (bet on reversion).
- **`mixed`** — partly real, partly noise. No clean signal either direction.
- **`unclear`** — not enough evidence to call it.

### Dimension weights bar chart

Five bars — one per attribution dimension — showing how much of the move
each dimension drove. Weights sum to 1.0.

| Dimension | What it captures |
|---|---|
| **demand** | Unit volume, customer count, market share shifts |
| **pricing** | Price changes, mix, discounting |
| **competitive** | New entrants, competitor moves, moat erosion |
| **management_credibility** | Guidance changes, execution, leadership tone |
| **macro** | Rates, FX, commodities, geopolitics |

### Rationale + evidence (per-dimension expanders)

Below the bar chart, one expander per dimension, sorted by weight descending.
Expanders with weight ≥ 0.25 are open by default.

Each expander header reads:

```
**demand** · weight 0.45 · ↓ negative
```

- **Name** — the dimension.
- **Weight** — 0.0 to 1.0, this dimension's share of the attribution.
- **Arrow + direction**:
  - `↑ positive` — supports the move going up
  - `↓ negative` — supports the move going down
  - `→ neutral` — this dimension didn't drive the move

Inside each expander:

1. **Rationale** — one-sentence explanation of the model's reasoning.
2. **Cited evidence** — a list of chunks supporting this dimension. For each chunk:
   - `` `sec_10k_AMD_2024-11-01_mda_003` `` — the stable chunk ID (monospaced).
   - **`sec_10k`** — the source type (`sec_10k`, `sec_8k`, `news`, etc.).
   - `2024-11-01` — publication date.
   - `mda` — section name (MD&A, risk factors, article, etc.).
   - The chunk's text snippet (up to 500 characters).
   - `[source]` — a link to the original filing or article URL when available.

If a cited chunk ID doesn't resolve to a real chunk (hallucinated citation),
a red error appears in place of the quote. This enforces **CLAUDE.md rule #6**:
every dimension must cite at least one real `chunk_id`.

### Model notes (blue info box)

If the model attached free-form commentary (e.g. "guidance language more
defensive than prior quarter"), it appears in a blue info box below the
dimensions.

### Coherence flags section (only when issues fire)

If `check_coherence()` finds plausibility problems (e.g., macro cited without
macro exposure, contradictory dimensions), a **"Coherence flags"** section
appears at the bottom with one yellow warning per issue. If everything is
coherent, this section is hidden.

---

## The story the UI is telling

End-to-end, a viewer's path looks like:

1. Pick **AMD** from the sidebar.
2. See a price chart with a handful of red/green dots.
3. Click the biggest red dot: a -12% move on a 2024 date.
4. Read: "Realized: -12%. Predicted: -6%. Character: transient. Confidence: 71%."
5. See the dimension breakdown: 60% macro, 20% competitive, 20% other.
6. Click **macro** — sees a news article about a broad semi selloff, linked to a Fed speech.
7. Conclusion: "the move was largely macro, no company-specific trigger → likely to revert → **fade**."
8. Change the **Ablation** dropdown from `+macro` back to `base_news` and watch
   the attribution change. With less context, the model is uncertain; with
   macro context, the story sharpens.

---

## Data flow (how the page is powered)

| UI element | Powered by |
|---|---|
| Price chart + flagged moves | `ingestion.prices.load_prices` + `detect_significant_moves` (Srilekha) |
| Move selector | The `PriceMove` list from above |
| Attribution cards / dimensions | `demo.live_attribution.get_attribution` → `model.attribute` (Claude) with `demo.mock_data` fallback |
| Cited evidence | `chunks` list fetched alongside the attribution — currently from the demo's chunk pool, should be `ingestion.sec.get_filings_as_of` + `ingestion.earnings_news.get_news_as_of` in a real run |
| Coherence badge + flags | `model.check_coherence` (stub today, real check once model is implemented) |

Caching: Streamlit caches per `(ticker, move_date, ablation, mock_toggle)` so
clicking around the same move doesn't re-hit the API. Clear the browser cache
or restart the app to reset.

---

## Known limitations (be honest about these)

The frontend ships before every backend piece is real. These gaps exist today:

1. **Attribution source is often `mock fallback`.** Without an `ANTHROPIC_API_KEY`
   set, or when `model.attribute` still has placeholder logic, the UI shows
   synthetic numbers. The source badge makes this obvious — check it.

2. **Cited evidence today comes from a mock chunk pool.** The real `ingestion.sec`
   and `ingestion.earnings_news` modules produce clean TextChunks, but the
   Streamlit app doesn't call them yet. Swapping the mock `chunks_for(...)`
   for `get_filings_as_of()` + `get_news_as_of()` is the next frontend fix.

3. **Every dimension may cite the same chunks.** In the current placeholder,
   the top 5 input chunks are assigned to every dimension's `evidence_chunk_ids`.
   Real per-dimension citations land when `model.attribute` is a Claude call
   with structured output.

4. **Predicted-vs-realized convergence is partially fabricated.** The synthetic
   `generate_attribution` formula makes `predicted` converge to `realized` as
   more sources are added to the ablation. The pattern is baked in, not
   emergent, until the real model is wired up.

5. **`+earnings` and `+macro` ablations have no backing data yet.** Earnings
   transcripts exist in the bundle but no ingestion module wraps them; macro
   is entirely a stub. Those ablation runs will look similar to `+sec` until
   implemented.

None of these break the UI — they just mean the demo's story is partly
scaffolded. When each one is replaced with the real thing, the UI wiring
stays identical.

---

## Where to dig in next

- `demo/app.py` — the Streamlit page (this guide's subject).
- `demo/live_attribution.py` — routes `(move, ablation)` → attribution, live or mocked.
- `demo/mock_data.py` — the synthetic fallback. Contains the placeholder formulas.
- `model/__init__.py` — where the real `attribute(move, chunks, config)` and
  `check_coherence(attr)` should live. Placeholder today.
- `ingestion/sec/` — real SEC 10-K and 8-K chunks.
- `ingestion/earnings_news/` — real news chunks from the bundled dataset.
- `ingestion/prices/` — price panel, significant moves, forward returns.
