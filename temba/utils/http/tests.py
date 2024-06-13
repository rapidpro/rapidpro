import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from django.utils import timezone

from temba.tests import TembaTest

from . import HttpLog, http_headers


class HttpTest(TembaTest):
    def test_http_headers(self):
        headers = http_headers(extra={"Foo": "Bar"})
        headers["Token"] = "123456"

        self.assertEqual(headers, {"User-agent": "RapidPro", "Foo": "Bar", "Token": "123456"})
        self.assertEqual(http_headers(), {"User-agent": "RapidPro"})  # check changes don't leak


class HttpLogTest(TembaTest):
    class TestHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            return

        def version_string(self):
            return "HttpLogTest/1.0"

        def date_time_string(self, timestamp=None):
            return "Fri, 26 Aug 2022 18:25:56 GMT"

    def setUp(self):
        super().setUp()

        self.server = HTTPServer(("", 0), HttpLogTest.TestHandler)
        threading.Thread(target=self.server.serve_forever).start()

    def tearDown(self):
        super().tearDown()

        self.server.shutdown()
        time.sleep(0.5)

    def test_from_response(self):
        response = requests.get(f"http://127.0.0.1:{self.server.server_port}/foo")

        t1 = timezone.now()
        t2 = t1 + timedelta(seconds=3)
        log = HttpLog.from_response(response, t1, t2)

        self.assertEqual(f"http://127.0.0.1:{self.server.server_port}/foo", log.url)
        self.assertEqual(200, log.status_code)
        self.assertEqual(
            f"GET /foo HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\nUser-Agent: python-requests/2.32.3\r\nAccept-Encoding: gzip, deflate\r\nAccept: */*\r\nConnection: keep-alive\r\n\r\n",
            log.request,
        )
        self.assertEqual(
            'HTTP/1.0 200 OK\r\nServer: HttpLogTest/1.0\r\nDate: Fri, 26 Aug 2022 18:25:56 GMT\r\nContent-type: application/json\r\n\r\n{"ok": true}',
            log.response,
        )
        self.assertEqual(0, log.retries)
        self.assertEqual(3000, log.elapsed_ms)
        self.assertEqual(t1, log.created_on)

    def test_from_request(self):
        try:
            requests.get("http://127.0.0.1:6666")
        except requests.exceptions.ConnectionError as e:
            request = e.request

        t1 = timezone.now()
        t2 = t1 + timedelta(seconds=3)
        log = HttpLog.from_request(request, t1, t2)

        self.assertEqual("http://127.0.0.1:6666/", log.url)
        self.assertEqual(0, log.status_code)
        self.assertEqual(
            "GET / HTTP/1.1\r\nHost: 127.0.0.1:6666\r\nUser-Agent: python-requests/2.32.3\r\nAccept-Encoding: gzip, deflate\r\nAccept: */*\r\nConnection: keep-alive\r\n\r\n",
            log.request,
        )
        self.assertEqual(
            "",
            log.response,
        )
        self.assertEqual(0, log.retries)
        self.assertEqual(3000, log.elapsed_ms)
        self.assertEqual(t1, log.created_on)
