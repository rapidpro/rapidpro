from django.urls import reverse

from temba.campaigns.models import Campaign, CampaignEvent
from temba.tests import CRUDLTestMixin, TembaTest, matchers, mock_mailroom


class CampaignCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.create_field("registered", "Registered", value_type="D")
        self.create_field("registered", "Registered", value_type="D", org=self.org2)

    def create_campaign(self, org, name, group):
        user = org.get_admins().first()
        registered = org.fields.get(key="registered")
        flow = self.create_flow(f"{name} Flow", org=org)
        campaign = Campaign.create(org, user, name, group)
        CampaignEvent.create_flow_event(
            org, user, campaign, registered, offset=1, unit="W", flow=flow, delivery_hour="13"
        )
        return campaign

    def test_menu(self):
        menu_url = reverse("campaigns.campaign_menu")

        group = self.create_group("My Group", contacts=[])
        self.create_campaign(self.org, "My Campaign", group)

        self.assertRequestDisallowed(menu_url, [None, self.agent])
        self.assertPageMenu(menu_url, self.admin, ["Active (1)", "Archived (0)"])

    def test_create(self):
        group = self.create_group("Reporters", contacts=[])

        create_url = reverse("campaigns.campaign_create")

        self.assertRequestDisallowed(create_url, [None, self.user, self.agent])
        self.assertCreateFetch(create_url, [self.editor, self.admin], form_fields=["name", "group"])

        # try to submit with no data
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {},
            form_errors={"name": "This field is required.", "group": "This field is required."},
        )

        # submit with valid data
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Reminders", "group": group.id},
            new_obj_query=Campaign.objects.filter(name="Reminders", group=group),
        )

    def test_read(self):
        group = self.create_group("Reporters", contacts=[])
        campaign = self.create_campaign(self.org, "Welcomes", group)
        read_url = reverse("campaigns.campaign_read", args=[campaign.uuid])

        self.assertRequestDisallowed(read_url, [None, self.agent, self.admin2])
        response = self.assertReadFetch(read_url, [self.user, self.editor, self.admin], context_object=campaign)
        self.assertContains(response, "Welcomes")
        self.assertContains(response, "Registered")

        self.assertContentMenu(read_url, self.admin, ["New Event", "Edit", "Export", "Archive"])

        campaign.archive(self.admin)

        self.assertContentMenu(read_url, self.admin, ["Activate", "Export"])

    def test_archive_and_activate(self):
        group = self.create_group("Reporters", contacts=[])
        campaign = self.create_campaign(self.org, "Welcomes", group)
        other_org_group = self.create_group("Reporters", contacts=[], org=self.org2)
        other_org_campaign = self.create_campaign(self.org2, "Welcomes", other_org_group)

        archive_url = reverse("campaigns.campaign_archive", args=[campaign.id])

        # can't archive campaign if not logged in
        response = self.client.post(archive_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.post(archive_url)
        self.assertEqual(302, response.status_code)

        campaign.refresh_from_db()
        self.assertTrue(campaign.is_archived)

        # activate that archve
        response = self.client.post(reverse("campaigns.campaign_activate", args=[campaign.id]))
        self.assertEqual(302, response.status_code)

        campaign.refresh_from_db()
        self.assertFalse(campaign.is_archived)

        # can't archive campaign from other org
        response = self.client.post(reverse("campaigns.campaign_archive", args=[other_org_campaign.id]))
        self.assertEqual(302, response.status_code)

        # check object is unchanged
        other_org_campaign.refresh_from_db()
        self.assertFalse(other_org_campaign.is_archived)

    @mock_mailroom
    def test_update(self, mr_mocks):
        group1 = self.create_group("Reporters", contacts=[])
        group2 = self.create_group("Testers", query="tester=1")

        campaign = self.create_campaign(self.org, "Welcomes", group1)

        update_url = reverse("campaigns.campaign_update", args=[campaign.id])

        self.assertRequestDisallowed(update_url, [None, self.user, self.agent, self.admin2])
        self.assertUpdateFetch(
            update_url, [self.editor, self.admin], form_fields={"name": "Welcomes", "group": group1.id}
        )

        # try to submit with empty name
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "", "group": group1.id},
            form_errors={"name": "This field is required."},
            object_unchanged=campaign,
        )

        # submit with valid name
        self.assertUpdateSubmit(update_url, self.admin, {"name": "Greetings", "group": group1.id}, success_status=200)

        campaign.refresh_from_db()
        self.assertEqual("Greetings", campaign.name)
        self.assertEqual(group1, campaign.group)

        # group didn't change so should only have dynamic group creation queued
        self.assertEqual(1, len(mr_mocks.queued_batch_tasks))

        # submit with group change
        self.assertUpdateSubmit(update_url, self.admin, {"name": "Greetings", "group": group2.id}, success_status=200)

        campaign.refresh_from_db()
        self.assertEqual("Greetings", campaign.name)
        self.assertEqual(group2, campaign.group)

        # should have a task queued to reschedule the campaign's event
        self.assertEqual(2, len(mr_mocks.queued_batch_tasks))
        self.assertEqual(
            {
                "type": "schedule_campaign_event",
                "org_id": self.org.id,
                "task": {"campaign_event_id": campaign.events.filter(is_active=True).get().id, "org_id": self.org.id},
                "queued_on": matchers.Datetime(),
            },
            mr_mocks.queued_batch_tasks[1],
        )

    def test_list(self):
        group = self.create_group("Reporters", contacts=[])
        campaign1 = self.create_campaign(self.org, "Welcomes", group)
        campaign2 = self.create_campaign(self.org, "Follow Ups", group)

        other_org_group = self.create_group("Reporters", contacts=[], org=self.org2)
        self.create_campaign(self.org2, "Welcomes", other_org_group)

        list_url = reverse("campaigns.campaign_list")

        self.assertRequestDisallowed(list_url, [None, self.agent])
        self.assertListFetch(list_url, [self.user, self.editor, self.admin], context_objects=[campaign2, campaign1])
        self.assertContentMenu(list_url, self.user, [])
        self.assertContentMenu(list_url, self.admin, ["New Campaign"])
