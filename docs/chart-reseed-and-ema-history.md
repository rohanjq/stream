# Chart reseeding and authoritative EMA history

The chart is a consumer. It does not calculate EMA or any other indicator.
Candles come from OHLC and EMA values come from Signals.

## Candle recovery

The OHLC browser client reconnects with bounded exponential backoff. Every
subscription requests durable history, and a `seed` replaces the pane's candle
array with a timestamp-deduplicated, sorted snapshot. An OHLC history repair
closes the affected WebSocket connection, so the running browser automatically
reconnects and replaces stale candles without restarting the chart container.

## EMA history

The scene server keeps authenticated Signals credentials out of the browser.
It opens one Signals WebSocket per supported timeframe (`1m`, `5m`, `15m`,
`1h`) and requests up to 1,000 historical events for that timeframe. Separate
subscriptions prevent one timeframe from consuming the global history limit.

Only EMA 50 and EMA 200 events are retained and exposed to the chart. On a
Signals reconnect, snapshot and history messages are staged in a temporary
cache, then replace that timeframe atomically when history is complete. This
allows a corrected lower revision to replace stale higher live revisions.

The browser receives a `replace` marker and clears that timeframe's EMA cache
before setting the complete line data. Live provisional points may update the
last point; equal-revision confirmed points cannot be replaced by provisional
ones.

## Restart behavior

- OHLC restart: chart reconnects and replaces candle history.
- Signals restart: the server bridge retains the last display while marked
  disconnected, then atomically replaces EMA history after reconnect.
- Stream application restart: all four private subscriptions reload complete
  history; the browser starts from empty caches and renders the replacement.
- Compositor restart: MediaMTX and the application continue independently; the
  publisher reconnects to the local raw stream.

Tests are in `demo/tests/test_signal_bridge.py` and the normal Stream test
suite. JavaScript syntax checks cover `feed.js`, `signal-feed.js`, and
`chart.js`.

