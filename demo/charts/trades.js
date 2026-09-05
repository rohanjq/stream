// TradingView-style long/short position tool (simulated).
// - Arm Long/Short, click the chart to drop the tool at that entry price.
// - Each order draws as ONE canvas primitive: green (target) & red (stop) boxes
//   extending right, entry/TP/SL lines, resize handles, and price/%/amount labels.
// - Drag any of the three lines (or its right-edge handle) to resize live.
// - It's a LIMIT: pending until price reaches entry, then fills; closes at TP/SL.
//   All lifecycle events + realized P&L in the panel.

const TRADE = {
  armed: null, orders: [], events: [], pane: null, css: null,
  seq: 1, lastPrice: null, realizedPnl: 0, drag: null, hoverField: null,
};

const tnf = (v, d = 2) => (v == null || isNaN(v) ? '—' : v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const tget = (id) => document.getElementById(id);

// Wire the panel + header buttons once (survives in-place TF rebuilds).
function initTradesUI(ctx) {
  TRADE.css = ctx.css;
  buildPanel();
  tget('btnLong').addEventListener('click', () => armTrade('long'));
  tget('btnShort').addEventListener('click', () => armTrade('short'));
  tget('btnClearTrades').addEventListener('click', clearTrades);
}
// (Re)bind the tool to the current primary chart. Called on every rebuild — the
// old chart's series/primitives are gone, so active orders are cleared, but the
// event log and realized P&L persist across timeframe switches.
function attachTradesToPane(pane, ctx) {
  if (ctx && ctx.css) TRADE.css = ctx.css;
  TRADE.pane = pane; TRADE.orders = []; TRADE.drag = null;
  pane.chart.subscribeClick((param) => {
    if (!TRADE.armed || !param.point) return;
    const entry = pane.candle.coordinateToPrice(param.point.y);
    if (entry == null) return;
    placeOrder(TRADE.armed, entry);
    TRADE.armed = null; updateArmUI();
  });
  attachDrag(pane);
  render();
}

function armTrade(side) { TRADE.armed = TRADE.armed === side ? null : side; updateArmUI(); }
function updateArmUI() {
  tget('btnLong').classList.toggle('armed', TRADE.armed === 'long');
  tget('btnShort').classList.toggle('armed', TRADE.armed === 'short');
  TRADE.pane.el.style.cursor = TRADE.armed ? 'crosshair' : '';
}

function placeOrder(side, entry) {
  const riskIn = parseFloat(tget('tRisk').value);
  const risk = riskIn > 0 ? riskIn : entry * 0.001;
  const rr = parseFloat(tget('tRR').value) > 0 ? parseFloat(tget('tRR').value) : 2;
  const qty = parseFloat(tget('tQty').value) > 0 ? parseFloat(tget('tQty').value) : 1;
  const o = {
    id: TRADE.seq++, side, entry, qty, status: 'pending',
    sl: side === 'long' ? entry - risk : entry + risk,
    tp: side === 'long' ? entry + risk * rr : entry - risk * rr,
    fromTime: currentBarTime(), prim: null,
  };
  o.prim = positionPrimitive(o);
  TRADE.pane.candle.attachPrimitive(o.prim);
  TRADE.orders.push(o);
  logEvent(`#${o.id} ${side.toUpperCase()} limit @ ${tnf(entry)}  SL ${tnf(o.sl)} · TP ${tnf(o.tp)}`);
  render();
}

function removeOrder(o) { if (o.prim) TRADE.pane.candle.detachPrimitive(o.prim); o.prim = null; }
function currentBarTime() { const p = TRADE.pane; return p.forming ? p.forming.time : (p.bars.length ? p.bars[p.bars.length - 1].time : 0); }

function updateTrades(price, time) {
  for (const o of TRADE.orders) checkOrder(o, price, time);
  TRADE.lastPrice = price;
  if (TRADE.orders.some((o) => o.status === 'open')) render(); // live P&L
}

function checkOrder(o, price, time) {
  if (o.status === 'pending') {
    const crossed = TRADE.lastPrice != null && (TRADE.lastPrice - o.entry) * (price - o.entry) <= 0;
    if (crossed) { o.status = 'open'; o.entryTime = time; logEvent(`#${o.id} ${o.side.toUpperCase()} FILLED @ ${tnf(o.entry)}`); render(); }
  } else if (o.status === 'open') {
    let hit = null, exit = null;
    if (o.side === 'long') { if (price >= o.tp) { hit = 'TP'; exit = o.tp; } else if (price <= o.sl) { hit = 'SL'; exit = o.sl; } }
    else { if (price <= o.tp) { hit = 'TP'; exit = o.tp; } else if (price >= o.sl) { hit = 'SL'; exit = o.sl; } }
    if (hit) {
      o.status = 'closed'; o.exitPrice = exit; o.result = hit;
      o.pnl = (exit - o.entry) * (o.side === 'long' ? 1 : -1) * o.qty;
      TRADE.realizedPnl += o.pnl;
      logEvent(`#${o.id} ${o.side.toUpperCase()} ${hit} @ ${tnf(exit)}  P&L ${o.pnl >= 0 ? '+' : ''}${tnf(o.pnl)}`);
      removeOrder(o); render();
    }
  }
}

function clearTrades() { TRADE.orders.forEach(removeOrder); TRADE.orders = []; TRADE.events = []; TRADE.realizedPnl = 0; render(); }
function logEvent(msg) { TRADE.events.unshift(msg); if (TRADE.events.length > 40) TRADE.events.pop(); }

// ---------- drawing primitive ----------
function roundRect(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }

function positionPrimitive(o) {
  let series = null, chart = null, requestUpdate = null;
  const view = {
    zOrder: () => 'top',
    renderer: () => ({
      draw: (target) => target.useMediaCoordinateSpace((scope) => {
        if (!series || !chart || o.status === 'closed') return;
        const ctx = scope.context, W = scope.mediaSize.width;
        const yE = series.priceToCoordinate(o.entry), yT = series.priceToCoordinate(o.tp), yS = series.priceToCoordinate(o.sl);
        if (yE == null || yT == null || yS == null) return;
        let xL = chart.timeScale().timeToCoordinate(o.fromTime); if (xL == null) xL = 0; xL = Math.max(0, xL);
        const xR = W - 1;
        const GREEN = 'rgba(12,163,12,'; const RED = 'rgba(208,59,59,';
        // boxes
        ctx.fillStyle = GREEN + (o.status === 'pending' ? '0.10)' : '0.16)'); ctx.fillRect(xL, Math.min(yE, yT), xR - xL, Math.abs(yT - yE));
        ctx.fillStyle = RED + (o.status === 'pending' ? '0.10)' : '0.16)'); ctx.fillRect(xL, Math.min(yE, yS), xR - xL, Math.abs(yS - yE));
        // lines
        const hline = (y, color, dash) => { ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash(dash || []); ctx.beginPath(); ctx.moveTo(xL, y + 0.5); ctx.lineTo(xR, y + 0.5); ctx.stroke(); ctx.restore(); };
        hline(yT, GREEN + '0.95)'); hline(yS, RED + '0.95)'); hline(yE, '#c8c8c8', [5, 3]);
        // handles (right edge)
        const handle = (y) => { ctx.fillStyle = '#0d0d0d'; ctx.strokeStyle = '#3987e5'; ctx.lineWidth = 1.5; ctx.fillRect(xR - 9, y - 4, 8, 8); ctx.strokeRect(xR - 9, y - 4, 8, 8); };
        [yE, yT, yS].forEach(handle);
        // labels
        ctx.font = '11px system-ui, sans-serif'; ctx.textBaseline = 'middle';
        const sign = o.side === 'long' ? 1 : -1;
        const rewardAmt = (o.tp - o.entry) * sign * o.qty, riskAmt = (o.entry - o.sl) * sign * o.qty;
        const tpPct = ((o.tp - o.entry) / o.entry) * 100 * sign, slPct = ((o.entry - o.sl) / o.entry) * 100 * sign;
        const rr = Math.abs(o.entry - o.sl) > 0 ? Math.abs((o.tp - o.entry) / (o.entry - o.sl)) : 0;
        const pill = (text, y, bg, fg) => {
          const w = ctx.measureText(text).width + 12, x = Math.max(xL, Math.min(xR - w - 12, 8));
          ctx.fillStyle = bg; roundRect(ctx, x, y - 9, w, 18, 4); ctx.fill();
          ctx.fillStyle = fg; ctx.fillText(text, x + 6, y);
        };
        pill(`Target ${tnf(o.tp)}  ${tpPct >= 0 ? '+' : ''}${tnf(tpPct)}%  ${tnf(rewardAmt)}`, yT, GREEN + '0.92)', '#fff');
        pill(`Stop ${tnf(o.sl)}  ${tnf(slPct)}%  ${tnf(-Math.abs(riskAmt))}`, yS, RED + '0.92)', '#fff');
        const live = o.status === 'open' && TRADE.lastPrice != null ? (TRADE.lastPrice - o.entry) * sign * o.qty : null;
        const mid = `#${o.id} ${o.side.toUpperCase()} · ${o.status.toUpperCase()}  Qty ${o.qty}  RR ${tnf(rr, 2)}` + (live != null ? `  P&L ${live >= 0 ? '+' : ''}${tnf(live)}` : '');
        pill(mid, yE, 'rgba(30,30,30,0.9)', '#e8e8e8');
      }),
    }),
  };
  return {
    attached(p) { series = p.series; chart = p.chart; requestUpdate = p.requestUpdate; o._req = p.requestUpdate; },
    detached() { series = chart = requestUpdate = null; },
    updateAllViews() {}, paneViews() { return [view]; },
  };
}

// ---------- drag to resize ----------
function attachDrag(pane) {
  const el = pane.el, c = pane.candle;
  const yOf = (price) => c.priceToCoordinate(price);
  const near = (my) => {
    let best = null, bestD = 7;
    for (const o of TRADE.orders) {
      if (o.status === 'closed') continue;
      for (const f of ['entry', 'tp', 'sl']) { const y = yOf(o[f]); if (y == null) continue; const d = Math.abs(y - my); if (d < bestD) { bestD = d; best = { o, f }; } }
    }
    return best;
  };
  el.addEventListener('pointermove', (e) => {
    const rect = el.getBoundingClientRect(); const my = e.clientY - rect.top;
    if (TRADE.drag) {
      const price = c.coordinateToPrice(my); if (price == null) return;
      const { o, f } = TRADE.drag; o[f] = price;
      if (o._req) o._req();
      render();
      e.preventDefault();
    } else {
      const hit = near(my); el.style.cursor = TRADE.armed ? 'crosshair' : (hit ? 'ns-resize' : '');
    }
  });
  el.addEventListener('pointerdown', (e) => {
    if (TRADE.armed) return; // click handler places the order
    const rect = el.getBoundingClientRect(); const hit = near(e.clientY - rect.top);
    if (hit) { TRADE.drag = hit; pane.chart.applyOptions({ handleScroll: false, handleScale: false }); el.setPointerCapture(e.pointerId); e.preventDefault(); }
  });
  const end = () => { if (TRADE.drag) { TRADE.drag = null; pane.chart.applyOptions({ handleScroll: true, handleScale: true }); } };
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
}

// ---------- panel ----------
function buildPanel() { const el = document.createElement('div'); el.id = 'tradesPanel'; document.body.appendChild(el); if (typeof makePanelDraggable === 'function') makePanelDraggable(el); render(); }
function render() {
  const el = tget('tradesPanel'); if (!el) return;
  const open = TRADE.orders.filter((o) => o.status === 'open').length;
  const pend = TRADE.orders.filter((o) => o.status === 'pending').length;
  const pnlCls = TRADE.realizedPnl >= 0 ? 'win' : 'loss';
  const rows = TRADE.events.map((e) => {
    const cls = /P&L \+|TP /.test(e) ? 'win' : /SL /.test(e) ? 'loss' : '';
    return `<div class="tp-row"><span class="${cls}">${e}</span></div>`;
  }).join('') || '<div class="tp-row muted">Arm Long/Short, click the chart to set entry. Drag the lines to resize.</div>';
  el.innerHTML =
    `<div class="tp-head"><span>Trades · realized <span class="${pnlCls}">${TRADE.realizedPnl >= 0 ? '+' : ''}${tnf(TRADE.realizedPnl)}</span></span>` +
    `<span class="muted">open ${open} · pending ${pend} <span class="tp-min" title="collapse">▾</span> <span class="tp-close" id="tpClear">clear</span></span></div>` + rows;
  const cl = tget('tpClear'); if (cl) cl.addEventListener('click', clearTrades);
}
