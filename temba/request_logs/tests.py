from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from temba.classifiers.models import Classifier
from temba.classifiers.types.wit import WitType
from temba.tests import TembaTest
from temba.tickets.models import Ticketer
from temba.tickets.types.mailgun import MailgunType

from .models import HTTPLog
from .tasks import trim_http_logs_task


class HTTPLogTest(TembaTest):
    def test_classifier(self):
        # create some classifiers
        c1 = Classifier.create(self.org, self.admin, WitType.slug, "Booker", {}, sync=False)
        c1.intents.create(name="book_flight", external_id="book_flight", created_on=timezone.now(), is_active=True)
        c1.intents.create(name="book_hotel", external_id="book_hotel", created_on=timezone.now(), is_active=False)
        c1.intents.create(name="book_car", external_id="book_car", created_on=timezone.now(), is_active=True)

        c2 = Classifier.create(self.org, self.admin, WitType.slug, "Old Booker", {}, sync=False)
        c2.is_active = False
        c2.save()

        log_url = reverse("request_logs.httplog_classifier", args=[c1.uuid])
        response = self.client.get(log_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # create some logs
        l1 = HTTPLog.objects.create(
            classifier=c1,
            url="http://org1.bar/zap",
            request="GET /zap",
            response=" OK 200",
            is_error=False,
            log_type=HTTPLog.INTENTS_SYNCED,
            request_time=10,
            org=self.org,
        )
        l2 = HTTPLog.objects.create(
            classifier=c2,
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

        self.login(self.admin2)
        response = self.client.get(log_url)
        self.assertLoginRedirect(response)

        # move l1 to be from a week ago
        l1.created_on = timezone.now() - timedelta(days=7)
        l1.save(update_fields=["created_on"])

        trim_http_logs_task()

        # should only have one log remaining and should be l2
        self.assertEqual(1, HTTPLog.objects.all().count())
        self.assertTrue(HTTPLog.objects.filter(id=l2.id))

        # release l2
        l2.release()
        self.assertFalse(HTTPLog.objects.filter(id=l2.id))

    def test_ticketer(self):
        self.customer_support.is_staff = True
        self.customer_support.save()

        # create some ticketers
        t1 = Ticketer.create(self.org, self.admin, MailgunType.slug, "Email (bob@acme.com)", {})
        t2 = Ticketer.create(self.org, self.admin, MailgunType.slug, "Old Email", {})
        t2.is_active = False
        t2.save()

        list_url = reverse("request_logs.httplog_ticketer", args=[t1.uuid])
        response = self.client.get(list_url)
        self.assertLoginRedirect(response)

        # even admins can't view ticketer logs
        self.login(self.admin)
        response = self.client.get(list_url)
        self.assertLoginRedirect(response)

        # customer support can
        self.login(self.customer_support)
        response = self.client.get(list_url)
        self.assertEqual(200, response.status_code)

        # create some logs
        l1 = HTTPLog.objects.create(
            ticketer=t1,
            url="http://org1.bar/zap",
            request="GET /zap",
            response=" OK 200",
            is_error=False,
            log_type=HTTPLog.TICKETER_CALLED,
            request_time=10,
            org=self.org,
        )

        response = self.client.get(list_url)
        self.assertEqual(1, len(response.context["object_list"]))
        self.assertContains(response, "Ticketing Service Called")

        log_url = reverse("request_logs.httplog_read", args=[l1.id])
        self.assertContains(response, log_url)

        # view the individual log item
        response = self.client.get(log_url)
        self.assertContains(response, "200")
        self.assertContains(response, "http://org1.bar/zap")
        self.assertNotContains(response, "http://org2.bar/zap")

        # still need to be customer support to do that
        self.login(self.admin)
        response = self.client.get(log_url)
        self.assertLoginRedirect(response)

        # and can't be from other org
        self.login(self.admin2)
        response = self.client.get(log_url)
        self.assertLoginRedirect(response)

        self.login(self.customer_support)

        # can't list logs for deleted ticketer
        list_url = reverse("request_logs.httplog_ticketer", args=[t2.uuid])
        response = self.client.get(list_url)
        self.assertEqual(404, response.status_code)
