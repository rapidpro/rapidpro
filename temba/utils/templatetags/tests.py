from datetime import datetime, timezone as tzone

from temba.campaigns.models import Campaign, CampaignEvent
from temba.flows.models import Flow
from temba.tests import TembaTest
from temba.triggers.models import Trigger

from . import temba as tags


class TembaTagLibraryTest(TembaTest):
    def test_verbose_name_plural(self):
        flow = self.create_flow("Test")
        group = self.create_group("Testers", contacts=[])

        self.assertEqual("Flows", tags.verbose_name_plural(flow))
        self.assertEqual("Groups", tags.verbose_name_plural(group))
        self.assertEqual("Campaigns", tags.verbose_name_plural(Campaign()))
        self.assertEqual("Campaign Events", tags.verbose_name_plural(CampaignEvent()))

    def test_object_url(self):
        flow = self.create_flow("Test")
        group = self.create_group("Testers", contacts=[])

        self.assertEqual(f"/flow/editor/{flow.uuid}/", tags.object_url(flow))
        self.assertEqual(f"/contact/filter/{group.uuid}/", tags.object_url(group))

    def test_object_class_plural(self):
        self.assertEqual("Flow", tags.object_class_name(Flow()))
        self.assertEqual("Campaign", tags.object_class_name(Campaign()))
        self.assertEqual("CampaignEvent", tags.object_class_name(CampaignEvent()))
        self.assertEqual("Trigger", tags.object_class_name(Trigger()))

    def test_first_word(self):
        self.assertEqual("First", tags.first_word("First Second"))
        self.assertEqual("First", tags.first_word("First"))
        self.assertEqual("", tags.first_word(""))

    def test_unsnake(self):
        self.assertEqual("Rapid Pro", tags.unsnake("rapid_pro"))
        self.assertEqual("Contact Birth Year", tags.unsnake("contact_birth_year"))

    def test_day(self):
        self.assertEqual(
            '<temba-date value="2024-04-03T14:45:30+00:00" display="date"></temba-date>',
            tags.day(datetime(2024, 4, 3, 14, 45, 30, 0, tzone.utc)),
        )
        self.assertEqual(
            '<temba-date value="2024-04-03T14:45:30+00:00" display="date"></temba-date>',
            tags.day("2024-04-03T14:45:30+00:00"),
        )

    def test_datetime(self):
        self.assertEqual(
            '<temba-date value="2024-04-03T14:45:30+00:00" display="datetime"></temba-date>',
            tags.datetime(datetime(2024, 4, 3, 14, 45, 30, 0, tzone.utc)),
        )
        self.assertEqual(
            '<temba-date value="2024-04-03T14:45:30+00:00" display="datetime"></temba-date>',
            tags.datetime("2024-04-03T14:45:30+00:00"),
        )

    def test_duration(self):
        self.assertEqual(
            '<temba-date value="2024-04-03T14:45:30+00:00" display="duration"></temba-date>',
            tags.duration(datetime(2024, 4, 3, 14, 45, 30, 0, tzone.utc)),
        )
        self.assertEqual(
            '<temba-date value="2024-04-03T14:45:30+00:00" display="duration"></temba-date>',
            tags.duration("2024-04-03T14:45:30+00:00"),
        )
