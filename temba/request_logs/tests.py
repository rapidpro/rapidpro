from django.urls import reverse
from django.utils import timezone

from temba.classifiers.models import Classifier
from temba.classifiers.types.wit import WitType
from temba.tests import TembaTest

from .models import HTTPLog


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

    def test_templates(self):
        log_url = reverse("request_logs.httplog_list", args=["classifier", self.c1.uuid])
        response = self.client.get(log_url)
        self.assertRedirect(response, "/users/login/")

        self.login(self.admin)

        # create some logs
        l1 = HTTPLog.objects.create(
            classifier=self.c1,
            url="http://org1.bar/zap",
            request="GET /zap",
            response=" OK 200",
            is_error=False,
            log_type=HTTPLog.INTENTS_SYNCED,
            request_time=10,
            org=self.org,
        )
        HTTPLog.objects.create(
            classifier=self.c2,
            url="http://org2.bar/zap",
            request="GET /zap",
            response=" OK 200",
            is_error=False,
            log_type=HTTPLog.CLASSIFIER_CALLED,
            request_time=10,
            org=self.org,
        )

        response = self.client.get(log_url)
        self.assertEqual(1, len(response.context["object_list"]))
        self.assertContains(response, "Intents Synced")
        self.assertNotContains(response, "Classifier Called")

        log_url = reverse("request_logs.httplog_read", args=[l1.id])
        self.assertContains(response, log_url)

        response = self.client.get(log_url)
        self.assertContains(response, "200")
        self.assertContains(response, "http://org1.bar/zap")
        self.assertNotContains(response, "http://org2.bar/zap")
