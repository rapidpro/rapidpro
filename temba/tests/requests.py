import json
from unittest.mock import patch

from temba.tests import MockResponse


class MockRequestsPost:
    def __init__(self, response, status=200):
        self.response = response
        self.status = status

    def __enter__(self):
        self.patch = patch("requests.post")
        mock = self.patch.__enter__()
        mock.return_value = MockResponse(self.status, json.dumps(self.response))
        return mock

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self.patch.__exit__(exc_type, exc_val, exc_tb)
