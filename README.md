# PRISM | Equity Price Action Tagger

**Live demo: [prism-004.vercel.app](https://prism-004.vercel.app/)**

A financial historian. Given a 3-sigma stock move, ingest the news, SEC filings, earnings transcripts, and 13F snapshots dated near it, then ask Claude to attribute the move across five dimensions — demand, pricing, competitive, management credibility, macro — each score citing the exact evidence chunks that drove it. Decide whether the market has it right (lean) or is overreacting to transient signals (fade).

Bridgewater AI Hackathon, Track 1.

## Quick Start

```bash
git clone <repo> PRISM && cd PRISM
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
huggingface-cli login                          # private BW dataset access
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env     # optional — synthetic fallback otherwise
echo 'BW_USE_LIVE_ATTRIBUTION=1'  >> .env
uvicorn demo.server:app --port 2004
```

Open http://127.0.0.1:2004. First boot pulls ~2.3 GB of parquets to `ingestion/earnings_news/.cache/` and takes a couple of minutes. Subsequent boots are 30-60s.

## System Architecture

Six layers, top-down. Full diagrams + per-section detail at http://127.0.0.1:2004/architecture.html once the server is running.

```
EXTERNAL     HuggingFace BW · SEC EDGAR · Finnhub · Anthropic API
   │
INGESTION    ingestion/{prices,sec,earnings_news,idiosyncratic,events}/
   │         → data/cache/*.parquet
   │
COMPUTE      detect_significant_moves()  → PriceMove
   │         chunks_for_real()           → TextChunk[]   (8 source streams)
   │
MODEL        model.attribute()           → Claude tool_use → 5-dim Attribution
   │         + coherence pass (Haiku)    → drops uncited / contradictory dims
   │
CACHE        data/cache/api_attribute/{ticker}_{date}_{sources}.json
   │         first call ~700 ms · cached read ~2 ms
   │
SERVE        FastAPI (:2004) · GET / · POST /api/attribute · /architecture.html
   │
BROWSER      Plotly chart · 5-dim cards · cited-evidence drawer
```

### The eight evidence streams

| Source | SourceType | Origin |
|---|---|---|
| News | `news` | Yahoo Finance + Finnhub |
| SEC 10-K | `sec_10k` | EDGAR Risk Factors + MD&A |
| SEC 8-K | `sec_8k` | EDGAR item codes (2.02, 5.02, 7.01, 8.01) |
| Earnings transcript | `earnings_transcript` | Yahoo Finance call transcripts |
| Peer news | `peer_news` | Yahoo news for family companies |
| Sector news | `sector_news` | Yahoo news for sector-wide stories |
| Macro | `macro` | FOMC + VIX + commodities |
| 13F positioning | `thirteen_f` | SEC 13F filings |

### The seven ablation bundles

Additive — each bundle adds one new source. The demo's "watch the attribution sharpen" story.

| Bundle | New addition |
|---|---|
| `base_news` | news only |
| `+sec` | + 10-K, 8-K |
| `+earnings` | + earnings transcripts |
| `+peer_news` | + peer companies |
| `+sector_news` | + sector-wide |
| `+macro` | + Fed / VIX / commodities |
| `+positioning` | + 13F holdings |

### Pipeline (6 mentor steps)

| Step | Module | Input → Output |
|---|---|---|
| 1. Flag significant moves | `ingestion/prices/` | ticker → `list[PriceMove]` |
| 2. Ingest text | `ingestion/sec/`, `ingestion/earnings_news/` | ticker + date → `list[TextChunk]` |
| 3. Attribute | `model/attribution/` | PriceMove + chunks → `Attribution` |
| 4. Add peer / macro / sector | `ingestion/idiosyncratic/`, `ingestion/events/` | ablation inputs |
| 5. Coherence check | `model/attribution/coherence` | `Attribution` → `CoherenceCheck` |
| 6. Fade-or-follow | `backtest/` | Attribution + realized → lean/fade + PnL |

### Cache & zero-spend demo mode

Every `/api/attribute` response is memoized to `data/cache/api_attribute/` keyed by `(ticker, move_date, enabled_sources)`. First miss bills Claude (~$0.75 on Opus); every subsequent identical request reads the file (~2 ms).

For a live demo with a hard zero-spend guarantee:

```bash
python demo/prewarm_cache.py                              # fill cache once
BW_CACHE_ONLY=1 uvicorn demo.server:app --port 2004       # 503 on miss, never bills
```

Set `BW_ATTRIBUTION_MODEL=claude-haiku-4-5` during prewarm to cut the fill cost from ~$470 to ~$15.

## Tech Stack

| Layer | Tools |
|---|---|
| Backend | Python 3.11, FastAPI, uvicorn, Pydantic |
| Data | HuggingFace datasets, pyarrow / parquet, edgartools |
| Model | Anthropic Claude — Opus 4.7 (attribution) + Haiku 4.5 (coherence) |
| Frontend | Vanilla JS, Plotly, no build step |
| Eval | pytest, custom backtest with 4 baselines (naive-lean, always-fade, random, sentiment) |

## Project Structure

```
PRISM/
├── schema.py                  Pydantic contracts (do not modify alone)
├── ingestion/
│   ├── prices/                OHLCV, splits, dividends, earnings calendar
│   ├── sec/                   10-K MD&A + Risk Factors, 8-K item codes
│   ├── earnings_news/         news + earnings transcripts
│   ├── idiosyncratic/         13F, short interest, FDA, analyst actions
│   └── events/                8 source adapters + trading-day-window joiner
├── model/attribution/         Claude tool_use + coherence + validator
├── backtest/                  fade/lean signal + 4 baselines + PnL
├── eval/                      attribution accuracy + calibration + perturbation
├── demo/
│   ├── server.py              FastAPI app
│   ├── build_static.py        bakes per-ticker JSON for first paint
│   ├── prewarm_cache.py       fills /api/attribute cache for live demo
│   ├── real_chunks.py         chunks_for_real()
│   └── static/                HTML/CSS/JS + architecture.html
├── presentation/              intro slide deck (served at /presentation/)
├── design/
│   ├── brand/                 brand exploration mockups (index/mark/icons/explore)
│   └── handoff_v2/            v2 console design handoff (HTML/CSS/JS snapshot)
├── docs/                      hf_schemas.md, design docs
├── scripts/                   smoke + load utilities
└── tests/                     pytest suite (~380+ tests)
```

## What ships in the repo vs. what's downloaded at runtime

**Committed** (small, needed at boot):
- `demo/static/data/{ABT,ACU,AIR,AMD,APD}.json` + `index.json` — pre-baked per-ticker bundles the frontend fetches on first paint
- `data/thirteen_f/focal_chunks.jsonl` — 13F text (5 KB)
- `tests/fixtures/` — synthetic test data

**Downloaded on first run** (large, gitignored):
- `ingestion/earnings_news/.cache/` — news + transcripts parquets from the private HF repo

**Regenerated locally** for fresh attributions in the static bundles:

```bash
python demo/build_static.py        # re-bake per-ticker JSON (slow — runs the model per move)
python demo/build_13f_chunks.py    # re-fetch 13F chunks (slow — EDGAR rate-limited)
```

## Module ownership

| Owner | Module | Responsibility |
|---|---|---|
| Srilekha | `ingestion/prices/` | Backbone everyone joins onto. Events table + transcripts with BMO/AMC alignment. |
| Sophia | `ingestion/sec/` | 10-K Risk Factors + MD&A, 8-K item codes. Stretch: change-detection between filings. |
| Henry | `ingestion/idiosyncratic/`, `ingestion/events/` | 13F positioning + 7 other idiosyncratic streams. |
| Thomas | `ingestion/earnings_news/`, `model/attribution/`, `demo/` | News, Claude attribution, FastAPI demo + cache stack. |

## Frozen test case

Pick one (ticker, date) where the team knows what caused the move. Store expected outputs in `tests/fixtures/<ticker>_<event>_expected.json`. Don't change the test case mid-iteration — freeze inputs so a regression is obvious.

## Troubleshooting

- **"Error loading data" on the demo page** — `demo/static/data/index.json` is missing. Rebuild with `python demo/build_static.py` (slow) or pull from a branch that has them.
- **`huggingface_hub.errors.GatedRepoError`** — your HF account doesn't have access to `BridgewaterAIHackathon/BW-AI-Hackathon`. Ask the team for an invite, then re-run `huggingface-cli login`.
- **`synthetic fixture: ANTHROPIC_API_KEY missing`** in the model_notes badge — set `ANTHROPIC_API_KEY` in `.env` and `BW_USE_LIVE_ATTRIBUTION=1`. The synthetic path still produces a coherent response, just not a real one.
- **Port 2004 in use** — pick another (`--port 2005`) and open that URL. The frontend hard-codes nothing port-specific.

---

Built by Thomas Ou
