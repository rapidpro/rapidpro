import json

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from temba import mailroom
from temba.msgs.models import Broadcast, Media, OptIn, SystemLabel
from temba.msgs.views import ScheduleForm
from temba.schedules.models import Schedule
from temba.templates.models import TemplateTranslation
from temba.tests import CRUDLTestMixin, TembaTest, mock_mailroom
from temba.tests.engine import MockSessionWriter
from temba.utils.compose import compose_deserialize_attachments, compose_serialize
from temba.utils.fields import ContactSearchWidget


class BroadcastCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", urns=["tel:+12025550149"])
        self.frank = self.create_contact("Frank Blow", urns=["tel:+12025550195"])
        self.joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])

    def _form_data(
        self,
        *,
        translations,
        contacts=(),
        advanced=False,
        query=None,
        optin=None,
        template=None,
        variables=[],
        send_when=ScheduleForm.SEND_LATER,
        start_datetime="",
        repeat_period="",
        repeat_days_of_week="",
    ):
        # UI puts optin in translations
        if translations:
            first_lang = next(iter(translations))
            translations[first_lang]["optin"] = {"uuid": str(optin.uuid), "name": optin.name} if optin else None

        if template:
            translation = template.translations.all().first()
            first_lang = next(iter(translations))
            translations[first_lang]["template"] = str(template.uuid)
            translations[first_lang]["variables"] = variables
            translations[first_lang]["locale"] = translation.locale

        recipients = ContactSearchWidget.get_recipients(contacts)
        contact_search = {"recipients": recipients, "advanced": advanced, "query": query, "exclusions": {}}

        payload = {
            "target": {"contact_search": json.dumps(contact_search)},
            "compose": {"compose": compose_serialize(translations, json_encode=True)} if translations else None,
            "schedule": (
                {
                    "send_when": send_when,
                    "start_datetime": start_datetime,
                    "repeat_period": repeat_period,
                    "repeat_days_of_week": repeat_days_of_week,
                }
                if send_when
                else None
            ),
        }

        if send_when == ScheduleForm.SEND_NOW:
            payload["schedule"] = {"send_when": send_when, "repeat_period": Schedule.REPEAT_NEVER}
        return payload

    @mock_mailroom
    def test_create(self, mr_mocks):
        create_url = reverse("msgs.broadcast_create")

        template = self.create_template(
            "Hello World",
            [
                TemplateTranslation(
                    channel=self.channel,
                    locale="eng-US",
                    status=TemplateTranslation.STATUS_APPROVED,
                    external_id="1003",
                    external_locale="en_US",
                    namespace="",
                    components=[
                        {"name": "header", "type": "header/media", "variables": {"1": 0}},
                        {
                            "name": "body",
                            "type": "body/text",
                            "content": "Hello {{1}}",
                            "variables": {"1": 1},
                        },
                    ],
                    variables=[{"type": "image"}, {"type": "text"}],
                )
            ],
        )

        text = "I hope you are having a great day"
        media = Media.from_upload(
            self.org,
            self.admin,
            self.upload(f"{settings.MEDIA_ROOT}/test_media/steve marten.jpg", "image/jpeg"),
            process=False,
        )

        self.assertRequestDisallowed(create_url, [None, self.user, self.agent])
        self.assertCreateFetch(create_url, [self.editor, self.admin], form_fields=("contact_search",))

        # initialize form based on a contact
        response = self.client.get(f"{create_url}?c={self.joe.uuid}")
        contact_search = response.context["form"]["contact_search"]

        self.assertEqual(
            {
                "recipients": [
                    {
                        "id": self.joe.uuid,
                        "name": "Joe Blow",
                        "urn": "+1 202-555-0149",
                        "type": "contact",
                    }
                ],
                "advanced": False,
                "query": None,
                "exclusions": {"in_a_flow": True},
            },
            json.loads(contact_search.value()),
        )

        # missing text
        response = self.process_wizard(
            "create",
            create_url,
            self._form_data(translations={"und": {"text": ""}}, contacts=[self.joe]),
        )
        self.assertFormError(response.context["form"], "compose", ["This field is required."])

        # text too long
        response = self.process_wizard(
            "create",
            create_url,
            self._form_data(translations={"eng": {"text": "." * 641}}, contacts=[self.joe]),
        )
        self.assertFormError(response.context["form"], "compose", ["Maximum allowed text is 640 characters."])

        # too many attachments
        attachments = compose_deserialize_attachments([{"content_type": media.content_type, "url": media.url}])
        response = self.process_wizard(
            "create",
            create_url,
            self._form_data(translations={"eng": {"text": text, "attachments": attachments * 11}}, contacts=[self.joe]),
        )
        self.assertFormError(response.context["form"], "compose", ["Maximum allowed attachments is 10 files."])

        # empty recipients
        response = self.process_wizard("create", create_url, self._form_data(translations={"eng": {"text": text}}))
        self.assertFormError(response.context["form"], "contact_search", ["Contacts or groups are required."])

        # empty query
        response = self.process_wizard(
            "create", create_url, self._form_data(advanced=True, translations={"eng": {"text": text}})
        )
        self.assertFormError(response.context["form"], "contact_search", ["A contact query is required."])

        # invalid query
        mr_mocks.exception(mailroom.QueryValidationException("Invalid query.", "syntax"))
        response = self.process_wizard(
            "create",
            create_url,
            self._form_data(advanced=True, translations={"eng": {"text": text}}, query="invalid"),
        )
        self.assertFormError(response.context["form"], "contact_search", ["Invalid query syntax."])

        # missing start time
        response = self.process_wizard(
            "create",
            create_url,
            self._form_data(translations={"eng": {"text": text}}, contacts=[self.joe]),
        )
        self.assertFormError(response.context["form"], None, ["Select when you would like the broadcast to be sent"])

        # start time in past and no repeat
        response = self.process_wizard(
            "create",
            create_url,
            self._form_data(
                translations={"eng": {"text": text}},
                contacts=[self.joe],
                start_datetime="2021-06-24 12:00Z",
                repeat_period="O",
                repeat_days_of_week=[],
            ),
        )
        self.assertFormError(
            response.context["form"], "start_datetime", ["Must specify a start time that is in the future."]
        )

        optin = OptIn.create(self.org, self.admin, "Alerts")

        # successful broadcast schedule
        response = self.process_wizard(
            "create",
            create_url,
            self._form_data(
                template=template,
                variables=["image/jpeg:http://domain/meow.jpg", "World"],
                translations={"eng": {"text": text}},
                contacts=[self.joe],
                optin=optin,
                start_datetime="2021-06-24 12:00Z",
                repeat_period="W",
                repeat_days_of_week=["M", "F"],
            ),
        )

        self.assertEqual(302, response.status_code)
        self.assertEqual(1, Broadcast.objects.count())
        broadcast = Broadcast.objects.filter(translations__icontains=text).first()
        self.assertEqual("W", broadcast.schedule.repeat_period)
        self.assertEqual(optin, broadcast.optin)
        self.assertEqual(template, broadcast.template)

        # send a broadcast right away
        response = self.process_wizard(
            "create",
            create_url,
            self._form_data(
                translations={"eng": {"text": text}},
                contacts=[self.joe],
                send_when=ScheduleForm.SEND_NOW,
            ),
        )
        self.assertEqual(302, response.status_code)

        # we should have a sent broadcast, so no schedule attached
        self.assertEqual(1, Broadcast.objects.filter(schedule=None).count())

        # servicers should be able to use wizard up to the last step
        self.login(self.customer_support, choose_org=self.org)
        response = self.process_wizard(
            "create",
            create_url,
            self._form_data(contacts=[self.joe], translations=None),
        )
        self.assertEqual(200, response.status_code)

        self.login(self.customer_support, choose_org=self.org)
        response = self.process_wizard(
            "create",
            create_url,
            self._form_data(contacts=[self.joe], translations={"eng": {"text": "test"}}),
        )
        self.assertEqual(403, response.status_code)

    def test_update(self):
        optin = self.create_optin("Daily Polls")
        language = self.org.flow_languages[0]
        updated_text = {language: {"text": "Updated broadcast"}}

        broadcast = self.create_broadcast(
            self.admin,
            {language: {"text": "Please update this broadcast when you get a chance."}},
            groups=[self.joe_and_frank],
            contacts=[self.joe],
            schedule=Schedule.create(self.org, timezone.now(), Schedule.REPEAT_DAILY),
        )

        template = self.create_template(
            "Hello World",
            [
                TemplateTranslation(
                    channel=self.channel,
                    locale="eng-US",
                    status=TemplateTranslation.STATUS_APPROVED,
                    external_id="1003",
                    external_locale="en_US",
                    namespace="",
                    components=[
                        {"name": "header", "type": "header/media", "variables": {"1": 0}},
                        {
                            "name": "body",
                            "type": "body/text",
                            "content": "Hello {{1}}",
                            "variables": {"1": 1},
                        },
                    ],
                    variables=[{"type": "image"}, {"type": "text"}],
                )
            ],
        )

        update_url = reverse("msgs.broadcast_update", args=[broadcast.id])

        self.assertRequestDisallowed(update_url, [None, self.user, self.agent, self.admin2])
        self.assertUpdateFetch(update_url, [self.editor, self.admin], form_fields=("contact_search",))
        self.login(self.admin)

        response = self.process_wizard(
            "update",
            update_url,
            self._form_data(
                translations=updated_text,
                template=template,
                variables=["", "World"],
                contacts=[self.joe],
                start_datetime="2021-06-24 12:00",
                repeat_period="W",
                repeat_days_of_week=["M", "F"],
            ),
        )

        # requires an attachment
        self.assertFormError(
            response.context["form"], "compose", ["The attachment for the WhatsApp template is required."]
        )

        # now with the attachment
        response = self.process_wizard(
            "update",
            update_url,
            self._form_data(
                translations=updated_text,
                template=template,
                variables=["image/jpeg:http://domain/meow.jpg", "World"],
                contacts=[self.joe],
                start_datetime="2021-06-24 12:00",
                repeat_period="W",
                repeat_days_of_week=["M", "F"],
            ),
        )

        self.assertEqual(302, response.status_code)

        # now lets remove the template
        response = self.process_wizard(
            "update",
            update_url,
            self._form_data(
                translations={language: {"text": "Updated broadcast"}},
                contacts=[self.joe],
                optin=optin,
                start_datetime="2021-06-24 12:00",
                repeat_period="W",
                repeat_days_of_week=["M", "F"],
            ),
        )

        broadcast.refresh_from_db()
        # Update should have cleared our template
        self.assertIsNone(broadcast.template)

        # optin should be extracted from the translations form data and saved on the broadcast itself
        self.assertEqual({language: {"text": "Updated broadcast", "attachments": []}}, broadcast.translations)
        self.assertEqual(optin, broadcast.optin)

        # now lets unset the optin from the broadcast
        response = self.process_wizard(
            "update",
            update_url,
            self._form_data(
                translations=updated_text,
                contacts=[self.joe],
                start_datetime="2021-06-24 12:00",
                repeat_period="W",
                repeat_days_of_week=["M", "F"],
            ),
        )
        self.assertEqual(302, response.status_code)
        broadcast.refresh_from_db()

        # optin should be gone now
        self.assertIsNone(broadcast.optin)

        # post the first two forms
        response = self.process_wizard(
            "update",
            update_url,
            self._form_data(
                translations=updated_text,
                contacts=[self.joe],
            ),
        )

        # Update broadcast should not have the option to send now
        self.assertNotContains(response, "Send Now")

        # servicers should be able to use wizard up to the last step
        self.login(self.customer_support, choose_org=self.org)
        response = self.process_wizard(
            "update",
            update_url,
            self._form_data(
                translations=None,
                contacts=[self.joe],
            ),
        )
        self.assertEqual(200, response.status_code)

        response = self.process_wizard(
            "update",
            update_url,
            self._form_data(
                translations=updated_text,
                contacts=[self.joe],
            ),
        )
        self.assertEqual(403, response.status_code)

    def test_localization(self):
        # create a broadcast without a language
        broadcast = self.create_broadcast(
            self.admin,
            {"und": {"text": "This should end up as the language und"}},
            groups=[self.joe_and_frank],
            contacts=[self.joe],
            schedule=Schedule.create(self.org, timezone.now(), Schedule.REPEAT_DAILY),
        )
        update_url = reverse("msgs.broadcast_update", args=[broadcast.id])

        self.org.flow_languages = ["eng", "esp"]
        self.org.save()
        update_url = reverse("msgs.broadcast_update", args=[broadcast.id])

        def get_languages(response):
            return json.loads(response.context["form"]["compose"].field.widget.attrs["languages"])

        self.login(self.admin)
        response = self.process_wizard(
            "update",
            update_url,
            self._form_data(translations={}, contacts=[self.joe]),
        )

        # we only have a base language and don't have values for org languages, it should be first
        languages = get_languages(response)
        self.assertEqual("und", languages[0]["iso"])

        # add a value for the primary language
        response = self.process_wizard(
            "update",
            update_url,
            self._form_data(
                translations={"und": {"text": "undefined"}, "eng": {"text": "hello"}, "esp": {"text": "hola"}},
                contacts=[self.joe],
                start_datetime="2021-06-24 12:00",
                repeat_period="W",
                repeat_days_of_week=["M", "F"],
            ),
        )
        self.assertEqual(302, response.status_code)

        response = self.process_wizard(
            "update",
            update_url,
            self._form_data(translations={}, contacts=[self.joe]),
        )

        # We have a primary language, it should be first
        languages = get_languages(response)
        self.assertEqual("eng", languages[0]["iso"])

        # and our base language should now be last
        self.assertEqual("und", languages[-1]["iso"])

        # now mark our secondary language as the base language
        broadcast.base_language = "esp"
        broadcast.save()

        # with a secondary language as the base language, it should come first
        response = self.process_wizard(
            "update",
            update_url,
            self._form_data(translations={}, contacts=[self.joe]),
        )
        languages = get_languages(response)
        self.assertEqual("esp", languages[0]["iso"])

    @mock_mailroom
    def test_preview(self, mr_mocks):
        self.create_field("age", "Age")
        self.create_contact("Ann", phone="+16302222222", fields={"age": 40})
        self.create_contact("Bob", phone="+16303333333", fields={"age": 33})

        mr_mocks.msg_broadcast_preview(query='age > 30 AND status = "active"', total=100)

        preview_url = reverse("msgs.broadcast_preview")

        self.login(self.editor)

        response = self.client.post(
            preview_url,
            {"query": "age > 30", "exclusions": {"non_active": True}},
            content_type="application/json",
        )
        self.assertEqual(
            {"query": 'age > 30 AND status = "active"', "total": 100, "warnings": [], "blockers": []},
            response.json(),
        )

        # try with a bad query
        mr_mocks.exception(mailroom.QueryValidationException("mismatched input at (((", "syntax"))

        response = self.client.post(
            preview_url, {"query": "(((", "exclusions": {"non_active": True}}, content_type="application/json"
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual({"query": "", "total": 0, "error": "Invalid query syntax."}, response.json())

        # suspended orgs should block
        self.org.suspend()
        mr_mocks.msg_broadcast_preview(query="age > 30", total=2)
        response = self.client.post(preview_url, {"query": "age > 30"}, content_type="application/json")
        self.assertEqual(
            [
                "Sorry, your workspace is currently suspended. To re-enable starting flows and sending messages, please contact support."
            ],
            response.json()["blockers"],
        )

        # flagged orgs should block
        self.org.unsuspend()
        self.org.flag()
        mr_mocks.msg_broadcast_preview(query="age > 30", total=2)
        response = self.client.post(preview_url, {"query": "age > 30"}, content_type="application/json")
        self.assertEqual(
            [
                "Sorry, your workspace is currently flagged. To re-enable starting flows and sending messages, please contact support."
            ],
            response.json()["blockers"],
        )

        self.org.unflag()

        # if we have too many messages in our outbox we should block
        mr_mocks.msg_broadcast_preview(query="age > 30", total=2)
        self.org.counts.create(scope=f"msgs:folder:{SystemLabel.TYPE_OUTBOX}", count=1_000_001)
        response = self.client.post(preview_url, {"query": "age > 30"}, content_type="application/json")
        self.assertEqual(
            [
                "You have too many messages queued in your outbox. Please wait for these messages to send and then try again."
            ],
            response.json()["blockers"],
        )
        self.org.counts.prefix("msgs:folder:").delete()

        # if we release our send channel we can't send a broadcast
        self.channel.release(self.admin)
        mr_mocks.msg_broadcast_preview(query='age > 30 AND status = "active"', total=100)

        response = self.client.post(
            preview_url, {"query": "age > 30", "exclusions": {"non_active": True}}, content_type="application/json"
        )

        self.assertEqual(
            response.json()["blockers"][0],
            'To get started you need to <a href="/channels/channel/claim/">add a channel</a> to your workspace which will allow you to send messages to your contacts.',
        )

    @mock_mailroom
    def test_to_node(self, mr_mocks):
        to_node_url = reverse("msgs.broadcast_to_node")

        # give Joe a flow run that has stopped on a node
        flow = self.get_flow("color_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[4]
        (
            MockSessionWriter(self.joe, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        self.assertRequestDisallowed(to_node_url, [None, self.user, self.agent])

        # initialize form based on a flow node UUID
        self.assertCreateFetch(
            f"{to_node_url}?node={color_split['uuid']}&count=1", [self.editor, self.admin], form_fields=["text"]
        )

        response = self.assertCreateSubmit(
            f"{to_node_url}?node={color_split['uuid']}&count=1",
            self.admin,
            {"text": "Hurry up"},
            new_obj_query=Broadcast.objects.filter(
                translations={"und": {"text": "Hurry up"}},
                base_language="und",
                groups=None,
                contacts=None,
                node_uuid=color_split["uuid"],
            ),
            success_status=200,
        )

        self.assertEqual(1, Broadcast.objects.count())

        # if org has no send channel, show blocker
        response = self.assertCreateFetch(
            f"{to_node_url}?node=4ba8fcfa-f213-4164-a8d4-daede0a02144&count=1", [self.admin2], form_fields=["text"]
        )
        self.assertContains(response, "To get started you need to")

    def test_list(self):
        list_url = reverse("msgs.broadcast_list")

        self.assertRequestDisallowed(list_url, [None, self.agent])
        self.assertListFetch(list_url, [self.user, self.editor, self.admin], context_objects=[])
        self.assertContentMenu(list_url, self.user, [])
        self.assertContentMenu(list_url, self.admin, ["Send"])

        broadcast = self.create_broadcast(
            self.admin,
            {"eng": {"text": "Broadcast sent to one contact"}},
            contacts=[self.joe],
        )

        self.assertListFetch(list_url, [self.admin], context_objects=[broadcast])

    def test_scheduled(self):
        scheduled_url = reverse("msgs.broadcast_scheduled")

        self.assertRequestDisallowed(scheduled_url, [None, self.agent])
        self.assertListFetch(scheduled_url, [self.user, self.editor, self.admin], context_objects=[])
        self.assertContentMenu(scheduled_url, self.user, [])
        self.assertContentMenu(scheduled_url, self.admin, ["Send"])

        bc1 = self.create_broadcast(
            self.admin,
            {"eng": {"text": "good morning"}},
            contacts=[self.joe],
            schedule=Schedule.create(self.org, timezone.now(), Schedule.REPEAT_DAILY),
        )
        bc2 = self.create_broadcast(
            self.admin,
            {"eng": {"text": "good evening"}},
            contacts=[self.frank],
            schedule=Schedule.create(self.org, timezone.now(), Schedule.REPEAT_DAILY),
        )
        self.create_broadcast(self.admin, {"eng": {"text": "not_scheduled"}}, groups=[self.joe_and_frank])

        bc3 = self.create_broadcast(
            self.admin,
            {"eng": {"text": "good afternoon"}},
            contacts=[self.frank],
            schedule=Schedule.create(self.org, timezone.now(), Schedule.REPEAT_DAILY),
        )

        self.assertListFetch(scheduled_url, [self.editor], context_objects=[bc3, bc2, bc1])

        bc3.is_active = False
        bc3.save(update_fields=("is_active",))

        self.assertListFetch(scheduled_url, [self.editor], context_objects=[bc2, bc1])

    def test_scheduled_delete(self):
        self.login(self.editor)
        schedule = Schedule.create(self.org, timezone.now(), "D", repeat_days_of_week="MWF")
        broadcast = self.create_broadcast(
            self.admin,
            {"eng": {"text": "Daily reminder"}},
            groups=[self.joe_and_frank],
            schedule=schedule,
        )

        delete_url = reverse("msgs.broadcast_scheduled_delete", args=[broadcast.id])

        self.assertRequestDisallowed(delete_url, [None, self.user, self.agent, self.admin2])

        # fetch the delete modal
        response = self.assertDeleteFetch(delete_url, [self.editor, self.admin], as_modal=True)
        self.assertContains(response, "You are about to delete")

        # submit the delete modal
        response = self.assertDeleteSubmit(delete_url, self.admin, object_deactivated=broadcast, success_status=200)
        self.assertEqual("/broadcast/scheduled/", response["X-Temba-Success"])

        broadcast.refresh_from_db()

        self.assertFalse(broadcast.is_active)
        self.assertIsNone(broadcast.schedule)
        self.assertEqual(0, Schedule.objects.count())

    def test_status(self):
        broadcast = self.create_broadcast(
            self.admin,
            {"eng": {"text": "Daily reminder"}},
            groups=[self.joe_and_frank],
            status=Broadcast.STATUS_PENDING,
        )

        status_url = f"{reverse('msgs.broadcast_status')}?id={broadcast.id}&status=P"
        self.assertRequestDisallowed(status_url, [None, self.agent])
        response = self.assertReadFetch(status_url, [self.user, self.editor, self.admin])

        # status returns json
        self.assertEqual("Pending", response.json()["results"][0]["status"])

    def test_interrupt(self):
        broadcast = self.create_broadcast(
            self.admin,
            {"eng": {"text": "Daily reminder"}},
            groups=[self.joe_and_frank],
            status=Broadcast.STATUS_PENDING,
        )

        interrupt_url = reverse("msgs.broadcast_interrupt", args=[broadcast.id])
        self.assertRequestDisallowed(interrupt_url, [None, self.user, self.agent])
        self.requestView(interrupt_url, self.admin, post_data={})

        broadcast.refresh_from_db()
        self.assertEqual(Broadcast.STATUS_INTERRUPTED, broadcast.status)
