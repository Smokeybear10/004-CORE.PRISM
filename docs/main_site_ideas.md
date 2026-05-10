# PRISM main site · functionality roadmap

What's already on the page (as of 2026-05-10):

- **Ticker pills** — masthead-level switch between ABT / ACU / AIR / AMD / APD
- **5-year price chart** with flagged 3σ dots, direction filter, ablation overlay
- **Verdict mini** — Lean / Fade / Skip + active strategy
- **Prism canvas** — event card → beam SVG → 5 dimension cards
- **Sources / Evidence tabs** — per-stream toggles, restricted-set re-attribution, cited evidence drawer
- **Strategies card** — three mappings with explainers
- **PnL strip** — model vs 4 baselines, 5-day forward returns
- **Move navigator** — prev / next + counter, ←/→ keyboard, URL hash permalinks
- **Eval headline strip** — collapsible ground-truth cases
- **About page** — method, pipeline, module graph, source taxonomy

What follows is a sequenced backlog. Each item names the artifact, the demo value, and a rough effort estimate. Pick by combining "cheap + high signal."

---

## Tier 1 · quick wins (≤ 1 hour each, high demo signal)

### 1. Hover tooltip on flagged dots
**What.** Hover any flagged dot on the chart and see a 4-line preview: date · return · dominant dimension · Lean / Fade / Skip.
**Why.** Right now the user has to click to see anything. Hover preview lets a judge scan all 18 events in 5 seconds.
**Effort.** Plotly `hovertemplate` + a custom `customdata` blob. Maybe 30 min.

### 2. Color flagged dots by dominant dimension
**What.** Replace the green/red dot color with the dominant-dimension color (red / orange / gold / blue / navy). Direction stays encoded by ▲ / ▼ symbol.
**Why.** Judge looks at the chart and immediately sees "AMD's 2022 selloff was macro-driven; the 2023 rally was demand-driven."
**Effort.** Marker color array on the chart trace. Maybe 30 min.

### 3. Top-citation strip under the verdict
**What.** Pull the single highest-weighted cited quote into a strip directly under the Lean. / Fade. word. One sentence, italics, with `[chunk_id]` after.
**Why.** Currently the user has to scroll into the dim cards or open the Evidence tab to see anything cited. Surface the strongest quote immediately.
**Effort.** Read the highest-weight dim's first citation. ~45 min.

### 4. Cache hit vs live miss badge
**What.** Replace the static `cached · attribution gate ✓` foot meta with a per-attribution badge: `cache hit · 2 ms` vs `live · 712 ms · ~$0.78`.
**Why.** Demonstrates the cost-control story (a key research-engineering claim: zero-spend live demos).
**Effort.** Server already returns timing; expose a header. ~30 min.

### 5. "Copy permalink" button
**What.** Visible button next to the move navigator: copies `prism.app/#AMD/2022-04-15` to clipboard. Hash already syncs; this just makes it discoverable.
**Why.** Judges share verdicts in writeups and Slack, not just by clicking around.
**Effort.** ~10 min.

### 6. Verdict-character pill on the verdict mini
**What.** A small pill below the strat line: `STRUCTURAL` / `TRANSIENT` / `MIXED` / `UNCLEAR`. Already in the Attribution; not surfaced.
**Why.** Move character is the underlying classification; verdict is its mapping. Showing both makes the strategy comparison legible.
**Effort.** ~15 min.

---

## Tier 2 · mid-effort, high payoff (1–3 hours)

### 7. Ablation diff strip
**What.** A horizontal strip of 7 mini-chips, one per ablation bundle (`base_news` → `+sec` → `+earnings` → … → `+positioning`). Each chip shows verdict + top-driving dimension under that information set. Highlight the active toggle state.
**Why.** This is *the* additive-testing story the mentor called "demo gold." Right now it's invisible — toggling sources changes the live attribution but doesn't show the trajectory.
**Effort.** Bundle already has pre-baked ablations per move (`bundle.moves[i].ablations`). Render 7 cards. ~2 hours.

### 8. Coherence-pass diagnostic
**What.** A small expandable panel under the dim cards: "Coherence audit" → which dimensions were dropped, kept, or rewritten by the Haiku second pass.
**Why.** Anti-hallucination is a non-trivial claim. Showing it visibly — "macro dimension dropped: rationale didn't match cited chunks" — turns it from a footnote into a defensible feature.
**Effort.** Bundle attribution has coherence metadata if exposed. ~2 hours.

### 9. Cross-ticker date scan
**What.** "Show this date across all tickers." Picks one calendar date (e.g. 2022-09-13 — CPI day) and renders a 5-up grid of mini attributions, one per ticker. Same dimensional palette so common drivers (macro on a CPI day, competitive on a chip-tariff day) are visible.
**Why.** Shows the system isn't just per-ticker — it's a research surface for cross-sectional questions.
**Effort.** ~3 hours. Layout work + a new data join.

### 10. Sentiment baseline overlay
**What.** Add a thin gray dotted line to the chart: VADER-equivalent scalar sentiment over time. Marker color shows agreement / disagreement with PRISM's verdict.
**Why.** Mentor explicitly asked for sentiment as a baseline. Showing the *visual* gap between sentiment and structured attribution is the strongest version of "we beat sentiment."
**Effort.** ~2 hours assuming a `sentiment_score` field is in or near the bundle.

### 11. Per-dimension time-series chart
**What.** A small chart at the bottom of the prism canvas: dimension weights over time (one stacked area per dimension). Click a date → focuses that move.
**Why.** Decomposition isn't just point-in-time; it evolves. Showing demand fade and macro spike during a recession is the kind of visual that wins prizes.
**Effort.** ~3 hours.

---

## Tier 3 · stretch / showpiece (half-day or more)

### 12. Two-up move comparison
**What.** Pin a move as "comparison anchor." Pick a second move. Side-by-side: same prism canvas, same evidence panel, same verdict block. Shared y-axis so dimension weights compare directly.
**Why.** Why was Q1 earnings a structural beat but Q2 earnings a fade? PRISM should help judges *answer* that, not just classify each in isolation.
**Effort.** ~half day. Significant layout work.

### 13. Live "explain why this verdict changed" annotation
**What.** When the user toggles a source off, fade-in text under the verdict: "Removing 13F flipped Lean → Fade because the macro dimension lost 0.18 weight." A diff explainer.
**Why.** The ablation surface goes from "watch numbers move" to "watch the model reason out loud."
**Effort.** ~half day. Requires diffing two attributions and templating.

### 14. Annotation / ground-truth capture
**What.** A small "I agree / I disagree / unsure" trio under each verdict. Saves to `localStorage`. Aggregate counts shown in a tiny badge on the eval strip.
**Why.** Lets a judge generate ground-truth cases by clicking through. Doubles as a feedback loop for the team.
**Effort.** ~half day. Pure frontend.

### 15. Outlier callouts on the chart
**What.** Chart annotations like "biggest call" (model's largest correct contrarian fade) and "biggest miss" (largest wrong-direction). Curated, hand-labeled.
**Why.** Address the mentor's "include a failure case" ask explicitly. Makes the demo feel honest.
**Effort.** ~3 hours. Mostly content + a Plotly annotation pass.

### 16. Print / PDF clean view
**What.** A `?print=1` URL flag that hides the chrome and renders a 1-page report: chart + prism + dim summaries + PnL. Print stylesheet collapses to 8.5×11.
**Why.** Judges who write up findings want a clean artifact to paste into a doc. Better than screenshots.
**Effort.** ~3 hours of CSS print rules.

---

## Tier 4 · polish details (10–30 min each)

These don't add core functionality but tighten the demo:

- **Loading skeletons** — replace the "—" placeholders with shimmer skeletons during fetch
- **Smooth transitions** between moves (cross-fade the dim cards instead of hard-swap)
- **Dim-card click → highlight evidence group** in the Evidence tab
- **Sticky verdict mini** — pin to top on scroll so the verdict stays visible while reading dim cards
- **Compact mode** — single keyboard shortcut (`c`) collapses the chart card so the prism + PnL fit one viewport
- **Sound off / on** — *optional*, a single soft tone on Lean / Fade / Skip flip (most judges will hate this)
- **Ticker switcher search** — type-ahead so adding more tickers later doesn't break the masthead
- **Tooltip on dimension labels** — hover "Demand" → see the persistence prior `+0.85` and what it means
- **Zero-state empty graphics** — when no move is selected, show a faint Plate-I prism instead of "—"
- **Mobile pass** — the page is desktop-first; one tight pass to make it usable at iPad width

---

## Tier 5 · backend / infra reach

Bigger lifts that change what the page can show:

- **More tickers** — extend the pre-bake to 20-30 names so judges can stress-test
- **Live attribution endpoint exposure** — let users type in a ticker that isn't pre-baked (cost: $0.75 / move on Opus)
- **Per-judge API key surface** — let a judge plug in their own Anthropic key to attribute fresh moves without billing the team
- **WebSocket live mode** — stream the model's tool_use call token-by-token so the dim cards fill in real time. High demo wow.
- **Eval harness UI** — clickable list of every ground-truth case with side-by-side expected vs actual

---

## Decision principle

If picking 3 items for the next push, the highest leverage combo is **#1 + #2 + #7** — hover preview, dimension-colored dots, ablation diff strip. Together they take the chart from "passive timeline" to "scannable research surface" and make the additive-testing story visual instead of buried in toggles.

If picking just 1, **#7 (ablation diff strip)** is the single feature that turns the mentor's "demo gold" framing into something a judge sees in 3 seconds.
