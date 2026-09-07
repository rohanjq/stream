import unittest

import scene_server


def event(period, open_time, value, revision=1, status="confirmed"):
    return {
        "type": "io.ytstack.signals.analysis.updated.v1",
        "data": {
            "series": {"dataset": "live", "symbol": "BTCUSDT", "timeframe": "5m"},
            "bar": {"open_time": open_time, "status": status},
            "analysis": {"name": "ema", "parameters": {"period": period}},
            "analysis_revision": revision,
            "outputs": [{"name": "value", "type": "number", "value": value}],
            "ready": True,
        },
    }


class SignalBridgeTests(unittest.TestCase):
    def test_keeps_full_history_for_only_ema_50_and_200(self):
        bridge = scene_server.SignalBridge()
        bridge._consume({"data": [
            event(50, "2026-09-06T12:00:00Z", 100.0),
            event(50, "2026-09-06T12:05:00Z", 101.0),
            event(200, "2026-09-06T12:00:00Z", 90.0),
            event(21, "2026-09-06T12:00:00Z", 99.0),
        ]})

        payload = bridge.payload("BTCUSDT", "5m", -1)
        self.assertEqual(3, len(payload["data"]))
        self.assertTrue(payload["replace"])
        self.assertEqual({50, 200}, {
            item["data"]["analysis"]["parameters"]["period"]
            for item in payload["data"]
        })

    def test_confirmed_value_is_not_replaced_by_equal_revision_provisional(self):
        bridge = scene_server.SignalBridge()
        bridge._consume({"data": [event(50, "2026-09-06T12:00:00Z", 100.0)]})
        bridge._consume({"data": [
            event(50, "2026-09-06T12:00:00Z", 999.0, status="provisional")
        ]})

        payload = bridge.payload("BTCUSDT", "5m", -1)
        self.assertEqual(100.0, payload["data"][0]["data"]["outputs"][0]["value"])

    def test_authoritative_reconnect_replaces_stale_timeframe_cache(self):
        bridge = scene_server.SignalBridge()
        bridge._consume({"data": [
            event(50, "2026-09-06T12:00:00Z", 999.0, revision=500),
        ]})
        replacement = {}
        corrected = event(50, "2026-09-06T12:00:00Z", 100.0, revision=1)
        bridge._apply(replacement, corrected)
        with bridge._lock:
            for key in list(bridge._points):
                if key[0] == "BTCUSDT" and key[1] == "5m":
                    del bridge._points[key]
            bridge._points.update(replacement)
            bridge._versions["5m"] += 1

        payload = bridge.payload("BTCUSDT", "5m", -1)
        self.assertEqual(1, len(payload["data"]))
        self.assertEqual(100.0, payload["data"][0]["data"]["outputs"][0]["value"])


if __name__ == "__main__":
    unittest.main()
