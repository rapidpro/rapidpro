from datetime import timedelta
from unittest.mock import Mock

from requests import Request, RequestException

from django.urls import reverse
from django.utils import timezone

from temba.classifiers.models import Classifier
from temba.classifiers.types.wit import WitType
from temba.tests import CRUDLTestMixin, TembaTest
from temba.utils.views import TEMBA_MENU_SELECTION

from .models import HTTPLog
from .tasks import trim_http_logs


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

        trim_http_logs()

        # should only have one log remaining and should be l2
        self.assertEqual(1, HTTPLog.objects.all().count())
        self.assertTrue(HTTPLog.objects.filter(id=l2.id))


class HTTPLogCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_webhooks(self):
        flow = self.create_flow("Test")
        l1 = HTTPLog.objects.create(
            org=self.org,
            log_type="webhook_called",
            url="http://org1.bar/",
            request="GET /zap\nHost: org1.bar\n\n",
            response=" OK 200",
            request_time=10,
            is_error=False,
            flow=flow,
        )

        # log with error
        l2 = HTTPLog.objects.create(
            org=self.org,
            log_type="webhook_called",
            url="http://org1.bar/",
            request="GET /zap\nHost: org1.bar\n\n",
            response=" ERROR 400",
            request_time=10,
            is_error=True,
            flow=flow,
        )

        # log from other org
        HTTPLog.objects.create(
            org=self.org2,
            log_type="webhook_called",
            url="http://org1.bar/",
            request="GET /zap\nHost: org1.bar\n\n",
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
            request="GET /zap\nHost: org1.bar\n\n",
            response=" OK 200",
            request_time=10,
            is_error=False,
        )

        webhooks_url = reverse("request_logs.httplog_webhooks")
        log_url = reverse("request_logs.httplog_read", args=[l1.id])
        error_log_url = reverse("request_logs.httplog_read", args=[l2.id])

        self.assertRequestDisallowed(webhooks_url, [None, self.user, self.agent])
        response = self.assertListFetch(webhooks_url, [self.editor, self.admin], context_objects=[l2, l1])
        self.assertContains(response, "Webhooks")
        self.assertContains(response, log_url)
        self.assertContains(response, error_log_url)
        self.assertContentMenu(webhooks_url, self.admin, ["Errors"])

        response = self.assertListFetch(webhooks_url + "?error=1", [self.editor, self.admin], context_objects=[l2])
        self.assertContains(response, "Failed Webhooks")
        self.assertNotContains(response, log_url)
        self.assertContains(response, error_log_url)
        self.assertContentMenu(webhooks_url + "?error=1", self.admin, ["All logs"])

        # view the individual log item
        self.assertRequestDisallowed(log_url, [None, self.user, self.agent, self.admin2])
        response = self.assertReadFetch(log_url, [self.editor, self.admin], context_object=l1)
        self.assertContains(response, "200")
        self.assertContains(response, "org1.bar")

        response = self.assertReadFetch(log_url, [self.editor], context_object=l1)
        self.assertEqual("/flow/history/webhooks", response.headers.get(TEMBA_MENU_SELECTION))

    def test_channel(self):
        ch1 = self.create_channel("WAC", "WhatsApp: 1234", "1234")
        ch2 = self.create_channel("WAC", "WhatsApp: 2345", "2345")

        l1 = HTTPLog.objects.create(
            channel=ch1,
            url="http://org1.bar/zap/?text=" + ("0123456789" * 30),
            request="GET /zap\nHost: org1.bar\n\n",
            response=" OK 200",
            is_error=False,
            log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED,
            request_time=10,
            org=self.org,
        )
        l2 = HTTPLog.objects.create(
            channel=ch1,
            url="http://org2.bar/zap",
            request="GET /zap\nHost: org2.bar\n\n",
            response=" OK 200",
            is_error=False,
            log_type=HTTPLog.WHATSAPP_CONTACTS_REFRESHED,
            request_time=10,
            org=self.org,
        )
        HTTPLog.objects.create(
            channel=ch2,
            url="http://org2.bar/zap",
            request="GET /zap\nHost: org2.bar\n\n",
            response=" OK 200",
            is_error=False,
            log_type=HTTPLog.WHATSAPP_TOKENS_SYNCED,
            request_time=10,
            org=self.org,
        )

        list_url = reverse("request_logs.httplog_channel", args=[ch1.uuid])

        self.assertRequestDisallowed(list_url, [None, self.user, self.editor, self.agent, self.admin2])
        self.assertListFetch(list_url, [self.admin], context_objects=[l2, l1])

    def test_classifier(self):
        c1 = Classifier.create(self.org, self.admin, WitType.slug, "Booker", {}, sync=False)
        c2 = Classifier.create(self.org, self.admin, WitType.slug, "Old Booker", {}, sync=False)
        c2.is_active = False
        c2.save()

        l1 = HTTPLog.objects.create(
            classifier=c1,
            url="http://org1.bar/zap/?text=" + ("0123456789" * 30),
            request="GET /zap\nHost: org1.bar\n\n",
            response=" OK 200",
            is_error=False,
            log_type=HTTPLog.INTENTS_SYNCED,
            request_time=10,
            org=self.org,
        )
        HTTPLog.objects.create(
            classifier=c2,
            url="http://org2.bar/zap",
            request="GET /zap\nHost: org2.bar\n\n",
            response=" OK 200",
            is_error=False,
            log_type=HTTPLog.CLASSIFIER_CALLED,
            request_time=10,
            org=self.org,
        )

        list_url = reverse("request_logs.httplog_classifier", args=[c1.uuid])
        log_url = reverse("request_logs.httplog_read", args=[l1.id])

        self.assertRequestDisallowed(list_url, [None, self.user, self.editor, self.agent, self.admin2])
        response = self.assertListFetch(list_url, [self.admin], context_objects=[l1])

        menu_path = f"/settings/classifiers/{c1.uuid}"

        self.assertEqual(menu_path, response.headers[TEMBA_MENU_SELECTION])
        self.assertContains(response, "Intents Synced")
        self.assertContains(response, menu_path)
        self.assertNotContains(response, "Classifier Called")

        # view the individual log item
        self.assertRequestDisallowed(log_url, [None, self.user, self.agent, self.editor, self.admin2])
        response = self.assertReadFetch(log_url, [self.admin], context_object=l1)
        self.assertEqual(menu_path, response.headers[TEMBA_MENU_SELECTION])
        self.assertContains(response, "200")
        self.assertContains(response, "org1.bar")
        self.assertNotContains(response, "org2.bar")

        # can't list logs for deleted classifier
        response = self.requestView(reverse("request_logs.httplog_classifier", args=[c2.uuid]), self.admin)
        self.assertEqual(404, response.status_code)

    def test_http_log(self):
        channel = self.create_channel("WAC", "WhatsApp: 1234", "1234")
        exception = RequestException(
            "Network is unreachable",
            request=Mock(
                Request,
                method="GET",
                url="https://graph.facebook.com/v18.0/1234/message_templates?access_token=MISSING_WHATSAPP_ADMIN_SYSTEM_USER_TOKEN",
                body=b"{}",
                headers={},
            ),
        )
        start = timezone.now()

        log1 = HTTPLog.from_exception(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, exception, start, channel=channel)

        self.login(self.admin)
        log_url = reverse("request_logs.httplog_read", args=[log1.id])
        response = self.client.get(log_url)
        self.assertContains(response, "200")
        self.assertContains(response, "Connection Error")
        self.assertContains(response, "/v18.0/1234/message_templates")

        log2 = HTTPLog.from_exception(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, exception, start, channel=channel)
        log2_url = reverse("request_logs.httplog_read", args=[log2.id])
        response = self.client.get(log2_url)
        self.assertContains(response, "200")
        self.assertContains(response, "Connection Error")
        self.assertContains(response, "/v18.0/1234/message_templates?access_token=********")

        # and can't be from other org
        self.login(self.admin2)
        response = self.client.get(log_url)
        self.assertLoginRedirect(response)
