from datetime import datetime, timezone as tzone
from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings

from temba.schedules.models import Schedule
from temba.tests import MockJsonResponse, MockResponse, TembaTest
from temba.tickets.models import Topic
from temba.utils import json

from .. import modifiers
from .client import MailroomClient
from .exceptions import FlowValidationException, QueryValidationException, RequestException, URNValidationException
from .types import ContactSpec, Exclusions, Inclusions, RecipientsPreview, ScheduleSpec, URNResult


class MailroomClientTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.client = MailroomClient("http://localhost:8090", "sesame")

    def test_version(self):
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockJsonResponse(200, {"version": "5.3.4"})
            version = self.client.version()

        self.assertEqual("5.3.4", version)

    @patch("requests.post")
    def test_android_event(self, mock_post):
        mock_post.return_value = MockJsonResponse(200, {"id": 12345})
        response = self.client.android_event(
            org=self.org,
            channel=self.channel,
            phone="+1234567890",
            event_type="mo_miss",
            extra={"duration": 45},
            occurred_on=datetime(2024, 4, 1, 16, 28, 30, 0, tzone.utc),
        )

        self.assertEqual({"id": 12345}, response)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/android/event",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "org_id": self.org.id,
                "channel_id": self.channel.id,
                "phone": "+1234567890",
                "event_type": "mo_miss",
                "extra": {"duration": 45},
                "occurred_on": "2024-04-01T16:28:30+00:00",
            },
        )

    @patch("requests.post")
    def test_android_message(self, mock_post):
        mock_post.return_value = MockJsonResponse(200, {"id": 12345})
        response = self.client.android_message(
            org=self.org,
            channel=self.channel,
            phone="+1234567890",
            text="hello",
            received_on=datetime(2024, 4, 1, 16, 28, 30, 0, tzone.utc),
        )

        self.assertEqual({"id": 12345}, response)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/android/message",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "org_id": self.org.id,
                "channel_id": self.channel.id,
                "phone": "+1234567890",
                "text": "hello",
                "received_on": "2024-04-01T16:28:30+00:00",
            },
        )

    @patch("requests.post")
    def test_contact_create(self, mock_post):
        ann = self.create_contact("Ann", urns=["tel:+12340000001"])
        bob = self.create_contact("Bob", urns=["tel:+12340000002"])
        mock_post.return_value = MockJsonResponse(200, {"contact": {"id": ann.id, "name": "Bob", "language": ""}})

        # try with empty contact spec
        result = self.client.contact_create(
            self.org, self.admin, ContactSpec(name="", language="", status="", urns=[], fields={}, groups=[])
        )

        self.assertEqual(ann, result)
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/create",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "org_id": self.org.id,
                "user_id": self.admin.id,
                "contact": {"name": "", "language": "", "status": "", "urns": [], "fields": {}, "groups": []},
            },
        )

        mock_post.reset_mock()
        mock_post.return_value = MockJsonResponse(200, {"contact": {"id": bob.id, "name": "Bob", "language": "eng"}})

        result = self.client.contact_create(
            self.org,
            self.admin,
            ContactSpec(
                name="Bob",
                language="eng",
                status="active",
                urns=["tel:+123456789"],
                fields={"age": "39", "gender": "M"},
                groups=["d5b1770f-0fb6-423b-86a0-b4d51096b99a"],
            ),
        )

        self.assertEqual(bob, result)
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/create",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "org_id": self.org.id,
                "user_id": self.admin.id,
                "contact": {
                    "name": "Bob",
                    "language": "eng",
                    "status": "active",
                    "urns": ["tel:+123456789"],
                    "fields": {"age": "39", "gender": "M"},
                    "groups": ["d5b1770f-0fb6-423b-86a0-b4d51096b99a"],
                },
            },
        )

    @patch("requests.post")
    def test_contact_deindex(self, mock_post):
        ann = self.create_contact("Ann", urns=["tel:+12340000001"])
        bob = self.create_contact("Bob", urns=["tel:+12340000002"])
        mock_post.return_value = MockJsonResponse(200, {"deindexed": 2})
        response = self.client.contact_deindex(self.org, [ann, bob])

        self.assertEqual({"deindexed": 2}, response)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/deindex",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={"org_id": self.org.id, "contact_ids": [ann.id, bob.id]},
        )

    @patch("requests.post")
    def test_contact_export(self, mock_post):
        group = self.create_group("Doctors", contacts=[])
        mock_post.return_value = MockJsonResponse(200, {"contact_ids": [123, 234]})

        result = self.client.contact_export(self.org, group, "age = 42")

        self.assertEqual([123, 234], result)
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/export",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={"org_id": self.org.id, "group_id": group.id, "query": "age = 42"},
        )

    @patch("requests.post")
    def test_contact_export_preview(self, mock_post):
        group = self.create_group("Doctors", contacts=[])
        mock_post.return_value = MockJsonResponse(200, {"total": 123})

        result = self.client.contact_export_preview(self.org, group, "age = 42")

        self.assertEqual(123, result)
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/export_preview",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={"org_id": self.org.id, "group_id": group.id, "query": "age = 42"},
        )

    @patch("requests.post")
    def test_contact_inspect(self, mock_post):
        ann = self.create_contact("Ann", urns=["tel:+12340000001"])
        bob = self.create_contact("Bob", urns=["tel:+12340000002"])
        mock_post.return_value = MockJsonResponse(200, {ann.id: {}, bob.id: {}})

        result = self.client.contact_inspect(self.org, [ann, bob])

        self.assertEqual({ann: {}, bob: {}}, result)
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/inspect",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={"org_id": self.org.id, "contact_ids": [ann.id, bob.id]},
        )

    @patch("requests.post")
    def test_contact_interrupt(self, mock_post):
        ann = self.create_contact("Ann", urns=["tel:+12340000001"])
        mock_post.return_value = MockJsonResponse(200, {"sessions": 1})

        result = self.client.contact_interrupt(self.org, self.admin, ann)

        self.assertEqual(1, result)
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/interrupt",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={"org_id": self.org.id, "user_id": self.admin.id, "contact_id": ann.id},
        )

    @patch("requests.post")
    def test_contact_modify(self, mock_post):
        ann = self.create_contact("Ann", urns=["tel:+12340000001"])
        mock_post.return_value = MockJsonResponse(
            200,
            {
                str(ann.id): {
                    "contact": {
                        "uuid": str(ann.uuid),
                        "id": ann.id,
                        "name": "Frank",
                        "timezone": "America/Los_Angeles",
                        "created_on": "2018-07-06T12:30:00.123457Z",
                    },
                    "events": [
                        {
                            "type": "contact_groups_changed",
                            "created_on": "2018-07-06T12:30:03.123456789Z",
                            "groups_added": [{"uuid": "c153e265-f7c9-4539-9dbc-9b358714b638", "name": "Doctors"}],
                        }
                    ],
                }
            },
        )

        response = self.client.contact_modify(
            self.org,
            self.admin,
            [ann],
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
        self.assertEqual(str(ann.uuid), response[str(ann.id)]["contact"]["uuid"])
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/modify",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "org_id": self.org.id,
                "user_id": self.admin.id,
                "contact_ids": [ann.id],
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
    def test_contact_parse_query(self, mock_post):
        mock_post.return_value = MockJsonResponse(
            200, {"query": 'name ~ "frank"', "metadata": {"attributes": ["name"]}}
        )
        parsed = self.client.contact_parse_query(self.org, "frank")

        self.assertEqual('name ~ "frank"', parsed.query)
        self.assertEqual(["name"], parsed.metadata.attributes)
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/parse_query",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={"query": "frank", "org_id": self.org.id, "parse_only": False},
        )

        mock_post.return_value = MockJsonResponse(400, {"error": "no such field age"})

        with self.assertRaises(RequestException):
            self.client.contact_parse_query(self.org, "age > 10")

    @patch("requests.post")
    def test_contact_search(self, mock_post):
        group = self.create_group("Doctors", contacts=[])

        mock_post.return_value = MockJsonResponse(
            200,
            {
                "query": 'name ~ "frank"',
                "contact_ids": [1, 2],
                "total": 2,
                "metadata": {"attributes": ["name"]},
            },
        )
        response = self.client.contact_search(self.org, group, "frank", "-created_on")

        self.assertEqual('name ~ "frank"', response.query)
        self.assertEqual(["name"], response.metadata.attributes)
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/search",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "query": "frank",
                "org_id": self.org.id,
                "group_id": group.id,
                "exclude_ids": (),
                "sort": "-created_on",
                "offset": 0,
                "limit": 50,
            },
        )

    @patch("requests.post")
    def test_contact_urns(self, mock_post):
        mock_post.return_value = MockJsonResponse(
            200, {"urns": [{"normalized": "tel:+1234", "contact_id": 345}, {"normalized": "webchat:3a2ef3"}]}
        )

        response = self.client.contact_urns(self.org, ["tel:+1234", "webchat:3a2ef3"])

        self.assertEqual(
            [URNResult(normalized="tel:+1234", contact_id=345), URNResult(normalized="webchat:3a2ef3")], response
        )
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/contact/urns",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={"org_id": self.org.id, "urns": ["tel:+1234", "webchat:3a2ef3"]},
        )

    def test_flow_change_language(self):
        flow_def = {"nodes": [{"val": Decimal("1.23")}]}

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockJsonResponse(200, {"language": "spa"})
            migrated = self.client.flow_change_language(flow_def, language="spa")

            self.assertEqual({"language": "spa"}, migrated)

        call = mock_post.call_args

        self.assertEqual(("http://localhost:8090/mr/flow/change_language",), call[0])
        self.assertEqual(
            {"User-Agent": "Temba", "Authorization": "Token sesame", "Content-Type": "application/json"},
            call[1]["headers"],
        )
        self.assertEqual({"flow": flow_def, "language": "spa"}, json.loads(call[1]["data"]))

    @override_settings(TESTING=False)
    def test_flow_inspect(self):
        flow_def = {"nodes": [{"val": Decimal("1.23")}]}

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockJsonResponse(200, {"dependencies": []})
            info = self.client.flow_inspect(self.org, flow_def)

            self.assertEqual({"dependencies": []}, info)

        call = mock_post.call_args

        self.assertEqual(("http://localhost:8090/mr/flow/inspect",), call[0])
        self.assertEqual(
            {"User-Agent": "Temba", "Authorization": "Token sesame", "Content-Type": "application/json"},
            call[1]["headers"],
        )
        self.assertEqual({"org_id": self.org.id, "flow": flow_def}, json.loads(call[1]["data"]))

    def test_flow_migrate(self):
        flow_def = {"nodes": [{"val": Decimal("1.23")}]}

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockJsonResponse(200, {"name": "Migrated!"})
            migrated = self.client.flow_migrate(flow_def, to_version="13.1.0")

            self.assertEqual({"name": "Migrated!"}, migrated)

        call = mock_post.call_args

        self.assertEqual(("http://localhost:8090/mr/flow/migrate",), call[0])
        self.assertEqual(
            {"User-Agent": "Temba", "Authorization": "Token sesame", "Content-Type": "application/json"},
            call[1]["headers"],
        )
        self.assertEqual({"flow": flow_def, "to_version": "13.1.0"}, json.loads(call[1]["data"]))

    def test_flow_start_preview(self):
        flow = self.create_flow("Test Flow")

        with patch("requests.post") as mock_post:
            mock_resp = {"query": 'group = "Farmers" AND status = "active"', "total": 2345}
            mock_post.return_value = MockJsonResponse(200, mock_resp)
            preview = self.client.flow_start_preview(
                self.org,
                flow,
                include=Inclusions(
                    group_uuids=["1e42a9dd-3683-477d-a3d8-19db951bcae0"],
                    contact_uuids=["ad32f9a9-e26e-4628-b39b-a54f177abea8"],
                ),
                exclude=Exclusions(non_active=True, not_seen_since_days=30),
            )

            self.assertEqual(RecipientsPreview(query='group = "Farmers" AND status = "active"', total=2345), preview)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/flow/start_preview",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "org_id": self.org.id,
                "flow_id": flow.id,
                "include": {
                    "group_uuids": ["1e42a9dd-3683-477d-a3d8-19db951bcae0"],
                    "contact_uuids": ["ad32f9a9-e26e-4628-b39b-a54f177abea8"],
                    "query": "",
                },
                "exclude": {
                    "non_active": True,
                    "in_a_flow": False,
                    "started_previously": False,
                    "not_seen_since_days": 30,
                },
            },
        )

    @patch("requests.post")
    def test_msg_broadcast(self, mock_post):
        ann = self.create_contact("Ann", urns=["tel:+12340000001"])
        bob = self.create_contact("Bob", urns=["tel:+12340000002"])
        group = self.create_group("Doctors", contacts=[])
        optin = self.create_optin("Cat Facts")
        bcast = self.create_broadcast(self.admin, {"eng": {"text": "Hello"}}, groups=[group])
        template = self.create_template("reminder", [])

        mock_post.return_value = MockJsonResponse(200, {"id": bcast.id})
        result = self.client.msg_broadcast(
            self.org,
            self.admin,
            {"eng": {"text": "Hello"}},
            "eng",
            [group],
            [ann, bob],
            ["tel:1234"],
            "age > 20",
            "",
            Exclusions(in_a_flow=True),
            optin,
            template,
            ["@contact"],
            ScheduleSpec(start="2024-06-20T16:23:30Z", repeat_period=Schedule.REPEAT_DAILY),
        )

        self.assertEqual(bcast, result)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/msg/broadcast",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "org_id": self.org.id,
                "user_id": self.admin.id,
                "translations": {"eng": {"text": "Hello"}},
                "base_language": "eng",
                "group_ids": [group.id],
                "contact_ids": [ann.id, bob.id],
                "urns": ["tel:1234"],
                "query": "age > 20",
                "node_uuid": "",
                "exclude": {
                    "in_a_flow": True,
                    "non_active": False,
                    "not_seen_since_days": 0,
                    "started_previously": False,
                },
                "optin_id": optin.id,
                "template_id": template.id,
                "template_variables": ["@contact"],
                "schedule": {"start": "2024-06-20T16:23:30Z", "repeat_period": "D", "repeat_days_of_week": None},
            },
        )

    @patch("requests.post")
    def test_msg_broadcast_preview(self, mock_post):
        mock_resp = {"query": 'group = "Farmers" AND status = "active"', "total": 2345}
        mock_post.return_value = MockJsonResponse(200, mock_resp)
        preview = self.client.msg_broadcast_preview(
            self.org,
            include=Inclusions(
                group_uuids=["1e42a9dd-3683-477d-a3d8-19db951bcae0"],
                contact_uuids=["ad32f9a9-e26e-4628-b39b-a54f177abea8"],
            ),
            exclude=Exclusions(non_active=True, not_seen_since_days=30),
        )

        self.assertEqual(RecipientsPreview(query='group = "Farmers" AND status = "active"', total=2345), preview)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/msg/broadcast_preview",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "org_id": self.org.id,
                "include": {
                    "group_uuids": ["1e42a9dd-3683-477d-a3d8-19db951bcae0"],
                    "contact_uuids": ["ad32f9a9-e26e-4628-b39b-a54f177abea8"],
                    "query": "",
                },
                "exclude": {
                    "non_active": True,
                    "in_a_flow": False,
                    "started_previously": False,
                    "not_seen_since_days": 30,
                },
            },
        )

    @patch("requests.post")
    def test_msg_handle(self, mock_post):
        ann = self.create_contact("Ann", urns=["tel:+12340000001"])
        msg1 = self.create_incoming_msg(ann, "Hi")
        msg2 = self.create_incoming_msg(ann, "Hi again")
        mock_post.return_value = MockJsonResponse(200, {"msg_ids": [msg1.id]})
        response = self.client.msg_handle(self.org, [msg1, msg2])

        self.assertEqual({"msg_ids": [msg1.id]}, response)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/msg/handle",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={"org_id": self.org.id, "msg_ids": [msg1.id, msg2.id]},
        )

    @patch("requests.post")
    def test_msg_resend(self, mock_post):
        ann = self.create_contact("Ann", urns=["tel:+12340000001"])
        msg1 = self.create_outgoing_msg(ann, "Hi")
        msg2 = self.create_outgoing_msg(ann, "Hi again")
        mock_post.return_value = MockJsonResponse(200, {"msg_ids": [msg1.id]})
        response = self.client.msg_resend(self.org, msgs=[msg1, msg2])

        self.assertEqual({"msg_ids": [msg1.id]}, response)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/msg/resend",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={"org_id": self.org.id, "msg_ids": [msg1.id, msg2.id]},
        )

    @patch("requests.post")
    def test_msg_send(self, mock_post):
        ann = self.create_contact("Ann", urns=["tel:+12340000001"])
        ticket = self.create_ticket(ann)
        mock_post.return_value = MockJsonResponse(200, {"id": 12345})
        response = self.client.msg_send(self.org, self.admin, ann, "hi", [], ticket)

        self.assertEqual({"id": 12345}, response)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/msg/send",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "org_id": self.org.id,
                "user_id": self.admin.id,
                "contact_id": ann.id,
                "text": "hi",
                "attachments": [],
                "ticket_id": ticket.id,
            },
        )

    @patch("requests.post")
    def test_org_deindex(self, mock_post):
        mock_post.return_value = MockJsonResponse(200, {})
        response = self.client.org_deindex(self.org)

        self.assertEqual({}, response)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/org/deindex",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={"org_id": self.org.id},
        )

    def test_po_export(self):
        flow1 = self.create_flow("Flow 1")
        flow2 = self.create_flow("Flow 2")

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, 'msgid "Red"\nmsgstr "Rojo"\n\n')
            response = self.client.po_export(self.org, [flow1, flow2], "spa")

        self.assertEqual(b'msgid "Red"\nmsgstr "Rojo"\n\n', response)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/po/export",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={"org_id": self.org.id, "flow_ids": [flow1.id, flow2.id], "language": "spa"},
        )

    def test_po_import(self):
        flow1 = self.create_flow("Flow 1")
        flow2 = self.create_flow("Flow 2")

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockJsonResponse(200, {"flows": []})
            response = self.client.po_import(self.org, [flow1, flow2], "spa", b'msgid "Red"\nmsgstr "Rojo"\n\n')

        self.assertEqual({"flows": []}, response)

        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/po/import",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            data={"org_id": self.org.id, "flow_ids": [flow1.id, flow2.id], "language": "spa"},
            files={"po": b'msgid "Red"\nmsgstr "Rojo"\n\n'},
        )

    @patch("requests.post")
    def test_ticket_assign(self, mock_post):
        ann = self.create_contact("Ann", urns=["tel:+12340000001"])
        bob = self.create_contact("Bob", urns=["tel:+12340000002"])
        ticket1 = self.create_ticket(ann)
        ticket2 = self.create_ticket(bob)

        mock_post.return_value = MockJsonResponse(200, {"changed_ids": [ticket1.id]})
        response = self.client.ticket_assign(self.org, self.admin, [ticket1, ticket2], self.agent)

        self.assertEqual({"changed_ids": [ticket1.id]}, response)
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/ticket/assign",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "org_id": self.org.id,
                "user_id": self.admin.id,
                "ticket_ids": [ticket1.id, ticket2.id],
                "assignee_id": self.agent.id,
            },
        )

    @patch("requests.post")
    def test_ticket_add_note(self, mock_post):
        ann = self.create_contact("Ann", urns=["tel:+12340000001"])
        bob = self.create_contact("Bob", urns=["tel:+12340000002"])
        ticket1 = self.create_ticket(ann)
        ticket2 = self.create_ticket(bob)

        mock_post.return_value = MockJsonResponse(200, {"changed_ids": [ticket1.id]})
        response = self.client.ticket_add_note(self.org, self.admin, [ticket1, ticket2], "please handle")

        self.assertEqual({"changed_ids": [ticket1.id]}, response)
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/ticket/add_note",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "org_id": self.org.id,
                "user_id": self.admin.id,
                "ticket_ids": [ticket1.id, ticket2.id],
                "note": "please handle",
            },
        )

    @patch("requests.post")
    def test_ticket_change_topic(self, mock_post):
        ann = self.create_contact("Ann", urns=["tel:+12340000001"])
        bob = self.create_contact("Bob", urns=["tel:+12340000002"])
        ticket1 = self.create_ticket(ann)
        ticket2 = self.create_ticket(bob)
        topic = Topic.create(self.org, self.admin, "Support")

        mock_post.return_value = MockJsonResponse(200, {"changed_ids": [ticket1.id]})
        response = self.client.ticket_change_topic(self.org, self.admin, [ticket1, ticket2], topic)

        self.assertEqual({"changed_ids": [ticket1.id]}, response)
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/ticket/change_topic",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "org_id": self.org.id,
                "user_id": self.admin.id,
                "ticket_ids": [ticket1.id, ticket2.id],
                "topic_id": topic.id,
            },
        )

    @patch("requests.post")
    def test_ticket_close(self, mock_post):
        ann = self.create_contact("Ann", urns=["tel:+12340000001"])
        bob = self.create_contact("Bob", urns=["tel:+12340000002"])
        ticket1 = self.create_ticket(ann)
        ticket2 = self.create_ticket(bob)

        mock_post.return_value = MockJsonResponse(200, {"changed_ids": [ticket1.id]})
        response = self.client.ticket_close(self.org, self.admin, [ticket1, ticket2], force=True)

        self.assertEqual({"changed_ids": [ticket1.id]}, response)
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/ticket/close",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "org_id": self.org.id,
                "user_id": self.admin.id,
                "ticket_ids": [ticket1.id, ticket2.id],
                "force": True,
            },
        )

    @patch("requests.post")
    def test_ticket_reopen(self, mock_post):
        ann = self.create_contact("Ann", urns=["tel:+12340000001"])
        bob = self.create_contact("Bob", urns=["tel:+12340000002"])
        ticket1 = self.create_ticket(ann)
        ticket2 = self.create_ticket(bob)

        mock_post.return_value = MockJsonResponse(200, {"changed_ids": [ticket1.id]})
        response = self.client.ticket_reopen(self.org, self.admin, [ticket1, ticket2])

        self.assertEqual({"changed_ids": [ticket1.id]}, response)
        mock_post.assert_called_once_with(
            "http://localhost:8090/mr/ticket/reopen",
            headers={"User-Agent": "Temba", "Authorization": "Token sesame"},
            json={
                "org_id": self.org.id,
                "user_id": self.admin.id,
                "ticket_ids": [ticket1.id, ticket2.id],
            },
        )

    @patch("requests.post")
    def test_errors(self, mock_post):
        group = self.create_group("Doctors", contacts=[])

        mock_post.return_value = MockJsonResponse(422, {"error": "node isn't valid", "code": "flow:invalid"})

        with self.assertRaises(FlowValidationException) as e:
            self.client.flow_inspect(self.org, {})

        self.assertEqual("node isn't valid", e.exception.error)
        self.assertEqual("node isn't valid", str(e.exception))

        mock_post.return_value = MockJsonResponse(
            422, {"error": "no such field age", "code": "query:unknown_property", "extra": {"property": "age"}}
        )

        with self.assertRaises(QueryValidationException) as e:
            self.client.contact_search(self.org, group, "age > 10", "-created_on")

        self.assertEqual("no such field age", e.exception.error)
        self.assertEqual("unknown_property", e.exception.code)
        self.assertEqual({"property": "age"}, e.exception.extra)
        self.assertEqual("Can't resolve 'age' to a field or URN scheme.", str(e.exception))

        mock_post.return_value = MockJsonResponse(
            422, {"error": "URN 1 is taken", "code": "urn:taken", "extra": {"index": 1}}
        )

        with self.assertRaises(URNValidationException) as e:
            self.client.contact_create(
                self.org,
                self.admin,
                ContactSpec(name="Bob", language="eng", status="active", urns=["tel:+123456789"], fields={}, groups=[]),
            )

        self.assertEqual("URN 1 is taken", e.exception.error)
        self.assertEqual("taken", e.exception.code)
        self.assertEqual(1, e.exception.index)
        self.assertEqual("URN 1 is taken", str(e.exception))

        mock_post.return_value = MockJsonResponse(500, {"error": "error loading fields"})

        with self.assertRaises(RequestException) as e:
            self.client.contact_search(self.org, group, "age > 10", "-created_on")

        self.assertEqual("error loading fields", e.exception.error)

        mock_post.return_value = MockResponse(502, "Bad Gateway")

        with self.assertRaises(RequestException) as e:
            self.client.contact_search(self.org, group, "age > 10", "-created_on")

        self.assertEqual("Bad Gateway", e.exception.error)


class QueryExceptionTest(TembaTest):
    def test_str(self):
        tests = (
            (
                QueryValidationException("mismatched input '$' expecting {'(', TEXT, STRING}", "syntax"),
                "Invalid query syntax.",
            ),
            (
                QueryValidationException("can't convert 'XZ' to a number", "invalid_number", {"value": "XZ"}),
                "Unable to convert 'XZ' to a number.",
            ),
            (
                QueryValidationException("can't convert 'AB' to a date", "invalid_date", {"value": "AB"}),
                "Unable to convert 'AB' to a date.",
            ),
            (
                QueryValidationException(
                    "'Cool Kids' is not a valid group name", "invalid_group", {"value": "Cool Kids"}
                ),
                "'Cool Kids' is not a valid group name.",
            ),
            (
                QueryValidationException(
                    "'zzzzzz' is not a valid language code", "invalid_language", {"value": "zzzz"}
                ),
                "'zzzz' is not a valid language code.",
            ),
            (
                QueryValidationException(
                    "contains operator on name requires token of minimum length 2",
                    "invalid_partial_name",
                    {"min_token_length": "2"},
                ),
                "Using ~ with name requires token of at least 2 characters.",
            ),
            (
                QueryValidationException(
                    "contains operator on URN requires value of minimum length 3",
                    "invalid_partial_urn",
                    {"min_value_length": "3"},
                ),
                "Using ~ with URN requires value of at least 3 characters.",
            ),
            (
                QueryValidationException(
                    "contains conditions can only be used with name or URN values",
                    "unsupported_contains",
                    {"property": "uuid"},
                ),
                "Can only use ~ with name or URN values.",
            ),
            (
                QueryValidationException(
                    "comparisons with > can only be used with date and number fields",
                    "unsupported_comparison",
                    {"property": "uuid", "operator": ">"},
                ),
                "Can only use > with number or date values.",
            ),
            (
                QueryValidationException(
                    "can't check whether 'uuid' is set or not set",
                    "unsupported_setcheck",
                    {"property": "uuid", "operator": "!="},
                ),
                "Can't check whether 'uuid' is set or not set.",
            ),
            (
                QueryValidationException(
                    "can't resolve 'beers' to attribute, scheme or field", "unknown_property", {"property": "beers"}
                ),
                "Can't resolve 'beers' to a field or URN scheme.",
            ),
            (
                QueryValidationException("unknown property type 'xxx'", "unknown_property_type", {"type": "xxx"}),
                "Prefixes must be 'fields' or 'urns'.",
            ),
            (
                QueryValidationException("cannot query on redacted URNs", "redacted_urns", {}),
                "Can't query on URNs in an anonymous workspace.",
            ),
            (QueryValidationException("no code here", "", {}), "no code here"),
        )

        for exception, expected in tests:
            self.assertEqual(expected, str(exception))
