from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from temba.request_logs.models import HTTPLog
from temba.tests import MockResponse, TembaTest

from .models import Classifier
from .tasks import sync_classifier_intents
from .types.luis import LuisType
from .types.wit import WitType

INTENT_RESPONSE = """
{
  "builtin": false,
  "name": "intent",
  "doc": "User-defined entity",
  "id": "ef9236ec-22c7-e96b-6b29-886c94d23953",
  "lang": "en",
  "lookups": [
    "trait"
  ],
  "values": [
    {
      "value": "book_car",
      "expressions": [
      ]
    },
    {
      "value": "book_hotel",
      "expressions": [
      ]
    },
    {
      "value": "book_horse",
      "expressions": [
      ]
    }
  ]
}
"""


class ClassifierTest(TembaTest):
    def setUp(self):
        super().setUp()
        self.setUpSecondaryOrg()

        # create some classifiers
        self.c1 = Classifier.create(self.org, self.admin, WitType.slug, "Booker", {})
        self.c1.intents.create(
            name="book_flight", external_id="book_flight", created_on=timezone.now(), is_active=True
        )
        self.c1.intents.create(name="book_hotel", external_id="book_hotel", created_on=timezone.now(), is_active=False)
        self.c1.intents.create(name="book_car", external_id="book_car", created_on=timezone.now(), is_active=True)

        self.c2 = Classifier.create(self.org, self.admin, WitType.slug, "Old Booker", {})
        self.c2.is_active = False
        self.c2.save()

        # on another org
        self.c3 = Classifier.create(self.org2, self.admin, LuisType.slug, "Org2 Booker", {})

    def test_syncing(self):
        # will fail due to missing keys
        sync_classifier_intents(self.c1.id)

        # no intents should have been changed / removed as this was an error
        self.assertEqual(2, self.c1.active_intents().count())

        # ok, fix our config
        self.c1.config = {WitType.CONFIG_ACCESS_TOKEN: "sesasme", WitType.CONFIG_APP_ID: "1234"}
        self.c1.save()

        # try again
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, INTENT_RESPONSE)
            sync_classifier_intents(self.c1.id)

            # should have three active intents
            intents = self.c1.active_intents()
            self.assertEqual(3, intents.count())
            self.assertEqual("book_car", intents[0].name)
            self.assertEqual("book_car", intents[0].external_id)
            self.assertEqual("book_horse", intents[1].name)
            self.assertEqual("book_horse", intents[1].external_id)
            self.assertEqual("book_hotel", intents[2].name)
            self.assertEqual("book_hotel", intents[2].external_id)

            # one inactive
            self.assertEqual(1, self.c1.intents.filter(is_active=False).count())

            # one classifier log
            self.assertEqual(1, HTTPLog.objects.filter(classifier=self.c1, org=self.org).count())

    def test_templates(self):
        # fetch org home page
        self.login(self.admin)
        response = self.client.get(reverse("orgs.org_home"))

        # should contain classifier
        self.assertContains(response, "Booker")
        self.assertNotContains(response, "Old Booker")
        self.assertNotContains(response, "Org 2 Booker")

        connect_url = reverse("classifiers.classifier_connect")
        response = self.client.get(reverse("orgs.org_home"))
        self.assertContains(response, connect_url)

        read_url = reverse("classifiers.classifier_read", args=[self.c1.uuid])
        self.assertContains(response, read_url)

        # read page
        response = self.client.get(read_url)

        # contains intents
        self.assertContains(response, "book_flight")
        self.assertNotContains(response, "book_hotel")
        self.assertContains(response, "book_car")

        # and link to logs
        log_url = reverse("request_logs.httplog_list", args=["classifier", self.c1.uuid])
        self.assertContains(response, log_url)
