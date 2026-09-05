// Fetch GOLD klines from AllTick for the live-mode backfill (~7 weeks on higher
// TFs; 1m/5m capped to keep it within the free-tier request quota). Paginates
// backward with adaptive rate-limit backoff. Writes data_live.js (window.HIST_LIVE).
const https = require('https');
const fs = require('fs');
const TOKEN = process.env.ALLTICK_TOKEN || '';  // redacted for repo — supply your own AllTick token
const SYM = 'GOLD';
const get = (u) => new Promise((res) => { https.get(u, (r) => { let d = ''; r.on('data', (c) => d += c); r.on('end', () => { try { res(JSON.parse(d)); } catch (e) { res({ ret: -1 }); } }); }).on('error', () => res({ ret: -1 })); });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function page(kt, end) {
  const q = encodeURIComponent(JSON.stringify({ trace: 'bf', data: { code: SYM, kline_type: kt, kline_timestamp_end: end, query_kline_num: 500, adjust_type: 0 } }));
  const url = `https://quote.alltick.co/quote-b-api/kline?token=${encodeURIComponent(TOKEN)}&query=${q}`;
  for (let a = 0; a < 8; a++) { const j = await get(url); if (j && j.ret === 200 && j.data && j.data.kline_list) return j.data.kline_list; await sleep(12000); }
  return [];
}
async function fetchTF(kt, target) {
  const map = new Map(); let end = 0;
  while (map.size < target) {
    const list = await page(kt, end); if (!list.length) break;
    let oldest = Infinity; for (const b of list) { const t = +b.timestamp; if (!map.has(t)) map.set(t, b); if (t < oldest) oldest = t; }
    if (end && oldest >= end) break; end = oldest; process.stdout.write('.'); await sleep(11000);
    if (list.length < 500) break;
  }
  return [...map.values()].map((b) => ({ time: +b.timestamp, open: +b.open_price, high: +b.high_price, low: +b.low_price, close: +b.close_price })).sort((a, b) => a.time - b.time);
}
(async () => {
  const D = 86400;
  const tfs = [['1m', 60, 1, 5 * D], ['5m', 300, 2, 21 * D], ['15m', 900, 3, 49 * D], ['30m', 1800, 4, 49 * D], ['1h', 3600, 5, 49 * D]];
  const HIST = {};
  for (const [label, sec, kt, span] of tfs) {
    process.stdout.write(`\n${label} `); HIST[sec] = await fetchTF(kt, Math.ceil(span / sec));
    const f = HIST[sec][0], l = HIST[sec][HIST[sec].length - 1];
    console.log(` -> ${HIST[sec].length} bars  ${f && new Date(f.time * 1000).toISOString()} .. ${l && new Date(l.time * 1000).toISOString()}`);
    await sleep(11000);
  }
  fs.writeFileSync('data_live.js', '// GOLD klines from AllTick — backfill seed for LIVE mode. window.HIST_LIVE[sec]=[{time,o,h,l,c}].\nwindow.HIST_LIVE=' + JSON.stringify(HIST) + ';\n');
  console.log('\nwrote data_live.js', fs.statSync('data_live.js').size, 'bytes');
})();
