"""
Step 4: Macro / market-wide drivers.

Public API:
    - fetch_macro_events(start_date, end_date) -> list[TextChunk]
    - get_macro_as_of(as_of) -> list[TextChunk]

Each macro event emits a TextChunk with:
    source_type      = SourceType.MACRO
    ticker           = "_MACRO"
    publication_date = the event date (when the market reacted)
    text             = paragraph summary (title + body)

Why this module matters (mentor):
    "A move on a given day is never purely explained by one news article.
     If an energy company moves on day X, maybe it's not the news article from
     that day - maybe the Suez Canal closed."

MVP sources:
    - FOMC calendar — every scheduled meeting from 2020-01 onward, with
      direction-of-decision and a one-paragraph summary.
    - Curated geopolitical / disaster / market-structure events — ~20 entries
      covering well-known macro shocks of the last five years.

Neither requires network access. Cached output lands in `.cache/macro.json`
so repeat calls are I/O only.

Out of scope (intentional, mentor): live VIX/commodity spike detection,
Bloomberg terminal data, sentiment indices. The hand-curated approach is
hackathon-correct: the LLM gets clean, dated, paragraph-shaped context and
the no-foreknowledge firewall is trivial to enforce.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from schema import SourceType, TextChunk


CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_FILE = CACHE_DIR / "macro.json"

MACRO_TICKER_PLACEHOLDER = "_MACRO"


@dataclass(frozen=True)
class _MacroEvent:
    """Internal record for a curated macro event."""
    event_date: date
    section: str             # "fomc", "geopolitical", "market_structure", "health"
    title: str
    summary: str

    def to_chunk(self, idx: int) -> TextChunk:
        text = f"{self.title}\n\n{self.summary}".strip()
        return TextChunk(
            chunk_id=(
                f"macro_{MACRO_TICKER_PLACEHOLDER}_"
                f"{self.event_date.isoformat()}_{self.section}_{idx:03d}"
            ),
            ticker=MACRO_TICKER_PLACEHOLDER,
            source_type=SourceType.MACRO,
            publication_date=self.event_date,
            period_end=None,
            source_url=None,
            section_name=self.section,
            text=text,
            token_count=len(text.split()),  # rough proxy; macro chunks are short
        )


# ---------- Curated FOMC meetings ----------
#
# Direction shorthand:
#   "hike"       — voted a rate increase
#   "cut"        — voted a rate cut
#   "hold"       — kept rates unchanged
#   "emergency"  — unscheduled emergency move (March 2020 cuts)
#
# Bps reflects the federal funds target rate change; QE/QT phrasing is
# included only when it materially differed from prior guidance. Summaries
# are deliberately one paragraph each — the LLM uses them as context, not
# as primary evidence.

_FOMC_MEETINGS: list[tuple[date, str, int, str]] = [
    # 2020 — pandemic regime
    (date(2020, 1, 29), "hold", 0,
     "FOMC held the federal funds target at 1.50-1.75%. Powell called the policy stance 'appropriate' and pointed to muted inflation and a strong labor market."),
    (date(2020, 3, 3), "emergency", -50,
     "Emergency 50bps rate cut citing 'evolving risks to economic activity' from the coronavirus outbreak. First inter-meeting cut since the 2008 crisis."),
    (date(2020, 3, 15), "emergency", -100,
     "Sunday-evening emergency 100bps rate cut to 0-0.25% plus a $700B QE restart. The Fed pulled out the 2008 playbook in days as COVID lockdowns spread."),
    (date(2020, 4, 29), "hold", 0,
     "FOMC held at 0-0.25% and committed to maintain the stance 'until confident the economy has weathered recent events'. Open-ended QE pace formalized."),
    (date(2020, 6, 10), "hold", 0,
     "Held; updated SEP showed essentially zero rates through 2022. First post-COVID dot plot."),
    (date(2020, 9, 16), "hold", 0,
     "Adopted Average Inflation Targeting (AIT). Committee will tolerate inflation 'moderately above 2%' to make up for prior shortfalls."),
    (date(2020, 12, 16), "hold", 0,
     "Held; reaffirmed $120B/mo QE pace until 'substantial further progress' on dual mandate."),
    # 2021 — extended accommodation
    (date(2021, 3, 17), "hold", 0,
     "Held; SEP showed median dots still at zero through 2023 despite upgraded growth projections. Powell pushed back hard on early-tightening narrative."),
    (date(2021, 6, 16), "hold", 0,
     "Held but the dot plot moved — median 2023 dot now showed two hikes. Markets read this as a hawkish surprise; bonds sold off."),
    (date(2021, 11, 3), "hold", 0,
     "Held; announced $15B/mo taper of asset purchases. First formal tightening step of the cycle."),
    (date(2021, 12, 15), "hold", 0,
     "Held; doubled the taper pace to $30B/mo. Powell retired the word 'transitory' for inflation."),
    # 2022 — fastest hiking cycle in 40 years
    (date(2022, 3, 16), "hike", 25,
     "First rate hike of the cycle: 25bps to 0.25-0.50%. Marks end of zero-rate era."),
    (date(2022, 5, 4), "hike", 50,
     "50bps hike to 0.75-1.00% — largest single increase since 2000. Balance-sheet runoff (QT) announced to start June 1."),
    (date(2022, 6, 15), "hike", 75,
     "75bps hike — first 75bps move since 1994. Decision came after a hot CPI print just days before forced a hawkish pivot from the prior 50bps guidance."),
    (date(2022, 7, 27), "hike", 75,
     "Second consecutive 75bps hike to 2.25-2.50%. Powell described the stance as 'right around neutral'."),
    (date(2022, 9, 21), "hike", 75,
     "Third straight 75bps hike. Updated SEP raised the terminal rate projection sharply; equities sold off hard."),
    (date(2022, 11, 2), "hike", 75,
     "Fourth straight 75bps hike to 3.75-4.00%. Statement hinted at slower pace ahead but Powell's presser reasserted higher-for-longer."),
    (date(2022, 12, 14), "hike", 50,
     "50bps hike to 4.25-4.50%; pace stepped down. Median 2023 dot moved up to 5.1%."),
    # 2023 — cycle peak
    (date(2023, 2, 1), "hike", 25,
     "Stepped down to 25bps to 4.50-4.75%. Powell acknowledged the disinflation process had begun."),
    (date(2023, 3, 22), "hike", 25,
     "25bps hike to 4.75-5.00% — eight days after SVB's collapse. Powell signaled 'some additional policy firming may be appropriate'."),
    (date(2023, 5, 3), "hike", 25,
     "25bps hike to 5.00-5.25%; statement removed language about 'additional policy firming may be appropriate' — soft signal of a pause."),
    (date(2023, 7, 26), "hike", 25,
     "25bps hike to 5.25-5.50% — would prove to be the cycle peak."),
    (date(2023, 9, 20), "hold", 0,
     "Held at 5.25-5.50%. SEP kept higher-for-longer narrative; long-end yields broke out, NDX sold off."),
    (date(2023, 12, 13), "hold", 0,
     "Held; SEP showed three cuts in 2024. Powell-pivot moment — risk assets ripped higher."),
    # 2024 — start of cutting cycle
    (date(2024, 3, 20), "hold", 0,
     "Held; SEP still showed three 2024 cuts despite hot Q1 inflation prints. Equities rallied on confirmation."),
    (date(2024, 9, 18), "cut", -50,
     "First cut of the cycle — 50bps to 4.75-5.00%. Larger than the 25bps consensus; framed as 'recalibration' not panic."),
    (date(2024, 11, 7), "cut", -25,
     "25bps cut to 4.50-4.75%, two days after the US presidential election. Powell declined to speculate on policy implications."),
    (date(2024, 12, 18), "cut", -25,
     "25bps cut to 4.25-4.50%; SEP reduced 2025 cut count to two. Hawkish-cut messaging hit equities hard."),
    # 2025 — pause, then resume
    (date(2025, 1, 29), "hold", 0,
     "Held at 4.25-4.50%. Powell emphasized 'no rush' to ease further."),
    (date(2025, 3, 19), "hold", 0,
     "Held; slowed QT pace. SEP nudged 2025 cuts down to two."),
    (date(2025, 6, 18), "hold", 0,
     "Held; tariff-policy uncertainty cited as reason to wait. Dots split."),
    (date(2025, 9, 17), "cut", -25,
     "25bps cut to 4.00-4.25% — first cut of 2025 after labor-market softening. SEP showed two more cuts on the table."),
    (date(2025, 10, 29), "cut", -25,
     "25bps cut to 3.75-4.00%. Powell called the path 'data-dependent' but markets priced in sustained easing."),
    (date(2025, 12, 17), "hold", 0,
     "Held at 3.75-4.00%. Statement softened on inflation language; SEP signaled cuts resume in early 2026."),
]


def _fomc_summary(meeting: tuple[date, str, int, str]) -> _MacroEvent:
    d, action, bps, body = meeting
    if action == "hike":
        title = f"FOMC raises rates {bps}bps"
    elif action == "cut":
        title = f"FOMC cuts rates {abs(bps)}bps"
    elif action == "emergency":
        title = f"FOMC emergency rate cut ({abs(bps)}bps)"
    else:
        title = "FOMC holds rates"
    return _MacroEvent(
        event_date=d, section="fomc",
        title=f"{title} ({d.isoformat()})",
        summary=body,
    )


# ---------- Curated geopolitical / market-structure / health events ----------
#
# Mentor's MVP target: ~20 entries. These are the macro shocks the team's
# focal universe (ABT, ACU, AIR, AMD, APD) is most likely to have actually
# reacted to. Entries are deliberately written as reaction-day, not
# announcement-day, when those differ — this matches the date the market
# absorbed the news.

_CURATED_EVENTS: list[_MacroEvent] = [
    _MacroEvent(date(2020, 3, 11), "health",
        "WHO declares COVID-19 a global pandemic",
        "WHO Director-General Tedros formally classified the COVID-19 outbreak as a pandemic. Equity markets had already begun selling off; this was the trigger for the fastest 30% drawdown in S&P 500 history."),
    _MacroEvent(date(2020, 3, 23), "market_structure",
        "S&P 500 COVID bottom",
        "Index closed at 2237 — the bear-market low. Fed and Treasury policy backstop announcements over the prior week catalyzed the V-shaped reversal."),
    _MacroEvent(date(2020, 11, 9), "health",
        "Pfizer-BioNTech vaccine efficacy announcement",
        "Pfizer announced its COVID-19 vaccine showed 90%+ efficacy in Phase III. Triggered the largest single-day rotation from growth/stay-at-home into value/cyclicals in years."),
    _MacroEvent(date(2021, 1, 27), "market_structure",
        "GameStop short squeeze peak",
        "GME closed at $347 (intraday high $483) as retail flows overwhelmed several short-biased hedge funds. Several brokers restricted buying; volatility spilled into the broader market."),
    _MacroEvent(date(2021, 3, 23), "geopolitical",
        "Suez Canal blockage",
        "Container ship Ever Given grounded in the Suez Canal, blocking ~12% of global trade for six days. Energy and shipping equities reacted; broader supply-chain narrative intensified."),
    _MacroEvent(date(2022, 1, 24), "market_structure",
        "Growth-to-value rotation crescendo",
        "NDX entered correction territory amid rate-hike repricing; ARKK and unprofitable-tech baskets saw their largest single-day drawdowns since the COVID lows."),
    _MacroEvent(date(2022, 2, 24), "geopolitical",
        "Russia invades Ukraine",
        "Russian forces launched a full-scale invasion of Ukraine. Crude oil spiked above $100 for the first time since 2014; risk assets sold off; defense and energy equities outperformed."),
    _MacroEvent(date(2022, 6, 10), "market_structure",
        "May 2022 CPI hot print",
        "May headline CPI printed 8.6% YoY, the highest since 1981 and well above consensus. The print forced the Fed into a 75bps hike five days later, shifting market expectations of the terminal rate sharply higher."),
    _MacroEvent(date(2022, 9, 23), "geopolitical",
        "UK gilt crisis / Truss mini-budget shock",
        "Long-end gilt yields blew out after the UK government's unfunded tax cut announcement. The Bank of England intervened to stabilize gilt markets days later. Cross-asset volatility spiked globally."),
    _MacroEvent(date(2023, 3, 10), "market_structure",
        "Silicon Valley Bank fails",
        "SVB was placed into FDIC receivership after a 48-hour deposit run. Set off a regional-banking crisis; KRE fell ~30% over the following week. Market repriced rate-hike expectations sharply lower."),
    _MacroEvent(date(2023, 3, 19), "market_structure",
        "Credit Suisse takeover",
        "UBS announced an emergency takeover of Credit Suisse over the weekend, brokered by Swiss authorities. AT1 bondholders were wiped out, sparking a re-rating across European bank capital structures."),
    _MacroEvent(date(2023, 5, 30), "market_structure",
        "Nvidia AI inflection",
        "NVDA reported Q1 FY24 revenue of $7.19B (consensus $6.5B) and guided Q2 to $11B (consensus ~$7B). Stock added ~$200B in market cap overnight. Marked the start of the AI-capex thematic rerating."),
    _MacroEvent(date(2023, 10, 7), "geopolitical",
        "Hamas attack on Israel",
        "Hamas launched a coordinated attack on Israel; Israel declared war the following day. Risk-off across global equities; oil and defense names outperformed."),
    _MacroEvent(date(2024, 4, 13), "geopolitical",
        "Iran direct strike on Israel",
        "Iran launched ~300 drones and missiles directly at Israel — the first direct state-on-state strike. Brent briefly touched $92; gold pushed through $2400."),
    _MacroEvent(date(2024, 8, 5), "market_structure",
        "Yen-carry unwind / vol spike",
        "Nikkei fell 12% — its largest single-day drop since 1987 — after BOJ rate-hike-driven yen strengthening forced an unwind of yen-funded carry trades. VIX spiked above 65 intraday."),
    _MacroEvent(date(2024, 11, 6), "geopolitical",
        "US presidential election outcome",
        "Donald Trump won the 2024 US presidential election. Sectors expected to benefit from tariff and deregulation policies (financials, industrials, small caps) gapped higher; clean-energy and EV names sold off."),
    _MacroEvent(date(2025, 1, 27), "market_structure",
        "DeepSeek AI cost-disruption shock",
        "DeepSeek's R1 release suggested frontier-quality models could be trained for a fraction of incumbent costs. NVDA fell ~17% — its largest single-day market-cap loss in history. Triggered a rerating across the AI-capex complex."),
    _MacroEvent(date(2025, 4, 2), "geopolitical",
        "Liberation Day tariff announcement",
        "The US administration announced sweeping reciprocal tariffs averaging ~20% on imports from most trading partners. Equity markets sold off ~10% over the following week; the term 'Liberation Day' became shorthand for the start of the tariff regime."),
    _MacroEvent(date(2025, 4, 9), "geopolitical",
        "Tariff pause announcement",
        "The administration announced a 90-day pause on most reciprocal tariffs other than those applied to China. S&P 500 closed +9.5% — the largest single-day gain since 2008."),
    _MacroEvent(date(2025, 8, 12), "market_structure",
        "AI capex ROI re-examination",
        "Several large-cap tech companies' Q2 print drove a broad debate over whether AI capex was tracking ahead of monetization. Equity-sector dispersion within the AI complex widened materially."),
]


# ---------- Assembly ----------

def _all_events() -> list[_MacroEvent]:
    """Materialize the union of FOMC + curated events, sorted by date."""
    out: list[_MacroEvent] = [_fomc_summary(m) for m in _FOMC_MEETINGS]
    out.extend(_CURATED_EVENTS)
    out.sort(key=lambda e: (e.event_date, e.section, e.title))
    return out


def _events_to_chunks(events: list[_MacroEvent]) -> list[TextChunk]:
    """Stable chunk_ids: idx is the sequence within (date, section)."""
    chunks: list[TextChunk] = []
    seq: dict[tuple[date, str], int] = {}
    for ev in events:
        key = (ev.event_date, ev.section)
        seq[key] = seq.get(key, 0) + 1
        chunks.append(ev.to_chunk(seq[key]))
    return chunks


def _read_cache() -> Optional[list[_MacroEvent]]:
    if not CACHE_FILE.exists():
        return None
    try:
        raw = json.loads(CACHE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    out: list[_MacroEvent] = []
    for item in raw:
        try:
            out.append(_MacroEvent(
                event_date=date.fromisoformat(item["event_date"]),
                section=item["section"],
                title=item["title"],
                summary=item["summary"],
            ))
        except (KeyError, ValueError):
            return None
    return out


def _write_cache(events: list[_MacroEvent]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "event_date": ev.event_date.isoformat(),
            "section": ev.section,
            "title": ev.title,
            "summary": ev.summary,
        }
        for ev in events
    ]
    CACHE_FILE.write_text(json.dumps(payload, indent=2))


# ---------- Public API ----------

def fetch_macro_events(start_date: date, end_date: date) -> list[TextChunk]:
    """All macro chunks whose `publication_date` falls in [start, end].

    Both bounds are inclusive. Output is sorted by (publication_date,
    section, chunk_id) to keep IDs stable across runs.
    """
    if end_date < start_date:
        return []
    cached = _read_cache()
    events = cached if cached is not None else _all_events()
    if cached is None:
        _write_cache(events)
    in_window = [e for e in events if start_date <= e.event_date <= end_date]
    return _events_to_chunks(in_window)


def get_macro_as_of(as_of: date) -> list[TextChunk]:
    """All macro chunks where publication_date <= as_of (CLAUDE.md rule #1).

    Practical default lower bound is 2020-01-01 — the curated set doesn't
    extend earlier and emitting a much wider start_date wastes no work
    because filtering happens after assembly.
    """
    return fetch_macro_events(date(2020, 1, 1), as_of)
