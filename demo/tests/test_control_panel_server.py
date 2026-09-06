import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tempfile import TemporaryDirectory


DEMO_DIR = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, DEMO_DIR)
import control_panel_server


class UpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _reply(self):
        body = json.dumps({
            "method": self.command,
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _reply
    do_POST = _reply


class ControlPanelServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        control_panel_server.SCENE_API = (
            f"http://127.0.0.1:{cls.upstream.server_address[1]}")
        control_panel_server.COMPOSITOR_API = control_panel_server.SCENE_API
        control_panel_server.UPSTREAM_TOKEN = "server-token"
        control_panel_server.TRUST_LOCAL_OPERATOR = True
        cls.upstream_thread = threading.Thread(
            target=cls.upstream.serve_forever, daemon=True)
        cls.upstream_thread.start()

        cls.control = ThreadingHTTPServer(
            ("127.0.0.1", 0), control_panel_server.Handler)
        cls.control_thread = threading.Thread(
            target=cls.control.serve_forever, daemon=True)
        cls.control_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.control.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.control.shutdown()
        cls.upstream.shutdown()
        cls.control.server_close()
        cls.upstream.server_close()

    def test_health_is_owned_by_control_server(self):
        with urllib.request.urlopen(self.base_url + "/healthz") as response:
            self.assertEqual(json.load(response), {"status": "ok"})

    def test_root_serves_operator_console(self):
        with urllib.request.urlopen(self.base_url + "/") as response:
            body = response.read().decode()
        self.assertIn("Night Shift Operator", body)
        self.assertNotIn("Night Shift Control Room", body)

    def test_api_requests_preserve_path_and_authorization(self):
        request = urllib.request.Request(
            self.base_url + "/api/control?detail=1",
            headers={"Authorization": "Bearer test-token"})
        with urllib.request.urlopen(request) as response:
            payload = json.load(response)
        self.assertEqual(payload["path"], "/api/control?detail=1")
        self.assertEqual(payload["authorization"], "Bearer test-token")

    def test_compositor_requests_strip_the_public_namespace(self):
        request = urllib.request.Request(
            self.base_url + "/compositor-api/layout?source=console",
            data=b'{"layout":"main"}', method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            payload = json.load(response)
        self.assertEqual(payload["method"], "POST")
        self.assertEqual(payload["path"], "/layout?source=console")

    def test_local_console_adds_upstream_token_server_side(self):
        with urllib.request.urlopen(self.base_url + "/api/control") as response:
            payload = json.load(response)
        self.assertEqual(payload["authorization"], "Bearer server-token")

    def test_panel_config_reports_local_access_without_exposing_token(self):
        with urllib.request.urlopen(self.base_url + "/panel-config") as response:
            payload = json.load(response)
        self.assertEqual(payload, {"local_access": True})
        self.assertNotIn("token", payload)

    def test_non_api_post_is_rejected(self):
        request = urllib.request.Request(
            self.base_url + "/index.html", data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request)
        self.assertEqual(raised.exception.code, 404)


if __name__ == "__main__":
    unittest.main()