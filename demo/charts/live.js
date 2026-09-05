// Live 1-minute chart with a simulated tick-by-tick feed.
// - Backfill: real Binance 1m bars (window.OHLC_1M) seed all EMAs (incl. 200).
// - Live: a tick generator random-walks from the last real close. Each tick
//   updates the FORMING bar and recomputes every EMA incrementally, then the
//   chart is updated via series.update() (the fast, real-time path).
//
// Swap `nextTickPrice()` for a real feed (MT5 EA -> ZeroMQ/WebSocket) and the
// rest of this file is unchanged: that is the whole extension point.

const BAR_SEC = 60;                 // 1-minute bars (chart time step)
const TICKS_PER_BAR = 30;           // simulated ticks that make up one bar
const RUN_MS = 10 * 60 * 1000;      // auto-stop after 10 wall-clock minutes
const MINUTE_SIGMA = 0.0003;        // per-minute log-vol (derived from real 1m data, nudged for visibility)
const TICK_SIGMA = MINUTE_SIGMA / Math.sqrt(TICKS_PER_BAR);

// wall-clock ms between ticks per speed preset -> bar duration = ms * TICKS_PER_BAR
const SPEEDS = { Slow: 100, Normal: 50, Fast: 25, Turbo: 10 };
let tickMs = SPEEDS.Fast;

const EMAS = [
  { period: 5, varName: '--ema-5' },
  { period: 9, varName: '--ema-9' },
  { period: 21, varName: '--ema-21' },
  { period: 50, varName: '--ema-50' },
  { period: 200, varName: '--ema-200' },
];

const rootStyle = getComputedStyle(document.querySelector('.viz-root'));
const css = (n) => rootStyle.getPropertyValue(n).trim();

const chart = LightweightCharts.createChart(document.getElementById('chart'), {
  layout: {
    background: { type: 'solid', color: css('--surface-1') },
    textColor: css('--text-secondary'),
    fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif',
  },
  grid: { vertLines: { color: css('--gridline') }, horzLines: { color: css('--gridline') } },
  rightPriceScale: { borderColor: css('--baseline') },
  timeScale: { borderColor: css('--baseline'), timeVisible: true, secondsVisible: false },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  autoSize: true,
});

const candles = chart.addCandlestickSeries({
  upColor: css('--up'), downColor: css('--down'),
  borderUpColor: css('--up'), borderDownColor: css('--down'),
  wickUpColor: css('--up'), wickDownColor: css('--down'),
  priceLineVisible: false,
});

const emaSeries = EMAS.map(({ varName }) =>
  chart.addLineSeries({
    color: css(varName), lineWidth: 2,
    priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: true,
  })
);

// ---- EMA state ----
const k = EMAS.map((e) => 2 / (e.period + 1));
let prevEma = [];   // EMA of the last CLOSED bar, per period
let curEma = [];    // EMA including the forming bar, per period

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

// ---- state ----
const bars = window.OHLC_1M.slice();
let forming = null;
let ticksThisBar = 0;
let timer = null;
let startWall = null;
let running = false;
let tickCount = 0;

function randNormal() {
  let u = 0, v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function nextTickPrice(last) {
  return last * Math.exp(randNormal() * TICK_SIGMA);
}

function init() {
  candles.setData(bars);
  emaSeries.forEach((s, i) => {
    const seeded = seedEma(bars, EMAS[i].period);
    s.setData(seeded);
    prevEma[i] = seeded[seeded.length - 1].value;
    curEma[i] = prevEma[i];
  });
  const last = bars[bars.length - 1];
  forming = { time: last.time + BAR_SEC, open: last.close, high: last.close, low: last.close, close: last.close };
  ticksThisBar = 0;
  const n = bars.length;
  chart.timeScale().setVisibleLogicalRange({ from: n - 120, to: n + 8 });
  renderReadouts(last.close);
}

function tick() {
  if (startWall !== null && performance.now() - startWall >= RUN_MS) return stop(true);

  const price = nextTickPrice(forming.close);
  forming.close = price;
  if (price > forming.high) forming.high = price;
  if (price < forming.low) forming.low = price;
  candles.update(forming);

  for (let i = 0; i < EMAS.length; i++) {
    curEma[i] = price * k[i] + prevEma[i] * (1 - k[i]);
    emaSeries[i].update({ time: forming.time, value: curEma[i] });
  }

  tickCount++;
  ticksThisBar++;
  renderReadouts(price);

  if (ticksThisBar >= TICKS_PER_BAR) {
    bars.push(forming);
    for (let i = 0; i < EMAS.length; i++) prevEma[i] = curEma[i];
    forming = { time: forming.time + BAR_SEC, open: price, high: price, low: price, close: price };
    ticksThisBar = 0;
    chart.timeScale().scrollToRealTime();
  }
}

// ---- controls & readouts ----
const $ = (id) => document.getElementById(id);

function start() {
  if (running) return;
  running = true;
  if (startWall === null) startWall = performance.now();
  timer = setInterval(tick, tickMs);
  $('playpause').textContent = 'Pause';
  $('status').textContent = 'LIVE';
  $('status').className = 'badge live';
}

function pause() {
  running = false;
  clearInterval(timer);
  $('playpause').textContent = 'Resume';
  $('status').textContent = 'PAUSED';
  $('status').className = 'badge paused';
}

function stop(done) {
  running = false;
  clearInterval(timer);
  $('playpause').textContent = 'Resume';
  if (done) { $('status').textContent = 'COMPLETE'; $('status').className = 'badge done'; }
}

function setSpeed(name) {
  tickMs = SPEEDS[name];
  document.querySelectorAll('.speed').forEach((b) => b.classList.toggle('active', b.dataset.s === name));
  if (running) { clearInterval(timer); timer = setInterval(tick, tickMs); }
}

const nf = (v, d = 2) => v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

function renderReadouts(price) {
  $('price').textContent = nf(price);
  EMAS.forEach((e, i) => { $('ema' + e.period).textContent = nf(curEma[i]); });
  $('bars').textContent = bars.length - window.OHLC_1M.length;
  $('ticks').textContent = tickCount;
  if (startWall !== null) {
    const el = Math.min(RUN_MS, performance.now() - startWall);
    const rem = Math.max(0, RUN_MS - el);
    $('elapsed').textContent = fmtClock(el);
    $('remaining').textContent = fmtClock(rem);
  }
}

function fmtClock(ms) {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

$('playpause').addEventListener('click', () => (running ? pause() : start()));
$('reset').addEventListener('click', () => location.reload());
document.querySelectorAll('.speed').forEach((b) => b.addEventListener('click', () => setSpeed(b.dataset.s)));

init();
setSpeed('Fast');
start();
