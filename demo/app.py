"""
Clickable attribution demo.

Flow:
    ticker → price chart (significant moves highlighted) → click a move →
    attribution panel (dimensions + cited evidence).

Prices + significant moves come from Srilekha's live pipeline
(`ingestion.prices`). Attributions are still mock (`demo.mock_data`) because
`model.attribute()` is a stub — swap the factory call when Step 3 lands.

Run from the project root:
    streamlit run demo/app.py
"""

from __future__ import annotations

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

from demo.mock_data import (
    DEFAULT_TICKER,
    FOCAL_TICKERS,
    chunks_for,
    generate_attribution,
)
from ingestion.prices import detect_significant_moves, load_prices
from schema import PriceMove, TextChunk


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

st.sidebar.markdown("---")
st.sidebar.caption(
    "Prices + flagged moves → `ingestion.prices` (live).  \n"
    "Attribution text → `demo.mock_data` (mock until `model.attribute()` lands)."
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
# Default to the most dramatic move (highest |return|) in the window.
default_idx = max(range(len(sorted_moves)),
                  key=lambda i: abs(sorted_moves[i].return_pct))
selected_label = st.selectbox("Move", options, index=default_idx)
selected_move = sorted_moves[options.index(selected_label)]


# ---------- Attribution panel ----------

st.subheader(f"Attribution · {selected_move.move_date}")

attr = generate_attribution(
    ticker=ticker,
    move_date=selected_move.move_date,
    return_pct=selected_move.return_pct,
    ablation_name="+macro",
)
chunks: list[TextChunk] = chunks_for(ticker, selected_move.move_date)
chunks_by_id = {c.chunk_id: c for c in chunks}

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
