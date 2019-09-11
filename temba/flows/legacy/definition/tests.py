import copy
from uuid import uuid4

from django.conf import settings
from django.test import override_settings

from temba.channels.models import Channel
from temba.contacts.models import URN, Contact, ContactField, ContactGroup, ContactURN
from temba.flows.models import Flow, FlowException, FlowRevision, FlowRun
from temba.msgs.models import INCOMING, Broadcast, Label, Msg
from temba.tests import ESMockWithScroll, TembaTest, uses_legacy_engine

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
    TriggerFlowAction,
    VariableContactAction,
)
from .rules import (
    AndTest,
    BetweenTest,
    ContainsTest,
    EqTest,
    FalseTest,
    GteTest,
    GtTest,
    HasWardTest,
    LtTest,
    OrTest,
    Test,
    TrueTest,
)


class TestsTest(TembaTest):
    def test_factories(self):
        org = self.org

        js = dict(type="true")
        self.assertEqual(TrueTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, TrueTest().as_json())

        js = dict(type="false")
        self.assertEqual(FalseTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, FalseTest().as_json())

        js = dict(type="and", tests=[dict(type="true")])
        self.assertEqual(AndTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, AndTest([TrueTest()]).as_json())

        js = dict(type="or", tests=[dict(type="true")])
        self.assertEqual(OrTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, OrTest([TrueTest()]).as_json())

        js = dict(type="contains", test="green")
        self.assertEqual(ContainsTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, ContainsTest("green").as_json())

        js = dict(type="lt", test="5")
        self.assertEqual(LtTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, LtTest("5").as_json())

        js = dict(type="gt", test="5")
        self.assertEqual(GtTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, GtTest("5").as_json())

        js = dict(type="gte", test="5")
        self.assertEqual(GteTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, GteTest("5").as_json())

        js = dict(type="eq", test="5")
        self.assertEqual(EqTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, EqTest("5").as_json())

        js = dict(type="between", min="5", max="10")
        self.assertEqual(BetweenTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, BetweenTest("5", "10").as_json())

        js = dict(state="Kano", district="Ajingi", type="ward")
        self.assertEqual(HasWardTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, HasWardTest("Kano", "Ajingi").as_json())


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

    @uses_legacy_engine
    def test_trigger_flow_action(self):
        flow = self.create_flow()
        run = FlowRun.create(self.flow, self.contact)

        # add a channel to make sure that country is ambiguous
        Channel.create(self.org, self.admin, "US", "EX", schemes=["tel"])
        delattr(self.org, "_country_code")
        self.org.country = None
        self.org.save()

        # set a contact field with another phone number
        self.contact.set_field(self.admin, "other_contact_tel", "+12065551212", "Other Contact Tel")

        action = TriggerFlowAction(str(uuid4()), flow, [], [self.contact], ["@contact.other_contact_tel"])
        self.execute_action(action, run, None)

        # should have created a new contact with the above variable
        self.assertIsNotNone(Contact.from_urn(self.org, "tel:+12065551212"))

        action_json = action.as_json()
        action = TriggerFlowAction.from_json(self.org, action_json)
        self.assertEqual(action.flow.pk, flow.pk)

        self.assertTrue(FlowRun.objects.filter(contact=self.contact, flow=flow))

        action = TriggerFlowAction(str(uuid4()), flow, [self.other_group], [], [])
        run = FlowRun.create(self.flow, self.contact)
        msgs = self.execute_action(action, run, None)

        self.assertFalse(msgs)

        self.other_group.update_contacts(self.user, [self.contact2], True)

        action = TriggerFlowAction(str(uuid4()), flow, [self.other_group], [self.contact], [])
        run = FlowRun.create(self.flow, self.contact)
        self.execute_action(action, run, None)

        self.assertTrue(FlowRun.objects.filter(contact=self.contact2, flow=flow))

        # delete the group
        self.other_group.is_active = False
        self.other_group.save()

        self.assertTrue(action.groups)
        self.assertTrue(self.other_group.pk in [g.pk for g in action.groups])
        # should create new group the next time the flow is read
        updated_action = TriggerFlowAction.from_json(self.org, action.as_json())
        self.assertTrue(updated_action.groups)
        self.assertFalse(self.other_group.pk in [g.pk for g in updated_action.groups])

    def test_send_action(self):
        # previously @step.contact was the run contact and @contact would become the recipient but that has been
        # changed so that both are the run contact
        msg_body = "Hi @contact.name (@contact.state). @step.contact (@step.contact.state) is in the flow"

        self.contact.set_field(self.user, "state", "WA", label="State")
        self.contact2.set_field(self.user, "state", "GA", label="State")
        run = FlowRun.create(self.flow, self.contact)

        action = SendAction(str(uuid4()), dict(base=msg_body), [], [self.contact2], [])
        self.execute_action(action, run, None)

        action_json = action.as_json()
        action = SendAction.from_json(self.org, action_json)
        self.assertEqual(action.msg["base"], msg_body)
        self.assertEqual(action.media, dict())

        broadcast = Broadcast.objects.get()
        self.assertEqual(broadcast.text, dict(base=msg_body))
        self.assertEqual(broadcast.base_language, "base")
        self.assertEqual(broadcast.get_messages().count(), 1)
        msg = broadcast.get_messages().first()
        self.assertEqual(msg.contact, self.contact2)
        self.assertEqual(msg.text, "Hi Eric (WA). Eric (WA) is in the flow")

        # empty message should be a no-op
        action = SendAction(str(uuid4()), dict(base=""), [], [self.contact], [])
        self.execute_action(action, run, None)
        self.assertEqual(Broadcast.objects.all().count(), 1)

        # support sending to groups inside SendAction
        SendAction.from_json(self.org, action.as_json())

        # test send media to someone else
        run = FlowRun.create(self.flow, self.contact)
        msg_body = "I am a media message message"

        action = SendAction(
            str(uuid4()),
            dict(base=msg_body),
            [],
            [self.contact2],
            [],
            dict(base=f"image/jpeg:{settings.STORAGE_URL}/attachments/picture.jpg"),
        )
        self.execute_action(action, run, None)

        action_json = action.as_json()
        action = SendAction.from_json(self.org, action_json)
        self.assertEqual(action.msg["base"], msg_body)
        self.assertEqual(action.media["base"], f"image/jpeg:{settings.STORAGE_URL}/attachments/picture.jpg")

        self.assertEqual(Broadcast.objects.all().count(), 2)  # new broadcast with media

        broadcast = Broadcast.objects.order_by("-id").first()
        self.assertEqual(broadcast.media, dict(base=f"image/jpeg:{settings.STORAGE_URL}/attachments/picture.jpg"))
        self.assertEqual(broadcast.get_messages().count(), 1)
        msg = broadcast.get_messages().first()
        self.assertEqual(msg.contact, self.contact2)
        self.assertEqual(msg.text, msg_body)
        self.assertEqual(msg.attachments, [f"image/jpeg:{settings.STORAGE_URL}/attachments/picture.jpg"])

        # also send if we have empty message but have an attachment
        action = SendAction(
            str(uuid4()), dict(base=""), [], [self.contact], [], dict(base="image/jpeg:attachments/picture.jpg")
        )
        self.execute_action(action, run, None)

        broadcast = Broadcast.objects.order_by("-id").first()
        self.assertEqual(broadcast.text, dict(base=""))
        self.assertEqual(broadcast.media, dict(base="image/jpeg:attachments/picture.jpg"))
        self.assertEqual(broadcast.base_language, "base")

    def test_variable_contact_parsing(self):
        groups = dict(groups=[dict(id=-1)])
        groups = VariableContactAction.parse_groups(self.org, groups)
        self.assertTrue("Missing", groups[0].name)

    @override_settings(SEND_EMAILS=True)
    def test_email_action(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        action = EmailAction(str(uuid4()), ["steve@apple.com"], "Subject", "Body")

        # check to and from JSON
        action_json = action.as_json()
        action = EmailAction.from_json(self.org, action_json)

        self.execute_action(action, run, msg)
        self.assertOutbox(0, "no-reply@temba.io", "Subject", "Body", ["steve@apple.com"])

        try:
            EmailAction(str(uuid4()), [], "Subject", "Body")
            self.fail("Should have thrown due to empty recipient list")
        except FlowException:
            pass

        # check expression evaluation in action fields
        action = EmailAction(
            str(uuid4()),
            ["@contact.name", "xyz", "", '@(SUBSTITUTE(LOWER(contact), " ", "") & "@nyaruka.com")'],
            "@contact.name added in subject",
            "@contact.name uses phone @contact.tel",
        )

        action_json = action.as_json()
        action = EmailAction.from_json(self.org, action_json)

        self.execute_action(action, run, msg)
        self.assertOutbox(
            1, "no-reply@temba.io", "Eric added in subject", "Eric uses phone 0788 382 382", ["eric@nyaruka.com"]
        )

        # check that all white space is replaced with single spaces in the subject
        test = EmailAction(
            str(uuid4()), ["steve@apple.com"], "Allo \n allo\tmessage", "Email notification for allo allo"
        )
        self.execute_action(test, run, msg)

        self.assertOutbox(
            2, "no-reply@temba.io", "Allo allo message", "Email notification for allo allo", ["steve@apple.com"]
        )

        # now try with a custom from address
        branding = copy.deepcopy(settings.BRANDING)
        branding["rapidpro.io"]["flow_email"] = "no-reply@mybrand.com"
        with self.settings(BRANDING=branding):
            self.execute_action(action, run, msg)
            self.assertOutbox(
                3,
                "no-reply@mybrand.com",
                "Eric added in subject",
                "Eric uses phone 0788 382 382",
                ["eric@nyaruka.com"],
            )

        # same thing, but with a custom smtp server
        self.org.add_smtp_config(
            "support@example.com", "smtp.example.com", "support@example.com", "secret", "465", self.admin
        )
        action = EmailAction(str(uuid4()), ["steve@apple.com"], "Subject", "Body")
        self.execute_action(action, run, msg)
        self.assertOutbox(4, "support@example.com", "Subject", "Body", ["steve@apple.com"])

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

        # check to and from JSON
        action_json = action.as_json()
        action = SetLanguageAction.from_json(self.org, action_json)

        self.assertEqual("kli", action.lang)
        self.assertEqual("Klingon", action.name)

        # execute our action and check we are Klingon now, eeektorp shnockahltip.
        run = FlowRun.create(self.flow, self.contact)
        self.execute_action(action, run, None)
        self.assertEqual("kli", Contact.objects.get(pk=self.contact.pk).language)

        # try setting the language to something thats not three characters
        action_json["lang"] = "base"
        action_json["name"] = "Default"
        action = SetLanguageAction.from_json(self.org, action_json)
        self.execute_action(action, run, None)

        # should clear the contacts language
        self.assertIsNone(Contact.objects.get(pk=self.contact.pk).language)

    def test_group_actions(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        group = self.create_group("Flow Group", [])

        # check converting to and from json
        action = AddToGroupAction(str(uuid4()), [group, "@step.contact"])
        action_json = action.as_json()
        action = AddToGroupAction.from_json(self.org, action_json)

        self.execute_action(action, run, msg)

        # user should now be in the group
        self.assertEqual(set(group.contacts.all()), {self.contact})

        # we should never create a new group in the flow execution
        self.assertIsNone(ContactGroup.user_groups.filter(name=self.contact.name).first())

        # should match existing group for variables
        replace_group1 = ContactGroup.create_static(self.org, self.admin, self.contact.name)
        self.assertEqual(set(replace_group1.contacts.all()), set())

        # passing through twice doesn't change anything
        self.execute_action(action, run, msg)

        self.assertEqual(set(group.contacts.all()), {self.contact})
        self.assertEqual(self.contact.user_groups.all().count(), 2)

        # having the group name containing a space doesn't change anything
        self.contact.name += " "
        self.contact.save(update_fields=("name",), handle_update=False)
        run.contact = self.contact

        self.execute_action(action, run, msg)

        self.assertEqual(set(group.contacts.all()), {self.contact})
        self.assertEqual(set(replace_group1.contacts.all()), {self.contact})

        # try when group is inactive
        action = DeleteFromGroupAction(str(uuid4()), [group])
        group.is_active = False
        group.save()

        self.assertIn(group, action.groups)

        # reading the action should create a new group
        updated_action = DeleteFromGroupAction.from_json(self.org, action.as_json())
        self.assertTrue(updated_action.groups)
        self.assertFalse(group.pk in [g.pk for g in updated_action.groups])

        # try adding a contact to a dynamic group
        self.create_field("isalive", "Is Alive")
        with ESMockWithScroll():
            dynamic_group = self.create_group("Dynamic", query="isalive=YES")
        action = AddToGroupAction(str(uuid4()), [dynamic_group])

        self.execute_action(action, run, msg)

        # should do nothing
        self.assertEqual(dynamic_group.contacts.count(), 0)

        group1 = self.create_group("Flow Group 1", [])
        group2 = self.create_group("Flow Group 2", [])

        test = AddToGroupAction(str(uuid4()), [group1])
        action_json = test.as_json()
        test = AddToGroupAction.from_json(self.org, action_json)

        self.execute_action(test, run, msg)

        test = AddToGroupAction(str(uuid4()), [group2])
        action_json = test.as_json()
        test = AddToGroupAction.from_json(self.org, action_json)

        self.execute_action(test, run, msg)

        # user should be in both groups now
        self.assertTrue(group1.contacts.filter(id=self.contact.pk))
        self.assertEqual(1, group1.contacts.all().count())
        self.assertTrue(group2.contacts.filter(id=self.contact.pk))
        self.assertEqual(1, group2.contacts.all().count())

        test = DeleteFromGroupAction(str(uuid4()), [])
        action_json = test.as_json()
        test = DeleteFromGroupAction.from_json(self.org, action_json)

        self.execute_action(test, run, msg)

        # user should be gone from both groups now
        self.assertFalse(group1.contacts.filter(id=self.contact.pk))
        self.assertEqual(0, group1.contacts.all().count())
        self.assertFalse(group2.contacts.filter(id=self.contact.pk))
        self.assertEqual(0, group2.contacts.all().count())

    def test_set_channel_action(self):
        flow = self.flow
        run = FlowRun.create(flow, self.contact)

        tel1_channel = Channel.add_config_external_channel(self.org, self.admin, "US", "+12061111111", "KN", {})
        tel2_channel = Channel.add_config_external_channel(self.org, self.admin, "US", "+12062222222", "KN", {})

        fb_channel = Channel.create(
            self.org,
            self.user,
            None,
            "FB",
            address="Page Id",
            config={"page_name": "Page Name", "auth_token": "Page Token"},
        )

        # create an incoming message on tel1, this should create an affinity to that channel
        Msg.create_incoming(tel1_channel, str(self.contact.urns.all().first()), "Incoming msg")
        urn = self.contact.urns.all().first()
        self.assertEqual(urn.channel, tel1_channel)

        action = SetChannelAction(str(uuid4()), tel2_channel)
        self.execute_action(action, run, None)

        # check the affinity on our urn again, should now be the second channel
        urn.refresh_from_db()
        self.assertEqual(urn.channel, tel2_channel)

        # try to set it to a channel that we don't have a URN for
        action = SetChannelAction(str(uuid4()), fb_channel)
        self.execute_action(action, run, None)

        # affinity is unchanged
        urn.refresh_from_db()
        self.assertEqual(urn.channel, tel2_channel)

        # add a FB urn for our contact
        fb_urn = ContactURN.get_or_create(self.org, self.contact, "facebook:1001")

        # default URN should be FB now, as it has the highest priority
        contact, resolved_urn = Msg.resolve_recipient(self.org, self.admin, self.contact, None)
        self.assertEqual(resolved_urn, fb_urn)

        # but if we set our channel to tel, will override that
        run.contact.clear_urn_cache()
        action = SetChannelAction(str(uuid4()), tel1_channel)
        self.execute_action(action, run, None)

        contact.clear_urn_cache()
        contact, resolved_urn = Msg.resolve_recipient(self.org, self.admin, self.contact, None)
        self.assertEqual(resolved_urn, urn)
        self.assertEqual(resolved_urn.channel, tel1_channel)

        # test serializing
        action_json = action.as_json()
        action = SetChannelAction.from_json(self.org, action_json)
        self.assertEqual(tel1_channel, action.channel)

        # action shouldn't blow up without a channel
        action = SetChannelAction(str(uuid4()), None)
        self.execute_action(action, run, None)

        # incoming messages will still cause preference to switch
        Msg.create_incoming(tel2_channel, str(urn), "Incoming msg")
        urn.refresh_from_db()
        self.assertEqual(urn.channel, tel2_channel)

        # make sure that switch will work across schemes as well
        Msg.create_incoming(fb_channel, str(fb_urn), "Incoming FB message")
        self.contact.clear_urn_cache()
        contact, resolved_urn = Msg.resolve_recipient(self.org, self.admin, self.contact, None)
        self.assertEqual(resolved_urn, fb_urn)

    def test_add_label_action(self):
        flow = self.flow
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(flow, self.contact)

        label1 = Label.get_or_create(self.org, self.user, "green label")
        action = AddLabelAction(str(uuid4()), [label1, "@step.contact"])

        action_json = action.as_json()
        action = AddLabelAction.from_json(self.org, action_json)

        # no message yet; such Add Label action on entry Actionset. No error should be raised
        self.execute_action(action, run, None)

        self.assertFalse(label1.get_messages())
        self.assertEqual(label1.get_visible_count(), 0)

        self.execute_action(action, run, msg)

        # only label one was added to the message and no new label created
        self.assertEqual(set(label1.get_messages()), {msg})
        self.assertEqual(label1.get_visible_count(), 1)
        self.assertEqual(Label.label_objects.all().count(), 1)

        # make sure the expression variable label exists too
        label1 = Label.label_objects.get(pk=label1.pk)
        label2 = Label.label_objects.create(
            org=self.org, name=self.contact.name, created_by=self.admin, modified_by=self.admin
        )

        self.execute_action(action, run, msg)

        # and message should have been labeled with both labels
        msg = Msg.objects.get(pk=msg.pk)
        self.assertEqual(set(msg.labels.all()), {label1, label2})
        self.assertEqual(set(label1.get_messages()), {msg})
        self.assertEqual(label1.get_visible_count(), 1)
        self.assertTrue(set(label2.get_messages()), {msg})
        self.assertEqual(label2.get_visible_count(), 1)

        # passing through twice doesn't change anything
        self.execute_action(action, run, msg)

        self.assertEqual(set(Msg.objects.get(pk=msg.pk).labels.all()), {label1, label2})
        self.assertEqual(Label.label_objects.get(pk=label1.pk).get_visible_count(), 1)
        self.assertEqual(Label.label_objects.get(pk=label2.pk).get_visible_count(), 1)


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
