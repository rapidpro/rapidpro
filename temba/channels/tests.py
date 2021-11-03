import base64
import copy
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import quote

from django_redis import get_redis_connection
from smartmin.tests import SmartminTest

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core import mail
from django.template import loader
from django.test import RequestFactory
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_text

from temba.channels.views import channel_status_processor
from temba.contacts.models import URN, Contact, ContactGroup, ContactURN
from temba.ivr.models import IVRCall
from temba.msgs.models import Msg
from temba.orgs.models import Org
from temba.tests import AnonymousOrg, CRUDLTestMixin, MockResponse, TembaTest, matchers, mock_mailroom
from temba.triggers.models import Trigger
from temba.utils import dict_to_struct, json
from temba.utils.models import generate_uuid

from .models import Alert, Channel, ChannelCount, ChannelEvent, ChannelLog, SyncEvent
from .tasks import (
    check_channels_task,
    squash_channelcounts,
    sync_old_seen_channels_task,
    track_org_channel_counts,
    trim_channel_log_task,
    trim_sync_events_task,
)


class ChannelTest(TembaTest):
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

    def send_message(self, numbers, message, org=None, user=None):
        org = org or self.org
        user = user or self.user

        group = ContactGroup.get_or_create(org, user, "Numbers: %s" % ",".join(numbers))

        contacts = []
        for number in numbers:
            urn = URN.from_tel(number)
            urn_obj = ContactURN.lookup(org, urn)
            if urn_obj:
                contacts.append(urn_obj.contact)
            else:
                contacts.append(self.create_contact("", urns=[urn]))

        group.contacts.add(*contacts)

        broadcast = self.create_broadcast(user, message, groups=[group], msg_status="W")

        msg = Msg.objects.filter(broadcast=broadcast).order_by("text", "pk")
        if len(numbers) == 1:
            return msg.first()
        else:
            return list(msg)

    def assertHasCommand(self, cmd_name, response):
        self.assertEqual(200, response.status_code)
        data = response.json()

        for cmd in data["cmds"]:
            if cmd["cmd"] == cmd_name:
                return

        raise Exception("Did not find '%s' cmd in response: '%s'" % (cmd_name, response.content))

    def test_channel_read_with_customer_support(self):
        self.customer_support.is_staff = True
        self.customer_support.save()

        self.login(self.customer_support)

        response = self.client.get(reverse("channels.channel_read", args=[self.tel_channel.uuid]))

        gear_links = response.context["view"].get_gear_links()
        self.assertListEqual([gl["title"] for gl in gear_links], ["Service"])
        self.assertEqual(
            gear_links[-1]["href"],
            f"/org/service/?organization={self.tel_channel.org_id}&redirect_url=/channels/channel/read/{self.tel_channel.uuid}/",
        )

    def test_deactivate(self):
        self.login(self.admin)
        self.tel_channel.is_active = False
        self.tel_channel.save()
        response = self.client.get(reverse("channels.channel_read", args=[self.tel_channel.uuid]))
        self.assertEqual(404, response.status_code)

    def test_channellog_links(self):
        self.login(self.admin)

        channel_types = (
            ("JN", Channel.DEFAULT_ROLE, None, "Channel Log"),
            ("T", Channel.ROLE_CALL, None, "Call Log"),
            ("T", Channel.ROLE_SEND + Channel.ROLE_CALL, None, "Channel Log"),
            ("EX", Channel.ROLE_RECEIVE, ["tel"], "Channel Log"),
        )

        for channel_type, channel_role, channel_schemes, link_text in channel_types:
            channel = Channel.create(
                self.org,
                self.user,
                None,
                channel_type,
                name="Test Channel",
                role=channel_role,
                schemes=channel_schemes,
            )
            response = self.client.get(reverse("channels.channel_read", args=[channel.uuid]))
            self.assertContains(response, link_text)

    def test_delegate_channels(self):

        self.login(self.admin)

        # we don't support IVR yet
        self.assertFalse(self.org.supports_ivr())

        # pretend we are connected to twiliko
        self.org.config = {"ACCOUNT_SID": "AccountSid", "ACCOUNT_TOKEN": "AccountToken"}
        self.org.save(update_fields=("config",))

        # add a delegate caller
        post_data = dict(channel=self.tel_channel.pk, connection="T")
        response = self.client.post(reverse("channels.channel_create_caller"), post_data)

        # get the caller, make sure config options are set
        caller = Channel.objects.get(org=self.org, role="C")
        self.assertEqual("AccountSid", caller.config["account_sid"])
        self.assertEqual("AccountToken", caller.config["auth_token"])

        # now we should be IVR capable
        self.assertTrue(self.org.supports_ivr())

        # we cannot add multiple callers
        response = self.client.post(reverse("channels.channel_create_caller"), post_data)
        self.assertFormError(response, "form", "channel", "A caller has already been added for that number")

        # should now have the option to disable
        self.login(self.admin)
        response = self.client.get(reverse("channels.channel_read", args=[self.tel_channel.uuid]))
        self.assertContains(response, "Disable Voice Calling")

        # try adding a caller for an invalid channel
        response = self.client.post("%s?channel=20000" % reverse("channels.channel_create_caller"))
        self.assertEqual(200, response.status_code)
        self.assertFormError(response, "form", "channel", "A caller cannot be added for that number")

        # disable our twilio connection
        with patch("temba.channels.types.twilio.TwilioType.deactivate"):
            self.org.remove_twilio_account(self.admin)

        self.assertFalse(self.org.supports_ivr())

        # we should lose our caller
        response = self.client.get(reverse("channels.channel_read", args=[self.tel_channel.uuid]))
        self.assertNotContains(response, "Disable Voice Calling")

        # now try and add it back without a twilio connection
        response = self.client.post(reverse("channels.channel_create_caller"), post_data)

        # shouldn't have added, so no ivr yet
        self.assertFalse(self.assertFalse(self.org.supports_ivr()))

        self.assertEqual(
            "A connection to a Twilio account is required", response.context["form"].errors["connection"][0]
        )

    def test_get_channel_type_name(self):
        self.assertEqual(self.tel_channel.get_channel_type_name(), "Android Phone")
        self.assertEqual(self.twitter_channel.get_channel_type_name(), "Twitter Channel")
        self.assertEqual(self.unclaimed_channel.get_channel_type_name(), "Vonage Channel")

    def test_get_address_display(self):
        self.assertEqual("+250 785 551 212", self.tel_channel.get_address_display())
        self.assertEqual("+250785551212", self.tel_channel.get_address_display(e164=True))

        self.assertEqual("@billy_bob", self.twitter_channel.get_address_display())

        # make sure it works with alphanumeric numbers
        self.tel_channel.address = "EATRIGHT"
        self.assertEqual("EATRIGHT", self.tel_channel.get_address_display())
        self.assertEqual("EATRIGHT", self.tel_channel.get_address_display(e164=True))

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
        flow = self.get_flow("color")
        Trigger.objects.create(
            org=self.org, flow=flow, channel=channel1, modified_by=self.admin, created_by=self.admin
        )

        # create some activity on this channel
        contact = self.create_contact("Bob", phone="+593979123456")
        self.create_incoming_msg(contact, "Hi", channel=channel1)
        self.create_outgoing_msg(contact, "Hi", channel=channel1, status="P")
        self.create_outgoing_msg(contact, "Hi", channel=channel1, status="E")
        self.create_outgoing_msg(contact, "Hi", channel=channel1, status="S")
        Alert.objects.create(
            channel=channel1, alert_type=Alert.TYPE_POWER, created_by=self.admin, modified_by=self.admin
        )
        Alert.objects.create(
            channel=channel1, alert_type=Alert.TYPE_DISCONNECTED, created_by=self.admin, modified_by=self.admin
        )
        SyncEvent.create(
            channel1,
            dict(p_src="AC", p_sts="DIS", p_lvl=80, net="WIFI", pending=[1, 2], retry=[3, 4], cc="RW"),
            [1, 2],
        )

        # and some on another channel
        self.create_outgoing_msg(contact, "Hi", channel=channel2, status="E")
        Alert.objects.create(
            channel=channel2, alert_type=Alert.TYPE_POWER, created_by=self.admin, modified_by=self.admin
        )
        SyncEvent.create(
            channel2,
            dict(p_src="AC", p_sts="DIS", p_lvl=80, net="WIFI", pending=[1, 2], retry=[3, 4], cc="RW"),
            [1, 2],
        )
        Trigger.objects.create(
            org=self.org, flow=flow, channel=channel2, modified_by=self.admin, created_by=self.admin
        )

        # add channel to a flow as a dependency
        flow.channel_dependencies.add(channel1)

        channel1.release(self.admin)

        flow.refresh_from_db()
        self.assertTrue(flow.has_issues)
        self.assertNotIn(channel1, flow.channel_dependencies.all())

        # should have failed the pending and errored messages
        self.assertEqual(2, self.org.msgs.filter(status="F").count())

        self.assertEqual(0, channel1.alerts.count())
        self.assertEqual(0, channel1.sync_events.count())
        self.assertEqual(0, channel1.triggers.filter(is_active=True).count())

        # check that we queued a task to interrupt sessions tied to this channel
        self.assertEqual(
            {
                "org_id": self.org.id,
                "type": "interrupt_sessions",
                "queued_on": matchers.Datetime(),
                "task": {"channel_ids": [channel1.id]},
            },
            mr_mocks.queued_batch_tasks[-1],
        )

        # other channel should be unaffected
        self.assertEqual(1, channel2.msgs.filter(status="E").count())
        self.assertEqual(1, channel2.alerts.count())
        self.assertEqual(1, channel2.sync_events.count())
        self.assertEqual(1, channel2.triggers.filter(is_active=True).count())

    @mock_mailroom
    def test_release_android(self, mr_mocks):
        android = self.claim_new_android()
        self.assertEqual("FCM111", android.config.get(Channel.CONFIG_FCM_ID))

        # add bulk sender
        self.org.connect_vonage("key", "secret", self.admin)
        vonage = Channel.add_vonage_bulk_sender(self.admin, android)

        # release it
        android.release(self.admin)

        android.refresh_from_db()
        vonage.refresh_from_db()

        response = self.sync(android, cmds=[])
        self.assertEqual(200, response.status_code)

        # should be a rel cmd to instruct app to reset
        self.assertEqual({"cmds": [{"cmd": "rel", "relayer_id": str(android.id)}]}, response.json())

        # and FCM ID now cleared
        self.assertIsNone(android.config.get(Channel.CONFIG_FCM_ID))

        # bulk sender was also released
        self.assertFalse(vonage.is_active)

    def test_list(self):
        # de-activate existing channels
        Channel.objects.all().update(is_active=False)

        # list page redirects to claim page
        self.login(self.user)
        response = self.client.get(reverse("channels.channel_list"))
        self.assertRedirect(response, reverse("channels.channel_claim"))

        # unless you're a superuser
        self.login(self.superuser)
        response = self.client.get(reverse("channels.channel_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["object_list"]), [])

        # re-activate one of the channels so org has a single channel
        self.tel_channel.is_active = True
        self.tel_channel.save()

        # list page now redirects to channel read page
        self.login(self.user)
        response = self.client.get(reverse("channels.channel_list"))
        self.assertRedirect(response, reverse("channels.channel_read", args=[self.tel_channel.uuid]))

        # unless you're a superuser
        self.login(self.superuser)
        response = self.client.get(reverse("channels.channel_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["object_list"]), [self.tel_channel])

        # re-activate other channel so org now has two channels
        self.twitter_channel.is_active = True
        self.twitter_channel.save()

        # no-more redirection for anyone
        self.login(self.user)
        response = self.client.get(reverse("channels.channel_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context["object_list"]), {self.tel_channel, self.twitter_channel})

        # clear out the phone and name for the Android channel
        self.tel_channel.name = None
        self.tel_channel.address = None
        self.tel_channel.save()
        response = self.client.get(reverse("channels.channel_list"))
        self.assertContains(response, "Unknown")
        self.assertContains(response, "Android Phone")

    def test_channel_status(self):
        # visit page as a viewer
        self.login(self.user)
        response = self.client.get("/", follow=True)
        self.assertNotIn("unsent_msgs", response.context, msg="Found unsent_msgs in context")
        self.assertNotIn("delayed_syncevents", response.context, msg="Found delayed_syncevents in context")

        # visit page as superuser
        self.login(self.superuser)
        response = self.client.get("/", follow=True)
        # superusers doesn't have orgs thus cannot have both values
        self.assertNotIn("unsent_msgs", response.context, msg="Found unsent_msgs in context")
        self.assertNotIn("delayed_syncevents", response.context, msg="Found delayed_syncevents in context")

        # visit page as administrator
        self.login(self.admin)
        response = self.client.get("/", follow=True)

        # there is not unsent nor delayed syncevents
        self.assertNotIn("unsent_msgs", response.context, msg="Found unsent_msgs in context")
        self.assertNotIn("delayed_syncevents", response.context, msg="Found delayed_syncevents in context")

        # replace existing channels with a single Android device
        Channel.objects.update(is_active=False)
        channel = Channel.create(
            self.org,
            self.user,
            None,
            "A",
            None,
            "+250781112222",
            config={Channel.CONFIG_FCM_ID: "asdf"},
            secret="asdf",
            created_on=(timezone.now() - timedelta(hours=2)),
        )

        response = self.client.get("/", Follow=True)
        self.assertNotIn("delayed_syncevents", response.context)
        self.assertNotIn("unsent_msgs", response.context, msg="Found unsent_msgs in context")

        # simulate a sync in back in two hours
        self.sync(
            channel,
            cmds=[
                # device details status
                dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="60", net="UMTS", pending=[], retry=[])
            ],
        )
        sync_event = SyncEvent.objects.all()[0]
        sync_event.created_on = timezone.now() - timedelta(hours=2)
        sync_event.save()

        response = self.client.get("/", Follow=True)
        self.assertIn("delayed_syncevents", response.context)
        self.assertNotIn("unsent_msgs", response.context, msg="Found unsent_msgs in context")

        contact = self.create_contact("Bob", phone="+250788123123")

        # add a message, just sent so shouldn't have delayed
        msg = self.create_outgoing_msg(contact, "test", channel=channel)
        response = self.client.get("/", Follow=True)
        self.assertIn("delayed_syncevents", response.context)
        self.assertNotIn("unsent_msgs", response.context, msg="Found unsent_msgs in context")

        # but put it in the past
        msg.delete()
        with patch("django.utils.timezone.now", return_value=timezone.now() - timedelta(hours=3)):
            self.create_outgoing_msg(contact, "test", channel=channel, status=Msg.STATUS_QUEUED)

        response = self.client.get("/", Follow=True)
        self.assertIn("delayed_syncevents", response.context)
        self.assertIn("unsent_msgs", response.context, msg="Found unsent_msgs in context")

        # if there is a successfully sent message after sms was created we do not consider it as delayed
        with patch("django.utils.timezone.now", return_value=timezone.now() - timedelta(hours=2)):
            success_msg = self.create_outgoing_msg(contact, "success-send", channel=channel)

        success_msg.sent_on = timezone.now() - timedelta(hours=2)
        success_msg.status = "S"
        success_msg.save()
        response = self.client.get("/", Follow=True)
        self.assertIn("delayed_syncevents", response.context)
        self.assertNotIn("unsent_msgs", response.context, msg="Found unsent_msgs in context")

        # test that editors have the channel of the the org the are using
        other_user = self.create_user("Other")
        self.org2.administrators.add(other_user)
        self.org.editors.add(other_user)
        self.assertFalse(self.org2.channels.all())

        self.login(other_user)

        other_user.set_org(self.org2)

        self.assertEqual(self.org2, other_user.get_org())
        response = self.client.get("/", follow=True)
        self.assertNotIn("channel_type", response.context, msg="Found channel_type in context")

        other_user.set_org(self.org)

        self.assertEqual(1, self.org.channels.filter(is_active=True).count())
        self.assertEqual(self.org, other_user.get_org())

        response = self.client.get("/", follow=True)
        # self.assertIn('channel_type', response.context)

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
        response = self.client.get(reverse("channels.channel_read", args=[self.tel_channel.uuid]))
        self.assertRedirect(response, reverse("orgs.org_choose"))

        # org users can
        response = self.fetch_protected(reverse("channels.channel_read", args=[self.tel_channel.uuid]), self.user)

        self.assertEqual(
            len(response.context["source_stats"]),
            len(SyncEvent.objects.values_list("power_source", flat=True).distinct()),
        )
        self.assertEqual("AC", response.context["source_stats"][0][0])
        self.assertEqual(1, response.context["source_stats"][0][1])
        self.assertEqual("BAT", response.context["source_stats"][1][0])
        self.assertEqual(1, response.context["source_stats"][0][1])

        self.assertEqual(
            len(response.context["network_stats"]),
            len(SyncEvent.objects.values_list("network_type", flat=True).distinct()),
        )
        self.assertEqual("UMTS", response.context["network_stats"][0][0])
        self.assertEqual(1, response.context["network_stats"][0][1])
        self.assertEqual("WIFI", response.context["network_stats"][1][0])
        self.assertEqual(1, response.context["network_stats"][1][1])

        self.assertTrue(len(response.context["latest_sync_events"]) <= 5)

        response = self.fetch_protected(reverse("channels.channel_read", args=[self.tel_channel.uuid]), self.admin)
        self.assertNotContains(response, "Enable Voice")

        # Add twilio credentials to make sure we can add calling for our android channel
        self.org.config.update({Org.CONFIG_TWILIO_SID: "SID", Org.CONFIG_TWILIO_TOKEN: "TOKEN"})
        self.org.save(update_fields=("config",))

        response = self.fetch_protected(reverse("channels.channel_read", args=[self.tel_channel.uuid]), self.admin)
        self.assertTrue(self.org.is_connected_to_twilio())
        self.assertContains(response, "Enable Voice")

        two_hours_ago = timezone.now() - timedelta(hours=2)

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

        response = self.fetch_protected(reverse("channels.channel_read", args=[self.tel_channel.uuid]), self.admin)
        self.assertIn("delayed_sync_event", response.context_data.keys())
        self.assertIn("unsent_msgs_count", response.context_data.keys())

        # with superuser
        response = self.fetch_protected(reverse("channels.channel_read", args=[self.tel_channel.uuid]), self.superuser)
        self.assertEqual(200, response.status_code)

        # now that we can access the channel, which messages do we display in the chart?
        joe = self.create_contact("Joe", phone="+2501234567890")

        # should have two series, one for incoming one for outgoing
        self.assertEqual(2, len(response.context["message_stats"]))

        # but only an outgoing message so far
        self.assertEqual(0, len(response.context["message_stats"][0]["data"]))
        self.assertEqual(1, response.context["message_stats"][1]["data"][-1]["count"])

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
        response = self.fetch_protected(reverse("channels.channel_read", args=[self.tel_channel.uuid]), self.superuser)
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.context["message_stats"][0]["data"][-1]["count"])

        # this assertion is problematic causing time-sensitive failures, to reconsider
        # self.assertEqual(2, response.context['message_stats'][1]['data'][-1]['count'])

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
        self.create_incoming_msg(joe, "incoming ivr", channel=self.tel_channel, msg_type=Msg.TYPE_IVR)
        self.create_outgoing_msg(joe, "outgoing ivr", channel=self.tel_channel, msg_type=Msg.TYPE_IVR)
        response = self.fetch_protected(reverse("channels.channel_read", args=[self.tel_channel.uuid]), self.superuser)

        self.assertEqual(4, len(response.context["message_stats"]))
        self.assertEqual(1, response.context["message_stats"][2]["data"][0]["count"])
        self.assertEqual(1, response.context["message_stats"][3]["data"][0]["count"])

        self.assertEqual(1, len(response.context["message_stats_table"]))
        self.assertEqual(1, response.context["message_stats_table"][0]["incoming_messages_count"])
        self.assertEqual(2, response.context["message_stats_table"][0]["outgoing_messages_count"])
        self.assertEqual(1, response.context["message_stats_table"][0]["incoming_ivr_count"])
        self.assertEqual(1, response.context["message_stats_table"][0]["outgoing_ivr_count"])

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

        # one recommended channel (Mtarget in Rwanda)
        self.assertEqual(len(response.context["recommended_channels"]), 2)

        self.assertEqual(response.context["channel_types"]["PHONE"][0].code, "T")
        self.assertEqual(response.context["channel_types"]["PHONE"][1].code, "TMS")
        self.assertEqual(response.context["channel_types"]["PHONE"][2].code, "NX")
        self.assertEqual(response.context["channel_types"]["PHONE"][3].code, "CT")
        self.assertEqual(response.context["channel_types"]["PHONE"][4].code, "EX")

        self.org.timezone = "Canada/Central"
        self.org.save()

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertEqual(200, response.status_code)

        self.assertEqual(len(response.context["recommended_channels"]), 3)
        self.assertEqual(response.context["recommended_channels"][0].code, "T")
        self.assertEqual(response.context["recommended_channels"][1].code, "TMS")
        self.assertEqual(response.context["recommended_channels"][2].code, "NX")

        self.assertEqual(response.context["channel_types"]["PHONE"][0].code, "CT")
        self.assertEqual(response.context["channel_types"]["PHONE"][1].code, "EX")
        self.assertEqual(response.context["channel_types"]["PHONE"][2].code, "I2")
        self.assertEqual(response.context["channel_types"]["PHONE"][3].code, "IB")
        self.assertEqual(response.context["channel_types"]["PHONE"][4].code, "JS")

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
        self.assertEqual(len(response.context["recommended_channels"]), 2)

        self.assertEqual(response.context["channel_types"]["PHONE"][0].code, "AC")
        self.assertEqual(response.context["channel_types"]["PHONE"][1].code, "T")
        self.assertEqual(response.context["channel_types"]["PHONE"][2].code, "TMS")
        self.assertEqual(response.context["channel_types"]["PHONE"][-2].code, "YO")
        self.assertEqual(response.context["channel_types"]["PHONE"][-1].code, "ZVS")

        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][0].code, "D3")
        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][1].code, "ZVW")
        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][2].code, "TWA")
        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][3].code, "FBA")

        self.admin.groups.add(Group.objects.get(name="Beta"))

        response = self.client.get(reverse("channels.channel_claim_all"))
        self.assertEqual(200, response.status_code)

        # should see all channel types having a category including beta only channel types
        self.assertEqual(len(response.context["recommended_channels"]), 2)

        self.assertEqual(response.context["channel_types"]["PHONE"][0].code, "AC")
        self.assertEqual(response.context["channel_types"]["PHONE"][1].code, "T")
        self.assertEqual(response.context["channel_types"]["PHONE"][2].code, "TMS")
        self.assertEqual(response.context["channel_types"]["PHONE"][-2].code, "YO")
        self.assertEqual(response.context["channel_types"]["PHONE"][-1].code, "ZVS")

        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][0].code, "WA")
        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][1].code, "D3")
        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][2].code, "ZVW")
        self.assertEqual(response.context["channel_types"]["SOCIAL_MEDIA"][3].code, "TWA")

    def test_register_unsupported_android(self):
        # remove our explicit country so it needs to be derived from channels
        self.org.country = None
        self.org.save()

        Channel.objects.all().delete()

        reg_data = dict(cmds=[dict(cmd="gcm", gcm_id="GCM111", uuid="uuid"), dict(cmd="status", cc="RW", dev="Nexus")])

        # try a post register
        response = self.client.post(reverse("register"), json.dumps(reg_data), content_type="application/json")
        self.assertEqual(200, response.status_code)

        response_json = response.json()
        self.assertEqual(
            response_json,
            dict(cmds=[dict(cmd="reg", relayer_claim_code="*********", relayer_secret="0" * 64, relayer_id=-1)]),
        )

        # missing uuid raises
        reg_data = dict(cmds=[dict(cmd="fcm", fcm_id="FCM111"), dict(cmd="status", cc="RW", dev="Nexus")])

        with self.assertRaises(ValueError):
            self.client.post(reverse("register"), json.dumps(reg_data), content_type="application/json")

        # missing fcm_id raises
        reg_data = dict(cmds=[dict(cmd="fcm", uuid="uuid"), dict(cmd="status", cc="RW", dev="Nexus")])
        with self.assertRaises(ValueError):
            self.client.post(reverse("register"), json.dumps(reg_data), content_type="application/json")

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

    def test_no_topup_quota_exceeded(self):
        # reduce out credits to 10
        self.org.topups.all().update(credits=10)
        self.org.clear_credit_cache()

        self.assertEqual(10, self.org.get_credits_remaining())
        self.assertEqual(0, self.org.get_credits_used())

        # if we sync should get one message back
        self.send_message(["250788382382"], "How is it going?")

        response = self.sync(self.tel_channel, cmds=[])
        self.assertEqual(200, response.status_code)
        response = response.json()
        self.assertEqual(1, len(response["cmds"]))

        self.assertEqual(9, self.org.get_credits_remaining())
        self.assertEqual(1, self.org.get_credits_used())

        # let's create 10 other messages
        for i in range(10):
            self.send_message(["250788382%03d" % i], "This is message # %d" % i)

        # should send all the 11 messages that exist
        response = self.sync(self.tel_channel, cmds=[])
        self.assertEqual(200, response.status_code)
        response = response.json()
        self.assertEqual(11, len(response["cmds"]))

    def test_sync_broadcast_multiple_channels(self):
        self.org.administrators.add(self.user)
        self.user.set_org(self.org)

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

        # send a broadcast to urn that have different preferred channels
        self.send_message(["250788382382", "250788383383"], "How is it going?")

        # Should contain messages for the the channel only
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

        contact = self.create_contact("Bob", phone="+250788382382")

        # create a payload from the client
        bcast = self.send_message(["250788382382", "250788383383"], "How is it going?")
        msg1 = bcast[0]
        msg2 = bcast[1]
        msg3 = self.send_message(["250788382382"], "What is your name?")
        msg4 = self.send_message(["250788382382"], "Do you have any children?")
        msg5 = self.send_message(["250788382382"], "What's my dog's name?")
        msg6 = self.send_message(["250788382382"], "from when?")

        # an incoming message that should not be included even if it is still pending
        incoming_message = self.create_incoming_msg(
            contact, "hey", channel=self.tel_channel, status=Msg.STATUS_PENDING
        )

        self.org.administrators.add(self.user)
        self.user.set_org(self.org)

        # Check our sync point has all three messages queued for delivery
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
        msg6 = self.send_message(
            ["250788382382"], "Pretend this message is in retry on the client, don't send it on sync"
        )

        # a pending outgoing message should be included
        self.create_outgoing_msg(
            msg6.contact, "Hello, we heard from you.", channel=self.tel_channel, status=Msg.STATUS_QUEUED
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
            dict(cmd="call", phone="2505551212", type="miss", ts=date),
            # repeated missed calls should be skipped
            dict(cmd="call", phone="2505551212", type="miss", ts=date),
            dict(cmd="call", phone="2505551212", type="miss", ts=date),
            # incoming
            dict(cmd="call", phone="2505551212", type="mt", dur=10, ts=date),
            # repeated calls should be skipped
            dict(cmd="call", phone="2505551212", type="mt", dur=10, ts=date),
            # incoming, invalid URN
            dict(cmd="call", phone="*", type="mt", dur=10, ts=date),
            # outgoing
            dict(cmd="call", phone="+250788383383", type="mo", dur=5, ts=date),
            # a new incoming message
            dict(cmd="mo_sms", phone="+250788383383", msg="This is giving me trouble", p_id="1", ts=date),
            # an incoming message from an empty contact
            dict(cmd="mo_sms", phone="", msg="This is spam", p_id="2", ts=date),
        ]

        # now send the channel's updates
        response = self.sync(self.tel_channel, cmds=cmds)

        self.tel_channel.refresh_from_db()
        self.assertEqual(self.tel_channel.config["FCM_ID"], "12345")
        self.assertTrue(self.tel_channel.last_seen > six_mins_ago)

        # new batch, our ack and our claim command for new org
        self.assertEqual(5, len(response.json()["cmds"]))
        self.assertContains(response, "Hello, we heard from you.")
        self.assertContains(response, "mt_bcast")

        # check that our messages were updated accordingly
        self.assertEqual(2, Msg.objects.filter(channel=self.tel_channel, status="S", direction="O").count())
        self.assertEqual(1, Msg.objects.filter(channel=self.tel_channel, status="D", direction="O").count())
        self.assertEqual(1, Msg.objects.filter(channel=self.tel_channel, status="E", direction="O").count())
        self.assertEqual(2, Msg.objects.filter(channel=self.tel_channel, status="F", direction="O").count())

        # we should now have two incoming messages
        self.assertEqual(3, Msg.objects.filter(direction="I").count())

        # one of them should have an empty 'tel'
        self.assertTrue(Msg.objects.filter(direction="I", contact_urn__path="empty"))

        # We should now have one sync
        self.assertEqual(1, SyncEvent.objects.filter(channel=self.tel_channel).count())

        # We should have 3 channel event
        self.assertEqual(3, ChannelEvent.objects.filter(channel=self.tel_channel).count())

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

        # set an email on our channel
        self.tel_channel.alert_email = "fred@worldrelif.org"
        self.tel_channel.save()

        # We should not have an alert this time
        self.assertEqual(0, Alert.objects.all().count())

        # the case the status must be be reported
        response = self.sync(
            self.tel_channel,
            cmds=[
                # device details status
                dict(cmd="status", p_sts="DIS", p_src="BAT", p_lvl="20", net="UMTS", retry=[], pending=[])
            ],
        )

        # we should now have an Alert
        self.assertEqual(1, Alert.objects.all().count())

        # and at this time it must be not ended
        self.assertEqual(
            1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type="P").count()
        )

        # the case the status must be be reported but already notification sent
        response = self.sync(
            self.tel_channel,
            cmds=[
                # device details status
                dict(cmd="status", p_sts="DIS", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
            ],
        )

        # we should not create a new alert
        self.assertEqual(1, Alert.objects.all().count())

        # still not ended
        self.assertEqual(
            1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type="P").count()
        )

        # Let plug the channel to charger
        response = self.sync(
            self.tel_channel,
            cmds=[
                # device details status
                dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
            ],
        )

        # only one alert
        self.assertEqual(1, Alert.objects.all().count())

        # and we end all alert related to this issue
        self.assertEqual(
            0, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type="P").count()
        )

        # make our events old so we can test trimming them
        SyncEvent.objects.all().update(created_on=timezone.now() - timedelta(days=45))
        trim_sync_events_task()

        # should be cleared out
        self.assertEqual(1, SyncEvent.objects.all().count())
        self.assertFalse(Alert.objects.exists())

        # the case the status is in unknown state
        response = self.sync(
            self.tel_channel,
            cmds=[
                # device details status
                dict(cmd="status", p_sts="UNK", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
            ],
        )

        # we should now create a new alert
        self.assertEqual(1, Alert.objects.all().count())

        # one alert not ended
        self.assertEqual(
            1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type="P").count()
        )

        # Let plug the channel to charger to end this unknown power status
        response = self.sync(
            self.tel_channel,
            cmds=[
                # device details status
                dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
            ],
        )

        # still only one alert
        self.assertEqual(1, Alert.objects.all().count())

        # and we end all alert related to this issue
        self.assertEqual(
            0, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type="P").count()
        )

        # clear all the alerts
        Alert.objects.all().delete()

        # the case the status is in not charging state
        response = self.sync(
            self.tel_channel,
            cmds=[
                # device details status
                dict(cmd="status", p_sts="NOT", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
            ],
        )

        # we should now create a new alert
        self.assertEqual(1, Alert.objects.all().count())

        # one alert not ended
        self.assertEqual(
            1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type="P").count()
        )

        # Let plug the channel to charger to end this unknown power status
        response = self.sync(
            self.tel_channel,
            cmds=[
                # device details status
                dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
            ],
        )

        # first we have a new alert created
        self.assertEqual(1, Alert.objects.all().count())

        # and we end all alert related to this issue
        self.assertEqual(
            0, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type="P").count()
        )

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

    def test_ignore_android_incoming_msg_invalid_phone(self):
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

    @mock_mailroom
    def test_inbox_duplication(self, mr_mocks):

        # if the connection gets interrupted but some messages succeed, we want to make sure subsequent
        # syncs do not result in duplication of messages from the inbox
        date = timezone.now()
        date = int(time.mktime(date.timetuple())) * 1000

        response = self.sync(
            self.tel_channel,
            cmds=[
                dict(cmd="mo_sms", phone="2505551212", msg="First message", p_id="1", ts=date),
                dict(cmd="mo_sms", phone="2505551212", msg="First message", p_id="2", ts=date),
                dict(cmd="mo_sms", phone="2505551212", msg="A second message", p_id="3", ts=date),
            ],
        )
        self.assertEqual(200, response.status_code)

        responses = response.json()
        cmds = responses["cmds"]

        # check the server gave us responses for our messages
        r0 = self.get_response(cmds, "1")
        r1 = self.get_response(cmds, "2")
        r2 = self.get_response(cmds, "3")

        self.assertIsNotNone(r0)
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)

        # first two should have the same server id
        self.assertEqual(r0["extra"], r1["extra"])

        # One was a duplicate, should only have 2
        self.assertEqual(2, Msg.objects.filter(direction="I").count())

    def get_response(self, responses, p_id):
        for response in responses:
            if "p_id" in response and response["p_id"] == p_id:
                return response

    def test_channel_status_processor(self):

        request = RequestFactory().get("/")
        request.user = self.admin

        def get_context(channel_type, role):
            Channel.objects.all().delete()
            Channel.create(
                self.org,
                self.admin,
                "RW",
                channel_type,
                None,
                "1234",
                config=dict(username="junebug-user", password="junebug-pass", send_url="http://example.org/"),
                uuid="00000000-0000-0000-0000-000000001234",
                role=role,
            )
            return channel_status_processor(request)

        Channel.objects.all().delete()
        no_channel_context = channel_status_processor(request)
        self.assertFalse(no_channel_context["has_outgoing_channel"])

        sms_context = get_context("JN", Channel.ROLE_SEND)
        self.assertTrue(sms_context["has_outgoing_channel"])


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

        # fields shown depend on scheme and role
        self.assertUpdateFetch(
            android_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields={"name": "My Android", "alert_email": None, "allow_international": False},
        )
        self.assertUpdateFetch(
            vonage_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields={
                "name": "My Vonage",
                "alert_email": None,
                "allow_international": False,
                "machine_detection": False,
            },
        )
        self.assertUpdateFetch(
            telegram_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields={"name": "My Telegram", "alert_email": None},
        )

        # name can't be empty
        self.assertUpdateSubmit(
            android_url,
            {"name": ""},
            form_errors={"name": "This field is required."},
            object_unchanged=android_channel,
        )

        # make some changes
        self.assertUpdateSubmit(
            vonage_url,
            {
                "name": "Updated Name",
                "alert_email": "bob@nyaruka.com",
                "allow_international": True,
                "machine_detection": True,
            },
        )

        vonage_channel.refresh_from_db()
        self.assertEqual("Updated Name", vonage_channel.name)
        self.assertEqual("+1234567890", vonage_channel.address)
        self.assertEqual("bob@nyaruka.com", vonage_channel.alert_email)
        self.assertTrue(vonage_channel.config.get("allow_international"))
        self.assertTrue(vonage_channel.config.get("machine_detection"))

        self.assertUpdateFetch(
            vonage_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields={
                "name": "Updated Name",
                "alert_email": "bob@nyaruka.com",
                "allow_international": True,
                "machine_detection": True,
            },
        )

    def test_delete(self):
        delete_url = reverse("channels.channel_delete", args=[self.ex_channel.uuid])

        # fetch delete modal
        response = self.assertDeleteFetch(delete_url, allow_editors=True)
        self.assertContains(response, "You are about to delete")

        # submit to delete it
        response = self.assertDeleteSubmit(delete_url, object_deactivated=self.ex_channel, success_status=200)
        self.assertEqual("/org/home/", response["Temba-Success"])

        # reactivate
        self.ex_channel.is_active = True
        self.ex_channel.save()

        # add a dependency and try again
        flow = self.create_flow("Color Flow")
        flow.channel_dependencies.add(self.ex_channel)
        self.assertFalse(flow.has_issues)

        response = self.assertDeleteFetch(delete_url, allow_editors=True)
        self.assertContains(response, "is used by the following items but can still be deleted:")
        self.assertContains(response, "Color Flow")

        self.assertDeleteSubmit(delete_url, object_deactivated=self.ex_channel, success_status=200)

        flow.refresh_from_db()
        self.assertTrue(flow.has_issues)
        self.assertNotIn(self.ex_channel, flow.channel_dependencies.all())

    def test_delete_delegate(self):
        self.org.connect_vonage("key", "secret", self.admin)
        android = Channel.create(
            self.org, self.admin, "RW", "A", name="Android", address="+250785551313", role="SR", schemes=("tel",)
        )
        vonage = Channel.add_vonage_bulk_sender(self.admin, android)

        delete_url = reverse("channels.channel_delete", args=[vonage.uuid])

        # fetch delete modal
        response = self.assertDeleteFetch(delete_url, allow_editors=True)
        self.assertContains(response, "Disable Bulk Sending")

        # try when delegate is a caller instead
        vonage.role = "C"
        vonage.save(update_fields=("role",))

        # fetch delete modal
        response = self.assertDeleteFetch(delete_url, allow_editors=True)
        self.assertContains(response, "Disable Voice Calling")

        # submit to delete it - should be redirected to the Android channel page
        response = self.assertDeleteSubmit(delete_url, object_deactivated=vonage, success_status=200)
        self.assertEqual(f"/channels/channel/read/{android.uuid}/", response["Temba-Success"])


class ChannelEventCRUDLTest(TembaTest):
    def test_calls(self):
        self.create_channel_event(self.channel, "tel:12345", ChannelEvent.TYPE_CALL_IN, extra=dict(duration=600))
        self.create_channel_event(self.channel, "tel:890", ChannelEvent.TYPE_CALL_IN_MISSED)
        self.create_channel_event(self.channel, "tel:456767", ChannelEvent.TYPE_UNKNOWN)

        list_url = reverse("channels.channelevent_calls")

        response = self.fetch_protected(list_url, self.user)

        self.assertEqual(response.context["object_list"].count(), 2)
        self.assertContains(response, "Missed Incoming Call")
        self.assertContains(response, "Incoming Call (600 seconds)")


class SyncEventTest(SmartminTest):
    def setUp(self):
        self.superuser = User.objects.create_superuser(username="super", email="super@user.com", password="super")
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


class ChannelAlertTest(TembaTest):
    def test_no_alert_email(self):
        # set our last seen to a while ago
        self.channel.last_seen = timezone.now() - timedelta(minutes=40)
        self.channel.save()

        check_channels_task()
        self.assertTrue(len(mail.outbox) == 0)

        # add alert email, remove org and set last seen to now to force an resolve email to try to send
        self.channel.alert_email = "fred@unicef.org"
        self.channel.org = None
        self.channel.last_seen = timezone.now()
        self.channel.save()
        check_channels_task()

        self.assertTrue(len(mail.outbox) == 0)


class ChannelSyncTest(TembaTest):
    @patch("temba.channels.models.Channel.trigger_sync")
    def test_sync_old_seen_chaanels(self, mock_trigger_sync):
        self.channel.last_seen = timezone.now() - timedelta(days=40)
        self.channel.save()

        sync_old_seen_channels_task()
        self.assertFalse(mock_trigger_sync.called)

        self.channel.last_seen = timezone.now() - timedelta(minutes=5)
        self.channel.save()

        sync_old_seen_channels_task()
        self.assertFalse(mock_trigger_sync.called)

        self.channel.last_seen = timezone.now() - timedelta(hours=3)
        self.channel.save()

        sync_old_seen_channels_task()
        self.assertTrue(mock_trigger_sync.called)


class ChannelClaimTest(TembaTest):
    @override_settings(SEND_EMAILS=True)
    def test_disconnected_alert(self):
        # set our last seen to a while ago
        self.channel.alert_email = "fred@unicef.org"
        self.channel.last_seen = timezone.now() - timedelta(minutes=40)
        self.channel.save()

        branding = copy.deepcopy(settings.BRANDING)
        branding["rapidpro.io"]["from_email"] = "support@mybrand.com"
        with self.settings(BRANDING=branding):
            check_channels_task()

            # should have created one alert
            alert = Alert.objects.get()
            self.assertEqual(self.channel, alert.channel)
            self.assertEqual(Alert.TYPE_DISCONNECTED, alert.alert_type)
            self.assertFalse(alert.ended_on)

            self.assertTrue(len(mail.outbox) == 1)
            template = "channels/email/disconnected_alert.txt"
            context = dict(
                org=self.channel.org,
                channel=self.channel,
                now=timezone.now(),
                branding=self.channel.org.get_branding(),
                last_seen=self.channel.last_seen,
                sync=alert.sync_event,
            )

            text_template = loader.get_template(template)
            text = text_template.render(context)

            self.assertEqual(mail.outbox[0].body, text)
            self.assertEqual(mail.outbox[0].from_email, "support@mybrand.com")

        # call it again
        check_channels_task()

        # still only one alert
        self.assertEqual(1, Alert.objects.all().count())
        self.assertTrue(len(mail.outbox) == 1)

        # ok, let's have the channel show up again
        self.channel.last_seen = timezone.now() + timedelta(minutes=5)
        self.channel.save()

        check_channels_task()

        # still only one alert, but it is now ended
        alert = Alert.objects.get()
        self.assertTrue(alert.ended_on)
        self.assertTrue(len(mail.outbox) == 2)
        template = "channels/email/connected_alert.txt"
        context = dict(
            org=self.channel.org,
            channel=self.channel,
            now=timezone.now(),
            branding=self.channel.org.get_branding(),
            last_seen=self.channel.last_seen,
            sync=alert.sync_event,
        )

        text_template = loader.get_template(template)
        text = text_template.render(context)

        self.assertEqual(mail.outbox[1].body, text)

    @override_settings(SEND_EMAILS=True)
    def test_sms_alert(self):
        contact = self.create_contact("John Doe", phone="123")

        # create a message from two hours ago
        one_hour_ago = timezone.now() - timedelta(hours=1)
        two_hours_ago = timezone.now() - timedelta(hours=2)
        three_hours_ago = timezone.now() - timedelta(hours=3)
        four_hours_ago = timezone.now() - timedelta(hours=4)
        five_hours_ago = timezone.now() - timedelta(hours=5)
        six_hours_ago = timezone.now() - timedelta(hours=6)

        msg1 = self.create_outgoing_msg(contact, "Message One", created_on=five_hours_ago, status="Q")

        # make sure our channel has been seen recently
        self.channel.last_seen = timezone.now()
        self.channel.alert_email = "fred@unicef.org"
        self.channel.org = self.org
        self.channel.save()

        # ok check on our channel
        check_channels_task()

        # we don't have  successfully sent message and we have an alert and only one
        self.assertEqual(Alert.objects.all().count(), 1)

        alert = Alert.objects.get()
        self.assertEqual(self.channel, alert.channel)
        self.assertEqual(Alert.TYPE_SMS, alert.alert_type)
        self.assertFalse(alert.ended_on)
        self.assertTrue(len(mail.outbox) == 1)

        # let's end the alert
        alert = Alert.objects.all()[0]
        alert.ended_on = six_hours_ago
        alert.save()

        dany = self.create_contact("Dany Craig", phone="765")

        # let have a recent sent message
        sent_msg = self.create_outgoing_msg(
            dany, "SENT Message", created_on=four_hours_ago, sent_on=one_hour_ago, status="D"
        )

        # ok check on our channel
        check_channels_task()

        # if latest_sent_message is after our queued message no alert is created
        self.assertEqual(Alert.objects.all().count(), 1)

        # consider the sent message was sent before our queued msg
        sent_msg.sent_on = three_hours_ago
        sent_msg.save()

        msg1.delete()
        msg1 = self.create_outgoing_msg(contact, "Message One", created_on=two_hours_ago, status="Q")

        # check our channel again
        check_channels_task()

        #  no new alert created because we sent one in the past hour
        self.assertEqual(Alert.objects.all().count(), 1)

        sent_msg.sent_on = six_hours_ago
        sent_msg.save()

        alert = Alert.objects.all()[0]
        alert.created_on = six_hours_ago
        alert.save()

        # check our channel again
        check_channels_task()

        # this time we have a new alert and should create only one
        self.assertEqual(Alert.objects.all().count(), 2)

        # get the alert which is not ended
        alert = Alert.objects.get(ended_on=None)
        self.assertEqual(self.channel, alert.channel)
        self.assertEqual(Alert.TYPE_SMS, alert.alert_type)
        self.assertFalse(alert.ended_on)
        self.assertTrue(len(mail.outbox) == 2)

        # create another open SMS alert
        Alert.objects.create(
            channel=self.channel,
            alert_type=Alert.TYPE_SMS,
            created_on=timezone.now(),
            created_by=self.admin,
            modified_on=timezone.now(),
            modified_by=self.admin,
        )

        # run again, nothing should change
        with self.assertNumQueries(9):
            check_channels_task()

        self.assertEqual(2, Alert.objects.filter(channel=self.channel, ended_on=None).count())
        self.assertTrue(len(mail.outbox) == 2)

        # fix our message
        msg1.status = "D"
        msg1.sent_on = timezone.now()
        msg1.save(update_fields=("status", "sent_on"))

        # run again, our alert should end
        check_channels_task()

        # still only one alert though, and no new email sent, alert must not be ended before one hour
        alert = Alert.objects.all().latest("ended_on")
        self.assertTrue(alert.ended_on)
        self.assertTrue(len(mail.outbox) == 2)


class ChannelCountTest(TembaTest):
    def assertDailyCount(self, channel, assert_count, count_type, day):
        calculated_count = ChannelCount.get_day_count(channel, count_type, day)
        self.assertEqual(assert_count, calculated_count)

    def test_daily_counts(self):
        self.admin.set_org(self.org)

        # no channel counts
        self.assertFalse(ChannelCount.objects.all())

        # contact without a channel
        contact = self.create_contact("Joe", phone="+250788111222")
        self.create_incoming_msg(contact, "Test Message", surveyor=True)

        # still no channel counts
        self.assertFalse(ChannelCount.objects.all())

        # incoming msg with a channel
        msg = self.create_incoming_msg(contact, "Test Message")
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        # insert another
        msg = self.create_incoming_msg(contact, "Test Message")
        self.assertDailyCount(self.channel, 2, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        # squash our counts
        squash_channelcounts()

        # same count
        self.assertDailyCount(self.channel, 2, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        # and only one channel count
        self.assertEqual(ChannelCount.objects.all().count(), 1)

        # deleting a message doesn't decrement the count
        msg.release()
        self.assertDailyCount(self.channel, 2, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        ChannelCount.objects.all().delete()

        # ok, test outgoing now
        msg = self.create_outgoing_msg(contact, "Real Message", channel=self.channel)
        log = ChannelLog.objects.create(channel=self.channel, msg=msg, description="Unable to send", is_error=True)

        # squash our counts
        squash_channelcounts()

        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_MSG_TYPE, msg.created_on.date())
        self.assertEqual(ChannelCount.objects.filter(count_type=ChannelCount.SUCCESS_LOG_TYPE).count(), 0)
        self.assertEqual(ChannelCount.objects.filter(count_type=ChannelCount.ERROR_LOG_TYPE).count(), 1)

        # delete our log, should decrement our count
        log.delete()
        self.assertEqual(0, self.channel.get_count([ChannelCount.ERROR_LOG_TYPE]))

        # deleting a message doesn't decrement the count
        msg.release()
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_MSG_TYPE, msg.created_on.date())

        ChannelCount.objects.all().delete()

        # incoming IVR
        msg = self.create_incoming_msg(contact, "Test Message", msg_type=Msg.TYPE_IVR)
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_IVR_TYPE, msg.created_on.date())
        msg.release()
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_IVR_TYPE, msg.created_on.date())

        ChannelCount.objects.all().delete()

        # outgoing ivr
        msg = self.create_outgoing_msg(contact, "Real Voice", msg_type=Msg.TYPE_IVR)
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_IVR_TYPE, msg.created_on.date())
        msg.release()
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_IVR_TYPE, msg.created_on.date())

        with patch("temba.channels.tasks.track") as mock:
            self.create_incoming_msg(contact, "Test Message")

            with self.assertNumQueries(6):
                track_org_channel_counts(now=timezone.now() + timedelta(days=1))
                self.assertEqual(2, mock.call_count)
                mock.assert_called_with(self.admin, "temba.ivr_outgoing", {"count": 1})


class ChannelLogTest(TembaTest):
    def test_views(self):
        self.channel.role = "CASR"
        self.channel.save(update_fields=("role",))

        other_org_channel = Channel.create(
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

        contact = self.create_contact("Fred Jones", phone="+12067799191")

        # create unrelated incoming message
        self.create_incoming_msg(contact, "incoming msg")

        # create sent outgoing message with success channel log
        success_msg = self.create_outgoing_msg(contact, "success message", status="D")
        success_log = ChannelLog.objects.create(
            channel=self.channel, msg=success_msg, description="Successfully Sent", is_error=False
        )
        success_log.response = ""
        success_log.request = "POST https://foo.bar/send?msg=failed+message"
        success_log.save(update_fields=["request", "response"])

        # create failed outgoing message with error channel log
        failed_msg = self.create_outgoing_msg(contact, "failed message")
        failed_log = ChannelLog.log_error(dict_to_struct("MockMsg", failed_msg.as_task_json()), "Error Sending")

        failed_log.response = json.dumps(dict(error="invalid credentials"))
        failed_log.request = "POST https://foo.bar/send?msg=failed+message"
        failed_log.save(update_fields=["request", "response"])

        # create call with an interaction log
        ivr_flow = self.get_flow("ivr")
        call = self.create_incoming_call(ivr_flow, contact)

        # create failed call with an interaction log
        self.create_incoming_call(ivr_flow, contact, status=IVRCall.STATUS_FAILED)

        # create log for other org
        other_org_contact = self.create_contact("Hans", phone="+593979123456")
        other_org_msg = self.create_outgoing_msg(other_org_contact, "hi", status="D")
        other_org_log = ChannelLog.objects.create(
            channel=other_org_channel, msg=other_org_msg, description="Successfully Sent", is_error=False
        )
        other_org_log.response = ""
        other_org_log.request = "POST https://foo.bar/send?msg=failed+message"
        other_org_log.save(update_fields=["request", "response"])

        # can't see the view without logging in
        list_url = reverse("channels.channellog_list", args=[self.channel.uuid])
        response = self.client.get(list_url)
        self.assertLoginRedirect(response)

        read_url = reverse("channels.channellog_read", args=[failed_log.id])
        response = self.client.get(read_url)
        self.assertLoginRedirect(response)

        # same if logged in as other admin
        self.login(self.admin2)

        list_url = reverse("channels.channellog_list", args=[self.channel.uuid])
        response = self.client.get(list_url)
        self.assertLoginRedirect(response)

        read_url = reverse("channels.channellog_read", args=[failed_log.id])
        response = self.client.get(read_url)
        self.assertLoginRedirect(response)

        # login as real admin
        self.login(self.admin)

        response = self.client.get(reverse("channels.channellog_list", args=["invalid-uuid"]))
        self.assertEqual(404, response.status_code)

        # check our list page has both our channel logs
        response = self.client.get(list_url)
        self.assertContains(response, "Successfully Sent")
        self.assertContains(response, "Error Sending")

        # check error logs only
        response = self.client.get(list_url + "?errors=1")
        self.assertNotContains(response, "Successfully Sent")
        self.assertContains(response, "Error Sending")

        # view failed alone
        response = self.client.get(read_url)
        self.assertContains(response, "failed+message")
        self.assertContains(response, "invalid credentials")

        # can't view log from other org
        response = self.client.get(reverse("channels.channellog_read", args=[other_org_log.id]))
        self.assertLoginRedirect(response)

        # disconnect our msg
        failed_log.msg = None
        failed_log.save(update_fields=["msg"])
        response = self.client.get(read_url)
        self.assertContains(response, "failed+message")
        self.assertContains(response, "invalid credentials")

        # view success alone
        response = self.client.get(reverse("channels.channellog_read", args=[success_log.id]))
        self.assertContains(response, "Successfully Sent")

        self.assertEqual(self.channel.get_success_log_count(), 2)
        self.assertEqual(self.channel.get_error_log_count(), 4)  # error log count always includes IVR logs

        # check that IVR logs are displayed correctly
        response = self.client.get(reverse("channels.channellog_list", args=[self.channel.uuid]) + "?connections=1")
        self.assertContains(response, "15 seconds")
        self.assertContains(response, "2 results")

        # make sure we can see the details of the IVR log
        response = self.client.get(reverse("channels.channellog_connection", args=[call.id]))
        self.assertContains(response, "{&quot;say&quot;: &quot;Hello&quot;}")

        # if duration isn't set explicitly, it can be calculated
        call.started_on = datetime(2019, 8, 12, 11, 4, 0, 0, timezone.utc)
        call.status = IVRCall.STATUS_IN_PROGRESS
        call.duration = None
        call.save(update_fields=("started_on", "status", "duration"))

        with patch("django.utils.timezone.now", return_value=datetime(2019, 8, 12, 11, 4, 30, 0, timezone.utc)):
            response = self.client.get(
                reverse("channels.channellog_list", args=[self.channel.uuid]) + "?connections=1"
            )
            self.assertContains(response, "30 seconds")

    def test_channellog_connection_anonymous(self):
        url = reverse("channels.channellog_connection", args=(1,))

        self.login(self.admin)
        response = self.client.get(url)

        self.assertTrue(response.status_code, 200)

        with AnonymousOrg(self.org):
            response = self.client.get(url)
            # admin has no access
            self.assertLoginRedirect(response)

        self.customer_support.is_staff = True
        self.customer_support.save()

        self.login(self.customer_support)

        with AnonymousOrg(self.org):
            response = self.client.get(url)
            # customer_support has access
            self.assertTrue(response.status_code, 200)

    def test_redaction_for_telegram(self):
        urn = "telegram:3527065"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("TG", "Test TG Channel", "234567")
        msg = self.create_incoming_msg(contact, "incoming msg", channel=channel)

        success_log = ChannelLog.objects.create(
            channel=channel,
            msg=msg,
            description="Successfully Sent",
            is_error=False,
            url=r"https://api.telegram.org/65474/sendMessage",
            method="POST",
            request="POST /65474/sendMessage HTTP/1.1\r\nHost: api.telegram.org\r\nUser-Agent: Courier/1.2.159\r\nContent-Length: 231\r\nContent-Type: application/x-www-form-urlencoded\r\nAccept-Encoding: gzip\r\n\r\nchat_id=3527065&reply_markup=%7B%22resize_keyboard%22%3Atrue%2C%22one_time_keyboard%22%3Atrue%2C%22keyboard%22%3A%5B%5B%7B%22text%22%3A%22blackjack%22%7D%2C%7B%22text%22%3A%22balance%22%7D%5D%5D%7D&text=Your+balance+is+now+%246.00.",
            response='HTTP/1.1 200 OK\r\nContent-Length: 298\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Expose-Headers: Content-Length,Content-Type,Date,Server,Connection\r\nConnection: keep-alive\r\nContent-Type: application/json\r\nDate: Tue, 11 Jun 2019 15:33:06 GMT\r\nServer: nginx/1.12.2\r\nStrict-Transport-Security: max-age=31536000; includeSubDomains; preload\r\n\r\n{"ok":true,"result":{"message_id":1440,"from":{"id":678777066,"is_bot":true,"first_name":"textit_staging","username":"textit_staging_bot"},"chat":{"id":3527065,"first_name":"Nic","last_name":"Pottier","username":"Nicpottier","type":"private"},"date":1560267186,"text":"Your balance is now $6.00."}}',
            response_status=200,
        )

        self.login(self.admin)

        list_url = reverse("channels.channellog_list", args=[channel.uuid])
        read_url = reverse("channels.channellog_read", args=[success_log.id])

        # check list page shows un-redacted content for a regular org
        response = self.client.get(list_url)

        self.assertContains(response, "3527065", count=1)

        # check list page shows redacted content for an anon org
        with AnonymousOrg(self.org):
            response = self.client.get(list_url)

            self.assertContains(response, "3527065", count=0)
            self.assertContains(response, ContactURN.ANON_MASK, count=1)

        # check read page shows un-redacted content for a regular org
        response = self.client.get(read_url)

        self.assertContains(response, "3527065", count=3)
        self.assertContains(response, "Nic", count=2)
        self.assertContains(response, "Pottier", count=1)

        # check read page shows redacted content for an anon org
        with AnonymousOrg(self.org):
            response = self.client.get(read_url)

            self.assertContains(response, "3527065", count=0)
            self.assertContains(response, "Nic", count=0)
            self.assertContains(response, "Pottier", count=0)
            self.assertContains(response, ContactURN.ANON_MASK, count=9)

        # login as customer support, must see URNs
        self.customer_support.is_staff = True
        self.customer_support.save()

        self.login(self.customer_support)

        read_url = reverse("channels.channellog_read", args=[success_log.id])
        response = self.client.get(read_url)

        self.assertContains(response, "3527065", count=3)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)
            # contact_urn is still masked on the read page, it uses contacts.models.Contact.get_display
            # Contact.get_display does not check if user has `contacts.contact_break_anon` permission
            self.assertContains(response, "3527065", count=2)
            self.assertContains(response, "Nic", count=2)
            self.assertContains(response, "Pottier", count=1)
            self.assertContains(response, ContactURN.ANON_MASK, count=1)

    def test_redaction_for_telegram_with_invalid_json(self):
        urn = "telegram:3527065"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("TG", "Test TG Channel", "234567")
        msg = self.create_incoming_msg(contact, "incoming msg", channel=channel)

        success_log = ChannelLog.objects.create(
            channel=channel,
            msg=msg,
            description="Successfully Sent",
            is_error=False,
            url=r"not important",
            method="POST",
            request=r"not important",
            response='Content-Type: application/json\r\n\r\n{"bad_json":true, "first_name": "Nic"',
            response_status=200,
        )

        self.login(self.admin)

        read_url = reverse("channels.channellog_read", args=[success_log.id])
        response = self.client.get(read_url)

        self.assertContains(response, "3527065", count=1)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)

            self.assertContains(response, "3527065", count=0)
            self.assertContains(response, "Nic", count=0)
            self.assertContains(response, "Pottier", count=0)

            # everything is masked
            self.assertContains(response, ContactURN.ANON_MASK, count=4)

        # login as customer support, must see URNs
        self.customer_support.is_staff = True
        self.customer_support.save()

        self.login(self.customer_support)

        read_url = reverse("channels.channellog_read", args=[success_log.id])
        response = self.client.get(read_url)

        self.assertContains(response, "3527065", count=1)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)
            self.assertContains(response, "Nic", count=1)

            self.assertContains(response, ContactURN.ANON_MASK, count=1)

    def test_redaction_for_telegram_when_no_match(self):
        urn = "telegram:3527065"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("TG", "Test TG Channel", "234567")
        msg = self.create_incoming_msg(contact, "incoming msg", channel=channel)

        success_log = ChannelLog.objects.create(
            channel=channel,
            msg=msg,
            description="Successfully Sent",
            is_error=False,
            url="There is no contact identifying information",
            method="POST",
            request='There is no contact identifying information\r\n\r\n{"json": "ok"}',
            response='There is no contact identifying information\r\n\r\n{"json": "ok"}',
            response_status=200,
        )

        self.login(self.admin)

        read_url = reverse("channels.channellog_read", args=[success_log.id])
        response = self.client.get(read_url)

        self.assertContains(response, "3527065", count=1)
        self.assertContains(response, "There is no contact identifying information", count=3)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)

            # url/request/reponse are masked
            self.assertContains(response, "There is no contact identifying information", count=0)

            self.assertContains(response, "3527065", count=0)
            self.assertContains(response, ContactURN.ANON_MASK, count=4)

        # login as customer support, must see URNs
        self.customer_support.is_staff = True
        self.customer_support.save()

        self.login(self.customer_support)

        response = self.client.get(read_url)

        self.assertContains(response, "3527065", count=1)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)
            self.assertContains(response, "There is no contact identifying information", count=3)

            self.assertContains(response, ContactURN.ANON_MASK, count=1)

    def test_redaction_for_twitter(self):
        urn = "twitterid:767659860"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("TWT", "Test TWT Channel", "nyaruka")
        msg = self.create_incoming_msg(contact, "incoming msg", channel=channel)

        success_log = ChannelLog.objects.create(
            channel=channel,
            msg=msg,
            description="Successfully Sent",
            is_error=False,
            url=r"https://textit.in/c/twt/5c70a767-f3dc-4a99-9323-4774f6432af5/receive",
            method="POST",
            request='POST /c/twt/5c70a767-f3dc-4a99-9323-4774f6432af5/receive HTTP/1.1\r\nHost: textit.in\r\nContent-Length: 1596\r\nContent-Type: application/json\r\nFinagle-Ctx-Com.twitter.finagle.deadline: 1560853608671000000 1560853611615000000\r\nFinagle-Ctx-Com.twitter.finagle.retries: 0\r\nFinagle-Http-Retryable-Request: \r\nX-Amzn-Trace-Id: Root=1-5d08bc68-de52174e83904d614a32a5c6\r\nX-B3-Flags: 2\r\nX-B3-Parentspanid: fe22fff79af84311\r\nX-B3-Sampled: false\r\nX-B3-Spanid: 86f3c3871ae31c2d\r\nX-B3-Traceid: fe22fff79af84311\r\nX-Forwarded-For: 199.16.157.173\r\nX-Forwarded-Port: 443\r\nX-Forwarded-Proto: https\r\nX-Twitter-Webhooks-Signature: sha256=CYVI5q7e7bzKufCD3GnZoJheSmjVRmNQo9uzO/gi4tA=\r\n\r\n{"for_user_id":"3753944237","direct_message_events":[{"type":"message_create","id":"1140928844112814089","created_timestamp":"1560853608526","message_create":{"target":{"recipient_id":"3753944237"},"sender_id":"767659860","message_data":{"text":"Briefly what will you be talking about and do you have any feature stories","entities":{"hashtags":[],"symbols":[],"user_mentions":[],"urls":[]}}}}],"users":{"767659860":{"id":"767659860","created_timestamp":"1345386861000","name":"Aaron Tumukunde","screen_name":"tumaaron","description":"Mathematics \u25a1 Media \u25a1 Real Estate \u25a1 And Jesus above all.","protected":false,"verified":false,"followers_count":167,"friends_count":485,"statuses_count":237,"profile_image_url":"http://pbs.twimg.com/profile_images/860380640029573120/HKuXgxR__normal.jpg","profile_image_url_https":"https://pbs.twimg.com/profile_images/860380640029573120/HKuXgxR__normal.jpg"},"3753944237":{"id":"3753944237","created_timestamp":"1443048916258","name":"Teheca","screen_name":"tehecaug","location":"Uganda","description":"We connect new mothers & parents to nurses for postnatal care. #Google LaunchPad Africa 2018, #UNFPA UpAccelerate 2017 #MasterCard Innovation exp 2017 #YCSUS18","url":"https://t.co/i0hcLRwEj7","protected":false,"verified":false,"followers_count":3369,"friends_count":4872,"statuses_count":1128,"profile_image_url":"http://pbs.twimg.com/profile_images/694638274204143616/Q4Mbg1tO_normal.png","profile_image_url_https":"https://pbs.twimg.com/profile_images/694638274204143616/Q4Mbg1tO_normal.png"}}}',
            response='HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\n{"message":"Message Accepted","data":[{"type":"msg","channel_uuid":"5c70a767-f3dc-4a99-9323-4774f6432af5","msg_uuid":"6c26277d-7002-4489-9b7f-998d4be5d0db","text":"Briefly what will you be talking about and do you have any feature stories","urn":"twitterid:767659860#tumaaron","external_id":"1140928844112814089","received_on":"2019-06-18T10:26:48.526Z"}]}',
            response_status=200,
        )

        self.login(self.admin)

        list_url = reverse("channels.channellog_list", args=[channel.uuid])
        read_url = reverse("channels.channellog_read", args=[success_log.id])

        response = self.client.get(list_url)

        self.assertContains(response, "767659860", count=1)

        with AnonymousOrg(self.org):
            response = self.client.get(list_url)

            self.assertContains(response, "767659860", count=0)
            self.assertContains(response, ContactURN.ANON_MASK, count=1)

        response = self.client.get(read_url)

        self.assertContains(response, "767659860", count=5)
        self.assertContains(response, "Aaron Tumukunde", count=1)
        self.assertContains(response, "tumaaron", count=2)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)

            self.assertContains(response, "767659860", count=0)
            self.assertContains(response, "Aaron Tumukunde", count=0)
            self.assertContains(response, "tumaaron", count=0)
            self.assertContains(response, ContactURN.ANON_MASK, count=14)

        # login as customer support, must see URNs
        self.customer_support.is_staff = True
        self.customer_support.save()

        self.login(self.customer_support)

        read_url = reverse("channels.channellog_read", args=[success_log.id])
        response = self.client.get(read_url)

        self.assertContains(response, "767659860", count=5)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)
            # contact_urn is still masked on the read page, it uses contacts.models.Contact.get_display
            # Contact.get_display does not check if user has `contacts.contact_break_anon` permission
            self.assertContains(response, "767659860", count=4)
            self.assertContains(response, "Aaron Tumukunde", count=1)
            self.assertContains(response, "tumaaron", count=2)

            self.assertContains(response, ContactURN.ANON_MASK, count=1)

    def test_redaction_for_twitter_when_no_match(self):
        urn = "twitterid:767659860"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("TWT", "Test TWT Channel", "nyaruka")
        msg = self.create_incoming_msg(contact, "incoming msg", channel=channel)

        success_log = ChannelLog.objects.create(
            channel=channel,
            msg=msg,
            description="Successfully Sent",
            is_error=False,
            url="There is no contact identifying information",
            method="POST",
            request=r"""There is no contact identifying information\r\n\r\n{"json": "ok"}""",
            response=r"""There is no contact identifying information\r\n\r\n{"json": "ok"}""",
            response_status=200,
        )

        self.login(self.admin)

        read_url = reverse("channels.channellog_read", args=[success_log.id])
        response = self.client.get(read_url)

        self.assertContains(response, "767659860", count=1)
        self.assertContains(response, "There is no contact identifying information", count=3)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)

            # url/request/reponse are masked
            self.assertContains(response, "There is no contact identifying information", count=0)

            self.assertContains(response, "767659860", count=0)
            self.assertContains(response, ContactURN.ANON_MASK, count=4)

        # login as customer support, must see URNs
        self.customer_support.is_staff = True
        self.customer_support.save()

        self.login(self.customer_support)

        response = self.client.get(read_url)

        self.assertContains(response, "767659860", count=1)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)
            self.assertContains(response, "There is no contact identifying information", count=3)

            self.assertContains(response, ContactURN.ANON_MASK, count=1)

    def test_redaction_for_facebook(self):
        urn = "facebook:2150393045080607"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("FB", "Test FB Channel", "54764868534")
        msg = self.create_incoming_msg(contact, "incoming msg", channel=channel)

        success_log = ChannelLog.objects.create(
            channel=channel,
            msg=msg,
            description="Successfully Sent",
            is_error=False,
            url=f"https://textit.in/c/fb/{channel.uuid}/receive",
            method="POST",
            request="""POST /c/fb/d1117754-f2ab-4348-9572-996ddc1959a8/receive HTTP/1.1\r\nHost: textit.in\r\nAccept: */*\r\nAccept-Encoding: deflate, gzip\r\nContent-Length: 314\r\nContent-Type: application/json\r\n\r\n{"object":"page","entry":[{"id":"311494332880244","time":1559102364444,"messaging":[{"sender":{"id":"2150393045080607"},"recipient":{"id":"311494332880244"},"timestamp":1559102363925,"message":{"mid":"ld5jgfQP8TLBX9FFc3AETshZgE6Zn5UjpY3vY00t3A_YYC2AYDM3quxaodTiHj7nK6lI_ds4WFUJlTmM2l5xoA","seq":0,"text":"hi"}}]}]}""",
            response="""HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Type: application/json\r\n\r\n{"message":"Events Handled","data":[{"type":"msg","channel_uuid":"d1117754-f2ab-4348-9572-996ddc1959a8","msg_uuid":"55a3387b-f97e-4270-8157-7ba781a86411","text":"hi","urn":"facebook:2150393045080607","external_id":"ld5jgfQP8TLBX9FFc3AETshZgE6Zn5UjpY3vY00t3A_YYC2AYDM3quxaodTiHj7nK6lI_ds4WFUJlTmM2l5xoA","received_on":"2019-05-29T03:59:23.925Z"}]}""",
            response_status=200,
        )

        self.login(self.admin)

        list_url = reverse("channels.channellog_list", args=[channel.uuid])
        response = self.client.get(list_url)

        self.assertContains(response, "2150393045080607", count=1)
        self.assertContains(response, "facebook:2150393045080607", count=0)

        with AnonymousOrg(self.org):
            response = self.client.get(list_url)

            self.assertContains(response, "2150393045080607", count=0)
            self.assertContains(response, "facebook:2150393045080607", count=0)
            self.assertContains(response, ContactURN.ANON_MASK, count=1)

        read_url = reverse("channels.channellog_read", args=[success_log.id])

        response = self.client.get(read_url)

        self.assertContains(response, "2150393045080607", count=3)
        self.assertContains(response, "facebook:2150393045080607", count=1)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)

            self.assertContains(response, "2150393045080607", count=0)
            self.assertContains(response, "facebook:", count=1)

            self.assertContains(response, ContactURN.ANON_MASK, count=4)

        # login as customer support, must see URNs
        self.customer_support.is_staff = True
        self.customer_support.save()

        self.login(self.customer_support)

        read_url = reverse("channels.channellog_read", args=[success_log.id])

        response = self.client.get(read_url)

        self.assertContains(response, "2150393045080607", count=3)
        self.assertContains(response, "facebook:2150393045080607", count=1)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)
            # contact_urn is still masked on the read page, it uses contacts.models.Contact.get_display
            # Contact.get_display does not check if user has `contacts.contact_break_anon` permission
            self.assertContains(response, "2150393045080607", count=2)
            self.assertContains(response, "facebook:", count=1)

            self.assertContains(response, ContactURN.ANON_MASK, count=1)

    def test_redaction_for_facebook_when_no_match(self):
        # in this case we are paranoid and mask everything
        urn = "facebook:2150393045080607"
        contact = self.create_contact("Fred Jones", urns=[urn])
        channel = self.create_channel("FB", "Test FB Channel", "54764868534")
        msg = self.create_incoming_msg(contact, "incoming msg", channel=channel)

        success_log = ChannelLog.objects.create(
            channel=channel,
            msg=msg,
            description="Successfully Sent",
            is_error=False,
            url="There is no contact identifying information",
            method="POST",
            request="""There is no contact identifying information""",
            response="""There is no contact identifying information""",
            response_status=200,
        )

        self.login(self.admin)

        list_url = reverse("channels.channellog_list", args=[channel.uuid])
        response = self.client.get(list_url)

        self.assertContains(response, "2150393045080607", count=1)

        with AnonymousOrg(self.org):
            response = self.client.get(list_url)

            self.assertContains(response, ContactURN.ANON_MASK, count=1)

        read_url = reverse("channels.channellog_read", args=[success_log.id])

        response = self.client.get(read_url)

        self.assertContains(response, "2150393045080607", count=1)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)

            # url/request/reponse are masked
            self.assertContains(response, "There is no contact identifying information", count=0)

            self.assertContains(response, "2150393045080607", count=0)
            self.assertContains(response, ContactURN.ANON_MASK, count=4)

        # login as customer support, must see URNs
        self.customer_support.is_staff = True
        self.customer_support.save()

        self.login(self.customer_support)

        response = self.client.get(read_url)

        self.assertContains(response, "2150393045080607", count=1)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)
            self.assertContains(response, "There is no contact identifying information", count=3)

            # contact_urn is still masked on the read page, it uses contacts.models.Contact.get_display
            # Contact.get_display does not check if user has `contacts.contact_break_anon` permission
            self.assertContains(response, ContactURN.ANON_MASK, count=1)

    def test_redaction_for_twilio(self):
        contact = self.create_contact("Fred Jones", phone="+593979099111")
        channel = self.create_channel("T", "Test Twilio Channel", "+12345")
        msg = self.create_outgoing_msg(contact, "Hi")

        success_log = ChannelLog.objects.create(
            channel=channel,
            msg=msg,
            description="Status Updated",
            is_error=False,
            url="https://textit.in/c/t/1234-5678/status?id=2466753&action=callback",
            method="POST",
            request="POST /c/t/1234-5678/status?id=86598533&action=callback HTTP/1.1\r\nHost: textit.in\r\nAccept: */*\r\nAccept-Encoding: gzip,deflate\r\nCache-Control: max-age=259200\r\nContent-Length: 237\r\nContent-Type: application/x-www-form-urlencoded; charset=utf-8\r\nUser-Agent: TwilioProxy/1.1\r\nX-Amzn-Trace-Id: Root=1-5d5a10b2-8c8b96c86d45a9c6bdc5f43c\r\nX-Forwarded-For: 54.210.179.19\r\nX-Forwarded-Port: 443\r\nX-Forwarded-Proto: https\r\nX-Twilio-Signature: sdgreh54hehrghssghh55=\r\n\r\nSmsSid=SM357343637&SmsStatus=delivered&MessageStatus=delivered&To=%2B593979099111&MessageSid=SM357343637&AccountSid=AC865965965&From=%2B253262278&ApiVersion=2010-04-01&ToCity=Quito&ToCountry=EC",
            response='{"message":"Status Update Accepted","data":[{"type":"status","channel_uuid":"1234-5678","status":"D","msg_id":2466753}]}\n',
            response_status=200,
        )

        self.login(self.admin)

        list_url = reverse("channels.channellog_list", args=[channel.uuid])
        read_url = reverse("channels.channellog_read", args=[success_log.id])

        # check list page shows un-redacted content for a regular org
        response = self.client.get(list_url)

        self.assertContains(response, "097 909 9111", count=1)

        # check list page shows redacted content for an anon org
        with AnonymousOrg(self.org):
            response = self.client.get(list_url)

            self.assertContains(response, "097 909 9111", count=0)
            self.assertContains(response, "979099111", count=0)
            self.assertContains(response, "Quito", count=0)
            self.assertContains(response, ContactURN.ANON_MASK, count=1)

        # check read page shows un-redacted content for a regular org
        response = self.client.get(read_url)

        self.assertContains(response, "097 909 9111", count=1)
        self.assertContains(response, "979099111", count=1)
        self.assertContains(response, "Quito", count=1)

        # check read page shows redacted content for an anon org
        with AnonymousOrg(self.org):
            response = self.client.get(read_url)

            self.assertContains(response, "097 909 9111", count=0)
            self.assertContains(response, "979099111", count=0)
            self.assertContains(response, "Quito", count=0)
            self.assertContains(response, ContactURN.ANON_MASK, count=5)

        # login as customer support, must see URNs
        self.customer_support.is_staff = True
        self.customer_support.save()

        self.login(self.customer_support)

        read_url = reverse("channels.channellog_read", args=[success_log.id])
        response = self.client.get(read_url)

        self.assertContains(response, "097 909 9111", count=1)
        self.assertContains(response, "979099111", count=1)
        self.assertContains(response, "Quito", count=1)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)
            # contact_urn is still masked on the read page, it uses contacts.models.Contact.get_display
            # Contact.get_display does not check if user has `contacts.contact_break_anon` permission
            self.assertContains(response, "097 909 9111", count=0)
            self.assertContains(response, "979099111", count=1)
            self.assertContains(response, "Quito", count=1)
            self.assertContains(response, ContactURN.ANON_MASK, count=1)

    def test_channellog_anonymous_org_no_msg(self):
        tw_urn = "15128505839"

        tw_channel = self.create_channel("TW", "Test TW Channel", "+12345")

        failed_log = ChannelLog.objects.create(
            channel=tw_channel,
            msg=None,
            description="Channel Error",
            is_error=True,
            url=f"https://textit.in/c/tw/{tw_channel.uuid}/status?action=callback&id=58027120",
            method="POST",
            request="""
POST /c/tw/8388f8cd-658f-4fae-925e-ee0792588e68/status?action=callback&id=58027120 HTTP/1.1
Host: textit.in
Accept: */*
Accept-Encoding: gzip;q=1.0,deflate;q=0.6,identity;q=0.3
Content-Length: 343
Content-Type: application/x-www-form-urlencoded
User-Agent: SignalwireCallback/1.0

MessageSid=e1d12194-a643-4007-834a-5900db47e262&SmsSid=e1d12194-a643-4007-834a-5900db47e262&AccountSid=<redacted>&From=%2B15618981512&To=%2B15128505839&Body=Hi+Ben+Google+Voice%2C+Did+you+enjoy+your+stay+at+White+Bay+Villas%3F++Answer+with+Yes+or+No.+reply+STOP+to+opt-out.&NumMedia=0&NumSegments=1&MessageStatus=sent""",
            response="""
HTTP/1.1 400 Bad Request
Content-Encoding: gzip
Content-Type: application/json

{"message":"Error","data":[{"type":"error","error":"missing request signature"}]}


Error: missing request signature""",
            response_status=400,
        )

        self.login(self.admin)

        read_url = reverse("channels.channellog_read", args=[failed_log.id])

        response = self.client.get(read_url)

        # non anon user can see contact identifying data (in the request)
        self.assertContains(response, tw_urn, count=1)

        with AnonymousOrg(self.org):
            response = self.client.get(read_url)

            self.assertContains(response, tw_urn, count=0)

            # when we can't identify the contact, url, request and response objects are completely masked
            self.assertContains(response, ContactURN.ANON_MASK, count=3)

    def test_trim_task(self):
        contact = self.create_contact("Fred Jones", phone="12345")
        msg = self.create_incoming_msg(contact, "incoming msg", channel=self.channel)

        ChannelLog.objects.create(
            channel=self.channel,
            msg=msg,
            description="Successfully Sent",
            is_error=False,
            url="htpp://example.com",
            method="POST",
            request='{"json": "ok"}',
            response='{"json": "ok"}',
            response_status=200,
            created_on=timezone.now() - timedelta(days=7),
        )
        l2 = ChannelLog.objects.create(
            channel=self.channel,
            msg=msg,
            description="Successfully Sent",
            is_error=False,
            url="htpp://example.com",
            method="POST",
            request='{"json": "ok"}',
            response='{"json": "ok"}',
            response_status=200,
            created_on=timezone.now() - timedelta(days=2),
        )

        trim_channel_log_task()

        # should only have one log remaining and should be l2
        self.assertEqual(1, ChannelLog.objects.all().count())
        self.assertTrue(ChannelLog.objects.filter(id=l2.id))


class FacebookWhitelistTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.channel.delete()
        self.channel = Channel.create(
            self.org,
            self.user,
            None,
            "FB",
            None,
            "1234",
            config={Channel.CONFIG_AUTH_TOKEN: "auth"},
            uuid="00000000-0000-0000-0000-000000001234",
        )

    def test_whitelist(self):
        whitelist_url = reverse("channels.channel_facebook_whitelist", args=[self.channel.uuid])
        response = self.client.get(whitelist_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)
        response = self.client.get(reverse("channels.channel_read", args=[self.channel.uuid]))

        self.assertContains(response, whitelist_url)

        with patch("requests.post") as mock:
            mock.return_value = MockResponse(400, '{"error": { "message": "FB Error" } }')
            response = self.client.post(whitelist_url, dict(whitelisted_domain="https://foo.bar"))
            self.assertFormError(response, "form", None, "FB Error")

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
    @override_settings(SEND_MESSAGES=True)
    def test_queue_to_courier(self):
        self.channel.channel_type = "T"
        self.channel.save()

        bob = self.create_contact("Bob", urns=["tel:+12065551111"])
        cat = self.create_contact("Cat", urns=["tel:+12065552222"])
        dan = self.create_contact("Dan", urns=["tel:+12065553333"])
        eve = self.create_contact("eve", urns=["tel:+12065554444"])
        incoming = self.create_incoming_msg(bob, "Hello", external_id="external-id")

        # create some outgoing messages for our channel
        msg1 = self.create_outgoing_msg(
            bob,
            "Outgoing 1",
            attachments=["image/jpg:https://example.com/test.jpg", "image/jpg:https://example.com/test2.jpg"],
        )
        msg2 = self.create_outgoing_msg(cat, "Outgoing 2", response_to=incoming, attachments=[])
        msg3 = self.create_outgoing_msg(dan, "Outgoing 3", high_priority=False, attachments=None)
        msg4 = self.create_outgoing_msg(eve, "Outgoing 4", high_priority=True)
        msg5 = self.create_outgoing_msg(eve, "Outgoing 5", high_priority=True)
        all_msgs = [msg1, msg2, msg3, msg4, msg5]

        Msg.send_messages(all_msgs)

        # we should have been queued to our courier queues and our msgs should be marked as such
        for msg in all_msgs:
            msg.refresh_from_db()
            self.assertEqual(msg.status, Msg.STATUS_QUEUED)

        self.assertFalse(msg1.high_priority)

        # responses arent enough to be high priority, it depends on run responded
        self.assertFalse(msg2.high_priority)

        self.assertFalse(msg3.high_priority)
        self.assertTrue(msg4.high_priority)  # explicitly high
        self.assertTrue(msg5.high_priority)

        # check against redis
        r = get_redis_connection()

        # should have our channel in the active queue
        queue_name = "msgs:" + self.channel.uuid + "|10"
        self.assertEqual(1, r.zcard("msgs:active"))
        self.assertEqual(0, r.zrank("msgs:active", queue_name))

        # check that messages went into the correct queues
        high_priority_msgs = [json.loads(force_text(t)) for t in r.zrange(queue_name + "/1", 0, -1)]
        low_priority_msgs = [json.loads(force_text(t)) for t in r.zrange(queue_name + "/0", 0, -1)]

        self.assertEqual([[m["text"] for m in b] for b in high_priority_msgs], [["Outgoing 4", "Outgoing 5"]])
        self.assertEqual(
            [[m["text"] for m in b] for b in low_priority_msgs], [["Outgoing 1"], ["Outgoing 2"], ["Outgoing 3"]]
        )

        self.assertEqual(
            low_priority_msgs[0][0]["attachments"],
            ["image/jpg:https://example.com/test.jpg", "image/jpg:https://example.com/test2.jpg"],
        )
        self.assertEqual(2, low_priority_msgs[0][0]["tps_cost"])
        self.assertEqual([], low_priority_msgs[1][0]["attachments"])
        self.assertEqual(1, low_priority_msgs[1][0]["tps_cost"])
        self.assertEqual("external-id", low_priority_msgs[1][0]["response_to_external_id"])
        self.assertIsNone(low_priority_msgs[2][0]["attachments"])

    def test_courier_urls(self):
        response = self.client.get(reverse("courier.t", args=[self.channel.uuid, "receive"]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b"this URL should be mapped to a Courier instance")
