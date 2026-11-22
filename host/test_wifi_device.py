"""Test LAN transport without accessing the user's CYD or credentials."""
import http.server
import json
import threading
import unittest
from wifi_device import DashboardWiFi

KEY = "01" * 32


class Handler(http.server.BaseHTTPRequestHandler):
    received = None

    def log_message(self, *_):
        pass

    def do_POST(self):
        if self.path != "/usage" or self.headers.get("X-CYD-Key") != KEY:
            self.send_error(403)
            return
        length = int(self.headers.get("Content-Length", "0"))
        Handler.received = json.loads(self.rfile.read(length))
        data = b'{"type":"ack","message":"usage"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class WiFiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def test_payload(self):
        DashboardWiFi("127.0.0.1", KEY, port=self.server.server_port).send_json(
            {"type": "usage", "hostTime": 123, "limits": []})
        self.assertEqual(Handler.received["hostTime"], 123)

    def test_rejected_key(self):
        with self.assertRaises(OSError):
            DashboardWiFi("127.0.0.1", "02" * 32, port=self.server.server_port).send_json({"type": "usage"})

    def test_rejected_public_host(self):
        with self.assertRaises(ValueError):
            DashboardWiFi("8.8.8.8", KEY)

    def test_rejected_short_key(self):
        with self.assertRaises(ValueError):
            DashboardWiFi("192.168.1.1", "bad")


if __name__ == "__main__":
    unittest.main()
