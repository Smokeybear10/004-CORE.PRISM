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
  selectedStrategy: 'fundamental_vs_nonfundamental',
  lastStrategies: {},   // {strategy_name: 'lean'|'fade'|'neutral'}
};

const DIM_LABEL = {
  demand: 'Demand',
  pricing: 'Pricing',
  competitive: 'Competitive',
  management_credibility: 'Management credibility',
  macro: 'Macro',
};
const ARROW = { positive: '↑', negative: '↓', neutral: '→' };

// Mirror of backtest.frameworks.RESEARCH_GROUNDED_PERSISTENCE — used to
// reproduce strategy_dimension_weighted's score client-side so the verdict
// subline can show the actual driver, not a generic blurb. Keep these in
// sync with backtest/frameworks.py if the priors are recalibrated.
const PERSISTENCE = {
  demand: 0.85,
  pricing: 0.65,
  competitive: 0.45,
  management_credibility: -0.15,
  macro: -0.75,
};

// ---------- Strategies ----------
// Strategy verdicts are computed by backtest.signal.STRATEGY_REGISTRY in
// Python — server.py runs them per-request, build_static.py bakes them into
// the static bundles. The frontend never recomputes them. STRATEGIES below
// is UI metadata only (labels + blurbs); IDs MUST match Python registry keys.
const STRATEGIES = [
  { id: 'fundamental_vs_nonfundamental', label: 'Fundamental vs Non',
    blurb: 'fundamental cause → lean, sentiment-driven → fade.',
    description:
      'Asks one question: did the move come from something <em>real and ' +
      'durable</em> — a genuine earnings beat, customer growth, pricing ' +
      'power, a competitive shift — or from something <em>soft</em> like a ' +
      'fear cycle, hype, or a single news headline? If the cause looks ' +
      'fundamental, <span class="pos">lean</span> with the move (fundamentals ' +
      'tend to keep paying off). If it looks like a sentiment reaction, ' +
      '<span class="neg">fade</span> it (those moves usually unwind). When ' +
      'the model can\'t tell, sit it out.' },

  { id: 'expected_vs_realized',          label: 'Expected vs Realized',
    blurb: 'market overreacted → fade. Underreacted → lean.',
    description:
      'The model reads the news and filings around the move and estimates ' +
      'how much the stock <em>should</em> have moved given that evidence. ' +
      'We compare to what actually happened. If the stock moved <em>much ' +
      'more</em> than the news justifies, the market overreacted — bet on a ' +
      'pullback (<span class="neg">fade</span>). If it moved <em>less</em> ' +
      'than the news justifies, the price hasn\'t caught up yet — bet on ' +
      'more move in the same direction (<span class="pos">lean</span>). If ' +
      'they roughly agree, the news is already priced in — skip.' },

  { id: 'dimension_weighted',            label: 'Dimension-weighted',
    blurb: 'fundamental drivers → lean. Macro/sentiment drivers → fade.',
    description:
      'Different <em>kinds</em> of price moves have very different staying ' +
      'power, going back to the post-earnings drift literature. Moves ' +
      'driven by real <em>demand</em> (units, customers) or <em>pricing</em> ' +
      '(margins, price hikes) tend to keep paying off for weeks. Moves ' +
      'driven by <em>macro</em> shocks (Fed, rates, geopolitics) or single ' +
      'management-credibility hits tend to fully unwind. This strategy ' +
      'measures how much of the move came from each cause and weights them: ' +
      'mostly demand or pricing → <span class="pos">lean</span>; mostly ' +
      'macro or one-off mgmt noise → <span class="neg">fade</span>.' },

  { id: 'hybrid',                        label: 'Hybrid',
    blurb: 'fundamental check, then overshoot check, then driver sanity check.',
    description:
      'Stacks the other three checks in order. <em>First,</em> is the move ' +
      'fundamental or just narrative? Narrative → ' +
      '<span class="neg">fade</span>; unclear → skip. <em>Second,</em> for ' +
      'fundamental moves, did the price still overshoot what the news ' +
      'justified? If yes → <span class="neg">fade</span> the overshoot. ' +
      '<em>Third,</em> sanity-check the strongest cause — if the move was ' +
      'driven mostly by something that historically reverses (like a macro ' +
      'shock), back off to skip; otherwise <span class="pos">lean</span>.' },
];

// Plain-English labels for the 5 attribution dimensions, used in the
// "What the model concluded" text. Keep these reader-friendly — they're
// the only place a non-engineer sees a dimension name.
const DIM_PHRASE = {
  demand:                 'real demand growth (units, customers, market share)',
  pricing:                'pricing power (margins, price hikes, mix)',
  competitive:            'competitive dynamics (market share, rivals, moats)',
  management_credibility: 'management credibility (guidance, execution, leadership)',
  macro:                  'macro forces (rates, FX, commodities, geopolitics)',
};

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
  document.getElementById('ticker-title').textContent = bundle.name;
  document.getElementById('ticker-sub').textContent =
    `${bundle.ticker} · ${bundle.sector} · ${bundle.start_date} → ${bundle.end_date}`;

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
  const hoverText = (m) =>
    `<b>${m.move_date}</b><br>close $${priceByDate.get(m.move_date)?.toFixed(2) ?? '—'}` +
    `<br>return ${pct(m.return_pct)}<br>vol z ${signed(m.vol_zscore)}`;

  const rallyIdx = [];
  const selloffIdx = [];
  bundle.moves.forEach((m, i) => {
    (m.return_pct < 0 ? selloffIdx : rallyIdx).push(i);
  });
  const buildMarkerTrace = (idx, color, name) => ({
    x: idx.map(i => bundle.moves[i].move_date),
    y: idx.map(i => priceByDate.get(bundle.moves[i].move_date) ?? null),
    customdata: idx,
    text: idx.map(i => hoverText(bundle.moves[i])),
    hovertemplate: '%{text}<extra></extra>',
    mode: 'markers',
    marker: {
      size: 10,
      color,
      line: { color: COLOR.surface, width: 1.5 },
      symbol: 'circle',
    },
    name,
  });

  const traces = [
    {
      x: bundle.prices.map(p => p.date),
      y: bundle.prices.map(p => p.close),
      mode: 'lines',
      line: { color: COLOR.accent, width: 1.4 },
      name: 'Close',
      hovertemplate: '%{x}<br>$%{y:.2f}<extra></extra>',
    },
    buildMarkerTrace(rallyIdx, COLOR.positive, 'Rally (up move)'),
    buildMarkerTrace(selloffIdx, COLOR.negative, 'Selloff (down move)'),
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
      if (!pt || pt.curveNumber === 0) return;   // skip the close-price line
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
    if (disabled) {
      // Hover hint explaining why the toggle is greyed out. News + peer_news
      // come from a Yahoo Finance parquet whose coverage starts in early
      // 2025, so older moves legitimately have nothing in those slots.
      // The other sources (SEC / earnings / macro / 13F) extend further
      // back but can still be sparse per (ticker, date).
      const hint = (src.id === 'news' || src.id === 'peer_news' ||
                    src.id === 'sector_news')
        ? 'No Yahoo News coverage for this date — bundled news parquet starts in early 2025.'
        : `No ${src.label} chunks for this (ticker, date).`;
      wrapper.setAttribute('title', hint);
    }

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

// ---------- Strategy row + verdict ----------
function renderStrategyRow() {
  const row = document.getElementById('strategy-row');
  row.innerHTML = '';
  for (const s of STRATEGIES) {
    const verdict = STATE.lastStrategies[s.id];
    const verdictClass = verdict || 'pending';
    const verdictText = verdict || '—';
    const active = s.id === STATE.selectedStrategy;
    const wrapper = document.createElement('label');
    wrapper.className = 'strategy-pill' + (active ? ' active' : '');
    wrapper.setAttribute('role', 'radio');
    wrapper.setAttribute('aria-checked', active);
    wrapper.innerHTML =
      `<input type="radio" name="strategy" value="${s.id}" ${active ? 'checked' : ''}>` +
      `<span class="name">${s.label}</span>` +
      `<span class="verdict ${verdictClass}">${verdictText}</span>`;
    wrapper.addEventListener('click', () => selectStrategy(s.id));
    row.appendChild(wrapper);
  }
  renderStrategyVerdict();
}

function renderStrategyVerdict() {
  const nameEl = document.getElementById('verdict-strategy-name');
  const wordEl = document.getElementById('verdict-word');
  const subEl = document.getElementById('verdict-subline');
  const howEl = document.getElementById('verdict-explainer-how');
  const whyEl = document.getElementById('verdict-explainer-why');
  const verdict = STATE.lastStrategies[STATE.selectedStrategy];
  const meta = STRATEGIES.find(s => s.id === STATE.selectedStrategy);

  // "How this strategy decides" stays the same regardless of verdict —
  // it's a property of the strategy itself, not the move.
  if (howEl) howEl.innerHTML = meta && meta.description ? meta.description : '—';

  if (!verdict) {
    nameEl.textContent = meta ? meta.label.toUpperCase() : 'PICK A MOVE';
    wordEl.textContent = '—';
    wordEl.dataset.state = 'idle';
    subEl.innerHTML = meta ? meta.blurb : '';
    if (whyEl) whyEl.innerHTML = 'Pick a flagged move on the chart above.';
    return;
  }

  // Brief swap animation: fade out, swap text, fade in.
  wordEl.classList.add('swap');
  setTimeout(() => {
    nameEl.textContent = `${meta.label.toUpperCase()} SAYS`;
    const display = verdict === 'neutral' ? 'SKIP' : verdict.toUpperCase();
    wordEl.textContent = display;
    wordEl.dataset.state = verdict === 'neutral' ? 'skip' : verdict;
    subEl.innerHTML = buildVerdictSubline(STATE.selectedStrategy, verdict);
    if (whyEl) whyEl.innerHTML = buildVerdictConclusion(
      STATE.selectedStrategy, verdict, STATE.lastFullStack);
    wordEl.classList.remove('swap');
  }, 90);
}

// Helpers for strategy-specific computations the subline + explainer share.
function _dimWeightedScore(ref) {
  let score = 0, top = null, topAbs = 0;
  for (const [k, v] of Object.entries(ref.dimensions || {})) {
    const c = (PERSISTENCE[k] ?? 0) * (v.weight ?? 0);
    score += c;
    if (Math.abs(c) > topAbs) { topAbs = Math.abs(c); top = k; }
  }
  return { score, top };
}

function _dominantDimension(ref) {
  let domName = null, domWeight = 0;
  for (const [k, v] of Object.entries(ref.dimensions || {})) {
    if ((v.weight ?? 0) > domWeight) { domWeight = v.weight; domName = k; }
  }
  return domName;
}

// Each strategy in backtest/frameworks.py uses a different signal to make its
// lean/fade/neutral call. The subline surfaces THAT strategy's inputs *plus*
// confidence (a common factor across all four).
function buildVerdictSubline(strategyId, verdict) {
  const ref = STATE.lastFullStack;
  const move = (STATE.bundle && STATE.selectedMoveIdx !== null)
    ? STATE.bundle.moves[STATE.selectedMoveIdx]
    : null;
  if (!move || !ref) {
    const meta = STRATEGIES.find(s => s.id === strategyId);
    return meta ? meta.blurb : '';
  }

  const SEP = ' &nbsp;·&nbsp; ';
  const conf = Math.round((ref.confidence ?? 0) * 100);
  const confHtml = `confidence <span class="num">${conf}%</span>`;

  let driverHtml = '';
  if (strategyId === 'fundamental_vs_nonfundamental') {
    driverHtml = `character <span class="num">${ref.character}</span>`;
  } else if (strategyId === 'expected_vs_realized') {
    const realized = ref.realized;
    const predicted = ref.predicted;
    const hasPred = predicted !== null && predicted !== undefined;
    if (!hasPred) {
      driverHtml = `predicted <span class="num">—</span>${SEP}` +
                   `<span class="muted">no baseline → neutral</span>`;
    } else if (predicted === 0) {
      driverHtml = `predicted <span class="num">0%</span>${SEP}` +
                   `<span class="muted">no baseline magnitude → neutral</span>`;
    } else if (predicted * realized < 0) {
      driverHtml = `realized <span class="num">${pct(realized)}</span>${SEP}` +
                   `predicted <span class="num">${pct(predicted)}</span>${SEP}` +
                   `<span class="muted">opposite sign → neutral</span>`;
    } else {
      const ratio = Math.abs(realized) / Math.abs(predicted);
      driverHtml = `realized <span class="num">${pct(realized)}</span>${SEP}` +
                   `predicted <span class="num">${pct(predicted)}</span>${SEP}` +
                   `ratio <span class="num">${ratio.toFixed(2)}×</span>`;
    }
  } else if (strategyId === 'dimension_weighted') {
    const { score, top } = _dimWeightedScore(ref);
    const scoreSign = score >= 0 ? '+' : '';
    const scoreCls = score >= 0.20 ? 'delta-up' : score <= -0.20 ? 'delta-down' : '';
    const topLabel = top ? (DIM_LABEL[top] || top) : '—';
    driverHtml = `score <span class="num ${scoreCls}">${scoreSign}${score.toFixed(2)}</span>${SEP}` +
                 `top driver <span class="num">${topLabel}</span>`;
  } else if (strategyId === 'hybrid') {
    const domName = _dominantDimension(ref);
    const domLabel = domName ? (DIM_LABEL[domName] || domName) : '—';
    const domPersist = domName ? (PERSISTENCE[domName] ?? 0) : 0;
    const domSign = domPersist >= 0 ? '+' : '';
    driverHtml = `character <span class="num">${ref.character}</span>${SEP}` +
                 `top dim <span class="num">${domLabel}</span> ` +
                 `<span class="muted">(persist ${domSign}${domPersist.toFixed(2)})</span>`;
  } else {
    const realized = ref.realized;
    const predicted = ref.predicted;
    const hasPred = predicted !== null && predicted !== undefined;
    const realizedHtml = `realized <span class="num">${pct(realized)}</span>`;
    const predHtml = hasPred
      ? `predicted <span class="num">${pct(predicted)}</span>`
      : `predicted <span class="num">—</span>`;
    driverHtml = `${realizedHtml}${SEP}${predHtml}`;
  }

  return `${driverHtml}${SEP}${confHtml}`;
}

// "What the model concluded" — a strategy-specific paragraph that walks
// through the inputs that produced THIS verdict. Reads the same numbers the
// subline shows, but in plain English so the reader can reason about whether
// the call is sound.
function buildVerdictConclusion(strategyId, verdict, ref) {
  if (!ref) return 'Pick a flagged move on the chart above.';
  const conf = Math.round((ref.confidence ?? 0) * 100);
  const verdictWord = verdict === 'neutral' ? 'SKIP'
                    : verdict === 'lean'    ? 'LEAN'
                    : verdict === 'fade'    ? 'FADE' : '—';
  const verdictCls = verdict === 'lean' ? 'pos'
                   : verdict === 'fade' ? 'neg' : 'muted';
  const tag = `<span class="${verdictCls}">${verdictWord}</span>`;
  const confTail = `Confidence <span class="num">${conf}%</span>.`;

  if (strategyId === 'fundamental_vs_nonfundamental') {
    if (ref.character === 'structural') {
      return `Model labeled the move <span class="num">structural</span> — ` +
             `cause is judged durable, so this strategy says ${tag} (trade with the move). ${confTail}`;
    }
    if (ref.character === 'transient') {
      return `Model labeled the move <span class="num">transient</span> — ` +
             `narrative-driven and likely to revert, so this strategy says ${tag} (trade against). ${confTail}`;
    }
    return `Model labeled the move <span class="num">${ref.character}</span> — ` +
           `not enough conviction either way, so this strategy says ${tag}. ${confTail}`;
  }

  if (strategyId === 'expected_vs_realized') {
    const realized = ref.realized;
    const predicted = ref.predicted;
    const hasPred = predicted !== null && predicted !== undefined;
    if (!hasPred) {
      return `Model didn\'t produce a predicted return for this move, so there\'s no ` +
             `baseline to compare realized against — strategy is forced to ${tag}. ${confTail}`;
    }
    if (predicted === 0) {
      return `Predicted return is zero, leaving no magnitude to ratio against — strategy ` +
             `is forced to ${tag}. ${confTail}`;
    }
    if (predicted * realized < 0) {
      return `Predicted (<span class="num">${pct(predicted)}</span>) and realized ` +
             `(<span class="num">${pct(realized)}</span>) have opposite signs — the move ` +
             `went the opposite way the evidence implied, so this strategy refuses to call ` +
             `it and returns ${tag}. ${confTail}`;
    }
    const ratio = Math.abs(realized) / Math.abs(predicted);
    if (ratio >= 1.5) {
      return `Realized magnitude (<span class="num">${pct(realized)}</span>) is ` +
             `<span class="num">${ratio.toFixed(2)}×</span> the predicted ` +
             `(<span class="num">${pct(predicted)}</span>) — overshoot beyond the 1.5× ` +
             `band, read as overreaction → ${tag}. ${confTail}`;
    }
    if (ratio <= 0.5) {
      return `Realized magnitude (<span class="num">${pct(realized)}</span>) is only ` +
             `<span class="num">${ratio.toFixed(2)}×</span> the predicted ` +
             `(<span class="num">${pct(predicted)}</span>) — undershoot below the 0.5× ` +
             `band, the move has room → ${tag}. ${confTail}`;
    }
    return `Realized vs predicted ratio is <span class="num">${ratio.toFixed(2)}×</span> ` +
           `— inside the [0.5×, 1.5×] neutral band, so this strategy says ${tag}. ${confTail}`;
  }

  if (strategyId === 'dimension_weighted') {
    const { score, top } = _dimWeightedScore(ref);
    const scoreSign = score >= 0 ? '+' : '';
    const topLabel = top ? (DIM_LABEL[top] || top) : '—';
    const topPersist = top ? (PERSISTENCE[top] ?? 0) : 0;
    const topPersistSign = topPersist >= 0 ? '+' : '';
    if (score >= 0.20) {
      return `Weighted score <span class="num pos">${scoreSign}${score.toFixed(2)}</span> ` +
             `clears the +0.20 lean threshold. <span class="num">${topLabel}</span> ` +
             `(persistence <span class="num">${topPersistSign}${topPersist.toFixed(2)}</span>) ` +
             `is the dominant contributor → ${tag}. ${confTail}`;
    }
    if (score <= -0.20) {
      return `Weighted score <span class="num neg">${scoreSign}${score.toFixed(2)}</span> ` +
             `falls below the −0.20 fade threshold. <span class="num">${topLabel}</span> ` +
             `(persistence <span class="num">${topPersistSign}${topPersist.toFixed(2)}</span>) ` +
             `dominates and historically reverts → ${tag}. ${confTail}`;
    }
    return `Weighted score <span class="num">${scoreSign}${score.toFixed(2)}</span> sits ` +
           `inside the [−0.20, +0.20] neutral band — no dimension dominates clearly → ${tag}. ${confTail}`;
  }

  if (strategyId === 'hybrid') {
    const char = ref.character;
    const realized = ref.realized;
    const predicted = ref.predicted;
    const domName = _dominantDimension(ref);
    const domLabel = domName ? (DIM_LABEL[domName] || domName) : '—';
    const domPersist = domName ? (PERSISTENCE[domName] ?? 0) : 0;

    if (char === 'transient') {
      return `Layer 1 fired: character is <span class="num">transient</span> → ${tag} ` +
             `(narrative move, expect reversion). Later layers skipped. ${confTail}`;
    }
    if (char === 'mixed' || char === 'unclear') {
      return `Layer 1 fired: character is <span class="num">${char}</span> → ${tag}. ` +
             `Strategy refuses to act on a low-conviction read. ${confTail}`;
    }
    // structural beyond here
    const hasPred = predicted !== null && predicted !== undefined && predicted !== 0
                    && predicted * realized > 0;
    if (hasPred && Math.abs(realized) >= 1.5 * Math.abs(predicted)) {
      return `Character is <span class="num">structural</span>, but layer 2 caught an ` +
             `overshoot — realized (<span class="num">${pct(realized)}</span>) is ≥1.5× ` +
             `predicted (<span class="num">${pct(predicted)}</span>) → ${tag}. ${confTail}`;
    }
    if (domPersist < 0) {
      return `Character is <span class="num">structural</span> and overshoot check passed, ` +
             `but layer 3 downgraded — dominant dimension <span class="num">${domLabel}</span> ` +
             `has negative historical persistence (<span class="num">${domPersist.toFixed(2)}</span>), ` +
             `so the strategy backs off → ${tag}. ${confTail}`;
    }
    return `All three layers passed — structural character, no overshoot, dominant dimension ` +
           `<span class="num">${domLabel}</span> historically persists ` +
           `(<span class="num">+${domPersist.toFixed(2)}</span>) → ${tag}. ${confTail}`;
  }

  return `Strategy concluded ${tag}. ${confTail}`;
}

function selectStrategy(id) {
  if (id === STATE.selectedStrategy) return;
  STATE.selectedStrategy = id;
  renderStrategyRow();
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
  STATE.lastStrategies = response.strategies || {};
  renderStrategyRow();
  renderAttributionDetails(renderableMove);
}

// ---------- Attribution panel (render details from either pre-baked or API shape) ----------
function renderAttribution(bundle, moveIdx) {
  const card = document.getElementById('attribution-card');
  if (moveIdx === null || moveIdx === undefined) {
    STATE.lastStrategies = {};
    renderStrategyRow();
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

  STATE.lastStrategies = bundle.moves[moveIdx].strategies || {};
  renderStrategyRow();

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

    // Prefer the rich `cited_evidence` shape (per-citation quote +
    // reasoning) when available; fall back to bare chunk_ids + raw text
    // for placeholder attributions or older bundles that don't carry it.
    const richCitations = Array.isArray(d.cited_evidence) ? d.cited_evidence : [];
    const useRich = richCitations.length > 0;
    const iter = useRich
      ? richCitations.map(ce => ({
          cid: ce.chunk_id,
          quote: ce.quote || '',
          reasoning: ce.reasoning || '',
        }))
      : (d.evidence_chunk_ids || []).map(cid => ({ cid, quote: '', reasoning: '' }));

    for (const it of iter) {
      const chunk = chunkMap.get(it.cid);
      const div = document.createElement('div');
      div.className = 'citation';
      if (!chunk) {
        div.innerHTML = `<div class="citation-missing">Missing chunk <code>${it.cid}</code> — coherence check would reject this attribution.</div>`;
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

        let bodyHtml = `<div class="citation-meta">${metaBits.join(' ')}</div>`;
        if (it.quote) {
          // Rich shape: render the quote as a blockquote-style excerpt and
          // the reasoning as the why-this-mattered note. Drop the full
          // chunk text — that's the whole point of the upgrade.
          bodyHtml +=
            `<blockquote class="citation-quote">${escapeHtml(it.quote)}</blockquote>`;
          if (it.reasoning) {
            bodyHtml +=
              `<div class="citation-reasoning">${escapeHtml(it.reasoning)}</div>`;
          }
        } else {
          // Fallback to raw chunk text when no quote was provided.
          bodyHtml += `<div class="citation-text">${escapeHtml(chunk.text)}</div>`;
        }
        if (chunk.source_url) {
          bodyHtml +=
            `<a class="citation-link" href="${chunk.source_url}" target="_blank" rel="noopener">source ↗</a>`;
        }
        div.innerHTML = bodyHtml;
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
  // Render the pre-baked attribution as a fast first paint, then fire the
  // live /api/attribute call so the per-citation quote + reasoning shape
  // arrives without the user having to toggle a source. The live call's
  // result replaces the placeholder when it lands.
  renderAttribution(STATE.bundle, idx);
  renderToggleRow(counts);
  const totalAvailable = Object.keys(counts).filter(k => counts[k] > 0).length;
  renderToggleCaption(STATE.enabledSources.size, totalAvailable,
                      move.attribution?.chunks_considered ?? 0);
  if (STATE.enabledSources.size > 0) {
    recomputeAttribution();
  }
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
