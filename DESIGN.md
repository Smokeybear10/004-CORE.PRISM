# Design — Price Action Tagger demo

The brief: this is a financial historian, not a fintech dashboard. The page should
read like a Bloomberg Businessweek piece grafted onto a Bloomberg terminal —
editorial typography, generous whitespace, one bold accent, and the lean/fade
verdict as the visual climax.

## Direction: "Bloomberg Editorial"

Financial heritage typography (display serif for the moneyshots, mono for tabular
numbers, sans for everything else) over a deep cool-dark canvas. Amber accent for
the brand and the active state — that's the only "color" on the page besides the
greens and reds of the actual data.

## Tokens

```
--bg              #0a0b10        deep, slightly indigo-undertoned
--surface         #12141b        card
--surface-2       #181b24        nested chip
--surface-3       #232733        active chip / hover

--ink             #f1ece1        warm off-white body text
--ink-muted       #8b8e98        labels, kickers
--ink-dim         #585c66        timestamps, footers

--accent          #f5b441        amber gold (single brand color)
--accent-soft     rgba(245,180,65,0.10)
--accent-line     rgba(245,180,65,0.40)

--up              #5fc88f        warm green, slightly desaturated
--down            #ef5a4d        warm red

--rule            #232733        hairline borders
--rule-strong     #2f3441

--shadow-card     0 2px 0 rgba(255,255,255,0.02), 0 14px 40px rgba(0,0,0,0.55)
--shadow-glow     0 0 0 1px var(--accent-line), 0 0 32px rgba(245,180,65,0.12)
```

## Type stack

- **Display:** `Newsreader` (Google Fonts) — serif, optical-size aware. Used for the
  brand wordmark, ticker title, KPI numbers, and the giant verdict word.
- **Sans:** `Inter` — UI, labels, body.
- **Mono:** `JetBrains Mono` — tabular numbers, chunk_ids, captions.

Sizing reference:
- Verdict word ("LEAN" / "FADE" / "SKIP"): 96px display serif, weight 600
- Ticker title (e.g., "AMD"): 56px display serif, weight 500, italic feels editorial
- KPI numbers: 32px display serif, weight 500
- Brand wordmark "LEAN/FADE.": 22px display serif, weight 600, with a 0.78rem mono
  kicker underneath ("PRICE ACTION TAGGER")

## Layout

Top bar (sticky, blurred): wordmark left, ticker pills right.

Main column (max 1180px, generous gutters):

1. **Overview** — ticker name in giant display serif + sector kicker · trading-day
   stats right-aligned in mono.
2. **Chart card** — soft gradient area fill under the price line. Dots stay punchy.
3. **Verdict Console** (new) — the climax of the page. Two stacked rows:
   - Top: 4 strategy cards as a tight horizontal row, each showing strategy name +
     its inline verdict (lean/fade/skip) in mono.
   - Bottom: huge centered verdict word for the active strategy in display serif,
     plus a subtitle showing predicted vs realized as a delta.
4. **Sources** — compact chip strip, smaller than before. Now a footnote-y row, not
   a hero.
5. **Attribution** — KPI quad (display-serif numbers), dimension bars, evidence
   list.

## What's removed

- The `◤` ASCII glyph brand mark. Wordmark replaces it.
- The standalone "char-pill" (structural / transient) at the top of the attribution
  card. Character now lives inside the KPI quad as one of four KPIs, and inside the
  verdict console subtitle.
- The toggle-row title heading "Sources feeding the model" — collapses to a single
  row caption since sources are visually demoted.

## Motion

- Verdict word fades + slides 6px on strategy change (180ms ease-out).
- Dimension bars animate width on initial render only.
- Strategy cards have a soft press scale (0.99) on click.

## What this lifts

- The page now answers the demo's core question — "lean or fade?" — at a glance.
  Right now you have to read four KPIs and a character pill to figure that out.
- Editorial type stack signals "research", not "trading dashboard." Matches the
  "financial historian" framing in CLAUDE.md.
- Single accent color forces hierarchy. Everything that's amber is interactive.
  Everything green/red is data.
