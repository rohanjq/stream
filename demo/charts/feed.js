// AllTick live WebSocket feed (crypto "b" market).
// Protocol (github.com/alltick/alltick-realtime-...-websocket-api):
//   URL   : wss://quote.alltick.co/quote-b-ws-api?token=TOKEN
//   sub   : cmd_id 22004  { data:{ symbol_list:[{code:"BTCUSDT"}] } }
//   ping  : cmd_id 22000  {} every 10s (drop after 30s idle)
//   tick  : cmd_id 22998  { data:{ code, tick_time, price, volume } }
//
// NOTE: the token below is client-visible. Fine for local backtesting; do not
// deploy publicly or commit to a shared repo — rotate if leaked.
const ALLTICK_TOKEN = process.env.ALLTICK_TOKEN || '';  // redacted for repo — supply your own AllTick token
const ALLTICK_WS = 'wss://quote.alltick.co/quote-b-ws-api';

// startLiveFeed(code, onTick(priceNumber, unixSeconds), onStatus(text, badgeClass))
function startLiveFeed(code, onTick, onStatus) {
  let ws, hb, seq = 1, retry = 0, alive = true;
  const trace = () => `wf-${seq}-${(seq * 2654435761) % 1e9}`; // deterministic-ish unique string
  const status = (t, c) => { if (typeof onStatus === 'function') onStatus(t, c); };

  function connect() {
    status('CONNECTING…', 'paused');
    try { ws = new WebSocket(`${ALLTICK_WS}?token=${encodeURIComponent(ALLTICK_TOKEN)}`); }
    catch (e) { return scheduleReconnect(); }

    ws.onopen = () => {
      retry = 0;
      status('LIVE', 'live');
      // subscribe to the transaction/tick stream
      ws.send(JSON.stringify({ cmd_id: 22004, seq_id: seq++, trace: trace(), data: { symbol_list: [{ code }] } }));
      // heartbeat every 10s (server drops after 30s idle)
      clearInterval(hb);
      hb = setInterval(() => { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ cmd_id: 22000, seq_id: seq++, trace: trace(), data: {} })); }, 10000);
    };

    ws.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.cmd_id === 22998 && m.data && m.data.price != null) {
        const price = parseFloat(m.data.price);
        // Bucket by the BROWSER clock (real "now"), not AllTick's tick_time — the
        // exchange tick timestamps are offset from the kline timestamps, which would
        // otherwise land ticks before the last backfill bar (chart looks frozen).
        if (isFinite(price)) onTick(price, Math.floor(Date.now() / 1000));
      }
      // 22001 = heartbeat ack, 22005 = sub ack — ignored.
    };

    ws.onclose = () => { clearInterval(hb); if (alive) scheduleReconnect(); };
    ws.onerror = () => { try { ws.close(); } catch {} };
  }

  function scheduleReconnect() {
    retry++;
    status(`RECONNECT ${retry}…`, 'paused');
    setTimeout(() => { if (alive) connect(); }, Math.min(15000, 1000 * retry));
  }

  connect();
  return { stop() { alive = false; clearInterval(hb); if (ws) try { ws.close(); } catch {} } };
}
