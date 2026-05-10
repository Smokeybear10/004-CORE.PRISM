# Handoff: PRISM Console v2 — Demo Redesign

## Overview

A complete visual redesign of the PRISM interactive demo (`demo/static/index.html` + `app.js`). The new design — **PRISM Console v2** — replaces the existing Bridgewater-style stacked layout with a centerpiece visual metaphor: **the prism**. One incoming price move (the "white light") refracts through a triangle into five colored beams, each landing on one of the five attribution dimensions (Demand, Pricing, Competitive, Management, Macro).

The redesign keeps the existing FastAPI backend **unchanged** — same endpoints, same payload shapes, same response contract. All the work is in the frontend.

## About the Design Files

The files in this bundle (`index_v2.html`, `styles_v2.css`, `app_v2.js`) are **production-quality design references created in HTML/CSS/vanilla JS**. They were authored to drop directly into the existing `demo/static/` folder alongside `index.html`, and they already wire to your real `/api/attribute` endpoint and `/data/{ticker}.json` bundles.

You have two options:

1. **Use them as-is** — drop the three files into `demo/static/`, optionally add a route to `demo/server.py` like `@app.get("/v2")` that returns `FileResponse(STATIC_DIR / "index_v2.html")`, and serve them alongside the existing demo. This is the fastest path; the code is already complete.
2. **Reimplement in a different framework** — if you'd rather rebuild this in React/Vue/Svelte, treat these files as a high-fidelity reference. The README below documents every layout, token, and interaction precisely enough to rebuild from scratch.

`reference_canvas.html` is the earlier exploration (two design variants on a side-by-side canvas) that led to v2 — useful as additional design context but not part of the implementation.

## Fidelity

**High-fidelity.** Pixel-exact colors, typography, spacing, and interactions. The HTML files render the final intended look and are wired to the real backend.

## Architecture

The demo is a single-page app with three regions stacked vertically:

```
┌─────────────────────────────────────────────────────────┐
│  Spectrum strip (5 colored bars — the brand)            │
│  Masthead: PRISM wordmark | ticker pills | live status  │
│  Strip: ticker name + stats + verdict mini              │
├─────────────────────────────────────────────────────────┤
│  Chart card (5-year tape, click flagged dots to focus)  │
├─────────────────────────────────────────────────────────┤
│  PRISM CANVAS (the centerpiece)                         │
│   ┌──────────┐  ╱╲   ┌────────────────────────────┐     │
│   │  Event   │─→ ╲╲─→│ Demand        ▌  weight   │     │
│   │  card    │ ╱   ╲ │ Pricing       ▌  weight   │     │
│   │ (incoming│ │ △ │→│ Competitive   ▌  weight   │     │
│   │  beam)   │ ╲   ╱ │ Management    ▌  weight   │     │
│   │          │  ╲╲ ─→│ Macro         ▌  weight   │     │
│   └──────────┘   ╲╱   └────────────────────────────┘    │
├─────────────────────────────────────────────────────────┤
│  Sources card (toggles)  │  Strategies card (verdicts) │
├─────────────────────────────────────────────────────────┤
│  Footer + spectrum strip                                │
└─────────────────────────────────────────────────────────┘
```

## Backend Contract (unchanged)

The frontend uses these exact endpoints — no server changes required:

| Method | Path | Purpose |
|---|---|---|
| GET | `/data/index.json` | List of available tickers + sectors |
| GET | `/data/{ticker}.json` | Pre-baked bundle: prices, moves, attribution, chunks, strategies |
| POST | `/api/attribute` | Live attribution under user-toggled source set |

`POST /api/attribute` request body:
```json
{
  "ticker": "AMD",
  "move_date": "2026-02-04",
  "return_pct": -0.1731,
  "vol_zscore": -5.27,
  "magnitude_rank": 0.99,
  "enabled_sources": ["news", "sec_8k", "earnings_transcript"]
}
```

Response shape used by the UI:
```json
{
  "attribution": {
    "demand":      { "weight": 0.42, "direction": "negative", "rationale": "...",
                     "evidence_chunk_ids": ["..."], "cited_evidence": [{ "chunk_id": "...", "quote": "...", "reasoning": "..." }] },
    "pricing":     { ... },
    "competitive": { ... },
    "management_credibility": { ... },
    "macro":       { ... },
    "return_pct":         -0.1731,
    "predicted_return_pct": -0.0890,
    "move_character":     "transient",
    "confidence":         0.78,
    "chunks_considered":  37,
    "sources_used":       ["news", "sec_8k"]
  },
  "chunks":            [ { "chunk_id": "...", "source_type": "news", "publication_date": "...", "text": "...", "source_url": "..." } ],
  "chunks_considered": 37,
  "chunks_available":  { "news": 22, "sec_8k": 4, "...": 0 },
  "enabled_sources":   ["news", "sec_8k"],
  "strategies":        { "fundamental_vs_nonfundamental": "fade", "expected_vs_realized": "fade", "hybrid": "fade" }
}
```

## Design Tokens

### Colors

```css
/* Paper — backgrounds */
--paper:     #fbfaf6;   /* primary surface (cards) */
--paper-2:   #f4f3ee;   /* secondary surface (toggle bg) */
--paper-3:   #e8e6dd;   /* tertiary */
--rule:      #dcd9cf;   /* hairline borders */
--rule-2:    #b8b4a6;   /* hover borders */
body bg:     #efece4;   /* page bg behind cards */

/* Ink — text */
--ink:       #0d1320;   /* primary text */
--ink-2:     #2c3344;   /* secondary text */
--ink-q:     #6a6e7c;   /* quiet text */
--ink-dim:   #989384;   /* placeholder / idle */

/* Brand accents */
--navy:      #0a1d36;   /* masthead, headlines */
--oxblood:   #7a1f1f;   /* eyebrow labels, predicted overlay */
--gold:      #a87c3d;

/* Semantic */
--up:        #2e6f48;   /* positive return, lean verdict */
--down:      #8c2f2f;   /* negative return, fade verdict */

/* THE FIVE DIMENSION COLORS — these ARE the brand */
--d-demand:      #C8442C;   /* red */
--d-pricing:     #E89B4A;   /* orange */
--d-competitive: #D4B85A;   /* gold */
--d-mgmt:        #5A8DA8;   /* blue */
--d-macro:       #3D4A6B;   /* navy */
```

### Typography

```css
--serif: 'Source Serif 4', 'Iowan Old Style', Georgia, serif;
--sans:  'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
--mono:  'JetBrains Mono', ui-monospace, monospace;
```

Type scale (key items):
- Wordmark: serif 700 22px, letter-spacing 0.18em, uppercase, navy
- Ticker H1: serif 500 36px, letter-spacing −0.015em
- Card titles: serif 500 14–16px
- Verdict mini: italic serif 500 56px (the giant "Lean."/"Fade."/"Skip." word)
- Beam-card name: serif 500 16px
- Beam-card weight: serif 500 32px
- Eyebrow labels: mono 600 9.5–10px, letter-spacing 0.18–0.24em, uppercase
- Mono numbers throughout: 'tnum' 1 (tabular figures)

### Spacing & radii

- Container max-width: `1320px`
- Card padding: `18px 22px`
- Card border-radius: `--r-md: 6px`
- Small radius: `--r-sm: 4px`
- Card border: `1px solid var(--rule)`
- Card grid gap: `18px`

## Screens / Views

There is **one** view. Sections from top to bottom:

### 1. Spectrum strip (4px tall)
Five flex children in dimension-color order: red → orange → gold → blue → navy. Repeated at the very bottom of the page (3px there).

### 2. Masthead (sticky, blurred backdrop)
Three columns: brand block | ticker pills (centered) | status (right). 2px navy bottom border.
- **Brand**: `PRISM.` wordmark with the `.` in oxblood, plus an italic serif tagline "Persistence Reasoning over Idiosyncratic Stock Moves".
- **Ticker pills**: pill-shaped, mono symbol + italic serif sector label inside. Active = navy fill, white text.
- **Status**: ISO date + a green "live" indicator with a 1.6s pulsing dot.

### 3. Strip (ticker title + stats + verdict)
Three columns: ticker name block | KPI stats (Days / Flagged moves / Split ↓/↑) | verdict-mini.
- **Verdict mini** is the dominant element here — a 56px italic serif word ("Lean.", "Fade.", "Skip.") color-coded by verdict (green/red/quiet), with the active strategy name in italic underneath.

### 4. Chart card
Plotly chart, 280px tall, on cream paper. Shows close price line + flagged moves as colored dots (green for rallies, red for selloffs). When sources are enabled, a dashed oxblood line shows the model's predicted close path, and diamond markers mark the predicted level on each event date. Click any dot to focus that move.

### 5. The Prism Canvas (the centerpiece)
Three-column grid: `280px | 60px | 1fr`, min-height 520px.

**Left column — the incoming-move card.**
A bordered cream card displaying:
- Eyebrow: "INCOMING · PRICE ACTION" in oxblood mono
- Date in 22px serif
- Big return percentage (36px mono, color = green/red)
- 2-col grid of metadata: z-score, Volume z, Realized, Predicted, Gap, Character

**Middle column — the beam SVG.**
A vertical 60×520 viewBox SVG with `preserveAspectRatio="none"`:
- A horizontal navy bar enters from the left edge (the "incoming light")
- Hits a triangular prism (a navy-stroked polygon, near-transparent fill)
- Five colored beams shoot out from the prism's apex toward the right, each terminating at the y-center of its corresponding dim card
- **Beam thickness** scales with the dimension's weight (1.5px → 7px), so the user sees at a glance which evidence streams dominate

**Right column — five dim cards.**
A 5-row grid, gap 6px. Each card has:
- A 6px-wide left-border color stripe matching its dimension
- Three columns: name stack (130px) | body | stat (80px)
- Body shows a citation: italic serif quote (2-line clamp) + chunk_id in oxblood mono
- Stat shows the weight as a percentage (32px serif) and direction (`↑ pos` / `↓ neg` / `→ neu`)
- Hover: 3px translateX + soft shadow
- Idle (no attribution yet): opacity 0.55

Above the grid, a "canvas-meta" line shows: "N chunks · K/7 sources · confidence X%".

### 6. Bottom row (sources + strategies)
Two equal columns.

**Sources card (left):**
- Header: "Sources · ablate to see which streams matter" + "Reset to full stack" button (oxblood on hover)
- 4×2 grid of toggleable source pills (News, 10-K, 8-K, Earnings call, Peer news, Macro, 13F)
- Each toggle shows label + chunk count. Checked = oxblood border + tinted background. Disabled (0 chunks) = 40% opacity.
- Caption below: "[N/7] [K] chunks feeding attribution."

**Strategies card (right):**
- Header: "Strategies · fade · lean · skip"
- Three radio-style strategy rows: Fundamental vs Non / Expected vs Realized / Hybrid (3-stage). Each row shows the strategy name + a verdict tag (LEAN/FADE/SKIP, color-coded). Active strategy = oxblood name.
- Strategy explainer block beneath: two stacked panels — "How this strategy decides" (from STRATEGIES descriptions) and "What the model concluded" (a dynamically-built sentence describing the model's reading + the strategy's reasoning + the 5-day forward outcome).

### 7. Footer
2px navy top border, mono uppercase: "PRISM Console · v2 · live" on the left, "{N} chunks · attribution gate ✓" on the right. Followed by a 3px-tall repeat of the spectrum strip.

## Interactions & Behavior

| Trigger | Action |
|---|---|
| Click a ticker pill | Fetch `/data/{ticker}.json`, re-render everything, auto-select the largest-magnitude flagged move. |
| Click a flagged dot on the chart | Focus that move: re-render event card, dim cards, beams, strategies. |
| Toggle a source | Updates `STATE.enabledSources`, fires `POST /api/attribute` with new set, repaints attribution + chart overlay. |
| Click "Reset to full stack" | Re-enables every source that has chunks for the current move and re-paints from the pre-baked attribution. |
| Click a strategy row | Updates the verdict mini + strategy explainer; no network call (verdicts are cached per attribution). |

**Loading order on a move change:**
1. Render the **pre-baked** attribution from the bundle JSON immediately (fast first paint, no flash of empty state).
2. Fire the live `/api/attribute` POST in the background.
3. When it returns, replace the dim cards + beams + strategies with the live response (which has richer cited_evidence quotes).

A monotonic `STATE.fetchSeq` guards against out-of-order responses when the user toggles fast.

**Beam animation:** none currently. The beams just redraw on each attribution update. If you want to add motion: 200ms CSS opacity fade on the SVG element when re-rendering, or a stroke-dashoffset animation on first appearance.

**Pulsing live dot:** `@keyframes pulse` on the status indicator, 1.6s infinite.

## State Management

```js
STATE = {
  tickers,                    // [{ ticker, name, sector }]
  currentTicker,              // 'AMD'
  bundle,                     // /data/{ticker}.json payload
  selectedMoveIdx,            // index into bundle.moves
  enabledSources: Set,        // user's source toggle state
  lastFullStack,              // last live attribution response (used as ref)
  fetchSeq,                   // increments on each request, guards stale responses
  selectedStrategy,           // 'fundamental_vs_nonfundamental' | 'expected_vs_realized' | 'hybrid'
  lastStrategies,             // { strategy_id: 'lean' | 'fade' | 'neutral' }
  lastDims,                   // currently rendered dimension shape
  lastChunkMap,               // chunk_id → chunk
}
```

Source-availability rule: when a move is selected, only sources with `chunks_available[id] > 0` are auto-enabled; sources with zero chunks for that move start unchecked & disabled.

The `_COUNT_TO_BUNDLE` map (`{0–7: ablation_name}`) mirrors the backend exactly — it's used to pick the chart-overlay name based on how many sources are toggled on.

## Strategy verdict generation

The "What the model concluded" sentence is built from:
1. **Read sentence** — top 3 dimensions by weight, with their direction phrased in plain English ("demand growth (negative, 42%)"), then a comparison of realized vs predicted return.
2. **Reasoning sentence** — strategy-specific. Fundamental-vs-Non keys off `move_character`. Expected-vs-Realized keys off the ratio `|realized| / |predicted|` (≥1.5× = overreaction → fade; ≤0.5× = underreacted → lean; else neutral). Hybrid layers all three.
3. **Outcome sentence** — looks up the 5-day forward return from `bundle.prices` and reports whether the verdict would have paid off.

All of this logic is in `app_v2.js` — `buildModelReadSentence`, `buildStrategyReasoning`, `buildOutcomeSentence`, `buildVerdictConclusion`. Lift verbatim if reimplementing.

## Responsive Behavior

- `<1100px`: strip collapses to single column; prism-grid narrows; bottom row stacks; source grid → 3 columns.
- `<800px`: prism-grid → single column, beam SVG hidden; source grid → 2 columns.

## Files in this Bundle

- **`index_v2.html`** — the page skeleton. Drop into `demo/static/`.
- **`styles_v2.css`** — full stylesheet, ~620 lines. Drop into `demo/static/`.
- **`app_v2.js`** — full application logic, ~700 lines. Drop into `demo/static/`.
- **`reference_canvas.html`** — earlier two-variant exploration on a pan/zoom canvas. Reference only; not part of the implementation.

## Wiring into the existing demo

To serve v2 at a route like `/v2`:

```python
# demo/server.py — add near the bottom, BEFORE the static mount
@app.get("/v2")
def _index_v2() -> FileResponse:
    return FileResponse(STATIC_DIR / "index_v2.html")
```

Or just open it directly: with the static mount as-is, `http://127.0.0.1:2004/index_v2.html` already works.

The v1 demo at `/` stays untouched.

## Implementation Checklist for the Developer

- [ ] Drop `index_v2.html`, `styles_v2.css`, `app_v2.js` into `demo/static/`
- [ ] (Optional) Add the `/v2` route to `demo/server.py`
- [ ] Verify the page loads via the running uvicorn server (NOT a file-preview — fetches need the backend)
- [ ] Click through every ticker; ensure each renders without errors
- [ ] Toggle each source; confirm `/api/attribute` is being called with the right `enabled_sources`
- [ ] Confirm the dashed predicted line + diamond markers appear on the chart when at least one source is enabled
- [ ] Confirm the prism beams thicken proportionally to dimension weights
- [ ] Confirm strategy verdict pills update when sources change, and the explainer text rewrites
- [ ] Test with a ticker that has zero attribution (e.g. force `enabled_sources: []`) — the zero-warning panel should appear and beams/cards should go to idle state
- [ ] Test the "Reset to full stack" button restores the originally-available source set

## Notes

- The Plotly version pinned in the HTML (`plotly-2.35.2.min.js`) matches what the v1 demo uses — feel free to upgrade.
- The CSS uses Google Fonts (Inter, JetBrains Mono, Source Serif 4). If your environment is offline, self-host them.
- All numbers in the UI use tabular figures (`font-feature-settings: 'tnum' 1` on body) so columns align.
- The five dimension colors are intentional and load-bearing — don't recolor them; they're the brand. They're used in: top/bottom spectrum strips, beam-card left borders, beam strokes in the SVG.
