// 2x2 multi-timeframe view: 1m / 5m / 15m / 30m, one shared tick feed.
// Each pane buckets the SAME tick stream into its own interval via
// floor(virtualTime / interval) * interval — the real-feed model. One price,
// four candle aggregations, four EMA sets, all updated per tick via update().
//
// Real feed swap: replace nextPrice() with prices from your MT5/ZeroMQ bridge
// and feed real tick timestamps into virtualTime. Everything else is unchanged.

const TFS = [
  { label: '1m', sec: 60 },
  { label: '5m', sec: 300 },
  { label: '15m', sec: 900 },
  { label: '30m', sec: 1800 },
];
const EMAS = [
  { period: 5, varName: '--ema-5' },
  { period: 9, varName: '--ema-9' },
  { period: 21, varName: '--ema-21' },
  { period: 50, varName: '--ema-50' },
  { period: 200, varName: '--ema-200' },
];

const STEP_SEC = 2;               // virtual market seconds advanced per tick
const RUN_MS = 10 * 60 * 1000;    // auto-stop after 10 wall-clock minutes
const MINUTE_SIGMA = 0.0003;
const TICK_SIGMA = MINUTE_SIGMA / Math.sqrt(60 / STEP_SEC);
const SPEEDS = { Slow: 100, Normal: 50, Fast: 25, Turbo: 10 };
let tickMs = SPEEDS.Fast;

const rootStyle = getComputedStyle(document.querySelector('.viz-root'));
const css = (n) => rootStyle.getPropertyValue(n).trim();
const nf = (v) => v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function seedEma(bars, period) {
  const m = 2 / (period + 1);
  const out = [];
  let prev, sum = 0;
  for (let i = 0; i < bars.length; i++) {
    const c = bars[i].close;
    if (i < period - 1) { sum += c; continue; }
    if (i === period - 1) { sum += c; prev = sum / period; }
    else prev = c * m + prev * (1 - m);
    out.push({ time: bars[i].time, value: prev });
  }
  return out;
}

// ---- build DOM cells + charts per timeframe ----
const grid = document.getElementById('grid');
const panes = TFS.map((tf) => {
  const cell = document.createElement('div');
  cell.className = 'cell';
  const head = document.createElement('div');
  head.className = 'cell-head';
  const legend = EMAS.map((e) =>
    `<span class="li"><span class="dot" style="background:${css(e.varName)}"></span>${e.period}<b data-e="${e.period}"></b></span>`
  ).join('');
  head.innerHTML = `<span class="tf">${tf.label}</span><span class="last" data-price></span><span class="legend">${legend}</span>`;
  const chartDiv = document.createElement('div');
  chartDiv.className = 'cell-chart';
  cell.append(head, chartDiv);
  grid.appendChild(cell);

  const chart = LightweightCharts.createChart(chartDiv, {
    layout: { background: { type: 'solid', color: css('--surface-1') }, textColor: css('--text-secondary'), fontFamily: 'system-ui, sans-serif', fontSize: 10 },
    grid: { vertLines: { color: css('--gridline') }, horzLines: { color: css('--gridline') } },
    rightPriceScale: { borderColor: css('--baseline') },
    timeScale: { borderColor: css('--baseline'), timeVisible: true, secondsVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    autoSize: true,
  });
  const candle = chart.addCandlestickSeries({
    upColor: css('--up'), downColor: css('--down'), borderUpColor: css('--up'),
    borderDownColor: css('--down'), wickUpColor: css('--up'), wickDownColor: css('--down'), priceLineVisible: false,
  });
  const emaSeries = EMAS.map((e) => chart.addLineSeries({ color: css(e.varName), lineWidth: 2, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: true }));

  // seed from real history: last bar becomes the forming bar
  const hist = window.HIST[tf.sec].slice();
  const forming = { ...hist.pop() };
  candle.setData(hist);
  candle.update(forming);
  const k = EMAS.map((e) => 2 / (e.period + 1));
  const prevEma = [], curEma = [];
  emaSeries.forEach((s, i) => {
    const seeded = seedEma(hist, EMAS[i].period);
    s.setData(seeded);
    prevEma[i] = seeded[seeded.length - 1].value;
    curEma[i] = forming.close * k[i] + prevEma[i] * (1 - k[i]);
    s.update({ time: forming.time, value: curEma[i] });
  });
  const n = hist.length;
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 90), to: n + 6 });

  return {
    tf, chart, candle, emaSeries, k, prevEma, curEma, forming,
    priceEl: head.querySelector('[data-price]'),
    emaEls: EMAS.map((e) => head.querySelector(`[data-e="${e.period}"]`)),
  };
});

// ---- shared feed state ----
let price = panes[0].forming.close;
let virtualTime = panes[0].forming.time; // 1m last-bar time = max across TFs
let timer = null, startWall = null, running = false, tickCount = 0;

function randNormal() {
  let u = 0, v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}
function nextPrice() { return price * Math.exp(randNormal() * TICK_SIGMA); }

function tick() {
  if (startWall !== null && performance.now() - startWall >= RUN_MS) return stop(true);
  virtualTime += STEP_SEC;
  price = nextPrice();

  for (const p of panes) {
    const bucket = Math.floor(virtualTime / p.tf.sec) * p.tf.sec;
    if (bucket > p.forming.time) {                 // interval rolled over
      for (let i = 0; i < EMAS.length; i++) p.prevEma[i] = p.curEma[i]; // finalize
      p.forming = { time: bucket, open: price, high: price, low: price, close: price };
      p.chart.timeScale().scrollToRealTime();
    }
    const f = p.forming;
    f.close = price;
    if (price > f.high) f.high = price;
    if (price < f.low) f.low = price;
    p.candle.update(f);
    for (let i = 0; i < EMAS.length; i++) {
      p.curEma[i] = price * p.k[i] + p.prevEma[i] * (1 - p.k[i]);
      p.emaSeries[i].update({ time: f.time, value: p.curEma[i] });
    }
    p.priceEl.textContent = nf(price);
    for (let i = 0; i < EMAS.length; i++) p.emaEls[i].textContent = nf(p.curEma[i]);
  }

  tickCount++;
  renderStatus();
}

// ---- controls ----
const $ = (id) => document.getElementById(id);
function start() {
  if (running) return;
  running = true;
  if (startWall === null) startWall = performance.now();
  timer = setInterval(tick, tickMs);
  $('playpause').textContent = 'Pause';
  $('status').textContent = 'LIVE'; $('status').className = 'badge live';
}
function pause() {
  running = false; clearInterval(timer);
  $('playpause').textContent = 'Resume';
  $('status').textContent = 'PAUSED'; $('status').className = 'badge paused';
}
function stop(done) {
  running = false; clearInterval(timer);
  $('playpause').textContent = 'Resume';
  if (done) { $('status').textContent = 'COMPLETE'; $('status').className = 'badge done'; }
}
function setSpeed(name) {
  tickMs = SPEEDS[name];
  document.querySelectorAll('.speed').forEach((b) => b.classList.toggle('active', b.dataset.s === name));
  if (running) { clearInterval(timer); timer = setInterval(tick, tickMs); }
}
function fmtClock(ms) {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}
function renderStatus() {
  $('ticks').textContent = tickCount;
  if (startWall !== null) {
    const el = Math.min(RUN_MS, performance.now() - startWall);
    $('elapsed').textContent = fmtClock(el);
    $('remaining').textContent = fmtClock(Math.max(0, RUN_MS - el));
  }
}

$('playpause').addEventListener('click', () => (running ? pause() : start()));
$('reset').addEventListener('click', () => location.reload());
document.querySelectorAll('.speed').forEach((b) => b.addEventListener('click', () => setSpeed(b.dataset.s)));

setSpeed('Fast');
start();
