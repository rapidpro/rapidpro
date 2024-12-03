from django.urls import reverse

from temba.campaigns.models import Campaign, CampaignEvent
from temba.campaigns.views import CampaignEventCRUDL
from temba.contacts.models import ContactField
from temba.flows.models import Flow
from temba.tests import CRUDLTestMixin, TembaTest, matchers
from temba.utils.views.mixins import TEMBA_MENU_SELECTION


class CampaignEventCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.create_field("registered", "Registered", value_type="D")

        self.campaign1 = self.create_campaign(self.org, "Welcomes")
        self.other_org_campaign = self.create_campaign(self.org2, "Welcomes")

    def create_campaign(self, org, name):
        user = org.get_admins().first()
        group = self.create_group("Reporters", contacts=[], org=org)
        registered = self.org.fields.get(key="registered")
        campaign = Campaign.create(org, user, name, group)
        flow = self.create_flow(f"{name} Flow", org=org)
        background_flow = self.create_flow(f"{name} Background Flow", org=org, flow_type=Flow.TYPE_BACKGROUND)
        CampaignEvent.create_flow_event(
            org, user, campaign, registered, offset=1, unit="W", flow=flow, delivery_hour="13"
        )
        CampaignEvent.create_flow_event(
            org, user, campaign, registered, offset=2, unit="W", flow=flow, delivery_hour="13"
        )
        CampaignEvent.create_flow_event(
            org, user, campaign, registered, offset=2, unit="W", flow=background_flow, delivery_hour="13"
        )
        return campaign

    def test_read(self):
        event = self.campaign1.events.order_by("id").first()
        read_url = reverse("campaigns.campaignevent_read", args=[event.campaign.uuid, event.id])

        self.assertRequestDisallowed(read_url, [None, self.agent, self.admin2])
        response = self.assertReadFetch(read_url, [self.user, self.editor, self.admin], context_object=event)

        self.assertContains(response, "Welcomes")
        self.assertContains(response, "1 week after")
        self.assertContains(response, "Registered")
        self.assertEqual("/campaign/active/", response.headers.get(TEMBA_MENU_SELECTION))
        self.assertContentMenu(read_url, self.admin, ["Edit", "Delete"])

        event.campaign.is_archived = True
        event.campaign.save()

        # archived campaigns should focus the archived menu
        response = self.assertReadFetch(read_url, [self.editor], context_object=event)
        self.assertEqual("/campaign/archived/", response.headers.get(TEMBA_MENU_SELECTION))

        self.assertContentMenu(read_url, self.admin, ["Delete"])

    def test_create(self):
        farmer1 = self.create_contact("Rob Jasper", phone="+250788111111")
        farmer2 = self.create_contact("Mike Gordon", phone="+250788222222", language="kin")
        self.create_contact("Trey Anastasio", phone="+250788333333")
        farmers = self.create_group("Farmers", [farmer1, farmer2])

        # create a contact field for our planting date
        planting_date = self.create_field("planting_date", "Planting Date", ContactField.TYPE_DATETIME)

        # update the planting date for our contacts
        self.set_contact_field(farmer1, "planting_date", "1/10/2020")

        # create a campaign for our farmers group
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", farmers)

        create_url = f"{reverse('campaigns.campaignevent_create')}?campaign={campaign.id}"

        # update org to use a single flow language
        self.org.set_flow_languages(self.admin, ["eng"])

        non_lang_fields = [
            "event_type",
            "relative_to",
            "offset",
            "unit",
            "delivery_hour",
            "direction",
            "flow_to_start",
            "flow_start_mode",
            "message_start_mode",
        ]

        self.assertRequestDisallowed(create_url, [None, self.user, self.agent])

        response = self.assertCreateFetch(create_url, [self.editor, self.admin], form_fields=non_lang_fields + ["eng"])
        self.assertEqual(3, len(response.context["form"].fields["message_start_mode"].choices))

        # try to submit with missing fields
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {
                "event_type": "M",
                "eng": "This is my message",
                "direction": "A",
                "offset": 1,
                "unit": "W",
                "delivery_hour": 13,
            },
            form_errors={"message_start_mode": "This field is required."},
        )
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {
                "event_type": "F",
                "direction": "A",
                "offset": 1,
                "unit": "W",
                "delivery_hour": 13,
            },
            form_errors={"flow_start_mode": "This field is required.", "flow_to_start": "This field is required."},
        )

        # can create an event with just a eng translation
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {
                "relative_to": planting_date.id,
                "event_type": "M",
                "eng": "This is my message",
                "direction": "A",
                "offset": 1,
                "unit": "W",
                "flow_to_start": "",
                "delivery_hour": 13,
                "message_start_mode": "I",
            },
            new_obj_query=CampaignEvent.objects.filter(campaign=campaign, event_type="M"),
        )

        event1 = CampaignEvent.objects.get(campaign=campaign)
        self.assertEqual({"eng": "This is my message"}, event1.message)

        # add another language to our org
        self.org.set_flow_languages(self.admin, ["eng", "kin"])
        # self.org2.set_flow_languages(self.admin, ["fra", "spa"])

        response = self.assertCreateFetch(create_url, [self.admin], form_fields=non_lang_fields + ["eng", "kin"])

        # and our language list should be there
        self.assertContains(response, "show_language")

        # have to submit translation for primary language
        response = self.assertCreateSubmit(
            create_url,
            self.admin,
            {
                "relative_to": planting_date.id,
                "event_type": "M",
                "eng": "",
                "kin": "muraho",
                "direction": "B",
                "offset": 2,
                "unit": "W",
                "flow_to_start": "",
                "delivery_hour": 13,
                "message_start_mode": "I",
            },
            form_errors={"__all__": "A message is required for 'English'"},
        )

        response = self.assertCreateSubmit(
            create_url,
            self.admin,
            {
                "relative_to": planting_date.id,
                "event_type": "M",
                "eng": "hello",
                "kin": "muraho",
                "direction": "B",
                "offset": 2,
                "unit": "W",
                "flow_to_start": "",
                "delivery_hour": 13,
                "message_start_mode": "I",
            },
            new_obj_query=CampaignEvent.objects.filter(campaign=campaign, event_type="M", offset=-2),
        )

        # should be redirected back to our campaign read page
        self.assertRedirect(response, reverse("campaigns.campaign_read", args=[campaign.uuid]))

        event = CampaignEvent.objects.get(campaign=campaign, event_type="M", offset=-2)
        self.assertEqual(-2, event.offset)
        self.assertEqual(13, event.delivery_hour)
        self.assertEqual("W", event.unit)
        self.assertEqual("M", event.event_type)
        self.assertEqual("I", event.start_mode)

        self.assertEqual("hello", event.get_message(contact=farmer1))
        self.assertEqual("muraho", event.get_message(contact=farmer2))
        self.assertEqual("hello", event.get_message())

        self.assertTrue(event.flow.is_system)
        self.assertEqual("eng", event.flow.base_language)
        self.assertEqual(Flow.TYPE_BACKGROUND, event.flow.flow_type)

        flow_json = event.flow.get_definition()
        action_uuid = flow_json["nodes"][0]["actions"][0]["uuid"]

        self.assertEqual(
            {
                "uuid": str(event.flow.uuid),
                "name": f"Single Message ({event.id})",
                "spec_version": Flow.CURRENT_SPEC_VERSION,
                "revision": 1,
                "expire_after_minutes": 0,
                "language": "eng",
                "type": "messaging_background",
                "localization": {"kin": {action_uuid: {"text": ["muraho"]}}},
                "nodes": [
                    {
                        "uuid": matchers.UUID4String(),
                        "actions": [{"uuid": action_uuid, "type": "send_msg", "text": "hello"}],
                        "exits": [{"uuid": matchers.UUID4String()}],
                    }
                ],
            },
            flow_json,
        )

        update_url = reverse("campaigns.campaignevent_update", args=[event.id])

        # update the event to be passive
        response = self.assertUpdateSubmit(
            update_url,
            self.admin,
            {
                "relative_to": planting_date.id,
                "event_type": "M",
                "eng": "hello",
                "kin": "muraho",
                "direction": "B",
                "offset": 3,
                "unit": "W",
                "flow_to_start": "",
                "delivery_hour": 13,
                "message_start_mode": "P",
            },
        )

        self.assertEqual(response.status_code, 302)
        event = CampaignEvent.objects.get(is_active=True, offset=-3)

        self.assertEqual(-3, event.offset)
        self.assertEqual(13, event.delivery_hour)
        self.assertEqual("W", event.unit)
        self.assertEqual("M", event.event_type)
        self.assertEqual("P", event.start_mode)

        update_url = reverse("campaigns.campaignevent_update", args=[event.id])

        # and add another language to org
        self.org.set_flow_languages(self.admin, ["eng", "kin", "spa"])

        response = self.client.get(update_url)

        self.assertEqual("hello", response.context["form"].fields["eng"].initial)
        self.assertEqual("muraho", response.context["form"].fields["kin"].initial)
        self.assertEqual("", response.context["form"].fields["spa"].initial)
        self.assertEqual(2, len(response.context["form"].fields["flow_start_mode"].choices))

        # 'Created On' system field must be selectable in the form
        contact_fields = [field.key for field in response.context["form"].fields["relative_to"].queryset]
        self.assertEqual(contact_fields, ["created_on", "last_seen_on", "planting_date", "registered"])

        # translation in new language is optional
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {
                "relative_to": planting_date.id,
                "event_type": "M",
                "eng": "Required",
                "kin": "@fields.planting_date",
                "spa": "",
                "direction": "B",
                "offset": 1,
                "unit": "W",
                "flow_to_start": "",
                "delivery_hour": 13,
                "message_start_mode": "I",
            },
        )

        event.flow.refresh_from_db()

        # we should retain our base language
        self.assertEqual("eng", event.flow.base_language)

        # update org languages to something not including the flow's base language
        self.org.set_flow_languages(self.admin, ["por", "kin"])

        event = CampaignEvent.objects.all().order_by("id").last()
        update_url = reverse("campaigns.campaignevent_update", args=[event.id])

        self.assertRequestDisallowed(update_url, [None, self.user, self.agent, self.admin2])

        # should get new org primary language but also base language of flow
        response = self.assertUpdateFetch(
            update_url, [self.editor, self.admin], form_fields=non_lang_fields + ["por", "kin", "eng"]
        )

        self.assertEqual(response.context["form"].fields["por"].initial, "")
        self.assertEqual(response.context["form"].fields["kin"].initial, "@fields.planting_date")
        self.assertEqual(response.context["form"].fields["eng"].initial, "Required")

    def test_update(self):
        event1, event2, event3 = self.campaign1.events.order_by("id")
        other_org_event1 = self.other_org_campaign.events.order_by("id").first()

        update_url = reverse("campaigns.campaignevent_update", args=[event1.id])

        # can't view update form if not logged in
        response = self.client.get(update_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(update_url)
        self.assertEqual(
            [
                "event_type",
                "relative_to",
                "offset",
                "unit",
                "delivery_hour",
                "direction",
                "flow_to_start",
                "flow_start_mode",
                "message_start_mode",
                "eng",
                "kin",
                "loc",
            ],
            list(response.context["form"].fields.keys()),
        )

        # can't view update form for event from other org
        response = self.client.get(reverse("campaigns.campaignevent_update", args=[other_org_event1.id]))
        self.assertLoginRedirect(response)

        accepted = self.create_field("accepted", "Accepted", value_type="D")

        # update the first event
        response = self.client.post(
            update_url,
            {
                "relative_to": accepted.id,
                "event_type": "M",
                "eng": "Hi there",
                "direction": "B",
                "offset": 2,
                "unit": "D",
                "flow_to_start": "",
                "delivery_hour": 11,
                "message_start_mode": "I",
            },
        )
        self.assertEqual(302, response.status_code)

        # original event will be unchanged.. except to be inactive
        event1.refresh_from_db()
        self.assertEqual("F", event1.event_type)
        self.assertFalse(event1.is_active)

        # but will have a new replacement event
        new_event1 = self.campaign1.events.filter(id__gt=event2.id).last()

        self.assertEqual(accepted, new_event1.relative_to)
        self.assertEqual("M", new_event1.event_type)
        self.assertEqual(-2, new_event1.offset)
        self.assertEqual("D", new_event1.unit)

        # can't update event in other org
        response = self.client.post(
            update_url,
            {
                "relative_to": other_org_event1.relative_to,
                "event_type": "M",
                "eng": "Hi there",
                "direction": "B",
                "offset": 2,
                "unit": "D",
                "flow_to_start": "",
                "delivery_hour": 11,
            },
        )
        self.assertEqual(404, response.status_code)

        # check event is unchanged
        other_org_event1.refresh_from_db()
        self.assertEqual("F", other_org_event1.event_type)
        self.assertTrue(other_org_event1.is_active)

        # event based on background flow should show a warning for it's info text
        update_url = reverse("campaigns.campaignevent_update", args=[event3.id])
        response = self.client.get(update_url)
        self.assertEqual(
            CampaignEventCRUDL.BACKGROUND_WARNING,
            response.context["form"].fields["flow_to_start"].widget.attrs["info_text"],
        )

    def test_delete(self):
        # update event to have a field dependency
        event = self.campaign1.events.get(offset=1)
        update_url = reverse("campaigns.campaignevent_update", args=[event.id])
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {
                "relative_to": event.relative_to.id,
                "event_type": "M",
                "eng": "This is my message @fields.registered",
                "direction": "A",
                "offset": 1,
                "unit": "W",
                "flow_to_start": "",
                "delivery_hour": 13,
                "message_start_mode": "I",
            },
        )

        event = self.campaign1.events.get(offset=1, is_active=True)

        self.assertEqual(1, event.flow.field_dependencies.count())

        # delete the event
        self.client.post(reverse("campaigns.campaignevent_delete", args=[event.id]), dict())
        self.assertFalse(CampaignEvent.objects.filter(id=event.id).first().is_active)

        # our single message flow should be released and take its dependencies with it
        self.assertEqual(event.flow.field_dependencies.count(), 0)
