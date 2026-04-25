// ---------- Design tokens exposed to JS ----------
const CSS = getComputedStyle(document.documentElement);
const COLOR = {
  text:     CSS.getPropertyValue('--text').trim() || '#eef0f3',
  muted:    CSS.getPropertyValue('--text-muted').trim() || '#858a95',
  accent:   CSS.getPropertyValue('--accent').trim() || '#5eb5ff',
  surface:  CSS.getPropertyValue('--surface').trim() || '#14151a',
  border:   CSS.getPropertyValue('--border').trim() || '#2a2d35',
  positive: CSS.getPropertyValue('--positive').trim() || '#6fc58a',
  negative: CSS.getPropertyValue('--negative').trim() || '#ef6459',
};

// ---------- State ----------
const SOURCE_TYPES = [
  { id: 'news',                 label: 'News' },
  { id: 'sec_10k',              label: '10-K' },
  { id: 'sec_8k',               label: '8-K' },
  { id: 'earnings_transcript',  label: 'Earnings call' },
  { id: 'peer_news',            label: 'Peer news' },
  { id: 'macro',                label: 'Macro' },
  { id: 'thirteen_f',           label: '13F positioning' },
];
const ALL_SOURCE_IDS = SOURCE_TYPES.map(s => s.id);

const STATE = {
  tickers: [],          // [{ticker, name, sector, moves}]
  currentTicker: null,  // 'AMD'
  bundle: null,         // latest loaded bundle
  selectedMoveIdx: null,
  enabledSources: new Set(ALL_SOURCE_IDS),  // full stack by default
  lastFullStack: null,  // last full-stack reference attribution
  fetchSeq: 0,          // monotonic to cancel stale fetches
};

const DIM_LABEL = {
  demand: 'Demand',
  pricing: 'Pricing',
  competitive: 'Competitive',
  management_credibility: 'Management credibility',
  macro: 'Macro',
};
const ARROW = { positive: '↑', negative: '↓', neutral: '→' };

// ---------- Fetch helpers ----------
async function fetchJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`Failed ${path}: ${res.status}`);
  return res.json();
}

// ---------- Formatters ----------
const pct = (x, digits = 2) =>
  x === null || x === undefined ? '—' : `${x >= 0 ? '+' : ''}${(x * 100).toFixed(digits)}%`;
const signed = (x, digits = 2) =>
  x === null || x === undefined ? '—' : `${x >= 0 ? '+' : ''}${x.toFixed(digits)}`;

// ---------- Ticker pill rendering ----------
function renderTickerPills() {
  const nav = document.getElementById('ticker-pills');
  nav.innerHTML = '';
  for (const t of STATE.tickers) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ticker-pill' + (t.ticker === STATE.currentTicker ? ' active' : '');
    btn.dataset.ticker = t.ticker;
    btn.setAttribute('role', 'tab');
    btn.setAttribute('aria-selected', t.ticker === STATE.currentTicker);
    btn.innerHTML =
      `<span class="ticker-sym">${t.ticker}</span>` +
      `<span class="ticker-sector">${t.sector}</span>`;
    btn.addEventListener('click', () => selectTicker(t.ticker));
    nav.appendChild(btn);
  }
}

// ---------- Overview (top stats) ----------
function renderOverview(bundle) {
  document.getElementById('ticker-title').textContent = `${bundle.ticker} · ${bundle.name}`;
  document.getElementById('ticker-sub').textContent =
    `${bundle.sector}  ·  ${bundle.start_date} → ${bundle.end_date}`;

  const n = bundle.prices.length;
  const m = bundle.moves.length;
  const down = bundle.moves.filter(x => x.return_pct < 0).length;
  const up = m - down;
  document.getElementById('stat-days').textContent = n.toLocaleString();
  document.getElementById('stat-moves').textContent = m.toLocaleString();
  document.getElementById('stat-split').textContent = `${down} ↓  /  ${up} ↑`;
}

// ---------- Chart ----------
function renderChart(bundle) {
  const priceByDate = new Map(bundle.prices.map(p => [p.date, p.close]));
  const moveX = bundle.moves.map(m => m.move_date);
  const moveY = bundle.moves.map(m => priceByDate.get(m.move_date) ?? null);
  const moveColors = bundle.moves.map(m => m.return_pct < 0 ? COLOR.negative : COLOR.positive);
  const moveText = bundle.moves.map(m =>
    `<b>${m.move_date}</b><br>close $${priceByDate.get(m.move_date)?.toFixed(2) ?? '—'}` +
    `<br>return ${pct(m.return_pct)}<br>vol z ${signed(m.vol_zscore)}`
  );
  const moveCustom = bundle.moves.map((m, i) => i);

  const traces = [
    {
      x: bundle.prices.map(p => p.date),
      y: bundle.prices.map(p => p.close),
      mode: 'lines',
      line: { color: COLOR.accent, width: 1.4 },
      name: 'Close',
      hovertemplate: '%{x}<br>$%{y:.2f}<extra></extra>',
    },
    {
      x: moveX,
      y: moveY,
      customdata: moveCustom,
      text: moveText,
      hovertemplate: '%{text}<extra></extra>',
      mode: 'markers',
      marker: {
        size: 10,
        color: moveColors,
        line: { color: COLOR.surface, width: 1.5 },
        symbol: 'circle',
      },
      name: 'Flagged move',
    }
  ];

  const layout = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor:  'rgba(0,0,0,0)',
    margin: { l: 56, r: 16, t: 14, b: 36 },
    font: { family: 'Inter, sans-serif', color: COLOR.muted, size: 12 },
    hoverlabel: {
      bgcolor: COLOR.surface,
      bordercolor: COLOR.border,
      font: { family: 'Inter, sans-serif', color: COLOR.text, size: 12 },
    },
    xaxis: {
      gridcolor: COLOR.border,
      linecolor: COLOR.border,
      tickcolor: COLOR.border,
      zeroline: false,
    },
    yaxis: {
      title: { text: 'Close ($)', standoff: 8 },
      gridcolor: COLOR.border,
      linecolor: COLOR.border,
      tickcolor: COLOR.border,
      zeroline: false,
      tickprefix: '$',
    },
    showlegend: true,
    legend: {
      orientation: 'h',
      x: 0, y: 1.02,
      xanchor: 'left', yanchor: 'bottom',
      font: { color: COLOR.muted },
    },
  };

  const config = { displaylogo: false, responsive: true,
                   modeBarButtonsToRemove: ['lasso2d', 'select2d'] };

  Plotly.react('chart', traces, layout, config);

  // Click handler: attach once. Reads current STATE so it works across ticker switches.
  const chartDiv = document.getElementById('chart');
  if (!chartDiv._clickHandlerAttached) {
    chartDiv.on('plotly_click', (ev) => {
      const pt = ev.points[0];
      if (!pt || pt.curveNumber !== 1) return;   // only flagged-move scatter
      selectMove(pt.customdata);
    });
    chartDiv._clickHandlerAttached = true;
  }
}

// ---------- Source toggles ----------
function renderToggleRow(availableCounts) {
  const row = document.getElementById('toggle-row');
  row.innerHTML = '';
  for (const src of SOURCE_TYPES) {
    const count = availableCounts[src.id] ?? 0;
    const checked = STATE.enabledSources.has(src.id) && count > 0;
    const disabled = count === 0;

    const wrapper = document.createElement('label');
    wrapper.className = 'src-toggle' +
      (checked ? ' checked' : '') +
      (disabled ? ' disabled' : '');

    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = src.id;
    input.checked = checked;
    input.disabled = disabled;
    input.addEventListener('change', (ev) => {
      if (ev.target.checked) STATE.enabledSources.add(src.id);
      else STATE.enabledSources.delete(src.id);
      renderToggleRow(availableCounts);
      recomputeAttribution();
    });

    const box = document.createElement('span');
    box.className = 'box';

    const labels = document.createElement('span');
    labels.className = 'labels';
    labels.innerHTML =
      `<span class="label">${src.label}</span>` +
      `<span class="count">${count} chunk${count === 1 ? '' : 's'}</span>`;

    wrapper.appendChild(input);
    wrapper.appendChild(box);
    wrapper.appendChild(labels);
    row.appendChild(wrapper);
  }
}

function renderToggleCaption(enabledCount, totalCount, chunksConsidered) {
  const el = document.getElementById('toggle-caption');
  if (enabledCount === 0) {
    el.innerHTML = `<span class="pill">0 / ${totalCount}</span> no sources enabled — attribution disabled.`;
    return;
  }
  el.innerHTML =
    `<span class="pill">${enabledCount} / ${totalCount}</span>` +
    `${chunksConsidered} chunk${chunksConsidered === 1 ? '' : 's'} feeding attribution.`;
}

// ---------- Attribution fetch ----------
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
  if (seq !== STATE.fetchSeq) return null;   // superseded by a newer request
  return data;
}

async function recomputeAttribution() {
  if (STATE.selectedMoveIdx === null) return;
  const move = STATE.bundle.moves[STATE.selectedMoveIdx];
  const enabled = Array.from(STATE.enabledSources);

  const availableCounts = computeAvailableCounts(move);
  const totalAvailable = Object.keys(availableCounts)
    .filter(k => availableCounts[k] > 0).length;

  if (enabled.length === 0) {
    // Invalidate any in-flight fetch so it can't land and clobber the empty render.
    STATE.fetchSeq++;
    renderAttributionEmpty(move);
    renderToggleCaption(0, totalAvailable, 0);
    return;
  }

  try {
    const data = await fetchAttribution(move, enabled);
    if (!data) return;   // stale
    renderAttributionFromResponse(move, data);
    renderToggleCaption(enabled.length, totalAvailable, data.chunks_considered);
  } catch (err) {
    console.error('attribution fetch failed', err);
  }
}

function computeAvailableCounts(move) {
  const counts = Object.fromEntries(ALL_SOURCE_IDS.map(s => [s, 0]));
  if (move.chunks_available && typeof move.chunks_available === 'object') {
    // Pre-baked truth: counts over the full pool, not just the top-10.
    Object.assign(counts, move.chunks_available);
    return counts;
  }
  // Fallback for old bundles that didn't carry chunks_available.
  for (const c of (move.chunks || [])) {
    counts[c.source_type] = (counts[c.source_type] ?? 0) + 1;
  }
  return counts;
}

function renderAttributionEmpty(move) {
  document.getElementById('attr-title').innerHTML =
    `Attribution <span class="attr-date">· ${move.move_date}</span>`;
  document.getElementById('attr-sub').textContent =
    `${STATE.bundle.ticker} · ${STATE.bundle.name}`;
  document.getElementById('char-pill').hidden = true;
  document.getElementById('kpis').hidden = true;
  document.getElementById('fullstack-ref').hidden = true;
  document.getElementById('dims-section').hidden = true;
  document.getElementById('evidence-section').hidden = true;
  document.getElementById('mock-banner').hidden = true;
  document.getElementById('zero-warning').hidden = false;
}

function renderAttributionFromResponse(move, response) {
  // Build an object shape matching the pre-baked bundle's move.attribution so
  // renderAttributionDetails() can handle both.
  const attr = response.attribution;
  const renderableMove = {
    move_date: move.move_date,
    attribution: {
      realized: attr.return_pct,
      predicted: attr.predicted_return_pct,
      character: attr.move_character,
      confidence: attr.confidence,
      chunks_considered: attr.chunks_considered,
      sources_used: attr.sources_used,
      dimensions: {
        demand: attr.demand,
        pricing: attr.pricing,
        competitive: attr.competitive,
        management_credibility: attr.management_credibility,
        macro: attr.macro,
      },
      model_notes: attr.model_notes,
    },
    chunks: response.chunks,
  };
  renderAttributionDetails(renderableMove);
}

// ---------- Attribution panel (render details from either pre-baked or API shape) ----------
function renderAttribution(bundle, moveIdx) {
  const card = document.getElementById('attribution-card');
  if (moveIdx === null || moveIdx === undefined) {
    document.getElementById('attr-title').textContent = 'Attribution';
    document.getElementById('attr-sub').textContent = 'Pick a flagged move to begin.';
    document.getElementById('kpis').hidden = true;
    document.getElementById('fullstack-ref').hidden = true;
    document.getElementById('dims-section').hidden = true;
    document.getElementById('evidence-section').hidden = true;
    document.getElementById('mock-banner').hidden = true;
    document.getElementById('char-pill').hidden = true;
    document.getElementById('zero-warning').hidden = true;
    return;
  }

  // Cache the full-stack pre-baked attribution for the "vs full stack" reference.
  STATE.lastFullStack = bundle.moves[moveIdx].attribution;

  renderAttributionDetails(bundle.moves[moveIdx]);
  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function renderAttributionDetails(move) {
  const attr = move.attribution;
  const bundle = STATE.bundle;

  document.getElementById('zero-warning').hidden = true;

  document.getElementById('attr-title').innerHTML =
    `Attribution <span class="attr-date">· ${move.move_date}</span>`;
  document.getElementById('attr-sub').textContent =
    `${bundle.ticker} · ${bundle.name}`;

  // Character pill
  const pill = document.getElementById('char-pill');
  pill.hidden = false;
  pill.className = `char-pill ${attr.character}`;
  pill.textContent = attr.character;

  // KPIs
  document.getElementById('kpis').hidden = false;
  const realized = document.getElementById('kpi-realized');
  realized.textContent = pct(attr.realized);
  realized.className = 'kpi-value ' + (attr.realized < 0 ? 'negative' : 'positive');

  const predicted = document.getElementById('kpi-predicted');
  predicted.textContent = pct(attr.predicted);
  predicted.className = 'kpi-value ' + (attr.predicted < 0 ? 'negative' : 'positive');

  const gapEl = document.getElementById('kpi-gap');
  if (attr.predicted !== null && attr.predicted !== undefined) {
    const gap = attr.predicted - attr.realized;
    const absGap = Math.abs(gap);
    if (absGap < 0.0005) {
      gapEl.textContent = 'predicted ≈ realized';
    } else if (gap > 0) {
      gapEl.textContent = `market overshot by ${pct(absGap, 2).replace('+', '')}`;
    } else {
      gapEl.textContent = `market undershot by ${pct(absGap, 2).replace('+', '')}`;
    }
  } else {
    gapEl.textContent = '';
  }

  document.getElementById('kpi-character').textContent = attr.character;
  document.getElementById('kpi-confidence').textContent = `${Math.round(attr.confidence * 100)}%`;

  // Full-stack reference row (bonus) — only show when user's selection differs
  // from the full stack.
  const fsRef = document.getElementById('fullstack-ref');
  const fullStack = STATE.lastFullStack;
  const isFullStack =
    STATE.enabledSources.size === ALL_SOURCE_IDS.length &&
    ALL_SOURCE_IDS.every(s => STATE.enabledSources.has(s));
  if (fullStack && !isFullStack) {
    document.getElementById('fs-predicted').textContent = pct(fullStack.predicted);
    document.getElementById('fs-character').textContent = fullStack.character;
    document.getElementById('fs-confidence').textContent =
      `${Math.round(fullStack.confidence * 100)}% confidence`;
    fsRef.hidden = false;
  } else {
    fsRef.hidden = true;
  }

  // Dimensions — sorted by weight, color by direction
  document.getElementById('dims-section').hidden = false;
  const dimBars = document.getElementById('dim-bars');
  dimBars.innerHTML = '';
  const dims = Object.entries(attr.dimensions)
    .map(([k, v]) => ({ key: k, ...v }))
    .sort((a, b) => b.weight - a.weight);
  const maxWeight = Math.max(0.001, ...dims.map(d => d.weight));
  for (const d of dims) {
    const row = document.createElement('div');
    row.className = 'dim-row';
    const pctWidth = (d.weight / maxWeight) * 100;
    row.innerHTML =
      `<div class="dim-name">` +
        `<span class="arrow" style="color: var(--${d.direction})">${ARROW[d.direction]}</span>` +
        `<span>${DIM_LABEL[d.key] || d.key}</span>` +
      `</div>` +
      `<div class="dim-track"><div class="dim-fill ${d.direction}" style="width:${pctWidth}%"></div></div>` +
      `<div class="dim-weight">${d.weight.toFixed(2)}</div>`;
    dimBars.appendChild(row);
  }

  // Evidence
  document.getElementById('evidence-section').hidden = false;
  const list = document.getElementById('evidence-list');
  list.innerHTML = '';
  const chunkMap = new Map((move.chunks || []).map(c => [c.chunk_id, c]));
  for (const d of dims) {
    const item = document.createElement('details');
    item.className = 'evidence-item';
    item.open = d.weight >= 0.25;

    const summary = document.createElement('summary');
    summary.className = 'evidence-summary';
    summary.innerHTML =
      `<span class="evidence-name">` +
        `<span class="arrow" style="color: var(--${d.direction})">${ARROW[d.direction]}</span>` +
        ` ${DIM_LABEL[d.key] || d.key}` +
      `</span>` +
      `<span class="evidence-weight">weight ${d.weight.toFixed(2)} · ${d.direction}</span>` +
      `<span class="evidence-chev">▾</span>`;
    item.appendChild(summary);

    const body = document.createElement('div');
    body.className = 'evidence-body';
    body.innerHTML = `<div class="evidence-rationale">${escapeHtml(d.rationale)}</div>`;
    for (const cid of d.evidence_chunk_ids) {
      const chunk = chunkMap.get(cid);
      const div = document.createElement('div');
      div.className = 'citation';
      if (!chunk) {
        div.innerHTML = `<div class="citation-missing">Missing chunk <code>${cid}</code> — coherence check would reject this attribution.</div>`;
      } else {
        const metaBits = [
          `<code>${escapeHtml(chunk.chunk_id)}</code>`,
          `<span class="sep">·</span>`,
          `<span class="src">${escapeHtml(chunk.source_type)}</span>`,
          `<span class="sep">·</span>`,
          escapeHtml(chunk.publication_date),
        ];
        if (chunk.section_name) {
          metaBits.push(`<span class="sep">·</span>`, `<em>${escapeHtml(chunk.section_name)}</em>`);
        }
        div.innerHTML =
          `<div class="citation-meta">${metaBits.join(' ')}</div>` +
          `<div class="citation-text">${escapeHtml(chunk.text)}</div>` +
          (chunk.source_url ? `<a class="citation-link" href="${chunk.source_url}" target="_blank" rel="noopener">source ↗</a>` : '');
      }
      body.appendChild(div);
    }
    item.appendChild(body);
    list.appendChild(item);
  }

  // Mock banner
  const banner = document.getElementById('mock-banner');
  if (attr.model_notes) {
    banner.hidden = false;
    banner.innerHTML = escapeHtmlPreserveCode(attr.model_notes);
  } else {
    banner.hidden = true;
  }

}

// ---------- Interactions ----------
async function selectTicker(t) {
  if (t === STATE.currentTicker) return;
  STATE.currentTicker = t;
  STATE.selectedMoveIdx = null;
  // enabledSources gets set in selectMove once we know what's available.
  renderTickerPills();
  const bundle = await fetchJSON(`data/${t}.json`);
  STATE.bundle = bundle;
  renderOverview(bundle);
  renderChart(bundle);
  if (bundle.moves.length > 0) {
    let maxIdx = 0, maxAbs = 0;
    bundle.moves.forEach((m, i) => {
      if (Math.abs(m.return_pct) > maxAbs) { maxAbs = Math.abs(m.return_pct); maxIdx = i; }
    });
    selectMove(maxIdx);
  } else {
    renderAttribution(bundle, null);
    document.getElementById('toggle-row').innerHTML = '';
    document.getElementById('toggle-caption').textContent = 'No flagged moves in this window.';
  }
}

function selectMove(idx) {
  STATE.selectedMoveIdx = idx;
  const move = STATE.bundle.moves[idx];
  const counts = computeAvailableCounts(move);
  // Enable only sources that actually have chunks for THIS move. Sources
  // with zero chunks (always-empty like earnings_transcript/peer_news/macro,
  // or sparse like thirteen_f for ABT) start disabled — both in STATE and
  // in the UI.
  STATE.enabledSources = new Set(
    ALL_SOURCE_IDS.filter(id => (counts[id] ?? 0) > 0)
  );
  renderAttribution(STATE.bundle, idx);
  renderToggleRow(counts);
  const totalAvailable = Object.keys(counts).filter(k => counts[k] > 0).length;
  renderToggleCaption(STATE.enabledSources.size, totalAvailable,
                      move.attribution?.chunks_considered ?? 0);
}

function resetToggles() {
  if (STATE.selectedMoveIdx === null) return;
  const move = STATE.bundle.moves[STATE.selectedMoveIdx];
  const counts = computeAvailableCounts(move);
  STATE.enabledSources = new Set(
    ALL_SOURCE_IDS.filter(id => (counts[id] ?? 0) > 0)
  );
  renderToggleRow(counts);
  renderAttribution(STATE.bundle, STATE.selectedMoveIdx);
}

// ---------- HTML escape ----------
function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
// Keep backticked `code` as <code>…</code> so the mock banner's references render properly.
function escapeHtmlPreserveCode(s) {
  const parts = String(s).split(/`([^`]+)`/g);
  return parts.map((p, i) => i % 2 === 0 ? escapeHtml(p) : `<code>${escapeHtml(p)}</code>`).join('');
}

// ---------- Boot ----------
(async function init() {
  try {
    const index = await fetchJSON('data/index.json');
    STATE.tickers = index.tickers;
    renderTickerPills();
    document.getElementById('reset-toggles').addEventListener('click', resetToggles);
    const initial = STATE.tickers.find(t => t.ticker === 'AMD')?.ticker
                  || STATE.tickers[0]?.ticker;
    if (initial) await selectTicker(initial);
  } catch (err) {
    document.getElementById('ticker-title').textContent = 'Error loading data';
    document.getElementById('ticker-sub').textContent = String(err);
    console.error(err);
  }
})();
