import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import requests

from django.utils import timezone

from temba.channels.models import Channel
from temba.msgs.models import Msg

COURIER_URL = "http://localhost:8080"


class TestChannel:
    """
    This test utility installs a EX channel which points to a local server. For message sending and flows to function
    correctly you need to be running both mailroom and courier against the same database and redis instance, e.g.

    mailroom -db="postgres://temba:temba@localhost:5432/temba?sslmode=disable" -redis=redis://localhost:6379/15 -log-level=info
    courier -db="postgres://temba:temba@localhost:5432/temba?sslmode=disable" -redis=redis://localhost:6379/15 -spool-dir="."  -log-level=info

    """

    def __init__(self, db_channel, server):
        self.db_channel = db_channel
        self.server = server
        self.server.channel = self

    @classmethod
    def create(cls, org, user, country="EC", address="123456", port=49999):
        server = cls.Server(port)

        config = {
            Channel.CONFIG_SEND_URL: f"{server.base_url}/send",
            Channel.CONFIG_SEND_METHOD: "POST",
            Channel.CONFIG_CONTENT_TYPE: "application/json",
            Channel.CONFIG_SEND_BODY: '{"text": "{{text}}"}',
        }
        return cls(
            Channel.add_config_external_channel(org, user, country, address, "EX", config, "SR", ["tel"]), server
        )

    def incoming(self, sender, text):
        response = requests.post(
            f"{COURIER_URL}/c/ex/{str(self.db_channel.uuid)}/receive",
            data={"from": sender, "text": text, "date": timezone.now().isoformat()},
        )

        if response.status_code != 200:
            raise ValueError(f"courier returned non-200 response: {response.content}")

        payload = response.json()
        return Msg.objects.get(uuid=payload["data"][0]["msg_uuid"])

    def handle_outgoing(self, data):
        print(f"[TestChannel] SENT: {data['text']}")
        return "OK"

    def release(self):
        self.db_channel.release()
        self.server.shutdown()

    class Server(HTTPServer):
        def __init__(self, port):
            HTTPServer.__init__(self, ("localhost", port), TestChannel.Handler)
            self.base_url = f"http://localhost:{port}"
            self.thread = Thread(target=self.serve_forever)
            self.thread.setDaemon(True)
            self.thread.start()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["content-length"])
            data = json.loads(self.rfile.read(length))

            response = self.server.channel.handle_outgoing(data)
            response = response.encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", len(response))
            self.end_headers()
            self.wfile.write(response)
