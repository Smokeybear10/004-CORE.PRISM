// =============================================================
// PRISM Pitch Deck — click-to-advance state machine
// Section 01 (Summary) only for now; extend by appending more
// <section class="state" data-state="N"> elements.
// =============================================================

const states = Array.from(document.querySelectorAll('.state'));
const stateLabel = document.getElementById('stateLabel');
const pager = document.getElementById('pager');

// State metadata — label per state (and optional section grouping later)
const META = {
  0: { label: 'Section 01 · Open' },
  1: { label: 'Section 01 · Problem' },
  2: { label: 'Section 01 · Gap' },
  3: { label: 'Section 01 · Methodology' },
  4: { label: 'Section 01 · Decision' },
  5: { label: 'Section 01 · → Section 02' },
  6: { label: 'Section 02 · Human Benchmark' },
};

let idx = 0;

function go(targetIdx) {
  if (targetIdx < 0 || targetIdx >= states.length || targetIdx === idx) return;
  idx = targetIdx;
  render();
}

// ── Demo iframe (state 6) ────────────────────────────────────
// Lazy-load: only set src when the demo state is first activated,
// so opening the deck doesn't ping localhost:8000 unnecessarily.
const demoState = document.querySelector('.state-demo');
const demoFrame = document.querySelector('[data-demo-frame]');
const demoFallback = document.querySelector('[data-demo-fallback]');
const demoUrl = demoState && demoState.getAttribute('data-demo-url');
let demoLoaded = false;
let demoLoadTimer = null;

function loadDemo() {
  if (!demoFrame || !demoUrl) return;
  if (demoFallback) demoFallback.hidden = true;
  demoFrame.style.visibility = 'visible';
  demoFrame.src = demoUrl;
  demoLoaded = true;
  // If the iframe never fires `load` within 4s, the dev server is
  // probably not running — surface the fallback panel.
  clearTimeout(demoLoadTimer);
  demoLoadTimer = setTimeout(showDemoFallback, 4000);
  demoFrame.addEventListener('load', () => {
    clearTimeout(demoLoadTimer);
    if (demoFallback) demoFallback.hidden = true;
  }, { once: true });
}

function showDemoFallback() {
  if (!demoFallback) return;
  demoFallback.hidden = false;
  if (demoFrame) demoFrame.style.visibility = 'hidden';
}

function maybeLoadDemo() {
  if (!demoState) return;
  if (states[idx] === demoState && !demoLoaded) loadDemo();
}

document.querySelectorAll('[data-demo-reload]').forEach((btn) => {
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    demoLoaded = false;
    loadDemo();
  });
});

let isFirstRender = true;

function render() {
  states.forEach((s, i) => {
    if (i === idx) {
      // Skip the remove/reflow/re-add dance on first render — the HTML
      // already has data-active on the opening state, and forcing it off
      // briefly causes a visible fade-out flash before the fade-in.
      // Only run the replay dance on revisit.
      if (!isFirstRender) {
        s.removeAttribute('data-active');
        void s.offsetWidth;
      }
      s.setAttribute('data-active', '');
    } else {
      s.removeAttribute('data-active');
    }
  });
  isFirstRender = false;
  const meta = META[idx] || {};
  if (stateLabel) stateLabel.textContent = meta.label || `State ${idx + 1}`;
  if (pager) pager.textContent = `page ${idx + 1} of ${states.length}`;
  maybeLoadDemo();
}

function next() {
  if (idx < states.length - 1) { idx++; render(); }
}
function prev() {
  if (idx > 0) { idx--; render(); }
}
function home() { idx = 0; render(); }

// Click anywhere to advance (skip on real interactive controls)
document.addEventListener('click', (e) => {
  // Explicit jump target wins (e.g. handoff card → "Human Benchmark").
  const jumper = e.target.closest('[data-go]');
  if (jumper) {
    const target = parseInt(jumper.getAttribute('data-go'), 10);
    if (!Number.isNaN(target)) { go(target); return; }
  }
  // Explicit back trigger.
  if (e.target.closest('[data-back]')) { prev(); return; }
  // Real interactive controls: don't advance.
  if (e.target.closest('a, button, input, textarea, select')) return;
  next();
});

// Keyboard activation for the handoff card (role="button" treats Enter/Space as click).
document.querySelectorAll('[data-go]').forEach((el) => {
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      const target = parseInt(el.getAttribute('data-go'), 10);
      if (!Number.isNaN(target)) go(target);
    }
  });
});

// Keyboard
document.addEventListener('keydown', (e) => {
  if (e.target.closest('input, textarea')) return;
  // Let focused buttons handle their own Enter/Space activation —
  // otherwise the global "next/advance on Enter" would clobber the back button.
  if ((e.key === 'Enter' || e.key === ' ') &&
      e.target.closest('button, [role="button"]')) return;
  switch (e.key) {
    case 'ArrowRight':
    case ' ':
    case 'Enter':
    case 'PageDown':
      e.preventDefault();
      next();
      break;
    case 'ArrowLeft':
    case 'PageUp':
    case 'Backspace':
      e.preventDefault();
      prev();
      break;
    case 'Home':
      home();
      break;
    case 'f':
    case 'F':
      if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {});
      else document.exitFullscreen();
      break;
    case 'Escape':
      if (document.fullscreenElement) document.exitFullscreen();
      break;
  }
});

render();
