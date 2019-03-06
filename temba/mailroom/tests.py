from unittest.mock import patch

from django.test import override_settings

from temba.flows.server.serialize import serialize_flow
from temba.mailroom.client import FlowValidationException, MailroomException, get_client
from temba.tests import MockResponse, TembaTest, matchers


class MailroomClientTest(TembaTest):
    @override_settings(TESTING=False)
    def test_validation_failure(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(422, '{"error":"flow don\'t look right"}')

            with self.assertRaises(FlowValidationException) as e:
                get_client().flow_validate(self.org, '{"nodes:[]"}')

        self.assertEqual(str(e.exception), "flow don't look right")
        self.assertEqual(
            e.exception.as_json(),
            {
                "endpoint": "flow/validate",
                "request": {"flow": '{"nodes:[]"}', "org_id": self.org.id},
                "response": {"error": "flow don't look right"},
            },
        )

    def test_request_failure(self):
        flow = self.get_flow("color")

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(400, '{"errors":["Bad request", "Doh!"]}')

            with self.assertRaises(MailroomException) as e:
                serialize_flow(flow)

        self.assertEqual(
            e.exception.as_json(),
            {"endpoint": "flow/migrate", "request": matchers.Dict(), "response": {"errors": ["Bad request", "Doh!"]}},
        )
