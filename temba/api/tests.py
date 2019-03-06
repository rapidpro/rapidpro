from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs

from django.conf import settings
from django.contrib.auth.models import Group
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from temba.api.models import APIToken, WebHookEvent, WebHookResult
from temba.api.tasks import retry_events_task, trim_webhook_event_task
from temba.channels.models import ChannelEvent, SyncEvent
from temba.contacts.models import TEL_SCHEME, Contact
from temba.msgs.models import FAILED, Broadcast
from temba.orgs.models import ALL_EVENTS
from temba.tests import MockResponse, TembaTest


class APITokenTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.create_secondary_org()

        self.admins_group = Group.objects.get(name="Administrators")
        self.editors_group = Group.objects.get(name="Editors")
        self.surveyors_group = Group.objects.get(name="Surveyors")

        self.org2.surveyors.add(self.admin)  # our admin can act as surveyor for other org

    def test_get_or_create(self):
        token1 = APIToken.get_or_create(self.org, self.admin)
        self.assertEqual(token1.org, self.org)
        self.assertEqual(token1.user, self.admin)
        self.assertEqual(token1.role, self.admins_group)
        self.assertTrue(token1.key)
        self.assertEqual(str(token1), token1.key)

        # tokens for different roles with same user should differ
        token2 = APIToken.get_or_create(self.org, self.admin, self.admins_group)
        token3 = APIToken.get_or_create(self.org, self.admin, self.editors_group)
        token4 = APIToken.get_or_create(self.org, self.admin, self.surveyors_group)

        self.assertEqual(token1, token2)
        self.assertNotEqual(token1, token3)
        self.assertNotEqual(token1, token4)
        self.assertNotEqual(token1.key, token3.key)

        # tokens with same role for different users should differ
        token5 = APIToken.get_or_create(self.org, self.editor)

        self.assertNotEqual(token3, token5)

        APIToken.get_or_create(self.org, self.surveyor)

        # can't create token for viewer users or other users using viewers role
        self.assertRaises(ValueError, APIToken.get_or_create, self.org, self.admin, Group.objects.get(name="Viewers"))
        self.assertRaises(ValueError, APIToken.get_or_create, self.org, self.user)

    def test_get_orgs_for_role(self):
        self.assertEqual(set(APIToken.get_orgs_for_role(self.admin, self.admins_group)), {self.org})
        self.assertEqual(set(APIToken.get_orgs_for_role(self.admin, self.surveyors_group)), {self.org, self.org2})

    def test_get_allowed_roles(self):
        self.assertEqual(
            set(APIToken.get_allowed_roles(self.org, self.admin)),
            {self.admins_group, self.editors_group, self.surveyors_group},
        )
        self.assertEqual(
            set(APIToken.get_allowed_roles(self.org, self.editor)), {self.editors_group, self.surveyors_group}
        )
        self.assertEqual(set(APIToken.get_allowed_roles(self.org, self.surveyor)), {self.surveyors_group})
        self.assertEqual(set(APIToken.get_allowed_roles(self.org, self.user)), set())

        # user from another org has no API roles
        self.assertEqual(set(APIToken.get_allowed_roles(self.org, self.admin2)), set())

    def test_get_default_role(self):
        self.assertEqual(APIToken.get_default_role(self.org, self.admin), self.admins_group)
        self.assertEqual(APIToken.get_default_role(self.org, self.editor), self.editors_group)
        self.assertEqual(APIToken.get_default_role(self.org, self.surveyor), self.surveyors_group)
        self.assertIsNone(APIToken.get_default_role(self.org, self.user))

        # user from another org has no API roles
        self.assertIsNone(APIToken.get_default_role(self.org, self.admin2))


class WebHookTest(TembaTest):
    def setUp(self):
        super().setUp()
        self.joe = self.create_contact("Joe Blow", "0788123123")
        settings.SEND_WEBHOOKS = True

    def tearDown(self):
        super().tearDown()
        settings.SEND_WEBHOOKS = False

    def setupChannel(self):
        org = self.channel.org
        org.webhook = {"url": "http://fake.com/webhook.php"}
        org.webhook_events = ALL_EVENTS
        org.save()

        self.channel.address = "+250788123123"
        self.channel.save()

    def test_call_deliveries(self):
        self.setupChannel()
        now = timezone.now()
        call = ChannelEvent.objects.create(
            org=self.org,
            channel=self.channel,
            contact=self.joe,
            contact_urn=self.joe.get_urn(),
            event_type=ChannelEvent.TYPE_CALL_IN_MISSED,
            occurred_on=now,
        )

        self.setupChannel()

        with patch("requests.Session.send") as mock:
            # clear out which events we listen for, we still shouldnt be notified though we have a webhook
            self.channel.org.webhook_events = 0
            self.channel.org.save()

            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_call_event(call)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch("requests.Session.send") as mock:
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event
            WebHookEvent.trigger_call_event(call)
            event = WebHookEvent.objects.get()

            self.assertEqual("C", event.status)
            self.assertEqual(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertEqual(200, result.status_code)
            self.assertEqual("Hello World", result.response)

            self.assertTrue(mock.called)
            args = mock.call_args_list[0][0]
            prepared_request = args[0]
            self.assertEqual(self.channel.org.get_webhook_url(), prepared_request.url)

            data = parse_qs(prepared_request.body)
            self.assertEqual("+250788123123", data["phone"][0])
            self.assertEqual(str(self.joe.get_urn(TEL_SCHEME)), data["urn"][0])
            self.assertEqual(self.joe.uuid, data["contact"][0])
            self.assertEqual(self.joe.name, data["contact_name"][0])
            self.assertEqual(call.pk, int(data["call"][0]))
            self.assertEqual(call.event_type, data["event"][0])
            self.assertIn("occurred_on", data)
            self.assertEqual(self.channel.pk, int(data["channel"][0]))

    def test_alarm_deliveries(self):
        sync_event = SyncEvent.objects.create(
            channel=self.channel,
            power_source="AC",
            power_status="CHARGING",
            power_level=85,
            network_type="WIFI",
            pending_message_count=5,
            retry_message_count=4,
            incoming_command_count=0,
            created_by=self.admin,
            modified_by=self.admin,
        )

        self.setupChannel()

        with patch("requests.Session.send") as mock:
            # clear out which events we listen for, we still shouldnt be notified though we have a webhook
            self.channel.org.webhook_events = 0
            self.channel.org.save()

            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_channel_alarm(sync_event)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch("requests.Session.send") as mock:
            mock.return_value = MockResponse(200, "")

            # trigger an event
            WebHookEvent.trigger_channel_alarm(sync_event)
            event = WebHookEvent.objects.get()

            self.assertEqual("C", event.status)
            self.assertEqual(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertEqual(200, result.status_code)
            self.assertEqual("", result.response)

            self.assertTrue(mock.called)
            args = mock.call_args_list[0][0]
            prepared_request = args[0]
            self.assertEqual(self.channel.org.get_webhook_url(), prepared_request.url)

            data = parse_qs(prepared_request.body)
            self.assertEqual(self.channel.pk, int(data["channel"][0]))
            self.assertEqual(85, int(data["power_level"][0]))
            self.assertEqual("AC", data["power_source"][0])
            self.assertEqual("CHARGING", data["power_status"][0])
            self.assertEqual("WIFI", data["network_type"][0])
            self.assertEqual(5, int(data["pending_message_count"][0]))
            self.assertEqual(4, int(data["retry_message_count"][0]))

    def test_webhook_first(self):
        self.setupChannel()
        org = self.channel.org
        org.save()

        # set our very first action to be a webhook
        flow = self.get_flow("webhook_rule_first")

        with patch("requests.Session.send") as mock_send:
            mock_send.return_value = MockResponse(200, "{}")

            # run a user through this flow
            flow.start([], [self.joe])

        event = WebHookEvent.objects.get()

        # make sure our contact still has a URN
        self.assertEqual(
            event.data["contact"],
            {"uuid": str(self.joe.uuid), "name": self.joe.name, "urn": str(self.joe.get_urn("tel"))},
        )

        # make sure we don't have an input
        self.assertNotIn("input", event.data)

    @patch("temba.api.models.time.time")
    def test_webhook_result_timing(self, mock_time):
        mock_time.side_effect = [1, 1, 1, 6, 6]

        sms = self.create_msg(contact=self.joe, direction="I", status="H", text="I'm gonna pop some tags")
        self.setupChannel()
        now = timezone.now()

        with patch("requests.Session.send") as mock:
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event
            WebHookEvent.trigger_sms_event(WebHookEvent.TYPE_SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEqual("C", event.status)
            self.assertEqual(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertEqual(200, result.status_code)
            self.assertEqual(result.request_time, 5000)

            self.assertTrue(mock_time.called)
            self.assertTrue(mock.called)

    def test_webhook_retry_task(self):
        sms = self.create_msg(contact=self.joe, direction="I", status="H", text="I'm gonna pop some tags")
        self.setupChannel()
        now = timezone.now()

        with patch("requests.Session.send") as mock:
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event
            WebHookEvent.trigger_sms_event(WebHookEvent.TYPE_SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            # mark it as errored with a retry in the past
            event.status = WebHookEvent.STATUS_ERRORED
            event.next_attempt = timezone.now()
            event.save(update_fields=["next_attempt", "status"])
            retry_events_task()
            self.assertEqual(2, mock.call_count)

            # mark it as pending more than five minutes ago
            event.status = WebHookEvent.STATUS_PENDING
            event.created_on = timezone.now() - timedelta(minutes=6)
            event.next_attempt = None
            event.save(update_fields=["created_on", "status", "next_attempt"])
            retry_events_task()
            self.assertEqual(3, mock.call_count)

            # mark it as errored and created hours hour ago
            event.status = WebHookEvent.STATUS_ERRORED
            event.created_on = timezone.now() - timedelta(hours=2)
            event.save(update_fields=["created_on", "status"])
            retry_events_task()
            self.assertEqual(4, mock.call_count)

    def test_webhook_event_trim_task(self):
        sms = self.create_msg(contact=self.joe, direction="I", status="H", text="I'm gonna pop some tags")
        self.setupChannel()
        now = timezone.now()

        with patch("requests.Session.send") as mock:
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event
            WebHookEvent.trigger_sms_event(WebHookEvent.TYPE_SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            five_hours_ago = timezone.now() - timedelta(hours=5)
            event.created_on = five_hours_ago
            event.save()
            WebHookResult.objects.all().update(created_on=five_hours_ago)

            with override_settings(SUCCESS_LOGS_TRIM_TIME=0):
                trim_webhook_event_task()
                self.assertTrue(WebHookEvent.objects.all())
                self.assertTrue(WebHookResult.objects.all())

            with override_settings(SUCCESS_LOGS_TRIM_TIME=12):
                trim_webhook_event_task()
                self.assertTrue(WebHookEvent.objects.all())
                self.assertTrue(WebHookResult.objects.all())

            with override_settings(SUCCESS_LOGS_TRIM_TIME=2):
                trim_webhook_event_task()
                self.assertFalse(WebHookEvent.objects.all())
                self.assertFalse(WebHookResult.objects.all())

            WebHookEvent.trigger_sms_event(WebHookEvent.TYPE_SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            five_hours_ago = timezone.now() - timedelta(hours=5)
            event.created_on = five_hours_ago
            event.status = FAILED
            event.save()
            WebHookResult.objects.all().update(status_code=401, created_on=five_hours_ago)

            with override_settings(ALL_LOGS_TRIM_TIME=0):
                trim_webhook_event_task()
                self.assertTrue(WebHookEvent.objects.all())
                self.assertTrue(WebHookResult.objects.all())

            with override_settings(ALL_LOGS_TRIM_TIME=12):
                trim_webhook_event_task()
                self.assertTrue(WebHookEvent.objects.all())
                self.assertTrue(WebHookResult.objects.all())

            with override_settings(ALL_LOGS_TRIM_TIME=2):
                trim_webhook_event_task()
                self.assertFalse(WebHookEvent.objects.all())
                self.assertFalse(WebHookResult.objects.all())

    def test_event_deliveries(self):
        sms = self.create_msg(contact=self.joe, direction="I", status="H", text="I'm gonna pop some tags")

        with patch("requests.Session.send") as mock:
            now = timezone.now()
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_sms_event(WebHookEvent.TYPE_SMS_RECEIVED, sms, now)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch("requests.Session.send") as mock:
            # clear out which events we listen for, we still shouldnt be notified though we have a webhook
            self.channel.org.webhook_events = 0
            self.channel.org.save()

            now = timezone.now()
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_sms_event(WebHookEvent.TYPE_SMS_RECEIVED, sms, now)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch("requests.Session.send") as mock:
            # remove all the org users
            self.org.administrators.clear()
            self.org.editors.clear()
            self.org.viewers.clear()

            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event
            WebHookEvent.trigger_sms_event(WebHookEvent.TYPE_SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEqual("F", event.status)
            self.assertEqual(0, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertEqual(0, result.status_code)

            self.assertFalse(mock.called)

            # what if they send weird json back?
            self.release(WebHookEvent.objects.all())
            self.release(WebHookResult.objects.all())

        # add ad manager back in
        self.org.administrators.add(self.admin)
        self.admin.set_org(self.org)

        with patch("requests.Session.send") as mock:
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event
            WebHookEvent.trigger_sms_event(WebHookEvent.TYPE_SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEqual("C", event.status)
            self.assertEqual(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertEqual(200, result.status_code)

            self.assertTrue(mock.called)

            self.release(WebHookEvent.objects.all())
            self.release(WebHookResult.objects.all())

        with patch("requests.Session.send") as mock:
            mock.side_effect = [MockResponse(500, "I am error")]

            # trigger an event
            WebHookEvent.trigger_sms_event(WebHookEvent.TYPE_SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.all().first()

            self.assertEqual("E", event.status)
            self.assertEqual(1, event.try_count)
            self.assertTrue(event.next_attempt)

            mock.return_value = MockResponse(200, "Hello World")
            # simulate missing channel
            event.channel = None
            event.save()

            # no exception should raised
            event.deliver()

            self.assertTrue(mock.called)
            self.assertEqual(mock.call_count, 2)

            self.release(WebHookEvent.objects.all())
            self.release(WebHookResult.objects.all())

        with patch("requests.Session.send") as mock:
            # valid json, but not our format
            bad_json = '{ "thrift_shops": ["Goodwill", "Value Village"] }'
            mock.return_value = MockResponse(200, bad_json)

            WebHookEvent.trigger_sms_event(WebHookEvent.TYPE_SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEqual("C", event.status)
            self.assertEqual(1, event.try_count)
            self.assertFalse(event.next_attempt)

            self.assertTrue(mock.called)

            result = WebHookResult.objects.get()
            self.assertEqual(200, result.status_code)
            self.assertEqual(bad_json, result.response)

            self.release(WebHookEvent.objects.all())
            self.release(WebHookResult.objects.all())

        with patch("requests.Session.send") as mock:
            mock.return_value = MockResponse(200, '{ "phone": "+250788123123", "text": "I am success" }')

            WebHookEvent.trigger_sms_event(WebHookEvent.TYPE_SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEqual("C", event.status)
            self.assertEqual(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertEqual(200, result.status_code)

            self.assertTrue(mock.called)

            broadcast = Broadcast.objects.get()
            contact, urn_obj = Contact.get_or_create(self.org, "tel:+250788123123", self.channel, user=self.admin)
            self.assertTrue(broadcast.text, {"base": "I am success"})
            self.assertTrue(contact, broadcast.contacts.all())

            self.assertTrue(mock.called)
            args = mock.call_args_list[0][0]
            prepared_request = args[0]
            self.assertEqual(self.org.get_webhook_url(), prepared_request.url)

            data = parse_qs(prepared_request.body)
            self.assertEqual(self.joe.get_urn(TEL_SCHEME).path, data["phone"][0])
            self.assertEqual(str(self.joe.get_urn(TEL_SCHEME)), data["urn"][0])
            self.assertEqual(self.joe.uuid, data["contact"][0])
            self.assertEqual(self.joe.name, data["contact_name"][0])
            self.assertEqual(sms.pk, int(data["sms"][0]))
            self.assertEqual(self.channel.pk, int(data["channel"][0]))
            self.assertEqual(WebHookEvent.TYPE_SMS_RECEIVED, data["event"][0])
            self.assertEqual("I'm gonna pop some tags", data["text"][0])
            self.assertIn("time", data)

            self.release(WebHookEvent.objects.all())
            self.release(WebHookResult.objects.all())

        with patch("requests.Session.send") as mock:
            mock.return_value = MockResponse(500, "I am error")

            next_attempt_earliest = timezone.now() + timedelta(minutes=4)
            next_attempt_latest = timezone.now() + timedelta(minutes=6)

            WebHookEvent.trigger_sms_event(WebHookEvent.TYPE_SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEqual("E", event.status)
            self.assertEqual(1, event.try_count)
            self.assertTrue(event.next_attempt)
            self.assertTrue(next_attempt_earliest < event.next_attempt and next_attempt_latest > event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertEqual(500, result.status_code)
            self.assertEqual("I am error", result.response)

            # make sure things become failures after three retries
            event.try_count = 2
            event.deliver()
            event.save()

            self.assertTrue(mock.called)

            self.assertEqual("F", event.status)
            self.assertEqual(3, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertEqual(500, result.status_code)
            self.assertEqual("I am error", result.response)
            self.assertEqual("http://fake.com/webhook.php", result.url)
            self.assertTrue(result.request.find("pop+some+tags") > 0)

            # check out our api log
            response = self.client.get(reverse("api.webhookresult_list"))
            self.assertRedirect(response, reverse("users.user_login"))

            response = self.client.get(reverse("api.webhookresult_read", args=[event.pk]))
            self.assertRedirect(response, reverse("users.user_login"))

            self.login(self.admin)

            response = self.client.get(reverse("api.webhookresult_list"))
            self.assertContains(response, "http://fake.com/webhook.php")

            response = self.client.get(reverse("api.webhookresult_read", args=[result.pk]))
            self.assertContains(response, "http://fake.com/webhook.php")

            self.release(WebHookEvent.objects.all())
            self.release(WebHookResult.objects.all())

        # add a webhook header to the org
        self.channel.org.webhook = {
            "url": "http://fake.com/webhook.php",
            "headers": {"X-My-Header": "foobar", "Authorization": "Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=="},
            "method": "POST",
        }
        self.channel.org.save()

        # check that our webhook settings have saved
        self.assertEqual("http://fake.com/webhook.php", self.channel.org.get_webhook_url())
        self.assertDictEqual(
            {"X-My-Header": "foobar", "Authorization": "Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=="},
            self.channel.org.get_webhook_headers(),
        )

        with patch("requests.Session.send") as mock:
            mock.return_value = MockResponse(200, "Boom")
            WebHookEvent.trigger_sms_event(WebHookEvent.TYPE_SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            result = WebHookResult.objects.get()
            # both headers should be in the json-encoded url string
            self.assertIn("X-My-Header: foobar", result.request)
            self.assertIn("Authorization: Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ==", result.request)

    def test_webhook(self):
        response = self.client.get(reverse("api.webhook"))
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Simulator")

        response = self.client.get(reverse("api.webhook_simulator"))
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Log in")

        self.login(self.admin)
        response = self.client.get(reverse("api.webhook_simulator"))
        self.assertEqual(200, response.status_code)
        self.assertNotContains(response, "Log in")

    def test_tunnel(self):
        response = self.client.post(reverse("api.webhook_tunnel"), dict())
        self.assertEqual(302, response.status_code)

        self.login(self.non_org_user)

        with patch("requests.post") as mock:
            mock.return_value = MockResponse(200, '{ "phone": "+250788123123", "text": "I am success" }')

            response = self.client.post(
                reverse("api.webhook_tunnel"),
                dict(url="http://webhook.url/", data="phone=250788383383&values=foo&bogus=2"),
            )
            self.assertEqual(200, response.status_code)
            self.assertContains(response, "I am success")
            self.assertIn("values", mock.call_args[1]["data"])
            self.assertIn("phone", mock.call_args[1]["data"])
            self.assertNotIn("bogus", mock.call_args[1]["data"])

            response = self.client.post(reverse("api.webhook_tunnel"), dict())
            self.assertContains(response, "Must include", status_code=400)
