from collections import namedtuple
from unittest.mock import patch

from requests import HTTPError
from requests.structures import CaseInsensitiveDict

from django.utils.encoding import force_bytes, force_str

from temba.utils import json


def mock_object(type_name, **attrs):
    return namedtuple(type_name, attrs.keys())(*attrs.values())


class MockResponse:
    """
    MockResponse is a utility class that mimics the requests library response object for use
    in unit tests and mocks.
    """

    def __init__(self, status_code: int, body, method="GET", url="http://foo.com/", headers=None):
        if headers is None:
            headers = {}

        # convert dictionaries to json if the body is passed that way
        if isinstance(body, dict):
            body = json.dumps(body)

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
        self.raw = mock_object("MockRaw", version="1.1", status=status_code, headers=headers)
        self.reason = ""

        # mock up a request object on our response as well
        self.request = mock_object("MockRequest", method=method, url=url, body="request body", headers=headers)

    def add_header(self, key, value):
        self.headers[key] = value

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code != 200:
            raise HTTPError(request=self.request, response=self)


class MockPost:
    """
    MockPost allows you to mock up a post easily within a context, initialize it with the response you want
    requests.post to return while in your block.

      with MockPost({"fields": ["name"], "query": "name = \"george\""}):
         ...

      with MockPost({"error": "invalid query"}, status=400):
         ...
    """

    def __init__(self, response, status=200):
        self.response = response
        self.status = status

    def __enter__(self):
        self.patch = patch("requests.post")
        mock = self.patch.__enter__()
        mock.return_value = MockResponse(self.status, json.dumps(self.response), method="POST")
        return mock

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self.patch.__exit__(exc_type, exc_val, exc_tb)
