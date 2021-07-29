from temba.campaigns.models import Campaign, CampaignEvent
from temba.flows.models import Flow
from temba.tests import TembaTest
from temba.triggers.models import Trigger

from .temba import object_class_name, object_url, verbose_name_plural


class TembaTagLibraryTest(TembaTest):
    def test_verbose_name_plural(self):
        flow = self.create_flow()
        group = self.create_group("Testers", contacts=[])

        self.assertEqual("Flows", verbose_name_plural(flow))
        self.assertEqual("Groups", verbose_name_plural(group))
        self.assertEqual("Campaigns", verbose_name_plural(Campaign()))
        self.assertEqual("Campaign Events", verbose_name_plural(CampaignEvent()))

    def test_object_url(self):
        flow = self.create_flow()
        group = self.create_group("Testers", contacts=[])

        self.assertEqual(f"/flow/editor/{flow.uuid}/", object_url(flow))
        self.assertEqual(f"/contact/filter/{group.uuid}/", object_url(group))

    def test_object_class_plural(self):
        self.assertEqual("Flow", object_class_name(Flow()))
        self.assertEqual("Campaign", object_class_name(Campaign()))
        self.assertEqual("CampaignEvent", object_class_name(CampaignEvent()))
        self.assertEqual("Trigger", object_class_name(Trigger()))
