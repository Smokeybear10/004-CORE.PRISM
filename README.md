# Equity Price Action Tagger

Bridgewater hackathon — Track 1. See `CLAUDE.md` for full project spec, the 6-step
pipeline, and the MVP scope.

## Run the demo from a fresh clone

End-to-end, a brand-new clone to a working `http://127.0.0.1:8000`:

```bash
# 1. Clone and enter the repo
git clone <repo-url> BW_Hackathon
cd BW_Hackathon

# 2. Python env + deps
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. HuggingFace login — the news + earnings-transcripts parquets live in the
#    private BridgewaterAIHackathon/BW-AI-Hackathon repo. You must have been
#    granted access; if `huggingface-cli whoami` already prints your handle
#    you can skip this.
huggingface-cli login

# 4. (Optional but recommended) Anthropic key for live attribution. Without it
#    the server falls back to the synthetic placeholder and labels every
#    response `synthetic fixture: …`. The model_notes badge in the UI tells
#    you which path served the request.
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env
echo 'BW_USE_LIVE_ATTRIBUTION=1'   >> .env

# 5. Start the server
uvicorn demo.server:app --host 127.0.0.1 --port 8000

# 6. Open http://127.0.0.1:8000
```

### What happens at startup

The first boot prints four warm-up lines and downloads ~2.3 GB of parquets to
`ingestion/earnings_news/.cache/`:

```
[server] warming news parquet (this is the one-time startup cost)…
[server] news parquet indexed
[server] indexing peer + sector news…
[server] peer + sector news indexed
[server] 13F chunks loaded
[server] loading earnings-call transcripts…
[server] earnings transcripts loaded
```

First boot: a couple of minutes (HF download dominates). Subsequent boots:
~30–60s (parquets cached, only the in-memory index is rebuilt). Per-request
attribution after that is milliseconds.

### What ships in the repo vs. what's downloaded at runtime

Committed (small, needed at boot):

- `demo/static/data/{ABT,ACU,AIR,AMD,APD}.json` + `index.json` — pre-baked
  per-ticker bundles the frontend fetches on first paint. Without these the
  demo page shows "Error loading data".
- `data/thirteen_f/focal_chunks.jsonl` — pre-fetched 13F text (5 KB).
- `tests/fixtures/` — synthetic data for the test suite.

Downloaded on first run (large, gitignored):

- `ingestion/earnings_news/.cache/` — news + transcripts parquets from the
  private HF repo. Pulled lazily by `preload_news()` and
  `preload_earnings_transcripts()` at server startup.

Regenerated locally when you want fresh attributions in the static bundles:

```bash
# Re-bake demo/static/data/*.json (slow — runs the model per move).
python demo/build_static.py

# Re-fetch 13F chunks for the focal tickers (slow — EDGAR rate-limited).
python demo/build_13f_chunks.py
```

### Quickstart for development

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pytest tests/
```

Focal universe is currently `ABT, ACU, AIR, AMD, APD` (see `demo/mock_data.py`).

### Troubleshooting

- **"Error loading data" on the demo page** — `demo/static/data/index.json` is
  missing. You're either on a branch where the bundles weren't committed, or
  someone deleted them. Rebuild with `python demo/build_static.py` (slow) or
  pull from a branch that has them.
- **`huggingface_hub.errors.GatedRepoError`** — your HF account doesn't have
  access to `BridgewaterAIHackathon/BW-AI-Hackathon`. Ask the team for an
  invite, then re-run `huggingface-cli login`.
- **`synthetic fixture: ANTHROPIC_API_KEY missing`** in the model_notes badge —
  set `ANTHROPIC_API_KEY` in `.env` and `export BW_USE_LIVE_ATTRIBUTION=1`.
  The synthetic path still produces a coherent response, just not a real one.
- **Port 8000 in use** — pick another (`--port 8001`) and open that URL
  instead. The frontend hard-codes nothing port-specific; it fetches relative
  paths.

## Pipeline (6 steps, mentor framework)

| Step | Module | Input → Output |
|---|---|---|
| 1. Flag significant moves | `ingestion/prices/` | ticker → `list[PriceMove]` |
| 2. Ingest text | `ingestion/sec/`, `ingestion/earnings_news/` | ticker + date range → `list[TextChunk]` |
| 3. Attribute | `model/` | PriceMove + chunks → `Attribution` |
| 4. Add peer / macro / sector | `ingestion/macro/` + peer tickers | ablation inputs |
| 5. Coherence check | `model/check_coherence` | `Attribution` → `CoherenceCheck` |
| 6. Fade-or-follow | `backtest/` | Attribution + realized → lean/fade, backtest result |

## Module ownership

| Directory | Owner | Status |
|---|---|---|
| `ingestion/prices/` | Srilekha (yfinance-based) | stub |
| `ingestion/sec/` | Sophia | stubs + fixtures in place |
| `ingestion/earnings_news/` | [News lead TBD] | stub |
| `ingestion/macro/` | Henry (+ 13F via research) | stub |
| `model/` | shared — attribute/predict/coherence | stub |
| `backtest/` + `demo/` | shared — fade/follow + ablation chart | stub |

Team branches: `person1-sec`, `person2-yahoo`, `person3-research`, `person4-news`.

## Workflow (MVP first)

1. **Sprint to a crappy end-to-end MVP on ONE ticker before polishing anything.** Target for the next mentor check-in: one ticker, big moves flagged, company news + 10-Ks pulled, model produces `Attribution`, basic fade-or-follow output. No peer / macro yet.
2. After MVP works end-to-end, **add sources one at a time as ablations** — each is a demo talking point.
3. Each person works on their own branch. Build against `tests/fixtures/` while your real data source is in progress.
4. `pytest tests/` before every push.
5. Merge to `main` only after a teammate reviews.
6. **If you touch `schema.py`, post in team chat first.**

## Frozen test case

Once tickers are chosen, pick one (ticker, date) where the team knows what caused
the move. Store expected outputs in `tests/fixtures/<ticker>_<event>_expected.json`.
Don't change the test case mid-iteration — freeze inputs so you can isolate which
prompt change caused which output change. See CLAUDE.md for the detailed rules.

## Demo story (design backward from this)

The demo is **the journey, not just the result.** For one flagged move, walk the
audience through each ablation in order:

- **Run 1 — base news only:** attribution + confidence. Often wrong on macro-driven moves.
- **Run 2 — + SEC 10-K/10-Q:** does the dominant dimension shift? Does confidence change?
- **Run 3 — + peer news:** can the model now see sector-wide weakness?
- **Run 4 — + macro (Fed / VIX / commodities):** does `predicted_return_pct` align with realized?

Each ablation bar on the chart is a claim we can defend. "We found that 10-K
language moves the dominant dimension in N% of cases; peer news adds X% to hit
rate; macro flips the sign on Y% of moves." Whatever the numbers show, that's
the story.
