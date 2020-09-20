from unittest.mock import patch

from django_redis import get_redis_connection

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from temba.channels.models import ChannelEvent
from temba.flows.models import FlowRun, FlowStart
from temba.mailroom.client import MailroomException, get_client
from temba.msgs.models import Broadcast, Msg
from temba.tests import MockResponse, TembaTest, matchers
from temba.tests.engine import MockSessionWriter
from temba.utils import json

from . import modifiers, queue_interrupt


class MailroomClientTest(TembaTest):
    def test_version(self):
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, '{"version": "5.3.4"}')
            version = get_client().version()

        self.assertEqual("5.3.4", version)

    def test_flow_migrate(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"name": "Migrated!"}')
            migrated = get_client().flow_migrate({"nodes": []}, to_version="13.1.0")

            self.assertEqual({"name": "Migrated!"}, migrated)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/flow/migrate",
            headers={"User-Agent": "Temba"},
            json={"flow": {"nodes": []}, "to_version": "13.1.0"},
        )

    def test_flow_change_language(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"language": "spa"}')
            migrated = get_client().flow_change_language({"nodes": []}, language="spa")

            self.assertEqual({"language": "spa"}, migrated)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/flow/change_language",
            headers={"User-Agent": "Temba"},
            json={"flow": {"nodes": []}, "language": "spa"},
        )

    def test_contact_modify(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(
                200,
                """{
                    "1": {
                        "contact": {
                            "uuid": "6393abc0-283d-4c9b-a1b3-641a035c34bf",
                            "id": 1,
                            "name": "Frank",
                            "timezone": "America/Los_Angeles",
                            "created_on": "2018-07-06T12:30:00.123457Z"
                        },
                        "events": [
                            {
                                "type": "contact_groups_changed",
                                "created_on": "2018-07-06T12:30:03.123456789Z",
                                "groups_added": [
                                    {
                                        "uuid": "c153e265-f7c9-4539-9dbc-9b358714b638",
                                        "name": "Doctors"
                                    }
                                ]
                            }
                        ]
                    }
                }
                """,
            )

            response = get_client().contact_modify(
                1,
                1,
                [1],
                [
                    modifiers.Name(name="Bob"),
                    modifiers.Language(language="fra"),
                    modifiers.Field(field=modifiers.FieldRef(key="age", name="Age"), value="43"),
                    modifiers.Status(status="blocked"),
                    modifiers.Groups(
                        groups=[modifiers.GroupRef(uuid="c153e265-f7c9-4539-9dbc-9b358714b638", name="Doctors")],
                        modification="add",
                    ),
                    modifiers.URNs(urns=["+tel+1234567890"], modification="append"),
                ],
            )
            self.assertEqual("6393abc0-283d-4c9b-a1b3-641a035c34bf", response["1"]["contact"]["uuid"])
            mock_post.assert_called_once_with(
                "http://localhost:8090/mr/contact/modify",
                headers={"User-Agent": "Temba"},
                json={
                    "org_id": 1,
                    "user_id": 1,
                    "contact_ids": [1],
                    "modifiers": [
                        {"type": "name", "name": "Bob"},
                        {"type": "language", "language": "fra"},
                        {"type": "field", "field": {"key": "age", "name": "Age"}, "value": "43"},
                        {"type": "status", "status": "blocked"},
                        {
                            "type": "groups",
                            "groups": [{"uuid": "c153e265-f7c9-4539-9dbc-9b358714b638", "name": "Doctors"}],
                            "modification": "add",
                        },
                        {"type": "urns", "urns": ["+tel+1234567890"], "modification": "append"},
                    ],
                },
            )

    def test_po_export(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, 'msgid "Red"\nmsgstr "Rojo"\n\n')
            response = get_client().po_export(self.org.id, [123, 234], "spa")

            self.assertEqual(b'msgid "Red"\nmsgstr "Rojo"\n\n', response)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/po/export",
            headers={"User-Agent": "Temba"},
            json={"org_id": self.org.id, "flow_ids": [123, 234], "language": "spa", "exclude_arguments": False},
        )

    def test_po_import(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"flows": []}')
            response = get_client().po_import(self.org.id, [123, 234], "spa", b'msgid "Red"\nmsgstr "Rojo"\n\n')

            self.assertEqual({"flows": []}, response)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/po/import",
            headers={"User-Agent": "Temba"},
            data={"org_id": self.org.id, "flow_ids": [123, 234], "language": "spa"},
            files={"po": b'msgid "Red"\nmsgstr "Rojo"\n\n'},
        )

    def test_parse_query(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"query":"name ~ \\"frank\\"","fields":["name"]}')
            response = get_client().parse_query(1, "frank")

            self.assertEqual('name ~ "frank"', response["query"])
            mock_post.assert_called_once_with(
                "http://localhost:8090/mr/contact/parse_query",
                headers={"User-Agent": "Temba"},
                json={"query": "frank", "org_id": 1, "group_uuid": ""},
            )

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(400, '{"error":"no such field age"}')

            with self.assertRaises(MailroomException):
                get_client().parse_query(1, "age > 10")

    def test_contact_search(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(
                200,
                """
                {
                  "query":"name ~ \\"frank\\"",
                  "contact_ids":[1,2],
                  "fields":["name"],
                  "total": 2,
                  "offset": 0
                }
                """,
            )
            response = get_client().contact_search(1, "2752dbbc-723f-4007-8bc5-b3720835d3a9", "frank", "-created_on")

            self.assertEqual('name ~ "frank"', response["query"])
            mock_post.assert_called_once_with(
                "http://localhost:8090/mr/contact/search",
                headers={"User-Agent": "Temba"},
                json={
                    "query": "frank",
                    "org_id": 1,
                    "group_uuid": "2752dbbc-723f-4007-8bc5-b3720835d3a9",
                    "offset": 0,
                    "sort": "-created_on",
                },
            )

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(400, '{"error":"no such field age"}')

            with self.assertRaises(MailroomException):
                get_client().contact_search(1, "2752dbbc-723f-4007-8bc5-b3720835d3a9", "age > 10", "-created_on")

    def test_ticket_close(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"changed_ids": [123]}')
            response = get_client().ticket_close(1, [123, 345])

            self.assertEqual({"changed_ids": [123]}, response)
            mock_post.assert_called_once_with(
                "http://localhost:8090/mr/ticket/close",
                headers={"User-Agent": "Temba"},
                json={"org_id": 1, "ticket_ids": [123, 345]},
            )

    def test_ticket_reopen(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"changed_ids": [123]}')
            response = get_client().ticket_reopen(1, [123, 345])

            self.assertEqual({"changed_ids": [123]}, response)
            mock_post.assert_called_once_with(
                "http://localhost:8090/mr/ticket/reopen",
                headers={"User-Agent": "Temba"},
                json={"org_id": 1, "ticket_ids": [123, 345]},
            )

    @override_settings(TESTING=False)
    def test_inspect_with_org(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"dependencies":[]}')

            get_client().flow_inspect(self.org.id, {"nodes": []})

            mock_post.assert_called_once_with(
                "http://localhost:8090/mr/flow/inspect",
                headers={"User-Agent": "Temba"},
                json={"org_id": self.org.id, "flow": {"nodes": []}},
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
                    "start_type": "M",
                    "org_id": self.org.id,
                    "created_by": self.admin.username,
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

    def test_queue_interrupt_by_session(self):
        jim = self.create_contact("Jim", "+12065551212")

        flow = self.get_flow("favorites")
        flow_nodes = flow.as_json()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[2]

        (
            MockSessionWriter(jim, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        )

        run = FlowRun.objects.get(contact=jim)
        session = run.session
        run.release("U")

        self.assert_org_queued(self.org, "batch")
        self.assert_queued_batch_task(
            self.org,
            {
                "type": "interrupt_sessions",
                "org_id": self.org.id,
                "task": {"session_ids": [session.id]},
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
