from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from temba.classifiers.models import Classifier
from temba.classifiers.types.wit import WitType
from temba.tests import TembaTest

from .models import HTTPLog
from .tasks import trim_http_logs_task


class ClassifierTest(TembaTest):
    def setUp(self):
        super().setUp()
        self.setUpSecondaryOrg()

        # create some classifiers
        self.c1 = Classifier.create(self.org, self.admin, WitType.slug, "Booker", {}, sync=False)
        self.c1.intents.create(
            name="book_flight", external_id="book_flight", created_on=timezone.now(), is_active=True
        )
        self.c1.intents.create(name="book_hotel", external_id="book_hotel", created_on=timezone.now(), is_active=False)
        self.c1.intents.create(name="book_car", external_id="book_car", created_on=timezone.now(), is_active=True)

        self.c2 = Classifier.create(self.org, self.admin, WitType.slug, "Old Booker", {}, sync=False)
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
        l2 = HTTPLog.objects.create(
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

        # move l1 to be from a week ago
        l1.created_on = timezone.now() - timedelta(days=7)
        l1.save(update_fields=["created_on"])

        trim_http_logs_task()

        # should only have one log remaining and should be l2
        self.assertEqual(1, HTTPLog.objects.all().count())
        self.assertIsNotNone(HTTPLog.objects.filter(id=l2.id))
