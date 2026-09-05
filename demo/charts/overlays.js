// Market-structure & key-level overlays — threshold-zigzag implementation.
// Pure price-action on an OHLC array; no deps; node-testable.
//
// The key change vs a naive pivot detector: swings come from a ZIGZAG with a
// minimum reversal threshold (max of a % floor and an ATR multiple), so only
// SIGNIFICANT swings register — no label on every micro-wiggle. BOS/CHoCH/FAKE
// and key levels all reference these significant swings.
//
//   1. zigzagDev()  swing = confirmed only after price reverses >= dev from the
//                   running extreme. Each swing records confirmIdx (bar where the
//                   reversal was confirmed — used so scanning never looks ahead).
//   2. label()      HH/HL/LH/LL vs previous same-side swing.
//   3. detectRange()recent swing highs cluster AND lows cluster -> band.
//   4. scanBreaks() close beyond last swing = BOS(with trend)/CHoCH(against);
//                   wick beyond but close back inside = FAKE. Inside a range,
//                   edge pierces are FAKE until a bar CLOSES out of the band.
//   5. keyLevels()  cluster swing prices, score by touches + recency, near price.

const MS_DEFAULTS = {
  minSwingPct: 0.0005,  // small % floor; detection is mainly ATR-driven (adaptive)
  atrPeriod: 14,
  atrMult: 2.0,         // zigzag sensitivity: catch shallow-but-valid pullbacks; merge prunes noise
  minPullbackFrac: 0.6, // pullback must retrace >= this of the prior leg (or be a valid maru pullback) to keep the swing
  eqTol: 0.0015,        // swings "equal" within this -> range candidate
  rangeMinSwings: 4,
  rangeMaxHeight: 0.05,
  // --- valid-breakout rules (NCI) ---
  secondMaruRangeMin: 0.70,  // Rule A: 2nd maru total length >= 70% of 1st
  bigVsRef: 0.70,            // Rule B: C1 "big" >= 70% of biggest of last 5 marubozu
  breakBodyBeyond: 0.30,     // Rule B: >30% of C1 body beyond the line
  oppSmallMax: 0.30,         // Rule B: small 2nd candle <= 30% of C1 length
  pacWindow: 4,              // PAC repair lookahead (bars)
  levelClusterTol: 0.0015,
  levelSwingLag: 2,     // exclude the most recent N swings so levels don't shift with new candles
  maxLevels: 5,
  maxLevelDistPct: 0.03,
  recentBars: 400,
};

function atr(bars, period) {
  const out = new Array(bars.length).fill(0);
  let sum = 0;
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    const tr = i === 0 ? b.high - b.low
      : Math.max(b.high - b.low, Math.abs(b.high - bars[i - 1].close), Math.abs(b.low - bars[i - 1].close));
    if (i < period) { sum += tr; out[i] = sum / (i + 1); }
    else out[i] = (out[i - 1] * (period - 1) + tr) / period;
  }
  return out;
}

function zigzagDev(bars, o) {
  const a = atr(bars, o.atrPeriod);
  const dev = (i, price) => Math.max(o.minSwingPct, price > 0 ? (o.atrMult * a[i]) / price : o.minSwingPct);
  const sw = [];
  let dir = 0, hiIdx = 0, loIdx = 0;
  for (let i = 1; i < bars.length; i++) {
    if (bars[i].high > bars[hiIdx].high) hiIdx = i;
    if (bars[i].low < bars[loIdx].low) loIdx = i;
    if (dir >= 0 && bars[i].low <= bars[hiIdx].high * (1 - dev(i, bars[hiIdx].high))) {
      sw.push({ i: hiIdx, type: 'H', price: bars[hiIdx].high, time: bars[hiIdx].time, confirmIdx: i });
      dir = -1; loIdx = i;
    } else if (dir <= 0 && bars[i].high >= bars[loIdx].low * (1 + dev(i, bars[loIdx].low))) {
      sw.push({ i: loIdx, type: 'L', price: bars[loIdx].low, time: bars[loIdx].time, confirmIdx: i });
      dir = 1; hiIdx = i;
    }
  }
  return sw;
}

// A pullback is a 2-CANDLE pattern (per the course): two consecutive counter-trend
// marubozu, OR a big marubozu followed by a small candle. A lone candle popping
// against the trend is NOT a valid pullback (so it won't create a new swing).
function hasValidPullback(bars, iA, iB, dir, o) {
  for (let j = iA; j < iB; j++) {
    if (!isMaru(bars[j], dir)) continue;
    if (isMaru(bars[j + 1], dir)) return true;                 // TWO_MARU
    const mx = maru5Max(bars, j);
    const big = mx > 0 && (bars[j].high - bars[j].low) >= o.bigVsRef * mx;
    if (big && bars[j + 1]) {                                  // BIG + SMALL
      const Rj = bars[j].high - bars[j].low, Rn = (bars[j + 1].high - bars[j + 1].low) || 1e-9;
      if (Rn <= o.oppSmallMax * Rj) return true;
    }
  }
  return false;
}

// Merge swings whose intervening pullback is neither a valid pullback pattern NOR
// a deep-enough retracement — so a shallow consolidation between two highs is not
// two HHs; the zigzag connects straight to the more extreme (second) swing.
function mergeByValidPullback(bars, swings, o) {
  let changed = true;
  while (changed && swings.length >= 3) {
    changed = false;
    for (let i = 1; i < swings.length - 1; i++) {
      const a = swings[i - 1], b = swings[i], c = swings[i + 1];
      const dir = b.type === 'L' ? -1 : 1;                       // pullback direction into b
      const depth = Math.abs(a.price - b.price);
      const impulse = i >= 2 ? Math.abs(a.price - swings[i - 2].price) : Math.abs(c.price - b.price);
      const retrace = impulse > 0 ? depth / impulse : 1;
      const valid = hasValidPullback(bars, a.i, b.i, dir, o) || retrace >= o.minPullbackFrac;
      if (!valid) {
        if (a.type === 'H') { if (c.price >= a.price) swings.splice(i - 1, 2); else swings.splice(i, 2); }
        else { if (c.price <= a.price) swings.splice(i - 1, 2); else swings.splice(i, 2); }
        changed = true; break;
      }
    }
  }
  return swings;
}

// Label swings by MARKET-STRUCTURE state, not raw geometry. Trend is taken from
// the validated breakouts (FAKE excluded): in a downtrend every swing high is a
// Lower High (an unvalidated / fake-breakout high never becomes an HH), and only
// when a valid breakout flips the trend do HH/HL appear. Mirror for uptrend.
function labelByStructure(swings, events) {
  const te = events.filter((e) => e.kind !== 'FAKE')
    .map((e) => ({ time: e.time, trend: e.dir === 'up' ? 'up' : 'down' }))
    .sort((a, b) => a.time - b.time);
  const trendAt = (t) => { let tr = 'unknown'; for (const e of te) { if (e.time <= t) tr = e.trend; else break; } return tr; };
  let lastH = null, lastL = null;
  for (const s of swings) {
    const tr = trendAt(s.time);
    if (s.type === 'H') {
      if (tr === 'down') s.label = 'LH';                              // downtrend: highs are lower highs
      else if (tr === 'up') s.label = lastH == null || s.price > lastH ? 'HH' : 'LH';
      else s.label = lastH == null ? null : (s.price > lastH ? 'HH' : 'LH');
      lastH = s.price;
    } else {
      if (tr === 'up') s.label = 'HL';                                // uptrend: lows are higher lows
      else if (tr === 'down') s.label = lastL == null || s.price < lastL ? 'LL' : 'HL';
      else s.label = lastL == null ? null : (s.price < lastL ? 'LL' : 'HL');
      lastL = s.price;
    }
  }
  return swings;
}

function trendFrom(swings) {
  const hs = swings.filter((s) => s.type === 'H').slice(-2);
  const ls = swings.filter((s) => s.type === 'L').slice(-2);
  const up = hs.length === 2 && hs[1].price > hs[0].price && ls.length === 2 && ls[1].price > ls[0].price;
  const down = hs.length === 2 && hs[1].price < hs[0].price && ls.length === 2 && ls[1].price < ls[0].price;
  return up ? 'up' : down ? 'down' : 'range';
}

function median(a) { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; }

function detectRange(bars, swings, o) {
  const recent = swings.slice(-Math.max(o.rangeMinSwings, 6));
  const hs = recent.filter((s) => s.type === 'H').map((s) => s.price);
  const ls = recent.filter((s) => s.type === 'L').map((s) => s.price);
  if (hs.length < 2 || ls.length < 2 || recent.length < o.rangeMinSwings) return null;
  const top = median(hs), bottom = median(ls), mid = (top + bottom) / 2;
  const spread = (arr) => (Math.max(...arr) - Math.min(...arr)) / mid;
  if (spread(hs) > o.eqTol * 3 || spread(ls) > o.eqTol * 3) return null;
  if ((top - bottom) / mid > o.rangeMaxHeight || top <= bottom) return null;
  return { fromTime: recent[0].time, toTime: bars[bars.length - 1].time, top, bottom };
}

// ---- NCI candle vocabulary (total-length based) ----
function feat(b) {
  const R = (b.high - b.low) || 1e-9;
  const body = Math.abs(b.close - b.open);
  return { R, body, br: body / R, dir: b.close > b.open ? 1 : b.close < b.open ? -1 : 0, mid: (b.high + b.low) / 2, closeLoc: (b.close - b.low) / R };
}
function isMaru(b, dir) {
  const f = feat(b);
  if (f.dir !== dir) return false;
  if (f.br >= 0.70) return true;                                   // standard marubozu
  if (dir > 0 && f.br >= 0.50 && f.closeLoc >= 0.90) return true;  // special maru up
  if (dir < 0 && f.br >= 0.50 && f.closeLoc <= 0.10) return true;  // special maru down
  return false;
}
// biggest total length among the last 5 marubozu-class candles before idx (non-maru skipped)
function maru5Max(bars, idx) {
  let cnt = 0, mx = 0;
  for (let j = idx - 1; j >= 0 && cnt < 5; j--) {
    const f = feat(bars[j]);
    const m = f.br >= 0.70 || (f.br >= 0.50 && (f.closeLoc >= 0.90 || f.closeLoc <= 0.10));
    if (m) { cnt++; if (f.R > mx) mx = f.R; }
  }
  return mx;
}

// Classify one breakout ATTEMPT beginning at C1 (bars[i] closes beyond `line` in dir).
// Returns { status:'valid'|'fake'|'pending', confirmIdx, excursion }.
function evaluateBreakout(bars, i, line, dir, o) {
  const beyond = (x) => (dir > 0 ? x > line : x < line);
  const C1 = bars[i], f1 = feat(C1);
  if (!beyond(C1.close)) return { status: 'none' };
  const excursion1 = dir > 0 ? C1.high : C1.low;
  const C2 = bars[i + 1];
  if (!C2) return { status: 'pending', excursion: excursion1 };
  const f2 = feat(C2);
  const ex2 = dir > 0 ? Math.max(C1.high, C2.high) : Math.min(C1.low, C2.low);

  // Rule A — TWO MARU
  if (isMaru(C1, dir) && isMaru(C2, dir) && beyond(C2.close)
    && (dir > 0 ? C2.close > C1.close : C2.close < C1.close)
    && f2.R >= o.secondMaruRangeMin * f1.R) return { status: 'valid', confirmIdx: i + 1, excursion: ex2 };

  // Rule B — BIG + SMALL (with >30% of C1 body beyond the line)
  const mx = maru5Max(bars, i);
  const big = mx > 0 && f1.R >= o.bigVsRef * mx;
  const pen = dir > 0 ? Math.max(0, C1.close - Math.max(C1.open, line)) / f1.body
    : Math.max(0, Math.min(C1.open, line) - C1.close) / f1.body;
  const c1ok = isMaru(C1, dir) && big && pen > o.breakBodyBeyond;
  const small = f2.R <= o.oppSmallMax * f1.R && (dir > 0 ? C2.low >= f1.mid : C2.high <= f1.mid);
  const c2back = dir > 0 ? C2.close < line : C2.close > line;   // 2nd candle closed back inside
  if (c1ok && small && !c2back) return { status: 'valid', confirmIdx: i + 1, excursion: ex2 };

  // Rule S — STRONG HOLD: a decisive close-through that HOLDS is a valid breakout
  // (covers range escapes / strong moves the 2-maru & big+small templates miss).
  // Decisive = the breaking candle is a marubozu OR >30% of its body cleared the
  // line; "holds" = the next candle does not close back inside.
  if ((isMaru(C1, dir) || pen > o.breakBodyBeyond) && !c2back) return { status: 'valid', confirmIdx: i + 1, excursion: ex2 };

  if (c2back) return { status: 'fake', confirmIdx: i + 1, excursion: ex2 };

  // Rule C — PAC: exactly one failed candle (C1 is a decent break candle, C2 failed) ->
  // repair if a candle closes beyond the ORIGINAL 2-candle extreme within the next 4 bars.
  const c1break = isMaru(C1, dir) && beyond(C1.close);
  if (c1break) {
    const end = i + 1 + o.pacWindow;
    for (let j = i + 2; j <= Math.min(bars.length - 1, end); j++) {
      if (dir > 0 ? bars[j].close > ex2 : bars[j].close < ex2)
        return { status: 'valid', confirmIdx: j, excursion: dir > 0 ? Math.max(ex2, bars[j].high) : Math.min(ex2, bars[j].low) };
    }
    if (bars.length - 1 >= end) return { status: 'fake', confirmIdx: i + 1, excursion: ex2 };
    return { status: 'pending', excursion: ex2 };
  }
  return { status: 'fake', confirmIdx: i + 1, excursion: ex2 };
}

// STRUCTURAL breaks: a break is evaluated ONCE, when a new swing takes out the
// prior same-side swing extreme. The swing between them IS the pullback, so a
// straight continuation (no intermediate pivot) yields exactly one BOS on the
// breaking candle — never a stack of FAKE dots. Validity uses the candle rules
// above; a wick-only breach (no close beyond) or an invalid pattern -> FAKE, and
// the reference advances to the fake extreme (next attempt must clear it).
// The fake zone = contiguous run of bars (ending at the swing) whose extreme
// pierced the reference, bounded by [reference, farthest pierce] — this is the
// range a fake breakout creates, drawn as a rectangle.
function fakeZone(bars, ref, swingI, dir) {
  let start = swingI;
  while (start > 0 && (dir > 0 ? bars[start - 1].high > ref : bars[start - 1].low < ref)) start--;
  let ext = ref;
  for (let j = start; j <= swingI; j++) ext = dir > 0 ? Math.max(ext, bars[j].high) : Math.min(ext, bars[j].low);
  return { fromTime: bars[start].time, toTime: bars[swingI].time, top: Math.max(ref, ext), bottom: Math.min(ref, ext) };
}

function scanBreaks(bars, swings, o) {
  const events = [];
  let trend = 'range';
  let lastHigh = null, lastLow = null; // {price, i}
  for (const s of swings) {
    if (s.type === 'H') {
      if (lastHigh && s.price > lastHigh.price) {
        let k = -1;
        for (let j = lastHigh.i + 1; j <= s.i; j++) if (bars[j].close > lastHigh.price) { k = j; break; }
        if (k < 0) { events.push({ time: bars[s.i].time, price: lastHigh.price, kind: 'FAKE', dir: 'up', zone: fakeZone(bars, lastHigh.price, s.i, 1) }); } // wick-only breach
        else {
          const r = evaluateBreakout(bars, k, lastHigh.price, 1, o);
          if (r.status === 'valid') { events.push({ time: bars[r.confirmIdx].time, price: lastHigh.price, kind: trend === 'down' ? 'CHoCH' : 'BOS', dir: 'up' }); trend = 'up'; }
          else if (r.status === 'fake') { events.push({ time: bars[k].time, price: lastHigh.price, kind: 'FAKE', dir: 'up', zone: fakeZone(bars, lastHigh.price, s.i, 1) }); }
        }
      }
      lastHigh = { price: s.price, i: s.i };
    } else {
      if (lastLow && s.price < lastLow.price) {
        let k = -1;
        for (let j = lastLow.i + 1; j <= s.i; j++) if (bars[j].close < lastLow.price) { k = j; break; }
        if (k < 0) { events.push({ time: bars[s.i].time, price: lastLow.price, kind: 'FAKE', dir: 'down', zone: fakeZone(bars, lastLow.price, s.i, -1) }); }
        else {
          const r = evaluateBreakout(bars, k, lastLow.price, -1, o);
          if (r.status === 'valid') { events.push({ time: bars[r.confirmIdx].time, price: lastLow.price, kind: trend === 'up' ? 'CHoCH' : 'BOS', dir: 'down' }); trend = 'down'; }
          else if (r.status === 'fake') { events.push({ time: bars[k].time, price: lastLow.price, kind: 'FAKE', dir: 'down', zone: fakeZone(bars, lastLow.price, s.i, -1) }); }
        }
      }
      lastLow = { price: s.price, i: s.i };
    }
  }
  return events;
}

// Key levels are built from ESTABLISHED structure only: the most recent
// `levelSwingLag` swings are excluded so a level does not shift with the last few
// candles. A cluster's price is pinned to its FIRST touch (not a running mean),
// and scoring is by touch count only (no recency), so an existing level stays put
// across bar closes — new levels appear only once a swing has aged past the lag.
function keyLevels(bars, swings, o) {
  const last = bars[bars.length - 1].close;
  const established = swings.slice(0, Math.max(0, swings.length - o.levelSwingLag));
  const clusters = [];
  for (const s of established) {
    const c = clusters.find((c) => Math.abs(c.price - s.price) / s.price < o.levelClusterTol);
    if (c) { c.touches++; c.lastI = Math.max(c.lastI, s.i); }   // price stays pinned to first touch
    else clusters.push({ price: s.price, touches: 1, lastI: s.i });
  }
  clusters.forEach((c) => { c.side = c.price >= last ? 'res' : 'sup'; });
  return clusters
    .filter((c) => Math.abs(c.price - last) / last <= o.maxLevelDistPct)
    .sort((a, b) => b.touches - a.touches || b.lastI - a.lastI)   // touches first, older wins ties
    .slice(0, o.maxLevels);
}

function computeStructure(allBars, opts) {
  const o = { ...MS_DEFAULTS, ...opts };
  const bars = allBars.slice(-o.recentBars);
  if (bars.length < o.atrPeriod + 4) return { swings: [], events: [], ranges: [], levels: [], trend: 'range' };
  const swings = mergeByValidPullback(bars, zigzagDev(bars, o), o);
  const range = detectRange(bars, swings, o);
  const events = scanBreaks(bars, swings, o);
  labelByStructure(swings, events);      // labels follow validated structure, not geometry
  // Key levels use the FULL history (not the rolling window) so an established
  // level never disappears/shifts as older bars scroll out of the structure window.
  const fullSwings = allBars.length > bars.length ? mergeByValidPullback(allBars, zigzagDev(allBars, o), o) : swings;
  const levels = keyLevels(allBars, fullSwings, o);
  const lastEv = [...events].reverse().find((e) => e.kind !== 'FAKE');
  // Trend follows the last validated break (BOS/CHoCH) so the badge flips on a
  // CHoCH; RANGE only before any break has happened.
  const trend = lastEv ? (lastEv.dir === 'up' ? 'up' : 'down') : (range ? 'range' : trendFrom(swings));
  return { swings, events, ranges: range ? [range] : [], levels, trend };
}

// ---------------- rendering (browser only) ----------------
// Lightweight Charts series primitive: a filled rectangle over [from,to]x[bottom,top].
function rectPrimitive(zone, fill, border) {
  let series = null, chart = null;
  const view = {
    zOrder: () => 'bottom',
    renderer: () => ({
      draw: (target) => target.useBitmapCoordinateSpace((scope) => {
        if (!series || !chart) return;
        const ts = chart.timeScale();
        const x1 = ts.timeToCoordinate(zone.fromTime), x2 = ts.timeToCoordinate(zone.toTime);
        const y1 = series.priceToCoordinate(zone.top), y2 = series.priceToCoordinate(zone.bottom);
        if (x1 == null || x2 == null || y1 == null || y2 == null) return;
        const c = scope.context, hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
        const L = Math.min(x1, x2) * hr, R = Math.max(x1, x2) * hr, T = Math.min(y1, y2) * vr, B = Math.max(y1, y2) * vr;
        const w = Math.max(R - L, 3 * hr);
        c.fillStyle = fill; c.fillRect(L, T, w, B - T);
        c.strokeStyle = border; c.lineWidth = hr; c.strokeRect(L, T, w, B - T);
      }),
    }),
  };
  return { attached(p) { series = p.series; chart = p.chart; }, detached() { series = null; chart = null; }, updateAllViews() {}, paneViews() { return [view]; } };
}

function initOverlays(pane, cfg, ctx) {
  pane.overlay.cfg = cfg; // shared CONFIG.overlays; toggles mutate it
  pane.overlay.structLine = pane.chart.addLineSeries({
    color: ctx.css('--struct'), lineWidth: 1, lineStyle: 2,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
  pane.overlay.structPlines = [];
  pane.overlay.levelPlines = [];
  pane.overlay.rects = [];
  redrawOverlays(pane, ctx);
}

function onBarClose(pane, ctx) { redrawOverlays(pane, ctx); }

function clearLines(pane, key) {
  (pane.overlay[key] || []).forEach((l) => pane.candle.removePriceLine(l));
  pane.overlay[key] = [];
}

function redrawOverlays(pane, ctx) {
  const cfg = pane.overlay.cfg || {};
  const css = ctx.css;
  const closed = pane.forming ? pane.bars.slice(0, -1) : pane.bars; // exclude only a live forming bar
  if (closed.length < 12) return;
  const res = computeStructure(closed, {});
  pane.overlay.trend = res.trend;

  clearLines(pane, 'structPlines');
  clearLines(pane, 'levelPlines');
  (pane.overlay.rects || []).forEach((r) => pane.candle.detachPrimitive(r));
  pane.overlay.rects = [];
  const markers = [];

  if (cfg.marketStructure) {
    const pts = res.swings.map((s) => ({ time: s.time, value: s.price }))
      .filter((p, k, a) => k === 0 || p.time !== a[k - 1].time);
    pane.overlay.structLine.applyOptions({ visible: true });
    pane.overlay.structLine.setData(pts);

    for (const s of res.swings) {
      if (!s.label || s.label === 'H' || s.label === 'L') continue;
      const bull = s.label === 'HH' || s.label === 'HL';
      markers.push({
        time: s.time, position: s.type === 'H' ? 'aboveBar' : 'belowBar',
        color: bull ? css('--level-sup') : css('--level-res'),
        shape: s.type === 'H' ? 'arrowDown' : 'arrowUp', text: s.label,
      });
    }
    for (const e of res.events) {
      if (e.kind === 'FAKE') {
        if (e.zone) {
          const rp = rectPrimitive(e.zone, 'rgba(137,135,129,0.20)', 'rgba(137,135,129,0.75)');
          pane.candle.attachPrimitive(rp);
          pane.overlay.rects.push(rp);
        }
        continue; // fakes drawn as rectangles, not dots
      }
      const color = e.dir === 'up' ? css('--struct') : css('--struct-bear');
      markers.push({
        time: e.time, position: e.dir === 'up' ? 'belowBar' : 'aboveBar',
        color, shape: e.dir === 'up' ? 'arrowUp' : 'arrowDown', text: e.kind,
      });
    }
    for (const r of res.ranges) {
      [r.top, r.bottom].forEach((p) => pane.overlay.structPlines.push(pane.candle.createPriceLine({
        price: p, color: css('--muted'), lineStyle: 1, lineWidth: 1, axisLabelVisible: true, title: 'range',
      })));
    }
  } else {
    pane.overlay.structLine.applyOptions({ visible: false });
  }

  if (cfg.keyLevels) {
    for (const lv of res.levels) {
      pane.overlay.levelPlines.push(pane.candle.createPriceLine({
        price: lv.price, lineWidth: Math.min(3, 1 + Math.floor(lv.touches / 2)),
        color: lv.side === 'res' ? css('--level-res') : css('--level-sup'),
        lineStyle: 0, axisLabelVisible: true, title: `${lv.side === 'res' ? 'R' : 'S'} x${lv.touches}`,
      }));
    }
  }

  markers.sort((a, b) => a.time - b.time);
  pane.candle.setMarkers(markers);
}

if (typeof module !== 'undefined') module.exports = { computeStructure, MS_DEFAULTS, zigzagDev, atr, labelByStructure };
