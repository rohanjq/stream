// Local app server + always-fresh AllTick backfill cache.
// - Serves the static app (no-cache, so reloads always get the latest files).
// - Keeps a rolling ~14-day BTCUSDT kline cache per timeframe, refreshed round-robin
//   (rate-limit friendly), so the browser can pull gap-free history up to "now" on
//   every load via /api/klines?sec=<sec>&days=<n>  (AllTick REST has no CORS, so the
//   browser can't call it directly — this proxy/cache is how live mode stays current).
//
// Launch:  node server.js    then open  http://localhost:8137/
const http = require('http'), fs = require('fs'), path = require('path'), https = require('https');
const PORT = 8137, ROOT = __dirname;
const TOKEN = process.env.ALLTICK_TOKEN || '';  // redacted for repo — supply your own AllTick token
const SYMBOL = process.env.SYM || 'GOLD';           // AllTick live instrument
const TFS = [['1m', 60, 1], ['5m', 300, 2], ['15m', 900, 3], ['30m', 1800, 4], ['1h', 3600, 5]];
const KEEP_DAYS = 49;                               // keep ~7 weeks

const cache = {};                                   // sec -> [{time,open,high,low,close}]
try {                                               // seed from data_live.js if present
  const s = fs.readFileSync(path.join(ROOT, 'data_live.js'), 'utf8');
  const i = s.indexOf('window.HIST_LIVE=');         // anchor past the comment lines
  const obj = JSON.parse(s.slice(s.indexOf('{', i), s.lastIndexOf('}') + 1));
  for (const k in obj) cache[+k] = obj[k];
  console.log('seeded cache from data_live.js:', Object.keys(cache).map((k) => `${k}s:${cache[k].length}`).join(' '));
} catch (e) { console.log('no data_live.js seed'); }

const getJSON = (u) => new Promise((res) => { https.get(u, (r) => { let d = ''; r.on('data', (c) => d += c); r.on('end', () => { try { res(JSON.parse(d)); } catch { res(null); } }); }).on('error', () => res(null)); });

async function refreshTF(sec, kt) {
  const q = encodeURIComponent(JSON.stringify({ trace: 'r', data: { code: SYMBOL, kline_type: kt, kline_timestamp_end: 0, query_kline_num: 200, adjust_type: 0 } }));
  const j = await getJSON(`https://quote.alltick.co/quote-b-api/kline?token=${encodeURIComponent(TOKEN)}&query=${q}`);
  if (!j || j.ret !== 200 || !j.data || !j.data.kline_list) return;
  const map = new Map((cache[sec] || []).map((b) => [b.time, b]));
  for (const b of j.data.kline_list) map.set(+b.timestamp, { time: +b.timestamp, open: +b.open_price, high: +b.high_price, low: +b.low_price, close: +b.close_price });
  let merged = [...map.values()].sort((a, b) => a.time - b.time);
  const cutoff = merged[merged.length - 1].time - KEEP_DAYS * 86400;
  cache[sec] = merged.filter((b) => b.time >= cutoff);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ri = 0;
function loop() { const [, sec, kt] = TFS[ri++ % TFS.length]; refreshTF(sec, kt).finally(() => setTimeout(loop, 20000)); } // 1 req / 20s (quota-safe)
// On boot, prime EVERY timeframe once (10s apart, rate-limit safe) so a restart
// after hours has all TFs current immediately — then hand off to the slow loop.
(async () => {
  for (const [label, sec, kt] of TFS) { await refreshTF(sec, kt); console.log('primed', label, (cache[sec] || []).length, 'bars'); await sleep(10000); }
  loop();
})();

const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.ico': 'image/x-icon' };
http.createServer((req, res) => {
  const u = new URL(req.url, 'http://x');
  if (u.pathname === '/api/klines') {
    const sec = +(u.searchParams.get('sec') || '300'), days = +(u.searchParams.get('days') || '7');
    const arr = cache[sec] || [];
    const cutoff = (arr.length ? arr[arr.length - 1].time : 0) - days * 86400;
    res.writeHead(200, { 'content-type': 'application/json', 'access-control-allow-origin': '*' });
    res.end(JSON.stringify(arr.filter((b) => b.time >= cutoff)));
    return;
  }
  let fp = path.join(ROOT, u.pathname === '/' ? 'chart.html' : decodeURIComponent(u.pathname));
  if (!fp.startsWith(ROOT)) { res.writeHead(403); res.end('forbidden'); return; }
  fs.readFile(fp, (e, data) => {
    if (e) { res.writeHead(404); res.end('not found'); return; }
    res.writeHead(200, { 'content-type': MIME[path.extname(fp)] || 'application/octet-stream', 'cache-control': 'no-cache' });
    res.end(data);
  });
}).listen(PORT, () => console.log('app + AllTick cache on http://localhost:' + PORT));
