/* ════════════════════════════════════════════════════════════
   PRISM CONSOLE v2 — app
   Wires the redesigned shell to the SAME endpoints as v1:
     GET  /data/index.json
     GET  /data/{ticker}.json
     POST /api/attribute   (with ticker, move_date, return_pct,
                            vol_zscore, magnitude_rank, enabled_sources)
   No backend changes required.
   ═══════════════════════════════════════════════════════════ */

// ───────── Constants ─────────
const SOURCE_TYPES = [
  { id: 'news',                 label: 'News' },
  { id: 'sec_10k',              label: '10-K' },
  { id: 'sec_8k',               label: '8-K' },
  { id: 'earnings_transcript',  label: 'Earnings call' },
  { id: 'peer_news',            label: 'Peer news' },
  { id: 'macro',                label: 'Macro' },
  { id: 'thirteen_f',           label: '13F' },
];
const ALL_SOURCE_IDS = SOURCE_TYPES.map(s => s.id);

// Mirrors backend `_COUNT_TO_BUNDLE` so the chart-overlay name matches.
const COUNT_TO_BUNDLE = {
  0: 'base_news', 1: 'base_news', 2: '+sec', 3: '+earnings',
  4: '+peer_news', 5: '+sector_news', 6: '+macro', 7: '+positioning',
};
function pickAblationName(enabledSet) {
  const n = enabledSet.size ?? enabledSet.length ?? 0;
  return COUNT_TO_BUNDLE[Math.min(n, 7)] ?? '+positioning';
}

// Display order for dimension cards (the prism's spectrum, top → bottom):
// red (demand) → orange (pricing) → gold (competitive) → blue (mgmt) → navy (macro)
const DIM_ORDER = ['demand', 'pricing', 'competitive', 'management_credibility', 'macro'];
const DIM_META = {
  demand:                 { label: 'Demand',     sub: 'Volume · Customers · Share',     color: '#C8442C' },
  pricing:                { label: 'Pricing',    sub: 'Price · Mix · Margins',           color: '#E89B4A' },
  competitive:            { label: 'Competitive',sub: 'Rivals · Moats · Entrants',        color: '#C9A227' },
  management_credibility: { label: 'Management', sub: 'Guidance · Tone · Execution',     color: '#5A8DA8' },
  macro:                  { label: 'Macro',      sub: 'Rates · FX · Geopolitics',        color: '#3D4A6B' },
};
const ARROW = { positive: '↑', negative: '↓', neutral: '→' };

// Persistence priors (mirror backtest/frameworks.py)
const PERSISTENCE = {
  demand: 0.85,
  pricing: 0.65,
  competitive: 0.45,
  management_credibility: -0.15,
  macro: -0.75,
};

const DIM_PHRASE_SHORT = {
  demand:                 'demand growth (units, customers)',
  pricing:                'pricing power (margins, price hikes)',
  competitive:            'competitive dynamics (rivals, market share)',
  management_credibility: 'management credibility (guidance, execution)',
  macro:                  'macro forces (rates, FX, geopolitics)',
};

const STRATEGIES = [
  { id: 'fundamental_vs_nonfundamental',
    label: 'Fundamental vs Non',
    blurb: 'fundamental cause → lean, sentiment-driven → fade.',
    description:
      'Asks one question: did the move come from something <em>real and ' +
      'durable</em> — a genuine earnings beat, customer growth, pricing power, ' +
      'a competitive shift — or from something <em>soft</em> like a fear cycle, ' +
      'hype, or a single news headline? If the cause looks fundamental, ' +
      '<span class="pos">lean</span> with the move. If it looks like a sentiment ' +
      'reaction, <span class="neg">fade</span> it. When the model can\'t tell, sit it out.' },
  { id: 'expected_vs_realized',
    label: 'Expected vs Realized',
    blurb: 'market overreacted → fade. Underreacted → lean.',
    description:
      'The model reads the news and filings around the move and estimates how ' +
      'much the stock <em>should</em> have moved given that evidence. Compared ' +
      'to actual: much more → overreaction (<span class="neg">fade</span>); much ' +
      'less → not priced in (<span class="pos">lean</span>); roughly equal → skip.' },
  { id: 'hybrid',
    label: 'Hybrid (3-stage)',
    blurb: 'fundamental check, then overshoot check, then driver sanity check.',
    description:
      'Stacks the others. Step 1: is the move fundamental? Narrative → ' +
      '<span class="neg">fade</span>. Step 2: even if fundamental, did the price ' +
      'overshoot the news? Yes → <span class="neg">fade</span> the overshoot. ' +
      'Step 3: sanity-check the dominant driver — if it\'s historically ' +
      'mean-reverting (e.g. macro), back off to skip; otherwise ' +
      '<span class="pos">lean</span>.' },
];

// ───────── State ─────────
const STATE = {
  tickers: [],
  currentTicker: null,
  bundle: null,
  selectedMoveIdx: null,
  enabledSources: new Set(ALL_SOURCE_IDS),
  lastFullStack: null,
  fetchSeq: 0,
  selectedStrategy: 'fundamental_vs_nonfundamental',
  lastStrategies: {},
  lastDims: null,           // currently rendered dim shape
  lastChunkMap: new Map(),  // chunk_id → chunk
  directionFilter: 'all',   // 'all' | 'up' | 'down' (chart filter)
  evalReport: null,         // cached /data/eval_report.json payload
};

// ───────── Helpers ─────────
const pct = (x, digits = 2) =>
  x === null || x === undefined ? '—' : `${x >= 0 ? '+' : ''}${(x * 100).toFixed(digits)}%`;
const signed = (x, digits = 2) =>
  x === null || x === undefined ? '—' : `${x >= 0 ? '+' : ''}${x.toFixed(digits)}`;

async function fetchJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`Failed ${path}: ${res.status}`);
  return res.json();
}

function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ═════════════════════════════════════════════
// TICKER STRIP
// ═════════════════════════════════════════════
function renderTickerStrip() {
  const nav = document.getElementById('ticker-strip');
  nav.innerHTML = '';
  for (const t of STATE.tickers) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ticker-pill' + (t.ticker === STATE.currentTicker ? ' active' : '');
    btn.setAttribute('role', 'tab');
    btn.innerHTML =
      `<span class="ticker-sym">${t.ticker}</span>` +
      `<span class="ticker-sector">${t.sector || ''}</span>`;
    btn.addEventListener('click', () => selectTicker(t.ticker));
    nav.appendChild(btn);
  }
}

// ═════════════════════════════════════════════
// HEADER STRIP (ticker + stats)
// ═════════════════════════════════════════════
function renderStrip(bundle) {
  document.getElementById('ticker-name').textContent = bundle.name;
  document.getElementById('ticker-sub').textContent =
    `${bundle.ticker} · ${bundle.sector} · ${bundle.start_date} → ${bundle.end_date}`;
  const n = bundle.prices.length;
  const m = bundle.moves.length;
  const down = bundle.moves.filter(x => x.return_pct < 0).length;
  const up = m - down;
  document.getElementById('stat-days').textContent = n.toLocaleString();
  document.getElementById('stat-moves').textContent = m.toLocaleString();
  document.getElementById('stat-split').textContent = `${down} ↓  /  ${up} ↑`;

  // ISO meta (top right)
  document.getElementById('status-iso').textContent =
    `${bundle.ticker} · ${new Date().toISOString().slice(0, 10)}`;
}

// ═════════════════════════════════════════════
// CHART — Plotly. Same overlay logic as v1.
// ═════════════════════════════════════════════
function computePredictedSeries(bundle, ablationName) {
  const eventByDate = new Map();
  const eventsSorted = bundle.moves
    .map((m, idx) => ({ idx, ...m }))
    .sort((a, b) => a.move_date.localeCompare(b.move_date));
  let cumGap = 1.0;
  for (const e of eventsSorted) {
    const pred = e.predictions_by_ablation?.[ablationName];
    const denom = 1 + e.return_pct;
    if (pred !== null && pred !== undefined && denom !== 0) {
      cumGap *= (1 + pred) / denom;
    }
    eventByDate.set(e.move_date, { cumGap, idx: e.idx, predicted: pred });
  }
  let runningGap = 1.0;
  const dates = [], predicted = [];
  for (const p of bundle.prices) {
    const ev = eventByDate.get(p.date);
    if (ev) runningGap = ev.cumGap;
    dates.push(p.date);
    predicted.push(p.close * runningGap);
  }
  return { dates, predicted, eventByDate };
}

function renderChart(bundle) {
  const priceByDate = new Map(bundle.prices.map(p => [p.date, p.close]));
  const hoverText = (m) =>
    `<b>${m.move_date}</b><br>close $${priceByDate.get(m.move_date)?.toFixed(2) ?? '—'}` +
    `<br>return ${pct(m.return_pct)}<br>vol z ${signed(m.vol_zscore)}`;

  const rallyIdx = [], selloffIdx = [];
  bundle.moves.forEach((m, i) => {
    if (STATE.directionFilter === 'up'   && m.return_pct < 0) return;
    if (STATE.directionFilter === 'down' && m.return_pct >= 0) return;
    (m.return_pct < 0 ? selloffIdx : rallyIdx).push(i);
  });

  // Mark currently selected
  const isSelected = (i) => i === STATE.selectedMoveIdx;

  const buildActualMarkerTrace = (idx, color, name) => ({
    x: idx.map(i => bundle.moves[i].move_date),
    y: idx.map(i => priceByDate.get(bundle.moves[i].move_date) ?? null),
    customdata: idx,
    text: idx.map(i => hoverText(bundle.moves[i])),
    hovertemplate: '%{text}<extra></extra>',
    mode: 'markers',
    marker: {
      size: idx.map(i => isSelected(i) ? 16 : 11),
      color,
      line: {
        color: idx.map(i => isSelected(i) ? '#0a1d36' : '#fbfaf6'),
        width: idx.map(i => isSelected(i) ? 2.5 : 1.6),
      },
      symbol: 'circle',
    },
    name,
  });

  const ablationName = pickAblationName(STATE.enabledSources);
  const hasOverlay = (STATE.enabledSources.size ?? 0) > 0;
  const overlay = hasOverlay ? computePredictedSeries(bundle, ablationName) : null;

  const buildPredictedMarkerTrace = (idx, color, name) => {
    const xs = [], ys = [], cd = [], txt = [];
    for (const i of idx) {
      const m = bundle.moves[i];
      const ev = overlay?.eventByDate.get(m.move_date);
      if (!ev || ev.predicted === null || ev.predicted === undefined) continue;
      const actualY = priceByDate.get(m.move_date);
      if (actualY === undefined) continue;
      const predY = actualY * ev.cumGap;
      xs.push(m.move_date); ys.push(predY); cd.push(i);
      const gap = predY - actualY;
      txt.push(
        `<b>${m.move_date}</b> · model<br>` +
        `predicted close $${predY.toFixed(2)}<br>actual close $${actualY.toFixed(2)}<br>` +
        `gap ${gap >= 0 ? '+' : ''}$${gap.toFixed(2)}<br>` +
        `predicted return ${pct(ev.predicted)}`
      );
    }
    return {
      x: xs, y: ys, customdata: cd, text: txt,
      hovertemplate: '%{text}<extra></extra>',
      mode: 'markers',
      marker: { size: 11, color, line: { color: '#fbfaf6', width: 1.5 }, symbol: 'diamond-open' },
      name,
    };
  };

  const gapShapes = [];
  if (overlay) {
    for (let i = 0; i < bundle.moves.length; i++) {
      const m = bundle.moves[i];
      const ev = overlay.eventByDate.get(m.move_date);
      if (!ev || ev.predicted === null || ev.predicted === undefined) continue;
      const actualY = priceByDate.get(m.move_date);
      if (actualY === undefined) continue;
      const predY = actualY * ev.cumGap;
      if (Math.abs(predY - actualY) / Math.abs(actualY) < 0.02) continue;
      gapShapes.push({
        type: 'line', x0: m.move_date, x1: m.move_date,
        y0: actualY, y1: predY,
        line: { color: '#6a6e7c', width: 1, dash: 'dot' },
        layer: 'below',
      });
    }
  }

  const traces = [{
    x: bundle.prices.map(p => p.date),
    y: bundle.prices.map(p => p.close),
    mode: 'lines',
    line: { color: '#0a1d36', width: 1.4 },
    name: 'Close (actual)',
    hovertemplate: '%{x}<br>$%{y:.2f}<extra></extra>',
  }];
  if (overlay) {
    traces.push({
      x: overlay.dates, y: overlay.predicted,
      mode: 'lines', line: { color: '#7a1f1f', width: 1.4, dash: 'dash' },
      name: `Predicted (${ablationName})`,
      hovertemplate: '%{x}<br>predicted $%{y:.2f}<extra></extra>',
      opacity: 0.85,
    });
  }
  traces.push(buildActualMarkerTrace(rallyIdx,   '#2e6f48', 'Rally'));
  traces.push(buildActualMarkerTrace(selloffIdx, '#8c2f2f', 'Selloff'));
  if (overlay) {
    traces.push(buildPredictedMarkerTrace(rallyIdx,   '#2e6f48', 'Predicted (rally)'));
    traces.push(buildPredictedMarkerTrace(selloffIdx, '#8c2f2f', 'Predicted (selloff)'));
  }

  const layout = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor:  'rgba(0,0,0,0)',
    margin: { l: 56, r: 16, t: 14, b: 36 },
    font: { family: 'Inter, sans-serif', color: '#6a6e7c', size: 12 },
    hoverlabel: { bgcolor: '#fbfaf6', bordercolor: '#dcd9cf',
                  font: { family: 'Inter, sans-serif', color: '#0d1320', size: 12 } },
    xaxis: { gridcolor: '#dcd9cf', linecolor: '#dcd9cf', tickcolor: '#dcd9cf', zeroline: false },
    yaxis: { title: { text: 'Close ($)', standoff: 8 },
             gridcolor: '#dcd9cf', linecolor: '#dcd9cf', tickcolor: '#dcd9cf',
             zeroline: false, tickprefix: '$' },
    shapes: gapShapes,
    showlegend: true,
    legend: { orientation: 'h', x: 0, y: 1.02, xanchor: 'left', yanchor: 'bottom',
              font: { color: '#6a6e7c' } },
  };
  Plotly.react('chart', traces, layout,
    { displaylogo: false, responsive: true, modeBarButtonsToRemove: ['lasso2d','select2d'] });

  const pill = document.getElementById('ablation-pill');
  if (pill) {
    if (!hasOverlay) {
      pill.textContent = 'No overlay — enable a source';
      pill.classList.add('muted');
    } else {
      pill.textContent = `Model overlay · ${ablationName}`;
      pill.classList.remove('muted');
    }
  }

  const chartDiv = document.getElementById('chart');
  if (!chartDiv._clickHandlerAttached) {
    chartDiv.on('plotly_click', (ev) => {
      const pt = ev.points[0];
      if (!pt || pt.customdata === undefined || pt.customdata === null) return;
      selectMove(pt.customdata);
    });
    chartDiv._clickHandlerAttached = true;
  }
}

// ═════════════════════════════════════════════
// PRISM — beam SVG (5 colored beams from triangle to dim cards)
// ═════════════════════════════════════════════
function renderBeams(weights) {
  // weights: { dim_key: weight 0..1 } — used to size beam thickness.
  const svg = document.getElementById('beam-svg');
  if (!svg) return;
  // SVG is 140 wide (matches middle column). Triangle is shifted right so
  // there's room for a longer incoming bar to its left without spilling
  // out of the SVG and overlapping the incoming-event card.
  const W = 140, H = 520;
  const baseX = 28, apexX = 76;         // triangle: base at x=28, apex at x=76
  const apexY = H / 2;
  const triTop = apexY - 60, triBot = apexY + 60;

  // Card centers — match the 5 grid rows on the right.
  // topPad/botPad must mirror .prism-right padding in styles_v2.css so the
  // beams visually land on each card's vertical center.
  const topPad = 50, botPad = 30, gap = 6;
  const cardHt = (H - topPad - botPad - 4 * gap) / 5;
  const targets = DIM_ORDER.map((key, i) => ({
    key,
    color: DIM_META[key].color,
    y: topPad + cardHt / 2 + i * (cardHt + gap),
    weight: weights ? (weights[key] ?? 0) : 0.2,
  }));

  let html = '';
  // SVG soft-glow filter for the colored beams
  html += `<defs>
    <filter id="beam-glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="1.4" result="blur"/>
      <feMerge>
        <feMergeNode in="blur"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>
  </defs>`;
  // Incoming dark bar — extends from x=0 to triangle base, fully inside SVG.
  html += `<rect x="0" y="${apexY - 5}" width="${baseX}" height="10" fill="#0a1d36"/>`;
  // Prism triangle
  html += `<polygon points="${baseX},${triTop} ${baseX},${triBot} ${apexX},${apexY}"
              fill="rgba(10,29,54,0.05)" stroke="#0a1d36" stroke-width="1.4"/>`;
  // Beams — apex → right edge. Each beam exits at a slightly different y
  // along the apex (spread over 18px) so they fan out instead of all
  // originating from one pixel — otherwise the horizontal middle beam
  // (Competitive) gets buried under adjacent thick beams.
  const N = targets.length;
  const apexSpread = 22;
  // Draw heaviest first so lighter beams sit on top
  const order = targets.map((t, i) => ({ t, i }))
    .sort((a, b) => (b.t.weight ?? 0) - (a.t.weight ?? 0));
  // Pass 1: dark silhouette under every beam — guarantees visibility
  // regardless of how pale the dim color is (Competitive gold ≈ paper).
  for (const { t, i } of order) {
    const w01 = Math.max(0, Math.min(1, t.weight));
    const thickness = 2.5 + w01 * 6;
    const oy = apexY + (i - (N - 1) / 2) * (apexSpread / (N - 1));
    html += `<line x1="${apexX}" y1="${oy.toFixed(2)}"
                  x2="${W}" y2="${t.y}"
                  stroke="#0a1d36" stroke-width="${(thickness + 2).toFixed(2)}"
                  stroke-linecap="round" opacity="0.10"/>`;
  }
  // Pass 2: the colored beam on top of its silhouette
  for (const { t, i } of order) {
    const w01 = Math.max(0, Math.min(1, t.weight));
    const thickness = 2.5 + w01 * 6;
    const opacity   = weights ? 0.85 + w01 * 0.15 : 0.7;
    const oy = apexY + (i - (N - 1) / 2) * (apexSpread / (N - 1));
    html += `<line x1="${apexX}" y1="${oy.toFixed(2)}"
                  x2="${W}" y2="${t.y}"
                  stroke="${t.color}" stroke-width="${thickness.toFixed(2)}"
                  stroke-linecap="round" opacity="${opacity.toFixed(2)}"
                  filter="url(#beam-glow)"/>`;
  }
  svg.innerHTML = html;
}

// ═════════════════════════════════════════════
// PRISM — dim cards on the right
// ═════════════════════════════════════════════
function renderDimCards(dims, chunkMap) {
  const host = document.getElementById('dim-cards');
  host.innerHTML = '';
  for (const key of DIM_ORDER) {
    const meta = DIM_META[key];
    const dim = dims ? dims[key] : null;
    const card = document.createElement('div');
    card.className = `beam-card ${key}` + (dim ? '' : ' idle');

    let quoteHtml = `<p class="quote">—</p><div class="cite">awaiting attribution</div>`;
    if (dim) {
      // Pull a citation. Prefer rich cited_evidence quote; fall back to chunk text.
      const rich = Array.isArray(dim.cited_evidence) ? dim.cited_evidence : [];
      let quote = '';
      let cid = '';
      if (rich.length > 0 && rich[0].quote) {
        quote = rich[0].quote;
        cid   = rich[0].chunk_id || '';
      } else if ((dim.evidence_chunk_ids || []).length > 0) {
        cid = dim.evidence_chunk_ids[0];
        const c = chunkMap.get(cid);
        if (c && c.text) quote = c.text.slice(0, 160) + (c.text.length > 160 ? '…' : '');
      }
      if (!quote && dim.rationale) quote = dim.rationale;
      quoteHtml =
        `<p class="quote">${escapeHtml(quote || '—')}</p>` +
        (cid ? `<div class="cite"><code>${escapeHtml(cid)}</code></div>`
             : `<div class="cite">no citations</div>`);
    }

    const dirClass = dim ? (dim.direction === 'positive' ? 'pos'
                          : dim.direction === 'negative' ? 'neg' : '') : '';
    const dirLabel = dim ? (dim.direction === 'positive' ? '↑ pos'
                          : dim.direction === 'negative' ? '↓ neg' : '→ neu') : '—';
    const wpct = dim ? `${Math.round((dim.weight ?? 0) * 100)}%` : '—';

    card.innerHTML =
      `<div class="name-stack">` +
        `<div class="name">${meta.label}</div>` +
        `<div class="sub">${meta.sub}</div>` +
      `</div>` +
      `<div class="body">${quoteHtml}</div>` +
      `<div class="stat">` +
        `<div class="w">${wpct}</div>` +
        `<div class="dir ${dirClass}">${dirLabel}</div>` +
      `</div>`;
    host.appendChild(card);
  }
}

// ═════════════════════════════════════════════
// PRISM — incoming event card (left)
// ═════════════════════════════════════════════
function renderEventCard(move, attr) {
  const ev = document.getElementById('prism-event');
  if (!move) {
    ev.classList.add('idle');
    document.getElementById('ev-date').textContent = '—';
    document.getElementById('ev-ret').textContent = '—';
    document.getElementById('ev-ret').dataset.state = 'idle';
    for (const id of ['ev-z','ev-volz','ev-realized','ev-predicted','ev-gap','ev-character']) {
      document.getElementById(id).textContent = '—';
    }
    return;
  }
  ev.classList.remove('idle');
  document.getElementById('ev-date').textContent = move.move_date;
  const retEl = document.getElementById('ev-ret');
  retEl.textContent = pct(move.return_pct);
  retEl.dataset.state = move.return_pct >= 0 ? 'up' : 'down';

  document.getElementById('ev-z').textContent = signed(move.vol_zscore);
  document.getElementById('ev-volz').textContent =
    move.volume_zscore !== null && move.volume_zscore !== undefined
      ? signed(move.volume_zscore) : '—';

  if (attr) {
    const realizedEl = document.getElementById('ev-realized');
    realizedEl.textContent = pct(attr.realized);
    realizedEl.className = 'v ' + (attr.realized < 0 ? 'down' : 'up');

    const predictedEl = document.getElementById('ev-predicted');
    if (attr.predicted !== null && attr.predicted !== undefined) {
      predictedEl.textContent = pct(attr.predicted);
      predictedEl.className = 'v ' + (attr.predicted < 0 ? 'down' : 'up');
    } else {
      predictedEl.textContent = '—';
      predictedEl.className = 'v muted';
    }

    const gapEl = document.getElementById('ev-gap');
    if (attr.predicted !== null && attr.predicted !== undefined) {
      const gap = attr.predicted - attr.realized;
      const abs = Math.abs(gap);
      if (abs < 0.0005) {
        gapEl.textContent = '≈ 0';
        gapEl.className = 'v muted';
      } else if (gap > 0) {
        gapEl.textContent = `model under by ${(abs * 100).toFixed(1)}pp`;
        gapEl.className = 'v muted';
      } else {
        gapEl.textContent = `over by ${(abs * 100).toFixed(1)}pp`;
        gapEl.className = 'v muted';
      }
    } else {
      gapEl.textContent = '—';
      gapEl.className = 'v muted';
    }

    const charEl = document.getElementById('ev-character');
    charEl.textContent = attr.character || '—';
    charEl.className = 'v ' + (attr.character === 'transient' ? 'down'
                             : attr.character === 'structural' ? 'up' : 'muted');
  } else {
    document.getElementById('ev-realized').textContent  = '—';
    document.getElementById('ev-predicted').textContent = '—';
    document.getElementById('ev-gap').textContent       = '—';
    document.getElementById('ev-character').textContent = '—';
  }
}

// ═════════════════════════════════════════════
// SOURCE TOGGLES
// ═════════════════════════════════════════════
function computeAvailableCounts(move) {
  const counts = Object.fromEntries(ALL_SOURCE_IDS.map(s => [s, 0]));
  if (move.chunks_available && typeof move.chunks_available === 'object') {
    Object.assign(counts, move.chunks_available);
    return counts;
  }
  for (const c of (move.chunks || [])) {
    counts[c.source_type] = (counts[c.source_type] ?? 0) + 1;
  }
  return counts;
}

function renderToggles(availableCounts) {
  const grid = document.getElementById('src-grid');
  grid.innerHTML = '';
  for (const src of SOURCE_TYPES) {
    const count = availableCounts[src.id] ?? 0;
    const checked = STATE.enabledSources.has(src.id) && count > 0;
    const disabled = count === 0;
    const wrap = document.createElement('label');
    wrap.className = 'src-toggle' + (checked ? ' checked' : '') + (disabled ? ' disabled' : '');
    if (disabled) wrap.title = `No ${src.label} chunks for this (ticker, date).`;
    const input = document.createElement('input');
    input.type = 'checkbox'; input.value = src.id;
    input.checked = checked; input.disabled = disabled;
    input.addEventListener('change', (e) => {
      if (e.target.checked) STATE.enabledSources.add(src.id);
      else STATE.enabledSources.delete(src.id);
      renderToggles(availableCounts);
      // Immediate visual feedback: filter the existing evidence list with
      // the new toggle state before the async live call returns.
      if (STATE.lastDims) renderEvidence(STATE.lastDims, STATE.lastChunkMap);
      recomputeAttribution();
      if (STATE.bundle) renderChart(STATE.bundle);
    });
    wrap.appendChild(input);
    const stack = document.createElement('div');
    stack.innerHTML = `<div class="name">${src.label}</div><div class="count">${count} chunk${count === 1 ? '' : 's'}</div>`;
    wrap.appendChild(stack);
    grid.appendChild(wrap);
  }
}

function renderToggleCaption(enabledCount, totalCount, chunksConsidered) {
  const el = document.getElementById('src-caption');
  if (enabledCount === 0) {
    el.innerHTML = `<span class="pill">0 / ${totalCount}</span> no sources enabled — attribution disabled.`;
    return;
  }
  el.innerHTML =
    `<span class="pill">${enabledCount} / ${totalCount}</span>` +
    `<span class="num">${chunksConsidered}</span> chunk${chunksConsidered === 1 ? '' : 's'} feeding attribution.`;
}

// ═════════════════════════════════════════════
// STRATEGY ROW + verdict
// ═════════════════════════════════════════════
function renderStrategyRow() {
  const list = document.getElementById('strat-list');
  list.innerHTML = '';
  for (const s of STRATEGIES) {
    const verdict = STATE.lastStrategies[s.id];
    const verdictClass = verdict || 'pending';
    const verdictText = verdict ? (verdict === 'neutral' ? 'SKIP' : verdict.toUpperCase()) : '—';
    const active = s.id === STATE.selectedStrategy;
    const wrap = document.createElement('label');
    wrap.className = 'strategy-pill' + (active ? ' active' : '');
    wrap.innerHTML =
      `<input type="radio" name="strategy" value="${s.id}" ${active ? 'checked' : ''}>` +
      `<span class="name">${s.label}</span>` +
      `<span class="verdict ${verdictClass}">${verdictText}</span>`;
    wrap.addEventListener('click', () => selectStrategy(s.id));
    list.appendChild(wrap);
  }
  renderVerdict();
}

function renderVerdict() {
  const verdict = STATE.lastStrategies[STATE.selectedStrategy];
  const meta = STRATEGIES.find(s => s.id === STATE.selectedStrategy);
  // Verdict word and explainer must agree. If either side is missing data
  // (verdict OR the lastFullStack ref the explainer needs), both fall back
  // to the placeholder so we never show "Lean." next to "Pick a move".
  const ready = !!(verdict && STATE.lastFullStack);

  const wordEl = document.getElementById('verdict-mini-word');
  const stratEl = document.getElementById('verdict-mini-strat');
  if (!ready) {
    wordEl.textContent = '—';
    wordEl.dataset.state = 'idle';
    stratEl.textContent = meta ? meta.label : 'pick a move';
  } else {
    const display = verdict === 'neutral' ? 'Skip.' : (verdict === 'lean' ? 'Lean.' : 'Fade.');
    wordEl.textContent = display;
    wordEl.dataset.state = verdict === 'neutral' ? 'skip' : verdict;
    stratEl.textContent = meta ? `${meta.label} says` : '';
  }

  const expHow = document.getElementById('exp-how');
  const expWhy = document.getElementById('exp-why');
  expHow.innerHTML = meta && meta.description ? meta.description : '—';
  expWhy.innerHTML = ready
    ? buildVerdictConclusion(STATE.selectedStrategy, verdict, STATE.lastFullStack)
    : 'Pick a flagged move on the chart above.';
}

// Updates the orientation hint above the prism + the chunks/sources/confidence
// canvas-meta line. Called from both the live and pre-baked render paths so
// every code path that updates the prism updates these too.
function renderOrientAndMeta(move, ref, chunksConsidered, sourceCount, source) {
  const orient = document.getElementById('canvas-orient');
  if (orient) {
    if (!move || !ref) {
      orient.textContent = 'Pick a flagged move on the chart to see what drove it.';
    } else {
      const ret = ref.realized != null
        ? `${ref.realized >= 0 ? '+' : ''}${(ref.realized * 100).toFixed(2)}%`
        : '—';
      const dirCls = (ref.realized ?? 0) >= 0 ? 'up' : 'down';
      const verb  = (ref.realized ?? 0) >= 0 ? 'jumped' : 'dropped';
      orient.innerHTML =
        `<b>${escapeHtml(STATE.bundle?.ticker ?? '')}</b> ${verb} ` +
        `<span class="${dirCls}">${ret}</span> on ${escapeHtml(move.move_date)}. ` +
        `Here's what the model thinks drove it.`;
    }
  }
  const cm = document.getElementById('canvas-meta');
  if (cm) {
    if (!move || !ref) {
      cm.innerHTML = '—';
    } else {
      cm.innerHTML =
        `<span class="num">${chunksConsidered ?? '—'}</span> chunks · ` +
        `<span class="num">${sourceCount ?? '—'}</span>/${ALL_SOURCE_IDS.length} sources · ` +
        `confidence <span class="num">${Math.round((ref.confidence ?? 0) * 100)}%</span>` +
        ` · <em style="color:var(--ink-dim); font-style:italic">thicker beam = more weight</em>` +
        (source === 'pre' ? ` · <em style="color:var(--ink-dim); font-style:italic">pre-baked</em>` : '');
    }
  }
}

function selectStrategy(id) {
  if (id === STATE.selectedStrategy) return;
  STATE.selectedStrategy = id;
  renderStrategyRow();
}

// ───── Verdict conclusion text (lifted + adapted from v1) ─────
function _dominantDimension(ref) {
  let domName = null, domWeight = 0;
  for (const [k, v] of Object.entries(ref.dimensions || {})) {
    if ((v.weight ?? 0) > domWeight) { domWeight = v.weight; domName = k; }
  }
  return domName;
}
function getForwardReturn(bundle, moveDate, n) {
  if (!bundle || !Array.isArray(bundle.prices)) return null;
  const idx = bundle.prices.findIndex(p => p.date === moveDate);
  if (idx < 0 || idx + n >= bundle.prices.length) return null;
  const px0 = bundle.prices[idx].close;
  const pxN = bundle.prices[idx + n].close;
  if (!px0) return null;
  return (pxN - px0) / px0;
}
function topDimsByWeight(ref, n) {
  return Object.entries(ref.dimensions || {})
    .map(([k, v]) => ({ key: k, weight: v.weight ?? 0, direction: v.direction || 'neutral' }))
    .filter(d => d.weight > 0)
    .sort((a, b) => b.weight - a.weight).slice(0, n);
}
function _dirCls(d) { return d === 'positive' ? 'pos' : d === 'negative' ? 'neg' : 'muted'; }
function _dirWord(d) { return d === 'positive' ? 'positive' : d === 'negative' ? 'negative' : 'neutral'; }

function buildModelReadSentence(ref) {
  const top = topDimsByWeight(ref, 3);
  if (top.length === 0) return `The model couldn't attribute this move to any dimension above zero weight.`;
  const parts = top.map(d => {
    const phrase = DIM_PHRASE_SHORT[d.key] || d.key;
    const cls = _dirCls(d.direction);
    const wpct = Math.round(d.weight * 100);
    return `<span class="${cls}">${phrase}</span> (${_dirWord(d.direction)}, ${wpct}%)`;
  });
  let lead;
  if (parts.length === 1) lead = `The model attributed this move primarily to ${parts[0]}.`;
  else if (parts.length === 2) lead = `The model attributed this move primarily to ${parts[0]} and ${parts[1]}.`;
  else lead = `The model attributed this move primarily to ${parts[0]} and ${parts[1]}, with ${parts[2]}.`;
  const realized = ref.realized, predicted = ref.predicted;
  const hasPred = predicted !== null && predicted !== undefined;
  let tail;
  if (hasPred) {
    const directionMatch = (predicted * realized > 0) || (predicted === 0 && realized === 0);
    const note = directionMatch ? `the model got the direction right`
                                : `<span class="neg">the model got the direction wrong</span>`;
    tail = ` It expected about <span class="num">${pct(predicted)}</span>; the stock actually moved <span class="num">${pct(realized)}</span> — ${note}.`;
  } else {
    tail = ` It saw the actual <span class="num">${pct(realized)}</span> move but the evidence was too thin to ground a precise expected magnitude.`;
  }
  return lead + tail;
}

function buildStrategyReasoning(strategyId, verdict, ref, tag) {
  if (strategyId === 'fundamental_vs_nonfundamental') {
    if (ref.character === 'structural')
      return `Because the model judged the cause durable rather than narrative, this strategy says ${tag} — bet the move keeps working.`;
    if (ref.character === 'transient')
      return `Because the model judged the cause sentiment-driven, this strategy says ${tag} — bet the move unwinds.`;
    return `Because the model couldn't cleanly call the cause fundamental or sentiment, this strategy stays out: ${tag}.`;
  }
  if (strategyId === 'expected_vs_realized') {
    const r = ref.realized, p = ref.predicted;
    const has = p !== null && p !== undefined;
    if (!has) return `With no expected-return baseline to compare against, this strategy is forced to ${tag}.`;
    if (p === 0) return `With expected return at zero, there's no magnitude to ratio against — ${tag}.`;
    if (p * r < 0) return `Since actual and expected pointed opposite directions, the strategy refuses to call it: ${tag}.`;
    const ratio = Math.abs(r) / Math.abs(p);
    if (ratio >= 1.5) return `The stock moved roughly <span class="num">${ratio.toFixed(2)}×</span> more than the news justified — overreaction, ${tag} (bet on a pullback).`;
    if (ratio <= 0.5) return `The stock moved only <span class="num">${ratio.toFixed(2)}×</span> the magnitude the news justified — price hasn't caught up, ${tag}.`;
    return `Realized and expected magnitudes are within the neutral band — news already priced in, ${tag}.`;
  }
  if (strategyId === 'hybrid') {
    const c = ref.character, r = ref.realized, p = ref.predicted;
    const dom = _dominantDimension(ref);
    const domPhrase = dom ? (DIM_PHRASE_SHORT[dom] || dom) : '—';
    const domPersist = dom ? (PERSISTENCE[dom] ?? 0) : 0;
    if (c === 'transient') return `Layer 1 caught it: the cause is sentiment-driven, ${tag}.`;
    if (c === 'mixed' || c === 'unclear') return `Layer 1 caught it: the cause is too unclear to act on, ${tag}.`;
    const hasPred = p !== null && p !== undefined && p !== 0 && p * r > 0;
    if (hasPred && Math.abs(r) >= 1.5 * Math.abs(p))
      return `Cause is fundamental, but Layer 2 caught an overshoot — price moved beyond the news. ${tag}.`;
    if (domPersist < 0)
      return `Cause is fundamental and there's no overshoot, but the strongest driver was <span class="num">${domPhrase}</span> — historically reverses, so Layer 3 backs off to ${tag}.`;
    return `All three checks passed — fundamental, no overshoot, persistent driver (<span class="num">${domPhrase}</span>). ${tag}.`;
  }
  return `Strategy concluded ${tag}.`;
}

function buildVerdictConclusion(strategyId, verdict, ref) {
  if (!ref) return 'Pick a flagged move on the chart above.';
  const conf = Math.round((ref.confidence ?? 0) * 100);
  const word = verdict === 'neutral' ? 'SKIP' : verdict === 'lean' ? 'LEAN' : verdict === 'fade' ? 'FADE' : '—';
  const cls = verdict === 'lean' ? 'pos' : verdict === 'fade' ? 'neg' : 'muted';
  const tag = `<span class="${cls}">${word}</span>`;
  const move = (STATE.bundle && STATE.selectedMoveIdx !== null) ? STATE.bundle.moves[STATE.selectedMoveIdx] : null;
  const md = move ? move.move_date : null;
  const read = buildModelReadSentence(ref);
  const reason = buildStrategyReasoning(strategyId, verdict, ref, tag);
  const outcome = (md && STATE.bundle) ? buildOutcomeSentence(verdict, ref, STATE.bundle, md) : '';
  const tail = ` <span class="muted">Model confidence: <span class="num">${conf}%</span>.</span>`;
  return `${read} ${reason} ${outcome}${tail}`;
}

function buildOutcomeSentence(verdict, ref, bundle, moveDate) {
  const fwd = getForwardReturn(bundle, moveDate, 5);
  if (fwd === null) return `Not enough price history after this move to score the call.`;
  const realized = ref.realized;
  const moveSign = realized > 0 ? 1 : realized < 0 ? -1 : 0;
  const fwdSign  = fwd > 0 ? 1 : fwd < 0 ? -1 : 0;
  const fwdHtml = `<span class="num">${pct(fwd)}</span>`;
  if (verdict === 'neutral') {
    if (fwdSign === 0) return `Over the next 5 trading days the stock was roughly flat (${fwdHtml}); <span class="muted">staying out was harmless</span>.`;
    const wouldHaveBeen = moveSign === fwdSign ? 'a LEAN' : 'a FADE';
    return `Over the next 5 trading days the stock moved ${fwdHtml}. <span class="muted">SKIP avoided a position; ${wouldHaveBeen} would have been the right call.</span>`;
  }
  if (verdict === 'lean') {
    if (moveSign === fwdSign && fwdSign !== 0)
      return `Over the next 5 trading days the stock continued (${fwdHtml}) — the <span class="pos">LEAN paid off</span>.`;
    return `Over the next 5 trading days the stock reversed to ${fwdHtml} — the <span class="neg">LEAN was wrong</span>.`;
  }
  if (verdict === 'fade') {
    if (moveSign !== fwdSign && fwdSign !== 0)
      return `Over the next 5 trading days the stock pulled back (${fwdHtml}) — the <span class="pos">FADE paid off</span>.`;
    return `Over the next 5 trading days the stock kept moving (${fwdHtml}) — the <span class="neg">FADE was wrong</span>.`;
  }
  return '';
}

// ═════════════════════════════════════════════
// ATTRIBUTION FETCH
// ═════════════════════════════════════════════
async function fetchAttribution(move, enabledSources) {
  const seq = ++STATE.fetchSeq;
  const res = await fetch('/api/attribute', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ticker: STATE.bundle.ticker,
      move_date: move.move_date,
      return_pct: move.return_pct,
      vol_zscore: move.vol_zscore ?? 0,
      magnitude_rank: move.magnitude_rank ?? null,
      enabled_sources: enabledSources,
    }),
  });
  if (!res.ok) throw new Error(`/api/attribute ${res.status}`);
  const data = await res.json();
  if (seq !== STATE.fetchSeq) return null;
  return data;
}

async function recomputeAttribution() {
  if (STATE.selectedMoveIdx === null) return;
  const move = STATE.bundle.moves[STATE.selectedMoveIdx];
  const enabled = Array.from(STATE.enabledSources);
  const counts = computeAvailableCounts(move);
  const totalAvail = Object.keys(counts).filter(k => counts[k] > 0).length;

  if (enabled.length === 0) {
    STATE.fetchSeq++;
    document.getElementById('zero-warning').hidden = false;
    renderEventCard(move, null);
    renderDimCards(null, new Map());
    renderEvidence(null, new Map());
    renderBeams(null);
    STATE.lastStrategies = {};
    STATE.lastFullStack = null;
    renderStrategyRow();
    renderToggleCaption(0, totalAvail, 0);
    renderOrientAndMeta(null, null);
    document.getElementById('canvas-meta').innerHTML =
      `<span class="num">0</span> chunks · attribution disabled`;
    return;
  }

  document.getElementById('zero-warning').hidden = true;

  try {
    const data = await fetchAttribution(move, enabled);
    if (!data) return;
    applyAttributionResponse(move, data);
    renderToggleCaption(enabled.length, totalAvail, data.chunks_considered);
  } catch (err) {
    console.error('attribution fetch failed', err);
  }
}

function applyAttributionResponse(move, response) {
  const a = response.attribution || {};
  // Server returns per-dim flat keys (demand, pricing, ...). Build the
  // {dimensions: {...}} shape the UI expects.
  const dims = {
    demand: a.demand,
    pricing: a.pricing,
    competitive: a.competitive,
    management_credibility: a.management_credibility,
    macro: a.macro,
  };
  const ref = {
    realized: a.return_pct,
    predicted: a.predicted_return_pct,
    character: a.move_character,
    confidence: a.confidence,
    chunks_considered: a.chunks_considered,
    sources_used: a.sources_used,
    dimensions: dims,
  };
  STATE.lastFullStack = ref;
  STATE.lastStrategies = response.strategies || {};
  STATE.lastDims = dims;
  STATE.lastChunkMap = new Map((response.chunks || []).map(c => [c.chunk_id, c]));

  renderEventCard(move, ref);
  renderDimCards(dims, STATE.lastChunkMap);
  renderEvidence(dims, STATE.lastChunkMap);
  // Compute weights map for beam thickness
  const weights = {};
  for (const k of DIM_ORDER) weights[k] = (dims[k]?.weight) ?? 0;
  renderBeams(weights);

  renderOrientAndMeta(move, ref, response.chunks_considered, response.enabled_sources.length, 'live');

  document.getElementById('foot-meta').textContent =
    `${response.chunks_considered} chunks · attribution gate ✓`;

  renderStrategyRow();
}

// ═════════════════════════════════════════════
// PRE-BAKED ATTRIBUTION (first paint, before live call)
// ═════════════════════════════════════════════
function applyPreBaked(bundle, moveIdx) {
  const move = bundle.moves[moveIdx];
  const attr = move.attribution;
  if (!attr) {
    renderEventCard(move, null);
    renderDimCards(null, new Map());
    renderEvidence(null, new Map());
    renderBeams(null);
    STATE.lastStrategies = {};
    STATE.lastFullStack = null;
    renderStrategyRow();
    renderOrientAndMeta(null, null);
    return;
  }
  const ref = {
    realized: attr.realized,
    predicted: attr.predicted,
    character: attr.character,
    confidence: attr.confidence,
    chunks_considered: attr.chunks_considered,
    dimensions: attr.dimensions,
  };
  STATE.lastFullStack = ref;
  STATE.lastStrategies = move.strategies || {};
  const chunkMap = new Map((move.chunks || []).map(c => [c.chunk_id, c]));
  STATE.lastChunkMap = chunkMap;
  STATE.lastDims = attr.dimensions;
  renderEventCard(move, ref);
  renderDimCards(attr.dimensions, chunkMap);
  renderEvidence(attr.dimensions, chunkMap);
  const weights = {};
  for (const k of DIM_ORDER) weights[k] = (attr.dimensions[k]?.weight) ?? 0;
  renderBeams(weights);
  renderStrategyRow();
  // Pre-baked attribution doesn't track per-source enabled count, so use
  // the bundle's full-stack source count for the meta line.
  const srcCount = (attr.sources_used || []).length || ALL_SOURCE_IDS.length;
  renderOrientAndMeta(move, ref, attr.chunks_considered, srcCount, 'pre');
}

// ═════════════════════════════════════════════
// TICKER + MOVE selection
// ═════════════════════════════════════════════
async function selectTicker(t) {
  if (t === STATE.currentTicker) return;
  STATE.currentTicker = t;
  STATE.selectedMoveIdx = null;
  renderTickerStrip();
  const bundle = await fetchJSON(`/data/${t}.json`);
  STATE.bundle = bundle;
  renderStrip(bundle);
  renderChart(bundle);
  if (bundle.moves.length > 0) {
    let maxIdx = 0, maxAbs = 0;
    bundle.moves.forEach((m, i) => {
      if (Math.abs(m.return_pct) > maxAbs) { maxAbs = Math.abs(m.return_pct); maxIdx = i; }
    });
    selectMove(maxIdx);
  } else {
    renderEventCard(null, null);
    renderDimCards(null, new Map());
    renderEvidence(null, new Map());
    renderBeams(null);
  }
}

function selectMove(idx) {
  STATE.selectedMoveIdx = idx;
  const move = STATE.bundle.moves[idx];
  const counts = computeAvailableCounts(move);
  STATE.enabledSources = new Set(ALL_SOURCE_IDS.filter(id => (counts[id] ?? 0) > 0));
  renderToggles(counts);
  applyPreBaked(STATE.bundle, idx);
  const total = Object.keys(counts).filter(k => counts[k] > 0).length;
  renderToggleCaption(STATE.enabledSources.size, total, move.attribution?.chunks_considered ?? 0);
  if (STATE.enabledSources.size > 0) recomputeAttribution();
  renderChart(STATE.bundle); // refresh selection highlight
}

function resetToggles() {
  if (STATE.selectedMoveIdx === null) return;
  const move = STATE.bundle.moves[STATE.selectedMoveIdx];
  const counts = computeAvailableCounts(move);
  STATE.enabledSources = new Set(ALL_SOURCE_IDS.filter(id => (counts[id] ?? 0) > 0));
  renderToggles(counts);
  applyPreBaked(STATE.bundle, STATE.selectedMoveIdx);
  if (STATE.bundle) renderChart(STATE.bundle);
  // Pre-baked attribution carries fewer citations than the live endpoint,
  // so refresh from /api/attribute to restore the richer evidence the user
  // had before clicking reset.
  if (STATE.enabledSources.size > 0) recomputeAttribution();
}

// ═════════════════════════════════════════════
// PORTED FROM v1 — Evidence list, PnL "closing the loop",
// Eval strip, direction filter, keyboard shortcuts.
// ═════════════════════════════════════════════

const _DIM_LABEL = {
  demand: 'Demand',
  pricing: 'Pricing',
  competitive: 'Competitive',
  management_credibility: 'Management',
  macro: 'Macro',
};

function renderEvidence(dims, chunkMap) {
  const groups = document.getElementById('evidence-groups');
  const ctx = document.getElementById('evd-context');
  const tabCount = document.getElementById('tab-count-evidence');
  if (!groups || !ctx || !tabCount) return;

  // Context line — what ticker + what move are we showing evidence for
  const move = (STATE.bundle && STATE.selectedMoveIdx !== null)
    ? STATE.bundle.moves[STATE.selectedMoveIdx] : null;
  if (!dims || !move) {
    ctx.textContent = '— · pick a flagged move to see the cited evidence.';
    groups.innerHTML = '';
    tabCount.textContent = '—';
    return;
  }
  const ret = move.return_pct != null
    ? `${move.return_pct >= 0 ? '+' : ''}${(move.return_pct * 100).toFixed(2)}%`
    : '—';
  ctx.innerHTML =
    `<b>${escapeHtml(STATE.bundle.ticker)}</b> · ` +
    `<span style="color:var(--navy)">${escapeHtml(move.move_date)}</span> · ` +
    `move ${ret} · ${chunkMap.size} chunks bundled`;

  // Sort dims by weight desc so the heaviest contributor is at the top
  const ordered = DIM_ORDER
    .map(key => ({ key, d: dims[key] }))
    .filter(x => x.d)
    .sort((a, b) => (b.d.weight ?? 0) - (a.d.weight ?? 0));

  // Client-side filter: only show citations whose chunk's source_type is
  // currently enabled. Without this, deselecting a source toggle wouldn't
  // visibly shrink the evidence list (the model fills ~5 cites per dim
  // regardless, so the raw count stays at ~25).
  const enabled = STATE.enabledSources;
  const isEnabled = (cid) => {
    const ch = chunkMap.get(cid);
    return ch ? enabled.has(ch.source_type) : false;
  };

  let totalCites = 0, totalRaw = 0;
  groups.innerHTML = ordered.map(({ key, d }) => {
    const cited = Array.isArray(d.cited_evidence) ? d.cited_evidence : [];
    const idsOnly = (d.evidence_chunk_ids || []).map(cid => ({ chunk_id: cid }));
    const rawCitations = cited.length ? cited : idsOnly;
    const citations = rawCitations.filter(c => isEnabled(c.chunk_id));
    totalRaw += rawCitations.length;
    totalCites += citations.length;

    const dirCls = d.direction === 'positive' ? 'pos'
                : d.direction === 'negative' ? 'neg' : '';
    const dirWord = (d.direction || 'neutral').toUpperCase();
    const wt = d.weight != null ? `${(d.weight * 100).toFixed(0)}%` : '—';
    const cn = citations.length;
    const dropped = rawCitations.length - cn;

    let body;
    if (citations.length) {
      body = citations.map(c => _renderCitation(c, chunkMap)).join('');
      if (dropped > 0) {
        body += `<div class="evd-missing" style="color:var(--ink-q); font-style:italic;">` +
                `${dropped} more citation${dropped === 1 ? '' : 's'} hidden — ` +
                `their source is currently toggled off.</div>`;
      }
    } else if (rawCitations.length) {
      body = `<div class="evd-missing">All ${rawCitations.length} citation${rawCitations.length === 1 ? '' : 's'} ` +
             `come from sources you've toggled off.</div>`;
    } else {
      body = `<div class="evd-missing">No citations on this dimension.</div>`;
    }

    return `
      <details class="evd-group ${key}">
        <summary class="evd-summary">
          <span class="chev">▸</span>
          <span class="name">${_DIM_LABEL[key] || key}</span>
          <span class="ct">${cn} cite${cn === 1 ? '' : 's'}</span>
          <span class="dir ${dirCls}">${dirWord}</span>
          <span class="wt">${wt}</span>
        </summary>
        <div class="evd-body">${body}</div>
      </details>`;
  }).join('');

  tabCount.textContent = totalCites < totalRaw
    ? `${totalCites} / ${totalRaw}`
    : String(totalCites);
}

function setupSourceTabs() {
  const btns = document.querySelectorAll('.src-tab');
  if (!btns.length) return;
  const reset = document.getElementById('reset-toggles');
  btns.forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.tab;
      btns.forEach(b => {
        const on = b.dataset.tab === target;
        b.classList.toggle('active', on);
        b.setAttribute('aria-selected', String(on));
        const panel = document.getElementById(`src-tab-${b.dataset.tab}`);
        if (panel) panel.hidden = !on;
      });
      // "Reset to full stack" only makes sense on the Sources tab
      if (reset) reset.hidden = (target !== 'sources');
    });
  });
}

function _renderCitation(c, chunkMap) {
  const cid = c.chunk_id;
  const chunk = chunkMap.get(cid);
  if (!chunk) {
    return `<div class="evd-missing">Missing chunk <code>${escapeHtml(cid || '')}</code> — coherence check would reject this attribution.</div>`;
  }
  // Preview = first ~120 chars of the quote (or raw text). Stays inline in
  // the collapsed summary so the user can scan citations without expanding.
  const previewSrc = c.quote || chunk.text || '';
  const preview = previewSrc.length > 140
    ? previewSrc.slice(0, 140).trimEnd() + '…'
    : previewSrc;

  const meta = [
    `<code>${escapeHtml(chunk.chunk_id)}</code>`,
    `<span class="sep">·</span>`,
    `<span class="src">${escapeHtml(chunk.source_type)}</span>`,
    `<span class="sep">·</span>`,
    escapeHtml(chunk.publication_date || ''),
  ];
  if (chunk.section_name) {
    meta.push(`<span class="sep">·</span><em>${escapeHtml(chunk.section_name)}</em>`);
  }
  let body = `<div class="meta">${meta.join(' ')}</div>`;
  if (c.quote) {
    body += `<blockquote class="quote">${escapeHtml(c.quote)}</blockquote>`;
    if (c.reasoning) body += `<p class="reason">${escapeHtml(c.reasoning)}</p>`;
  } else {
    body += `<p class="text">${escapeHtml(chunk.text || '')}</p>`;
  }
  if (chunk.source_url) {
    body += `<a class="lnk" href="${escapeHtml(chunk.source_url)}" target="_blank" rel="noopener">source ↗</a>`;
  }
  return `
    <details class="evd-citation">
      <summary class="evd-cite-summary">
        <span class="chev">▸</span>
        <span class="src-tag">${escapeHtml(chunk.source_type)}</span>
        <span class="prev">${escapeHtml(preview || '(no preview)')}</span>
      </summary>
      <div class="evd-cite-body">${body}</div>
    </details>`;
}

function _fmtMoney(x) {
  if (x === null || x === undefined) return '—';
  const sign = x < 0 ? '-' : '';
  const abs = Math.abs(x);
  return `${sign}$${abs.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

// renderPnl removed — Closing the Loop now lives on /about and renders
// from a small inline fetch there.

async function renderEvalStrip() {
  const strip = document.getElementById('eval-strip');
  if (!strip) return;
  if (!STATE.evalReport) {
    try {
      STATE.evalReport = await fetchJSON('/data/eval_report.json');
    } catch (err) {
      strip.hidden = true;
      return;
    }
  }
  const e = STATE.evalReport;
  if (!e || !e.primary_n_scored) { strip.hidden = true; return; }

  strip.hidden = false;
  const acc = e.primary_accuracy != null ? `${(e.primary_accuracy * 100).toFixed(1)}%` : '—';
  document.getElementById('eval-headline').textContent =
    `${e.primary_n_correct}/${e.primary_n_scored} cases correct (${acc})`;
  const universe = Array.isArray(e.universe) && e.universe.length
    ? e.universe.join(', ') : '—';
  document.getElementById('eval-sub').textContent =
    `primary: ${e.primary_strategy} · universe: ${universe}`;

  const primary = (e.strategies || []).find(s => s.strategy === e.primary_strategy);
  const cases = primary?.cases || [];
  const cont = document.getElementById('eval-cases');
  cont.innerHTML = cases.map(c => {
    const got = c.model_verdict || c.verdict || '—';
    const want = c.expected_verdict || '—';
    const correct = c.correct === true ? 'correct'
                   : c.correct === false ? 'wrong' : 'unscored';
    return `
      <div class="ec ${correct}">
        <div class="ec-h">
          <span class="ec-id">${escapeHtml(c.case_id || `${c.ticker}_${c.move_date}`)}</span>
          <span class="verdicts">expected ${escapeHtml(want)} · <span class="got">got ${escapeHtml(got)}</span></span>
        </div>
        <p class="cause">${escapeHtml(c.known_cause || '')}</p>
      </div>`;
  }).join('');
}

function setupDirectionFilter() {
  const bar = document.getElementById('dir-filter');
  if (!bar) return;
  bar.addEventListener('click', (e) => {
    const btn = e.target.closest('.dpill');
    if (!btn) return;
    const dir = btn.dataset.dir;
    if (dir === STATE.directionFilter) return;
    STATE.directionFilter = dir;
    bar.querySelectorAll('.dpill').forEach(b =>
      b.classList.toggle('active', b.dataset.dir === dir)
    );
    if (STATE.bundle) renderChart(STATE.bundle);
  });
}

function setupEvalToggle() {
  const evalHead = document.getElementById('eval-head');
  const evalCases = document.getElementById('eval-cases');
  if (!evalHead || !evalCases) return;
  evalHead.addEventListener('click', () => {
    const open = evalHead.getAttribute('aria-expanded') === 'true';
    evalHead.setAttribute('aria-expanded', String(!open));
    evalCases.hidden = open;
  });
}

// ═════════════════════════════════════════════
// BOOT
// ═════════════════════════════════════════════
(async function init() {
  try {
    const index = await fetchJSON('/data/index.json');
    STATE.tickers = index.tickers;
    renderTickerStrip();
    document.getElementById('reset-toggles').addEventListener('click', resetToggles);
    setupSourceTabs();
    setupDirectionFilter();
    setupEvalToggle();
    renderEvalStrip();  // fires once; cached on STATE
    const initial = STATE.tickers.find(t => t.ticker === 'AMD')?.ticker
                  || STATE.tickers[0]?.ticker;
    if (initial) await selectTicker(initial);
  } catch (err) {
    document.getElementById('ticker-name').textContent = 'Error loading data';
    document.getElementById('ticker-sub').textContent = String(err);
    console.error(err);
  }
})();
