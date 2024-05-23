from unittest.mock import Mock

from requests import HTTPError, Request
from requests.structures import CaseInsensitiveDict
from urllib3.response import HTTPHeaderDict, HTTPResponse

from django.utils.encoding import force_bytes, force_str

from temba.utils import json


class MockResponse:
    """
    MockResponse is a utility class that mimics the requests library response object for use
    in unit tests and mocks.
    """

    def __init__(self, status_code: int, body, method="GET", url="http://foo.com/", headers=None):
        if headers is None:
            headers = {}

        self.body = force_str(body)
        self.text = self.body
        self.content = force_bytes(self.body)
        self.status_code = status_code
        self.headers = CaseInsensitiveDict(data=headers)
        self.url = url
        self.ok = True
        self.cookies = dict()
        self.streaming = False
        self.charset = "utf-8"
        self.connection = dict()
        self.raw = Mock(HTTPResponse, version="1.1", status=status_code, headers=HTTPHeaderDict(headers))
        self.reason = ""

        # mock up a request object on our response as well
        self.request = Mock(Request, method=method, url=url, body="request body", headers=headers)

    def add_header(self, key, value):
        self.headers[key] = value

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code != 200:
            raise HTTPError(request=self.request, response=self)


class MockJsonResponse(MockResponse):
    def __init__(self, status_code: int, data):
        super().__init__(status_code, json.dumps(data), headers={"Content-Type": "application/json"})
