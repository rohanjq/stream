// Canonical live candle feed from ohlcd. The server owns candle bucketing and
// OHLC calculation; consumers only upsert the snapshots it sends.
function startLiveFeed(symbol, timeframes, onMessage, onStatus) {
  const params = new URLSearchParams(location.search);
  const defaultUrl = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.hostname}:18081/ws`;
  const wsUrl = params.get('ohlc_ws') || defaultUrl;
  const seconds = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400 };
  const validTimeframes = (items) => items.filter((tf) => seconds[tf]);
  const seedBars = (tf) => Math.min(25000, Math.ceil((14 * 86400) / seconds[tf]));
  const status = (text, cls) => { if (typeof onStatus === 'function') onStatus(text, cls); };

  let wanted = new Set(validTimeframes(timeframes));
  let subscribed = new Set();
  let ws = null;
  let alive = true;
  let retry = 0;
  let reconnectTimer = null;

  function send(action, tf) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const request = { action, symbol, tf };
    if (action === 'subscribe') request.seed_bars = seedBars(tf);
    ws.send(JSON.stringify(request));
  }

  function syncSubscriptions(refresh) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    for (const tf of [...subscribed]) {
      if (refresh || !wanted.has(tf)) {
        send('unsubscribe', tf);
        subscribed.delete(tf);
      }
    }
    for (const tf of wanted) {
      if (!subscribed.has(tf)) {
        send('subscribe', tf);
        subscribed.add(tf);
      }
    }
  }

  function scheduleReconnect() {
    if (!alive || reconnectTimer) return;
    retry += 1;
    status(`RECONNECT ${retry}…`, 'paused');
    const delay = Math.min(30000, 1000 * (2 ** Math.min(retry - 1, 5))) + Math.floor(Math.random() * 500);
    reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, delay);
  }

  function connect() {
    if (!alive) return;
    status('CONNECTING…', 'paused');
    try { ws = new WebSocket(wsUrl); } catch (e) { scheduleReconnect(); return; }

    ws.onopen = () => {
      retry = 0;
      subscribed.clear();
      status('LIVE', 'live');
      syncSubscriptions(false);
    };

    ws.onmessage = ({ data }) => {
      let message;
      try { message = JSON.parse(data); } catch (e) { return; }
      if (message.type === 'error') {
        status(`OHLC ERROR: ${message.error || 'subscription failed'}`, 'paused');
        return;
      }
      if (message.type === 'seed' || message.type === 'forming' || message.type === 'closed') onMessage(message);
    };

    ws.onclose = () => {
      subscribed.clear();
      ws = null;
      scheduleReconnect();
    };
    ws.onerror = () => { if (ws) try { ws.close(); } catch (e) {} };
  }

  connect();
  return {
    setTimeframes(next, refresh = false) {
      wanted = new Set(validTimeframes(next));
      syncSubscriptions(refresh);
    },
    stop() {
      alive = false;
      clearTimeout(reconnectTimer);
      if (ws) try { ws.close(); } catch (e) {}
    },
  };
}
