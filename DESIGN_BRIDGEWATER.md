# DESIGN — for Bridgewater specifically

This doc is opinionated. It assumes the audience is Bridgewater (the firm, the
judges, anyone whose taste was shaped by Daily Observations and Ray Dalio's
Principles). The previous concept doc was "what looks great in general." This
one is "what would land at Westport."

## Field research: what does Bridgewater's design language actually look like?

**Their public website (bridgewater.com):**
- Deep navy hero (~`#0a1d36`), white body, generous whitespace
- Serif headers (similar to Tiempos / GT Sectra), sans body (similar to GT
  America / Söhne)
- Restrained imagery, no stock photography, minimal animation
- Tight grid, A4-feeling proportions
- One muted accent (oxblood / muted gold) used very sparingly

**Their published research ("Daily Observations"):**
- Institutional research note format. ~5–15 pages, A4 portrait
- Header masthead: "Bridgewater Daily Observations" with date
- Two-column body, drop caps, footnotes, charts inline
- Charts are 2–3 colors max (navy, gray, occasionally a warm accent)
- Conservative typography, no fashion fonts
- Footer: small mono "© Bridgewater Associates, LP"

**Ray Dalio's "Principles":**
- Cause → effect flowcharts (boxes + arrows)
- Numbered principles, hierarchical structure
- Black + red diagrams over off-white paper
- "The 5-Step Process" as a literal flowchart in chapter 2
- Pull quotes in serif, surrounded by white space

**Their flagship fund "Pure Alpha":**
- Quant rigor, transparent process, systematic. The brand promise is "we
  reason explicitly, we publish our reasoning, we're not a black box."

**What this implies for our demo:**
- Skip dark-mode-fintech entirely. BW would read "another AI hackathon kid
  designed this."
- Lean serif. Use a real text serif (Tiempos / Newsreader / Source Serif), not
  a display.
- Navy + cream + one muted accent. No amber-on-black.
- Make the *reasoning* the hero. BW values transparency. The page should
  expose, not hide, how the model arrived at its verdict.
- Show provenance. Every number traceable to a chunk_id, every chunk traceable
  to its source.
- Charts: 2–3 colors max, one accent for the focal event, rest in greyscale.

---

## Four concepts, each Bridgewater-coded

### Concept 1 — DAILY OBSERVATIONS

> Emulate the form factor of Bridgewater's published research. The page reads
> like one of their Daily Observations notes, but it's interactive.

**The pitch.** When a Bridgewater PM opens this page, they should feel like
they're reading a Daily Observation that happens to be about AMD on April 9.
A4-feeling, two-column body, drop caps, footnotes. The verdict is a *finding*,
not a stat block.

**Layout (top to bottom)**

```
┌────────────────────────────────────────────────────────────────────┐
│  BRIDGEWATER DAILY OBSERVATIONS  ·  HACKATHON BUILD                │
│  ──────────────────────────────────────────────────────────────    │
│  Vol. 1 · Issue 04 · April 9, 2025 · prepared by Claude (Anthropic)│
└────────────────────────────────────────────────────────────────────┘

         A reading of Advanced Micro Devices' single-day
         move on April 9, 2025: structural in character,
         overshooting fundamentals, fade in the medium term.

                            ──── ··· ────

   [chart, full-width, navy line, single oxblood dot at the focal date,
    annotated]

                            ──── ··· ────

[two-column body, drop cap on first paragraph]

T   he move under examination is a +23.82%   The framework we apply to this
H   single-day appreciation in AMD on        question is the same one we
    April 9, 2025, against a trailing-       use across the firm: structural
    30-day volatility consistent with a      vs. transient, expected vs.
    4.5σ event...                            realized, dimension-weighted...

   [continuation of body, footnotes, citation marks
    in margin like Tufte sidenotes]

                            ──── ··· ────

§ I. The Finding
§ II. The Frameworks Disagree
§ III. The Five Dimensions of Attribution
§ IV. The Input Bundle and What Was Excluded

[footer: small mono]
© Bridgewater Hackathon Build 2026 · prices via ingestion.prices · attribution
via model.attribute() · frameworks via backtest.signal.STRATEGY_REGISTRY
```

**Type stack.**
- Display + body serif: Tiempos Text + Tiempos Headline (paid). Free
  alternative: Source Serif 4 + Source Serif Display, or Newsreader.
- UI / labels: GT America / Söhne (paid). Free alternative: Inter or IBM Plex
  Sans at 0.78rem with 0.16em tracking.
- Mono: Berkeley Mono or JetBrains Mono — only for chunk_ids and footnote
  references.

**Palette.**
- bg `#fbf7ee` (warm paper-cream)
- ink `#0d1320` (near-black with navy undertone, not pure black)
- ink-secondary `#3c4458`
- ink-quiet `#7c8497`
- accent navy `#0a1d36` (the BW navy)
- accent oxblood `#7a1f1f` (used once per page, on the focal dot, the verdict
  underline, and active states)
- chart navy `#0a1d36`
- chart oxblood `#7a1f1f`
- chart gray `#9aa3b3`
- rule `#d8d2c2` (warm gray hairlines)

**Motion.**
Almost none. The page settles. Drop caps fade in over 200ms. Hover on a
chunk_id underlines it and reveals the chunk in the right margin (Tufte-style
sidenote, no popup).

**Standout component: the masthead.** A real publication masthead. "BRIDGEWATER
DAILY OBSERVATIONS · HACKATHON BUILD" set in 0.78rem mono with a hairline
underneath. Tells the judge what they're looking at before they read anything.

**Why this lands at Bridgewater specifically.**
1. They publish research. This *looks* like their research.
2. Light mode in finance reads as "I respect the form" instead of "I copied
   Robinhood."
3. The two-column treatment forces editorial discipline: every section is a
   real argument, not a card.

**Risks.**
- Light mode is fashion-risky in 2026. If a judge expects dark, they may read
  this as "wrong."
- Long-form reading on a small laptop screen hurts.
- Doesn't feel like a "tool" — feels like a document. Some judges want to see
  interactivity, not just information.

---

### Concept 2 — PRINCIPLES

> Lean into Ray Dalio's flowchart-of-everything aesthetic. Make
> cause-and-effect literally visible.

**The pitch.** Dalio's whole career is "I draw the system, then I let the
system make decisions." This concept treats every flagged move as a system
diagram. Sources connect via SVG arrows to the dimensions they inform.
Dimensions connect via arrows to the move character. The move character feeds
the strategy. The strategy emits the verdict.

You can SEE the reasoning chain. That's the whole game.

**Layout**

```
                    PRICE ACTION TAGGER · A PRINCIPLES VIEW

                  ┌─ INPUTS (live chunk pool) ─┐
                  │                            │
            news ──┐    10-K ──┐    8-K ──┐    13F ──┐
                   ▼           ▼          ▼          ▼
              ┌─── DIMENSIONS (5) ──────────────────────────┐
              │                                              │
              │   demand     pricing     competitive         │
              │     │            │             │             │
              │     ▼            ▼             ▼             │
              │   weight 0.31  weight 0.18  weight 0.27      │
              │   ↑ pos        ↓ neg        ↑ pos            │
              │                                              │
              │     management_credibility (0.06)             │
              │     macro (0.18)                              │
              └──────────────────────────────────────────────┘
                                   │
                                   ▼
                        ┌─ CHARACTER ─┐
                        │  STRUCTURAL │
                        │  conf 95%   │
                        └─────────────┘
                              │
                  ┌───────────┴───────────┐
                  ▼          ▼            ▼          ▼
              fundamental  exp/real   dim_wt      hybrid
                  │           │          │           │
                LEAN        FADE       SKIP        FADE
                              │
                              ▼
                       ┌─ VERDICT ─┐
                       │  FADE     │
                       │           │
                       │  (Hybrid) │
                       └───────────┘
```

The whole page is one diagram. Hover a source — its arrow lights up amber.
Hover a dimension — the arrows feeding it light up. Toggle a source off —
its arrows go gray and the downstream weights animate to their new values.

**Type stack.**
- Display: GT Sectra (paid) or Newsreader for the verdict word
- Body / labels: GT America / Söhne (paid). Free: Inter
- Mono: Berkeley / JetBrains — for chunk_ids and weights only

**Palette.**
- bg `#0d1320` (deep navy, near-black)
- panel `#141b2c`
- ink `#e8e6df` (warm off-white)
- ink-quiet `#76829a`
- accent navy `#3a5b8a` (lighter than bg, used for arrow flow)
- accent oxblood `#9c3a3a` (negative arrows / fade indicators)
- accent gold `#c89a4a` (active state, focal arrows)
- arrow rest `#2a3552`
- arrow live `#c89a4a`
- arrow positive `#3a5b8a` (dimension flowing positive)
- arrow negative `#9c3a3a`

**Motion.**
- Arrows draw on first render (stroke-dashoffset animation) over 1.4s, in
  layered order: inputs → dimensions → character → strategies → verdict.
- Toggle a source: its arrow gracefully fades to gray, downstream weights
  animate to new values.
- Click a strategy: the verdict pulses once and the contributing arrows
  light up.

**Standout component: the diagram itself.** This is the only interface I've
ever seen that makes the model's reasoning visually explicit. Judges will
remember it. Researchers will *want* it.

**Why this lands at Bridgewater specifically.**
1. Dalio literally publishes flowcharts. "How the Economic Machine Works" is
   a 30-minute video built entirely on this diagram language.
2. Bridgewater's whole pitch is "we make our reasoning explicit." This UI is
   the visual embodiment of that pitch.
3. It implicitly demonstrates the demo's thesis: structured attribution is a
   thing you can SEE, not just read.

**Risks.**
- Most ambitious of the four to build. The arrow routing logic alone is real
  work (~half a day to do well).
- If the diagram is busy, it stops being a feature and becomes noise.
- Low information density per pixel; you trade compactness for clarity.

---

### Concept 3 — PURE ALPHA

> Academic math-paper aesthetic. Black on near-white. LaTeX-coded. Looks like
> a Bridgewater quant research note that escaped onto the web.

**The pitch.** Pure Alpha is BW's flagship discretionary-systematic fund. The
name signals rigor. This concept owns that. The page reads like a NeurIPS
submission or an academic finance preprint. Inline equations, monospace
captions on figures, tables instead of cards, theorem-style framing.

**Layout**

```
══════════════════════════════════════════════════════════════════════
       Decomposing single-day equity moves into structured
       attribution: a tool-use approach to financial historiography.

                     C. Anthropic et al.
                      Bridgewater 2026


                              Abstract

  We propose a method for attributing significant single-day equity
  price moves across five fixed dimensions [demand, pricing, competit-
  ive, management, macro] using a structured tool-use call against
  Claude (Anthropic). We evaluate four fade-or-follow frameworks
  against the same attribution and report disagreement rates. The
  method requires no foreknowledge filter and respects publication
  date for every cited chunk.

══════════════════════════════════════════════════════════════════════

  1. Setup

  Let m ∈ M be a flagged move with realized return r_m ∈ ℝ.
  Let C(m) be the set of text chunks publishable by m.move_date.
  Let A(m, C) be the attribution emitted by model.attribute(m, C).

  Each A(m, C) is a 5-tuple of (weight, direction, rationale, cite)
  with weights summing to 1.0 and direction ∈ {pos, neg, neut}.

  ───────────────────────────────────────────────────────────────────

  2. Application: AMD on April 9, 2025

       ticker     │ AMD
       move_date  │ 2025-04-09
       r_m        │ +0.2382 (4.5σ)
       |C(m)|     │ 1306 chunks
       model      │ claude-haiku-4-5

       [chart, single-line plot, no fill, only data, axis labels in mono]

       Figure 1. AMD price 2021-04-25 → 2026-04-24. Focal event at right.

  ───────────────────────────────────────────────────────────────────

  3. Attribution

       i  │ dimension              │ weight │ direction │ cited
       ───┼────────────────────────┼────────┼───────────┼─────────
       1  │ demand                 │ 0.31   │ +         │ [1, 4]
       2  │ pricing                │ 0.18   │ −         │ [3]
       3  │ competitive            │ 0.27   │ +         │ [2]
       4  │ management_credibility │ 0.06   │ ·         │ [5]
       5  │ macro                  │ 0.18   │ ·         │ [7]
       ─────────────────────────────────────
                                        Σ 1.00

  4. Strategy verdicts

       framework               │ verdict │ note
       ────────────────────────┼─────────┼────────────────────────
       fundamental_vs_non      │ lean    │ character = structural
       expected_vs_realized    │ fade    │ |r| / |r_pred| = 1.74
       dimension_weighted      │ neutral │ score = 0.16 (mid-range)
       hybrid                  │ fade    │ structural ∧ overshoot

       finding: 2 of 4 frameworks fade. recommended action: fade.

  ───────────────────────────────────────────────────────────────────

  5. References

  [1] Q4 2024 results showed record revenue with data center +69% YoY...
      news_AMD_2025-04-03_article_0001
  [2] AI/EPYC narrative continues to position AMD favorably against
      incumbents in datacenter... sec_10k_AMD_2024-12-31_business_0002
  [3] KeyBanc downgrade flagged risk of price war with Intel pressur-
      ing AMD gross margins... news_AMD_2025-04-09_article_0003
  ...

═══════════════════════════════════════════════════════════════════════
```

**Type stack.**
- Body: Source Serif 4 (free) or Tiempos Text (paid)
- Mono: Berkeley Mono / JetBrains Mono — used heavily, for tables and figures
- No display font — body serif scales up for headers

**Palette.**
- bg `#fafaf7` (very-near-white, slight warm tint)
- ink `#0a0a0a`
- ink-secondary `#3a3a3a`
- ink-quiet `#787878`
- accent navy `#0a1d36` (used once on the masthead rule)
- chart line `#0a0a0a` (just black)
- chart focal `#7a1f1f` (one oxblood point per chart)
- rule `#d4d0c4`

**Motion.** None. This is paper.

**Standout component: footnoted citations.** Every chunk_id is a numbered
reference [1], [2], [3] inline. The "References" section at the bottom is the
real chunk text. Click [1] in the body, scroll to References. Ctrl-F works
properly. Reads like a real paper. The judge can grep the page in their
browser the same way they grep a PDF.

**Why this lands at Bridgewater specifically.**
1. BW publishes white papers. This *is* a white paper.
2. The math notation (`r_m`, `|C(m)|`, `Σ`) signals "we know what we're
   doing." It's the design equivalent of citing your sources properly in a
   research interview.
3. Light mode + serif + tabular numbers. Three signals that say "I am
   serious."

**Risks.**
- Driest of the four. Some judges want to feel something; this is engineered
  to give them nothing emotional, only respect.
- Hardest to demo in 3 minutes; the value is in reading, not interacting.
- Mobile is dead. Tables don't reflow.

---

### Concept 4 — WESTPORT

> Bridgewater is in Westport, CT. The aesthetic is quiet wealth, prep school,
> private banking. Not flashy fintech. This concept owns that.

**The pitch.** Imagine if a private bank built an internal research tool
specifically for the family-office crowd. Warm wood tones, navy + cream +
oxblood, generous spacing, tasteful weight. No animations, no neon, no
"let's-go-team" voice. Just quiet competence.

**Layout**

```
┌─ A. Lean / Fade ────────────────────────────────  ABT  ACU  AIR  AMD  APD ─┐
│                                                                            │
│                                                                            │
│         AMD                                                                │
│         Advanced Micro Devices                                             │
│         Technology · Large-Cap                                             │
│                                                                            │
│         5y price action through April 24, 2026 · 1,241 trading days        │
│         16 flagged events · 9 up · 7 down                                  │
│                                                                            │
│         ────────────────                                                   │
│                                                                            │
│         [chart, generous margins, navy line, oxblood focal dot, no fill]   │
│                                                                            │
│         ────────────────                                                   │
│                                                                            │
│                                                                            │
│         The April 9, 2025 reaction.                                        │
│         A reading.                                                         │
│                                                                            │
│         The framework reads this move as STRUCTURAL.                       │
│         Realized +23.82%. Predicted +13.73%. Gap +10.09 percentage points. │
│                                                                            │
│         Of the four trading frameworks under consideration, two read the   │
│         move as a fade (Expected vs Realized; Hybrid), one as a lean       │
│         (Fundamental vs Non-fundamental), and one as a hold (Dimension     │
│         Weighted).                                                         │
│                                                                            │
│             ┌──────────────────────────────────┐                           │
│             │  FUNDAMENTAL  EXPECTED  DIM-WT   HYBRID  │                   │
│             │     LEAN        FADE     HOLD    FADE    │                   │
│             └──────────────────────────────────┘                           │
│                                                                            │
│         ────────────────                                                   │
│                                                                            │
│         The five dimensions.                                               │
│                                                                            │
│         Demand (weight 0.31, positive). AMD's Q4 2024 results showed       │
│         record revenue with data center up 69% year-over-year. The April   │
│         9 rally extends post-earnings momentum rather than reflecting      │
│         fresh demand information.                                          │
│                                                 — news_AMD_2025-04-03_…    │
│                                                                            │
│         Pricing (weight 0.18, negative). KeyBanc's downgrade flagged       │
│         pricing-war risk with Intel pressuring gross margins...            │
│                                                 — news_AMD_2025-04-09_…    │
│                                                                            │
│         [continued]                                                        │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

**Type stack.**
- Display + body: Tiempos Text + Tiempos Headline. Free: Newsreader (italic
  is gorgeous) for display + Source Serif 4 for body.
- UI / labels: GT America / Söhne. Free: Inter.
- Mono: Berkeley Mono. Free: JetBrains Mono.

**Palette.**
- bg `#f7f1e3` (warm cream, slightly more saturated than DAILY OBSERVATIONS)
- ink `#1a1814`
- ink-secondary `#4a4640`
- ink-quiet `#8a8278`
- accent navy `#0a1d36`
- accent oxblood `#7a1f1f` (used very sparingly: focal dot, active link,
  verdict accent)
- chart navy `#0a1d36`
- chart oxblood `#7a1f1f`
- chart gray `#aca59a`
- rule `#cdc6b8`

**Motion.** None worth mentioning. Page settles. Hover states are color-only,
no transforms. This is an analog-feeling page.

**Standout component: the typesetting.** Long-form treatment of the verdict
and the dimensions, set as actual prose paragraphs in Tiempos at 17px. The
chart and the prose share equal real estate. There's no "let me make this
flashy" energy. The voice is calm and certain.

**Why this lands at Bridgewater specifically.**
1. Westport is preppy old money. This is the visual language of that world.
2. Long-form prose treatment signals that you respect the reader's
   intelligence and patience.
3. Quiet wealth aesthetic is very specifically anti-Bloomberg-Terminal,
   anti-Robinhood, anti-AI-startup. It says "we're institutional."

**Risks.**
- Slowest to scan. PMs in a hurry want density.
- Light cream + serif together can read as "Substack" instead of "research
  firm" if the type isn't tight. Easy to get wrong.
- Less interactive feel than the other three.

---

## Decision matrix (Bridgewater-coded edition)

| If you want to signal…                         | Pick               |
|-----------------------------------------------|--------------------|
| "I read your research and respect the form"   | DAILY OBSERVATIONS |
| "I read Principles and applied the lesson"    | PRINCIPLES         |
| "I am serious. I cite my sources. I'm a quant"| PURE ALPHA         |
| "I understand institutional-quiet aesthetic"  | WESTPORT           |
| Highest "wow" factor for a 3-minute demo      | PRINCIPLES         |
| Highest "they'd hire this designer" reaction  | DAILY OBSERVATIONS |
| Highest "this could be a real product"        | DAILY OBSERVATIONS |
| Highest distinctiveness vs. other entries     | PRINCIPLES         |

## My pick, ranked

1. **PRINCIPLES** — highest ceiling. If executed well, judges will literally
   point at the screen and say "this is what it should look like." Riskiest
   to build, biggest payoff. The diagram-of-reasoning is unforgettable.

2. **DAILY OBSERVATIONS** — highest floor. If executed well, BW's judges feel
   like you GET them. Hard to land a 10/10 here, but very hard to land below
   8/10 either. Safest path to "yes, hire this team."

3. **PURE ALPHA** — niche. If a judge is a PhD or a published quant, they
   will love it. If they're not, they'll find it dry.

4. **WESTPORT** — beautiful but slow. Best as a print artifact, not a live
   demo. Save it for the take-home doc, not the screen.

## What I need from you

Pick one. Or remix two ("PRINCIPLES diagram on a DAILY OBSERVATIONS body" is
a real possibility — the diagram in the middle of a published research note).

Once you pick, I'll write a fresh `styles.css` and restructured `index.html`
from scratch. None of the previous Bloomberg Editorial implementation will
bleed into it. Each direction gets to be itself.

## A note on what's NOT here

I deliberately didn't include:

- Generic-dark-mode-fintech (we already have that, it's the current Bloomberg
  Editorial implementation)
- Glassmorphic / Apple-pro polish (looks like every YC SaaS, doesn't speak BW)
- Motion-heavy / animated interfaces (BW values calm, not flair)
- Brutalist / hot-orange-on-black (hackathon coded, not institutional coded)

Those are valid for other audiences. They aren't Bridgewater.
