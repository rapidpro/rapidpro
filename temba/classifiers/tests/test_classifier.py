from unittest.mock import patch

from django.utils import timezone

from temba.classifiers.models import Classifier
from temba.classifiers.types.wit import WitType
from temba.request_logs.models import HTTPLog
from temba.tests import MockResponse, TembaTest

INTENT_RESPONSE = """
[
    {
        "id": "754569408690533",
        "name": "book_car"
    },
    {
        "id": "754569408690020",
        "name": "book_horse"
    },
    {
        "id": "754569408690131",
        "name": "book_hotel"
    }
]
"""


class ClassifierTest(TembaTest):
    def setUp(self):
        super().setUp()

        # create some classifiers
        self.c1 = Classifier.create(self.org, self.admin, WitType.slug, "Booker", {}, sync=False)
        self.c1.intents.create(name="book_flight", external_id="book_flight", created_on=timezone.now(), is_active=True)
        self.c1.intents.create(
            name="book_hotel", external_id="754569408690131", created_on=timezone.now(), is_active=False
        )
        self.c1.intents.create(
            name="book_car", external_id="754569408690533", created_on=timezone.now(), is_active=True
        )

    def test_syncing(self):
        # will fail due to missing keys
        self.c1.async_sync()

        # no intents should have been changed / removed as this was an error
        self.assertEqual(2, self.c1.active_intents().count())

        # ok, fix our config
        self.c1.config = {WitType.CONFIG_ACCESS_TOKEN: "sesasme", WitType.CONFIG_APP_ID: "1234"}
        self.c1.save()

        # try again
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, INTENT_RESPONSE)
            self.c1.async_sync()

            # should have three active intents
            intents = self.c1.active_intents()
            self.assertEqual(3, intents.count())
            self.assertEqual("book_car", intents[0].name)
            self.assertEqual("754569408690533", intents[0].external_id)
            self.assertEqual("book_horse", intents[1].name)
            self.assertEqual("754569408690020", intents[1].external_id)
            self.assertEqual("book_hotel", intents[2].name)
            self.assertEqual("754569408690131", intents[2].external_id)

            # one inactive
            self.assertEqual(1, self.c1.intents.filter(is_active=False).count())

            # one classifier log
            self.assertEqual(1, HTTPLog.objects.filter(classifier=self.c1, org=self.org).count())
