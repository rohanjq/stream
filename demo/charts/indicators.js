// SMC / Key-Level indicators + signals, implemented from the course MASTER docs.
// Each detector runs per pane on closed bars: draws zones/lines (via rectPrimitive
// from overlays.js and candle price lines) and PUBLISHES events to the Signals feed
// ("pattern X formed" / "Y happened") — the non-drawing half of the courses.
//
// Toggle groups (INDI): fvg, orderblocks, patterns, liquidity, premdisc.
// Hooked from chart.js: initIndicators(pane,ctx) + onIndicatorBarClose(pane,ctx).

const INDI = { fvg: true, orderblocks: true, patterns: true, liquidity: true, premdisc: false };
const SIGNALS = { items: [], seen: new Set(), max: 60 };

// ---- candle vocabulary (course: Marubozu body>80%, Pinbar body<30%, Doji≈0) ----
function icf(b) { const R = (b.high - b.low) || 1e-9, body = Math.abs(b.close - b.open); return { R, body, br: body / R, up: b.close >= b.open, dir: b.close > b.open ? 1 : b.close < b.open ? -1 : 0, mid: (b.high + b.low) / 2 }; }
const icMaru = (b) => icf(b).br > 0.80;
const isPin = (b) => icf(b).br < 0.30;
const isDoji = (b) => icf(b).br < 0.10;

// ---- detectors (operate on a closed-bar array; return plain data) ----
// Fair Value Gap / imbalance: 3-candle non-overlap with a big middle marubozu.
function detectFVG(bars, lookback) {
  const out = []; const start = Math.max(2, bars.length - lookback);
  for (let i = start; i < bars.length; i++) {
    const a = bars[i - 2], b = bars[i - 1], c = bars[i];
    const anyMaru = icMaru(a) || icMaru(b) || icMaru(c);       // master: at least one of the 3 is a marubozu
    if (c.low > a.high && anyMaru) {                           // bullish FVG (gap candle1.high -> candle3.low)
      const gap = c.low - a.high; const strong = gap >= 0.3 * icf(b).R || (icMaru(a) && icMaru(b) && icMaru(c));
      if (!laterFilled(bars, i, a.high, 1)) out.push({ dir: 'up', top: c.low, bottom: a.high, time: c.time, i, grade: strong ? 'strong' : 'weak' });
    } else if (c.high < a.low && anyMaru) {                    // bearish FVG
      const gap = a.low - c.high; const strong = gap >= 0.3 * icf(b).R || (icMaru(a) && icMaru(b) && icMaru(c));
      if (!laterFilled(bars, i, a.low, -1)) out.push({ dir: 'down', top: a.low, bottom: c.high, time: c.time, i, grade: strong ? 'strong' : 'weak' });
    }
  }
  return out.slice(-8);
}
function laterFilled(bars, i, edge, dir) { for (let j = i + 1; j < bars.length; j++) { if (dir > 0 ? bars[j].low <= edge : bars[j].high >= edge) return true; } return false; }

// Order block: last opposite-color candle before the impulsive candle of an unfilled FVG.
function detectOB(bars, fvgs) {
  const out = [];
  for (const g of fvgs) {
    let j = g.i - 2;                                            // candle before the FVG's middle
    for (let s = 0; s < 4 && j >= 0; s++, j--) { const b = bars[j]; if ((g.dir === 'up' && !icf(b).up) || (g.dir === 'down' && icf(b).up)) { out.push({ dir: g.dir, top: b.high, bottom: b.low, time: b.time, grade: g.grade }); break; } }
  }
  return out.slice(-6);
}

// Reversal / continuation candle patterns -> events (no chart-marker clash with structure).
function detectPatterns(bars, lookback) {
  const ev = []; const start = Math.max(3, bars.length - lookback);
  for (let i = start; i < bars.length; i++) {
    const a = bars[i - 1], c = bars[i];
    // engulfing: opposite marubozu >=25% bigger body
    if (icf(c).up && !icf(a).up && icf(c).body >= 1.25 * icf(a).body && c.close > a.open && c.open < a.close) ev.push({ time: c.time, type: 'Bull Engulfing', side: 'up' });
    if (!icf(c).up && icf(a).up && icf(c).body >= 1.25 * icf(a).body && c.close < a.open && c.open > a.close) ev.push({ time: c.time, type: 'Bear Engulfing', side: 'down' });
    // inside bar
    if (c.high < a.high && c.low > a.low) ev.push({ time: c.time, type: 'Inside Bar', side: 'flat' });
    if (i >= start + 1) {
      const p2 = bars[i - 2];
      // morning / evening star
      if (icMaru(p2) && !icf(p2).up && icf(a).br < 0.4 && icMaru(c) && icf(c).up && c.close > icf(p2).mid) ev.push({ time: c.time, type: 'Morning Star', side: 'up' });
      if (icMaru(p2) && icf(p2).up && icf(a).br < 0.4 && icMaru(c) && !icf(c).up && c.close < icf(p2).mid) ev.push({ time: c.time, type: 'Evening Star', side: 'down' });
      // three soldiers / crows
      if (icMaru(p2) && icMaru(a) && icMaru(c) && icf(p2).up && icf(a).up && icf(c).up && a.close > p2.close && c.close > a.close) ev.push({ time: c.time, type: 'Three Soldiers', side: 'up' });
      if (icMaru(p2) && icMaru(a) && icMaru(c) && !icf(p2).up && !icf(a).up && !icf(c).up && a.close < p2.close && c.close < a.close) ev.push({ time: c.time, type: 'Three Crows', side: 'down' });
      // tweezer: two pinbars with ~equal low/high
      if (isPin(a) && isPin(c) && Math.abs(a.low - c.low) / c.low < 0.0006) ev.push({ time: c.time, type: 'Tweezer Bottom', side: 'up' });
      if (isPin(a) && isPin(c) && Math.abs(a.high - c.high) / c.high < 0.0006) ev.push({ time: c.time, type: 'Tweezer Top', side: 'down' });
    }
  }
  return ev.slice(-12);
}

// Liquidity pools: clustered equal highs / lows; sweep = wick beyond then close back.
function detectLiquidity(bars, lookback, tol) {
  const start = Math.max(5, bars.length - lookback); const highs = [], lows = [];
  for (let i = start + 2; i < bars.length - 2; i++) {
    const h = bars[i].high, l = bars[i].low;
    if (h >= bars[i - 1].high && h >= bars[i - 2].high && h >= bars[i + 1].high && h >= bars[i + 2].high) highs.push({ p: h, i });
    if (l <= bars[i - 1].low && l <= bars[i - 2].low && l <= bars[i + 1].low && l <= bars[i + 2].low) lows.push({ p: l, i });
  }
  const cluster = (arr) => { const c = []; for (const x of arr) { const f = c.find((k) => Math.abs(k.p - x.p) / x.p < tol); if (f) { f.n++; f.p = (f.p * (f.n - 1) + x.p) / f.n; } else c.push({ p: x.p, n: 1 }); } return c.filter((k) => k.n >= 2); };
  const eqH = cluster(highs), eqL = cluster(lows);
  const ev = []; const last = bars[bars.length - 1];
  for (const k of eqH) if (last.high > k.p && last.close < k.p) ev.push({ time: last.time, type: 'Liquidity grab (sell-side)', side: 'down' });
  for (const k of eqL) if (last.low < k.p && last.close > k.p) ev.push({ time: last.time, type: 'Liquidity grab (buy-side)', side: 'up' });
  return { eqH: eqH.slice(-3), eqL: eqL.slice(-3), ev };
}

// Premium/Discount + Fib POI band on the most recent structural leg.
function premDisc(bars) {
  if (typeof computeStructure !== 'function') return null;
  const r = computeStructure(bars, {}); const sw = r.swings; if (sw.length < 2) return null;
  // Anchor to the IMPULSE LEG that made the current extreme, so the "bottom" in an
  // uptrend is the swing low the move launched FROM (the protected low before the
  // swing high) — not a later minor pullback low. Downtrend mirrors.
  const up = r.trend !== 'down';
  let hiSw = null, loSw = null;
  if (up) { for (let i = sw.length - 1; i >= 0; i--) if (sw[i].type === 'H') { hiSw = sw[i]; for (let j = i - 1; j >= 0; j--) if (sw[j].type === 'L') { loSw = sw[j]; break; } break; } }
  else { for (let i = sw.length - 1; i >= 0; i--) if (sw[i].type === 'L') { loSw = sw[i]; for (let j = i - 1; j >= 0; j--) if (sw[j].type === 'H') { hiSw = sw[j]; break; } break; } }
  if (!hiSw || !loSw) return null;
  const hi = hiSw.price, lo = loSw.price; if (hi - lo <= 0) return null;
  const eq = (hi + lo) / 2;
  const poiBot = up ? lo + 0.20 * (hi - lo) : lo + 0.618 * (hi - lo);   // buy golden pocket sits LOW (discount)
  const poiTop = up ? lo + 0.382 * (hi - lo) : lo + 0.80 * (hi - lo);   // sell golden pocket sits HIGH (premium)
  return { hi, lo, eq, poiTop: Math.max(poiTop, poiBot), poiBot: Math.min(poiTop, poiBot), from: Math.min(hiSw.time, loSw.time), up };
}

// ---- signals feed ----
function emitSignal(tf, e) {
  const key = tf + '|' + e.type + '|' + e.time;
  if (SIGNALS.seen.has(key)) return; SIGNALS.seen.add(key);
  SIGNALS.items.unshift({ tf, time: e.time, type: e.type, side: e.side });
  if (SIGNALS.items.length > SIGNALS.max) SIGNALS.items.pop();
  renderSignals();
}
function renderSignals() {
  const el = document.getElementById('signalsPanel'); if (!el) return;
  const rows = SIGNALS.items.map((s) => {
    const cls = s.side === 'up' ? 'win' : s.side === 'down' ? 'loss' : 'muted';
    const t = new Date(s.time * 1000).toUTCString().slice(17, 22);
    return `<div class="tp-row"><span class="muted">${t} · ${s.tf}</span><span class="${cls}">${s.type}</span></div>`;
  }).join('') || '<div class="tp-row muted">No signals yet — SMC / key-level events appear here.</div>';
  el.innerHTML = `<div class="tp-head"><span>Signals</span><span><span class="tp-min" title="collapse">▾</span> <span class="tp-close" id="sigClear">clear</span></span></div>` + rows;
  const c = document.getElementById('sigClear'); if (c) c.addEventListener('click', () => { SIGNALS.items = []; SIGNALS.seen.clear(); renderSignals(); });
}

// ---- render per pane ----
// Make a floating panel draggable (by its .tp-head) and collapsible (.tp-min).
// Listeners live on the container so they survive innerHTML re-renders.
function makePanelDraggable(el) {
  if (!el || el._ux) return; el._ux = true; let drag = null;
  el.addEventListener('pointerdown', (e) => {
    if (!e.target.closest('.tp-head') || e.target.closest('.tp-close') || e.target.closest('.tp-min')) return;
    const r = el.getBoundingClientRect(); drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
    el.style.right = 'auto'; el.style.bottom = 'auto'; el.setPointerCapture(e.pointerId); e.preventDefault();
  });
  el.addEventListener('pointermove', (e) => { if (!drag) return; el.style.left = Math.max(0, e.clientX - drag.dx) + 'px'; el.style.top = Math.max(0, e.clientY - drag.dy) + 'px'; });
  const end = () => { drag = null; }; el.addEventListener('pointerup', end); el.addEventListener('pointercancel', end);
  el.addEventListener('click', (e) => { if (e.target.closest('.tp-min')) el.classList.toggle('collapsed'); });
}
function initIndicators(pane, ctx) {
  pane.ind = { rects: [], plines: [] }; redrawIndicators(pane, ctx);
  if (!document.getElementById('signalsPanel')) {
    const el = document.createElement('div'); el.id = 'signalsPanel'; document.body.appendChild(el); makePanelDraggable(el); renderSignals();
    // Events panel is hidden by default; the header "Events" button toggles it, so
    // it never pops up on its own when grids/overlays change.
    const btn = document.getElementById('btnSignals');
    if (btn && !btn._wired) { btn._wired = true; btn.addEventListener('click', () => el.classList.toggle('open')); }
  }
}
function onIndicatorBarClose(pane, ctx) { redrawIndicators(pane, ctx); }

function clearInd(pane) {
  (pane.ind.rects || []).forEach((r) => { try { pane.candle.detachPrimitive(r); } catch (e) {} });
  (pane.ind.plines || []).forEach((l) => { try { pane.candle.removePriceLine(l); } catch (e) {} });
  pane.ind = { rects: [], plines: [] };
}
function box(pane, zone, fill, border) { const rp = rectPrimitive(zone, fill, border); pane.candle.attachPrimitive(rp); pane.ind.rects.push(rp); }

function redrawIndicators(pane, ctx) {
  if (!pane.ind) pane.ind = { rects: [], plines: [] };
  clearInd(pane);
  const bars = pane.forming ? pane.bars.slice(0, -1) : pane.bars;
  if (bars.length < 20) return;
  const now = bars[bars.length - 1].time;
  const tf = pane.cfg.tf;

  // Strong, fresh imbalances near price drive both FVG and order-block drawing.
  const last = bars[bars.length - 1].close;
  const near = (p) => Math.abs(p - last) / last <= 0.03;
  const nearest = (arr) => arr.slice().sort((a, b) => Math.abs((a.top + a.bottom) / 2 - last) - Math.abs((b.top + b.bottom) / 2 - last));
  const strong = detectFVG(bars, 300).filter((g) => g.grade === 'strong' && near((g.top + g.bottom) / 2));
  if (INDI.fvg) {
    for (const g of nearest(strong).slice(0, 3)) {
      box(pane, { fromTime: g.time, toTime: now, top: g.top, bottom: g.bottom }, g.dir === 'up' ? 'rgba(38,166,154,0.14)' : 'rgba(239,83,80,0.14)', g.dir === 'up' ? 'rgba(38,166,154,0.55)' : 'rgba(239,83,80,0.55)');
      emitSignal(tf, { time: g.time, type: `FVG ${g.dir} (strong)`, side: g.dir });
    }
  }
  if (INDI.orderblocks) {
    const obs = nearest(detectOB(bars, strong).filter((o) => near((o.top + o.bottom) / 2)));
    for (const ob of obs.slice(0, 3)) {
      box(pane, { fromTime: ob.time, toTime: now, top: ob.top, bottom: ob.bottom }, ob.dir === 'up' ? 'rgba(41,98,255,0.16)' : 'rgba(255,152,0,0.16)', ob.dir === 'up' ? 'rgba(41,98,255,0.8)' : 'rgba(255,152,0,0.8)');
      emitSignal(tf, { time: ob.time, type: `Order Block ${ob.dir}`, side: ob.dir });
    }
  }
  if (INDI.patterns) for (const e of detectPatterns(bars, 120)) emitSignal(tf, e);
  if (INDI.liquidity) {
    const lq = detectLiquidity(bars, 300, 0.0006);
    for (const k of lq.eqH) pane.ind.plines.push(pane.candle.createPriceLine({ price: k.p, color: 'rgba(239,83,80,0.6)', lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: `EQH x${k.n}` }));
    for (const k of lq.eqL) pane.ind.plines.push(pane.candle.createPriceLine({ price: k.p, color: 'rgba(38,166,154,0.6)', lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: `EQL x${k.n}` }));
    for (const e of lq.ev) emitSignal(tf, e);
  }
  if (INDI.premdisc) {
    const pd = premDisc(bars);
    if (pd) {
      box(pane, { fromTime: pd.from, toTime: now, top: pd.eq, bottom: pd.lo }, 'rgba(38,166,154,0.06)', 'rgba(38,166,154,0.3)'); // discount
      box(pane, { fromTime: pd.from, toTime: now, top: pd.hi, bottom: pd.eq }, 'rgba(239,83,80,0.06)', 'rgba(239,83,80,0.3)');   // premium
      box(pane, { fromTime: pd.from, toTime: now, top: pd.poiTop, bottom: pd.poiBot }, 'rgba(255,235,59,0.12)', 'rgba(255,235,59,0.5)'); // POI 61.8-80
    }
  }
}
