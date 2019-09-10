from uuid import uuid4

from django.conf import settings

from temba.channels.models import Channel
from temba.contacts.models import URN, Contact, ContactField, ContactURN
from temba.flows.models import Flow, FlowException, FlowRevision, FlowRun
from temba.msgs.models import INCOMING, Broadcast, Label, Msg
from temba.tests import TembaTest, uses_legacy_engine

from ..engine import flow_start
from ..expressions import flow_context
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
    HasWardTest,
    LteTest,
    LtTest,
    OrTest,
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
            (HasWardTest("Kano", "Ajingi"), {"type": "ward", "state": "Kano", "district": "Ajingi"}),
            (OrTest([TrueTest()]), {"type": "or", "tests": [{"type": "true"}]}),
            (TrueTest(), {"type": "true"}),
            (WebhookStatusTest("success"), {"type": "webhook_status", "status": "success"}),
        ]

        for obj, definition in tests:
            self.assertEqual(obj.__class__, Test.from_json(self.org, definition).__class__)
            self.assertEqual(definition, obj.as_json())


class ActionTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.contact = self.create_contact("Eric", "+250788382382")
        self.contact2 = self.create_contact("Nic", "+250788383383")

        self.flow = self.get_flow("color")

        self.other_group = self.create_group("Other", [])

    def test_factories(self):
        self.assertEqual(
            ReplyAction, Action.from_json(self.org, dict(type="reply", msg=dict(base="hello world"))).__class__
        )
        self.assertEqual(
            SendAction,
            Action.from_json(
                self.org, dict(type="send", msg=dict(base="hello world"), contacts=[], groups=[], variables=[])
            ).__class__,
        )

    def execute_action(self, action, run, msg, **kwargs):
        context = flow_context(run.flow, run.contact, msg)
        return action.execute(run, context, None, msg, **kwargs)

    def test_reply_action(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        with self.assertRaises(FlowException):
            ReplyAction.from_json(self.org, {"type": ReplyAction.TYPE})

        with self.assertRaises(FlowException):
            ReplyAction.from_json(self.org, {"type": ReplyAction.TYPE, ReplyAction.MESSAGE: dict()})

        with self.assertRaises(FlowException):
            ReplyAction.from_json(self.org, {"type": ReplyAction.TYPE, ReplyAction.MESSAGE: dict(base="")})

        action = ReplyAction(str(uuid4()), dict(base="We love green too!"))
        self.execute_action(action, run, msg)
        msg = Msg.objects.get(contact=self.contact, direction="O")
        self.assertEqual("We love green too!", msg.text)

        Broadcast.objects.all().delete()

        action_json = action.as_json()
        action = ReplyAction.from_json(self.org, action_json)
        self.assertEqual(dict(base="We love green too!"), action.msg)

        self.execute_action(action, run, msg)

        response = msg.responses.get()
        self.assertEqual("We love green too!", response.text)
        self.assertEqual(self.contact, response.contact)

    def test_send_all_action(self):
        contact = self.create_contact("Stephen", "+12078778899", twitter="stephen")
        msg = self.create_msg(direction=INCOMING, contact=contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        action = ReplyAction(str(uuid4()), dict(base="We love green too!"), None, send_all=True)
        action_replies = self.execute_action(action, run, msg)
        self.assertEqual(len(action_replies), 1)
        for action_reply in action_replies:
            self.assertIsInstance(action_reply, Msg)

        replies = Msg.objects.filter(contact=contact, direction="O")
        self.assertEqual(replies.count(), 1)
        self.assertIsNone(replies.filter(contact_urn__path="stephen").first())
        self.assertIsNotNone(replies.filter(contact_urn__path="+12078778899").first())

        self.release(Broadcast.objects.all())
        self.release(Msg.objects.all())

        msg = self.create_msg(direction=INCOMING, contact=contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        # create twitter channel
        Channel.create(self.org, self.user, None, "TT")
        delattr(self.org, "__schemes__%s" % Channel.ROLE_SEND)

        action_json = action.as_json()
        action = ReplyAction.from_json(self.org, action_json)
        self.assertEqual(dict(base="We love green too!"), action.msg)
        self.assertTrue(action.send_all)

        action_replies = self.execute_action(action, run, msg)
        self.assertEqual(len(action_replies), 2)
        for action_reply in action_replies:
            self.assertIsInstance(action_reply, Msg)

        replies = Msg.objects.filter(contact=contact, direction="O")
        self.assertEqual(replies.count(), 2)
        self.assertIsNotNone(replies.filter(contact_urn__path="stephen").first())
        self.assertIsNotNone(replies.filter(contact_urn__path="+12078778899").first())

    def test_media_action(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        action = ReplyAction(str(uuid4()), dict(base="We love green too!"), "image/jpeg:path/to/media.jpg")
        self.execute_action(action, run, msg)
        reply_msg = Msg.objects.get(contact=self.contact, direction="O")
        self.assertEqual("We love green too!", reply_msg.text)
        self.assertEqual(reply_msg.attachments, [f"image/jpeg:{settings.STORAGE_URL}/path/to/media.jpg"])

        self.release(Broadcast.objects.all())
        self.release(Msg.objects.all())

        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")

        action_json = action.as_json()
        action = ReplyAction.from_json(self.org, action_json)
        self.assertEqual(dict(base="We love green too!"), action.msg)
        self.assertEqual("image/jpeg:path/to/media.jpg", action.media)

        self.execute_action(action, run, msg)

        response = msg.responses.get()
        self.assertEqual("We love green too!", response.text)
        self.assertEqual(response.attachments, [f"image/jpeg:{settings.STORAGE_URL}/path/to/media.jpg"])
        self.assertEqual(self.contact, response.contact)

    def test_media_expression(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="profile")
        run = FlowRun.create(self.flow, self.contact)

        action = ReplyAction(
            str(uuid4()), dict(base="Here is your profile pic."), "image:/photos/contacts/@(contact.name).jpg"
        )

        # export and import our json to make sure that works as well
        action_json = action.as_json()
        action = ReplyAction.from_json(self.org, action_json)

        # now execute it
        self.execute_action(action, run, msg)
        reply_msg = Msg.objects.get(contact=self.contact, direction="O")
        self.assertEqual("Here is your profile pic.", reply_msg.text)
        self.assertEqual(reply_msg.attachments, ["image:/photos/contacts/Eric.jpg"])

        response = msg.responses.get()
        self.assertEqual("Here is your profile pic.", response.text)
        self.assertEqual(self.contact, response.contact)

    def test_quick_replies_action(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Yes")
        run = FlowRun.create(self.flow, self.contact)

        payload = [dict(eng="Yes"), dict(eng="No")]

        action = ReplyAction(str(uuid4()), msg=dict(base="Are you fine?"), quick_replies=payload)
        action_json = action.as_json()
        action = ReplyAction.from_json(self.org, action_json)

        self.execute_action(action, run, msg)
        self.assertEqual(action.msg, dict(base="Are you fine?"))
        self.assertEqual(action.quick_replies, payload)

    def test_trigger_flow(self):
        flow = self.create_flow()
        action = TriggerFlowAction(str(uuid4()), flow, [], [self.contact], ["@contact.other_contact_tel"])

        action_json = action.as_json()
        action = TriggerFlowAction.from_json(self.org, action_json)

        self.assertEqual(flow, action.flow)
        self.assertEqual([self.contact], action.contacts)
        self.assertEqual([], action.groups)
        self.assertEqual(["@contact.other_contact_tel"], action.variables)

    def test_send_action(self):
        action = SendAction(str(uuid4()), dict(base="Hello"), [], [self.contact2], [])

        action_json = action.as_json()
        action = SendAction.from_json(self.org, action_json)
        self.assertEqual("Hello", action.msg["base"])
        self.assertEqual({}, action.media)
        self.assertEqual([self.contact2], action.contacts)

    def test_variable_contact_parsing(self):
        groups = dict(groups=[dict(id=-1)])
        groups = VariableContactAction.parse_groups(self.org, groups)
        self.assertTrue("Missing", groups[0].name)

    def test_email_action(self):
        action = EmailAction(str(uuid4()), ["steve@apple.com"], "Subject", "Body")

        # check to and from JSON
        action_json = action.as_json()
        action = EmailAction.from_json(self.org, action_json)
        self.assertEqual(["steve@apple.com"], action.emails)
        self.assertEqual("Subject", action.subject)
        self.assertEqual("Body", action.message)

    def test_save_to_contact_action(self):
        sms = self.create_msg(direction=INCOMING, contact=self.contact, text="batman")
        test = SaveToContactAction.from_json(self.org, dict(type="save", label="Superhero Name", value="@step"))
        run = FlowRun.create(self.flow, self.contact)

        field = ContactField.user_fields.get(org=self.org, key="superhero_name")
        self.assertEqual("Superhero Name", field.label)

        self.execute_action(test, run, sms)

        # user should now have a nickname field with a value of batman
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual("batman", contact.get_field_serialized(field))

        # test clearing our value
        test = SaveToContactAction.from_json(self.org, test.as_json())
        test.value = ""
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual(None, contact.get_field_serialized(field))

        # test setting our name
        test = SaveToContactAction.from_json(self.org, dict(type="save", label="Name", value="", field="name"))
        test.value = "Eric Newcomer"
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual("Eric Newcomer", contact.name)
        run.contact = contact

        # test setting just the first name
        test = SaveToContactAction.from_json(
            self.org, dict(type="save", label="First Name", value="", field="first_name")
        )
        test.value = "Jen"
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual("Jen Newcomer", contact.name)

        # throw exception for other reserved words except name, first_name and URN schemes
        for key in Contact.RESERVED_FIELD_KEYS:
            if key not in ["name", "first_name", "tel_e164"] + list(URN.VALID_SCHEMES):
                with self.assertRaises(Exception):
                    test = SaveToContactAction.from_json(self.org, dict(type="save", label=key, value="", field=key))
                    test.value = "Jen"
                    self.execute_action(test, run, sms)

        # we should strip whitespace
        run.contact = contact
        test = SaveToContactAction.from_json(
            self.org, dict(type="save", label="First Name", value="", field="first_name")
        )
        test.value = " Jackson "
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual("Jackson Newcomer", contact.name)

        # first name works with a single word
        run.contact = contact
        contact.name = "Percy"
        contact.save(update_fields=("name",), handle_update=False)

        test = SaveToContactAction.from_json(
            self.org, dict(type="save", label="First Name", value="", field="first_name")
        )
        test.value = " Cole"
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual("Cole", contact.name)

        # test saving something really long to another field
        test = SaveToContactAction.from_json(
            self.org, dict(type="save", label="Last Message", value="", field="last_message")
        )
        test.value = (
            "This is a long message, longer than 160 characters, longer than 250 characters, all the way up "
            "to 500 some characters long because sometimes people save entire messages to their contact "
            "fields and we want to enable that for them so that they can do what they want with the platform."
        )
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual(test.value, contact.get_field_value(ContactField.get_by_key(self.org, "last_message")))

        # test saving a contact's phone number
        test = SaveToContactAction.from_json(
            self.org, dict(type="save", label="Phone Number", field="tel_e164", value="@step")
        )

        # make sure they have a twitter urn first
        contact.urns.add(ContactURN.create(self.org, None, "twitter:enewcomer"))
        self.assertIsNotNone(contact.urns.filter(path="enewcomer").first())

        # add another phone number to make sure it doesn't get removed too
        contact.urns.add(ContactURN.create(self.org, None, "tel:+18005551212"))
        self.assertEqual(3, contact.urns.all().count())

        # create an inbound message on our original phone number
        sms = self.create_msg(
            direction=INCOMING,
            contact=self.contact,
            text="+12065551212",
            contact_urn=contact.urns.filter(path="+250788382382").first(),
        )

        # create another contact with that phone number, to test stealing
        robbed = self.create_contact("Robzor", "+12065551212")

        self.execute_action(test, run, sms)

        # updating Phone Number should not create a contact field
        self.assertIsNone(ContactField.user_fields.filter(org=self.org, key="tel_e164").first())

        # instead it should update the tel urn for our contact
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual(4, contact.urns.all().count())
        self.assertIsNotNone(contact.urns.filter(path="+12065551212").first())

        # we should still have our twitter scheme
        self.assertIsNotNone(contact.urns.filter(path="enewcomer").first())

        # and our other phone number
        self.assertIsNotNone(contact.urns.filter(path="+18005551212").first())

        # and our original number too
        self.assertIsNotNone(contact.urns.filter(path="+250788382382").first())

        # robzor shouldn't have a number anymore
        self.assertFalse(robbed.urns.all())

        self.assertFalse(ContactField.user_fields.filter(org=self.org, label="Ecole"))
        SaveToContactAction.from_json(self.org, dict(type="save", label="[_NEW_]Ecole", value="@step"))
        field = ContactField.user_fields.get(org=self.org, key="ecole")
        self.assertEqual("Ecole", field.label)

        # try saving some empty data into mailto
        action = SaveToContactAction.from_json(self.org, dict(type="save", label="mailto", value="@contact.mailto"))
        self.execute_action(action, run, None)

        self.assertEqual(SaveToContactAction.get_label(self.org, "foo"), "Foo")

    def test_set_language_action(self):
        action = SetLanguageAction(str(uuid4()), "kli", "Klingon")
        action_json = action.as_json()
        action = SetLanguageAction.from_json(self.org, action_json)

        self.assertEqual("kli", action.lang)
        self.assertEqual("Klingon", action.name)

    @uses_legacy_engine
    def test_start_flow_action(self):
        self.flow.name = "Parent"
        self.flow.save()

        flow_start(self.flow, [], [self.contact])

        sms = Msg.create_incoming(self.channel, "tel:+250788382382", "Blue is my favorite")

        run = FlowRun.objects.get()

        new_flow = Flow.create_single_message(
            self.org, self.user, {"base": "You chose @parent.color.category"}, base_language="base"
        )
        action = StartFlowAction(str(uuid4()), new_flow)

        action_json = action.as_json()
        action = StartFlowAction.from_json(self.org, action_json)

        self.execute_action(action, run, sms, started_flows=[])

        # our contact should now be in the flow
        self.assertTrue(FlowRun.objects.filter(flow=new_flow, contact=self.contact))
        self.assertTrue(Msg.objects.filter(contact=self.contact, direction="O", text="You chose Blue"))

    def test_group_actions(self):
        group = self.create_group("Flow Group", [])

        # check converting to and from json
        action = AddToGroupAction(str(uuid4()), [group, "@step.contact"])
        action_json = action.as_json()
        action = AddToGroupAction.from_json(self.org, action_json)

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

    def test_set_channel_action(self):
        channel = Channel.add_config_external_channel(self.org, self.admin, "US", "+12061111111", "KN", {})

        action = SetChannelAction(str(uuid4()), channel)
        action_json = action.as_json()
        action = SetChannelAction.from_json(self.org, action_json)
        self.assertEqual(channel, action.channel)

    def test_add_label_action(self):
        label1 = Label.get_or_create(self.org, self.user, "green label")
        action = AddLabelAction(str(uuid4()), [label1, "@step.contact"])

        action_json = action.as_json()
        action = AddLabelAction.from_json(self.org, action_json)
        self.assertEqual([label1, "@step.contact"], action.labels)


class SendActionTest(TembaTest):
    def test_send(self):
        contact1 = self.create_contact("Mark", "+14255551212")
        contact2 = self.create_contact("Gregg", "+12065551212")

        substitutions = dict(contact1_id=contact1.id, contact2_id=contact2.id)
        exported_json = self.get_import_json("bad_send_action", substitutions)

        # create a flow object, we just need this to test our flow revision
        flow = Flow.objects.create(
            org=self.org, name="Import Flow", created_by=self.admin, modified_by=self.admin, saved_by=self.admin
        )
        revision = FlowRevision.objects.create(
            flow=flow,
            definition=exported_json,
            spec_version="8",
            revision=1,
            created_by=self.admin,
            modified_by=self.admin,
        )
        flow.version_number = "8"
        flow.save()

        migrated = revision.get_definition_json()

        # assert our contacts have valid uuids now
        self.assertEqual(migrated["action_sets"][0]["actions"][0]["contacts"][0]["uuid"], contact1.uuid)
        self.assertEqual(migrated["action_sets"][0]["actions"][0]["contacts"][1]["uuid"], contact2.uuid)
