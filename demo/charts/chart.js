// Live/replay charting app.
// - MODE 'live' (default): AllTick BTCUSDT. Backfill pulled fresh from the local
//   server's rolling cache (/api/klines) on every load, then extended by the
//   AllTick WebSocket tick stream. Switching timeframes rebuilds charts IN PLACE
//   (no page reload) so the single WebSocket connection stays up — no reconnect.
// - MODE 'replay' (?mode=replay): gold Thursday, played back tick-by-tick.

const CONFIG = {
  panes: [{ tf: '5m', sec: 300 }],
  emas: [
    { period: 5, varName: '--ema-5' }, { period: 9, varName: '--ema-9' }, { period: 21, varName: '--ema-21' },
    { period: 50, varName: '--ema-50' }, { period: 200, varName: '--ema-200' },
  ],
  emasOn: false,
  overlays: { marketStructure: true, keyLevels: true },
};

const TF_MAP = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600 };
{ const raw = (new URLSearchParams(location.search).get('tf') || '').split(',').map((s) => s.trim()).filter((t) => TF_MAP[t]);
  if (raw.length) CONFIG.panes = raw.map((t) => ({ tf: t, sec: TF_MAP[t] })); }
CONFIG.panes.sort((a, b) => a.sec - b.sec);

const MODE = (new URLSearchParams(location.search).get('mode') === 'replay') ? 'replay' : 'live'; // live is default
const SYMBOL = new URLSearchParams(location.search).get('sym') || 'GOLD'; // AllTick live instrument (this token: GOLD / BTCUSDT)
const TICKS_PER_BAR = 20;
const SPEEDS = { Slow: 6000, Normal: 4000, Fast: 2000, Turbo: 1000 };
const DAY = 86400;
let tickMs = SPEEDS.Fast / TICKS_PER_BAR;

const rootStyle = getComputedStyle(document.querySelector('.viz-root'));
const css = (n) => rootStyle.getPropertyValue(n).trim();
const nf = (v) => v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const EMAS = CONFIG.emas;
const $ = (id) => document.getElementById(id);
const grid = $('grid');

let DATA = MODE === 'live' ? {} : window.HIST;   // live: filled by loadData() from the server cache
let panes = [];
let replayStartTime = 0, replayEndTime = 0;
let timer = null, running = false, tickCount = 0, lastStructTrend = null;
let feedStarted = false, feedHandle = null;

function seedEma(bars, period) {
  const m = 2 / (period + 1); const out = []; let prev, sum = 0;
  for (let i = 0; i < bars.length; i++) {
    const c = bars[i].close;
    if (i < period - 1) { sum += c; continue; }
    if (i === period - 1) { sum += c; prev = sum / period; } else prev = c * m + prev * (1 - m);
    out.push({ time: bars[i].time, value: prev });
  }
  return out;
}

// ---- data loading ----
async function loadData() {
  if (MODE !== 'live') { DATA = window.HIST; return; }
  const secs = [...new Set(CONFIG.panes.map((p) => p.sec))];
  for (const sec of secs) {
    try {
      const arr = await (await fetch(`/api/klines?sec=${sec}&days=14`)).json();
      if (Array.isArray(arr) && arr.length) { DATA[sec] = arr; continue; }
    } catch (e) { /* fall through to static */ }
    if (window.HIST_LIVE && window.HIST_LIVE[sec]) DATA[sec] = window.HIST_LIVE[sec];
  }
}

// ---- build all panes (teardown + rebuild; used on first load and every TF change) ----
function buildPanes() {
  panes.forEach((p) => { try { p.chart.remove(); } catch (e) {} });
  grid.innerHTML = '';
  const n = CONFIG.panes.length;
  const cols = Math.ceil(Math.sqrt(n)), rows = Math.ceil(n / cols);
  grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
  grid.style.gridTemplateRows = `repeat(${rows}, 1fr)`;

  if (MODE !== 'live') {
    const lastAvail = Math.max(...CONFIG.panes.map((p) => { const h = DATA[p.sec]; return h[h.length - 1].time; }));
    replayStartTime = Math.floor(lastAvail / DAY) * DAY - 2 * DAY;
    replayEndTime = replayStartTime + DAY;
  }
  const h1 = $('h1'); const title = $('title-h1') || document.querySelector('header h1');
  if (title) title.textContent = MODE === 'live' ? `${SYMBOL} · LIVE` : `GOLD · ${new Date(replayStartTime * 1000).toUTCString().slice(0, 16)}`;

  panes = CONFIG.panes.map((cfg) => {
    const hist0 = DATA[cfg.sec];
    if (!hist0 || !hist0.length) { console.warn('No data for', cfg.tf); return null; }
    const cell = document.createElement('div'); cell.className = 'cell';
    const head = document.createElement('div'); head.className = 'cell-head';
    const legend = EMAS.map((e) => `<span class="li"><span class="dot" style="background:${css(e.varName)}"></span>${e.period}<b data-e="${e.period}"></b></span>`).join('');
    head.innerHTML = `<span class="tf">${cfg.tf}</span><span class="ptrend" data-trend>—</span><span class="last" data-price></span><span class="legend">${legend}</span>`;
    const chartDiv = document.createElement('div'); chartDiv.className = 'cell-chart';
    cell.append(head, chartDiv); grid.appendChild(cell);

    const chart = LightweightCharts.createChart(chartDiv, {
      layout: { background: { type: 'solid', color: css('--surface-1') }, textColor: css('--text-secondary'), fontFamily: 'system-ui, sans-serif', fontSize: n > 1 ? 10 : 12 },
      grid: { vertLines: { color: css('--gridline') }, horzLines: { color: css('--gridline') } },
      rightPriceScale: { borderColor: css('--baseline') },
      timeScale: { borderColor: css('--baseline'), timeVisible: true, secondsVisible: false },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal }, autoSize: true,
    });
    const candle = chart.addCandlestickSeries({ upColor: css('--up'), downColor: css('--down'), borderUpColor: css('--up'), borderDownColor: css('--down'), wickUpColor: css('--up'), wickDownColor: css('--down'), priceLineVisible: false });
    const emaSeries = EMAS.map((e) => chart.addLineSeries({ color: css(e.varName), lineWidth: 2, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: true, visible: CONFIG.emasOn }));

    const all = hist0;
    let backfill, replay;
    if (MODE === 'live') { backfill = all.slice(); replay = []; }
    else {
      let splitIdx = all.findIndex((b) => b.time >= replayStartTime); if (splitIdx < 20) splitIdx = Math.max(20, Math.floor(all.length / 2));
      let endIdx = all.findIndex((b) => b.time >= replayEndTime); if (endIdx < 0) endIdx = all.length;
      backfill = all.slice(0, splitIdx); replay = all.slice(splitIdx, endIdx);
    }
    candle.setData(backfill);
    const k = EMAS.map((e) => 2 / (e.period + 1)); const prevEma = [], curEma = [];
    emaSeries.forEach((s, i) => { const seeded = seedEma(backfill, EMAS[i].period); s.setData(seeded); prevEma[i] = seeded.length ? seeded[seeded.length - 1].value : backfill[backfill.length - 1].close; curEma[i] = prevEma[i]; });
    const m = backfill.length; chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, m - 80), to: m + 30 });

    const pane = {
      cfg, chart, candle, emaSeries, k, prevEma, curEma, el: chartDiv,
      forming: null, bars: backfill.slice(), replay, rIdx: 0, tIdx: 0, path: null, lastPrice: backfill[backfill.length - 1].close,
      realByTime: new Map(replay.map((b) => [b.time, b])), overlay: {},
      priceEl: head.querySelector('[data-price]'), trendEl: head.querySelector('[data-trend]'),
      emaEls: EMAS.map((e) => head.querySelector(`[data-e="${e.period}"]`)),
    };
    if (typeof initOverlays === 'function') initOverlays(pane, CONFIG.overlays, { css });
    if (typeof initIndicators === 'function') initIndicators(pane, { css });
    return pane;
  }).filter(Boolean);

  if (typeof attachTradesToPane === 'function') attachTradesToPane(panes[0], { css });
  setupCrosshairSync();
  panes.forEach(updatePaneTrend);
  updateStructBadge();
}

// Sync the crosshair across all open charts by TIME (moving the cursor on one
// draws it at the same time on the others). Guarded against feedback loops.
let xhairSyncing = false, xhairSource = null;
function setupCrosshairSync() {
  panes.forEach((p) => {
    p.chart.subscribeCrosshairMove((param) => {
      if (xhairSyncing) return;                     // ignore our own programmatic updates
      if (param.time !== undefined && param.point) {
        xhairSource = p;                            // the chart the cursor is really on
        const price = p.candle.coordinateToPrice(param.point.y);
        if (price == null) return;
        xhairSyncing = true;
        try { panes.forEach((q) => { if (q !== p) q.chart.setCrosshairPosition(price, param.time, q.candle); }); }
        finally { xhairSyncing = false; }
      } else if (xhairSource === p) {               // clear only when the ACTIVE chart is left
        xhairSource = null;
        xhairSyncing = true;
        try { panes.forEach((q) => { if (q !== p) q.chart.clearCrosshairPosition(); }); }
        finally { xhairSyncing = false; }
      }
    });
  });
}

async function rebuild() { await loadData(); buildPanes(); }

// Re-sync each pane's CLOSED bars from the server cache. Fills any gap (e.g. after
// a restart, when 30m/1h were stale) by merging in bars the pane is missing —
// only rebuilds a pane when the server actually has newer closed bars, and never
// touches the live forming bar. Reads the local /api/klines (no AllTick cost).
async function resyncBackfill() {
  if (MODE !== 'live') return;
  for (const p of panes) {
    let arr; try { arr = await (await fetch(`/api/klines?sec=${p.cfg.sec}&days=49`)).json(); } catch (e) { continue; }
    if (!Array.isArray(arr) || !arr.length) continue;
    const curBucket = p.forming ? p.forming.time : Infinity;
    const existing = p.forming ? p.bars.slice(0, -1) : p.bars.slice();
    const lastExisting = existing.length ? existing[existing.length - 1].time : 0;
    const serverClosed = arr.filter((b) => b.time < curBucket);
    const serverLast = serverClosed.length ? serverClosed[serverClosed.length - 1].time : 0;
    if (serverLast <= lastExisting) continue;                 // no new closed bars -> leave the pane alone
    const map = new Map(existing.map((b) => [b.time, b]));
    for (const b of serverClosed) map.set(b.time, b);
    const merged = [...map.values()].sort((a, b) => a.time - b.time);
    p.candle.setData(merged);
    p.emaSeries.forEach((s, i) => { const seeded = seedEma(merged, EMAS[i].period); s.setData(seeded); p.prevEma[i] = seeded.length ? seeded[seeded.length - 1].value : merged[merged.length - 1].close; });
    p.bars = merged.slice();
    if (p.forming) { p.bars.push(p.forming); p.candle.update(p.forming); }
    if (typeof onBarClose === 'function') onBarClose(p, { css });
    if (typeof onIndicatorBarClose === 'function') onIndicatorBarClose(p, { css });
    updatePaneTrend(p);
  }
  updateStructBadge();
}

// ---- replay engine ----
function buildPath(rb, n) {
  const w = rb.close >= rb.open ? [rb.open, rb.low, rb.high, rb.close] : [rb.open, rb.high, rb.low, rb.close];
  const path = [];
  for (let s = 0; s < 3; s++) { const a = w[s], b = w[s + 1]; const cnt = s === 2 ? Math.max(1, n - path.length) : Math.max(1, Math.round(n / 3)); for (let t = 0; t < cnt; t++) path.push(a + (b - a) * ((t + 1) / cnt)); }
  return path;
}
function startBar(p) { const rb = p.replay[p.rIdx]; p.forming = { time: rb.time, open: rb.open, high: rb.open, low: rb.open, close: rb.open }; p.bars.push(p.forming); p.path = buildPath(rb, TICKS_PER_BAR); p.tIdx = 0; }
function finalizeBar(p) {
  const rb = p.replay[p.rIdx]; Object.assign(p.forming, { open: rb.open, high: rb.high, low: rb.low, close: rb.close }); p.candle.update(p.forming);
  for (let i = 0; i < EMAS.length; i++) { p.curEma[i] = rb.close * p.k[i] + p.prevEma[i] * (1 - p.k[i]); p.emaSeries[i].update({ time: rb.time, value: p.curEma[i] }); p.prevEma[i] = p.curEma[i]; }
  p.rIdx++; p.forming = null;
  if (typeof onBarClose === 'function') onBarClose(p, { css }); if (typeof onIndicatorBarClose === 'function') onIndicatorBarClose(p, { css }); updatePaneTrend(p); if (p === panes[0]) updateStructBadge();
}
function stepPane(p) {
  if (p.rIdx >= p.replay.length) return false;
  if (p.forming === null) startBar(p);
  const price = p.path[Math.min(p.tIdx, p.path.length - 1)]; const f = p.forming;
  f.close = price; if (price > f.high) f.high = price; if (price < f.low) f.low = price; p.candle.update(f);
  for (let i = 0; i < EMAS.length; i++) { p.curEma[i] = price * p.k[i] + p.prevEma[i] * (1 - p.k[i]); p.emaSeries[i].update({ time: f.time, value: p.curEma[i] }); }
  p.priceEl.textContent = nf(price); for (let i = 0; i < EMAS.length; i++) p.emaEls[i].textContent = nf(p.curEma[i]); p.lastPrice = price;
  if (++p.tIdx >= TICKS_PER_BAR) finalizeBar(p);
  return true;
}
// shared-price aggregation (coarse panes in replay; ALL panes in live)
function aggregatePane(p, price, driverTime) {
  let bucket = Math.floor(driverTime / p.cfg.sec) * p.cfg.sec;
  const lastT = p.forming ? p.forming.time : (p.bars.length ? p.bars[p.bars.length - 1].time : bucket);
  if (bucket < lastT) bucket = lastT;
  if (!p.forming || p.forming.time !== bucket) {
    if (p.forming) finalizeAgg(p);
    const lastBar = p.bars[p.bars.length - 1];
    if (lastBar && lastBar.time === bucket) { p.forming = lastBar; }
    else {
      if (lastBar && bucket > lastBar.time + p.cfg.sec) { let t = lastBar.time + p.cfg.sec, n = 0; const c = lastBar.close; while (t < bucket && n < 600) { const fb = { time: t, open: c, high: c, low: c, close: c }; p.bars.push(fb); p.candle.update(fb); t += p.cfg.sec; n++; } }
      p.forming = { time: bucket, open: price, high: price, low: price, close: price }; p.bars.push(p.forming);
    }
  }
  const f = p.forming; f.close = price; if (price > f.high) f.high = price; if (price < f.low) f.low = price; p.candle.update(f);
  for (let i = 0; i < EMAS.length; i++) { p.curEma[i] = price * p.k[i] + p.prevEma[i] * (1 - p.k[i]); p.emaSeries[i].update({ time: f.time, value: p.curEma[i] }); }
  p.priceEl.textContent = nf(price); for (let i = 0; i < EMAS.length; i++) p.emaEls[i].textContent = nf(p.curEma[i]); p.lastPrice = price;
}
function finalizeAgg(p) {
  if (!p.forming) return; const real = p.realByTime.get(p.forming.time);
  if (real) Object.assign(p.forming, { open: real.open, high: real.high, low: real.low, close: real.close }); p.candle.update(p.forming);
  const c = real ? real.close : p.forming.close;
  for (let i = 0; i < EMAS.length; i++) { p.curEma[i] = c * p.k[i] + p.prevEma[i] * (1 - p.k[i]); p.emaSeries[i].update({ time: p.forming.time, value: p.curEma[i] }); p.prevEma[i] = p.curEma[i]; }
  p.forming = null; if (typeof onBarClose === 'function') onBarClose(p, { css }); updatePaneTrend(p);
}
function tick() {
  const drv = panes[0];
  if (drv.rIdx >= drv.replay.length) { for (let i = 1; i < panes.length; i++) finalizeAgg(panes[i]); return stop(true); }
  stepPane(drv);
  const price = drv.lastPrice, dtime = drv.forming ? drv.forming.time : drv.bars[drv.bars.length - 1].time;
  for (let i = 1; i < panes.length; i++) aggregatePane(panes[i], price, dtime);
  if (typeof updateTrades === 'function' && drv.forming) updateTrades(price, drv.forming.time);
  tickCount++; renderStatus();
}

// ---- live feed ----
function feedTick(price, timeSec) {
  if (!running) return;
  for (const p of panes) aggregatePane(p, price, timeSec);
  if (typeof updateTrades === 'function') updateTrades(price, timeSec);
  updateStructBadge(); tickCount++; renderStatus();
}
function onFeedStatus(text, cls) { const el = $('status'); el.textContent = text; el.className = 'badge ' + (cls || 'live'); }

// ---- controls ----
function start() {
  if (running) return; running = true; $('playpause').textContent = 'Pause';
  if (MODE === 'live') {
    $('status').textContent = 'LIVE'; $('status').className = 'badge live';
    if (!feedStarted && typeof startLiveFeed === 'function') { feedStarted = true; feedHandle = startLiveFeed(SYMBOL, feedTick, onFeedStatus); }
  } else { timer = setInterval(tick, tickMs); $('status').textContent = 'REPLAY'; $('status').className = 'badge live'; }
}
function pause() { running = false; if (MODE !== 'live') clearInterval(timer); $('playpause').textContent = 'Resume'; $('status').textContent = 'PAUSED'; $('status').className = 'badge paused'; }
function stop(done) { running = false; clearInterval(timer); $('playpause').textContent = 'Resume'; if (done) { $('status').textContent = 'DONE'; $('status').className = 'badge done'; } }
function setSpeed(name) { tickMs = SPEEDS[name] / TICKS_PER_BAR; document.querySelectorAll('.speed').forEach((b) => b.classList.toggle('active', b.dataset.s === name)); if (running && MODE !== 'live') { clearInterval(timer); timer = setInterval(tick, tickMs); } }
function updatePaneTrend(p) { const el = p && p.trendEl; if (!el) return; const t = p.overlay && p.overlay.trend; const m = { up: ['BULLISH', 'var(--up)'], down: ['BEARISH', 'var(--down)'] }; const [txt, col] = m[t] || ['RANGE', 'var(--muted)']; el.textContent = txt; el.style.color = col; }
function updateStructBadge() {
  const el = $('structTrend'); if (!el || !panes.length) return; const t = panes[0].overlay && panes[0].overlay.trend;
  const map = { up: ['STRUCTURE: BULLISH', 'badge bull'], down: ['STRUCTURE: BEARISH', 'badge bear'] }; const [text, cls] = map[t] || ['STRUCTURE: RANGE', 'badge rng'];
  el.textContent = text; el.className = cls;
  if (t !== lastStructTrend && lastStructTrend !== null) { el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 500); } lastStructTrend = t;
}
function renderStatus() {
  if (!panes.length) return; const p = panes[0]; $('ticks').textContent = tickCount;
  if (MODE === 'live') $('elapsed').textContent = p.forming ? new Date(p.forming.time * 1000).toUTCString().slice(17, 25) : '—';
  else $('elapsed').textContent = `${p.rIdx}/${p.replay.length}`;
  $('remaining').textContent = nf(p.forming ? p.forming.close : (p.bars.length ? p.bars[p.bars.length - 1].close : 0));
}

// ---- header chips / buttons ----
function chip(label, on, dotColor, onToggle) {
  const el = document.createElement('span'); el.className = 'chip' + (on ? ' on' : '');
  el.innerHTML = (dotColor ? `<span class="dot" style="background:${dotColor}"></span>` : '') + label;
  el.addEventListener('click', () => { el.classList.toggle('on'); onToggle(el.classList.contains('on')); });
  return el;
}
function refreshOverlays() { if (typeof redrawOverlays === 'function') panes.forEach((p) => redrawOverlays(p, { css })); }
function refreshIndicators() { if (typeof redrawIndicators === 'function') panes.forEach((p) => redrawIndicators(p, { css })); }

const tfChips = {};
function updateTfChips() { Object.keys(tfChips).forEach((t) => tfChips[t].classList.toggle('on', CONFIG.panes.some((p) => p.tf === t))); }

function buildHeader() {
  $('playpause').addEventListener('click', () => (running ? pause() : start()));
  $('reset').addEventListener('click', () => location.reload());
  document.querySelectorAll('.speed').forEach((b) => b.addEventListener('click', () => setSpeed(b.dataset.s)));
  const mb = $('btnMode'); mb.textContent = MODE === 'live' ? 'Live: BTC' : 'Replay: Gold';
  mb.addEventListener('click', () => { const u = new URL(location.href); u.searchParams.set('mode', MODE === 'live' ? 'replay' : 'live'); location.href = u.toString(); });

  const toggles = $('toggles');
  const tfLbl = document.createElement('span'); tfLbl.className = 'grp-label'; tfLbl.textContent = 'TF'; toggles.appendChild(tfLbl);
  Object.keys(TF_MAP).forEach((t) => {
    const b = document.createElement('span'); b.className = 'chip' + (CONFIG.panes.some((p) => p.tf === t) ? ' on' : ''); b.textContent = t; tfChips[t] = b;
    b.addEventListener('click', async () => {
      let sel = CONFIG.panes.map((p) => p.tf); sel = sel.includes(t) ? sel.filter((x) => x !== t) : [...sel, t];
      if (!sel.length) sel = [t]; sel.sort((a, c) => TF_MAP[a] - TF_MAP[c]);
      CONFIG.panes = sel.map((x) => ({ tf: x, sec: TF_MAP[x] }));
      const u = new URL(location.href); u.searchParams.set('tf', sel.join(',')); history.replaceState(null, '', u); // no reload -> WS stays up
      updateTfChips();
      await rebuild();                       // rebuild charts in place
    });
    toggles.appendChild(b);
  });
  toggles.appendChild(Object.assign(document.createElement('span'), { className: 'sep' }));
  const emaLbl = document.createElement('span'); emaLbl.className = 'grp-label'; emaLbl.textContent = 'EMA'; toggles.appendChild(emaLbl);
  EMAS.forEach((e, i) => toggles.appendChild(chip(String(e.period), CONFIG.emasOn, css(e.varName), (on) => { panes.forEach((p) => p.emaSeries[i].applyOptions({ visible: on })); })));
  toggles.appendChild(Object.assign(document.createElement('span'), { className: 'sep' }));
  const ovLbl = document.createElement('span'); ovLbl.className = 'grp-label'; ovLbl.textContent = 'Overlays'; toggles.appendChild(ovLbl);

  // Unified control list so a master toggle can flip everything at once.
  const ctrls = [
    { label: 'Market Structure', color: css('--struct'), def: CONFIG.overlays.marketStructure, get: () => CONFIG.overlays.marketStructure, set: (v) => { CONFIG.overlays.marketStructure = v; }, refresh: refreshOverlays },
    { label: 'Key Levels', color: css('--level-res'), def: CONFIG.overlays.keyLevels, get: () => CONFIG.overlays.keyLevels, set: (v) => { CONFIG.overlays.keyLevels = v; }, refresh: refreshOverlays },
  ];
  if (typeof INDI !== 'undefined') ctrls.push(
    { label: 'FVG', def: INDI.fvg, get: () => INDI.fvg, set: (v) => { INDI.fvg = v; }, refresh: refreshIndicators, smc: true },
    { label: 'Order Blocks', def: INDI.orderblocks, get: () => INDI.orderblocks, set: (v) => { INDI.orderblocks = v; }, refresh: refreshIndicators, smc: true },
    { label: 'Patterns', def: INDI.patterns, get: () => INDI.patterns, set: (v) => { INDI.patterns = v; }, refresh: refreshIndicators, smc: true },
    { label: 'Liquidity', def: INDI.liquidity, get: () => INDI.liquidity, set: (v) => { INDI.liquidity = v; }, refresh: refreshIndicators, smc: true },
    { label: 'Prem/Disc', def: INDI.premdisc, get: () => INDI.premdisc, set: (v) => { INDI.premdisc = v; }, refresh: refreshIndicators, smc: true },
  );

  // master "Hide All / Show All"
  const master = document.createElement('span'); master.className = 'chip on'; master.textContent = 'Hide All'; toggles.appendChild(master);
  let masterOn = true;
  function setAll(v) {
    masterOn = v;
    ctrls.forEach((c) => { const t = v ? c.def : false; c.set(t); if (c.el) c.el.classList.toggle('on', t); });
    refreshOverlays(); refreshIndicators();
    master.textContent = v ? 'Hide All' : 'Show All'; master.classList.toggle('on', v);
  }
  master.addEventListener('click', () => setAll(!masterOn));

  let smcLabelled = false;
  ctrls.forEach((c) => {
    if (c.smc && !smcLabelled) { smcLabelled = true; toggles.appendChild(Object.assign(document.createElement('span'), { className: 'sep' })); const l = document.createElement('span'); l.className = 'grp-label'; l.textContent = 'SMC'; toggles.appendChild(l); }
    c.el = chip(c.label, c.get(), c.color, (on) => { c.set(on); c.refresh(); });
    toggles.appendChild(c.el);
  });
}

// close the WS cleanly on navigation so AllTick frees the connection immediately
window.addEventListener('pagehide', () => { if (feedHandle) try { feedHandle.stop(); } catch (e) {} });

// ---- boot ----
(async function boot() {
  if (typeof initTradesUI === 'function') initTradesUI({ css });
  buildHeader();
  await rebuild();
  setSpeed('Fast');
  start();
  if (MODE === 'live') { [8000, 25000, 55000, 95000].forEach((ms) => setTimeout(resyncBackfill, ms)); setInterval(resyncBackfill, 60000); }
})();
