# Demo

This is where the interpretability layer lives. Per mentor feedback, the demo
is the deliverable — NOT a trading strategy.

## Target artifact

A clickable chart: pick a ticker, see flagged moves, click a move to see the
attribution with cited evidence chunks. Mentor's exact ask:

> Let someone click on a date in your chart and see the news article, the
> attribution, and your model's decision, with the evidence spelled out.

## Orchestrator CLI — `demo.analyze_ticker`

End-to-end wiring for one ticker: loads prices, detects significant moves,
builds the events+chunks tables (if the cache is missing), joins evidence
per move, runs attribution + coherence, and writes the annotated-chart
JSON payload the frontend consumes.

```bash
# default out path: data/analysis/<TICKER>.json
python -m demo.analyze_ticker AMD 2024-10-01

# custom out path
python -m demo.analyze_ticker AMD 2024-10-01 --out /tmp/amd.json
```

Two positional args (`TICKER`, `AS_OF` ISO-8601) and one optional `--out`.
Progress is logged to stderr. Requires `ANTHROPIC_API_KEY` in the env for
the attribution + coherence calls; tests inject a fake client instead.

### Output shape (stable — frontend depends on it)

```jsonc
{
  "ticker": "AMD",
  "as_of": "2024-10-01",
  "n_moves": 3,
  "price_series": [{"date": "2024-01-02", "close": 143.9, "volume": 19821000}],
  "moves": [
    {
      "move_date": "2024-05-01",
      "return_pct": 0.09,
      "vol_zscore": 3.2,
      "magnitude_rank": 0.98,
      "earnings_day": true,
      "attribution": { /* Attribution.model_dump(mode="json") | null */ },
      "coherence":   { /* CoherenceCheck.model_dump(mode="json") | null */ },
      "evidence": {
        "events": [ /* Event.model_dump(mode="json") ... */ ],
        "chunks": [ /* TextChunk.model_dump(mode="json") ... */ ]
      },
      "error": null
    }
  ]
}
```

When attribution fails validation, `attribution` and `coherence` are `null`
and `error` carries the validator's message; the move is still listed.

### Library use

```python
from datetime import date
from demo.analyze_ticker import analyze_ticker
payload = analyze_ticker("AMD", date(2024, 10, 1))
```

## Planned notebooks / scripts

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
