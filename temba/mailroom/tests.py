from unittest.mock import patch

from django_redis import get_redis_connection

from django.utils import timezone

from temba.flows.server.serialize import serialize_flow
from temba.mailroom.client import MailroomException
from temba.msgs.models import Msg
from temba.tests import MockResponse, TembaTest, matchers
from temba.utils import json


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


class MailroomQueueTest(TembaTest):
    def test_msg_task(self):
        msg = Msg.create_relayer_incoming(self.org, self.channel, "tel:12065551212", "Hello World", timezone.now())

        # assert all looks good
        r = get_redis_connection()

        # org is queued
        self.assertEqual(1, r.zcard(f"handler:active"))
        org_task = json.loads(r.zrange(f"handler:active", 0, 1)[0])
        self.assertEqual(self.org.id, org_task)

        # contact is queued
        self.assertEqual(1, r.zcard(f"handler:{self.org.id}"))
        contact_task = json.loads(r.zrange(f"handler:{self.org.id}", 0, 1)[0])
        self.assertEqual("handle_contact_event", contact_task["type"])
        self.assertEqual(self.org.id, contact_task["org_id"])
        self.assertEqual({"contact_id": msg.contact_id}, contact_task["task"])

        # msg event is valid
        self.assertEqual(1, r.llen(f"c:{self.org.id}:{msg.contact_id}"))
        msg_task = json.loads(r.rpop(f"c:{self.org.id}:{msg.contact_id}"))
        self.assertEqual("msg_event", msg_task["type"])
        self.assertEqual(msg.contact_id, msg_task["task"]["contact_id"])
        self.assertEqual("Hello World", msg_task["task"]["text"])
        self.assertTrue(msg_task["task"]["new_contact"])
