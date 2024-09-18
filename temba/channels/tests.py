import base64
import hashlib
import hmac
import time
from datetime import date, datetime, timedelta, timezone as tzone
from unittest.mock import patch
from urllib.parse import quote

from smartmin.tests import SmartminTest

from django.conf import settings
from django.contrib.auth.models import Group
from django.core import mail
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes

from temba.apks.models import Apk
from temba.contacts.models import URN, Contact
from temba.msgs.models import Msg
from temba.notifications.incidents.builtin import ChannelDisconnectedIncidentType, ChannelOutdatedAppIncidentType
from temba.notifications.models import Incident
from temba.notifications.tasks import send_notification_emails
from temba.orgs.models import Org
from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from temba.tests import CRUDLTestMixin, MockResponse, TembaTest, matchers, mock_mailroom, override_brand
from temba.tests.crudl import StaffRedirect
from temba.triggers.models import Trigger
from temba.utils import json
from temba.utils.models import generate_uuid
from temba.utils.views import TEMBA_MENU_SELECTION

from .models import Channel, ChannelCount, ChannelEvent, ChannelLog, SyncEvent
from .tasks import (
    check_android_channels,
    squash_channel_counts,
    track_org_channel_counts,
    trim_channel_events,
    trim_channel_logs,
    trim_channel_sync_events,
)


class ChannelTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.channel.delete()

        self.tel_channel = self.create_channel(
            "A", "Test Channel", "+250785551212", country="RW", secret="12345", config={"FCM_ID": "123"}
        )
        self.twitter_channel = self.create_channel("TWT", "Twitter Channel", "billy_bob")

        self.unclaimed_channel = self.create_channel("NX", "Unclaimed Channel", "", config={"FCM_ID": "000"})
        self.unclaimed_channel.org = None
        self.unclaimed_channel.save(update_fields=("org",))

    def claim_new_android(self, fcm_id: str = "FCM111", number: str = "0788123123") -> Channel:
        """
        Helper function to register and claim a new Android channel
        """
        cmds = [dict(cmd="fcm", fcm_id=fcm_id, uuid="uuid"), dict(cmd="status", cc="RW", dev="Nexus")]
        response = self.client.post(reverse("register"), json.dumps({"cmds": cmds}), content_type="application/json")
        self.assertEqual(200, response.status_code)

        android = Channel.objects.order_by("id").last()

        self.login(self.admin)
        response = self.client.post(
            reverse("channels.types.android.claim"), {"claim_code": android.claim_code, "phone_number": number}
        )
        self.assertRedirect(response, "/welcome/")

        android.refresh_from_db()
        return android

    def assertHasCommand(self, cmd_name, response):
        self.assertEqual(200, response.status_code)
        data = response.json()

        for cmd in data["cmds"]:
            if cmd["cmd"] == cmd_name:
                return

        raise Exception("Did not find '%s' cmd in response: '%s'" % (cmd_name, response.content))

    def test_deactivate(self):
        self.login(self.admin)
        self.tel_channel.is_active = False
        self.tel_channel.save()
        response = self.client.get(reverse("channels.channel_read", args=[self.tel_channel.uuid]))
        self.assertEqual(404, response.status_code)

    def test_get_address_display(self):
        self.assertEqual("+250 785 551 212", self.tel_channel.get_address_display())
        self.assertEqual("+250785551212", self.tel_channel.get_address_display(e164=True))

        self.assertEqual("@billy_bob", self.twitter_channel.get_address_display())

        # make sure it works with alphanumeric numbers
        self.tel_channel.address = "EATRIGHT"
        self.assertEqual("EATRIGHT", self.tel_channel.get_address_display())
        self.assertEqual("EATRIGHT", self.tel_channel.get_address_display(e164=True))

        self.tel_channel.address = ""
        self.assertEqual("", self.tel_channel.get_address_display())

    def test_ensure_normalization(self):
        self.tel_channel.country = "RW"
        self.tel_channel.save()

        contact1 = self.create_contact("contact1", phone="0788111222")
        contact2 = self.create_contact("contact2", phone="+250788333444")
        contact3 = self.create_contact("contact3", phone="+18006927753")

        self.org.normalize_contact_tels()

        norm_c1 = Contact.objects.get(pk=contact1.pk)
        norm_c2 = Contact.objects.get(pk=contact2.pk)
        norm_c3 = Contact.objects.get(pk=contact3.pk)

        self.assertEqual(norm_c1.get_urn(URN.TEL_SCHEME).path, "+250788111222")
        self.assertEqual(norm_c2.get_urn(URN.TEL_SCHEME).path, "+250788333444")
        self.assertEqual(norm_c3.get_urn(URN.TEL_SCHEME).path, "+18006927753")

    def test_channel_create(self):
        # can't use an invalid scheme for a fixed-scheme channel type
        with self.assertRaises(ValueError):
            Channel.create(
                self.org,
                self.user,
                "KE",
                "AT",
                None,
                "+250788123123",
                config=dict(username="at-user", api_key="africa-key"),
                uuid="00000000-0000-0000-0000-000000001234",
                schemes=["fb"],
            )

        # a scheme is required
        with self.assertRaises(ValueError):
            Channel.create(
                self.org,
                self.user,
                "US",
                "EX",
                None,
                "+12065551212",
                uuid="00000000-0000-0000-0000-000000001234",
                schemes=[],
            )

        # country channels can't have scheme
        with self.assertRaises(ValueError):
            Channel.create(
                self.org,
                self.user,
                "US",
                "EX",
                None,
                "+12065551212",
                uuid="00000000-0000-0000-0000-000000001234",
                schemes=["fb"],
            )

    @mock_mailroom
    def test_release(self, mr_mocks):
        # create two channels..
        channel1 = Channel.create(
            self.org, self.user, "RW", "A", "Test Channel", "0785551212", config={Channel.CONFIG_FCM_ID: "123"}
        )
        channel2 = Channel.create(self.org, self.user, "", "T", "Test Channel", "0785553333")

        # add channel trigger
        flow = self.create_flow("Test")
        Trigger.create(self.org, self.admin, Trigger.TYPE_CATCH_ALL, flow, channel=channel1)

        # create some activity on this channel
        contact = self.create_contact("Bob", phone="+593979123456")
        self.create_incoming_msg(contact, "Hi", channel=channel1)
        self.create_outgoing_msg(contact, "Hi", channel=channel1, status="P")
        self.create_outgoing_msg(contact, "Hi", channel=channel1, status="E")
        self.create_outgoing_msg(contact, "Hi", channel=channel1, status="S")
        ChannelDisconnectedIncidentType.get_or_create(channel1)
        SyncEvent.create(
            channel1,
            dict(p_src="AC", p_sts="DIS", p_lvl=80, net="WIFI", pending=[1, 2], retry=[3, 4], cc="RW"),
            [1, 2],
        )
        self.create_template(
            "reminder",
            [
                TemplateTranslation(
                    channel=channel1,
                    locale="eng",
                    status="A",
                    external_locale="en",
                    components=[],
                    variables=[],
                )
            ],
        )

        # and some on another channel
        self.create_outgoing_msg(contact, "Hi", channel=channel2, status="E")
        ChannelDisconnectedIncidentType.get_or_create(channel2)
        SyncEvent.create(
            channel2,
            dict(p_src="AC", p_sts="DIS", p_lvl=80, net="WIFI", pending=[1, 2], retry=[3, 4], cc="RW"),
            [1, 2],
        )
        self.create_template(
            "reminder2",
            [
                TemplateTranslation(
                    channel=channel2,
                    locale="eng",
                    status="A",
                    external_locale="en",
                    components=[],
                    variables=[],
                )
            ],
        )
        Trigger.create(self.org, self.admin, Trigger.TYPE_CATCH_ALL, flow, channel=channel2)

        # add channel to a flow as a dependency
        flow.channel_dependencies.add(channel1)

        channel1.release(self.admin)

        flow.refresh_from_db()
        self.assertTrue(flow.has_issues)
        self.assertNotIn(channel1, flow.channel_dependencies.all())
        self.assertEqual(0, channel1.triggers.filter(is_active=True).count())
        self.assertEqual(0, channel1.incidents.filter(ended_on=None).count())
        self.assertEqual(0, channel1.template_translations.count())

        # check that we queued a task to interrupt sessions tied to this channel
        self.assertEqual(
            {
                "org_id": self.org.id,
                "type": "interrupt_channel",
                "queued_on": matchers.Datetime(),
                "task": {"channel_id": channel1.id},
            },
            mr_mocks.queued_batch_tasks[-1],
        )

        # other channel should be unaffected
        self.assertEqual(1, channel2.msgs.filter(status="E").count())
        self.assertEqual(1, channel2.sync_events.count())
        self.assertEqual(1, channel2.triggers.filter(is_active=True).count())
        self.assertEqual(1, channel2.incidents.filter(ended_on=None).count())
        self.assertEqual(1, channel2.template_translations.count())

        # now do actual delete of channel
        channel1.msgs.all().delete()
        channel1.org.notifications.all().delete()
        channel1.delete()

        self.assertFalse(Channel.objects.filter(id=channel1.id).exists())

    @mock_mailroom
    def test_release_android(self, mr_mocks):
        android = self.claim_new_android()
        self.assertEqual("FCM111", android.config.get(Channel.CONFIG_FCM_ID))

        # release it
        android.release(self.admin)
        android.refresh_from_db()

        response = self.sync(android, cmds=[])
        self.assertEqual(200, response.status_code)

        # should be a rel cmd to instruct app to reset
        self.assertEqual({"cmds": [{"cmd": "rel", "relayer_id": str(android.id)}]}, response.json())

        self.assertFalse(android.is_active)
        # and FCM ID now kept
        self.assertEqual("FCM111", android.config.get(Channel.CONFIG_FCM_ID))

    def sync(self, channel, *, cmds, signature=None, auto_add_fcm=True):
        # prepend FCM command if not included
        if auto_add_fcm and (not cmds or cmds[0]["cmd"] != "fcm"):
            cmds = [{"cmd": "fcm", "fcm_id": "3256262", "uuid": str(channel.uuid), "p_id": 1}] + cmds

        post_data = json.dumps({"cmds": cmds})
        ts = int(time.time())

        if not signature:
            # sign the request
            key = str(channel.secret) + str(ts)
            signature = hmac.new(key=force_bytes(key), msg=force_bytes(post_data), digestmod=hashlib.sha256).digest()

            # base64 and url sanitize
            signature = quote(base64.urlsafe_b64encode(signature))

        return self.client.post(
            "%s?signature=%s&ts=%d" % (reverse("sync", args=[channel.id]), signature, ts),
            content_type="application/json",
            data=post_data,
        )

    def test_chart(self):
        chart_url = reverse("channels.channel_chart", args=[self.tel_channel.uuid])

        self.assertRequestDisallowed(chart_url, [None, self.agent, self.admin2])
        self.assertReadFetch(chart_url, [self.user, self.editor, self.admin])

        # create some test messages
        test_date = datetime(2020, 1, 20, 0, 0, 0, 0, tzone.utc)
        test_date - timedelta(hours=2)
        bob = self.create_contact("Bob", phone="+250785551212")
        joe = self.create_contact("Joe", phone="+2501234567890")

        with patch("django.utils.timezone.now", return_value=test_date):
            self.create_outgoing_msg(bob, "Hey there Bob", channel=self.tel_channel)
            self.create_incoming_msg(joe, "This incoming message will be counted", channel=self.tel_channel)
            self.create_outgoing_msg(joe, "This outgoing message will be counted", channel=self.tel_channel)

            response = self.fetch_protected(chart_url, self.admin)
            chart = response.json()

            # an entry for each incoming and outgoing
            self.assertEqual(2, len(chart["series"]))

            # one incoming message in the first entry
            self.assertEqual(1, chart["series"][0]["data"][0][1])

            # two outgoing messages in the second entry
            self.assertEqual(2, chart["series"][1]["data"][0][1])

    def test_read(self):
        # now send the channel's updates
        self.sync(
            self.tel_channel,
            cmds=[
                # device details status
                dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="60", net="UMTS", pending=[], retry=[])
            ],
        )

        # now send the channel's updates
        self.sync(
            self.tel_channel,
            cmds=[
                # device details status
                dict(cmd="status", p_sts="FUL", p_src="AC", p_lvl="100", net="WIFI", pending=[], retry=[])
            ],
        )
        self.assertEqual(2, SyncEvent.objects.all().count())

        # non-org users can't view our channels
        self.login(self.non_org_user)

        tel_channel_read_url = reverse("channels.channel_read", args=[self.tel_channel.uuid])
        response = self.client.get(tel_channel_read_url)
        self.assertRedirect(response, reverse("orgs.org_choose"))

        self.login(self.user)

        response = self.client.get(tel_channel_read_url)
        self.assertEqual(f"/settings/channels/{self.tel_channel.uuid}", response.headers[TEMBA_MENU_SELECTION])

        # org users can
        response = self.fetch_protected(tel_channel_read_url, self.user)

        self.assertTrue(len(response.context["latest_sync_events"]) <= 5)

        response = self.fetch_protected(tel_channel_read_url, self.admin)
        self.assertContains(response, self.tel_channel.name)

        test_date = datetime(2020, 1, 20, 0, 0, 0, 0, tzone.utc)
        two_hours_ago = test_date - timedelta(hours=2)
        # make sure our channel is old enough to trigger alerts
        self.tel_channel.created_on = two_hours_ago
        self.tel_channel.save()

        # delayed sync status
        for sync in SyncEvent.objects.all():
            sync.created_on = two_hours_ago
            sync.save()

        bob = self.create_contact("Bob", phone="+250785551212")

        # add a message, just sent so shouldn't be delayed
        with patch("django.utils.timezone.now", return_value=two_hours_ago):
            self.create_outgoing_msg(bob, "delayed message", status=Msg.STATUS_QUEUED, channel=self.tel_channel)

        with patch("django.utils.timezone.now", return_value=test_date):
            response = self.fetch_protected(tel_channel_read_url, self.admin)
            self.assertIn("delayed_sync_event", response.context_data.keys())
            self.assertIn("unsent_msgs_count", response.context_data.keys())

            # now that we can access the channel, which messages do we display in the chart?
            joe = self.create_contact("Joe", phone="+2501234567890")

            # we have one row for the message stats table
            self.assertEqual(1, len(response.context["message_stats_table"]))
            # only one outgoing message
            self.assertEqual(0, response.context["message_stats_table"][0]["incoming_messages_count"])
            self.assertEqual(1, response.context["message_stats_table"][0]["outgoing_messages_count"])
            self.assertEqual(0, response.context["message_stats_table"][0]["incoming_ivr_count"])
            self.assertEqual(0, response.context["message_stats_table"][0]["outgoing_ivr_count"])

            # send messages
            self.create_incoming_msg(joe, "This incoming message will be counted", channel=self.tel_channel)
            self.create_outgoing_msg(joe, "This outgoing message will be counted", channel=self.tel_channel)

            # now we have an inbound message and two outbounds
            response = self.fetch_protected(tel_channel_read_url, self.admin)
            self.assertEqual(200, response.status_code)

            # message stats table have an inbound and two outbounds in the last month
            self.assertEqual(1, len(response.context["message_stats_table"]))
            self.assertEqual(1, response.context["message_stats_table"][0]["incoming_messages_count"])
            self.assertEqual(2, response.context["message_stats_table"][0]["outgoing_messages_count"])
            self.assertEqual(0, response.context["message_stats_table"][0]["incoming_ivr_count"])
            self.assertEqual(0, response.context["message_stats_table"][0]["outgoing_ivr_count"])

            # test cases for IVR messaging, make our relayer accept calls
            self.tel_channel.role = "SCAR"
            self.tel_channel.save()

            # now let's create an ivr interaction
            self.create_incoming_msg(joe, "incoming ivr", channel=self.tel_channel, voice=True)
            self.create_outgoing_msg(joe, "outgoing ivr", channel=self.tel_channel, voice=True)
            response = self.fetch_protected(tel_channel_read_url, self.admin)

            self.assertEqual(1, len(response.context["message_stats_table"]))
            self.assertEqual(1, response.context["message_stats_table"][0]["incoming_messages_count"])
            self.assertEqual(2, response.context["message_stats_table"][0]["outgoing_messages_count"])
            self.assertEqual(1, response.context["message_stats_table"][0]["incoming_ivr_count"])
            self.assertEqual(1, response.context["message_stats_table"][0]["outgoing_ivr_count"])

            # look at the chart for our messages
            chart_url = reverse("channels.channel_chart", args=[self.tel_channel.uuid])
            response = self.fetch_protected(chart_url, self.admin)

            # incoming, outgoing for both text and our ivr messages
            self.assertEqual(4, len(response.json()["series"]))

        # as staff
        self.requestView(tel_channel_read_url, self.customer_support, checks=[StaffRedirect()])

    def test_invalid(self):
        # Must be POST
        response = self.client.get(
            "%s?signature=sig&ts=123" % (reverse("sync", args=[100])), content_type="application/json"
        )
        self.assertEqual(500, response.status_code)

        # Unknown channel
        response = self.client.post(
            "%s?signature=sig&ts=123" % (reverse("sync", args=[999])), content_type="application/json"
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("rel", response.json()["cmds"][0]["cmd"])

        # too old
        ts = int(time.time()) - 60 * 16
        response = self.client.post(
            "%s?signature=sig&ts=%d" % (reverse("sync", args=[self.tel_channel.pk]), ts),
            content_type="application/json",
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual(3, response.json()["error_id"])

        # missing initial FCM command
        response = self.sync(self.tel_channel, cmds=[], auto_add_fcm=False)
        self.assertEqual(401, response.status_code)
        self.assertEqual(4, response.json()["error_id"])

    def test_claim(self):
        # no access for regular users
        self.login(self.user)
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertLoginRedirect(response)

        # editor can access
        self.login(self.editor)
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertEqual(200, response.status_code)

        # as can admins
        self.login(self.admin)
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertEqual(200, response.status_code)

        # 3 recommended channels for Rwanda
        self.assertEqual(["AT", "MT", "TG"], [t.code for t in response.context["recommended_channels"]])

        self.assertEqual(response.context["channel_types"]["PHONE"][0].code, "CT")
        self.assertEqual(response.context["channel_types"]["PHONE"][1].code, "EX")
        self.assertEqual(response.context["channel_types"]["PHONE"][2].code, "I2")
        self.assertEqual(response.context["channel_types"]["PHONE"][-1].code, "A")

        self.org.timezone = "Canada/Central"
        self.org.save()

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertEqual(200, response.status_code)

        self.assertEqual(["TG", "TMS", "T", "NX"], [t.code for t in response.context["recommended_channels"]])

        self.assertEqual(response.context["channel_types"]["PHONE"][0].code, "CT")
        self.assertEqual(response.context["channel_types"]["PHONE"][1].code, "EX")
        self.assertEqual(response.context["channel_types"]["PHONE"][2].code, "I2")
        self.assertEqual(response.context["channel_types"]["PHONE"][-1].code, "A")

        with override_settings(ORG_LIMIT_DEFAULTS={"channels": 2}):
            response = self.client.get(reverse("channels.channel_claim"))
            self.assertEqual(200, response.status_code)

            self.assertEqual(2, response.context["total_count"])
            self.assertEqual(2, response.context["total_limit"])
            self.assertContains(
                response,
                "You have reached the limit of 2 channels per workspace. Please remove channels that you are no longer using.",
            )

        with override_settings(ORG_LIMIT_DEFAULTS={"channels": 3}):
            response = self.client.get(reverse("channels.channel_claim"))
            self.assertEqual(200, response.status_code)

            self.assertEqual(2, response.context["total_count"])
            self.assertEqual(3, response.context["total_limit"])
            self.assertContains(
                response,
                "You are approaching the limit of 3 channels per workspace. You should remove channels that you are no longer using.",
            )

    def test_claim_all(self):
        # no access for regular users
        self.login(self.user)
        response = self.client.get(reverse("channels.channel_claim_all"))
        self.assertLoginRedirect(response)

        # editor can access
        self.login(self.editor)
        response = self.client.get(reverse("channels.channel_claim_all"))
        self.assertEqual(200, response.status_code)

        # as can admins
        self.login(self.admin)
        response = self.client.get(reverse("channels.channel_claim_all"))
        self.assertEqual(200, response.status_code)

        # should see all channel types not for beta only and having a category
        self.assertEqual(["AT", "MT", "TG"], [t.code for t in response.context["recommended_channels"]])

        self.assertEqual(response.context["channel_types"]["PHONE"][0].code, "AC")
        self.assertEqual(response.context["channel_types"]["PHONE"][1].code, "BL")
        self.assertEqual(response.context["channel_types"]["PHONE"][2].code, "BS")
        self.assertEqual(response.context["channel_types"]["PHONE"][-1].code, "A")

        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][0].code, "D3C")
        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][1].code, "FBA")
        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][2].code, "IG")
        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][-2].code, "WC")
        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][-1].code, "ZVW")

        self.admin.groups.add(Group.objects.get(name="Beta"))

        response = self.client.get(reverse("channels.channel_claim_all"))
        self.assertEqual(200, response.status_code)

        # should see all channel types having a category including beta only channel types
        self.assertEqual(["AT", "MT", "TG"], [t.code for t in response.context["recommended_channels"]])

        self.assertEqual(response.context["channel_types"]["PHONE"][0].code, "AC")
        self.assertEqual(response.context["channel_types"]["PHONE"][1].code, "BW")
        self.assertEqual(response.context["channel_types"]["PHONE"][2].code, "BL")
        self.assertEqual(response.context["channel_types"]["PHONE"][3].code, "BS")
        self.assertEqual(response.context["channel_types"]["PHONE"][-1].code, "A")

        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][0].code, "D3C")
        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][1].code, "FBA")
        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][2].code, "IG")
        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][-2].code, "WA")
        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][-1].code, "ZVW")

    def test_sync_unclaimed(self):
        response = self.sync(self.unclaimed_channel, cmds=[])
        self.assertEqual(401, response.status_code)

        # should be an error response
        self.assertEqual({"error": "Can't sync unclaimed channel", "error_id": 4, "cmds": []}, response.json())

        self.unclaimed_channel.secret = "12345674674"
        self.unclaimed_channel.uuid = generate_uuid()
        self.unclaimed_channel.claim_code = "ABCDEFGH9"
        self.unclaimed_channel.save(update_fields=("secret", "uuid", "claim_code"))

        response = self.sync(self.unclaimed_channel, cmds=[])
        self.assertEqual(200, response.status_code)

        response_json = response.json()
        self.assertEqual(
            response_json,
            dict(
                cmds=[
                    dict(
                        cmd="reg",
                        relayer_claim_code="ABCDEFGH9",
                        relayer_secret="12345674674",
                        relayer_id=self.unclaimed_channel.pk,
                    )
                ]
            ),
        )

        # Not matching UUID should be an error
        response = self.sync(
            self.unclaimed_channel,
            cmds=[{"cmd": "fcm", "fcm_id": "3256262", "uuid": str(generate_uuid()), "p_id": 1}],
            auto_add_fcm=False,
        )
        self.assertEqual(401, response.status_code)

        # should be an error response
        self.assertEqual({"error": "Can't sync unclaimed channel", "error_id": 4, "cmds": []}, response.json())

    @mock_mailroom
    def test_sync_client_reset(self, mr_mocks):
        android = self.claim_new_android()

        response = self.sync(android, cmds=[{"cmd": "reset"}])
        self.assertEqual(200, response.status_code)

        android.refresh_from_db()
        self.assertFalse(android.is_active)

    def test_sync_broadcast_multiple_channels(self):
        channel2 = Channel.create(
            self.org,
            self.user,
            "RW",
            "A",
            name="Test Channel 2",
            address="+250785551313",
            role="SR",
            secret="12367",
            config={Channel.CONFIG_FCM_ID: "456"},
        )

        contact1 = self.create_contact("John Doe", phone="250788382382")
        contact2 = self.create_contact("John Doe", phone="250788383383")

        contact1_urn = contact1.get_urn()
        contact1_urn.channel = self.tel_channel
        contact1_urn.save()

        contact2_urn = contact2.get_urn()
        contact2_urn.channel = channel2
        contact2_urn.save()

        # send a broadcast to urns that have different preferred channels
        self.create_outgoing_msg(contact1, "How is it going?", status=Msg.STATUS_QUEUED)
        self.create_outgoing_msg(contact2, "How is it going?", status=Msg.STATUS_QUEUED)

        # should contain messages for the the channel only
        response = self.sync(self.tel_channel, cmds=[])
        self.assertEqual(200, response.status_code)

        self.tel_channel.refresh_from_db()

        response = response.json()
        cmds = response["cmds"]
        self.assertEqual(1, len(cmds))
        self.assertEqual(len(cmds[0]["to"]), 1)
        self.assertEqual(cmds[0]["to"][0]["phone"], "+250788382382")

        # Should contain messages for the the channel only
        response = self.sync(channel2, cmds=[])
        self.assertEqual(200, response.status_code)

        channel2.refresh_from_db()

        response = response.json()
        cmds = response["cmds"]
        self.assertEqual(1, len(cmds))
        self.assertEqual(len(cmds[0]["to"]), 1)
        self.assertEqual(cmds[0]["to"][0]["phone"], "+250788383383")

    @mock_mailroom
    def test_sync(self, mr_mocks):
        date = timezone.now()
        date = int(time.mktime(date.timetuple())) * 1000

        Apk.objects.create(apk_type=Apk.TYPE_RELAYER, version="1.0.0")

        contact1 = self.create_contact("Ann", phone="+250788382382")
        contact2 = self.create_contact("Bob", phone="+250788383383")

        # create a payload from the client
        msg1 = self.create_outgoing_msg(
            contact1, "How is it going?", channel=self.tel_channel, status=Msg.STATUS_QUEUED
        )
        msg2 = self.create_outgoing_msg(
            contact2, "How is it going?", channel=self.tel_channel, status=Msg.STATUS_QUEUED
        )
        msg3 = self.create_outgoing_msg(
            contact2, "What is your name?", channel=self.tel_channel, status=Msg.STATUS_QUEUED
        )
        msg4 = self.create_outgoing_msg(
            contact2, "Do you have any children?", channel=self.tel_channel, status=Msg.STATUS_QUEUED
        )
        msg5 = self.create_outgoing_msg(
            contact2, "What's my dog's name?", channel=self.tel_channel, status=Msg.STATUS_QUEUED
        )
        msg6 = self.create_outgoing_msg(contact2, "from when?", channel=self.tel_channel, status=Msg.STATUS_QUEUED)

        # an incoming message that should not be included even if it is still pending
        incoming_message = self.create_incoming_msg(
            contact2, "hey", channel=self.tel_channel, status=Msg.STATUS_PENDING
        )

        # check our sync point has all three messages queued for delivery
        response = self.sync(self.tel_channel, cmds=[])
        self.assertEqual(200, response.status_code)

        # check last seen and fcm id were updated
        self.tel_channel.refresh_from_db()

        response = response.json()
        cmds = response["cmds"]
        self.assertEqual(5, len(cmds))

        # assert that our first command is the two message broadcast
        cmd = cmds[0]
        self.assertEqual("How is it going?", cmd["msg"])
        self.assertIn("+250788382382", [m["phone"] for m in cmd["to"]])
        self.assertIn("+250788383383", [m["phone"] for m in cmd["to"]])

        self.assertTrue(msg1.pk in [m["id"] for m in cmd["to"]])
        self.assertTrue(msg2.pk in [m["id"] for m in cmd["to"]])

        # add another message we'll pretend is in retry to see that we exclude them from sync
        msg6 = self.create_outgoing_msg(
            contact1,
            "Pretend this message is in retry on the client, don't send it on sync",
            channel=self.tel_channel,
            status=Msg.STATUS_QUEUED,
        )

        # a pending outgoing message should be included
        self.create_outgoing_msg(
            contact1, "Hello, we heard from you.", channel=self.tel_channel, status=Msg.STATUS_QUEUED
        )

        six_mins_ago = timezone.now() - timedelta(minutes=6)
        self.tel_channel.last_seen = six_mins_ago
        self.tel_channel.config["FCM_ID"] = "old_fcm_id"
        self.tel_channel.save(update_fields=["last_seen", "config"])

        cmds = [
            # device fcm data
            dict(cmd="fcm", fcm_id="12345", uuid="abcde"),
            # device details status
            dict(
                cmd="status",
                p_sts="DIS",
                p_src="BAT",
                p_lvl="60",
                net="UMTS",
                app_version="0.9.9",
                org_id=8,
                retry=[msg6.pk],
                pending=[],
            ),
            # pending incoming message that should be acknowledged but not updated
            dict(cmd="mt_sent", msg_id=incoming_message.pk, ts=date),
            # results for the outgoing messages
            dict(cmd="mt_sent", msg_id=msg1.pk, ts=date),
            dict(cmd="mt_sent", msg_id=msg2.pk, ts=date),
            dict(cmd="mt_dlvd", msg_id=msg3.pk, ts=date),
            dict(cmd="mt_error", msg_id=msg4.pk, ts=date),
            dict(cmd="mt_fail", msg_id=msg5.pk, ts=date),
            dict(cmd="mt_fail", msg_id=(msg6.pk - 4294967296), ts=date),  # simulate a negative integer from relayer
            # a missed call
            dict(cmd="call", phone="0788381212", type="mo_miss", ts=date),
            # repeated missed calls should be skipped
            dict(cmd="call", phone="0788381212", type="mo_miss", ts=date),
            dict(cmd="call", phone="0788381212", type="mo_miss", ts=date),
            # incoming
            dict(cmd="call", phone="0788381212", type="mt_call", dur=10, ts=date),
            # repeated calls should be skipped
            dict(cmd="call", phone="0788381212", type="mt_call", dur=10, ts=date),
            # incoming, invalid URN
            dict(cmd="call", phone="*", type="mt_call", dur=10, ts=date),
            # outgoing
            dict(cmd="call", phone="+250788383383", type="mo_call", dur=5, ts=date),
            # a new incoming message
            dict(cmd="mo_sms", phone="+250788383383", msg="This is giving me trouble", p_id="1", ts=date),
            # an incoming message from an empty contact
            dict(cmd="mo_sms", phone="", msg="This is spam", p_id="2", ts=date),
            # an incoming message from an invalid phone number
            dict(cmd="mo_sms", phone="!!@#$%", msg="sender ID invalid", p_id="4", ts=date),
        ]

        # now send the channel's updates
        response = self.sync(self.tel_channel, cmds=cmds)

        self.tel_channel.refresh_from_db()
        self.assertEqual(self.tel_channel.config["FCM_ID"], "12345")
        self.assertTrue(self.tel_channel.last_seen > six_mins_ago)

        # new batch, our ack and our claim command for new org
        self.assertEqual(6, len(response.json()["cmds"]))
        self.assertContains(response, "Hello, we heard from you.")
        self.assertContains(response, "mt_bcast")

        # check that our messages were updated accordingly
        self.assertEqual(2, Msg.objects.filter(channel=self.tel_channel, status="S", direction="O").count())
        self.assertEqual(1, Msg.objects.filter(channel=self.tel_channel, status="D", direction="O").count())
        self.assertEqual(1, Msg.objects.filter(channel=self.tel_channel, status="E", direction="O").count())
        self.assertEqual(2, Msg.objects.filter(channel=self.tel_channel, status="F", direction="O").count())

        # we should now have 4 incoming messages
        self.assertEqual(2, Msg.objects.filter(direction="I").count())
        # We should now have one sync
        self.assertEqual(1, SyncEvent.objects.filter(channel=self.tel_channel).count())

        # We should have 3 channel event
        self.assertEqual(3, ChannelEvent.objects.filter(channel=self.tel_channel).count())

        # We should have an incident for the app version
        self.assertEqual(
            1,
            Incident.objects.filter(
                incident_type=ChannelOutdatedAppIncidentType.slug, ended_on=None, channel=self.tel_channel
            ).count(),
        )

        # check our channel fcm and uuid were updated
        self.tel_channel = Channel.objects.get(pk=self.tel_channel.pk)
        self.assertEqual("12345", self.tel_channel.config["FCM_ID"])
        self.assertEqual("abcde", self.tel_channel.uuid)

        # should ignore incoming messages without text
        msgs_count = Msg.objects.all().count()
        response = self.sync(
            self.tel_channel,
            cmds=[
                # incoming msg without text
                dict(cmd="mo_sms", phone="+250788383383", p_id="1", ts=date)
            ],
        )

        # no new message
        self.assertEqual(Msg.objects.all().count(), msgs_count)

        response = self.sync(
            self.tel_channel,
            cmds=[
                # device details status
                dict(
                    cmd="status",
                    p_sts="DIS",
                    p_src="BAT",
                    p_lvl="15",
                    net="UMTS",
                    app_version="1.0.0",
                    pending=[],
                    retry=[],
                )
            ],
        )

        self.assertEqual(2, SyncEvent.objects.all().count())

        # We should have all incident for the app version ended
        self.assertEqual(
            1,
            Incident.objects.filter(
                incident_type=ChannelOutdatedAppIncidentType.slug, channel=self.tel_channel
            ).count(),
        )
        self.assertEqual(
            0,
            Incident.objects.filter(
                incident_type=ChannelOutdatedAppIncidentType.slug, ended_on=None, channel=self.tel_channel
            ).count(),
        )

        # make our events old so we can test trimming them
        SyncEvent.objects.all().update(created_on=timezone.now() - timedelta(days=45))
        trim_channel_sync_events()

        # should be cleared out
        self.assertEqual(1, SyncEvent.objects.all().count())

        response = self.sync(
            self.tel_channel,
            cmds=[
                # device fcm data
                dict(cmd="fcm", fcm_id="12345", uuid="abcde")
            ],
        )

        self.tel_channel.refresh_from_db()
        self.assertTrue(self.tel_channel.last_seen > six_mins_ago)
        self.assertEqual(self.tel_channel.config[Channel.CONFIG_FCM_ID], "12345")

    def test_signing(self):
        # good signature
        self.assertEqual(200, self.sync(self.tel_channel, cmds=[]).status_code)

        # bad signature, should result in 401 Unauthorized
        self.assertEqual(401, self.sync(self.tel_channel, signature="badsig", cmds=[]).status_code)

    @mock_mailroom
    def test_ignore_android_incoming_msg_invalid_phone(self, mr_mocks):
        date = timezone.now()
        date = int(time.mktime(date.timetuple())) * 1000

        response = self.sync(
            self.tel_channel, cmds=[dict(cmd="mo_sms", phone="_@", msg="First message", p_id="1", ts=date)]
        )
        self.assertEqual(200, response.status_code)

        responses = response.json()
        cmds = responses["cmds"]

        # check the server gave us responses for our message
        r0 = self.get_response(cmds, "1")

        self.assertIsNotNone(r0)
        self.assertEqual(r0["cmd"], "ack")

    def get_response(self, responses, p_id):
        for response in responses:
            if "p_id" in response and response["p_id"] == p_id:
                return response


class ChannelCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.ex_channel = Channel.create(
            self.org,
            self.admin,
            "RW",
            "EX",
            name="External Channel",
            address="+250785551313",
            role="SR",
            schemes=("tel",),
            config={"send_url": "http://send.com"},
        )
        self.other_org_channel = Channel.create(
            self.org2,
            self.admin2,
            "RW",
            "EX",
            name="Other Channel",
            address="+250785551414",
            role="SR",
            secret="45473",
            schemes=("tel",),
            config={"send_url": "http://send.com"},
        )

    def test_configuration(self):
        config_url = reverse("channels.channel_configuration", args=[self.ex_channel.uuid])

        # can't view configuration if not logged in
        response = self.client.get(config_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(config_url)
        self.assertContains(response, "To finish configuring your connection")
        self.assertEqual(f"/settings/channels/{self.ex_channel.uuid}", response.context[TEMBA_MENU_SELECTION])

        # can't view configuration of channel whose type doesn't support it
        response = self.client.get(reverse("channels.channel_configuration", args=[self.channel.uuid]))
        self.assertRedirect(response, reverse("channels.channel_read", args=[self.channel.uuid]))

        # can't view configuration of channel in other org
        response = self.client.get(reverse("channels.channel_configuration", args=[self.other_org_channel.uuid]))
        self.assertLoginRedirect(response)

    def test_update(self):
        android_channel = self.create_channel(
            "A", "My Android", "+250785551212", country="RW", secret="sesame", config={"FCM_ID": "123"}
        )
        vonage_channel = self.create_channel("NX", "My Vonage", "+1234567890", country="US", config={}, role="CASR")
        telegram_channel = self.create_channel("TG", "My Telegram", "75474745", config={})

        android_url = reverse("channels.channel_update", args=[android_channel.id])
        vonage_url = reverse("channels.channel_update", args=[vonage_channel.id])
        telegram_url = reverse("channels.channel_update", args=[telegram_channel.id])

        self.assertRequestDisallowed(android_url, [None, self.user, self.agent, self.admin2])

        # fields shown depend on scheme and role
        self.assertUpdateFetch(
            android_url, [self.editor, self.admin], form_fields={"name": "My Android", "allow_international": False}
        )
        self.assertUpdateFetch(
            vonage_url,
            [self.editor, self.admin],
            form_fields={"name": "My Vonage", "allow_international": False, "machine_detection": False},
        )
        self.assertUpdateFetch(telegram_url, [self.editor, self.admin], form_fields={"name": "My Telegram"})

        # name can't be empty
        self.assertUpdateSubmit(
            android_url,
            self.admin,
            {"name": ""},
            form_errors={"name": "This field is required."},
            object_unchanged=android_channel,
        )

        # make some changes
        self.assertUpdateSubmit(
            vonage_url,
            self.admin,
            {"name": "Updated Name", "allow_international": True, "machine_detection": True},
        )

        vonage_channel.refresh_from_db()
        self.assertEqual("Updated Name", vonage_channel.name)
        self.assertEqual("+1234567890", vonage_channel.address)
        self.assertTrue(vonage_channel.config.get("allow_international"))
        self.assertTrue(vonage_channel.config.get("machine_detection"))

        self.assertUpdateFetch(
            vonage_url,
            [self.editor, self.admin],
            form_fields={"name": "Updated Name", "allow_international": True, "machine_detection": True},
        )

        # staff users see extra log policy field
        self.assertUpdateFetch(
            vonage_url,
            [self.customer_support],
            form_fields=["name", "log_policy", "allow_international", "machine_detection"],
            choose_org=self.org,
        )

    def test_delete(self):
        delete_url = reverse("channels.channel_delete", args=[self.ex_channel.uuid])

        self.assertRequestDisallowed(delete_url, [None, self.user, self.agent, self.admin2])

        response = self.assertDeleteFetch(delete_url, [self.editor, self.admin])
        self.assertContains(response, "You are about to delete")

        # submit to delete it
        response = self.assertDeleteSubmit(
            delete_url, self.admin, object_deactivated=self.ex_channel, success_status=200
        )
        self.assertEqual("/org/workspace/", response["Temba-Success"])

        # reactivate
        self.ex_channel.is_active = True
        self.ex_channel.save()

        # add a dependency and try again
        flow = self.create_flow("Color Flow")
        flow.channel_dependencies.add(self.ex_channel)
        self.assertFalse(flow.has_issues)

        response = self.assertDeleteFetch(delete_url, [self.admin])
        self.assertContains(response, "is used by the following items but can still be deleted:")
        self.assertContains(response, "Color Flow")

        self.assertDeleteSubmit(delete_url, self.admin, object_deactivated=self.ex_channel, success_status=200)

        flow.refresh_from_db()
        self.assertTrue(flow.has_issues)
        self.assertNotIn(self.ex_channel, flow.channel_dependencies.all())


class SyncEventTest(SmartminTest):
    def setUp(self):
        self.user = self.create_user("tito")
        self.org = Org.objects.create(
            name="Temba", timezone="Africa/Kigali", created_by=self.user, modified_by=self.user
        )
        self.tel_channel = Channel.create(
            self.org,
            self.user,
            "RW",
            "A",
            "Test Channel",
            "0785551212",
            secret="12345",
            config={Channel.CONFIG_FCM_ID: "123"},
        )

    @mock_mailroom
    def test_sync_event_model(self, mr_mocks):
        self.sync_event = SyncEvent.create(
            self.tel_channel,
            dict(p_src="AC", p_sts="DIS", p_lvl=80, net="WIFI", pending=[1, 2], retry=[3, 4], cc="RW"),
            [1, 2],
        )
        self.assertEqual(SyncEvent.objects.all().count(), 1)
        self.assertEqual(self.sync_event.get_pending_messages(), [1, 2])
        self.assertEqual(self.sync_event.get_retry_messages(), [3, 4])
        self.assertEqual(self.sync_event.incoming_command_count, 0)

        self.sync_event = SyncEvent.create(
            self.tel_channel,
            dict(p_src="AC", p_sts="DIS", p_lvl=80, net="WIFI", pending=[1, 2], retry=[3, 4], cc="US"),
            [1],
        )
        self.assertEqual(self.sync_event.incoming_command_count, 0)
        self.tel_channel = Channel.objects.get(pk=self.tel_channel.pk)

        # we shouldn't update country once the relayer is claimed
        self.assertEqual("RW", self.tel_channel.country)


class ChannelIncidentsTest(TembaTest):
    def test_disconnected(self):
        # set our last seen to a while ago
        self.channel.last_seen = timezone.now() - timedelta(minutes=40)
        self.channel.save(update_fields=("last_seen",))

        with override_brand(emails={"notifications": "support@mybrand.com"}):
            check_android_channels()

            # should have created an incident
            incident = self.org.incidents.get()
            self.assertEqual(self.channel, incident.channel)
            self.assertEqual("channel:disconnected", incident.incident_type)
            self.assertIsNone(incident.ended_on)

            self.assertEqual(1, self.admin.notifications.count())

            notification = self.admin.notifications.get()
            self.assertFalse(notification.is_seen)

            send_notification_emails()

            self.assertEqual(1, len(mail.outbox))
            self.assertEqual("[Nyaruka] Incident: Channel Disconnected", mail.outbox[0].subject)
            self.assertEqual("support@mybrand.com", mail.outbox[0].from_email)

        # if we go to the read page of the channel, notification will be marked as seen
        read_url = reverse("channels.channel_read", args=[self.channel.uuid])
        self.login(self.admin)
        self.client.get(read_url)

        notification.refresh_from_db()
        self.assertTrue(notification.is_seen)

        # call task again
        check_android_channels()

        # still only one incident
        incident = self.org.incidents.get()
        self.assertEqual(1, len(mail.outbox))

        # ok, let's have the channel show up again
        self.channel.last_seen = timezone.now() + timedelta(minutes=5)
        self.channel.save(update_fields=("last_seen",))

        check_android_channels()

        # still only one incident, but it is now ended
        incident = self.org.incidents.get()
        self.assertIsNotNone(incident.ended_on)


class ChannelCountTest(TembaTest):
    def assertDailyCount(self, channel, assert_count, count_type, day):
        calculated_count = ChannelCount.get_day_count(channel, count_type, day)
        self.assertEqual(assert_count, calculated_count)

    def test_msg_counts(self):
        contact = self.create_contact("Joe", phone="+250788111222")

        self.assertEqual(0, ChannelCount.objects.count())

        # message without a channel won't be recorded
        self.create_outgoing_msg(contact, "X", failed_reason=Msg.FAILED_NO_DESTINATION)
        self.assertEqual(0, ChannelCount.objects.count())

        # create some messages...
        self.create_incoming_msg(contact, "A", created_on=datetime(2023, 5, 31, 13, 0, 30, 0, tzone.utc))
        self.create_incoming_msg(contact, "B", created_on=datetime(2023, 6, 1, 13, 0, 30, 0, tzone.utc))
        self.create_incoming_msg(contact, "C", created_on=datetime(2023, 6, 1, 13, 0, 30, 0, tzone.utc))
        self.create_incoming_msg(contact, "D", created_on=datetime(2023, 6, 1, 13, 0, 30, 0, tzone.utc), voice=True)
        self.create_outgoing_msg(contact, "E", created_on=datetime(2023, 6, 1, 13, 0, 30, 0, tzone.utc))

        # and 3 in bulk
        Msg.objects.bulk_create(
            [
                Msg(
                    org=self.org,
                    channel=self.channel,
                    contact=contact,
                    text="F",
                    direction="O",
                    msg_type="T",
                    created_on=datetime(2023, 6, 1, 13, 0, 30, 0, tzone.utc),
                    modified_on=datetime(2023, 6, 1, 13, 0, 30, 0, tzone.utc),
                ),
                Msg(
                    org=self.org,
                    channel=self.channel,
                    contact=contact,
                    text="G",
                    direction="O",
                    msg_type="T",
                    created_on=datetime(2023, 6, 1, 13, 0, 30, 0, tzone.utc),
                    modified_on=datetime(2023, 6, 1, 13, 0, 30, 0, tzone.utc),
                ),
                Msg(
                    org=self.org,
                    channel=self.channel,
                    contact=contact,
                    text="H",
                    direction="O",
                    msg_type="V",
                    created_on=datetime(2023, 6, 1, 13, 0, 30, 0, tzone.utc),
                    modified_on=datetime(2023, 6, 1, 13, 0, 30, 0, tzone.utc),
                ),
            ]
        )

        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_MSG_TYPE, date(2023, 5, 31))
        self.assertDailyCount(self.channel, 0, ChannelCount.INCOMING_IVR_TYPE, date(2023, 5, 31))
        self.assertDailyCount(self.channel, 2, ChannelCount.INCOMING_MSG_TYPE, date(2023, 6, 1))
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_IVR_TYPE, date(2023, 6, 1))
        self.assertDailyCount(self.channel, 3, ChannelCount.OUTGOING_MSG_TYPE, date(2023, 6, 1))
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_IVR_TYPE, date(2023, 6, 1))

        # squash our counts
        squash_channel_counts()

        self.assertEqual(ChannelCount.objects.all().count(), 5)

        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_MSG_TYPE, date(2023, 5, 31))
        self.assertDailyCount(self.channel, 0, ChannelCount.INCOMING_IVR_TYPE, date(2023, 5, 31))
        self.assertDailyCount(self.channel, 2, ChannelCount.INCOMING_MSG_TYPE, date(2023, 6, 1))
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_IVR_TYPE, date(2023, 6, 1))
        self.assertDailyCount(self.channel, 3, ChannelCount.OUTGOING_MSG_TYPE, date(2023, 6, 1))
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_IVR_TYPE, date(2023, 6, 1))

        # soft deleting a message doesn't decrement the count
        Msg.bulk_soft_delete([Msg.objects.get(text="A")])
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_MSG_TYPE, date(2023, 5, 31))

        # nor hard deleting
        Msg.bulk_delete([Msg.objects.get(text="B")])
        self.assertDailyCount(self.channel, 2, ChannelCount.INCOMING_MSG_TYPE, date(2023, 6, 1))

    def test_log_counts(self):
        contact = self.create_contact("Joe", phone="+250788111222")

        self.assertEqual(0, ChannelCount.objects.count())

        # ok, test outgoing now
        log = ChannelLog.objects.create(channel=self.channel, log_type=ChannelLog.LOG_TYPE_MSG_SEND, is_error=True)
        msg3 = self.create_outgoing_msg(contact, "Real Message", channel=self.channel, logs=[log])

        # squash our counts
        squash_channel_counts()

        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_MSG_TYPE, msg3.created_on.date())
        self.assertEqual(ChannelCount.objects.filter(count_type=ChannelCount.SUCCESS_LOG_TYPE).count(), 0)
        self.assertEqual(ChannelCount.objects.filter(count_type=ChannelCount.ERROR_LOG_TYPE).count(), 1)

        # delete our log, should decrement our count
        log.delete()
        self.assertEqual(0, self.channel.get_count([ChannelCount.ERROR_LOG_TYPE]))

        # deleting a message doesn't decrement the count
        msg3.delete()
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_MSG_TYPE, msg3.created_on.date())

        ChannelCount.objects.all().delete()

        # incoming IVR
        msg4 = self.create_incoming_msg(contact, "Test Message", voice=True)
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_IVR_TYPE, msg4.created_on.date())
        msg4.delete()
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_IVR_TYPE, msg4.created_on.date())

        ChannelCount.objects.all().delete()

        # outgoing ivr
        msg5 = self.create_outgoing_msg(contact, "Real Voice", voice=True)
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_IVR_TYPE, msg5.created_on.date())
        msg5.delete()
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_IVR_TYPE, msg5.created_on.date())

        with patch("temba.channels.tasks.track") as mock:
            self.create_incoming_msg(contact, "Test Message")

            with self.assertNumQueries(6):
                track_org_channel_counts(now=timezone.now() + timedelta(days=1))
                self.assertEqual(2, mock.call_count)
                mock.assert_called_with(self.admin, "temba.ivr_outgoing", {"count": 1})


class ChannelEventTest(TembaTest):
    def test_trim_task(self):
        contact = self.create_contact("Joe", phone="+250788111222")
        ChannelEvent.objects.create(
            org=self.org,
            channel=self.channel,
            event_type=ChannelEvent.TYPE_STOP_CONTACT,
            contact=contact,
            created_on=timezone.now() - timedelta(days=91),
            occurred_on=timezone.now() - timedelta(days=91),
        )
        e2 = ChannelEvent.objects.create(
            org=self.org,
            channel=self.channel,
            event_type=ChannelEvent.TYPE_NEW_CONVERSATION,
            contact=contact,
            created_on=timezone.now() - timedelta(days=85),
            occurred_on=timezone.now() - timedelta(days=85),
        )

        results = trim_channel_events()
        self.assertEqual({"deleted": 1}, results)

        # should only have one event remaining and should be e2
        self.assertEqual(1, ChannelEvent.objects.all().count())
        self.assertTrue(ChannelEvent.objects.filter(id=e2.id))


class ChannelLogTest(TembaTest):
    def test_get_by_uuid(self):
        log1 = self.create_channel_log(
            ChannelLog.LOG_TYPE_MSG_SEND,
            http_logs=[{"url": "https://foo.bar/send1"}],
            errors=[{"code": "bad_response", "message": "response not right"}],
        )
        log2 = self.create_channel_log(
            ChannelLog.LOG_TYPE_MSG_STATUS,
            http_logs=[{"url": "https://foo.bar/send2"}],
            errors=[],
        )

        self.assertEqual([], ChannelLog.get_by_uuid(self.channel, []))

        logs = ChannelLog.get_by_uuid(self.channel, [log1.uuid, log2.uuid])
        self.assertEqual(2, len(logs))
        self.assertEqual(log1.uuid, logs[0].uuid)
        self.assertEqual(self.channel, logs[0].channel)
        self.assertEqual(ChannelLog.LOG_TYPE_MSG_SEND, logs[0].log_type)
        self.assertEqual(log2.uuid, logs[1].uuid)
        self.assertEqual(self.channel, logs[1].channel)
        self.assertEqual(ChannelLog.LOG_TYPE_MSG_STATUS, logs[1].log_type)

    def test_get_display(self):
        channel = self.create_channel("TG", "Telegram", "mybot")
        contact = self.create_contact("Fred Jones", urns=["telegram:74747474"])
        log = ChannelLog.objects.create(
            channel=channel,
            log_type=ChannelLog.LOG_TYPE_MSG_SEND,
            is_error=True,
            http_logs=[
                {
                    "url": "https://telegram.com/send?to=74747474",
                    "status_code": 400,
                    "request": 'POST https://telegram.com/send?to=74747474 HTTP/1.1\r\n\r\n{"to":"74747474"}',
                    "response": 'HTTP/2.0 200 OK\r\n\r\n{"to":"74747474","first_name":"Fred"}',
                    "elapsed_ms": 263,
                    "retries": 0,
                    "created_on": "2022-08-17T14:07:30Z",
                }
            ],
            errors=[{"code": "bad_response", "ext_code": "", "message": "response not right"}],
        )
        msg_out = self.create_outgoing_msg(contact, "Working", channel=channel, status="S", logs=[log])

        self.assertEqual(
            {
                "uuid": str(log.uuid),
                "type": "msg_send",
                "http_logs": [
                    {
                        "url": "https://telegram.com/send?to=74747474",
                        "status_code": 400,
                        "request": 'POST https://telegram.com/send?to=74747474 HTTP/1.1\r\n\r\n{"to":"74747474"}',
                        "response": 'HTTP/2.0 200 OK\r\n\r\n{"to":"74747474","first_name":"Fred"}',
                        "elapsed_ms": 263,
                        "retries": 0,
                        "created_on": "2022-08-17T14:07:30Z",
                    }
                ],
                "errors": [{"code": "bad_response", "ext_code": "", "message": "response not right", "ref_url": None}],
                "elapsed_ms": 0,
                "created_on": matchers.ISODate(),
            },
            log.get_display(anonymize=False, urn=msg_out.contact_urn),
        )

        self.assertEqual(
            {
                "uuid": str(log.uuid),
                "type": "msg_send",
                "http_logs": [
                    {
                        "url": "https://telegram.com/send?to=********",
                        "status_code": 400,
                        "request": 'POST https://telegram.com/send?to=******** HTTP/1.1\r\n\r\n{"to":"********"}',
                        "response": 'HTTP/2.0 200 OK\r\n\r\n{"to": "********", "first_name": "********"}',
                        "elapsed_ms": 263,
                        "retries": 0,
                        "created_on": "2022-08-17T14:07:30Z",
                    }
                ],
                "errors": [{"code": "bad_response", "ext_code": "", "message": "response n********", "ref_url": None}],
                "elapsed_ms": 0,
                "created_on": matchers.ISODate(),
            },
            log.get_display(anonymize=True, urn=msg_out.contact_urn),
        )

        # if we don't pass it a URN, anonymization is more aggressive
        self.assertEqual(
            {
                "uuid": str(log.uuid),
                "type": "msg_send",
                "http_logs": [
                    {
                        "url": "https://te********",
                        "status_code": 400,
                        "request": "POST https********",
                        "response": "HTTP/2.0 2********",
                        "elapsed_ms": 263,
                        "retries": 0,
                        "created_on": "2022-08-17T14:07:30Z",
                    }
                ],
                "errors": [{"code": "bad_response", "ext_code": "", "message": "response n********", "ref_url": None}],
                "elapsed_ms": 0,
                "created_on": matchers.ISODate(),
            },
            log.get_display(anonymize=True, urn=None),
        )

    def test_get_display_timed_out(self):
        channel = self.create_channel("TG", "Telegram", "mybot")
        contact = self.create_contact("Fred Jones", urns=["telegram:74747474"])
        log = ChannelLog.objects.create(
            channel=channel,
            log_type=ChannelLog.LOG_TYPE_MSG_SEND,
            is_error=True,
            http_logs=[
                {
                    "url": "https://telegram.com/send?to=74747474",
                    "request": 'POST https://telegram.com/send?to=74747474 HTTP/1.1\r\n\r\n{"to":"74747474"}',
                    "elapsed_ms": 30001,
                    "retries": 0,
                    "created_on": "2022-08-17T14:07:30Z",
                }
            ],
            errors=[{"code": "bad_response", "ext_code": "", "message": "response not right"}],
        )
        msg_out = self.create_outgoing_msg(contact, "Working", channel=channel, status="S", logs=[log])

        self.assertEqual(
            {
                "uuid": str(log.uuid),
                "type": "msg_send",
                "http_logs": [
                    {
                        "url": "https://telegram.com/send?to=74747474",
                        "request": 'POST https://telegram.com/send?to=74747474 HTTP/1.1\r\n\r\n{"to":"74747474"}',
                        "elapsed_ms": 30001,
                        "retries": 0,
                        "created_on": "2022-08-17T14:07:30Z",
                    }
                ],
                "errors": [{"code": "bad_response", "ext_code": "", "message": "response not right", "ref_url": None}],
                "elapsed_ms": 0,
                "created_on": matchers.ISODate(),
            },
            log.get_display(anonymize=False, urn=msg_out.contact_urn),
        )
        self.assertEqual(
            {
                "uuid": str(log.uuid),
                "type": "msg_send",
                "http_logs": [
                    {
                        "url": "https://telegram.com/send?to=********",
                        "request": 'POST https://telegram.com/send?to=******** HTTP/1.1\r\n\r\n{"to":"********"}',
                        "response": "",
                        "elapsed_ms": 30001,
                        "retries": 0,
                        "created_on": "2022-08-17T14:07:30Z",
                    }
                ],
                "errors": [{"code": "bad_response", "ext_code": "", "message": "response n********", "ref_url": None}],
                "elapsed_ms": 0,
                "created_on": matchers.ISODate(),
            },
            log.get_display(anonymize=True, urn=msg_out.contact_urn),
        )

    def test_trim_task(self):
        ChannelLog.objects.create(
            channel=self.channel,
            log_type=ChannelLog.LOG_TYPE_MSG_SEND,
            is_error=False,
            http_logs=[],
            errors=[],
            created_on=timezone.now() - timedelta(days=15),
        )
        l2 = ChannelLog.objects.create(
            channel=self.channel,
            log_type=ChannelLog.LOG_TYPE_MSG_SEND,
            is_error=False,
            http_logs=[],
            errors=[],
            created_on=timezone.now() - timedelta(days=2),
        )

        results = trim_channel_logs()
        self.assertEqual({"deleted": 1}, results)

        # should only have one log remaining and should be l2
        self.assertEqual(1, ChannelLog.objects.all().count())
        self.assertTrue(ChannelLog.objects.filter(id=l2.id))


class ChannelLogCRUDLTest(CRUDLTestMixin, TembaTest):
    def test_msg(self):
        contact = self.create_contact("Fred", phone="+12067799191")

        log1 = self.create_channel_log(
            ChannelLog.LOG_TYPE_MSG_SEND,
            http_logs=[
                {
                    "url": "https://foo.bar/send1",
                    "status_code": 200,
                    "request": "POST https://foo.bar/send1\r\n\r\n{}",
                    "response": "HTTP/1.0 200 OK\r\r\r\n",
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2024-09-16T00:00:00Z",
                }
            ],
        )
        log2 = self.create_channel_log(
            ChannelLog.LOG_TYPE_MSG_SEND,
            http_logs=[
                {
                    "url": "https://foo.bar/send2",
                    "status_code": 200,
                    "request": "POST https://foo.bar/send2\r\n\r\n{}",
                    "response": "HTTP/1.0 200 OK\r\r\r\n",
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2024-09-16T00:00:00Z",
                }
            ],
        )
        msg1 = self.create_outgoing_msg(contact, "success message", status="D", logs=[log1, log2])

        # create another msg and log that shouldn't be included
        log3 = self.create_channel_log(
            ChannelLog.LOG_TYPE_MSG_SEND,
            http_logs=[
                {
                    "url": "https://foo.bar/send3",
                    "status_code": 200,
                    "request": "POST https://foo.bar/send3\r\n\r\n{}",
                    "response": "HTTP/1.0 200 OK\r\r\r\n",
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2024-09-16T00:00:00Z",
                }
            ],
        )
        self.create_outgoing_msg(contact, "success message", status="D", logs=[log3])

        msg1_url = reverse("channels.channellog_msg", args=[self.channel.uuid, msg1.id])

        self.assertRequestDisallowed(msg1_url, [None, self.user, self.editor, self.agent, self.admin2])
        response = self.assertListFetch(msg1_url, [self.admin], context_objects=[])
        self.assertEqual(2, len(response.context["logs"]))
        self.assertEqual("https://foo.bar/send1", response.context["logs"][0]["http_logs"][0]["url"])
        self.assertEqual("https://foo.bar/send2", response.context["logs"][1]["http_logs"][0]["url"])

        response = self.client.get(msg1_url)
        self.assertEqual(f"/settings/channels/{self.channel.uuid}", response.headers[TEMBA_MENU_SELECTION])

    def test_call(self):
        contact = self.create_contact("Fred", phone="+12067799191")
        flow = self.create_flow("IVR")

        log1 = self.create_channel_log(
            ChannelLog.LOG_TYPE_MSG_SEND,
            http_logs=[
                {
                    "url": "https://foo.bar/call1",
                    "status_code": 200,
                    "request": "POST https://foo.bar/send1\r\n\r\n{}",
                    "response": "HTTP/1.0 200 OK\r\r\r\n",
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2024-09-16T00:00:00Z",
                }
            ],
        )
        log2 = self.create_channel_log(
            ChannelLog.LOG_TYPE_IVR_START,
            http_logs=[
                {
                    "url": "https://foo.bar/call2",
                    "status_code": 200,
                    "request": "POST /send2\r\n\r\n{}",
                    "response": "HTTP/1.0 200 OK\r\r\r\n",
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
        )
        call1 = self.create_incoming_call(flow, contact, logs=[log1, log2])

        # create another call and log that shouldn't be included
        log3 = self.create_channel_log(
            ChannelLog.LOG_TYPE_IVR_START,
            http_logs=[
                {
                    "url": "https://foo.bar/call2",
                    "status_code": 200,
                    "request": "POST /send2\r\n\r\n{}",
                    "response": "HTTP/1.0 200 OK\r\r\r\n",
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
        )
        self.create_incoming_call(flow, contact, logs=[log3])

        call1_url = reverse("channels.channellog_call", args=[self.channel.uuid, call1.id])

        self.assertRequestDisallowed(call1_url, [None, self.user, self.editor, self.agent, self.admin2])
        response = self.assertListFetch(call1_url, [self.admin], context_objects=[])
        self.assertEqual(2, len(response.context["logs"]))
        self.assertEqual("https://foo.bar/call1", response.context["logs"][0]["http_logs"][0]["url"])
        self.assertEqual("https://foo.bar/call2", response.context["logs"][1]["http_logs"][0]["url"])

    def test_read_and_list(self):
        self.channel.role = "CASR"
        self.channel.save(update_fields=("role",))

        contact = self.create_contact("Fred Jones", phone="+12067799191")

        # create unrelated incoming message
        self.create_incoming_msg(contact, "incoming msg")

        # create sent outgoing message with success channel log
        success_log = ChannelLog.objects.create(
            channel=self.channel,
            log_type=ChannelLog.LOG_TYPE_MSG_SEND,
            is_error=False,
            http_logs=[
                {
                    "url": "https://foo.bar/send?msg=message",
                    "status_code": 200,
                    "request": "POST /send?msg=message\r\n\r\n{}",
                    "response": 'HTTP/1.0 200 OK\r\r\r\n{"ok":true}',
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
        )
        self.create_outgoing_msg(contact, "success message", status="D", logs=[success_log])

        # create failed outgoing message with error channel log
        failed_log = ChannelLog.objects.create(
            channel=self.channel,
            log_type=ChannelLog.LOG_TYPE_MSG_SEND,
            is_error=True,
            http_logs=[
                {
                    "url": "https://foo.bar/send?msg=failed+message",
                    "status_code": 400,
                    "request": "POST /send?msg=failed+message\r\n\r\n{}",
                    "response": "HTTP/1.0 200 OK\r\r\r\n",
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
            errors=[{"message": "invalid credentials", "code": ""}],
        )
        self.create_outgoing_msg(contact, "failed message", logs=[failed_log])

        # create a non-message, non-call other log
        other_log = ChannelLog.objects.create(
            channel=self.channel,
            log_type=ChannelLog.LOG_TYPE_PAGE_SUBSCRIBE,
            is_error=False,
            http_logs=[
                {
                    "url": "https://foo.bar/page",
                    "status_code": 400,
                    "request": "POST /send?msg=failed+message\r\n\r\n{}",
                    "response": "HTTP/1.0 200 OK\r\r\r\n",
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
        )

        list_url = reverse("channels.channellog_list", args=[self.channel.uuid])

        self.assertRequestDisallowed(list_url, [None, self.user, self.editor, self.agent, self.admin2])
        response = self.assertListFetch(list_url, [self.admin], context_objects=[other_log, failed_log, success_log])
        self.assertEqual(f"/settings/channels/{self.channel.uuid}", response.headers[TEMBA_MENU_SELECTION])

        # try viewing the failed message log
        read_url = reverse("channels.channellog_read", args=[failed_log.id])

        self.assertRequestDisallowed(read_url, [None, self.user, self.agent, self.editor, self.admin2])
        self.assertReadFetch(read_url, [self.admin], context_object=failed_log)

        # invalid channel UUID returns 404
        response = self.client.get(reverse("channels.channellog_list", args=["invalid-uuid"]))
        self.assertEqual(404, response.status_code)

    def assertRedacted(self, response, values: tuple):
        for value in values:
            self.assertNotContains(response, value)

        self.assertContains(response, ChannelLog.REDACT_MASK)

    def assertNotRedacted(self, response, values: tuple):
        for value in values:
            self.assertContains(response, value)

    def test_redaction_for_telegram(self):
        urn = "telegram:3527065"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("TG", "Test TG Channel", "234567")
        log = self.create_channel_log(
            ChannelLog.LOG_TYPE_MSG_SEND,
            http_logs=[
                {
                    "url": "https://api.telegram.org/65474/sendMessage",
                    "status_code": 200,
                    "request": "POST /65474/sendMessage HTTP/1.1\r\nHost: api.telegram.org\r\nUser-Agent: Courier/1.2.159\r\nContent-Length: 231\r\nContent-Type: application/x-www-form-urlencoded\r\nAccept-Encoding: gzip\r\n\r\nchat_id=3527065&reply_markup=%7B%22resize_keyboard%22%3Atrue%2C%22one_time_keyboard%22%3Atrue%2C%22keyboard%22%3A%5B%5B%7B%22text%22%3A%22blackjack%22%7D%2C%7B%22text%22%3A%22balance%22%7D%5D%5D%7D&text=Your+balance+is+now+%246.00.",
                    "response": 'HTTP/1.1 200 OK\r\nContent-Length: 298\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Expose-Headers: Content-Length,Content-Type,Date,Server,Connection\r\nConnection: keep-alive\r\nContent-Type: application/json\r\nDate: Tue, 11 Jun 2019 15:33:06 GMT\r\nServer: nginx/1.12.2\r\nStrict-Transport-Security: max-age=31536000; includeSubDomains; preload\r\n\r\n{"ok":true,"result":{"message_id":1440,"from":{"id":678777066,"is_bot":true,"first_name":"textit_staging","username":"textit_staging_bot"},"chat":{"id":3527065,"first_name":"Nic","last_name":"Pottier","username":"Nicpottier","type":"private"},"date":1560267186,"text":"Your balance is now $6.00."}}',
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
        )
        msg = self.create_incoming_msg(contact, "incoming msg", channel=channel, logs=[log])

        self.login(self.admin)

        read_url = reverse("channels.channellog_msg", args=[channel.uuid, msg.id])

        # check read page shows un-redacted content for a regular org
        response = self.client.get(read_url)
        self.assertEqual(1, len(response.context["logs"]))
        self.assertNotRedacted(response, ("3527065", "Nic", "Pottier"))

        # but for anon org we see redaction...
        with self.anonymous(self.org):
            response = self.client.get(read_url)
            self.assertRedacted(response, ("3527065", "Nic", "Pottier"))

            # even as customer support
            self.login(self.customer_support, choose_org=self.org)

            response = self.client.get(read_url)
            self.assertRedacted(response, ("3527065", "Nic", "Pottier"))

            # unless we explicitly break out of it
            response = self.client.get(read_url + "?break=1")
            self.assertNotRedacted(response, ("3527065", "Nic", "Pottier"))

    def test_redaction_for_telegram_with_invalid_json(self):
        urn = "telegram:3527065"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("TG", "Test TG Channel", "234567")
        log = self.create_channel_log(
            ChannelLog.LOG_TYPE_MSG_SEND,
            http_logs=[
                {
                    "url": "https://api.telegram.org/65474/sendMessage",
                    "status_code": 200,
                    "request": "POST /65474/sendMessage HTTP/1.1\r\nHost: api.telegram.org\r\nUser-Agent: Courier/1.2.159\r\nContent-Length: 231\r\nContent-Type: application/x-www-form-urlencoded\r\nAccept-Encoding: gzip\r\n\r\nchat_id=3527065&reply_markup=%7B%22resize_keyboard%22%3Atrue%2C%22one_time_keyboard%22%3Atrue%2C%22keyboard%22%3A%5B%5B%7B%22text%22%3A%22blackjack%22%7D%2C%7B%22text%22%3A%22balance%22%7D%5D%5D%7D&text=Your+balance+is+now+%246.00.",
                    "response": 'HTTP/1.1 200 OK\r\nContent-Length: 298\r\nContent-Type: application/json\r\n\r\n{"bad_json":true, "first_name": "Nic"',
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
        )
        msg = self.create_incoming_msg(contact, "incoming msg", channel=channel, logs=[log])

        self.login(self.admin)

        read_url = reverse("channels.channellog_msg", args=[channel.uuid, msg.id])

        # check read page shows un-redacted content for a regular org
        response = self.client.get(read_url)
        self.assertNotRedacted(response, ("3527065", "Nic"))

        # but for anon org we see redaction...
        with self.anonymous(self.org):
            response = self.client.get(read_url)
            self.assertRedacted(response, ("3527065", "Nic"))

    def test_redaction_for_telegram_when_no_match(self):
        urn = "telegram:3527065"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("TG", "Test TG Channel", "234567")
        log = self.create_channel_log(
            ChannelLog.LOG_TYPE_MSG_SEND,
            http_logs=[
                {
                    "url": "https://api.telegram.org/There is no contact identifying information",
                    "status_code": 200,
                    "request": 'POST /65474/sendMessage HTTP/1.1\r\nHost: api.telegram.org\r\nUser-Agent: Courier/1.2.159\r\nContent-Length: 231\r\nContent-Type: application/x-www-form-urlencoded\r\nAccept-Encoding: gzip\r\n\r\n{"json": "There is no contact identifying information"}',
                    "response": 'HTTP/1.1 200 OK\r\nContent-Length: 298\r\nContent-Type: application/json\r\n\r\n{"json": "There is no contact identifying information"}',
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
        )
        msg = self.create_incoming_msg(contact, "incoming msg", channel=channel, logs=[log])

        self.login(self.admin)

        read_url = reverse("channels.channellog_msg", args=[channel.uuid, msg.id])

        # check read page shows un-redacted content for a regular org
        response = self.client.get(read_url)
        self.assertNotRedacted(response, ("3527065",))

        # but for anon org we see complete redaction
        with self.anonymous(self.org):
            response = self.client.get(read_url)
            self.assertRedacted(response, ("3527065", "api.telegram.org", "/65474/sendMessage"))

    def test_redaction_for_twitter(self):
        urn = "twitterid:767659860"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("TWT", "Test TWT Channel", "nyaruka")
        log = self.create_channel_log(
            ChannelLog.LOG_TYPE_MSG_SEND,
            http_logs=[
                {
                    "url": "https://textit.in/c/twt/5c70a767-f3dc-4a99-9323-4774f6432af5/receive",
                    "status_code": 200,
                    "request": 'POST /c/twt/5c70a767-f3dc-4a99-9323-4774f6432af5/receive HTTP/1.1\r\nHost: textit.in\r\nContent-Length: 1596\r\nContent-Type: application/json\r\nFinagle-Ctx-Com.twitter.finagle.deadline: 1560853608671000000 1560853611615000000\r\nFinagle-Ctx-Com.twitter.finagle.retries: 0\r\nFinagle-Http-Retryable-Request: \r\nX-Amzn-Trace-Id: Root=1-5d08bc68-de52174e83904d614a32a5c6\r\nX-B3-Flags: 2\r\nX-B3-Parentspanid: fe22fff79af84311\r\nX-B3-Sampled: false\r\nX-B3-Spanid: 86f3c3871ae31c2d\r\nX-B3-Traceid: fe22fff79af84311\r\nX-Forwarded-For: 199.16.157.173\r\nX-Forwarded-Port: 443\r\nX-Forwarded-Proto: https\r\nX-Twitter-Webhooks-Signature: sha256=CYVI5q7e7bzKufCD3GnZoJheSmjVRmNQo9uzO/gi4tA=\r\n\r\n{"for_user_id":"3753944237","direct_message_events":[{"type":"message_create","id":"1140928844112814089","created_timestamp":"1560853608526","message_create":{"target":{"recipient_id":"3753944237"},"sender_id":"767659860","message_data":{"text":"Briefly what will you be talking about and do you have any feature stories","entities":{"hashtags":[],"symbols":[],"user_mentions":[],"urls":[]}}}}],"users":{"767659860":{"id":"767659860","created_timestamp":"1345386861000","name":"Aaron Tumukunde","screen_name":"tumaaron","description":"Mathematics \u25a1 Media \u25a1 Real Estate \u25a1 And Jesus above all.","protected":false,"verified":false,"followers_count":167,"friends_count":485,"statuses_count":237,"profile_image_url":"http://pbs.twimg.com/profile_images/860380640029573120/HKuXgxR__normal.jpg","profile_image_url_https":"https://pbs.twimg.com/profile_images/860380640029573120/HKuXgxR__normal.jpg"},"3753944237":{"id":"3753944237","created_timestamp":"1443048916258","name":"Teheca","screen_name":"tehecaug","location":"Uganda","description":"We connect new mothers & parents to nurses for postnatal care. #Google LaunchPad Africa 2018, #UNFPA UpAccelerate 2017 #MasterCard Innovation exp 2017 #YCSUS18","url":"https://t.co/i0hcLRwEj7","protected":false,"verified":false,"followers_count":3369,"friends_count":4872,"statuses_count":1128,"profile_image_url":"http://pbs.twimg.com/profile_images/694638274204143616/Q4Mbg1tO_normal.png","profile_image_url_https":"https://pbs.twimg.com/profile_images/694638274204143616/Q4Mbg1tO_normal.png"}}}',
                    "response": 'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\n{"message":"Message Accepted","data":[{"type":"msg","channel_uuid":"5c70a767-f3dc-4a99-9323-4774f6432af5","msg_uuid":"6c26277d-7002-4489-9b7f-998d4be5d0db","text":"Briefly what will you be talking about and do you have any feature stories","urn":"twitterid:767659860#tumaaron","external_id":"1140928844112814089","received_on":"2019-06-18T10:26:48.526Z"}]}',
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
        )
        msg = self.create_incoming_msg(contact, "incoming msg", channel=channel, logs=[log])

        self.login(self.admin)

        read_url = reverse("channels.channellog_msg", args=[channel.uuid, msg.id])

        # check read page shows un-redacted content for a regular org
        response = self.client.get(read_url)
        self.assertNotRedacted(response, ("767659860", "Aaron Tumukunde", "tumaaron"))

        # but for anon org we see redaction...
        with self.anonymous(self.org):
            response = self.client.get(read_url)
            self.assertRedacted(response, ("767659860", "Aaron Tumukunde", "tumaaron"))

    def test_redaction_for_twitter_when_no_match(self):
        urn = "twitterid:767659860"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("TWT", "Test TWT Channel", "nyaruka")
        log = self.create_channel_log(
            ChannelLog.LOG_TYPE_MSG_SEND,
            http_logs=[
                {
                    "url": "https://twitter.com/There is no contact identifying information",
                    "status_code": 200,
                    "request": 'POST /65474/sendMessage HTTP/1.1\r\nHost: api.telegram.org\r\nUser-Agent: Courier/1.2.159\r\nContent-Length: 231\r\nContent-Type: application/x-www-form-urlencoded\r\nAccept-Encoding: gzip\r\n\r\n{"json": "There is no contact identifying information"}',
                    "response": 'HTTP/1.1 200 OK\r\nContent-Length: 298\r\nContent-Type: application/json\r\n\r\n{"json": "There is no contact identifying information"}',
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
        )
        msg = self.create_incoming_msg(contact, "incoming msg", channel=channel, logs=[log])

        self.login(self.admin)

        read_url = reverse("channels.channellog_msg", args=[channel.uuid, msg.id])

        # check read page shows un-redacted content for a regular org
        response = self.client.get(read_url)
        self.assertNotRedacted(response, ("767659860",))

        # but for anon org we see complete redaction...
        with self.anonymous(self.org):
            response = self.client.get(read_url)
            self.assertRedacted(response, ("767659860", "twitter.com", "/65474/sendMessage"))

    def test_redaction_for_facebook(self):
        urn = "facebook:2150393045080607"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("FB", "Test FB Channel", "54764868534")
        log = self.create_channel_log(
            ChannelLog.LOG_TYPE_MSG_SEND,
            http_logs=[
                {
                    "url": f"https://textit.in/c/fb/{channel.uuid}/receive",
                    "status_code": 200,
                    "request": """POST /c/fb/d1117754-f2ab-4348-9572-996ddc1959a8/receive HTTP/1.1\r\nHost: textit.in\r\nAccept: */*\r\nAccept-Encoding: deflate, gzip\r\nContent-Length: 314\r\nContent-Type: application/json\r\n\r\n{"object":"page","entry":[{"id":"311494332880244","time":1559102364444,"messaging":[{"sender":{"id":"2150393045080607"},"recipient":{"id":"311494332880244"},"timestamp":1559102363925,"message":{"mid":"ld5jgfQP8TLBX9FFc3AETshZgE6Zn5UjpY3vY00t3A_YYC2AYDM3quxaodTiHj7nK6lI_ds4WFUJlTmM2l5xoA","seq":0,"text":"hi"}}]}]}""",
                    "response": """HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Type: application/json\r\n\r\n{"message":"Events Handled","data":[{"type":"msg","channel_uuid":"d1117754-f2ab-4348-9572-996ddc1959a8","msg_uuid":"55a3387b-f97e-4270-8157-7ba781a86411","text":"hi","urn":"facebook:2150393045080607","external_id":"ld5jgfQP8TLBX9FFc3AETshZgE6Zn5UjpY3vY00t3A_YYC2AYDM3quxaodTiHj7nK6lI_ds4WFUJlTmM2l5xoA","received_on":"2019-05-29T03:59:23.925Z"}]}""",
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
        )
        msg = self.create_incoming_msg(contact, "incoming msg", channel=channel, logs=[log])

        self.login(self.admin)

        read_url = reverse("channels.channellog_msg", args=[channel.uuid, msg.id])

        response = self.client.get(read_url)

        # check read page shows un-redacted content for a regular org
        response = self.client.get(read_url)
        self.assertNotRedacted(response, ("2150393045080607",))

        # but for anon org we see redaction...
        with self.anonymous(self.org):
            response = self.client.get(read_url)
            self.assertRedacted(response, ("2150393045080607",))

    def test_redaction_for_facebook_when_no_match(self):
        # in this case we are paranoid and mask everything
        urn = "facebook:2150393045080607"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("FB", "Test FB Channel", "54764868534")
        log = self.create_channel_log(
            ChannelLog.LOG_TYPE_MSG_SEND,
            http_logs=[
                {
                    "url": "https://facebook.com/There is no contact identifying information",
                    "status_code": 200,
                    "request": 'POST /65474/sendMessage HTTP/1.1\r\nHost: api.telegram.org\r\nUser-Agent: Courier/1.2.159\r\nContent-Length: 231\r\nContent-Type: application/x-www-form-urlencoded\r\nAccept-Encoding: gzip\r\n\r\n{"json": "There is no contact identifying information"}',
                    "response": 'HTTP/1.1 200 OK\r\nContent-Length: 298\r\nContent-Type: application/json\r\n\r\n{"json": "There is no contact identifying information"}',
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
        )
        msg = self.create_incoming_msg(contact, "incoming msg", channel=channel, logs=[log])

        self.login(self.admin)

        read_url = reverse("channels.channellog_msg", args=[channel.uuid, msg.id])

        # check read page shows un-redacted content for a regular org
        response = self.client.get(read_url)
        self.assertNotRedacted(response, ("2150393045080607",))

        # but for anon org we see complete redaction...
        with self.anonymous(self.org):
            response = self.client.get(read_url)
            self.assertRedacted(response, ("2150393045080607", "facebook.com", "/65474/sendMessage"))

    def test_redaction_for_twilio(self):
        contact = self.create_contact("Fred Jones", phone="+593979099111")
        channel = self.create_channel("T", "Test Twilio Channel", "+12345")
        log = self.create_channel_log(
            ChannelLog.LOG_TYPE_MSG_SEND,
            http_logs=[
                {
                    "url": "https://textit.in/c/t/1234-5678/status?id=2466753&action=callback",
                    "status_code": 200,
                    "request": "POST /c/t/1234-5678/status?id=86598533&action=callback HTTP/1.1\r\nHost: textit.in\r\nAccept: */*\r\nAccept-Encoding: gzip,deflate\r\nCache-Control: max-age=259200\r\nContent-Length: 237\r\nContent-Type: application/x-www-form-urlencoded; charset=utf-8\r\nUser-Agent: TwilioProxy/1.1\r\nX-Amzn-Trace-Id: Root=1-5d5a10b2-8c8b96c86d45a9c6bdc5f43c\r\nX-Forwarded-For: 54.210.179.19\r\nX-Forwarded-Port: 443\r\nX-Forwarded-Proto: https\r\nX-Twilio-Signature: sdgreh54hehrghssghh55=\r\n\r\nSmsSid=SM357343637&SmsStatus=delivered&MessageStatus=delivered&To=%2B593979099111&MessageSid=SM357343637&AccountSid=AC865965965&From=%2B253262278&ApiVersion=2010-04-01&ToCity=Quito&ToCountry=EC",
                    "response": '{"message":"Status Update Accepted","data":[{"type":"status","channel_uuid":"1234-5678","status":"D","msg_id":2466753}]}\n',
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
        )
        msg = self.create_outgoing_msg(contact, "Hi", logs=[log])

        self.login(self.admin)

        read_url = reverse("channels.channellog_msg", args=[channel.uuid, msg.id])

        # check read page shows un-redacted content for a regular org
        response = self.client.get(read_url)
        self.assertNotRedacted(response, ("097 909 9111", "979099111", "Quito"))

        # but for anon org we see redaction...
        with self.anonymous(self.org):
            response = self.client.get(read_url)
            self.assertRedacted(response, ("097 909 9111", "979099111", "Quito"))

    def test_channellog_whatsapp_cloud(self):
        urn = "whatsapp:15128505839"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("WAC", "Test WAC Channel", "54764868534")
        log = ChannelLog.objects.create(
            channel=channel,
            log_type=ChannelLog.LOG_TYPE_MSG_SEND,
            is_error=False,
            http_logs=[
                {
                    "url": f"https://example.com/send/message?access_token={settings.WHATSAPP_ADMIN_SYSTEM_USER_TOKEN}",
                    "status_code": 200,
                    "request": f"""
POST /send/message?access_token={settings.WHATSAPP_ADMIN_SYSTEM_USER_TOKEN} HTTP/1.1
Host: example.com
Accept: */*
Accept-Encoding: gzip;q=1.0,deflate;q=0.6,identity;q=0.3
Content-Length: 343
Content-Type: application/x-www-form-urlencoded
User-Agent: SignalwireCallback/1.0
Authorization: Bearer {settings.WHATSAPP_ADMIN_SYSTEM_USER_TOKEN}
MessageSid=e1d12194-a643-4007-834a-5900db47e262&SmsSid=e1d12194-a643-4007-834a-5900db47e262&AccountSid=<redacted>&From=%2B15618981512&To=%2B15128505839&Body=Hi+Ben+Google+Voice%2C+Did+you+enjoy+your+stay+at+White+Bay+Villas%3F++Answer+with+Yes+or+No.+reply+STOP+to+opt-out.&NumMedia=0&NumSegments=1&MessageStatus=sent""",
                    "response": '{"success": true }',
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
        )
        self.create_incoming_msg(contact, "incoming msg", channel=channel, logs=[log])

        self.login(self.admin)

        read_url = reverse("channels.channellog_read", args=[log.id])

        # the token should have been redacted by courier so blow up rather than let user see it
        with self.assertRaises(AssertionError):
            self.client.get(read_url)

    def test_channellog_anonymous_org_no_msg(self):
        tw_urn = "15128505839"

        tw_channel = self.create_channel("TW", "Test TW Channel", "+12345")

        failed_log = ChannelLog.objects.create(
            channel=tw_channel,
            log_type=ChannelLog.LOG_TYPE_MSG_STATUS,
            is_error=True,
            http_logs=[
                {
                    "url": f"https://textit.in/c/tw/{tw_channel.uuid}/status?action=callback&id=58027120",
                    "status_code": 200,
                    "request": """POST /c/tw/8388f8cd-658f-4fae-925e-ee0792588e68/status?action=callback&id=58027120 HTTP/1.1
Host: textit.in
Accept: */*
Accept-Encoding: gzip;q=1.0,deflate;q=0.6,identity;q=0.3
Content-Length: 343
Content-Type: application/x-www-form-urlencoded
User-Agent: SignalwireCallback/1.0

MessageSid=e1d12194-a643-4007-834a-5900db47e262&SmsSid=e1d12194-a643-4007-834a-5900db47e262&AccountSid=<redacted>&From=%2B15618981512&To=%2B15128505839&Body=Hi+Ben+Google+Voice%2C+Did+you+enjoy+your+stay+at+White+Bay+Villas%3F++Answer+with+Yes+or+No.+reply+STOP+to+opt-out.&NumMedia=0&NumSegments=1&MessageStatus=sent""",
                    "response": """HTTP/1.1 400 Bad Request
Content-Encoding: gzip
Content-Type: application/json

{"message":"Error","data":[{"type":"error","error":"missing request signature"}]}""",
                    "elapsed_ms": 12,
                    "retries": 0,
                    "created_on": "2022-01-01T00:00:00Z",
                }
            ],
            errors=[{"message": "missing request signature", "code": ""}],
        )

        self.login(self.admin)

        read_url = reverse("channels.channellog_read", args=[failed_log.id])

        response = self.client.get(read_url)

        # non anon user can see contact identifying data (in the request)
        self.assertContains(response, tw_urn, count=1)

        with self.anonymous(self.org):
            response = self.client.get(read_url)

            self.assertContains(response, tw_urn, count=0)

            # when we can't identify the contact, request, and response body
            self.assertContains(response, HTTPLog.REDACT_MASK, count=3)


class FacebookWhitelistTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.channel.delete()
        self.channel = Channel.create(
            self.org,
            self.user,
            None,
            "FB",
            "Facebook",
            "1234",
            config={Channel.CONFIG_AUTH_TOKEN: "auth"},
            uuid="00000000-0000-0000-0000-000000001234",
        )

    def test_whitelist(self):
        read_url = reverse("channels.channel_read", args=[self.channel.uuid])
        whitelist_url = reverse("channels.channel_facebook_whitelist", args=[self.channel.uuid])

        response = self.client.get(whitelist_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)
        response = self.client.get(read_url)
        self.assertContains(response, self.channel.name)
        self.assertContentMenu(read_url, self.admin, ["Configuration", "Logs", "Edit", "Delete", "Whitelist Domain"])

        with patch("requests.post") as mock:
            mock.return_value = MockResponse(400, '{"error": { "message": "FB Error" } }')
            response = self.client.post(whitelist_url, dict(whitelisted_domain="https://foo.bar"))
            self.assertFormError(response.context["form"], None, "FB Error")

        with patch("requests.post") as mock:
            mock.return_value = MockResponse(200, '{ "ok": "true" }')
            response = self.client.post(whitelist_url, dict(whitelisted_domain="https://foo.bar"))

            mock.assert_called_once_with(
                "https://graph.facebook.com/v3.3/me/thread_settings?access_token=auth",
                json=dict(
                    setting_type="domain_whitelisting",
                    whitelisted_domains=["https://foo.bar"],
                    domain_action_type="add",
                ),
            )

            self.assertNoFormErrors(response)


class CourierTest(TembaTest):
    def test_courier_urls(self):
        response = self.client.get(reverse("courier.t", args=[self.channel.uuid, "receive"]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b"this URL should be mapped to a Courier instance")
