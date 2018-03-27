# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import six

from cgi import parse_header, parse_multipart
from http.server import BaseHTTPRequestHandler, HTTPServer
from six.moves.urllib.parse import urlparse
from threading import Thread


class MockServerRequestHandler(BaseHTTPRequestHandler):
    """
    A simple HTTP handler which responds to a request with a matching mocked request
    """
    def _handle_request(self, method, data=None):

        if not self.server.mocked_requests:
            raise ValueError("unexpected request %s %s with no mock configured" % (method, self.path))

        mock = self.server.mocked_requests[0]
        if mock.method != method or mock.path != self.path:
            raise ValueError("expected request %s %s but received %s %s" % (mock.method, mock.path, method, self.path))

        # add some stuff to the mock from the request that the caller might want to check
        mock.requested = True
        mock.data = data

        # remove this mocked request now that it has been made
        self.server.mocked_requests = self.server.mocked_requests[1:]

        self.send_response(mock.status)
        self.send_header("Content-type", mock.content_type)
        self.end_headers()
        self.wfile.write(mock.content.encode('utf-8'))

    def do_GET(self):
        return self._handle_request('GET')

    def do_POST(self):
        ctype, pdict = parse_header(self.headers['content-type'])
        if ctype == 'multipart/form-data':
            data = parse_multipart(self.rfile, pdict)
        elif ctype == 'application/x-www-form-urlencoded':
            length = int(self.headers['content-length'])
            data = urlparse.parse_qs(self.rfile.read(length), keep_blank_values=1)
        elif ctype == 'application/json':
            length = int(self.headers['content-length'])
            data = json.loads(self.rfile.read(length))
        else:
            data = {}

        return self._handle_request('POST', data)


class MockServer(HTTPServer):
    """
    Webhook calls may call out to external HTTP servers so a instance of this server runs alongside the test suite
    and provides a mechanism for mocking requests to particular URLs
    """
    @six.python_2_unicode_compatible
    class Request(object):
        def __init__(self, method, path, content, content_type, status):
            self.method = method
            self.path = path
            self.content = content
            self.content_type = content_type
            self.status = status

            self.requested = False
            self.data = None
            self.headers = None

        def __str__(self):
            return '%s %s -> %s' % (self.method, self.path, self.content)

    def __init__(self):
        HTTPServer.__init__(self, ('localhost', 49999), MockServerRequestHandler)

        self.base_url = 'http://localhost:49999'
        self.mocked_requests = []

    def start(self):
        """
        Starts running mock server in a daemon thread which will automatically shut down when the main process exits
        """
        t = Thread(target=self.serve_forever)
        t.setDaemon(True)
        t.start()

    def mock_request(self, method, path, content, content_type, status):
        request = MockServer.Request(method, path, content, content_type, status)
        self.mocked_requests.append(request)
        return request
