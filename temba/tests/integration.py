import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import requests

from django.utils import timezone

from temba.channels.models import Channel
from temba.channels.types.external.type import ExternalType
from temba.msgs.models import Msg


class TestChannel:
    """
    This test utility installs a EX channel which points to a local server. For message sending and flows to function
    correctly you need to be running both mailroom and courier against the same database and redis instance, e.g.

    mailroom -db="postgres://temba:temba@localhost:5432/temba?sslmode=disable" -redis=redis://localhost:6379/15
    courier -db="postgres://temba:temba@localhost:5432/temba?sslmode=disable" -redis=redis://localhost:6379/15 -spool-dir="."

    """

    def __init__(self, db_channel, server, courier_url, callback):
        self.db_channel = db_channel
        self.server = server
        self.server.channel = self
        self.courier_url = courier_url
        self.callback = callback

    @classmethod
    def create(cls, org, user, courier_url, callback, country="EC", scheme="tel", address="123456", port=49999):
        server = cls.Server(port)

        config = {
            Channel.CONFIG_SEND_URL: f"{server.base_url}/send",
            ExternalType.CONFIG_SEND_METHOD: "POST",
            ExternalType.CONFIG_CONTENT_TYPE: "application/json",
            ExternalType.CONFIG_SEND_BODY: '{"text": "{{text}}"}',
        }

        db_channel = Channel.add_config_external_channel(
            org, user, country, address, "EX", config, "SR", [scheme], name="Test Channel"
        )

        return cls(db_channel, server, courier_url, callback)

    def incoming(self, sender, text):
        webhook = f"{self.courier_url}/c/ex/{str(self.db_channel.uuid)}/receive"
        response = requests.post(webhook, data={"from": sender, "text": text, "date": timezone.now().isoformat()})

        if response.status_code != 200:
            raise ValueError(f"courier returned non-200 response: {response.content}")

        payload = response.json()
        return Msg.objects.get(uuid=payload["data"][0]["msg_uuid"])

    def handle_outgoing(self, data):
        return self.callback(data) or "OK"

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

        def log_message(self, format, *args):
            pass
