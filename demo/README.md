# Demo

This is where the interpretability layer lives. Per mentor feedback, the demo
is the deliverable — NOT a trading strategy.

## Target artifact

A clickable chart: pick a ticker, see flagged moves, click a move to see the
attribution with cited evidence chunks. Mentor's exact ask:

> Let someone click on a date in your chart and see the news article, the
> attribution, and your model's decision, with the evidence spelled out.

## Planned notebooks / scripts

- `run_mvp.py` — end-to-end on one ticker, single ablation. Target for the next mentor check-in.
- `ablation_chart.ipynb` — side-by-side bars for each AblationConfig:
  base_news, +sec, +earnings, +peer_news, +sector, +macro.
  Y-axis: hit rate / plausibility / Sharpe. This is the demo goldmine.
- `case_study.ipynb` — deep dive on the frozen test case
  (whichever ticker/event the team picks). Show how attribution changes as
  ablations add sources.

## What good looks like (demo narrative)

For one flagged move, walk through each ablation:

1. **base_news** — attribution + confidence. Often wrong on macro-driven moves.
2. **+sec** — does the dominant dimension shift? Does confidence change?
3. **+peer_news** — can the model now see sector-wide weakness?
4. **+macro** — does `predicted_return_pct` align with realized?

Each bar is a defensible claim. "10-K language flips the dominant dimension in
N% of cases; peer news adds X% to hit rate; macro aligns predicted with
realized on Y% of moves." Whatever the numbers show, that's the story.
