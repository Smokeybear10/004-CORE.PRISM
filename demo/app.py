"""
Streamlit demo — the clickable attribution dashboard.

Run from the project root:
    streamlit run demo/app.py

Reads from demo.mock_data today so it renders before ingestion/model/backtest
are wired up. Swap the factory imports below for live pipeline calls as each
module lands. The UI flow (events → attribution → evidence) is source-agnostic.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the project root importable no matter where streamlit is launched from.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import streamlit as st

from demo.mock_data import (
    ABLATIONS,
    DEFAULT_TICKER,
    all_chunks,
    sample_attributions,
    sample_backtest_results,
    sample_moves,
)
from schema import Attribution, TextChunk


st.set_page_config(page_title="Price Action Tagger", layout="wide")


# ---------- Data ----------

@st.cache_data
def _load():
    moves = sample_moves()
    attrs = sample_attributions()
    chunks = all_chunks()
    backtests = sample_backtest_results()
    return moves, attrs, chunks, backtests


moves, attributions, chunks, backtests = _load()
chunks_by_id: dict[str, TextChunk] = {c.chunk_id: c for c in chunks}
attr_by_key: dict[tuple[str, str], Attribution] = {
    (str(a.move_date), a.ablation_name or ""): a for a in attributions
}


# ---------- Sidebar ----------

st.sidebar.title("Price Action Tagger")
st.sidebar.caption("Structural vs transient — attribution-driven.")

ticker = st.sidebar.selectbox("Ticker", [DEFAULT_TICKER])

ablation_names = [a.name for a in ABLATIONS]
selected_ablation = st.sidebar.radio(
    "Ablation",
    ablation_names,
    index=len(ablation_names) - 1,  # default to +macro (full stack)
    help="Each row adds one more data source on top of the prior. This is the demo goldmine.",
)

st.sidebar.markdown("---")
st.sidebar.markdown(
    "**Data source**\n\n"
    "Reading `demo/mock_data.py`. Replace with live calls from `ingestion/`, "
    "`model/`, `backtest/` as those modules land."
)


# ---------- Header: Ablation comparison chart ----------

st.title(f"{ticker} — Price Move Attribution")
st.markdown(
    "_Financial historian: decompose qualitative language into structured dimensions,"
    " then decide **lean** (structural) or **fade** (transient)._"
)

st.subheader("Ablation comparison")
st.caption("Additive-testing: each bar is one more data source layered on. Mentor's demo goldmine.")

bt_rows = [b.model_dump() for b in backtests]
bt_df = pd.DataFrame(bt_rows).set_index("ablation_name")

col1, col2, col3 = st.columns(3)
with col1:
    st.bar_chart(bt_df["hit_rate"], use_container_width=True)
    st.caption("Hit rate")
with col2:
    st.bar_chart(bt_df["sharpe"], use_container_width=True)
    st.caption("Sharpe")
with col3:
    st.bar_chart(bt_df["avg_return"], use_container_width=True)
    st.caption("Avg return per trade")

with st.expander("Why this chart matters"):
    st.markdown(
        "Additive testing tells us which data sources actually shift the signal. "
        "A flat bar means that source is noise on this universe; a step-up means "
        "it carries structural information the model couldn't derive without it. "
        "This is the demo sentence we're engineering toward: "
        "*'10-K language is the biggest attribution driver; peer news adds 15% more signal; "
        "macro closes the predicted-vs-realized gap.'*"
    )


# ---------- Events + attribution (the clickable core) ----------

st.subheader("Flagged price moves")
st.caption(
    "Moves that cleared the significance threshold (vol z-score or top 5% "
    "magnitude). Click a row to see what the model thinks caused it."
)

move_options = [
    {
        "label": f"{m.move_date} · {m.return_pct:+.2%} · z={m.vol_zscore:+.1f}",
        "date": str(m.move_date),
        "move": m,
    }
    for m in sorted(moves, key=lambda m: m.move_date, reverse=True)
]

selected_label = st.radio(
    "Move",
    options=[o["label"] for o in move_options],
    horizontal=True,
    label_visibility="collapsed",
)
selected_idx = next(i for i, o in enumerate(move_options) if o["label"] == selected_label)
selected = move_options[selected_idx]
selected_date = selected["date"]
selected_move = selected["move"]

# Context row: all moves
ctx_df = pd.DataFrame([m.model_dump() for m in moves if m.ticker == ticker])
ctx_df["return_pct"] = ctx_df["return_pct"].map(lambda x: f"{x:+.2%}")
ctx_df["vol_zscore"] = ctx_df["vol_zscore"].map(lambda x: f"{x:+.2f}")
ctx_df = ctx_df[["move_date", "return_pct", "vol_zscore", "volume_zscore",
                 "magnitude_rank", "is_significant"]]
st.dataframe(ctx_df, use_container_width=True, hide_index=True)


# ---------- Attribution detail ----------

attr = attr_by_key.get((selected_date, selected_ablation))

st.subheader(f"Attribution · {selected_date} · `{selected_ablation}`")

if attr is None:
    st.warning(f"No attribution for {selected_date} under `{selected_ablation}`. "
               "Mock data only covers the 3 sample moves × 5 ablations.")
    st.stop()

m1, m2, m3, m4 = st.columns(4)
m1.metric("Realized", f"{attr.return_pct:+.2%}")
m2.metric(
    "Predicted",
    f"{attr.predicted_return_pct:+.2%}" if attr.predicted_return_pct is not None else "—",
    delta=(f"{(attr.predicted_return_pct - attr.return_pct):+.2%} gap"
           if attr.predicted_return_pct is not None else None),
    delta_color="off",
)
m3.metric("Character", attr.move_character)
m4.metric("Confidence", f"{attr.confidence:.0%}")

# Dimension weights
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

# Rationales + citations
st.markdown("### Rationale + evidence")
st.caption("Every dimension must cite at least one real chunk_id. Missing citations fail coherence.")

ordered = sorted(dims.items(), key=lambda kv: kv[1].weight, reverse=True)
for name, score in ordered:
    arrow = {"positive": "↑", "negative": "↓", "neutral": "→"}[score.direction]
    header = f"**{name}**  ·  weight {score.weight:.2f}  ·  {arrow} {score.direction}"
    with st.expander(header, expanded=(score.weight >= 0.25)):
        st.write(score.rationale)
        st.markdown("**Cited evidence:**")
        for cid in score.evidence_chunk_ids:
            chunk = chunks_by_id.get(cid)
            if chunk is None:
                st.error(f"Missing chunk `{cid}` — coherence check would reject this attribution.")
                continue
            meta = (
                f"`{cid}` · **{chunk.source_type.value}** · {chunk.publication_date}"
                + (f" · _{chunk.section_name}_" if chunk.section_name else "")
            )
            st.markdown(meta)
            snippet = chunk.text if len(chunk.text) <= 500 else chunk.text[:500] + "…"
            st.caption(snippet)
            if chunk.source_url:
                st.markdown(f"[source]({chunk.source_url})")

if attr.model_notes:
    st.info(f"**Model notes:** {attr.model_notes}")

st.markdown("---")
st.caption(
    f"Sources used: {', '.join(s.value for s in attr.sources_used)}  ·  "
    f"chunks considered: {attr.chunks_considered}"
)
