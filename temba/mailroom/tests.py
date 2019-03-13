from unittest.mock import patch

from temba.flows.server.serialize import serialize_flow
from temba.mailroom.client import MailroomException
from temba.tests import MockResponse, TembaTest, matchers


class MailroomClientTest(TembaTest):
    @patch("requests.post")
    def test_request_failure(self, mock_post):
        mock_post.return_value = MockResponse(400, '{"errors":["Bad request", "Doh!"]}')

        flow = self.get_flow("color")

        with self.assertRaises(MailroomException) as e:
            serialize_flow(flow)

        self.assertEqual(
            e.exception.as_json(),
            {"endpoint": "flow/migrate", "request": matchers.Dict(), "response": {"errors": ["Bad request", "Doh!"]}},
        )
