from unittest.mock import patch

from django_redis import get_redis_connection

from django.test import override_settings
from django.utils import timezone

from temba.channels.models import ChannelEvent
from temba.flows.server.serialize import serialize_flow
from temba.mailroom.client import FlowValidationException, MailroomException, get_client
from temba.msgs.models import Msg
from temba.tests import MockResponse, TembaTest, matchers
from temba.utils import json


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

    def test_event_task(self):
        event = ChannelEvent.create_relayer_event(
            self.channel, "tel:12065551212", ChannelEvent.TYPE_CALL_OUT, timezone.now()
        )

        r = get_redis_connection()

        # noop, this event isn't handled by mailroom
        self.assertEqual(0, r.zcard(f"handler:active"))
        self.assertEqual(0, r.zcard(f"handler:{self.org.id}"))
        self.assertEqual(0, r.llen(f"c:{self.org.id}:{event.contact_id}"))

        event = ChannelEvent.create_relayer_event(
            self.channel, "tel:12065551515", ChannelEvent.TYPE_CALL_IN_MISSED, timezone.now()
        )

        # org is queued
        self.assertEqual(1, r.zcard(f"handler:active"))
        org_task = json.loads(r.zrange(f"handler:active", 0, 1)[0])
        self.assertEqual(self.org.id, org_task)

        # contact is queued
        self.assertEqual(1, r.zcard(f"handler:{self.org.id}"))
        contact_task = json.loads(r.zrange(f"handler:{self.org.id}", 0, 1)[0])
        self.assertEqual("handle_contact_event", contact_task["type"])
        self.assertEqual(self.org.id, contact_task["org_id"])
        self.assertEqual({"contact_id": event.contact_id}, contact_task["task"])

        # event event is valid
        self.assertEqual(1, r.llen(f"c:{self.org.id}:{event.contact_id}"))
        event_task = json.loads(r.rpop(f"c:{self.org.id}:{event.contact_id}"))
        self.assertEqual("mo_miss", event_task["type"])
        self.assertEqual(event.contact_id, event_task["task"]["contact_id"])
        self.assertEqual("tel:+12065551515", event_task["task"]["urn"])
        self.assertTrue(event_task["task"]["new_contact"])
