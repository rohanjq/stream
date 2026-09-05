// EMA overlay on 5m candles using TradingView Lightweight Charts.
// Data source: window.OHLC (see data.js), array of {time, open, high, low, close}.

// Standard EMA: seed with SMA of the first `period` closes, then recurse.
// EMA_t = close_t * k + EMA_{t-1} * (1 - k),  k = 2 / (period + 1).
// Values before the seed bar are omitted (a 200-EMA has no value until bar 200).
function ema(bars, period) {
  const k = 2 / (period + 1);
  const out = [];
  let prev;
  let sum = 0;
  for (let i = 0; i < bars.length; i++) {
    const close = bars[i].close;
    if (i < period - 1) {
      sum += close;
      continue;
    }
    if (i === period - 1) {
      sum += close;
      prev = sum / period; // SMA seed
    } else {
      prev = close * k + prev * (1 - k);
    }
    out.push({ time: bars[i].time, value: prev });
  }
  return out;
}

const EMAS = [
  { period: 5, varName: '--ema-5' },
  { period: 9, varName: '--ema-9' },
  { period: 21, varName: '--ema-21' },
  { period: 50, varName: '--ema-50' },
  { period: 200, varName: '--ema-200' },
];

const bars = window.OHLC;
const rootStyle = getComputedStyle(document.querySelector('.viz-root'));
const css = (name) => rootStyle.getPropertyValue(name).trim();

const container = document.getElementById('chart');

const chart = LightweightCharts.createChart(container, {
  layout: {
    background: { type: 'solid', color: css('--surface-1') },
    textColor: css('--text-secondary'),
    fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif',
  },
  grid: {
    vertLines: { color: css('--gridline') },
    horzLines: { color: css('--gridline') },
  },
  rightPriceScale: { borderColor: css('--baseline') },
  timeScale: { borderColor: css('--baseline'), timeVisible: true, secondsVisible: false },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  autoSize: true,
});

const candles = chart.addCandlestickSeries({
  upColor: css('--up'),
  downColor: css('--down'),
  borderUpColor: css('--up'),
  borderDownColor: css('--down'),
  wickUpColor: css('--up'),
  wickDownColor: css('--down'),
  priceLineVisible: false,
});
candles.setData(bars);

const emaSeries = EMAS.map(({ period, varName }) => {
  const s = chart.addLineSeries({
    color: css(varName),
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: true,
  });
  s.setData(ema(bars, period));
  return { period, series: s, color: css(varName) };
});

chart.timeScale().fitContent();

// Legend with live values on crosshair move.
const legend = document.getElementById('legend');
const swatches = EMAS.map(({ period }, i) => {
  const row = document.createElement('span');
  row.className = 'legend-item';
  row.innerHTML =
    `<span class="dot" style="background:${emaSeries[i].color}"></span>` +
    `EMA ${period}<span class="val" data-p="${period}"></span>`;
  legend.appendChild(row);
  return row.querySelector('.val');
});

const fmt = (v) => (v == null ? '' : ' ' + v.toLocaleString(undefined, { maximumFractionDigits: 2 }));

function renderLegend(param) {
  emaSeries.forEach((e, i) => {
    const v = param && param.seriesData ? param.seriesData.get(e.series) : undefined;
    swatches[i].textContent = fmt(v ? v.value : lastValue(e.series));
  });
}

function lastValue(series) {
  const d = series.data();
  return d.length ? d[d.length - 1].value : null;
}

chart.subscribeCrosshairMove(renderLegend);
renderLegend(null); // seed with latest values
