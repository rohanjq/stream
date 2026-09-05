// Fetch ~7 days of BTCUSDT klines from AllTick for all timeframes, paginating
// backward (500/request cap) with adaptive rate-limit backoff. Writes data_btc.js.
const https = require('https');
const fs = require('fs');
const TOKEN = process.env.ALLTICK_TOKEN || '';  // redacted for repo — supply your own AllTick token
const get = (u) => new Promise((res, rej) => {
  https.get(u, (r) => { let d = ''; r.on('data', (c) => d += c); r.on('end', () => { try { res(JSON.parse(d)); } catch (e) { res({ ret: -1, raw: d.slice(0, 120) }); } }); }).on('error', rej);
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function klinePage(code, kt, end) {
  const q = encodeURIComponent(JSON.stringify({ trace: 'bf', data: { code, kline_type: kt, kline_timestamp_end: end, query_kline_num: 500, adjust_type: 0 } }));
  const url = `https://quote.alltick.co/quote-b-api/kline?token=${encodeURIComponent(TOKEN)}&query=${q}`;
  let spacing = 1500;
  for (let attempt = 0; attempt < 8; attempt++) {
    const j = await get(url);
    if (j && j.ret === 200 && j.data && j.data.kline_list) return j.data.kline_list;
    if (j && (j.error_msg || j.ret === -1)) { await sleep(12000); continue; }   // rate-limited -> back off
    await sleep(spacing); spacing = Math.min(12000, spacing * 2);
  }
  return [];
}

async function fetchTF(code, kt, target) {
  const map = new Map();
  let end = 0;
  while (map.size < target) {
    const list = await klinePage(code, kt, end);
    if (!list.length) break;
    let oldest = Infinity;
    for (const b of list) { const t = +b.timestamp; if (!map.has(t)) map.set(t, b); if (t < oldest) oldest = t; }
    if (!isFinite(oldest) || oldest >= end && end !== 0) break;
    end = oldest;                 // paginate backward
    process.stdout.write('.');
    await sleep(1500);
    if (list.length < 500) break; // no more history
  }
  return [...map.values()].map((b) => ({ time: +b.timestamp, open: +b.open_price, high: +b.high_price, low: +b.low_price, close: +b.close_price })).sort((a, b) => a.time - b.time);
}

(async () => {
  const DAYS = 7;
  const tfs = [['1m', 60, 1], ['5m', 300, 2], ['15m', 900, 3], ['30m', 1800, 4], ['1h', 3600, 5]];
  const HIST = {};
  for (const [label, sec, kt] of tfs) {
    const target = Math.ceil(DAYS * 86400 / sec);
    process.stdout.write(`\n${label} (target ${target}) `);
    HIST[sec] = await fetchTF('BTCUSDT', kt, target);
    const last = HIST[sec][HIST[sec].length - 1], first = HIST[sec][0];
    console.log(` -> ${HIST[sec].length} bars  ${first && new Date(first.time * 1000).toISOString()} .. ${last && new Date(last.time * 1000).toISOString()}`);
    await sleep(1500);
  }
  fs.writeFileSync('data_btc.js', '// BTCUSDT klines from AllTick (~7 days), backfill seed for LIVE mode.\n// window.HIST_BTC[sec]=[{time,o,h,l,c}].\nwindow.HIST_BTC=' + JSON.stringify(HIST) + ';\n');
  console.log('\nwrote data_btc.js', fs.statSync('data_btc.js').size, 'bytes');
})().catch((e) => { console.error('ERR', e); process.exit(1); });
