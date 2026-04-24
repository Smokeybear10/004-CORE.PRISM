"""
Clickable attribution demo.

Flow:
    ticker → price chart (significant moves highlighted) → click a move →
    attribution panel (dimensions + cited evidence + coherence badge).

Prices + significant moves come from Srilekha's `ingestion.prices` pipeline.
Attribution comes from `model.attribution` via `demo.live_attribution.get_attribution`
(live by default; falls back to `demo.mock_data` on API failure, missing key,
or empty evidence). Flip the "Use mock data" toggle in the sidebar to force
the mock path without hitting the API.

Run from the project root:
    streamlit run demo/app.py
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Make the project root importable regardless of streamlit's launch CWD.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from demo.live_attribution import AttributionResult, get_attribution
from demo.mock_data import (
    ABLATIONS,
    DEFAULT_TICKER,
    FOCAL_TICKERS,
)
from ingestion.events import build_events_parquet
from ingestion.prices import detect_significant_moves, load_prices
from schema import PriceMove

logger = logging.getLogger(__name__)

st.set_page_config(page_title="Price Action Tagger", layout="wide")


# ---------- Data loaders (streamlit-cached) ----------

@st.cache_data(show_spinner="Loading price panel…")
def _load_prices(ticker: str, as_of: date) -> pd.DataFrame:
    return load_prices([ticker], as_of=as_of)


@st.cache_data(show_spinner="Detecting significant moves…")
def _load_moves(ticker: str, as_of_iso: str) -> list[PriceMove]:
    as_of = date.fromisoformat(as_of_iso)
    df = _load_prices(ticker, as_of)
    return detect_significant_moves(df)


# Empty-frame factories — match the columns join_evidence expects.

def _empty_events_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "event_id", "ticker", "event_date", "event_type",
        "source", "payload_ref", "text",
    ])


def _empty_chunks_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "chunk_id", "ticker", "source_type", "publication_date",
        "period_end", "source_url", "section_name", "text", "token_count",
    ])


def _empty_earnings_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["ticker", "report_date"])


@st.cache_data(show_spinner="Loading events…")
def _load_events_frames():
    """Return (events_df, chunks_df, earnings_calendar) for join_evidence.

    Reads data/cache/events.parquet + text_chunks.parquet if present;
    otherwise runs the aggregator once to populate them. If no source parquets
    exist at all, returns empty frames with the right columns — join_evidence
    handles those gracefully. Earnings calendar is loaded separately from
    data/earnings/calendar_*.parquet (not cached by the aggregator).
    """
    events_path = Path("data/cache/events.parquet")
    chunks_path = Path("data/cache/text_chunks.parquet")

    if not events_path.exists() or not chunks_path.exists():
        try:
            build_events_parquet(
                as_of=date.today(),
                out_path=events_path,
                data_dir=Path("data"),
                chunks_out_path=chunks_path,
            )
        except Exception as e:  # pragma: no cover — defensive UI path
            logger.warning("events aggregator run failed: %s", e)

    events_df = pd.read_parquet(events_path) if events_path.exists() else _empty_events_df()
    chunks_df = pd.read_parquet(chunks_path) if chunks_path.exists() else _empty_chunks_df()

    earnings_cal = _load_earnings_calendar()
    return events_df, chunks_df, earnings_cal


def _load_earnings_calendar() -> pd.DataFrame:
    calendar_paths = sorted(Path("data/earnings").glob("calendar_*.parquet"))
    if not calendar_paths:
        return _empty_earnings_df()
    try:
        dfs = [pd.read_parquet(p) for p in calendar_paths]
        cal = pd.concat(dfs, ignore_index=True)
    except Exception as e:  # pragma: no cover
        logger.warning("earnings calendar load failed: %s", e)
        return _empty_earnings_df()

    if "symbol" in cal.columns and "ticker" not in cal.columns:
        cal = cal.rename(columns={"symbol": "ticker"})
    keep = [c for c in ("ticker", "report_date") if c in cal.columns]
    if set(keep) != {"ticker", "report_date"}:
        return _empty_earnings_df()
    return cal[["ticker", "report_date"]]


@st.cache_data(show_spinner="Running attribution…")
def _cached_attribution(
    ticker: str,
    move_date_iso: str,
    ablation_name: str,
    use_mock: bool,
    # Non-key-affecting args (Streamlit still hashes these; we keep them
    # stable by construction since move is uniquely identified by ticker+date).
    return_pct: float,
    vol_zscore: float,
    magnitude_rank: float | None,
    volume_zscore: float | None,
    is_significant: bool,
) -> AttributionResult:
    move = PriceMove(
        ticker=ticker,
        move_date=date.fromisoformat(move_date_iso),
        return_pct=return_pct,
        vol_zscore=vol_zscore,
        magnitude_rank=magnitude_rank,
        volume_zscore=volume_zscore,
        is_significant=is_significant,
    )
    events_df, chunks_df, earnings_cal = _load_events_frames()
    return get_attribution(
        ticker=ticker,
        move=move,
        ablation_name=ablation_name,
        events_df=events_df,
        chunks_df=chunks_df,
        earnings_calendar=earnings_cal,
        use_mock=use_mock,
    )


# ---------- Sidebar ----------

st.sidebar.title("Price Action Tagger")
st.sidebar.caption("Structural vs transient — attribution-driven.")

tickers = list(FOCAL_TICKERS.keys())
ticker = st.sidebar.selectbox(
    "Ticker",
    tickers,
    index=tickers.index(DEFAULT_TICKER) if DEFAULT_TICKER in tickers else 0,
    format_func=lambda t: f"{t} · {FOCAL_TICKERS[t]['name']}",
)
meta = FOCAL_TICKERS[ticker]
st.sidebar.caption(f"Sector: **{meta['sector']}**")

today = date.today()
default_start = today - timedelta(days=5 * 365)
window = st.sidebar.slider(
    "Chart window",
    min_value=date(2000, 1, 1),
    max_value=today,
    value=(default_start, today),
    format="YYYY-MM",
)
start_date, end_date = window

# Ablation selector — default "+macro" (the fullest-stack ablation).
ablation_names = [a.name for a in ABLATIONS]
default_ablation_idx = ablation_names.index("+macro") if "+macro" in ablation_names else 0
ablation_name = st.sidebar.selectbox(
    "Ablation",
    ablation_names,
    index=default_ablation_idx,
    help="Which source set the attribution LLM sees. +macro is the full stack.",
)

# Mock toggle. If ANTHROPIC_API_KEY is absent AND user hasn't ticked mock,
# auto-flip for this session and show a warning — the live path would fail.
use_mock_toggle = st.sidebar.checkbox(
    "Use mock data",
    value=False,
    help="Skip the live API call and render a deterministic mock attribution.",
)
api_key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
if not api_key_present and not use_mock_toggle:
    st.sidebar.warning(
        "`ANTHROPIC_API_KEY` not set — auto-flipping to mock for this session. "
        "Export the key to run the live pipeline."
    )
    use_mock = True
else:
    use_mock = use_mock_toggle

st.sidebar.markdown("---")
st.sidebar.caption(
    "Prices + flagged moves → `ingestion.prices` (live).  \n"
    "Events + evidence → `ingestion.events`.  \n"
    "Attribution → `model.attribution`"
    + (" (mock)" if use_mock else " (live)")
    + "."
)


# ---------- Load data ----------

prices = _load_prices(ticker, end_date)
prices = prices[(prices["date"] >= start_date) & (prices["date"] <= end_date)].reset_index(drop=True)

all_moves = _load_moves(ticker, end_date.isoformat())
moves = [m for m in all_moves if start_date <= m.move_date <= end_date]


# ---------- Header ----------

st.title(f"{ticker} · {meta['name']}")
st.markdown(
    f"_Financial historian on a {meta['sector']} name._ "
    "Decompose each significant move into 5 dimensions with cited evidence, "
    "then decide lean (structural) or fade (transient)."
)


# ---------- Price chart ----------

st.subheader("Price with flagged moves")
if prices.empty:
    st.warning(f"No price data for {ticker} in [{start_date}, {end_date}].")
    st.stop()

fig = go.Figure()
fig.add_trace(
    go.Scatter(
        x=prices["date"], y=prices["close"],
        mode="lines", line=dict(width=1.6, color="#1f77b4"),
        name="Close",
        hovertemplate="%{x|%Y-%m-%d}<br>$%{y:.2f}<extra></extra>",
    )
)

if moves:
    move_df = pd.DataFrame([m.model_dump() for m in moves])
    move_df["date"] = pd.to_datetime(move_df["move_date"])
    price_by_date = dict(zip(pd.to_datetime(prices["date"]), prices["close"]))
    move_df["close"] = move_df["date"].map(price_by_date)
    move_df = move_df.dropna(subset=["close"])

    fig.add_trace(
        go.Scatter(
            x=move_df["date"],
            y=move_df["close"],
            mode="markers",
            marker=dict(
                size=10,
                color=["#d62728" if r < 0 else "#2ca02c" for r in move_df["return_pct"]],
                line=dict(color="white", width=1),
                symbol="circle",
            ),
            name="Flagged move",
            customdata=move_df[["return_pct", "vol_zscore"]].to_numpy(),
            hovertemplate=(
                "<b>%{x|%Y-%m-%d}</b><br>"
                "close $%{y:.2f}<br>"
                "return %{customdata[0]:+.2%}<br>"
                "vol z %{customdata[1]:+.2f}"
                "<extra></extra>"
            ),
        )
    )

fig.update_layout(
    height=420,
    margin=dict(l=10, r=10, t=10, b=10),
    hovermode="closest",
    showlegend=True,
    legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0.0),
    xaxis_title=None,
    yaxis_title="Close ($)",
)
st.plotly_chart(fig, use_container_width=True)

m1, m2, m3 = st.columns(3)
m1.metric("Trading days shown", f"{len(prices):,}")
m2.metric("Flagged moves in window", f"{len(moves):,}")
neg = sum(1 for m in moves if m.return_pct < 0)
pos = len(moves) - neg
m3.metric("Down / Up", f"{neg} ↓ / {pos} ↑")


# ---------- Move selector ----------

if not moves:
    st.info("No flagged moves in this window. Widen the chart window in the sidebar.")
    st.stop()

st.subheader("Pick a flagged move to inspect")

sorted_moves = sorted(moves, key=lambda m: m.move_date, reverse=True)
options = [
    f"{m.move_date} · {m.return_pct:+.2%} · z={m.vol_zscore:+.1f}"
    for m in sorted_moves
]
default_idx = max(range(len(sorted_moves)),
                  key=lambda i: abs(sorted_moves[i].return_pct))
selected_label = st.selectbox("Move", options, index=default_idx)
selected_move = sorted_moves[options.index(selected_label)]


# ---------- Attribution panel ----------

st.subheader(f"Attribution · {selected_move.move_date}")

# Track whether this attribution was freshly computed this render ("live")
# or served from the Streamlit cache ("live (cached)").
if "_attribution_keys_seen" not in st.session_state:
    st.session_state["_attribution_keys_seen"] = set()
cache_key = (ticker, selected_move.move_date.isoformat(), ablation_name, use_mock)
was_cached = cache_key in st.session_state["_attribution_keys_seen"]
st.session_state["_attribution_keys_seen"].add(cache_key)

result: AttributionResult = _cached_attribution(
    ticker=ticker,
    move_date_iso=selected_move.move_date.isoformat(),
    ablation_name=ablation_name,
    use_mock=use_mock,
    return_pct=selected_move.return_pct,
    vol_zscore=selected_move.vol_zscore,
    magnitude_rank=selected_move.magnitude_rank,
    volume_zscore=selected_move.volume_zscore,
    is_significant=selected_move.is_significant,
)
attr = result.attribution
chunks = result.chunks
chunks_by_id = {c.chunk_id: c for c in chunks}

# Status caption — demo honesty signal. Do not remove.
if result.source == "live" and was_cached:
    source_label = "live (cached)"
elif result.source == "live":
    source_label = "live"
else:
    if result.error:
        source_label = f"mock fallback — {result.error}"
    else:
        source_label = "mock fallback"
st.caption(f"**Attribution source:** {source_label}")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Realized", f"{attr.return_pct:+.2%}")
k2.metric(
    "Predicted",
    f"{attr.predicted_return_pct:+.2%}" if attr.predicted_return_pct is not None else "—",
    delta=(f"{(attr.predicted_return_pct - attr.return_pct):+.2%} gap"
           if attr.predicted_return_pct is not None else None),
    delta_color="off",
)
k3.metric("Character", attr.move_character)

# Coherence badge next to confidence.
if result.coherence is not None:
    if result.coherence.plausible:
        badge = "✓ plausible"
    else:
        first_issue = result.coherence.issues[0] if result.coherence.issues else "flagged"
        badge = f"⚠ {first_issue[:40]}"
    k4.metric("Confidence", f"{attr.confidence:.0%}", delta=badge, delta_color="off")
else:
    k4.metric("Confidence", f"{attr.confidence:.0%}")

st.markdown("### Dimension weights")
dims = {
    "demand": attr.demand,
    "pricing": attr.pricing,
    "competitive": attr.competitive,
    "management_credibility": attr.management_credibility,
    "macro": attr.macro,
}
dim_df = pd.DataFrame(
    [{"dimension": k, "weight": v.weight, "direction": v.direction}
     for k, v in dims.items()]
).set_index("dimension")
st.bar_chart(dim_df["weight"], use_container_width=True)

st.markdown("### Rationale + evidence")
st.caption("Every dimension cites at least one chunk_id (CLAUDE.md rule #6).")

for name, score in sorted(dims.items(), key=lambda kv: kv[1].weight, reverse=True):
    arrow = {"positive": "↑", "negative": "↓", "neutral": "→"}[score.direction]
    header = f"**{name}** · weight {score.weight:.2f} · {arrow} {score.direction}"
    with st.expander(header, expanded=(score.weight >= 0.25)):
        st.write(score.rationale)
        st.markdown("**Cited evidence:**")
        for cid in score.evidence_chunk_ids:
            chunk = chunks_by_id.get(cid)
            if chunk is None:
                st.error(f"Missing chunk `{cid}` — coherence check would reject this attribution.")
                continue
            meta_line = (
                f"`{cid}` · **{chunk.source_type.value}** · {chunk.publication_date}"
                + (f" · _{chunk.section_name}_" if chunk.section_name else "")
            )
            st.markdown(meta_line)
            snippet = chunk.text if len(chunk.text) <= 500 else chunk.text[:500] + "…"
            st.caption(snippet)
            if chunk.source_url:
                st.markdown(f"[source]({chunk.source_url})")

if attr.model_notes:
    st.info(attr.model_notes)

# Coherence issues panel — only shown when coherence fired and found problems.
if result.coherence is not None and not result.coherence.plausible and result.coherence.issues:
    st.markdown("### Coherence flags")
    for issue in result.coherence.issues:
        st.warning(issue)
