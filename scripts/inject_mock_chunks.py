"""Inject plausible mock chunks for missing sources on pre-2024 AMD events.

Why this exists
---------------
For events before 2024-01-01, our ingestion only produced
`earnings_transcript` and `macro` chunks. The Sources tab in the demo
shows every other source at "0 chunks", which makes the ablation chart
flat and the toggles feel empty. This script generates plausible mock
chunks for the missing source types (news, sec_10k, sec_8k, peer_news,
sector_news, thirteen_f) and patches them into
`demo/static/data/AMD.json`.

This is DEMO FILL — not real evidence. Every generated chunk:
  - Carries a stable id of the form `{source}_AMD_{YYYY-MM-DD}_mock_NNN`
    so they're identifiable in the data layer (the `mock` segment).
  - Has a `source_url` of `mock://demo-fill/...` so any code that resolves
    URLs can flag them.
  - Uses templated prose patterned on real financial press / SEC voice
    but not attributed to a real outlet or filing.

The pre-baked attribution is NOT regenerated (would cost LLM credits).
The mock chunks join the existing chunk pool so the toggles light up and
the Evidence panel can show entries when the user filters to those
source types.

Run: python scripts/inject_mock_chunks.py
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import date, timedelta
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "demo" / "static" / "data" / "AMD.json"
TICKER = "AMD"
CUTOFF = date(2025, 1, 1)  # the date real-source ingestion came online for AMD
MOCK_URL_PREFIX = "mock://demo-fill"
MOCK_TAG = "mock"  # appears in chunk_id so they're greppable in the bundle


# ───────────────────────────── content pools ─────────────────────────────
# Each entry is (section_name, body). Bodies pull in {date}, {ticker},
# {dir_word}, {mag_word} as needed via str.format on a per-event context.

NEWS_UP = [
    ("market_open",
     "AMD shares opened higher and held the bid into the close, with desk flow tilted bullish through the morning. Volume ran above the 20-day average but never spiked in a way that suggested a single forced-buyer; the move read more like coordinated re-engagement from accounts that had been underweight the name."),
    ("upgrade",
     "A second-tier sell-side desk upgraded {ticker} to overweight, anchoring the call to improving Data Center optics and a more stable PC end-market than feared. The note flagged share-gain potential of the EPYC roadmap against Intel's delayed launches and raised the 12-month target by mid-single-digits."),
    ("guidance_whisper",
     "Sources close to the company indicated leadership remained confident in the second-half ramp of next-generation accelerators, with hyperscaler conversations advancing faster than analyst models had incorporated. Buy-side desks took the read as supportive of a forward-quarter raise."),
    ("read_through",
     "The move appeared to be partly a read-through from constructive datapoints across the semi complex earlier in the week — including stronger-than-expected results from supplier-tier peers and a supportive supply-chain channel-check on advanced packaging capacity."),
    ("ai_capex",
     "Traders cited renewed enthusiasm for AI-capex beneficiaries as a primary driver, with desks reallocating into {ticker} from more defensive semi names. The move came on the back of upward revisions to 2025 data-center spending forecasts at two major cloud customers."),
]

NEWS_DOWN = [
    ("profit_take",
     "AMD shares sold off after a sharp recent run, with desks pointing to a positioning unwind rather than a clear fundamental shift. Volume was elevated but the move lacked a single named catalyst, fitting the profile of a crowded-trade exit."),
    ("downgrade",
     "A bulge-bracket desk downgraded {ticker} to neutral, citing caution around near-term PC client weakness and a slower-than-expected MI customer ramp. The note pulled the 12-month target by mid-single-digits and flagged management's prior guide as increasingly hard to defend."),
    ("channel_softness",
     "Channel checks suggested near-term softness in PC and gaming GPU demand, with elevated inventory at distributors expected to weigh on bookings into the back half. Analysts trimmed forward estimates modestly and lowered their consumer-segment assumptions."),
    ("rates_macro",
     "Risk-off action across the semi tape weighed on {ticker}, with the PHLX semiconductor index off sharply as rates moved higher and traders rotated out of high-multiple growth into more defensive corners of the market."),
    ("competitive",
     "Concerns over competitive positioning resurfaced after rival announcements at an industry event the prior day, with desks debating whether {ticker}'s GPU roadmap was on schedule and whether the EPYC server franchise still held its previous share-gain trajectory."),
]

SEC_10K_TEMPLATES = [
    ("Item 1. Business",
     "We design and integrate technology that powers high-performance and adaptive products spanning data center, client, gaming, and embedded markets. Our Data Center segment includes server CPUs (EPYC) and data center GPUs and accelerators (Instinct); our Client segment includes desktop, notebook, and Chromebook processors (Ryzen); Gaming includes discrete and semi-custom processors (Radeon); and Embedded covers high-performance FPGAs and adaptive SoCs (Xilinx). End-customer concentration remains a focus area: a small number of large customers, including hyperscale operators, contributed a meaningful share of segment revenue in the period."),
    ("Item 1A. Risk Factors",
     "Our financial results are dependent on our ability to introduce new products on schedule and to gain market acceptance for those products against well-resourced competitors. We rely on a small number of third-party foundries — principally TSMC — for substantially all of our wafer manufacturing, and any disruption in advanced-node capacity allocation could materially affect our ability to fulfill demand. Concentration in our top customers exposes us to volatility in any single customer's purchasing cadence, and changes in U.S. export-control rules could limit shipments to specific geographies."),
    ("Item 7. MD&A",
     "Data Center segment revenue grew year-over-year, driven by increased adoption of EPYC server processors among cloud and enterprise customers, partially offset by softness in adjacent accelerator categories. Client segment revenue reflected continued normalization in PC end-markets following pandemic-era pull-forward; we expect inventory rebalancing at OEM partners to continue near-term before returning to a more typical seasonal pattern. Gross margin was within our targeted range, with mix toward higher-margin Data Center products partially offsetting price pressure in Client. Operating expenses reflected continued investment in product development and go-to-market for our next-generation accelerator portfolio."),
]

SEC_8K_TEMPLATES = [
    ("Item 2.02 Results of Operations",
     "On the date hereof, {ticker} issued a press release announcing the financial results of the company for the quarter then ended. The information furnished pursuant to this Item 2.02 and the related exhibit shall not be deemed 'filed' for purposes of Section 18 of the Securities Exchange Act of 1934 and shall not be incorporated by reference into any filing under the Securities Act of 1933 or the Exchange Act."),
    ("Item 7.01 Regulation FD Disclosure",
     "{ticker} reaffirmed its full-year revenue outlook and noted continued strength in its Data Center segment, partially offset by ongoing normalization in client end-markets. The company indicated it remained on track to begin volume shipments of its next-generation data-center accelerator portfolio in the second half of the fiscal year."),
    ("Item 8.01 Other Events",
     "The company entered into a multi-year strategic supply-and-collaboration arrangement with a global cloud service provider, under which the customer will deploy {ticker}'s next-generation EPYC server processors and Instinct accelerators in its public-cloud and internal AI training fleets. Specific commercial terms were not disclosed."),
    ("Item 5.02 Departure of Directors or Certain Officers",
     "Effective as of the date hereof, the Board of Directors expanded by one seat and appointed a new independent director with extensive experience in cloud infrastructure and enterprise software. The new director will serve on the Audit Committee."),
]

PEER_NEWS_UP = [
    ("nvidia_dc",
     "Nvidia's data-center revenue grew sharply on accelerating AI training demand, with management raising the near-term ceiling on supply-constrained shipments. The read-through tilted positive for any data-center accelerator supplier with disclosed hyperscaler engagement."),
    ("tsmc_capacity",
     "TSMC reported advanced-node utilization remained at capacity through the quarter, with leading-edge wafer pricing power intact. The release flagged that customers on N4/N5 nodes — a group that includes both AMD and Nvidia — continued to absorb allocated wafer supply."),
    ("intel_delay",
     "Intel disclosed that the volume ramp of its next-generation data-center CPU would slip by approximately one quarter relative to prior guidance. Buy-side desks viewed the slip as adding incremental share-gain runway for AMD's EPYC franchise."),
    ("ai_hyperscaler",
     "A top-three cloud operator told investors that infrastructure spending would step up materially in the coming fiscal year, with the bulk of incremental spend tilted to AI training and inference fleets. The disclosure boosted AI-exposed semi names broadly."),
]

PEER_NEWS_DOWN = [
    ("nvidia_china",
     "Nvidia disclosed that revised U.S. export-control rules would meaningfully reduce its addressable market for advanced AI accelerators in China through the next two quarters. The disclosure dragged the broader AI-accelerator complex lower in sympathy."),
    ("intel_pcsoftness",
     "Intel pre-announced softer-than-expected client-PC revenue, citing OEM inventory adjustments and weaker consumer demand at major retailers. The read across to {ticker}'s Client segment was unambiguous."),
    ("micron_pricing",
     "Micron noted DRAM and NAND pricing remained under pressure as oversupply lingered, with management not yet calling a bottom. Memory weakness has historically flowed to system-builder demand for adjacent compute components."),
    ("tsm_capex_cut",
     "TSMC trimmed its 2024 capital-expenditure budget by mid-single-digit billions, citing softer near-term demand visibility. The cut tempered investor enthusiasm across the broader semiconductor capital-equipment and design ecosystem."),
]

SECTOR_NEWS_UP = [
    ("sox_rally",
     "The PHLX Semiconductor Index posted its strongest week in months, led by AI-exposed names. Sell-side notes flagged the resilience of forward bookings at U.S. design houses despite mixed signals from the broader hardware tape."),
    ("ai_capex_forecast",
     "Equipment-makers raised 2025 AI-infrastructure spending forecasts, with the bulk of incremental revenue concentrated in advanced packaging and high-bandwidth memory — both supply chains in which {ticker} is a significant consumer."),
    ("inventory_cleared",
     "Distributor commentary across the semiconductor channel suggested inventory destocking was largely behind the industry, with replenishment orders modestly inflecting at U.S. and European OEMs after several quarters of correction."),
]

SECTOR_NEWS_DOWN = [
    ("china_export",
     "Treasury and Commerce released updated export-control guidance covering advanced semiconductors and tooling. Industry trade groups warned of a measurable revenue hit to U.S. AI-accelerator suppliers shipping into China-domiciled cloud and AI customers."),
    ("pc_units",
     "IDC and Gartner cut their 2024 PC unit forecasts after a soft consumer back-to-school cycle and lingering enterprise refresh hesitancy. The downgrade hit CPU-exposed names disproportionately in the session that followed."),
    ("rates_pressure",
     "Long-end yields pushed higher after a hotter-than-expected inflation print, pressuring high-multiple semiconductor names. Sector ETFs traded down on elevated volume into the close."),
]

THIRTEEN_F_TEMPLATES = [
    ("Vanguard Total Stock Market Index",
     "Vanguard Group, the largest passive holder of {ticker}, reported increasing its position by approximately 1.5 million shares in the quarter then ended, consistent with broad-index inflows over the period. The position remains the largest single 13F holding of {ticker} shares."),
    ("BlackRock iShares",
     "BlackRock reported trimming its aggregate {ticker} stake by roughly 0.8 million shares, primarily through redemptions in its Technology Select sector ETFs. The reduction represented a small fraction of the firm's overall semiconductor exposure."),
    ("Fidelity Contrafund",
     "Fidelity Contrafund, a top-five active holder, increased its {ticker} position by approximately 2.3 million shares during the quarter. The disclosure aligned with the fund's manager comments noting elevated conviction in AI-accelerator-exposed semiconductor names."),
    ("Bridgewater Associates",
     "Bridgewater initiated a new position of approximately 0.5 million {ticker} shares as part of its risk-parity rebalancing into U.S. growth-tilted equities. The position is small relative to Bridgewater's overall U.S. equity sleeve but represents fresh demand in the quarter."),
    ("Renaissance Technologies",
     "Renaissance Technologies disclosed a reduction of roughly 1.1 million {ticker} shares, consistent with the fund's quantitative-overlay trimming after the stock's recent run. Position turnover at the fund is structurally elevated and not typically indicative of a directional view."),
]


# ───────────────────────────── helpers ─────────────────────────────

def _seed_rng(move_date: str) -> random.Random:
    """Deterministic per-event RNG so re-runs produce identical chunks."""
    h = hashlib.sha256(f"{TICKER}:{move_date}".encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def _mock_chunk_id(source: str, move_date: str, n: int) -> str:
    return f"{source}_{TICKER}_{move_date}_{MOCK_TAG}_{n:03d}"


def _publication_date_for(source: str, move_date: str, rng: random.Random) -> str:
    """Pick a plausible publication_date ≤ move_date for this source.
    Respects the as-of discipline: filings/news must pre-date the event."""
    move = date.fromisoformat(move_date)
    if source == "news":
        delta = rng.randint(0, 1)  # T-1 or T (intraday news pre-event)
    elif source == "peer_news":
        delta = rng.randint(1, 6)
    elif source == "sector_news":
        delta = rng.randint(1, 10)
    elif source == "sec_8k":
        delta = rng.randint(2, 18)
    elif source == "sec_10k":
        # Most recent 10-K — filed in Feb of the year of the event for FY-1
        ten_k = date(move.year, 2, 14) if move.month >= 3 else date(move.year - 1, 2, 14)
        return ten_k.isoformat()
    elif source == "thirteen_f":
        # Quarterly filings, ~45 days after quarter end. Pick the most
        # recent before the event.
        q_ends = [
            date(move.year, 3, 31), date(move.year, 6, 30),
            date(move.year, 9, 30), date(move.year, 12, 31),
            date(move.year - 1, 12, 31),
        ]
        cutoff_lag = timedelta(days=45)
        candidates = [q + cutoff_lag for q in q_ends if q + cutoff_lag < move]
        return max(candidates).isoformat() if candidates else (move - timedelta(days=90)).isoformat()
    else:
        delta = rng.randint(0, 30)
    return (move - timedelta(days=delta)).isoformat()


def _make_chunk(source: str, section: str, body: str, move_date: str,
                rng: random.Random, n: int, ticker_in_text: bool = True) -> dict:
    pub = _publication_date_for(source, move_date, rng)
    text = body.format(ticker=TICKER, date=move_date) if ticker_in_text else body
    return {
        "chunk_id": _mock_chunk_id(source, move_date, n),
        "source_type": source,
        "publication_date": pub,
        "section_name": section,
        "source_url": f"{MOCK_URL_PREFIX}/{source}/{TICKER}/{pub}",
        "text": text,
    }


def _pool_for(source: str, direction_up: bool) -> list[tuple[str, str]]:
    return {
        ("news",        True):  NEWS_UP,
        ("news",        False): NEWS_DOWN,
        ("peer_news",   True):  PEER_NEWS_UP,
        ("peer_news",   False): PEER_NEWS_DOWN,
        ("sector_news", True):  SECTOR_NEWS_UP,
        ("sector_news", False): SECTOR_NEWS_DOWN,
        ("sec_10k",     True):  SEC_10K_TEMPLATES,
        ("sec_10k",     False): SEC_10K_TEMPLATES,
        ("sec_8k",      True):  SEC_8K_TEMPLATES,
        ("sec_8k",      False): SEC_8K_TEMPLATES,
        ("thirteen_f",  True):  THIRTEEN_F_TEMPLATES,
        ("thirteen_f",  False): THIRTEEN_F_TEMPLATES,
    }[(source, direction_up)]


# How many chunks to inject per source for the bundled view. These are
# the items the UI's Evidence panel can render; the toggle availability
# count is bumped to a larger plausible total below.
BUNDLE_COUNTS = {
    "news":        5,
    "peer_news":   4,
    "sector_news": 3,
    "sec_10k":     3,
    "sec_8k":      3,
    "thirteen_f":  4,
}

# Pretend the full source pool is larger than what's bundled — matches
# the (chunks_available >> bundled chunks shown) pattern of real data.
AVAILABLE_COUNTS = {
    "news":        12,
    "peer_news":   9,
    "sector_news": 6,
    "sec_10k":     14,
    "sec_8k":      7,
    "thirteen_f":  8,
}


def inject_for_move(move: dict) -> int:
    """Add mock chunks for every missing source. Returns count added."""
    move_date = move["move_date"]
    if date.fromisoformat(move_date) >= CUTOFF:
        return 0

    return_pct = move.get("return_pct") or 0.0
    direction_up = return_pct >= 0
    rng = _seed_rng(move_date)

    avail = dict(move.get("chunks_available") or {})
    chunks = list(move.get("chunks") or [])
    added = 0

    for source, total_target in AVAILABLE_COUNTS.items():
        current = int(avail.get(source, 0))
        if current > 0:
            continue  # already has real data — don't touch it
        pool = _pool_for(source, direction_up)
        n_bundled = BUNDLE_COUNTS[source]
        # Sample without replacement (extend pool by shuffling repeats if needed)
        templates = pool[:]
        rng.shuffle(templates)
        chosen = []
        while len(chosen) < n_bundled:
            chosen.extend(templates[:n_bundled - len(chosen)])
        for idx, (section, body) in enumerate(chosen):
            chunks.append(_make_chunk(source, section, body, move_date, rng, idx))
            added += 1
        avail[source] = total_target

    move["chunks"] = chunks
    move["chunks_available"] = avail
    move["chunks_total"] = int(move.get("chunks_total") or 0) + sum(
        AVAILABLE_COUNTS[s] for s in AVAILABLE_COUNTS if avail.get(s, 0) == AVAILABLE_COUNTS[s]
    ) - sum(
        # subtract whatever the previous total claimed for these sources (0)
        0 for _ in AVAILABLE_COUNTS
    )
    return added


def main() -> None:
    bundle = json.loads(DATA_PATH.read_text())
    total_added = 0
    moves_touched = 0
    for m in bundle.get("moves", []):
        added = inject_for_move(m)
        if added > 0:
            total_added += added
            moves_touched += 1
    DATA_PATH.write_text(json.dumps(bundle, indent=2) + "\n")
    print(f"Injected {total_added} mock chunks across {moves_touched} pre-{CUTOFF.isoformat()} moves.")


if __name__ == "__main__":
    main()
