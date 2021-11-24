from decimal import Decimal
from unittest.mock import patch

from django_redis import get_redis_connection

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.channels.models import ChannelEvent, ChannelLog
from temba.flows.models import FlowRun, FlowStart
from temba.ivr.models import IVRCall
from temba.mailroom.client import ContactSpec, MailroomException, get_client
from temba.msgs.models import Broadcast, Msg
from temba.tests import MockResponse, TembaTest, matchers, mock_mailroom
from temba.tests.engine import MockSessionWriter
from temba.tickets.models import Ticketer, TicketEvent
from temba.utils import json

from . import modifiers, queue_interrupt
from .events import Event


class MailroomClientTest(TembaTest):
    def test_version(self):
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, '{"version": "5.3.4"}')
            version = get_client().version()

        self.assertEqual("5.3.4", version)

    def test_expression_migrate(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"migrated": "@fields.age"}')
            migrated = get_client().expression_migrate("@contact.age")

            self.assertEqual("@fields.age", migrated)

            mock_post.assert_called_once_with(
                "http://localhost:8090/mr/expression/migrate",
                headers={"User-Agent": "Temba"},
                json={"expression": "@contact.age"},
            )

            # in case of error just return original
            mock_post.return_value = MockResponse(422, '{"error": "bad isn\'t a thing"}')
            migrated = get_client().expression_migrate("@(bad)")

            self.assertEqual("@(bad)", migrated)

    def test_flow_migrate(self):
        flow_def = {"nodes": [{"val": Decimal("1.23")}]}

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"name": "Migrated!"}')
            migrated = get_client().flow_migrate(flow_def, to_version="13.1.0")

            self.assertEqual({"name": "Migrated!"}, migrated)

        call = mock_post.call_args

        self.assertEqual(("http://localhost:8090/mr/flow/migrate",), call[0])
        self.assertEqual({"User-Agent": "Temba", "Content-Type": "application/json"}, call[1]["headers"])
        self.assertEqual({"flow": flow_def, "to_version": "13.1.0"}, json.loads(call[1]["data"]))

    @override_settings(TESTING=False)
    def test_flow_inspect(self):
        flow_def = {"nodes": [{"val": Decimal("1.23")}]}

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"dependencies":[]}')
            info = get_client().flow_inspect(self.org.id, flow_def)

            self.assertEqual({"dependencies": []}, info)

        call = mock_post.call_args

        self.assertEqual(("http://localhost:8090/mr/flow/inspect",), call[0])
        self.assertEqual({"User-Agent": "Temba", "Content-Type": "application/json"}, call[1]["headers"])
        self.assertEqual({"org_id": self.org.id, "flow": flow_def}, json.loads(call[1]["data"]))

    def test_flow_change_language(self):
        flow_def = {"nodes": [{"val": Decimal("1.23")}]}

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"language": "spa"}')
            migrated = get_client().flow_change_language(flow_def, language="spa")

            self.assertEqual({"language": "spa"}, migrated)

        call = mock_post.call_args

        self.assertEqual(("http://localhost:8090/mr/flow/change_language",), call[0])
        self.assertEqual({"User-Agent": "Temba", "Content-Type": "application/json"}, call[1]["headers"])
        self.assertEqual({"flow": flow_def, "language": "spa"}, json.loads(call[1]["data"]))

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

    @patch("requests.post")
    def test_msg_resend(self, mock_post):
        mock_post.return_value = MockResponse(200, '{"msg_ids": [12345]}')
        response = get_client().msg_resend(org_id=self.org.id, msg_ids=[12345, 67890])

        self.assertEqual({"msg_ids": [12345]}, response)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/msg/resend",
            headers={"User-Agent": "Temba"},
            json={"org_id": self.org.id, "msg_ids": [12345, 67890]},
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

    @patch("requests.post")
    def test_parse_query(self, mock_post):
        mock_post.return_value = MockResponse(200, '{"query":"name ~ \\"frank\\"","fields":["name"]}')
        response = get_client().parse_query(self.org.id, "frank")

        self.assertEqual('name ~ "frank"', response["query"])
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/parse_query",
            headers={"User-Agent": "Temba"},
            json={"query": "frank", "org_id": self.org.id, "parse_only": False, "group_uuid": ""},
        )

        mock_post.return_value = MockResponse(400, '{"error":"no such field age"}')

        with self.assertRaises(MailroomException):
            get_client().parse_query(1, "age > 10")

    @patch("requests.post")
    def test_contact_create(self, mock_post):
        mock_post.return_value = MockResponse(200, '{"contact": {"id": 1234, "name": "", "language": ""}}')

        # try with empty contact spec
        response = get_client().contact_create(
            self.org.id, self.admin.id, ContactSpec(name="", language="", urns=[], fields={}, groups=[])
        )

        self.assertEqual({"id": 1234, "name": "", "language": ""}, response["contact"])
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/create",
            headers={"User-Agent": "Temba"},
            json={
                "org_id": self.org.id,
                "user_id": self.admin.id,
                "contact": {"name": "", "language": "", "urns": [], "fields": {}, "groups": []},
            },
        )

        mock_post.reset_mock()
        mock_post.return_value = MockResponse(200, '{"contact": {"id": 1234, "name": "Bob", "language": "eng"}}')

        response = get_client().contact_create(
            self.org.id,
            self.admin.id,
            ContactSpec(
                name="Bob",
                language="eng",
                urns=["tel:+123456789"],
                fields={"age": "39", "gender": "M"},
                groups=["d5b1770f-0fb6-423b-86a0-b4d51096b99a"],
            ),
        )

        self.assertEqual({"id": 1234, "name": "Bob", "language": "eng"}, response["contact"])
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/create",
            headers={"User-Agent": "Temba"},
            json={
                "org_id": self.org.id,
                "user_id": self.admin.id,
                "contact": {
                    "name": "Bob",
                    "language": "eng",
                    "urns": ["tel:+123456789"],
                    "fields": {"age": "39", "gender": "M"},
                    "groups": ["d5b1770f-0fb6-423b-86a0-b4d51096b99a"],
                },
            },
        )

    @patch("requests.post")
    def test_contact_resolve(self, mock_post):
        mock_post.return_value = MockResponse(200, '{"contact": {"id": 1234}, "urn": {"id": 2345}}')

        # try with empty contact spec
        response = get_client().contact_resolve(self.org.id, 345, "tel:+1234567890")

        self.assertEqual({"contact": {"id": 1234}, "urn": {"id": 2345}}, response)
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/resolve",
            headers={"User-Agent": "Temba"},
            json={"org_id": self.org.id, "channel_id": 345, "urn": "tel:+1234567890"},
        )

    @patch("requests.post")
    def test_contact_search(self, mock_post):
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
                "exclude_ids": (),
                "offset": 0,
                "sort": "-created_on",
            },
        )

        mock_post.return_value = MockResponse(400, '{"error":"no such field age"}')

        with self.assertRaises(MailroomException):
            get_client().contact_search(1, "2752dbbc-723f-4007-8bc5-b3720835d3a9", "age > 10", "-created_on")

    def test_ticket_assign(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"changed_ids": [123]}')
            response = get_client().ticket_assign(1, 12, [123, 345], 4, "please handle")

            self.assertEqual({"changed_ids": [123]}, response)
            mock_post.assert_called_once_with(
                "http://localhost:8090/mr/ticket/assign",
                headers={"User-Agent": "Temba"},
                json={"org_id": 1, "user_id": 12, "ticket_ids": [123, 345], "assignee_id": 4, "note": "please handle"},
            )

    def test_ticket_add_note(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"changed_ids": [123]}')
            response = get_client().ticket_add_note(1, 12, [123, 345], "please handle")

            self.assertEqual({"changed_ids": [123]}, response)
            mock_post.assert_called_once_with(
                "http://localhost:8090/mr/ticket/add_note",
                headers={"User-Agent": "Temba"},
                json={"org_id": 1, "user_id": 12, "ticket_ids": [123, 345], "note": "please handle"},
            )

    def test_ticket_change_topic(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"changed_ids": [123]}')
            response = get_client().ticket_change_topic(1, 12, [123, 345], 67)

            self.assertEqual({"changed_ids": [123]}, response)
            mock_post.assert_called_once_with(
                "http://localhost:8090/mr/ticket/change_topic",
                headers={"User-Agent": "Temba"},
                json={"org_id": 1, "user_id": 12, "ticket_ids": [123, 345], "topic_id": 67},
            )

    def test_ticket_close(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"changed_ids": [123]}')
            response = get_client().ticket_close(1, 12, [123, 345], force=True)

            self.assertEqual({"changed_ids": [123]}, response)
            mock_post.assert_called_once_with(
                "http://localhost:8090/mr/ticket/close",
                headers={"User-Agent": "Temba"},
                json={"org_id": 1, "user_id": 12, "ticket_ids": [123, 345], "force": True},
            )

    def test_ticket_reopen(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"changed_ids": [123]}')
            response = get_client().ticket_reopen(1, 12, [123, 345])

            self.assertEqual({"changed_ids": [123]}, response)
            mock_post.assert_called_once_with(
                "http://localhost:8090/mr/ticket/reopen",
                headers={"User-Agent": "Temba"},
                json={"org_id": 1, "user_id": 12, "ticket_ids": [123, 345]},
            )

    def test_request_failure(self):
        flow = self.get_flow("color")

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(400, '{"errors":["Bad request", "Doh!"]}')

            with self.assertRaises(MailroomException) as e:
                get_client().flow_migrate(flow.get_definition())

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

    @mock_mailroom(queue=False)
    def test_queue_msg_handling(self, mr_mocks):
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
                    "new_contact": False,
                },
                "queued_on": matchers.ISODate(),
            },
        )

    @mock_mailroom(queue=False)
    def test_queue_mo_miss_event(self, mr_mocks):
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
                    "new_contact": False,
                    "occurred_on": matchers.ISODate(),
                    "org_id": event.contact.org.id,
                    "urn_id": event.contact.urns.get().id,
                },
                "queued_on": matchers.ISODate(),
            },
        )

    def test_queue_broadcast(self):
        jim = self.create_contact("Jim", phone="+12065551212")
        bobs = self.create_group("Bobs", [self.create_contact("Bob", phone="+12065551313")])
        ticketer = Ticketer.create(self.org, self.admin, "mailgun", "Support Tickets", {})
        ticket = self.create_ticket(ticketer, jim, "Help!")

        bcast = Broadcast.create(
            self.org,
            self.admin,
            {"eng": "Welcome to mailroom!", "spa": "¡Bienvenidx a mailroom!"},
            groups=[bobs],
            contacts=[jim],
            urns=["tel:+12065556666"],
            base_language="eng",
            ticket=ticket,
        )

        bcast.send_async()

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
                    "urns": ["tel:+12065556666"],
                    "contact_ids": [jim.id],
                    "group_ids": [bobs.id],
                    "broadcast_id": bcast.id,
                    "org_id": self.org.id,
                    "ticket_id": ticket.id,
                },
                "queued_on": matchers.ISODate(),
            },
        )

    def test_queue_flow_start(self):
        flow = self.get_flow("favorites")
        jim = self.create_contact("Jim", phone="+12065551212")
        bobs = self.create_group("Bobs", [self.create_contact("Bob", phone="+12065551313")])

        start = FlowStart.create(
            flow,
            self.admin,
            groups=[bobs],
            contacts=[jim],
            urns=["tel:+1234567890", "twitter:bobby"],
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
                    "created_by_id": self.admin.id,
                    "flow_id": flow.id,
                    "flow_type": "M",
                    "contact_ids": [jim.id],
                    "group_ids": [bobs.id],
                    "urns": ["tel:+1234567890", "twitter:bobby"],
                    "query": None,
                    "restart_participants": True,
                    "include_active": True,
                    "extra": {"foo": "bar"},
                },
                "queued_on": matchers.ISODate(),
            },
        )

    def test_queue_contact_import_batch(self):
        imp = self.create_contact_import("media/test_imports/simple.xlsx")
        imp.start()

        self.assert_org_queued(self.org, "batch")
        self.assert_queued_batch_task(
            self.org,
            {
                "type": "import_contact_batch",
                "org_id": self.org.id,
                "task": {"contact_import_batch_id": imp.batches.get().id},
                "queued_on": matchers.ISODate(),
            },
        )

    def test_queue_interrupt_by_contacts(self):
        jim = self.create_contact("Jim", phone="+12065551212")
        bob = self.create_contact("Bob", phone="+12065551313")

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
        self.channel.release(self.admin)

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
        flow.archive(self.admin)

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
        jim = self.create_contact("Jim", phone="+12065551212")

        flow = self.get_flow("favorites")
        flow_nodes = flow.get_definition()["nodes"]
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


class EventTest(TembaTest):
    def test_from_msg(self):
        contact1 = self.create_contact("Jim", phone="0979111111")
        contact2 = self.create_contact("Bob", phone="0979222222")

        msg_in = self.create_incoming_msg(contact1, "Hello", external_id="12345")

        self.assertEqual(
            {
                "type": "msg_received",
                "created_on": matchers.ISODate(),
                "msg": {
                    "uuid": str(msg_in.uuid),
                    "id": msg_in.id,
                    "urn": "tel:+250979111111",
                    "text": "Hello",
                    "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    "external_id": "12345",
                },
                "msg_type": "I",
                "logs_url": None,
            },
            Event.from_msg(self.org, self.admin, msg_in),
        )

        msg_out = self.create_outgoing_msg(
            contact1, "Hello", channel=self.channel, status="E", quick_replies=("yes", "no")
        )
        log = ChannelLog.objects.create(channel=self.channel, is_error=True, description="Boom", msg=msg_out)
        msg_out.refresh_from_db()

        self.assertEqual(
            {
                "type": "msg_created",
                "created_on": matchers.ISODate(),
                "msg": {
                    "uuid": str(msg_out.uuid),
                    "id": msg_out.id,
                    "urn": "tel:+250979111111",
                    "text": "Hello",
                    "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    "quick_replies": ["yes", "no"],
                },
                "status": "E",
                "logs_url": f"/channels/channellog/read/{log.id}/",
            },
            Event.from_msg(self.org, self.admin, msg_out),
        )

        ivr_out = self.create_outgoing_msg(contact1, "Hello", msg_type="V")

        self.assertEqual(
            {
                "type": "ivr_created",
                "created_on": matchers.ISODate(),
                "msg": {
                    "uuid": str(ivr_out.uuid),
                    "id": ivr_out.id,
                    "urn": "tel:+250979111111",
                    "text": "Hello",
                    "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                },
                "status": "S",
                "logs_url": None,
            },
            Event.from_msg(self.org, self.admin, ivr_out),
        )

        bcast = self.create_broadcast(self.admin, "Hi there", contacts=[contact1, contact2])
        msg_out2 = bcast.msgs.filter(contact=contact1).get()

        self.assertEqual(
            {
                "type": "broadcast_created",
                "created_on": matchers.ISODate(),
                "translations": {"base": "Hi there"},
                "base_language": "base",
                "msg": {
                    "uuid": str(msg_out2.uuid),
                    "id": msg_out2.id,
                    "urn": "tel:+250979111111",
                    "text": "Hi there",
                    "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                },
                "status": "S",
                "recipient_count": 2,
                "logs_url": None,
            },
            Event.from_msg(self.org, self.admin, msg_out2),
        )

    def test_from_flow_run(self):
        contact = self.create_contact("Jim", phone="0979111111")
        flow = self.get_flow("color_v13")
        nodes = flow.get_definition()["nodes"]
        (
            MockSessionWriter(contact, flow)
            .visit(nodes[0])
            .send_msg("What is your favorite color?", self.channel)
            .wait()
            .save()
        )
        run = contact.runs.get()

        self.assertEqual(
            {
                "type": "flow_entered",
                "created_on": matchers.ISODate(),
                "flow": {"uuid": str(flow.uuid), "name": "Colors"},
                "logs_url": None,
            },
            Event.from_flow_run(self.org, self.admin, run),
        )

        # customer support get access to logs
        self.assertEqual(
            {
                "type": "flow_entered",
                "created_on": matchers.ISODate(),
                "flow": {"uuid": str(flow.uuid), "name": "Colors"},
                "logs_url": f"/flowsession/json/{run.session.uuid}/",
            },
            Event.from_flow_run(self.org, self.customer_support, run),
        )

    def test_from_event_fire(self):
        flow = self.get_flow("color_v13")
        group = self.create_group("Reporters", contacts=[])
        registered = self.create_field("registered", "Registered", value_type="D")
        campaign = Campaign.create(self.org, self.admin, "Welcomes", group)
        event = CampaignEvent.create_flow_event(
            self.org, self.user, campaign, registered, offset=1, unit="W", flow=flow
        )
        contact = self.create_contact("Jim", phone="0979111111")
        fire = EventFire.objects.create(
            event=event,
            contact=contact,
            scheduled=timezone.now(),
            fired=timezone.now(),
            fired_result=EventFire.RESULT_FIRED,
        )

        self.assertEqual(
            {
                "type": "campaign_fired",
                "created_on": fire.fired.isoformat(),
                "campaign": {"id": campaign.id, "name": "Welcomes"},
                "campaign_event": {
                    "id": event.id,
                    "offset_display": "1 week after",
                    "relative_to": {"key": "registered", "name": "Registered"},
                },
                "fired_result": "F",
            },
            Event.from_event_fire(self.org, self.admin, fire),
        )

    def test_from_ticket_event(self):
        ticketer = Ticketer.create(self.org, self.user, "mailgun", "Email (bob@acme.com)", {})
        contact = self.create_contact("Jim", phone="0979111111")
        ticket = self.create_ticket(ticketer, contact, "Where my shoes?")

        # event with a user
        event1 = TicketEvent.objects.create(
            org=self.org,
            contact=contact,
            ticket=ticket,
            event_type=TicketEvent.TYPE_NOTE_ADDED,
            created_by=self.agent,
            note="this is important",
        )

        self.assertEqual(
            {
                "type": "ticket_note_added",
                "note": "this is important",
                "topic": None,
                "assignee": None,
                "ticket": {
                    "uuid": str(ticket.uuid),
                    "opened_on": matchers.ISODate(),
                    "closed_on": None,
                    "status": "O",
                    "topic": {"uuid": str(self.org.default_ticket_topic.uuid), "name": "General"},
                    "body": "Where my shoes?",
                    "ticketer": {"uuid": str(ticketer.uuid), "name": "Email (bob@acme.com)"},
                },
                "created_on": matchers.ISODate(),
                "created_by": {"id": self.agent.id, "first_name": "", "last_name": "", "email": "Agent@nyaruka.com"},
            },
            Event.from_ticket_event(self.org, self.user, event1),
        )

        # event without a user
        event2 = TicketEvent.objects.create(
            org=self.org, contact=contact, ticket=ticket, event_type=TicketEvent.TYPE_CLOSED
        )

        self.assertEqual(
            {
                "type": "ticket_closed",
                "note": None,
                "topic": None,
                "assignee": None,
                "ticket": {
                    "uuid": str(ticket.uuid),
                    "opened_on": matchers.ISODate(),
                    "closed_on": None,
                    "status": "O",
                    "topic": {"uuid": str(self.org.default_ticket_topic.uuid), "name": "General"},
                    "body": "Where my shoes?",
                    "ticketer": {"uuid": str(ticketer.uuid), "name": "Email (bob@acme.com)"},
                },
                "created_on": matchers.ISODate(),
                "created_by": None,
            },
            Event.from_ticket_event(self.org, self.user, event2),
        )

    def test_from_ivr_call(self):
        contact = self.create_contact("Jim", phone="0979111111")

        call1 = IVRCall.objects.create(
            org=self.org,
            contact=contact,
            status=IVRCall.STATUS_IN_PROGRESS,
            channel=self.channel,
            contact_urn=contact.urns.all().first(),
            error_count=0,
        )
        call2 = IVRCall.objects.create(
            org=self.org,
            contact=contact,
            status=IVRCall.STATUS_ERRORED,
            error_reason=IVRCall.ERROR_BUSY,
            channel=self.channel,
            contact_urn=contact.urns.all().first(),
            error_count=0,
        )

        self.assertEqual(
            {
                "type": "call_started",
                "status": "I",
                "status_display": "In Progress",
                "created_on": matchers.ISODate(),
                "logs_url": None,
            },
            Event.from_ivr_call(self.org, self.user, call1),
        )

        self.assertEqual(
            {
                "type": "call_started",
                "status": "E",
                "status_display": "Errored (Busy)",
                "created_on": matchers.ISODate(),
                "logs_url": None,
            },
            Event.from_ivr_call(self.org, self.user, call2),
        )
