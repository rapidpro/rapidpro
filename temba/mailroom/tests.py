from unittest.mock import patch

from django_redis import get_redis_connection

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from temba.channels.models import ChannelEvent
from temba.flows.models import FlowStart
from temba.mailroom.client import FlowValidationException, MailroomException, get_client
from temba.msgs.models import Broadcast, Msg
from temba.tests import MockResponse, TembaTest, matchers
from temba.utils import json

from . import queue_interrupt


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
                get_client().flow_migrate(flow.as_json())

        self.assertEqual(
            e.exception.as_json(),
            {"endpoint": "flow/migrate", "request": matchers.Dict(), "response": {"errors": ["Bad request", "Doh!"]}},
        )

    def test_empty_expression(self):
        # empty is as empty does
        self.assertEqual("", get_client().expression_migrate(""))


class MailroomQueueTest(TembaTest):
    def setUp(self):
        super().setUp()
        r = get_redis_connection()
        r.execute_command("select", "9")
        r.execute_command("flushdb")

    def tearDown(self):
        super().tearDown()
        r = get_redis_connection()
        r.execute_command("select", settings.REDIS_DB)

    def test_queue_msg_handling(self):
        with override_settings(TESTING=False):
            msg = Msg.create_relayer_incoming(self.org, self.channel, "tel:12065551212", "Hello World", timezone.now())

        self.assert_org_queued(self.org, "handler")
        self.assert_contact_queued(msg.contact)
        self.assert_queued_handler_task(
            msg.contact,
            {
                "type": "msg_event",
                "org_id": self.org.id,
                "task": {
                    "org_id": self.org.id,
                    "channel_id": self.channel.id,
                    "contact_id": msg.contact_id,
                    "msg_id": msg.id,
                    "msg_uuid": str(msg.uuid),
                    "msg_external_id": None,
                    "urn": "tel:+12065551212",
                    "urn_id": msg.contact.urns.get().id,
                    "text": "Hello World",
                    "attachments": None,
                    "new_contact": True,
                },
                "queued_on": matchers.ISODate(),
            },
        )

    def test_queue_mo_miss_event(self):
        get_redis_connection("default").flushall()
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

        self.assert_org_queued(self.org, "handler")
        self.assert_contact_queued(event.contact)
        self.assert_queued_handler_task(
            event.contact,
            {
                "type": "mo_miss",
                "org_id": event.contact.org.id,
                "task": {
                    "channel_id": self.channel.id,
                    "contact_id": event.contact.id,
                    "event_type": "mo_miss",
                    "extra": None,
                    "id": event.id,
                    "new_contact": True,
                    "org_id": event.contact.org.id,
                    "urn": "tel:+12065551515",
                    "urn_id": event.contact.urns.get().id,
                },
                "queued_on": matchers.ISODate(),
            },
        )

    def test_queue_broadcast(self):
        jim = self.create_contact("Jim", "+12065551212")
        bobs = self.create_group("Bobs", [self.create_contact("Bob", "+12065551313")])

        bcast = Broadcast.create(
            self.org,
            self.admin,
            {"eng": "Welcome to mailroom!", "spa": "¡Bienvenidx a mailroom!"},
            groups=[bobs],
            contacts=[jim],
            urns=[jim.urns.get()],
            base_language="eng",
        )

        bcast.send()

        self.assert_org_queued(self.org, "batch")
        self.assert_queued_batch_task(
            self.org,
            {
                "type": "send_broadcast",
                "org_id": self.org.id,
                "task": {
                    "translations": {
                        "eng": {"text": "Welcome to mailroom!"},
                        "spa": {"text": "\u00a1Bienvenidx a mailroom!"},
                    },
                    "template_state": "legacy",
                    "base_language": "eng",
                    "urns": ["tel:+12065551212"],
                    "contact_ids": [jim.id],
                    "group_ids": [bobs.id],
                    "broadcast_id": bcast.id,
                    "org_id": self.org.id,
                },
                "queued_on": matchers.ISODate(),
            },
        )

    def test_queue_flow_start(self):
        flow = self.get_flow("favorites")
        jim = self.create_contact("Jim", "+12065551212")
        bobs = self.create_group("Bobs", [self.create_contact("Bob", "+12065551313")])

        start = FlowStart.create(
            flow,
            self.admin,
            groups=[bobs],
            contacts=[jim],
            restart_participants=True,
            extra={"foo": "bar"},
            include_active=True,
        )

        start.async_start()

        self.assert_org_queued(self.org, "batch")
        self.assert_queued_batch_task(
            self.org,
            {
                "type": "start_flow",
                "org_id": self.org.id,
                "task": {
                    "start_id": start.id,
                    "org_id": self.org.id,
                    "flow_id": flow.id,
                    "flow_type": "M",
                    "contact_ids": [jim.id],
                    "group_ids": [bobs.id],
                    "query": None,
                    "restart_participants": True,
                    "include_active": True,
                    "extra": {"foo": "bar"},
                },
                "queued_on": matchers.ISODate(),
            },
        )

    def test_queue_interrupt_by_contacts(self):
        jim = self.create_contact("Jim", "+12065551212")
        bob = self.create_contact("Bob", "+12065551313")

        queue_interrupt(self.org, contacts=[jim, bob])

        self.assert_org_queued(self.org, "batch")
        self.assert_queued_batch_task(
            self.org,
            {
                "type": "interrupt_sessions",
                "org_id": self.org.id,
                "task": {"contact_ids": [jim.id, bob.id]},
                "queued_on": matchers.ISODate(),
            },
        )

    def test_queue_interrupt_by_channel(self):
        self.channel.release()

        self.assert_org_queued(self.org, "batch")
        self.assert_queued_batch_task(
            self.org,
            {
                "type": "interrupt_sessions",
                "org_id": self.org.id,
                "task": {"channel_ids": [self.channel.id]},
                "queued_on": matchers.ISODate(),
            },
        )

    def test_queue_interrupt_by_flow(self):
        flow = self.get_flow("favorites")
        flow.archive()

        self.assert_org_queued(self.org, "batch")
        self.assert_queued_batch_task(
            self.org,
            {
                "type": "interrupt_sessions",
                "org_id": self.org.id,
                "task": {"flow_ids": [flow.id]},
                "queued_on": matchers.ISODate(),
            },
        )

    def assert_org_queued(self, org, queue):
        r = get_redis_connection()

        # check we have one org with active tasks
        self.assertEqual(r.zcard(f"{queue}:active"), 1)

        queued_org = json.loads(r.zrange(f"{queue}:active", 0, 1)[0])

        self.assertEqual(queued_org, org.id)

    def assert_contact_queued(self, contact):
        r = get_redis_connection()

        # check we have one contact handle event queued for its org
        self.assertEqual(r.zcard(f"handler:{contact.org.id}"), 1)

        # load and check that task
        task = json.loads(r.zrange(f"handler:{contact.org.id}", 0, 1)[0])

        self.assertEqual(
            task,
            {
                "type": "handle_contact_event",
                "org_id": contact.org.id,
                "task": {"contact_id": contact.id},
                "queued_on": matchers.ISODate(),
            },
        )

    def assert_queued_handler_task(self, contact, expected_task):
        r = get_redis_connection()

        # check we have one task in the contact's queue
        self.assertEqual(r.llen(f"c:{contact.org.id}:{contact.id}"), 1)

        # load and check that task
        actual_task = json.loads(r.rpop(f"c:{contact.org.id}:{contact.id}"))

        self.assertEqual(actual_task, expected_task)

    def assert_queued_batch_task(self, org, expected_task):
        r = get_redis_connection()

        # check we have one task in the org's queue
        self.assertEqual(r.zcard(f"batch:{org.id}"), 1)

        # load and check that task
        actual_task = json.loads(r.zrange(f"batch:{org.id}", 0, 1)[0])

        self.assertEqual(actual_task, expected_task)
