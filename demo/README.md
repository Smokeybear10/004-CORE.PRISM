# Demo

This is where the interpretability layer lives. Per mentor feedback, the demo
is the deliverable - NOT a trading strategy.

## Target artifact

A clickable chart: pick a ticker, see flagged moves, click a move to see the
attribution with cited evidence chunks. Mentor's exact ask:

> Let someone click on a date in your chart and see the news article, the
> attribution, and your model's decision, with the evidence spelled out.

## Planned notebooks / scripts

- `run_mvp.py` - end-to-end on AAPL, single ablation. Target for next mentor check-in.
- `ablation_chart.ipynb` - side-by-side bars for each AblationConfig:
  base_news, +sec, +earnings, +peer_news, +sector, +macro.
  Y-axis: hit rate / plausibility / Sharpe. This is the demo goldmine.
- `case_study_aapl_covid.ipynb` - deep dive on the frozen test case
  (tests/fixtures/aapl_march2020_expected.json). Show how attribution
  changes as ablations add sources.

## What good looks like (demo narrative)

> "Here's AAPL in March 2020. Run 1 (news only): model says 'pricing'. Wrong.
>  Run 2 (+10-K risk factors): 'macro + competitive'. Closer. Run 3 (+peer
>  news MSFT/GOOGL): sector-wide weakness visible, 'macro, high confidence'.
>  Run 4 (+Fed/VIX): confidence jumps, coherence passes, predicted return
>  within 2pp of realized. Character: transient. Fade signal hit."

Each ablation bar is a defensible claim.
