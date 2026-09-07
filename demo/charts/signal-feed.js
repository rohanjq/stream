// Authoritative EMA feed. Authentication stays in the scene server; this
// browser receives only validated EMA 50/200 events from its same origin.
function startSignalFeed(symbol, timeframes, onEvents, onStatus) {
  let wanted = new Set(timeframes);
  let versions = new Map();
  let alive = true;
  let timer = null;
  let busy = false;

  async function request(tf) {
    const params = new URLSearchParams({
      symbol, timeframe: tf, since: String(versions.get(tf) ?? -1),
    });
    const response = await fetch(`/api/signals/ema?${params}`);
    if (!response.ok) throw new Error(`Signals HTTP ${response.status}`);
    const payload = await response.json();
    if (Array.isArray(payload.data) && (payload.data.length || payload.replace)) {
      onEvents(payload.data, payload.replace === true, tf);
    }
    if (Number.isInteger(payload.version)) versions.set(tf, payload.version);
    return payload.connected !== false;
  }

  async function poll() {
    if (!alive || busy) return;
    busy = true;
    try {
      for (const tf of wanted) {
        await request(tf);
      }
      if (typeof onStatus === 'function') onStatus(true);
    } catch (error) {
      console.warn('Signals feed unavailable', error);
      if (typeof onStatus === 'function') onStatus(false);
    } finally {
      busy = false;
      if (alive) timer = setTimeout(poll, 1000);
    }
  }

  poll();
  return {
    setTimeframes(next) {
      wanted = new Set(next);
      versions = new Map();
      clearTimeout(timer);
      timer = setTimeout(poll, 0);
    },
    stop() {
      alive = false;
      clearTimeout(timer);
    },
  };
}
