# DESIGN CONCEPTS — Price Action Tagger

Four design directions, each meaningfully different. Pick one. Each is a different bet
on what this product *is*.

The current implementation ("Bloomberg Editorial") is solid but not bold. It looks like a
slightly nicer Bloomberg. The brief said drastic. So these are drastic, with the
references and the tradeoffs spelled out so you can make a real choice instead of
choosing on vibes.

---

## What this product actually does (the brief, restated)

A user picks a stock. The page shows 5 years of price action with significant moves
flagged. Click a move. Claude reads the news + filings + 13F positioning around that
day, decomposes the move into 5 dimensions (demand, pricing, competitive,
management, macro), and emits a verdict: lean (follow the move), fade (it overshot),
or skip (unclear).

Toggling sources changes the inputs, which changes the output. Four strategies read
the same attribution and produce four (potentially different) verdicts.

The user is a Bridgewater hackathon judge. Beyond that, hypothetically: a quant
researcher, an institutional analyst, a portfolio manager. Not a retail trader.

What the page must communicate at a glance:

1. This is a research tool, not a trading product.
2. The reasoning is grounded (real chunks cited, no hallucination).
3. Different sources lead to different conclusions (the ablation story).
4. Different strategies disagree (the fade-vs-lean tension).

What it must NOT look like:

- A retail trading app (Robinhood, eToro)
- A generic SaaS dashboard (any Vercel-hosted Next.js page from 2023)
- A finance fintech demo from a Y-Combinator batch (we are not pitching to consumers)

---

## Concept 1: TERMINAL

> "What if Bloomberg made a 2026 redesign of the Terminal that wasn't ugly?"

### One-line
A keyboard-driven, monospace-everything power tool. Density approaching real
institutional software. No mouse needed.

### References
- Bloomberg Terminal (the actual product, not the home page)
- `k9s` (Kubernetes TUI)
- Vim, Neovim
- Reuters Eikon
- Are.na's interface but in finance
- Devon's old "lobste.rs" theme

### What's drastic
Almost no images, no shadows, no gradients. Hairlines and typography only.
Command bar pinned to the bottom: `/AMD <enter>` to load. `:tickers` to list.
`,n` to toggle news. `,k` for 10-K. `f` to flip strategy to fade-only. The big
verdict word stays editorial display serif, but it's the *only* serif on the page.
Everything else is `Berkeley Mono` or `JetBrains Mono`.

### Layout (ASCII sketch, top to bottom, full width)

```
┌─ LEAN/FADE ──────────────────────────────  ABT  ACU  AIR [AMD] APD ──┐
│                                                                       │
│ AMD            Advanced Micro Devices      [TECH·LARGE-CAP]           │
│ ──────────────────────────────────────────────────────────────────    │
│ 2021-04-25 → 2026-04-24  ·  1241 bars  ·  16 flagged  ·  9 up / 7 dn  │
│                                                                       │
│ ┌─ FIG 01: PRICE × FLAGGED MOVES ────────────────────────────────┐   │
│ │  ░░▒▒▓▓██▓▒░ ░▓██▓▒░░ ▒▓██▓▒░ ░░▓██████████▓▒                  │   │
│ │  (terminal-style sparkline; dots in amber on the line)         │   │
│ └────────────────────────────────────────────────────────────────┘   │
│                                                                       │
│ ┌─ VERDICT [strategy: fundamental_vs_nonfundamental] ──────────────┐ │
│ │                                                                   │ │
│ │              L E A N                                              │ │
│ │              ───────                                              │ │
│ │   realized +23.82%   predicted +13.73%   gap +10.09pp             │ │
│ │                                                                   │ │
│ │   [f]undamental   [e]xpected   [d]imension   [h]ybrid             │ │
│ │   ▶ LEAN          FADE         SKIP          FADE                 │ │
│ └───────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│ ┌─ INPUTS ─────────────────────────────────────────────────────────┐ │
│ │ [n] news (4)  [k] 10-K (3)  [8] 8-K (3)  [e] earnings (5)        │ │
│ │ [p] peer (-)  [m] macro (-) [t] 13F (2)         total: 17 chunks  │ │
│ └───────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│ ┌─ ATTRIBUTION ─────────────────────────────────────────────────── │
│ │ DEMAND     [weight=0.31] ▲ +0.31 ████████████████░░░░░░░         │
│ │ PRICING    [weight=0.18] ▼ -0.18 ████████░░░░░░░░░░░░░░░         │
│ │ COMPETITIVE[weight=0.27] ▲ +0.27 █████████████░░░░░░░░░         │
│ │ MGMT       [weight=0.06] · 0.06  ███░░░░░░░░░░░░░░░░░░░         │
│ │ MACRO      [weight=0.18] · 0.18  ████████░░░░░░░░░░░░░░         │
│ └───────────────────────────────────────────────────────────────── │
│                                                                       │
│ ┌─ EVIDENCE ──────────────────────────────────────────────────────── │
│ │ ▸ DEMAND │ news_AMD_2025-04-03_article_0001                       │
│ │   Q4 2024 results showed record revenue with data center +69% YoY │
│ │   ─────────────────────────────────────────────────────────────── │
│ │ ▸ PRICING │ news_AMD_2025-04-09_article_0003                      │
│ │   KeyBanc downgrade flagged risk of price war with Intel          │
│ │   ─────────────────────────────────────────────────────────────── │
│ └────────────────────────────────────────────────────────────────── │
│                                                                       │
└─ : ────────────────────────────────────────  /amd  ,n  ,k  f  q ────┘
                                                              command bar
```

### Palette
- bg `#000000` (pure)
- ink `#d4d4d4` (warm paper-white)
- ink-dim `#666666`
- amber `#f5a623` (Bloomberg legacy)
- up `#3ecf8e`
- down `#ef4444`
- rule `#1a1a1a`

### Type
- Display: Newsreader (serif), only on the verdict word and ticker name
- Body / UI: JetBrains Mono everywhere else
- No Inter

### Motion
Almost none. Cursor blinks. Strategy verdict swap is instant (no fade). Dots on
chart fill with amber on hover. Command bar pulses on focus.

### Standout component
**The command bar.** Type `/amd` to switch tickers. `:strategy hybrid` to switch
verdicts. `:filter +sec -news` to set sources. Press `?` for full keymap. Judges
will remember this.

### Why this could win
Looks like real institutional software. Doesn't try to be cute. The hackathon
crowd has seen 200 dark-mode dashboards; almost none with a real command bar.

### Why this could lose
If you can't drive it confidently in a 3-minute demo, it looks broken. Mouse
users will be lost. Mobile is dead.

---

## Concept 2: DOSSIER

> "Each flagged move is a dossier. The page reads like the case file from a
> serious research firm."

### One-line
The page treats every move like a court case or a Bridgewater Daily Observation.
Cover page, numbered sections, drop caps, footnotes, generous margins. It looks
like research, not a tool.

### References
- Bridgewater Daily Observations (the actual PDFs they publish to clients)
- Stratechery (Ben Thompson's analysis)
- Visual Capitalist long-form
- BCG annual reports
- Court case files (CASE NO. / DATE / IN THE MATTER OF)
- Pitchbook IPO prospectus design

### What's drastic
Drops the dashboard metaphor entirely. No grids of cards. The chart is a single
figure on a "cover page" at top, the rest of the page reads top-to-bottom like a
research note. The verdict is a *finding*, not a stat.

### Layout

```
                    ────  L E A N / F A D E  ────
                  PRICE ACTION TAGGER · CASE FILE
                          NO. 2025-04-09

                       AMD                                  
              advanced micro devices
                  ──────────────────
                                                            
              FILED BY      Claude (Anthropic)
              CASE DATE     2025-04-09
              REACTION      +23.82%  (4.5σ)
              FILE WEIGHT   1,306 chunks of evidence

  ┌──────────────────────────────────────────────────┐
  │   [chart fills cover page, calm, generous space]   │
  └──────────────────────────────────────────────────┘

──────────────────────────  ·  ──────────────────────────

§ I  THE FINDING                                            

[L] EAN.    The court of the structured-attribution model
finds the move on April 9, 2025 to be STRUCTURAL. Realized
return of +23.82% modestly exceeds predicted +13.73% by 10
percentage points. Confidence 95%.

§ II  THE FRAMEWORKS DISAGREE                               

   Fundamental vs Non    LEAN
   Expected vs Realized  FADE  ←─ overshoots predicted by 1.74×
   Dimension-weighted    NEUTRAL
   Hybrid                FADE  ←─ structural but overshoots

§ III  WHAT THE EVIDENCE SAID                              

DEMAND       Weight 0.31   ↑ Positive
   "AMD's Q4 2024 results showed record revenue with data
    center up 69% YoY..." [news_AMD_2025-04-03_article_0001]

PRICING      Weight 0.18   ↓ Negative
   "KeyBanc downgrade flagged risk of price war with Intel
    pressuring AMD gross margins..." [news_AMD_2025-04-09_..]

[etc, paragraph treatment per dimension]

§ IV  THE INPUT BUNDLE                                       

  News           4 chunks ✓
  10-K           3 chunks ✓
  8-K            3 chunks ✓
  Earnings call  5 chunks ✓
  13F           2 chunks ✓
  Peer news     —
  Macro         —

[footer rule]
```

### Palette
- bg `#0c0a06` (deep paper-black with brown undertone)
- paper `#f4ede0` (cream, used in light variant for cover page only)
- ink `#e8e0d2` (paper-cream text on dark)
- accent `#7a1f1f` (oxblood, very institutional)
- up `#4a8a5e`
- down `#a04848`
- rule hairline `#2a2520`

### Type
- Display: GT Sectra or Newsreader bold italic
- Body: ITC Caslon or Newsreader regular at 17px (long-form readable)
- Mono: Berkeley Mono or JetBrains Mono for chunk_ids only

### Motion
Quiet. Page-turn metaphor: when you click a new flagged move, the dossier
"flips" with a subtle 3D rotate-Y. Chart cross-fades. Drop caps render with a
slight fade-in.

### Standout component
**The cover page.** When you load AMD or click a move, you see a publication-style
cover for ~600ms before the analysis fades in. Establishes that this is research,
not a chart. Also: footnotes work. Click any chunk_id, the chunk slides in from
the right margin like Tufte sidenotes.

### Why this could win
Bridgewater publishes Daily Observations. Their judges will recognize the form
language. It's the most "insider-coded" of all four directions. Also: the chart
has space to breathe instead of being cramped into a card.

### Why this could lose
You lose density. Power users want to see a lot at once; this makes them scroll.
And if a judge is skimming on a phone, the long-form treatment hurts.

---

## Concept 3: ATELIER

> "Bloomberg Businessweek's design team got bored and made an interactive
> version of one of their feature spreads."

### One-line
LIGHT mode. Cream paper background. Magazine-spread layout: oversized
display serif numbers, two-column body where appropriate, pull-quote treatment
on the verdict, charts styled like infographic illustrations.

### References
- Bloomberg Businessweek (long-form features, not the homepage)
- The Economist print edition
- Wired Italia
- Pentagram print work
- Pitch decks designed by good designers (Mule Design, Hyperakt)
- New York Magazine's "Approval Matrix" but for finance

### What's drastic
**It's a light theme.** Cream/off-white. This alone reads as drastic in a sea of
dark-mode hackathon demos. Also: chart is reskinned as an infographic, not a
trading chart. Editorial dingbats (small ornaments) between sections. Big
oversized number treatment for KPIs (think: 80px display serif for "+23.82%").

### Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  Lean / Fade.                          ABT  ACU  AIR  AMD  APD     │
│  ─────────────                                                      │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                          (1px hairline rule)

      VOL. 1   ·   ISSUE 04   ·   CASE OF AMD   ·   APRIL 9, 2025

                  Why did Advanced Micro Devices
                  rocket twenty-four percent on
                            April ninth?

           A reading of the news, the filings, and the
                positioning, by an attribution model

                            ─── ·· ───

                                                                      
      ┌─────────────────────────────────────────────────────────┐
      │   [chart, large, with subtle dotted grid, dots as       │
      │    annotated event markers like a long-read infographic]│
      └─────────────────────────────────────────────────────────┘

                                                                      
   FINDING                              ★      The framework reads the
                                               move as STRUCTURAL but
                                               overshooting fundamentals.
        “F   A   D   E.”                       Realized +23.82% versus a
                                               predicted +13.73%, a gap
        — Hybrid framework                     of 10 percentage points.
                                                                      
   ─────────────────────────────────────────────────────────────────  
                                                                      
   FOUR FRAMEWORKS, FOUR READS

   FUNDAMENTAL VS NON      EXPECTED VS REALIZED      DIMENSION-WT      HYBRID
   ────────────────       ────────────────────       ────────────     ──────
        LEAN                       FADE                 SKIP            FADE
                                                                      
                                                                      
   ─────────────────────────────────────────────────────────────────
                                                                      
   THE FIVE DIMENSIONS                                                
                                                                      
   ① DEMAND        +0.31  ↑                  
   "AMD's Q4 2024 results showed record revenue with data center up
   69% year-over-year, with strong AI/EPYC demand…"  
                                — news_AMD_2025-04-03_article_0001
                                                                      
   ② PRICING        −0.18  ↓                                          
   "KeyBanc downgrade flagged risk of price war with Intel
   pressuring AMD gross margins…"                                     
                                — news_AMD_2025-04-09_article_0003
                                                                      
   [etc]                                                              
                                                                      
   ─────────────────────────────────────────────────────────────────
   PRICE ACTION TAGGER  ·  HACKATHON DEMO  ·  BRIDGEWATER 2026       
```

### Palette
- bg cream `#f4ede0`
- ink charcoal `#1a1814`
- ink secondary `#4a4640`
- accent oxblood `#7a1f1f` (or deep navy `#1f2c5e`)
- up `#2e7a4a`
- down `#a02a2a`
- rule warm gray `#cdc6b8`

### Type
- Display: Tiempos Headline or GT Sectra Display (paid) — alt: Newsreader bold italic
- Body: Tiempos Text — alt: Newsreader at 16px
- Mono: Berkeley Mono — alt: JetBrains Mono
- Pull-quote: same display, italic, 56px+

### Motion
Almost none. Page settles in. Verdict word draws in via clip-path mask wipe (left
to right) over 600ms, like a printed letter being inked. Hover on a chunk_id
draws a thin underline.

### Standout component
**The pull-quote verdict.** A massive quotation-mark-styled "FADE." pulled out
of the body text like a magazine pull-quote. With the strategy name as the
attribution byline below. This is the page's identity.

### Why this could win
Visually distinct from every other hackathon demo. Reads as "high-end research,
not Robinhood-clone." Light mode is itself a statement here.

### Why this could lose
Light mode in finance is fashion-risky — 70% of judges expect dark. Also: long
read structure means slower scanning, and on a small laptop screen the magazine
feel can collapse.

---

## Concept 4: CONTROL

> "Mission control for moves that mattered."

### One-line
Multi-panel grid that fills the viewport. Status pills, monospace numbers, live
indicators. The page feels operational, not editorial. You scan it the way you
scan a NASA console or an SRE dashboard.

### References
- NASA Mission Control consoles (1969 + the modern redesign)
- Stripe Dashboard (their fraud / payments console)
- Linear's issue list density
- k9s (Kubernetes TUI)
- Splunk dashboards
- Datadog APM screens

### What's drastic
**Nothing scrolls.** Or almost nothing. The entire page fits a 1440×900 viewport
in a 4-panel grid. Each panel is its own micro-dashboard. Status indicators
everywhere (●  amber dot for "active", green for "ok", red for "alert"). Time
codes in mono. All numbers update in place when you toggle.

### Layout

```
┌────────────────────────────────────────────────────────────────────────────┐
│ LEAN/FADE  ●  PRICE ACTION TAGGER       AMD ▼   T-0 2025-04-09  •  4.5σ   │
├────────────────┬───────────────────────────────────────────────┬───────────┤
│  TICKERS       │  PRICE × FLAGGED                              │  VERDICT  │
│  ────────      │  ─────────────                                │  ────────  │
│  ● ABT         │                                               │           │
│  ● ACU         │   [chart, fills ~50% width, dark-on-dark      │   LEAN    │
│  ● AIR         │    grid, dots as annotated alerts]            │           │
│  ▶ AMD         │                                               │  +23.82   │
│  ● APD         │                                               │  pred +13.73   │
│                │                                               │  Δ +10.09 │
│                │                                               │           │
├────────────────┼─────────────────────────┬─────────────────────┼───────────┤
│  STRATEGIES    │  DIMENSIONS             │  INPUTS             │  CHARACTER │
│  ──────────    │  ──────────             │  ──────             │  ─────────  │
│  FUND   LEAN   │  DEMAND      +0.31 ↑   │  news     ✓ 4ch    │ STRUCTURAL │
│  E/R    FADE   │  PRICING     −0.18 ↓   │  10-K     ✓ 3ch    │            │
│  DIM_W  SKIP   │  COMPETIT    +0.27 ↑   │  8-K      ✓ 3ch    │  CONFIDENCE │
│  HYBRID FADE   │  MGMT         0.06 ·   │  earnings ✓ 5ch    │  ━━━━━ 95%  │
│                │  MACRO        0.18 ·   │  13F      ✓ 2ch    │            │
├────────────────┴─────────────────────────┴─────────────────────┴───────────┤
│  EVIDENCE  ●live                                                            │
│  ────────                                                                   │
│  [DEMAND   ↑ +0.31 ] news_AMD_2025-04-03_article_0001                     │
│  Q4 2024 results showed record revenue with data center +69% YoY...         │
│  [PRICING  ↓ -0.18 ] news_AMD_2025-04-09_article_0003                      │
│  KeyBanc downgrade flagged risk of price war with Intel...                  │
│                                                                             │
│  T+19s  attribute() returned · validate=skip · model=claude-haiku-4-5     │
└────────────────────────────────────────────────────────────────────────────┘
```

### Palette
- bg `#0a0d12` (cool, slight blue undertone)
- panel `#10141c`
- panel-2 `#161c27`
- ink `#e6ebf2`
- ink-dim `#6b7484`
- accent `#54a4ff` (signal blue, not amber)
- up `#34d399`
- down `#fb7185`
- alert (rare) `#fbbf24` amber
- rule `#1d2330`

### Type
- Body / UI: Inter (or IBM Plex Sans for SRE-coded vibe)
- Mono everywhere numbers: JetBrains Mono / Berkeley Mono
- No serif. Fully UI-typography.

### Motion
Live data feel. Numbers tick (digit-by-digit roll) when they change. Status
dots slowly pulse. When attribution updates, the panels briefly flash
border-amber for 200ms.

### Standout component
**The status footer.** Tiny mono line at the bottom: `T+19s attribute() returned
· validate=skip · model=claude-haiku-4-5`. Looks like Vercel deploy logs / Stripe
event stream. Signals "this is a real working system" louder than any prose
could.

### Why this could win
Most "operational" of the four. Looks like real internal tooling at a sophisticated
firm. Mission-control vibe matches the "judging events as they happen"
metaphor.

### Why this could lose
Risks looking like every dark Stripe/Vercel/Linear clone. Hardest to make
distinctive without a strong visual signature beyond layout. Cold; the editorial
direction has more soul.

---

## Decision matrix

| If you want…                                | Pick     |
| ------------------------------------------- | -------- |
| To look like real institutional software    | TERMINAL |
| To look like a Bridgewater Daily Observation| DOSSIER  |
| To look like nothing else in the hackathon  | ATELIER  |
| To look like a polished operations console  | CONTROL  |
| Highest density, power-user feel            | TERMINAL |
| Highest "we get the audience" credibility   | DOSSIER  |
| Highest visual distinctiveness              | ATELIER  |
| Highest "looks like a working product"      | CONTROL  |

## Which one I'd actually ship

If the goal is winning the Bridgewater hackathon: **DOSSIER**. It speaks BW's own
form language (Daily Observations, Principles flowcharts, structured findings).
It's also the most defensible — if a judge asks "what does this remind you of",
the answer is "your published research." Hard to beat.

If the goal is making the most striking demo regardless of audience: **ATELIER**.
The light theme alone will set it apart from every other hackathon entry. The
risk is that judges expecting fintech-dark might read it as "wrong" rather than
"refreshing." But if it lands, it lands hard.

If the goal is showing technical maturity and power-user empathy: **TERMINAL**.
Real product builders will notice the command bar. Other judges might not.

If the goal is "professionally finished and obviously operational": **CONTROL**.
Safest. Also most generic.

---

## What I need from you

Pick one (or remix two). Say it. I'll build it for real.

If you want to iterate the concept itself first ("DOSSIER but with CONTROL's
status footer", "ATELIER but in dark mode"), say that and I'll write a v2 of the
chosen concept before touching any code.

The current Bloomberg Editorial implementation is at `demo/static/`. Whichever
direction you pick, I'll write a fresh `styles.css` from scratch — these are
not "tweaks" of the current page, they're real redirections.
