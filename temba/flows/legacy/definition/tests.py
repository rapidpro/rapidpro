from temba.channels.models import Channel
from temba.flows.models import FlowException
from temba.msgs.models import Label
from temba.tests import TembaTest
from temba.utils import json
from temba.utils.uuid import uuid4

from .actions import (
    Action,
    AddLabelAction,
    AddToGroupAction,
    DeleteFromGroupAction,
    EmailAction,
    ReplyAction,
    SaveToContactAction,
    SendAction,
    SetChannelAction,
    SetLanguageAction,
    StartFlowAction,
    TriggerFlowAction,
    VariableContactAction,
)
from .rules import (
    AirtimeStatusTest,
    AndTest,
    BetweenTest,
    ContainsTest,
    EqTest,
    FalseTest,
    GteTest,
    GtTest,
    HasDistrictTest,
    HasEmailTest,
    HasStateTest,
    HasWardTest,
    LteTest,
    LtTest,
    OrTest,
    PhoneTest,
    RegexTest,
    Test,
    TrueTest,
    WebhookStatusTest,
)


class TestsTest(TembaTest):
    def test_factories(self):
        tests = [
            (AirtimeStatusTest("success"), {"type": "airtime_status", "exit_status": "success"}),
            (AndTest([TrueTest()]), {"type": "and", "tests": [{"type": "true"}]}),
            (BetweenTest("5", "10"), {"type": "between", "min": "5", "max": "10"}),
            (ContainsTest("green"), {"type": "contains", "test": "green"}),
            (EqTest("5"), {"type": "eq", "test": "5"}),
            (FalseTest(), {"type": "false"}),
            (GtTest("5"), {"type": "gt", "test": "5"}),
            (GteTest("5"), {"type": "gte", "test": "5"}),
            (LtTest("5"), {"type": "lt", "test": "5"}),
            (LteTest("5"), {"type": "lte", "test": "5"}),
            (HasDistrictTest("Kano"), {"type": "district", "test": "Kano"}),
            (HasEmailTest(), {"type": "has_email"}),
            (HasStateTest(), {"type": "state"}),
            (HasWardTest("Kano", "Ajingi"), {"type": "ward", "state": "Kano", "district": "Ajingi"}),
            (OrTest([TrueTest()]), {"type": "or", "tests": [{"type": "true"}]}),
            (PhoneTest(), {"type": "phone"}),
            (RegexTest("\\d"), {"type": "regex", "test": "\\d"}),
            (TrueTest(), {"type": "true"}),
            (WebhookStatusTest("success"), {"type": "webhook_status", "status": "success"}),
        ]

        for obj, definition in tests:
            self.assertEqual(obj.__class__, Test.from_json(self.org, definition).__class__)
            self.assertEqual(definition, obj.as_json())


class ActionTest(TembaTest):
    def _serialize_deserialize(self, action):
        action_json = json.dumps(action.as_json())
        return Action.from_json(self.org, json.loads(action_json))

    def test_reply(self):
        action = ReplyAction(
            str(uuid4()),
            {"eng": "Hello", "fra": "Bonjour"},
            {"eng": "media.jpg"},
            quick_replies=[{"eng": "Yes"}, {"eng": "No"}],
        )
        action = self._serialize_deserialize(action)

        self.assertEqual({"eng": "Hello", "fra": "Bonjour"}, action.msg)
        self.assertEqual({"eng": "media.jpg"}, action.media)
        self.assertEqual([{"eng": "Yes"}, {"eng": "No"}], action.quick_replies)

        # msg can't be none
        with self.assertRaises(FlowException):
            ReplyAction.from_json(self.org, {"msg": None})

        # msg can't be empty dict
        with self.assertRaises(FlowException):
            ReplyAction.from_json(self.org, {"msg": {}})

        # msg value can't be empty
        with self.assertRaises(FlowException):
            ReplyAction.from_json(self.org, {"msg": {"eng": ""}})

    def test_email(self):
        action = EmailAction(str(uuid4()), ["steve@apple.com"], "Subject", "Body")
        action = self._serialize_deserialize(action)

        self.assertEqual(["steve@apple.com"], action.emails)
        self.assertEqual("Subject", action.subject)
        self.assertEqual("Body", action.message)

    def test_save(self):
        action = SaveToContactAction(str(uuid4()), "Superhero Name", "superhero_name", "@step")
        action = self._serialize_deserialize(action)

        self.assertEqual("Superhero Name", action.label)
        self.assertEqual("superhero_name", action.field)
        self.assertEqual("@step", action.value)

        self.assertEqual("Contact Name", SaveToContactAction.get_label(self.org, "name"))
        self.assertEqual("First Name", SaveToContactAction.get_label(self.org, "first_name"))
        self.assertEqual("Phone Number", SaveToContactAction.get_label(self.org, "tel_e164"))
        self.assertEqual("Telegram identifier", SaveToContactAction.get_label(self.org, "telegram"))
        self.assertEqual("Foo", SaveToContactAction.get_label(self.org, "foo"))

    def test_language(self):
        action = SetLanguageAction(str(uuid4()), "kli", "Klingon")
        action = self._serialize_deserialize(action)

        self.assertEqual("kli", action.lang)
        self.assertEqual("Klingon", action.name)

    def test_flow(self):
        flow = self.get_flow("color")

        action = StartFlowAction(str(uuid4()), flow)
        action = self._serialize_deserialize(action)

        self.assertEqual(flow, action.flow)

    def test_group_actions(self):
        group = self.create_group("My Group", [])

        action = AddToGroupAction(str(uuid4()), [group, "@step.contact"])
        action = self._serialize_deserialize(action)

        self.assertEqual([group, "@step.contact"], action.groups)

        # try when group is inactive
        action = DeleteFromGroupAction(str(uuid4()), [group])
        group.is_active = False
        group.save()

        self.assertIn(group, action.groups)

        # reading the action should create a new group
        updated_action = DeleteFromGroupAction.from_json(self.org, action.as_json())
        self.assertTrue(updated_action.groups)
        self.assertNotIn(group, updated_action.groups)

    def test_channel(self):
        channel = Channel.add_config_external_channel(self.org, self.admin, "US", "+12061111111", "KN", {})

        action = SetChannelAction(str(uuid4()), channel)
        action = self._serialize_deserialize(action)

        self.assertEqual(channel, action.channel)

    def test_add_label(self):
        label = Label.get_or_create(self.org, self.user, "green label")

        action = AddLabelAction(str(uuid4()), [label, "@step.contact"])
        action = self._serialize_deserialize(action)

        self.assertEqual([label, "@step.contact"], action.labels)

    def test_trigger_flow(self):
        contact = self.create_contact("Eric", "+250788382382")
        group = self.create_group("My Group", [])
        flow = self.get_flow("color")

        action = TriggerFlowAction(str(uuid4()), flow, [group], [contact], ["@contact.supervisor"])
        action = self._serialize_deserialize(action)

        self.assertEqual(flow, action.flow)
        self.assertEqual([group], action.groups)
        self.assertEqual([contact], action.contacts)
        self.assertEqual(["@contact.supervisor"], action.variables)

    def test_send(self):
        contact = self.create_contact("Eric", "+250788382382")
        group = self.create_group("My Group", [])

        action = SendAction(
            str(uuid4()), {"eng": "Hello"}, [group], [contact], ["@contact.supervisor"], {"eng": "test.jpg"}
        )
        action = self._serialize_deserialize(action)

        self.assertEqual({"eng": "Hello"}, action.msg)
        self.assertEqual([group], action.groups)
        self.assertEqual([contact], action.contacts)
        self.assertEqual(["@contact.supervisor"], action.variables)
        self.assertEqual({"eng": "test.jpg"}, action.media)

    def test_variable_group_parsing(self):
        groups = VariableContactAction.parse_groups(self.org, {"groups": [{"id": 1}]})

        self.assertEqual("Missing", groups[0].name)
