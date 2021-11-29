from datetime import timedelta

from requests import RequestException

from django.urls import reverse
from django.utils import timezone

from temba.classifiers.models import Classifier
from temba.classifiers.types.wit import WitType
from temba.tests import CRUDLTestMixin, MockResponse, TembaTest
from temba.tickets.models import Ticketer
from temba.tickets.types.mailgun import MailgunType

from .models import HTTPLog
from .tasks import trim_http_logs_task


class HTTPLogTest(TembaTest):
    def test_trim_logs_task(self):
        c1 = Classifier.create(self.org, self.admin, WitType.slug, "Booker", {}, sync=False)

        HTTPLog.objects.create(
            classifier=c1,
            url="http://org1.bar/zap/?text=" + ("0123456789" * 30),
            request="GET /zap",
            response=" OK 200",
            is_error=False,
            log_type=HTTPLog.INTENTS_SYNCED,
            request_time=10,
            org=self.org,
            created_on=timezone.now() - timedelta(days=7),
        )
        l2 = HTTPLog.objects.create(
            classifier=c1,
            url="http://org2.bar/zap",
            request="GET /zap",
            response=" OK 200",
            is_error=False,
            log_type=HTTPLog.CLASSIFIER_CALLED,
            request_time=10,
            org=self.org,
        )

        trim_http_logs_task()

        # should only have one log remaining and should be l2
        self.assertEqual(1, HTTPLog.objects.all().count())
        self.assertTrue(HTTPLog.objects.filter(id=l2.id))


class HTTPLogCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_webhooks(self):
        flow = self.create_flow()
        l1 = HTTPLog.objects.create(
            org=self.org,
            log_type="webhook_called",
            url="http://org1.bar/",
            request="GET /zap",
            response=" OK 200",
            request_time=10,
            is_error=False,
            flow=flow,
        )

        # log from other org
        HTTPLog.objects.create(
            org=self.org2,
            log_type="webhook_called",
            url="http://org1.bar/",
            request="GET /zap",
            response=" OK 200",
            request_time=10,
            is_error=False,
            flow=flow,
        )

        # non-webhook log
        HTTPLog.objects.create(
            org=self.org,
            log_type="intents_synced",
            url="http://org1.bar/",
            request="GET /zap",
            response=" OK 200",
            request_time=10,
            is_error=False,
        )

        webhooks_url = reverse("request_logs.httplog_webhooks")
        log_url = reverse("request_logs.httplog_read", args=[l1.id])

        response = self.assertListFetch(webhooks_url, allow_viewers=False, allow_editors=True, context_objects=[l1])
        self.assertContains(response, "Webhook Calls")
        self.assertContains(response, log_url)

        # view the individual log item
        response = self.assertReadFetch(log_url, allow_viewers=False, allow_editors=True, context_object=l1)
        self.assertContains(response, "200")
        self.assertContains(response, "http://org1.bar/")

    def test_classifier(self):
        c1 = Classifier.create(self.org, self.admin, WitType.slug, "Booker", {}, sync=False)
        c2 = Classifier.create(self.org, self.admin, WitType.slug, "Old Booker", {}, sync=False)
        c2.is_active = False
        c2.save()

        l1 = HTTPLog.objects.create(
            classifier=c1,
            url="http://org1.bar/zap/?text=" + ("0123456789" * 30),
            request="GET /zap",
            response=" OK 200",
            is_error=False,
            log_type=HTTPLog.INTENTS_SYNCED,
            request_time=10,
            org=self.org,
        )
        HTTPLog.objects.create(
            classifier=c2,
            url="http://org2.bar/zap",
            request="GET /zap",
            response=" OK 200",
            is_error=False,
            log_type=HTTPLog.CLASSIFIER_CALLED,
            request_time=10,
            org=self.org,
        )

        list_url = reverse("request_logs.httplog_classifier", args=[c1.uuid])
        log_url = reverse("request_logs.httplog_read", args=[l1.id])

        response = self.assertListFetch(
            list_url, allow_viewers=False, allow_editors=False, allow_org2=False, context_objects=[l1]
        )
        self.assertContains(response, "Intents Synced")
        self.assertContains(response, log_url)
        self.assertNotContains(response, "Classifier Called")

        # view the individual log item
        response = self.assertReadFetch(log_url, allow_viewers=False, allow_editors=False, context_object=l1)
        self.assertContains(response, "200")
        self.assertContains(response, "http://org1.bar/zap")
        self.assertNotContains(response, "http://org2.bar/zap")

        # can't list logs for deleted classifier
        response = self.requestView(reverse("request_logs.httplog_classifier", args=[c2.uuid]), self.admin)
        self.assertEqual(404, response.status_code)

    def test_ticketer(self):
        t1 = Ticketer.create(self.org, self.admin, MailgunType.slug, "Email (bob@acme.com)", {})
        t2 = Ticketer.create(self.org, self.admin, MailgunType.slug, "Old Email", {})
        t2.is_active = False
        t2.save()

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

        list_url = reverse("request_logs.httplog_ticketer", args=[t1.uuid])
        log_url = reverse("request_logs.httplog_read", args=[l1.id])

        response = self.assertListFetch(
            list_url, allow_viewers=False, allow_editors=False, allow_org2=False, context_objects=[l1]
        )
        self.assertContains(response, "Ticketing Service Called")
        self.assertContains(response, log_url)

        # view the individual log item
        response = self.assertReadFetch(log_url, allow_viewers=False, allow_editors=False, context_object=l1)
        self.assertContains(response, "200")
        self.assertContains(response, "http://org1.bar/zap")
        self.assertNotContains(response, "http://org2.bar/zap")

        # can't list logs for deleted ticketer
        response = self.requestView(reverse("request_logs.httplog_ticketer", args=[t2.uuid]), self.admin)
        self.assertEqual(404, response.status_code)

    def test_channel_log(self):
        channel = self.create_channel("WA", "WhatsApp: 1234", "1234")

        exception = RequestException("Network is unreachable", response=MockResponse(100, ""))
        start = timezone.now()

        log1 = HTTPLog.create_from_exception(
            HTTPLog.WHATSAPP_TEMPLATES_SYNCED,
            "https://graph.facebook.com/v3.3/1234/message_templates",
            exception,
            start,
            channel=channel,
        )

        self.login(self.admin)
        log_url = reverse("request_logs.httplog_read", args=[log1.id])
        response = self.client.get(log_url)
        self.assertContains(response, "200")
        self.assertContains(response, "Connection Error")
        self.assertContains(response, "https://graph.facebook.com/v3.3/1234/message_templates")

        # and can't be from other org
        self.login(self.admin2)
        response = self.client.get(log_url)
        self.assertLoginRedirect(response)
