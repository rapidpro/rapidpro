# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import base64
import calendar
import copy
import hashlib
import hmac
import json

import iso8601
import pytz
import six
import time

from django.utils.encoding import force_text, force_bytes
from six.moves.urllib.parse import quote, urlencode
import uuid

from datetime import timedelta, date, datetime
from django.conf import settings
from django.contrib.auth.models import User, Group
from django.core import mail
from django.core.cache import cache
from django.core.urlresolvers import reverse
from django.test import RequestFactory
from django.test.utils import override_settings
from django.utils import timezone
from django.template import loader
from django_redis import get_redis_connection
from mock import patch
from smartmin.tests import SmartminTest
from temba.api.models import WebHookEvent
from temba.contacts.models import Contact, ContactGroup, ContactURN, URN, TEL_SCHEME, TWITTER_SCHEME, EXTERNAL_SCHEME, \
    LINE_SCHEME, JIOCHAT_SCHEME
from temba.flows.models import FlowRun
from temba.msgs.models import Broadcast, Msg, IVR, WIRED, FAILED, SENT, DELIVERED, ERRORED, INCOMING, PENDING, QUEUED, \
    HANDLER_QUEUE, HANDLE_EVENT_TASK
from temba.channels.views import channel_status_processor
from temba.contacts.models import TELEGRAM_SCHEME, FACEBOOK_SCHEME, VIBER_SCHEME, FCM_SCHEME
from temba.ivr.models import IVRCall
from temba.msgs.models import MSG_SENT_KEY, SystemLabel
from temba.orgs.models import Org, ALL_EVENTS, ACCOUNT_SID, ACCOUNT_TOKEN, APPLICATION_SID, NEXMO_KEY, NEXMO_SECRET, FREE_PLAN, NEXMO_UUID, \
    NEXMO_APP_ID, NEXMO_APP_PRIVATE_KEY
from temba.tests import TembaTest, MockResponse, AnonymousOrg
from temba.tests.twilio import MockTwilioClient, MockRequestValidator
from temba.triggers.models import Trigger
from temba.utils import dict_to_struct, get_anonymous_user
from temba.utils.dates import datetime_to_str, datetime_to_ms, ms_to_datetime
from temba.utils.http import http_headers
from temba.utils.jiochat import JiochatClient
from temba.utils.twitter import generate_twitter_signature
from temba.utils.queues import push_task
from twilio import TwilioRestException
from twilio.util import RequestValidator
from twython import TwythonError
from xml.etree import ElementTree as ET


from .models import Channel, ChannelCount, ChannelEvent, SyncEvent, Alert, ChannelLog, ChannelSession, CHANNEL_EVENT
from .models import DART_MEDIA_ENDPOINT, HUB9_ENDPOINT
from .tasks import check_channels_task, squash_channelcounts, refresh_jiochat_access_tokens


class ChannelTest(TembaTest):

    def setUp(self):
        super(ChannelTest, self).setUp()

        self.channel.delete()

        self.tel_channel = Channel.create(self.org, self.user, 'RW', 'A', name="Test Channel", address="+250785551212",
                                          role="SR", secret="12345", gcm_id="123")

        self.twitter_channel = Channel.create(self.org, self.user, None, 'TT', name="Twitter Channel",
                                              address="billy_bob", role="SR")

        self.unclaimed_channel = Channel.create(None, self.user, None, 'NX', name="Unclaimed Channel", address=None,
                                                secret=None, gcm_id="000")

        self.ussd_channel = Channel.create(self.org, self.user, None, 'JNU', name="Junebug USSD",
                                           address="*123#", role=Channel.ROLE_USSD)

    def send_message(self, numbers, message, org=None, user=None):
        if not org:
            org = self.org

        if not user:
            user = self.user

        group = ContactGroup.get_or_create(org, user, 'Numbers: %s' % ','.join(numbers))
        contacts = list()
        for number in numbers:
            contact, urn_obj = Contact.get_or_create(org, URN.from_tel(number), user=user, name=None)
            contacts.append(contact)

        group.contacts.add(*contacts)

        broadcast = Broadcast.create(org, user, message, [group])
        broadcast.send()

        msg = Msg.objects.filter(broadcast=broadcast).order_by('text', 'pk')
        if len(numbers) == 1:
            return msg.first()
        else:
            return list(msg)

    def assertHasCommand(self, cmd_name, response):
        self.assertEqual(200, response.status_code)
        data = response.json()

        for cmd in data['cmds']:
            if cmd['cmd'] == cmd_name:
                return

        raise Exception("Did not find '%s' cmd in response: '%s'" % (cmd_name, response.content))

    def test_expressions_context(self):
        context = self.tel_channel.build_expressions_context()
        self.assertEqual(context['__default__'], '+250 785 551 212')
        self.assertEqual(context['name'], 'Test Channel')
        self.assertEqual(context['address'], '+250 785 551 212')
        self.assertEqual(context['tel'], '+250 785 551 212')
        self.assertEqual(context['tel_e164'], '+250785551212')

        context = self.twitter_channel.build_expressions_context()
        self.assertEqual(context['__default__'], '@billy_bob')
        self.assertEqual(context['name'], 'Twitter Channel')
        self.assertEqual(context['address'], '@billy_bob')
        self.assertEqual(context['tel'], '')
        self.assertEqual(context['tel_e164'], '')

        context = self.unclaimed_channel.build_expressions_context()
        self.assertEqual(context['__default__'], 'Unclaimed Channel')
        self.assertEqual(context['name'], 'Unclaimed Channel')
        self.assertEqual(context['address'], '')
        self.assertEqual(context['tel'], '')
        self.assertEqual(context['tel_e164'], '')

    def test_deactivate(self):
        self.login(self.admin)
        self.tel_channel.is_active = False
        self.tel_channel.save()
        response = self.client.get(reverse('channels.channel_read', args=[self.tel_channel.uuid]))
        self.assertEqual(404, response.status_code)

    def test_channellog_links(self):
        self.login(self.admin)

        channel_types = (
            ('JN', Channel.DEFAULT_ROLE, 'Channel Log'),
            ('JNU', Channel.ROLE_USSD, 'USSD Log'),
            ('T', Channel.ROLE_CALL, 'Call Log'),
            ('T', Channel.ROLE_SEND + Channel.ROLE_CALL, 'Channel Log')
        )

        for channel_type, channel_role, link_text in channel_types:
            channel = Channel.create(self.org, self.user, None, channel_type, name="Test Channel", role=channel_role)
            response = self.client.get(reverse('channels.channel_read', args=[channel.uuid]))
            self.assertContains(response, link_text)

    def test_delegate_channels(self):

        self.login(self.admin)

        # we don't support IVR yet
        self.assertFalse(self.org.supports_ivr())

        # pretend we are connected to twiliko
        self.org.config = dict(ACCOUNT_SID='AccountSid', ACCOUNT_TOKEN='AccountToken', APPLICATION_SID='AppSid')
        self.org.save()

        # add a delegate caller
        post_data = dict(channel=self.tel_channel.pk, connection='T')
        response = self.client.post(reverse('channels.channel_create_caller'), post_data)

        # now we should be IVR capable
        self.assertTrue(self.org.supports_ivr())

        # should now have the option to disable
        self.login(self.admin)
        response = self.client.get(reverse('channels.channel_read', args=[self.tel_channel.uuid]))
        self.assertContains(response, 'Disable Voice Calls')

        # try adding a caller for an invalid channel
        response = self.client.post('%s?channel=20000' % reverse('channels.channel_create_caller'))
        self.assertEqual(200, response.status_code)
        self.assertEqual('Sorry, a caller cannot be added for that number', response.context['form'].errors['channel'][0])

        # disable our twilio connection
        self.org.remove_twilio_account(self.admin)
        self.assertFalse(self.org.supports_ivr())

        # we should lose our caller
        response = self.client.get(reverse('channels.channel_read', args=[self.tel_channel.uuid]))
        self.assertNotContains(response, 'Disable Voice Calls')

        # now try and add it back without a twilio connection
        response = self.client.post(reverse('channels.channel_create_caller'), post_data)

        # shouldn't have added, so no ivr yet
        self.assertFalse(self.assertFalse(self.org.supports_ivr()))

        self.assertEqual('A connection to a Twilio account is required', response.context['form'].errors['connection'][0])

    def test_get_channel_type_name(self):
        self.assertEqual(self.tel_channel.get_channel_type_name(), "Android Phone")
        self.assertEqual(self.twitter_channel.get_channel_type_name(), "Twitter Channel")
        self.assertEqual(self.unclaimed_channel.get_channel_type_name(), "Nexmo Channel")

    def test_channel_selection(self):
        # make our default tel channel MTN
        mtn = self.tel_channel
        mtn.name = "MTN"
        mtn.save()

        # create a channel for Tigo too
        tigo = Channel.create(self.org, self.user, 'RW', 'A', "Tigo", "+250725551212", secret="11111", gcm_id="456")

        # new contact on MTN should send with the MTN channel
        msg = self.send_message(['+250788382382'], "Sent to an MTN number")
        self.assertEqual(mtn, self.org.get_send_channel(contact_urn=msg.contact_urn))
        self.assertEqual(mtn, msg.channel)

        # new contact on Tigo should send with the Tigo channel
        msg = self.send_message(['+250728382382'], "Sent to a Tigo number")
        self.assertEqual(tigo, self.org.get_send_channel(contact_urn=msg.contact_urn))
        self.assertEqual(tigo, msg.channel)

        # now our MTN contact texts, the tigo number which should change their affinity
        msg = Msg.create_incoming(tigo, "tel:+250788382382", "Send an inbound message to Tigo")
        self.assertEqual(tigo, msg.channel)
        self.assertEqual(tigo, self.org.get_send_channel(contact_urn=msg.contact_urn))
        self.assertEqual(tigo, ContactURN.objects.get(path='+250788382382').channel)

        # new contact on Airtel (some overlap) should send with the Tigo channel since it is newest
        msg = self.send_message(['+250738382382'], "Sent to a Airtel number")
        self.assertEqual(tigo, self.org.get_send_channel(contact_urn=msg.contact_urn))
        self.assertEqual(tigo, msg.channel)

        # add a voice caller
        caller = Channel.add_call_channel(self.org, self.user, self.tel_channel)

        # set our affinity to the caller (ie, they were on an ivr call)
        ContactURN.objects.filter(path='+250788382382').update(channel=caller)
        self.assertEqual(mtn, self.org.get_send_channel(contact_urn=ContactURN.objects.get(path='+250788382382')))

        # change channel numbers to be shortcodes, i.e. no overlap with contact numbers
        mtn.address = '1234'
        mtn.save()
        tigo.address = '1235'
        tigo.save()

        self.org.clear_cached_channels()

        # should return the newest channel which is TIGO
        msg = self.send_message(['+250788382382'], "Sent to an MTN number, but with shortcode channels")
        self.assertEqual(tigo, msg.channel)
        self.assertEqual(tigo, self.org.get_send_channel(contact_urn=msg.contact_urn))

        # if we have prefixes matching set should honor those
        mtn.config = {Channel.CONFIG_SHORTCODE_MATCHING_PREFIXES: ['25078', '25072']}
        mtn.save()

        self.org.clear_cached_channels()

        msg = self.send_message(['+250788382382'], "Sent to an MTN number with shortcode channels and prefixes set")
        self.assertEqual(mtn, msg.channel)
        self.assertEqual(mtn, self.org.get_send_channel(contact_urn=msg.contact_urn))

        msg = self.send_message(['+250728382382'], "Sent to a TIGO number with shortcode channels and prefixes set")
        self.assertEqual(mtn, msg.channel)
        self.assertEqual(mtn, self.org.get_send_channel(contact_urn=msg.contact_urn))

        # check for twitter
        self.assertEqual(self.twitter_channel, self.org.get_send_channel(scheme=TWITTER_SCHEME))

        contact = self.create_contact("Billy", number="+250722222222", twitter="billy_bob")
        twitter_urn = contact.get_urn(schemes=[TWITTER_SCHEME])
        self.assertEqual(self.twitter_channel, self.org.get_send_channel(contact_urn=twitter_urn))

    def test_message_splitting(self):
        # external API requires messages to be <= 160 chars
        self.tel_channel.channel_type = 'EX'
        self.tel_channel.save()

        msg = Msg.create_outgoing(self.org, self.user, 'tel:+250738382382', 'x' * 400)  # 400 chars long
        Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
        self.assertEqual(3, Msg.objects.get(pk=msg.id).msg_count)

        # Nexmo limit is 1600
        self.tel_channel.channel_type = 'NX'
        self.tel_channel.save()
        cache.clear()  # clear the channel from cache

        msg = Msg.create_outgoing(self.org, self.user, 'tel:+250738382382', 'y' * 400)
        Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
        self.assertEqual(self.tel_channel, Msg.objects.get(pk=msg.id).channel)
        self.assertEqual(1, Msg.objects.get(pk=msg.id).msg_count)

    def test_ensure_normalization(self):
        self.tel_channel.country = 'RW'
        self.tel_channel.save()

        contact1 = self.create_contact("contact1", "0788111222")
        contact2 = self.create_contact("contact2", "+250788333444")
        contact3 = self.create_contact("contact3", "+18006927753")

        self.org.normalize_contact_tels()

        norm_c1 = Contact.objects.get(pk=contact1.pk)
        norm_c2 = Contact.objects.get(pk=contact2.pk)
        norm_c3 = Contact.objects.get(pk=contact3.pk)

        self.assertEqual(norm_c1.get_urn(TEL_SCHEME).path, "+250788111222")
        self.assertEqual(norm_c2.get_urn(TEL_SCHEME).path, "+250788333444")
        self.assertEqual(norm_c3.get_urn(TEL_SCHEME).path, "+18006927753")

    def test_channel_create(self):

        # can't use an invalid scheme for a fixed-scheme channel type
        with self.assertRaises(ValueError):
            Channel.create(self.org, self.user, 'KE', 'AT', None, '+250788123123',
                           config=dict(username='at-user', api_key='africa-key'),
                           uuid='00000000-0000-0000-0000-000000001234',
                           schemes=['fb'])

        # a scheme is required
        with self.assertRaises(ValueError):
            Channel.create(self.org, self.user, 'US', 'EX', None, '+12065551212',
                           uuid='00000000-0000-0000-0000-000000001234',
                           schemes=[])

        # country channels can't have scheme
        with self.assertRaises(ValueError):
            Channel.create(self.org, self.user, 'US', 'EX', None, '+12065551212',
                           uuid='00000000-0000-0000-0000-000000001234',
                           schemes=['fb'])

    def test_delete(self):
        self.org.administrators.add(self.user)
        self.user.set_org(self.org)
        self.login(self.user)

        # a message, a call, and a broadcast
        msg = self.send_message(['250788382382'], "How is it going?")
        call = ChannelEvent.create(self.tel_channel, "tel:+250788383385", ChannelEvent.TYPE_CALL_IN, timezone.now(), {})

        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.tel_channel, msg.channel)
        self.assertEqual(1, Msg.get_messages(self.org).count())
        self.assertEqual(1, ChannelEvent.get_all(self.org).count())
        self.assertEqual(1, Broadcast.get_broadcasts(self.org).count())

        # start off in the pending state
        self.assertEqual('P', msg.status)

        response = self.fetch_protected(reverse('channels.channel_delete', args=[self.tel_channel.pk]), self.user)
        self.assertContains(response, 'Test Channel')

        response = self.fetch_protected(reverse('channels.channel_delete', args=[self.tel_channel.pk]),
                                        post_data=dict(remove=True), user=self.user)
        self.assertRedirect(response, reverse("orgs.org_home"))

        msg = Msg.objects.get(pk=msg.pk)
        self.assertIsNotNone(msg.channel)
        self.assertFalse(msg.channel.is_active)
        self.assertEqual(self.org, msg.org)

        # queued messages for the channel should get marked as failed
        self.assertEqual('F', msg.status)

        call = ChannelEvent.objects.get(pk=call.pk)
        self.assertIsNotNone(call.channel)
        self.assertFalse(call.channel.is_active)

        self.assertEqual(self.org, call.org)

        broadcast = Broadcast.objects.get(pk=msg.broadcast.pk)
        self.assertEqual(self.org, broadcast.org)

        # should still be considered that user's message, call and broadcast
        self.assertEqual(1, Msg.get_messages(self.org).count())
        self.assertEqual(1, ChannelEvent.get_all(self.org).count())
        self.assertEqual(1, Broadcast.get_broadcasts(self.org).count())

        # syncing this channel should result in a release
        post_data = dict(cmds=[dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="60", net="UMTS", pending=[], retry=[])])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # our response should contain a release
        self.assertHasCommand('rel', response)

        # create a channel
        channel = Channel.create(self.org, self.user, 'RW', 'A', "Test Channel", "0785551212",
                                 secret=Channel.generate_secret(), gcm_id="123")

        response = self.fetch_protected(reverse('channels.channel_delete', args=[channel.pk]), self.superuser)
        self.assertContains(response, 'Test Channel')

        response = self.fetch_protected(reverse('channels.channel_delete', args=[channel.pk]),
                                        post_data=dict(remove=True), user=self.superuser)
        self.assertRedirect(response, reverse("orgs.org_home"))

        # create a channel
        channel = Channel.create(self.org, self.user, 'RW', 'A', "Test Channel", "0785551212",
                                 secret=Channel.generate_secret(), gcm_id="123")

        # add channel trigger
        from temba.triggers.models import Trigger
        Trigger.objects.create(org=self.org, flow=self.create_flow(), channel=channel,
                               modified_by=self.admin, created_by=self.admin)

        self.assertTrue(Trigger.objects.filter(channel=channel, is_active=True))

        response = self.fetch_protected(reverse('channels.channel_delete', args=[channel.pk]),
                                        post_data=dict(remove=True), user=self.superuser)

        self.assertRedirect(response, reverse("orgs.org_home"))

        # channel trigger should have be removed
        self.assertFalse(Trigger.objects.filter(channel=channel, is_active=True))

    def test_list(self):
        # de-activate existing channels
        Channel.objects.all().update(is_active=False)

        # list page redirects to claim page
        self.login(self.user)
        response = self.client.get(reverse('channels.channel_list'))
        self.assertRedirect(response, reverse('channels.channel_claim'))

        # unless you're a superuser
        self.login(self.superuser)
        response = self.client.get(reverse('channels.channel_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['object_list']), [])

        # re-activate one of the channels so org has a single channel
        self.tel_channel.is_active = True
        self.tel_channel.save()

        # list page now redirects to channel read page
        self.login(self.user)
        response = self.client.get(reverse('channels.channel_list'))
        self.assertRedirect(response, reverse('channels.channel_read', args=[self.tel_channel.uuid]))

        # unless you're a superuser
        self.login(self.superuser)
        response = self.client.get(reverse('channels.channel_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['object_list']), [self.tel_channel])

        # re-activate other channel so org now has two channels
        self.twitter_channel.is_active = True
        self.twitter_channel.save()

        # no-more redirection for anyone
        self.login(self.user)
        response = self.client.get(reverse('channels.channel_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context['object_list']), {self.tel_channel, self.twitter_channel})

        # clear out the phone and name for the Android channel
        self.tel_channel.name = None
        self.tel_channel.address = None
        self.tel_channel.save()
        response = self.client.get(reverse('channels.channel_list'))
        self.assertContains(response, "Unknown")
        self.assertContains(response, "Android Phone")

    def test_channel_status(self):
        # visit page as a viewer
        self.login(self.user)
        response = self.client.get('/', follow=True)
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")
        self.assertNotIn('delayed_syncevents', response.context, msg="Found delayed_syncevents in context")

        # visit page as superuser
        self.login(self.superuser)
        response = self.client.get('/', follow=True)
        # superusers doesn't have orgs thus cannot have both values
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")
        self.assertNotIn('delayed_syncevents', response.context, msg="Found delayed_syncevents in context")

        # visit page as administrator
        self.login(self.admin)
        response = self.client.get('/', follow=True)

        # there is not unsent nor delayed syncevents
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")
        self.assertNotIn('delayed_syncevents', response.context, msg="Found delayed_syncevents in context")

        # replace existing channels with a single Android device
        Channel.objects.update(is_active=False)
        channel = Channel.create(self.org, self.user, None, Channel.TYPE_ANDROID, None, "+250781112222",
                                 gcm_id="asdf", secret="asdf", created_on=(timezone.now() - timedelta(hours=2)))

        response = self.client.get('/', Follow=True)
        self.assertNotIn('delayed_syncevents', response.context)
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")

        # simulate a sync in back in two hours
        post_data = dict(cmds=[
                         # device details status
                         dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="60",
                              net="UMTS", pending=[], retry=[])])
        self.sync(channel, post_data)
        sync_event = SyncEvent.objects.all()[0]
        sync_event.created_on = timezone.now() - timedelta(hours=2)
        sync_event.save()

        response = self.client.get('/', Follow=True)
        self.assertIn('delayed_syncevents', response.context)
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")

        # add a message, just sent so shouldn't have delayed
        msg = Msg.create_outgoing(self.org, self.user, 'tel:250788123123', "test")
        response = self.client.get('/', Follow=True)
        self.assertIn('delayed_syncevents', response.context)
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")

        # but put it in the past
        msg.delete()
        msg = Msg.create_outgoing(self.org, self.user, 'tel:250788123123', "test",
                                  created_on=timezone.now() - timedelta(hours=3))
        response = self.client.get('/', Follow=True)
        self.assertIn('delayed_syncevents', response.context)
        self.assertIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")

        # if there is a successfully sent message after sms was created we do not consider it as delayed
        success_msg = Msg.create_outgoing(self.org, self.user, 'tel:+250788123123', "success-send",
                                          created_on=timezone.now() - timedelta(hours=2))
        success_msg.sent_on = timezone.now() - timedelta(hours=2)
        success_msg.status = 'S'
        success_msg.save()
        response = self.client.get('/', Follow=True)
        self.assertIn('delayed_syncevents', response.context)
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")

        # test that editors have the channel of the the org the are using
        other_user = self.create_user("Other")
        self.create_secondary_org()
        self.org2.administrators.add(other_user)
        self.org.editors.add(other_user)
        self.assertFalse(self.org2.channels.all())

        self.login(other_user)

        other_user.set_org(self.org2)

        self.assertEqual(self.org2, other_user.get_org())
        response = self.client.get('/', follow=True)
        self.assertNotIn('channel_type', response.context, msg="Found channel_type in context")

        other_user.set_org(self.org)

        self.assertEqual(1, self.org.channels.filter(is_active=True).count())
        self.assertEqual(self.org, other_user.get_org())

        response = self.client.get('/', follow=True)
        # self.assertIn('channel_type', response.context)

    def sync(self, channel, post_data=None, signature=None):
        if not post_data:
            post_data = "{}"
        else:
            post_data = json.dumps(post_data)

        ts = int(time.time())
        if not signature:

            # sign the request
            key = str(channel.secret) + str(ts)
            signature = hmac.new(key=force_bytes(key), msg=force_bytes(post_data), digestmod=hashlib.sha256).digest()

            # base64 and url sanitize
            signature = quote(base64.urlsafe_b64encode(signature))

        return self.client.post("%s?signature=%s&ts=%d" % (reverse('sync', args=[channel.pk]), signature, ts),
                                content_type='application/json', data=post_data)

    def test_update(self):
        update_url = reverse('channels.channel_update', args=[self.tel_channel.id])

        # only user of the org can view the update page of a channel
        self.client.logout()
        self.login(self.user)
        response = self.client.get(update_url)
        self.assertEqual(302, response.status_code)

        self.login(self.user)
        # visit the channel's update page as a manager within the channel's organization
        self.org.administrators.add(self.user)
        response = self.fetch_protected(update_url, self.user)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request['PATH_INFO'], update_url)

        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEqual(channel.name, "Test Channel")
        self.assertEqual(channel.address, "+250785551212")

        postdata = dict()
        postdata['name'] = "Test Channel Update1"
        postdata['address'] = "+250785551313"

        self.login(self.user)
        response = self.client.post(update_url, postdata, follow=True)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEqual(channel.name, "Test Channel Update1")
        self.assertEqual(channel.address, "+250785551313")

        # if we change the channel to a twilio type, shouldn't be able to edit our address
        channel.channel_type = 'T'
        channel.save()

        response = self.client.get(update_url)
        self.assertNotIn('address', response.context['form'].fields)

        # bring it back to android
        channel.channel_type = Channel.TYPE_ANDROID
        channel.save()

        # visit the channel's update page as administrator
        self.org.administrators.add(self.user)
        self.user.set_org(self.org)
        response = self.fetch_protected(update_url, self.user)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request['PATH_INFO'], update_url)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEqual(channel.name, "Test Channel Update1")
        self.assertEqual(channel.address, "+250785551313")

        postdata = dict()
        postdata['name'] = "Test Channel Update2"
        postdata['address'] = "+250785551414"

        response = self.fetch_protected(update_url, self.user, postdata)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEqual(channel.name, "Test Channel Update2")
        self.assertEqual(channel.address, "+250785551414")

        # visit the channel's update page as superuser
        self.superuser.set_org(self.org)
        response = self.fetch_protected(update_url, self.superuser)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request['PATH_INFO'], update_url)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEqual(channel.name, "Test Channel Update2")
        self.assertEqual(channel.address, "+250785551414")

        postdata = dict()
        postdata['name'] = "Test Channel Update3"
        postdata['address'] = "+250785551515"

        response = self.fetch_protected(update_url, self.superuser, postdata)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEqual(channel.name, "Test Channel Update3")
        self.assertEqual(channel.address, "+250785551515")

        # make sure channel works with alphanumeric numbers
        channel.address = "EATRIGHT"
        self.assertEqual("EATRIGHT", channel.get_address_display())
        self.assertEqual("EATRIGHT", channel.get_address_display(e164=True))

        # change channel type to Twitter
        channel.channel_type = 'TT'
        channel.schemes = [TWITTER_SCHEME]
        channel.address = 'billy_bob'
        channel.scheme = 'twitter'
        channel.config = {'handle_id': 12345, 'oauth_token': 'abcdef', 'oauth_token_secret': '23456'}
        channel.save()

        self.assertEqual('@billy_bob', channel.get_address_display())
        self.assertEqual('@billy_bob', channel.get_address_display(e164=True))

        response = self.fetch_protected(update_url, self.user)
        self.assertEqual(200, response.status_code)
        self.assertIn('name', response.context['fields'])
        self.assertIn('alert_email', response.context['fields'])
        self.assertIn('address', response.context['fields'])
        self.assertNotIn('country', response.context['fields'])

        postdata = dict()
        postdata['name'] = "Twitter2"
        postdata['alert_email'] = "bob@example.com"
        postdata['address'] = "billy_bob"

        self.fetch_protected(update_url, self.user, postdata)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEqual(channel.name, "Twitter2")
        self.assertEqual(channel.alert_email, "bob@example.com")
        self.assertEqual(channel.address, "billy_bob")

    def test_read(self):
        post_data = dict(cmds=[
                         # device details status
                         dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="60",
                              net="UMTS", pending=[], retry=[])])

        # now send the channel's updates
        self.sync(self.tel_channel, post_data)
        post_data = dict(cmds=[
                         # device details status
                         dict(cmd="status", p_sts="FUL", p_src="AC", p_lvl="100",
                              net="WIFI", pending=[], retry=[])])

        # now send the channel's updates
        self.sync(self.tel_channel, post_data)
        self.assertEqual(2, SyncEvent.objects.all().count())

        # non-org users can't view our channels
        self.login(self.non_org_user)
        response = self.client.get(reverse('channels.channel_read', args=[self.tel_channel.uuid]))
        self.assertRedirect(response, reverse('orgs.org_choose'))

        # org users can
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.user)

        self.assertEqual(len(response.context['source_stats']), len(SyncEvent.objects.values_list('power_source', flat=True).distinct()))
        self.assertEqual('AC', response.context['source_stats'][0][0])
        self.assertEqual(1, response.context['source_stats'][0][1])
        self.assertEqual('BAT', response.context['source_stats'][1][0])
        self.assertEqual(1, response.context['source_stats'][0][1])

        self.assertEqual(len(response.context['network_stats']), len(SyncEvent.objects.values_list('network_type', flat=True).distinct()))
        self.assertEqual('UMTS', response.context['network_stats'][0][0])
        self.assertEqual(1, response.context['network_stats'][0][1])
        self.assertEqual('WIFI', response.context['network_stats'][1][0])
        self.assertEqual(1, response.context['network_stats'][1][1])

        self.assertTrue(len(response.context['latest_sync_events']) <= 5)

        response = self.fetch_protected(reverse('orgs.org_home'), self.admin)
        self.assertNotContains(response, 'Enable Voice')

        # Add twilio credentials to make sure we can add calling for our android channel
        twilio_config = {ACCOUNT_SID: 'SID', ACCOUNT_TOKEN: 'TOKEN', APPLICATION_SID: 'APP SID'}
        config = self.org.config
        config.update(twilio_config)
        self.org.config = config
        self.org.save(update_fields=['config'])

        response = self.fetch_protected(reverse('orgs.org_home'), self.admin)
        self.assertTrue(self.org.is_connected_to_twilio())
        self.assertContains(response, 'Enable Voice')

        two_hours_ago = timezone.now() - timedelta(hours=2)

        # make sure our channel is old enough to trigger alerts
        self.tel_channel.created_on = two_hours_ago
        self.tel_channel.save()

        # delayed sync status
        for sync in SyncEvent.objects.all():
            sync.created_on = two_hours_ago
            sync.save()

        # add a message, just sent so shouldn't be delayed
        Msg.create_outgoing(self.org, self.user, 'tel:250785551212', 'delayed message', created_on=two_hours_ago)

        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.admin)
        self.assertIn('delayed_sync_event', response.context_data.keys())
        self.assertIn('unsent_msgs_count', response.context_data.keys())

        # with superuser
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.superuser)
        self.assertEqual(200, response.status_code)

        # now that we can access the channel, which messages do we display in the chart?
        joe = self.create_contact('Joe', '+2501234567890')
        test_contact = Contact.get_test_contact(self.admin)

        # should have two series, one for incoming one for outgoing
        self.assertEqual(2, len(response.context['message_stats']))

        # but only an outgoing message so far
        self.assertEqual(0, len(response.context['message_stats'][0]['data']))
        self.assertEqual(1, response.context['message_stats'][1]['data'][-1]['count'])

        # we have one row for the message stats table
        self.assertEqual(1, len(response.context['message_stats_table']))
        # only one outgoing message
        self.assertEqual(0, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEqual(1, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['outgoing_ivr_count'])

        # send messages with a test contact
        Msg.create_incoming(self.tel_channel, six.text_type(test_contact.get_urn()), 'This incoming message will not be counted')
        Msg.create_outgoing(self.org, self.user, test_contact, 'This outgoing message will not be counted')

        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.superuser)
        self.assertEqual(200, response.status_code)

        # nothing should change since it's a test contact
        self.assertEqual(0, len(response.context['message_stats'][0]['data']))
        self.assertEqual(1, response.context['message_stats'][1]['data'][-1]['count'])

        # no change on the table starts too
        self.assertEqual(1, len(response.context['message_stats_table']))
        self.assertEqual(0, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEqual(1, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['outgoing_ivr_count'])

        # send messages with a normal contact
        Msg.create_incoming(self.tel_channel, six.text_type(joe.get_urn(TEL_SCHEME)), 'This incoming message will be counted')
        Msg.create_outgoing(self.org, self.user, joe, 'This outgoing message will be counted')

        # now we have an inbound message and two outbounds
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.superuser)
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.context['message_stats'][0]['data'][-1]['count'])

        # this assertion is problematic causing time-sensitive failures, to reconsider
        # self.assertEqual(2, response.context['message_stats'][1]['data'][-1]['count'])

        # message stats table have an inbound and two outbounds in the last month
        self.assertEqual(1, len(response.context['message_stats_table']))
        self.assertEqual(1, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEqual(2, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['outgoing_ivr_count'])

        # test cases for IVR messaging, make our relayer accept calls
        self.tel_channel.role = 'SCAR'
        self.tel_channel.save()

        from temba.msgs.models import IVR
        Msg.create_incoming(self.tel_channel, six.text_type(test_contact.get_urn()), 'incoming ivr as a test contact', msg_type=IVR)
        Msg.create_outgoing(self.org, self.user, test_contact, 'outgoing ivr as a test contact', msg_type=IVR)
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.superuser)

        # nothing should have changed
        self.assertEqual(2, len(response.context['message_stats']))

        self.assertEqual(1, len(response.context['message_stats_table']))
        self.assertEqual(1, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEqual(2, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEqual(0, response.context['message_stats_table'][0]['outgoing_ivr_count'])

        # now let's create an ivr interaction from a real contact
        Msg.create_incoming(self.tel_channel, six.text_type(joe.get_urn()), 'incoming ivr', msg_type=IVR)
        Msg.create_outgoing(self.org, self.user, joe, 'outgoing ivr', msg_type=IVR)
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.superuser)

        self.assertEqual(4, len(response.context['message_stats']))
        self.assertEqual(1, response.context['message_stats'][2]['data'][0]['count'])
        self.assertEqual(1, response.context['message_stats'][3]['data'][0]['count'])

        self.assertEqual(1, len(response.context['message_stats_table']))
        self.assertEqual(1, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEqual(2, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEqual(1, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEqual(1, response.context['message_stats_table'][0]['outgoing_ivr_count'])

    def test_invalid(self):

        # Must be POST
        response = self.client.get("%s?signature=sig&ts=123" % (reverse('sync', args=[100])), content_type='application/json')
        self.assertEqual(500, response.status_code)

        # Unknown channel
        response = self.client.post("%s?signature=sig&ts=123" % (reverse('sync', args=[999])), content_type='application/json')
        self.assertEqual(200, response.status_code)
        self.assertEqual('rel', response.json()['cmds'][0]['cmd'])

        # too old
        ts = int(time.time()) - 60 * 16
        response = self.client.post("%s?signature=sig&ts=%d" % (reverse('sync', args=[self.tel_channel.pk]), ts), content_type='application/json')
        self.assertEqual(401, response.status_code)
        self.assertEqual(3, response.json()['error_id'])

    def test_claim(self):
        # no access for regular users
        self.login(self.user)
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertLoginRedirect(response)

        # editor can access
        self.login(self.editor)
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertEqual(200, response.status_code)

        # as can admins
        self.login(self.admin)
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.context['twilio_countries'], "Belgium, Canada, Finland, Norway, Poland, Spain, "
                                                               "Sweden, United Kingdom or United States")

        # one recommended channel (Mtarget in Rwanda)
        self.assertEqual(len(response.context['recommended_channels']), 1)

        self.assertEqual(response.context['channel_types']['PHONE'][0].code, 'T')
        self.assertEqual(response.context['channel_types']['PHONE'][1].code, 'TMS')
        self.assertEqual(response.context['channel_types']['PHONE'][2].code, 'NX')
        self.assertEqual(response.context['channel_types']['PHONE'][3].code, 'CT')
        self.assertEqual(response.context['channel_types']['PHONE'][4].code, 'EX')

        self.org.timezone = 'Canada/Central'
        self.org.save()

        response = self.client.get(reverse('channels.channel_claim'))
        self.assertEqual(200, response.status_code)

        self.assertEqual(len(response.context['recommended_channels']), 3)
        self.assertEqual(response.context['recommended_channels'][0].code, 'T')
        self.assertEqual(response.context['recommended_channels'][1].code, 'TMS')
        self.assertEqual(response.context['recommended_channels'][2].code, 'NX')

        self.assertEqual(response.context['channel_types']['PHONE'][0].code, 'CT')
        self.assertEqual(response.context['channel_types']['PHONE'][1].code, 'EX')
        self.assertEqual(response.context['channel_types']['PHONE'][2].code, 'IB')
        self.assertEqual(response.context['channel_types']['PHONE'][3].code, 'JS')

    def test_register_and_claim_android(self):
        # remove our explicit country so it needs to be derived from channels
        self.org.country = None
        self.org.save()

        Channel.objects.all().delete()

        reg_data = dict(cmds=[dict(cmd="gcm", gcm_id="GCM111", uuid='uuid'),
                              dict(cmd='status', cc='RW', dev='Nexus')])

        # must be a post
        response = self.client.get(reverse('register'), content_type='application/json')
        self.assertEqual(500, response.status_code)

        # try a legit register
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        self.assertEqual(200, response.status_code)

        android1 = Channel.objects.get()
        self.assertIsNone(android1.org)
        self.assertIsNone(android1.address)
        self.assertIsNone(android1.alert_email)
        self.assertEqual(android1.country, 'RW')
        self.assertEqual(android1.device, 'Nexus')
        self.assertEqual(android1.gcm_id, 'GCM111')
        self.assertEqual(android1.uuid, 'uuid')
        self.assertTrue(android1.secret)
        self.assertTrue(android1.claim_code)
        self.assertEqual(android1.created_by, get_anonymous_user())

        # check channel JSON in response
        response_json = response.json()
        self.assertEqual(response_json, dict(cmds=[dict(cmd='reg',
                                                        relayer_claim_code=android1.claim_code,
                                                        relayer_secret=android1.secret,
                                                        relayer_id=android1.id)]))

        # try registering again with same details
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        self.assertEqual(response.status_code, 200)

        android1 = Channel.objects.get()
        response_json = response.json()

        self.assertEqual(response_json, dict(cmds=[dict(cmd='reg',
                                                        relayer_claim_code=android1.claim_code,
                                                        relayer_secret=android1.secret,
                                                        relayer_id=android1.id)]))

        # try to claim as non-admin
        self.login(self.user)
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=android1.claim_code, phone_number="0788123123"))
        self.assertLoginRedirect(response)

        # try to claim with an invalid phone number
        self.login(self.admin)
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=android1.claim_code, phone_number="078123"))
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'phone_number', "Invalid phone number, try again.")

        # claim our channel
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=android1.claim_code, phone_number="0788123123"))

        # redirect to welcome page
        self.assertIn('success', response.get('Location', None))
        self.assertRedirect(response, reverse('public.public_welcome'))

        # channel is updated with org details and claim code is now blank
        android1.refresh_from_db()
        secret = android1.secret
        self.assertEqual(android1.org, self.org)
        self.assertEqual(android1.address, '+250788123123')  # normalized
        self.assertEqual(android1.alert_email, self.admin.email)  # the logged-in user
        self.assertEqual(android1.gcm_id, 'GCM111')
        self.assertEqual(android1.uuid, 'uuid')
        self.assertFalse(android1.claim_code)

        # try having a device register again
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        self.assertEqual(response.status_code, 200)

        # should return same channel but with a new claim code and secret
        android1.refresh_from_db()
        self.assertEqual(android1.org, self.org)
        self.assertEqual(android1.address, '+250788123123')
        self.assertEqual(android1.alert_email, self.admin.email)
        self.assertEqual(android1.gcm_id, 'GCM111')
        self.assertEqual(android1.uuid, 'uuid')
        self.assertEqual(android1.is_active, True)
        self.assertTrue(android1.claim_code)
        self.assertNotEqual(android1.secret, secret)

        # should be able to claim again
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=android1.claim_code, phone_number="0788123123"))
        self.assertRedirect(response, reverse('public.public_welcome'))

        # try having a device register yet again with new GCM ID
        reg_data['cmds'][0]['gcm_id'] = "GCM222"
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        self.assertEqual(response.status_code, 200)

        # should return same channel but with GCM updated
        android1.refresh_from_db()
        self.assertEqual(android1.org, self.org)
        self.assertEqual(android1.address, '+250788123123')
        self.assertEqual(android1.alert_email, self.admin.email)
        self.assertEqual(android1.gcm_id, 'GCM222')
        self.assertEqual(android1.uuid, 'uuid')
        self.assertEqual(android1.is_active, True)

        # we can claim again with new phone number
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=android1.claim_code, phone_number="+250788123124"))
        self.assertRedirect(response, reverse('public.public_welcome'))

        android1.refresh_from_db()
        self.assertEqual(android1.org, self.org)
        self.assertEqual(android1.address, '+250788123124')
        self.assertEqual(android1.alert_email, self.admin.email)
        self.assertEqual(android1.gcm_id, 'GCM222')
        self.assertEqual(android1.uuid, 'uuid')
        self.assertEqual(android1.is_active, True)

        # release and then register with same details and claim again
        old_uuid = android1.uuid
        android1.release()

        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        claim_code = response.json()['cmds'][0]['relayer_claim_code']
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=claim_code, phone_number="+250788123124"))
        self.assertRedirect(response, reverse('public.public_welcome'))

        android1.refresh_from_db()

        self.assertNotEqual(android1.uuid, old_uuid)  # inactive channel now has new UUID

        # and we have a new Android channel with our UUID
        android2 = Channel.objects.get(is_active=True)
        self.assertNotEqual(android2, android1)
        self.assertEqual(android2.uuid, 'uuid')

        # try to claim a bogus channel
        response = self.client.post(reverse('channels.channel_claim_android'), dict(claim_code="Your Mom"))
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'claim_code', "Invalid claim code, please check and try again.")

        # check our primary tel channel is the same as our outgoing
        default_sender = self.org.get_send_channel(TEL_SCHEME)
        self.assertEqual(default_sender, android2)
        self.assertEqual(default_sender, self.org.get_receive_channel(TEL_SCHEME))
        self.assertFalse(default_sender.is_delegate_sender())

        response = self.client.get(reverse('channels.channel_bulk_sender_options'))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('channels.channel_create_bulk_sender') + "?connection=NX",
                                    dict(connection='NX'))
        self.assertFormError(response, 'form', 'channel', "Can't add sender for that number")

        # try to claim a bulk Nexmo sender (without adding Nexmo account to org)
        claim_nexmo_url = reverse('channels.channel_create_bulk_sender') + "?connection=NX&channel=%d" % android2.pk
        response = self.client.post(claim_nexmo_url, dict(connection='NX', channel=android2.pk))
        self.assertFormError(response, 'form', 'connection', "A connection to a Nexmo account is required")

        # send channel is still our Android device
        self.assertEqual(self.org.get_send_channel(TEL_SCHEME), android2)
        self.assertFalse(self.org.is_connected_to_nexmo())

        # now connect to nexmo
        with patch('temba.utils.nexmo.NexmoClient.update_account') as connect:
            connect.return_value = True
            with patch('nexmo.Client.create_application') as create_app:
                create_app.return_value = dict(id='app-id', keys=dict(private_key='private-key'))
                self.org.connect_nexmo('123', '456', self.admin)
                self.org.save()
        self.assertTrue(self.org.is_connected_to_nexmo())

        # now adding Nexmo bulk sender should work
        response = self.client.post(claim_nexmo_url, dict(connection='NX', channel=android2.pk))
        self.assertRedirect(response, reverse('orgs.org_home'))

        # new Nexmo channel created for delegated sending
        nexmo = self.org.get_send_channel(TEL_SCHEME)
        self.assertEqual(nexmo.channel_type, 'NX')
        self.assertEqual(nexmo.parent, android2)
        self.assertTrue(nexmo.is_delegate_sender())
        self.assertEqual(nexmo.tps, 1)
        channel_config = nexmo.config
        self.assertEqual(channel_config[Channel.CONFIG_NEXMO_API_KEY], '123')
        self.assertEqual(channel_config[Channel.CONFIG_NEXMO_API_SECRET], '456')
        self.assertEqual(channel_config[Channel.CONFIG_NEXMO_APP_ID], 'app-id')
        self.assertEqual(channel_config[Channel.CONFIG_NEXMO_APP_PRIVATE_KEY], 'private-key')

        # reading our nexmo channel should now offer a disconnect option
        nexmo = self.org.channels.filter(channel_type='NX').first()
        response = self.client.get(reverse('channels.channel_read', args=[nexmo.uuid]))
        self.assertContains(response, 'Disable Bulk Sending')

        # receiving still job of our Android device
        self.assertEqual(self.org.get_receive_channel(TEL_SCHEME), android2)

        # re-register device with country as US
        reg_data = dict(cmds=[dict(cmd="gcm", gcm_id="GCM222", uuid='uuid'),
                              dict(cmd='status', cc='US', dev="Nexus 5X")])
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        self.assertEqual(response.status_code, 200)

        # channel country and device updated
        android2.refresh_from_db()
        self.assertEqual(android2.country, 'US')
        self.assertEqual(android2.device, "Nexus 5X")
        self.assertEqual(android2.org, self.org)
        self.assertEqual(android2.gcm_id, "GCM222")
        self.assertEqual(android2.uuid, "uuid")
        self.assertTrue(android2.is_active)

        # set back to RW...
        android2.country = 'RW'
        android2.save()

        # our country is RW
        self.assertEqual(self.org.get_country_code(), 'RW')

        # remove nexmo
        nexmo.release()

        self.assertEqual(self.org.get_country_code(), 'RW')

        # register another device with country as US
        reg_data = dict(cmds=[dict(cmd="gcm", gcm_id="GCM444", uuid='uuid4'),
                              dict(cmd='status', cc='US', dev="Nexus 6P")])
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')

        claim_code = response.json()['cmds'][0]['relayer_claim_code']

        # try to claim it...
        self.client.post(reverse('channels.channel_claim_android'), dict(claim_code=claim_code, phone_number="12065551212"))

        # should work, can have two channels in different countries
        channel = Channel.objects.get(country='US')
        self.assertEqual(channel.address, '+12065551212')

        self.assertEqual(Channel.objects.filter(org=self.org, is_active=True).count(), 2)

        # normalize a URN with a fully qualified number
        number, valid = URN.normalize_number('+12061112222', None)
        self.assertTrue(valid)

        # not international format
        number, valid = URN.normalize_number('0788383383', None)
        self.assertFalse(valid)

        # get our send channel without a URN, should just default to last
        default_channel = self.org.get_send_channel(TEL_SCHEME)
        self.assertEqual(default_channel, channel)

        # get our send channel for a Rwandan URN
        rwanda_channel = self.org.get_send_channel(TEL_SCHEME, ContactURN.create(self.org, None, 'tel:+250788383383'))
        self.assertEqual(rwanda_channel, android2)

        # and a US one
        us_channel = self.org.get_send_channel(TEL_SCHEME, ContactURN.create(self.org, None, 'tel:+12065555353'))
        self.assertEqual(us_channel, channel)

        # a different country altogether should just give us the default
        us_channel = self.org.get_send_channel(TEL_SCHEME, ContactURN.create(self.org, None, 'tel:+593997290044'))
        self.assertEqual(us_channel, channel)

        self.org = Org.objects.get(id=self.org.id)
        self.assertIsNone(self.org.get_country_code())

        # yet another registration in rwanda
        reg_data = dict(cmds=[dict(cmd="gcm", gcm_id="GCM555", uuid='uuid5'),
                              dict(cmd='status', cc='RW', dev="Nexus 5")])
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        claim_code = response.json()['cmds'][0]['relayer_claim_code']

        # try to claim it with number taken by other Android channel
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=claim_code, phone_number="+250788123124"))
        self.assertFormError(response, 'form', 'phone_number', "Another channel has this number. Please remove that channel first.")

        # create channel in another org
        self.create_secondary_org()
        Channel.create(self.org2, self.admin2, 'RW', 'A', "", "+250788382382")

        # can claim it with this number, and because it's a fully qualified RW number, doesn't matter that channel is US
        response = self.client.post(reverse('channels.channel_claim_android'),
                                    dict(claim_code=claim_code, phone_number="+250788382382"))
        self.assertRedirect(response, reverse('public.public_welcome'))

        # should be added with RW as the country
        self.assertTrue(Channel.objects.get(address='+250788382382', country='RW', org=self.org))

    def test_search_nexmo(self):
        self.login(self.admin)
        self.org.channels.update(is_active=False)
        self.channel = Channel.create(self.org, self.user, 'RW', 'NX', None, '+250788123123',
                                      uuid='00000000-0000-0000-0000-000000001234')

        self.nexmo_uuid = str(uuid.uuid4())
        nexmo_config = {NEXMO_KEY: '1234', NEXMO_SECRET: '1234', NEXMO_UUID: self.nexmo_uuid,
                        NEXMO_APP_ID: 'nexmo-app-id', NEXMO_APP_PRIVATE_KEY: 'nexmo-private-key'}

        org = self.channel.org

        config = org.config
        config.update(nexmo_config)
        org.config = config
        org.save()

        search_nexmo_url = reverse('channels.channel_search_nexmo')

        response = self.client.get(search_nexmo_url)
        self.assertIn('area_code', response.context['form'].fields)
        self.assertIn('country', response.context['form'].fields)

        with patch('requests.get') as nexmo_get:
            nexmo_get.side_effect = [MockResponse(200,
                                                  '{"count":1,"numbers":[{"features": ["SMS", "VOICE"], '
                                                  '"type":"mobile-lvn","country":"US","msisdn":"13607884540"}] }'),
                                     MockResponse(200,
                                                  '{"count":1,"numbers":[{"features": ["SMS", "VOICE"], '
                                                  '"type":"mobile-lvn","country":"US","msisdn":"13607884550"}] }'),
                                     ]

            post_data = dict(country='US', area_code='360')
            response = self.client.post(search_nexmo_url, post_data, follow=True)

            self.assertEqual(response.json(), ['+1 360-788-4540', '+1 360-788-4550'])

    def test_plivo_search_numbers(self):
        self.login(self.admin)

        plivo_search_url = reverse('channels.channel_search_plivo')

        with patch('requests.get') as plivo_get:
            plivo_get.return_value = MockResponse(200, json.dumps(dict(objects=[])))

            response = self.client.post(plivo_search_url, dict(country='US', area_code=''), follow=True)

            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'error')

            # missing key to throw exception
            plivo_get.return_value = MockResponse(200, json.dumps(dict()))
            response = self.client.post(plivo_search_url, dict(country='US', area_code=''), follow=True)

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'error')

            plivo_get.side_effect = [
                MockResponse(200, json.dumps(dict())),  # get account in pre_process
                MockResponse(400, "Bad request")  # failed search numbers
            ]
            response = self.client.post(plivo_search_url, dict(country='US', area_code=''), follow=True)

            self.assertContains(response, 'Bad request')

    def test_release(self):
        Channel.objects.all().delete()
        self.login(self.admin)

        # register and claim an Android channel
        reg_data = dict(cmds=[dict(cmd="gcm", gcm_id="GCM111", uuid='uuid'),
                              dict(cmd='status', cc='RW', dev='Nexus')])
        self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        android = Channel.objects.get()
        self.client.post(reverse('channels.channel_claim_android'),
                         dict(claim_code=android.claim_code, phone_number="0788123123"))
        android.refresh_from_db()

        # connect org to Nexmo and add bulk sender
        with patch('temba.utils.nexmo.NexmoClient.update_account') as connect:
            connect.return_value = True
            with patch('nexmo.Client.create_application') as create_app:
                create_app.return_value = dict(id='app-id', keys=dict(private_key='private-key'))
                self.org.connect_nexmo('123', '456', self.admin)
                self.org.save()

        claim_nexmo_url = reverse('channels.channel_create_bulk_sender') + "?connection=NX&channel=%d" % android.pk
        self.client.post(claim_nexmo_url, dict(connection='NX', channel=android.pk))
        nexmo = Channel.objects.get(channel_type='NX')

        android.release()

        # check that some details are cleared and channel is now in active
        self.assertFalse(android.is_active)

        # Nexmo delegate should have been released as well
        nexmo.refresh_from_db()
        self.assertFalse(nexmo.is_active)

        Channel.objects.all().delete()

        # register and claim an Android channel
        reg_data = dict(cmds=[dict(cmd="fcm", fcm_id="FCM111", uuid='uuid'),
                              dict(cmd='status', cc='RW', dev='Nexus')])
        self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        android = Channel.objects.get()
        self.client.post(reverse('channels.channel_claim_android'),
                         dict(claim_code=android.claim_code, phone_number="0788123123"))
        android.refresh_from_db()

        android.release()

        # check that some details are cleared and channel is now in active
        self.assertFalse(android.is_active)

    @override_settings(IS_PROD=True)
    def test_release_ivr_channel(self):

        # create outgoing call for the channel
        contact = self.create_contact('Bruno Mars', '+252788123123')
        call = IVRCall.create_outgoing(self.tel_channel, contact, contact.get_urn(TEL_SCHEME), self.admin)

        self.assertNotEqual(call.status, ChannelSession.INTERRUPTED)
        self.tel_channel.release()

        call.refresh_from_db()
        self.assertEqual(call.status, ChannelSession.INTERRUPTED)

    def test_unclaimed(self):
        response = self.sync(self.unclaimed_channel)
        self.assertEqual(200, response.status_code)
        response = response.json()

        # should be a registration command containing a new claim code
        self.assertEqual(response['cmds'][0]['cmd'], 'reg')

        post_data = dict(cmds=[dict(cmd="status",
                                    org_id=self.unclaimed_channel.pk,
                                    p_lvl=84,
                                    net="WIFI",
                                    p_sts="CHA",
                                    p_src="USB",
                                    pending=[],
                                    retry=[])])

        # try syncing against the unclaimed channel that has a secret
        self.unclaimed_channel.secret = "999"
        self.unclaimed_channel.save()

        response = self.sync(self.unclaimed_channel, post_data=post_data)
        response = response.json()

        # registration command
        self.assertEqual(response['cmds'][0]['cmd'], 'reg')

        # claim the channel on the site
        self.unclaimed_channel.org = self.org
        self.unclaimed_channel.save()

        post_data = dict(cmds=[dict(cmd="status",
                                    org_id="-1",
                                    p_lvl=84,
                                    net="WIFI",
                                    p_sts="STATUS_CHARGING",
                                    p_src="USB",
                                    pending=[],
                                    retry=[])])

        response = self.sync(self.unclaimed_channel, post_data=post_data)
        response = response.json()

        # should now be a claim command in return
        self.assertEqual(response['cmds'][0]['cmd'], 'claim')

        # now try releasing the channel from the client
        post_data = dict(cmds=[dict(cmd="reset", p_id=1)])

        response = self.sync(self.unclaimed_channel, post_data=post_data)
        response = response.json()

        # channel should be released now
        channel = Channel.objects.get(pk=self.unclaimed_channel.pk)
        self.assertFalse(channel.is_active)

    def test_quota_exceeded(self):
        # set our org to be on the trial plan
        self.org.plan = FREE_PLAN
        self.org.save()
        self.org.topups.all().update(credits=10)

        self.assertEqual(10, self.org.get_credits_remaining())
        self.assertEqual(0, self.org.get_credits_used())

        # if we sync should get one message back
        self.send_message(['250788382382'], "How is it going?")

        response = self.sync(self.tel_channel)
        self.assertEqual(200, response.status_code)
        response = response.json()
        self.assertEqual(1, len(response['cmds']))

        self.assertEqual(9, self.org.get_credits_remaining())
        self.assertEqual(1, self.org.get_credits_used())

        # let's create 10 other messages, this will put our last message above our quota
        for i in range(10):
            self.send_message(['250788382%03d' % i], "This is message # %d" % i)

        # should get the 10 messages we are allotted back, not the 11 that exist
        response = self.sync(self.tel_channel)
        self.assertEqual(200, response.status_code)
        response = response.json()
        self.assertEqual(10, len(response['cmds']))

    def test_sync_broadcast_multiple_channels(self):
        self.org.administrators.add(self.user)
        self.user.set_org(self.org)

        channel2 = Channel.create(self.org, self.user, 'RW', 'A', name="Test Channel 2", address="+250785551313",
                                  role="SR", secret="12367", gcm_id="456")

        contact1 = self.create_contact("John Doe", '250788382382')
        contact2 = self.create_contact("John Doe", '250788383383')

        contact1_urn = contact1.get_urn()
        contact1_urn.channel = self.tel_channel
        contact1_urn.save()

        contact2_urn = contact2.get_urn()
        contact2_urn.channel = channel2
        contact2_urn.save()

        # send a broadcast to urn that have different preferred channels
        self.send_message(['250788382382', '250788383383'], "How is it going?")

        # Should contain messages for the the channel only
        response = self.sync(self.tel_channel)
        self.assertEqual(200, response.status_code)

        self.tel_channel.refresh_from_db()

        response = response.json()
        cmds = response['cmds']
        self.assertEqual(1, len(cmds))
        self.assertEqual(len(cmds[0]['to']), 1)
        self.assertEqual(cmds[0]['to'][0]['phone'], '+250788382382')

        # Should contain messages for the the channel only
        response = self.sync(channel2)
        self.assertEqual(200, response.status_code)

        channel2.refresh_from_db()

        response = response.json()
        cmds = response['cmds']
        self.assertEqual(1, len(cmds))
        self.assertEqual(len(cmds[0]['to']), 1)
        self.assertEqual(cmds[0]['to'][0]['phone'], '+250788383383')

    def test_sync(self):
        date = timezone.now()
        date = int(time.mktime(date.timetuple())) * 1000

        # create a payload from the client
        bcast = self.send_message(['250788382382', '250788383383'], "How is it going?")
        msg1 = bcast[0]
        msg2 = bcast[1]
        msg3 = self.send_message(['250788382382'], "What is your name?")
        msg4 = self.send_message(['250788382382'], "Do you have any children?")
        msg5 = self.send_message(['250788382382'], "What's my dog's name?")

        # an incoming message that should not be included even if it is still pending
        incoming_message = Msg.create_incoming(self.tel_channel, "tel:+250788382382", 'hey')
        incoming_message.status = PENDING
        incoming_message.save()

        self.org.administrators.add(self.user)
        self.user.set_org(self.org)

        # Check our sync point has all three messages queued for delivery
        response = self.sync(self.tel_channel)
        self.assertEqual(200, response.status_code)

        # check last seen and gcm id were updated
        self.tel_channel.refresh_from_db()

        response = response.json()
        cmds = response['cmds']
        self.assertEqual(4, len(cmds))

        # assert that our first command is the two message broadcast
        cmd = cmds[0]
        self.assertEqual("How is it going?", cmd['msg'])
        self.assertIn('+250788382382', [m['phone'] for m in cmd['to']])
        self.assertIn('+250788383383', [m['phone'] for m in cmd['to']])

        self.assertTrue(msg1.pk in [m['id'] for m in cmd['to']])
        self.assertTrue(msg2.pk in [m['id'] for m in cmd['to']])

        # add another message we'll pretend is in retry to see that we exclude them from sync
        msg6 = self.send_message(['250788382382'], "Pretend this message is in retry on the client, don't send it on sync")

        # a pending outgoing message should be included
        Msg.create_outgoing(self.org, self.admin, msg6.contact, "Hello, we heard from you.")

        six_mins_ago = timezone.now() - timedelta(minutes=6)
        self.tel_channel.last_seen = six_mins_ago
        self.tel_channel.gcm_id = 'old_gcm_id'
        self.tel_channel.save(update_fields=['last_seen', 'gcm_id'])

        post_data = dict(cmds=[

            # device gcm data
            dict(cmd='gcm', gcm_id='12345', uuid='abcde'),

            # device details status
            dict(cmd="status", p_sts="DIS", p_src="BAT", p_lvl="60",
                 net="UMTS", org_id=8, retry=[msg6.pk], pending=[]),

            # pending incoming message that should be acknowledged but not updated
            dict(cmd="mt_sent", msg_id=incoming_message.pk, ts=date),

            # results for the outgoing messages
            dict(cmd="mt_sent", msg_id=msg1.pk, ts=date),
            dict(cmd="mt_sent", msg_id=msg2.pk, ts=date),
            dict(cmd="mt_dlvd", msg_id=msg3.pk, ts=date),
            dict(cmd="mt_error", msg_id=msg4.pk, ts=date),
            dict(cmd="mt_fail", msg_id=msg5.pk, ts=date),

            # a missed call
            dict(cmd="call", phone="2505551212", type='miss', ts=date),

            # incoming
            dict(cmd="call", phone="2505551212", type='mt', dur=10, ts=date),

            # incoming, invalid URN
            dict(cmd="call", phone="*", type='mt', dur=10, ts=date),

            # outgoing
            dict(cmd="call", phone="+250788383383", type='mo', dur=5, ts=date),

            # a new incoming message
            dict(cmd="mo_sms", phone="+250788383383", msg="This is giving me trouble", p_id="1", ts=date),

            # an incoming message from an empty contact
            dict(cmd="mo_sms", phone="", msg="This is spam", p_id="2", ts=date)])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        self.tel_channel.refresh_from_db()
        self.assertEqual(self.tel_channel.gcm_id, '12345')
        self.assertTrue(self.tel_channel.last_seen > six_mins_ago)

        # new batch, our ack and our claim command for new org
        self.assertEqual(4, len(response.json()['cmds']))
        self.assertContains(response, "Hello, we heard from you.")
        self.assertContains(response, "mt_bcast")

        # check that our messages were updated accordingly
        self.assertEqual(2, Msg.objects.filter(channel=self.tel_channel, status='S', direction='O').count())
        self.assertEqual(1, Msg.objects.filter(channel=self.tel_channel, status='D', direction='O').count())
        self.assertEqual(1, Msg.objects.filter(channel=self.tel_channel, status='E', direction='O').count())
        self.assertEqual(1, Msg.objects.filter(channel=self.tel_channel, status='F', direction='O').count())

        # we should now have two incoming messages
        self.assertEqual(3, Msg.objects.filter(direction='I').count())

        # one of them should have an empty 'tel'
        self.assertTrue(Msg.objects.filter(direction='I', contact_urn__path='empty'))

        # We should now have one sync
        self.assertEqual(1, SyncEvent.objects.filter(channel=self.tel_channel).count())

        # check our channel gcm and uuid were updated
        self.tel_channel = Channel.objects.get(pk=self.tel_channel.pk)
        self.assertEqual('12345', self.tel_channel.gcm_id)
        self.assertEqual('abcde', self.tel_channel.uuid)

        # should ignore incoming messages without text
        post_data = dict(cmds=[
            # incoming msg without text
            dict(cmd="mo_sms", phone="+250788383383", p_id="1", ts=date),

        ])

        msgs_count = Msg.objects.all().count()
        response = self.sync(self.tel_channel, post_data)

        # no new message
        self.assertEqual(Msg.objects.all().count(), msgs_count)

        # set an email on our channel
        self.tel_channel.alert_email = 'fred@worldrelif.org'
        self.tel_channel.save()

        # We should not have an alert this time
        self.assertEqual(0, Alert.objects.all().count())

        # the case the status must be be reported
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="DIS", p_src="BAT", p_lvl="20", net="UMTS", retry=[], pending=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # we should now have an Alert
        self.assertEqual(1, Alert.objects.all().count())

        # and at this time it must be not ended
        self.assertEqual(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # the case the status must be be reported but already notification sent
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="DIS", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # we should not create a new alert
        self.assertEqual(1, Alert.objects.all().count())

        # still not ended
        self.assertEqual(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # Let plug the channel to charger
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # only one alert
        self.assertEqual(1, Alert.objects.all().count())

        # and we end all alert related to this issue
        self.assertEqual(0, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # clear the alerts
        Alert.objects.all().delete()

        # the case the status is in unknown state

        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="UNK", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # we should now create a new alert
        self.assertEqual(1, Alert.objects.all().count())

        # one alert not ended
        self.assertEqual(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # Let plug the channel to charger to end this unknown power status
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # still only one alert
        self.assertEqual(1, Alert.objects.all().count())

        # and we end all alert related to this issue
        self.assertEqual(0, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # clear all the alerts
        Alert.objects.all().delete()

        # the case the status is in not charging state
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="NOT", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # we should now create a new alert
        self.assertEqual(1, Alert.objects.all().count())

        # one alert not ended
        self.assertEqual(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # Let plug the channel to charger to end this unknown power status
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # first we have a new alert created
        self.assertEqual(1, Alert.objects.all().count())

        # and we end all alert related to this issue
        self.assertEqual(0, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        post_data = dict(cmds=[
            # device fcm data
            dict(cmd='fcm', fcm_id='12345', uuid='abcde')])

        response = self.sync(self.tel_channel, post_data)

        self.tel_channel.refresh_from_db()
        self.assertIsNone(self.tel_channel.gcm_id)
        self.assertTrue(self.tel_channel.last_seen > six_mins_ago)
        self.assertEqual(self.tel_channel.config[Channel.CONFIG_FCM_ID], '12345')

    def test_signing(self):
        # good signature
        self.assertEqual(200, self.sync(self.tel_channel).status_code)

        # bad signature, should result in 401 Unauthorized
        self.assertEqual(401, self.sync(self.tel_channel, signature="badsig").status_code)

    def test_ignore_android_incoming_msg_invalid_phone(self):
        date = timezone.now()
        date = int(time.mktime(date.timetuple())) * 1000

        post_data = dict(cmds=[
            dict(cmd="mo_sms", phone="_@", msg="First message", p_id="1", ts=date)])

        response = self.sync(self.tel_channel, post_data)
        self.assertEqual(200, response.status_code)

        responses = response.json()
        cmds = responses['cmds']

        # check the server gave us responses for our message
        r0 = self.get_response(cmds, '1')

        self.assertIsNotNone(r0)
        self.assertEqual(r0['cmd'], 'ack')

    def test_inbox_duplication(self):

        # if the connection gets interrupted but some messages succeed, we want to make sure subsequent
        # syncs do not result in duplication of messages from the inbox
        date = timezone.now()
        date = int(time.mktime(date.timetuple())) * 1000

        post_data = dict(cmds=[
            dict(cmd="mo_sms", phone="2505551212", msg="First message", p_id="1", ts=date),
            dict(cmd="mo_sms", phone="2505551212", msg="First message", p_id="2", ts=date),
            dict(cmd="mo_sms", phone="2505551212", msg="A second message", p_id="3", ts=date)
        ])

        response = self.sync(self.tel_channel, post_data)
        self.assertEqual(200, response.status_code)

        responses = response.json()
        cmds = responses['cmds']

        # check the server gave us responses for our messages
        r0 = self.get_response(cmds, '1')
        r1 = self.get_response(cmds, '2')
        r2 = self.get_response(cmds, '3')

        self.assertIsNotNone(r0)
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)

        # first two should have the same server id
        self.assertEqual(r0['extra'], r1['extra'])

        # One was a duplicate, should only have 2
        self.assertEqual(2, Msg.objects.filter(direction='I').count())

    def get_response(self, responses, p_id):
        for response in responses:
            if 'p_id' in response and response['p_id'] == p_id:
                return response

    @patch('nexmo.Client.update_call')
    @patch('nexmo.Client.create_application')
    def test_get_ivr_client(self, mock_create_application, mock_update_call):
        mock_create_application.return_value = dict(id='app-id', keys=dict(private_key='private-key'))
        mock_update_call.return_value = dict(uuid='12345')

        channel = Channel.create(self.org, self.user, 'RW', 'A', "Tigo", "+250725551212", secret="11111", gcm_id="456")
        self.assertIsNone(channel.get_ivr_client())

        self.org.connect_nexmo('123', '456', self.admin)
        self.org.save()

        channel.channel_type = 'NX'
        channel.save()

        self.assertIsNotNone(channel.get_ivr_client())

        channel.release()
        self.assertIsNone(channel.get_ivr_client())

    def test_channel_status_processor(self):

        request = RequestFactory().get('/')
        request.user = self.admin

        def get_context(channel_type, role):
            Channel.objects.all().delete()
            Channel.create(
                self.org, self.admin, 'RW', channel_type, None, '1234',
                config=dict(username='junebug-user', password='junebug-pass', send_url='http://example.org/'),
                uuid='00000000-0000-0000-0000-000000001234', role=role)
            return channel_status_processor(request)

        Channel.objects.all().delete()
        no_channel_context = channel_status_processor(request)
        self.assertFalse(no_channel_context['has_outgoing_channel'])
        self.assertEqual(no_channel_context['is_ussd_channel'], False)

        sms_context = get_context('JN', Channel.ROLE_SEND)
        self.assertTrue(sms_context['has_outgoing_channel'])
        self.assertEqual(sms_context['is_ussd_channel'], False)

        ussd_context = get_context('JNU', Channel.ROLE_USSD)
        self.assertTrue(ussd_context['has_outgoing_channel'])
        self.assertEqual(ussd_context['is_ussd_channel'], True)

    def test_send_message_chatbase(self):
        Channel.create(self.org, self.user, None, 'FCM', 'FCM Channel', 'fcm-channel',
                       config=dict(FCM_KEY='123456789', FCM_TITLE='FCM Channel', FCM_NOTIFICATION=True),
                       uuid='00000000-0000-0000-0000-000000001234')

        org_config = self.org.config
        org_config.update(dict(CHATBASE_API_KEY='123456abcdef', CHATBASE_VERSION='1.0'))
        self.org.config = org_config
        self.org.save()

        self.assertTrue(self.org.get_chatbase_credentials())
        self.assertEqual(self.org.config['CHATBASE_API_KEY'], '123456abcdef')
        self.assertEqual(self.org.config['CHATBASE_VERSION'], '1.0')

        with self.settings(SEND_CHATBASE=True):
            joe = self.create_contact("Joe", urn="fcm:forrest_gump", auth="1234567890")
            msg = joe.send("Hello, world!", self.admin, trigger_send=False)[0]

            with self.settings(SEND_MESSAGES=True):
                with patch('requests.post') as mock:
                    mock.return_value = MockResponse(200, '{ "success": 1, "multicast_id": 123456, "failures": 0 }')

                    Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                    # check the status of the message is now sent
                    msg.refresh_from_db()
                    self.assertEqual(msg.status, WIRED)
                    self.assertTrue(msg.sent_on)


class ChannelBatchTest(TembaTest):

    def test_time_utils(self):
        now = timezone.now()
        now = now.replace(microsecond=now.microsecond // 1000 * 1000)

        epoch = datetime_to_ms(now)
        self.assertEqual(ms_to_datetime(epoch), now)


class ChannelEventTest(TembaTest):

    def test_create(self):
        now = timezone.now()
        event = ChannelEvent.create(self.channel, "tel:+250783535665", ChannelEvent.TYPE_CALL_OUT, now, extra=dict(duration=300))

        contact = Contact.objects.get()
        self.assertEqual(six.text_type(contact.get_urn()), "tel:+250783535665")

        self.assertEqual(event.org, self.org)
        self.assertEqual(event.channel, self.channel)
        self.assertEqual(event.contact, contact)
        self.assertEqual(event.event_type, ChannelEvent.TYPE_CALL_OUT)
        self.assertEqual(event.occurred_on, now)
        self.assertEqual(event.extra['duration'], 300)


class ChannelEventCRUDLTest(TembaTest):

    def test_calls(self):
        now = timezone.now()
        ChannelEvent.create(self.channel, "tel:12345", ChannelEvent.TYPE_CALL_IN, now, dict(duration=600))
        ChannelEvent.create(self.channel, "tel:890", ChannelEvent.TYPE_CALL_IN_MISSED, now)
        ChannelEvent.create(self.channel, "tel:456767", ChannelEvent.TYPE_UNKNOWN, now)

        list_url = reverse('channels.channelevent_calls')

        response = self.fetch_protected(list_url, self.user)

        self.assertEqual(response.context['object_list'].count(), 2)
        self.assertContains(response, "Missed Incoming Call")
        self.assertContains(response, "Incoming Call (600 seconds)")


class SyncEventTest(SmartminTest):

    def setUp(self):
        self.superuser = User.objects.create_superuser(username="super", email="super@user.com", password="super")
        self.user = self.create_user("tito")
        self.org = Org.objects.create(name="Temba", timezone="Africa/Kigali", created_by=self.user, modified_by=self.user)
        self.tel_channel = Channel.create(self.org, self.user, 'RW', 'A', "Test Channel", "0785551212",
                                          secret="12345", gcm_id="123")

    def test_sync_event_model(self):
        self.sync_event = SyncEvent.create(self.tel_channel, dict(p_src="AC", p_sts="DIS", p_lvl=80, net="WIFI",
                                                                  pending=[1, 2], retry=[3, 4], cc='RW'), [1, 2])
        self.assertEqual(SyncEvent.objects.all().count(), 1)
        self.assertEqual(self.sync_event.get_pending_messages(), [1, 2])
        self.assertEqual(self.sync_event.get_retry_messages(), [3, 4])
        self.assertEqual(self.sync_event.incoming_command_count, 0)

        self.sync_event = SyncEvent.create(self.tel_channel, dict(p_src="AC", p_sts="DIS", p_lvl=80, net="WIFI",
                                                                  pending=[1, 2], retry=[3, 4], cc='US'), [1])
        self.assertEqual(self.sync_event.incoming_command_count, 0)
        self.tel_channel = Channel.objects.get(pk=self.tel_channel.pk)

        # we shouldn't update country once the relayer is claimed
        self.assertEqual('RW', self.tel_channel.country)


class ChannelAlertTest(TembaTest):

    def test_no_alert_email(self):
        # set our last seen to a while ago
        self.channel.last_seen = timezone.now() - timedelta(minutes=40)
        self.channel.save()

        check_channels_task()
        self.assertTrue(len(mail.outbox) == 0)

        # add alert email, remove org and set last seen to now to force an resolve email to try to send
        self.channel.alert_email = 'fred@unicef.org'
        self.channel.org = None
        self.channel.last_seen = timezone.now()
        self.channel.save()
        check_channels_task()

        self.assertTrue(len(mail.outbox) == 0)


class ChannelClaimTest(TembaTest):

    @override_settings(SEND_EMAILS=True)
    def test_disconnected_alert(self):
        # set our last seen to a while ago
        self.channel.alert_email = 'fred@unicef.org'
        self.channel.last_seen = timezone.now() - timedelta(minutes=40)
        self.channel.save()

        branding = copy.deepcopy(settings.BRANDING)
        branding['rapidpro.io']['from_email'] = 'support@mybrand.com'
        with self.settings(BRANDING=branding):
            check_channels_task()

            # should have created one alert
            alert = Alert.objects.get()
            self.assertEqual(self.channel, alert.channel)
            self.assertEqual(Alert.TYPE_DISCONNECTED, alert.alert_type)
            self.assertFalse(alert.ended_on)

            self.assertTrue(len(mail.outbox) == 1)
            template = 'channels/email/disconnected_alert.txt'
            context = dict(org=self.channel.org, channel=self.channel, now=timezone.now(),
                           branding=self.channel.org.get_branding(),
                           last_seen=self.channel.last_seen, sync=alert.sync_event)

            text_template = loader.get_template(template)
            text = text_template.render(context)

            self.assertEqual(mail.outbox[0].body, text)
            self.assertEqual(mail.outbox[0].from_email, 'support@mybrand.com')

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
        template = 'channels/email/connected_alert.txt'
        context = dict(org=self.channel.org, channel=self.channel, now=timezone.now(),
                       branding=self.channel.org.get_branding(),
                       last_seen=self.channel.last_seen, sync=alert.sync_event)

        text_template = loader.get_template(template)
        text = text_template.render(context)

        self.assertEqual(mail.outbox[1].body, text)

    @override_settings(SEND_EMAILS=True)
    def test_sms_alert(self):
        contact = self.create_contact("John Doe", '123')

        # create a message from two hours ago
        one_hour_ago = timezone.now() - timedelta(hours=1)
        two_hours_ago = timezone.now() - timedelta(hours=2)
        three_hours_ago = timezone.now() - timedelta(hours=3)
        four_hours_ago = timezone.now() - timedelta(hours=4)
        five_hours_ago = timezone.now() - timedelta(hours=5)
        six_hours_ago = timezone.now() - timedelta(hours=6)

        msg1 = self.create_msg(text="Message One", contact=contact, created_on=five_hours_ago, status='Q')

        # make sure our channel has been seen recently
        self.channel.last_seen = timezone.now()
        self.channel.alert_email = 'fred@unicef.org'
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

        dany = self.create_contact("Dany Craig", "765")

        # let have a recent sent message
        sent_msg = self.create_msg(text="SENT Message", contact=dany, created_on=four_hours_ago, sent_on=one_hour_ago, status='D')

        # ok check on our channel
        check_channels_task()

        # if latest_sent_message is after our queued message no alert is created
        self.assertEqual(Alert.objects.all().count(), 1)

        # consider the sent message was sent before our queued msg
        sent_msg.sent_on = three_hours_ago
        sent_msg.save()

        msg1.delete()
        msg1 = self.create_msg(text="Message One", contact=contact, created_on=two_hours_ago, status='Q')

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

        # run again, nothing should change
        check_channels_task()

        alert = Alert.objects.get(ended_on=None)
        self.assertFalse(alert.ended_on)
        self.assertTrue(len(mail.outbox) == 2)

        # fix our message
        msg1.status = 'D'
        msg1.save()

        # run again, our alert should end
        check_channels_task()

        # still only one alert though, and no new email sent, alert must not be ended before one hour
        alert = Alert.objects.all().latest('ended_on')
        self.assertTrue(alert.ended_on)
        self.assertTrue(len(mail.outbox) == 2)


class ChannelCountTest(TembaTest):

    def assertDailyCount(self, channel, assert_count, count_type, day):
        calculated_count = ChannelCount.get_day_count(channel, count_type, day)
        self.assertEqual(assert_count, calculated_count)

    def test_daily_counts(self):
        # test that messages to test contacts aren't counted
        self.admin.set_org(self.org)
        test_contact = Contact.get_test_contact(self.admin)
        Msg.create_outgoing(self.org, self.admin, test_contact, "Test Message", channel=self.channel)

        # no channel counts
        self.assertFalse(ChannelCount.objects.all())

        # real contact, but no channel
        Msg.create_incoming(None, 'tel:+250788111222', "Test Message", org=self.org)

        # still no channel counts
        self.assertFalse(ChannelCount.objects.all())

        # incoming msg with a channel
        msg = Msg.create_incoming(self.channel, 'tel:+250788111222', "Test Message", org=self.org)
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        # insert another
        msg = Msg.create_incoming(self.channel, 'tel:+250788111222', "Test Message", org=self.org)
        self.assertDailyCount(self.channel, 2, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        # squash our counts
        squash_channelcounts()

        # same count
        self.assertDailyCount(self.channel, 2, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        # and only one channel count
        self.assertEqual(ChannelCount.objects.all().count(), 1)

        # deleting a message doesn't decrement the count
        msg.delete()
        self.assertDailyCount(self.channel, 2, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        ChannelCount.objects.all().delete()

        # ok, test outgoing now
        real_contact, urn_obj = Contact.get_or_create(self.org, 'tel:+250788111222', user=self.admin)
        msg = Msg.create_outgoing(self.org, self.admin, real_contact, "Real Message", channel=self.channel)
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
        msg.delete()
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_MSG_TYPE, msg.created_on.date())

        ChannelCount.objects.all().delete()

        # incoming IVR
        msg = Msg.create_incoming(self.channel, 'tel:+250788111222',
                                  "Test Message", org=self.org, msg_type=IVR)
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_IVR_TYPE, msg.created_on.date())

        # delete it, should be gone now
        msg.delete()
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_IVR_TYPE, msg.created_on.date())

        ChannelCount.objects.all().delete()

        # outgoing ivr
        msg = Msg.create_outgoing(self.org, self.admin, real_contact, "Real Voice",
                                  channel=self.channel, msg_type=IVR)
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_IVR_TYPE, msg.created_on.date())

        # delete it, should be gone now
        msg.delete()
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_IVR_TYPE, msg.created_on.date())


class AfricasTalkingTest(TembaTest):

    def setUp(self):
        super(AfricasTalkingTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'KE', 'AT', None, '+250788123123',
                                      config=dict(username='at-user', api_key='africa-key'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_delivery(self):
        # ok, what happens with an invalid uuid?
        post_data = dict(id="external1", status="Success")
        response = self.client.post(reverse('handlers.africas_talking_handler', args=['delivery', 'not-real-uuid']), post_data)
        self.assertEqual(404, response.status_code)

        # ok, try with a valid uuid, but invalid message id
        delivery_url = reverse('handlers.africas_talking_handler', args=['delivery', self.channel.uuid])
        response = self.client.post(delivery_url, post_data)
        self.assertEqual(404, response.status_code)

        # requires posts
        delivery_url = reverse('handlers.africas_talking_handler', args=['delivery', self.channel.uuid])
        response = self.client.get(delivery_url, post_data)
        self.assertEqual(400, response.status_code)

        # missing status
        del post_data['status']
        response = self.client.post(delivery_url, post_data)
        self.assertEqual(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]
        msg.external_id = "external1"
        msg.save(update_fields=('external_id',))

        def assertStatus(sms, post_status, assert_status):
            post_data['status'] = post_status
            response = self.client.post(delivery_url, post_data)
            self.assertEqual(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEqual(assert_status, sms.status)

        assertStatus(msg, 'Success', DELIVERED)
        assertStatus(msg, 'Sent', SENT)
        assertStatus(msg, 'Buffered', SENT)
        assertStatus(msg, 'Failed', ERRORED)
        assertStatus(msg, 'Rejected', ERRORED)

        msg.error_count = 3
        msg.save()

        assertStatus(msg, 'Failed', FAILED)
        assertStatus(msg, 'Rejected', FAILED)

    def test_callback(self):
        post_data = {'from': "0788123123", 'text': "Hello World"}
        callback_url = reverse('handlers.africas_talking_handler', args=['callback', self.channel.uuid])

        # missing test data
        response = self.client.post(callback_url, dict())
        self.assertEqual(400, response.status_code)

        response = self.client.post(callback_url, post_data)
        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual("+254788123123", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World", msg.text)

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps(dict(SMSMessageData=dict(Recipients=[dict(messageId='msg1', status='Success')]))))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertEqual('msg1', msg.external_id)

            # check that our from was set
            self.assertEqual(self.channel.address, mock.call_args[1]['data']['from'])

            self.clear_cache()

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps(
                dict(SMSMessageData=dict(Recipients=[dict(messageId='msg1', status='Could Not Send')]))))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

            self.clear_cache()

        # test with a non-dedicated shortcode
        self.channel.config = dict(username='at-user', api_key='africa-key', is_shared=True)
        self.channel.save()

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps(dict(SMSMessageData=dict(Recipients=[dict(messageId='msg1', status='Success')]))))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # assert we didn't send the short code in our data
            self.assertNotIn('from', mock.call_args[1]['data'])
            self.clear_cache()

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(400, "Error", method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

        with patch('requests.post') as mock:
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(FAILED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps(dict(SMSMessageData=dict(Recipients=[dict(messageId='msg1', status='Success')]))))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertEqual('msg1', msg.external_id)

            # check that our from was set
            self.assertEqual(self.channel.address, mock.call_args[1]['data']['from'])
            self.assertEqual(mock.call_args[1]['data']['message'],
                             "Test message\nhttps://example.com/attachments/pic.jpg")

            self.clear_cache()


class RedRabbitTest(TembaTest):

    def setUp(self):
        super(RedRabbitTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'BR', 'RR', None, '+250788123123', schemes=['tel'],
                                      config={Channel.CONFIG_USERNAME: 'username', Channel.CONFIG_PASSWORD: 'password'},
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with self.settings(SEND_MESSAGES=True):
            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "Sent")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                self.assertEqual(mock.call_args[0][0], 'http://http1.javna.com/epicenter/GatewaySendG.asp')
                self.assertNotIn('Msgtyp', mock.call_args[1]['params'])

        self.clear_cache()

        # > 160 chars
        msg.text += "x" * 170
        with self.settings(SEND_MESSAGES=True):
            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "Sent")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                self.assertEqual(mock.call_args[0][0], 'http://http1.javna.com/epicenter/GatewaySendG.asp')
                self.assertEqual(5, mock.call_args[1]['params']['Msgtyp'])

        self.clear_cache()

        # unicode
        msg.text = "التوطين"
        with self.settings(SEND_MESSAGES=True):
            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "Sent")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                self.assertEqual(mock.call_args[0][0], 'http://http1.javna.com/epicenter/GatewaySendG.asp')
                self.assertEqual(9, mock.call_args[1]['params']['Msgtyp'])

        self.clear_cache()

        # unicode > 1 msg
        msg.text += "x" * 80
        with self.settings(SEND_MESSAGES=True):
            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "Sent")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                self.assertEqual(mock.call_args[0][0], 'http://http1.javna.com/epicenter/GatewaySendG.asp')
                self.assertEqual(10, mock.call_args[1]['params']['Msgtyp'])


class ExternalTest(TembaTest):

    def setUp(self):
        super(ExternalTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'BR', 'EX', None, '+250788123123', schemes=['tel'],
                                      config={Channel.CONFIG_SEND_URL: 'http://foo.com/send', Channel.CONFIG_SEND_METHOD: 'POST'},
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_status(self):
        # try with an invalid channel
        response = self.client.post(reverse('handlers.external_handler', args=['sent', 'not-real-uuid']), dict(id="-1"))
        self.assertEqual(response.status_code, 400)

        delivery_url = reverse('handlers.external_handler', args=['sent', self.channel.uuid])
        joe = self.create_contact("Joe Biden", "+254788383383")

        # try with missing message id
        response = self.client.post(delivery_url, {})
        self.assertEqual(response.status_code, 400)

        # try with an invalid message id
        response = self.client.post(delivery_url, {'id': -1234})
        self.assertEqual(response.status_code, 400)

        # try with an incoming message id
        incoming = self.create_msg(direction='I', contact=joe, text="It's me")
        response = self.client.post(delivery_url, {'id': incoming.id})
        self.assertEqual(response.status_code, 400)

        # ok, lets create an outgoing message to update
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]
        payload = {'id': msg.id}

        def assertStatus(sms, status, assert_status):
            resp = self.client.post(reverse('handlers.external_handler', args=[status, self.channel.uuid]), payload)
            self.assertEqual(200, resp.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEqual(assert_status, sms.status)

        assertStatus(msg, 'delivered', DELIVERED)
        assertStatus(msg, 'sent', SENT)
        assertStatus(msg, 'failed', FAILED)

        # check when called with phone number rather than UUID
        response = self.client.post(reverse('handlers.external_handler', args=['sent', '250788123123']), {'id': msg.pk})
        self.assertEqual(200, response.status_code)
        msg.refresh_from_db()
        self.assertEqual(msg.status, SENT)

    def test_receive(self):
        data = {'from': '5511996458779', 'text': 'Hello World!'}
        callback_url = reverse('handlers.external_handler', args=['received', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual("+5511996458779", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World!", msg.text)

        data = {'from': "", 'text': "Hi there"}
        response = self.client.post(callback_url, data)

        self.assertEqual(400, response.status_code)

        Msg.objects.all().delete()

        # receive with a date
        data = {'from': '5511996458779', 'text': 'Hello World!', 'date': '2012-04-23T18:25:43.511Z'}
        callback_url = reverse('handlers.external_handler', args=['received', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEqual(200, response.status_code)

        # load our message, make sure the date was saved properly
        msg = Msg.objects.get()
        self.assertEqual(2012, msg.sent_on.year)
        self.assertEqual(18, msg.sent_on.hour)

        Msg.objects.all().delete()

        data = {'from': '5511996458779', 'text': 'Hello World!', 'date': '2012-04-23T18:25:43Z'}
        response = self.client.post(callback_url, data)

        self.assertContains(response, "Bad parameter error: time data '2012-04-23T18:25:43Z' "
                            "does not match format '%Y-%m-%dT%H:%M:%S.%fZ'", status_code=400)
        self.assertFalse(Msg.objects.all())

    def test_receive_external(self):
        self.channel.schemes = ['ext']
        self.channel.save()

        data = {'from': 'lynch24', 'text': 'Beast Mode!'}
        callback_url = reverse('handlers.external_handler', args=['received', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEqual(200, response.status_code)

        # check our message
        msg = Msg.objects.get()
        self.assertEqual('lynch24', msg.contact.get_urn(EXTERNAL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual('Beast Mode!', msg.text)

    def test_send_replacement(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        self.channel.config = {Channel.CONFIG_SEND_URL: 'http://foo.com/send&text={{text}}&to={{to_no_plus}}',
                               Channel.CONFIG_SEND_METHOD: 'GET'}
        self.channel.save()

        with self.settings(SEND_MESSAGES=True):
            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "Sent")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                self.assertEqual(mock.call_args[0][0], 'http://foo.com/send&text=Test+message&to=250788383383')

        self.channel.config = {Channel.CONFIG_SEND_URL: 'http://foo.com/send', Channel.CONFIG_SEND_METHOD: 'POST'}
        self.channel.save()
        self.clear_cache()

        with self.settings(SEND_MESSAGES=True):
            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, "Sent")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                self.assertEqual(mock.call_args[0][0], 'http://foo.com/send')
                self.assertEqual(mock.call_args[1]['data'], force_bytes('id=%d&text=Test+message&to=%%2B250788383383&to_no_plus=250788383383&'
                                                                        'from=%%2B250788123123&from_no_plus=250788123123&'
                                                                        'channel=%d' % (msg.id, self.channel.id)))

        self.channel.config = {Channel.CONFIG_SEND_URL: 'http://foo.com/send',
                               Channel.CONFIG_SEND_BODY: '{ "text": {{text}}, "to": {{to_no_plus}} }',
                               Channel.CONFIG_CONTENT_TYPE: Channel.CONTENT_TYPE_JSON,
                               Channel.CONFIG_SEND_METHOD: 'PUT'}

        self.channel.save()
        self.clear_cache()

        with self.settings(SEND_MESSAGES=True):
            with patch('requests.put') as mock:
                mock.return_value = MockResponse(200, "Sent")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                self.assertEqual(mock.call_args[0][0], 'http://foo.com/send')
                self.assertEqual(mock.call_args[1]['data'], force_bytes('{ "text": "Test message", "to": "250788383383" }'))
                self.assertEqual(mock.call_args[1]['headers']['Content-Type'], "application/json")

        self.channel.config = {Channel.CONFIG_SEND_URL: 'http://foo.com/send',
                               Channel.CONFIG_SEND_BODY: 'text={{text}}&to={{to_no_plus}}',
                               Channel.CONFIG_SEND_METHOD: 'POST',
                               Channel.CONFIG_MAX_LENGTH: 320}

        msg.text = "A" * 180
        msg.save()
        self.channel.save()
        self.clear_cache()

        with self.settings(SEND_MESSAGES=True):
            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, "Sent")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                self.assertEqual(mock.call_args[0][0], 'http://foo.com/send')
                self.assertEqual(mock.call_args[1]['data'], force_bytes('text=' + msg.text + '&to=250788383383'))

        self.channel.config = {Channel.CONFIG_SEND_URL: 'http://foo.com/send',
                               Channel.CONFIG_SEND_BODY: '<msg><text>{{text}}</text><to>{{to_no_plus}}</to></msg>',
                               Channel.CONFIG_CONTENT_TYPE: Channel.CONTENT_TYPE_XML,
                               Channel.CONFIG_SEND_METHOD: 'PUT'}
        self.channel.save()

        arabic = "التوطين"
        msg.text = arabic
        msg.save()
        self.clear_cache()

        with self.settings(SEND_MESSAGES=True):
            with patch('requests.put') as mock:
                mock.return_value = MockResponse(200, "Sent")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                self.assertEqual(mock.call_args[0][0], 'http://foo.com/send')
                self.assertEqual(mock.call_args[1]['data'], force_bytes('<msg><text>التوطين</text><to>250788383383</to></msg>'))
                self.assertEqual(mock.call_args[1]['headers']['Content-Type'], Channel.CONTENT_TYPES[Channel.CONTENT_TYPE_XML])

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, "Sent")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            self.assertIn(force_bytes("text=Test+message"), mock.call_args[1]['data'])

            self.clear_cache()

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(400, "Error")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

        with patch('requests.post') as mock:
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

        # view the log item for our send
        self.login(self.admin)
        log_item = ChannelLog.objects.all().order_by('created_on').first()
        response = self.client.get(reverse('channels.channellog_read', args=[log_item.pk]))
        self.assertEqual(response.context['object'].description, 'Successfully delivered')

        # test our log items if we don't have a message set (should only list the single message)
        log_item.msg = None
        log_item.save(update_fields=['msg'])
        self.assertEqual([log_item], list(log_item.log_group()))

        # make sure we can't see it as anon
        self.org.is_anon = True
        self.org.save()

        response = self.client.get(reverse('channels.channellog_read', args=[log_item.pk]))
        self.assertEqual(302, response.status_code)

        # change our admin to be a CS rep, see if they can see the page
        self.admin.groups.add(Group.objects.get(name='Customer Support'))
        response = self.client.get(reverse('channels.channellog_read', args=[log_item.pk]))
        self.assertEqual(response.context['object'].description, 'Successfully delivered')

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, "Sent")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            self.assertIn(force_bytes("text=Test+message%0Ahttps%3A%2F%2Fexample.com%2Fattachments%2Fpic.jpg"), mock.call_args[1]['data'])

            self.clear_cache()


class VerboiceTest(TembaTest):
    def setUp(self):
        super(VerboiceTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'US', 'VB', None, '+250788123123',
                                      config=dict(username='test', password='sesame'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_receive(self):
        callback_url = reverse('handlers.verboice_handler', args=['status', self.channel.uuid])

        response = self.client.post(callback_url, dict())
        self.assertEqual(response.status_code, 405)

        response = self.client.get(callback_url)
        self.assertEqual(response.status_code, 400)

        response = self.client.get(callback_url + "?From=250788456456&CallStatus=ringing&CallSid=12345")
        self.assertEqual(response.status_code, 400)

        contact = self.create_contact('Bruno Mars', '+252788123123')

        call = IVRCall.create_outgoing(self.channel, contact, contact.get_urn(TEL_SCHEME), self.admin)
        call.external_id = "12345"
        call.save()

        self.assertEqual(call.status, IVRCall.PENDING)

        response = self.client.get(callback_url + "?From=250788456456&CallStatus=ringing&CallSid=12345")

        self.assertEqual(response.status_code, 200)
        call = IVRCall.objects.get(pk=call.pk)
        self.assertEqual(call.status, IVRCall.RINGING)


class YoTest(TembaTest):
    def setUp(self):
        super(YoTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'BR', 'YO', None, '+250788123123',
                                      config=dict(username='test', password='sesame'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_receive(self):
        callback_url = reverse('handlers.yo_handler', args=['received', self.channel.uuid])
        response = self.client.get(callback_url + "?sender=252788123123&message=Hello+World")

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual("+252788123123", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World", msg.text)

        # fails if missing sender
        response = self.client.get(callback_url + "?sender=252788123123")
        self.assertEqual(400, response.status_code)

        # fails if missing message
        response = self.client.get(callback_url + "?message=Hello+World")
        self.assertEqual(400, response.status_code)

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+252788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, "ybs_autocreate_status=OK")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)
            self.assertTrue(msg.sent_on)

            self.assertIn("sms_content=Test+message", mock.call_args[0][0])

            self.clear_cache()

        with patch('requests.get') as mock:
            mock.side_effect = [MockResponse(401, "Error"), MockResponse(200, 'ybs_autocreate_status=OK')]

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)
            self.assertTrue(msg.sent_on)

            # check that requests was called twice, using the backup URL the second time
            self.assertEqual(2, mock.call_count)
            self.clear_cache()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(400, "Kaboom")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

            self.clear_cache()

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, "ybs_autocreate_status=ERROR&ybs_autocreate_message=" +
                                                  "YBS+AutoCreate+Subsystem%3A+Access+denied" +
                                                  "+due+to+wrong+authorization+code")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            # contact should not be stopped
            joe.refresh_from_db()
            self.assertFalse(joe.is_stopped)

            self.clear_cache()

        with patch('requests.get') as mock:
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(FAILED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            # contact should not be stopped
            joe.refresh_from_db()
            self.assertFalse(joe.is_stopped)

            self.clear_cache()

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, "ybs_autocreate_status=ERROR&ybs_autocreate_message=" +
                                             "256794224665%3ABLACKLISTED")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as a failure
            msg.refresh_from_db()
            self.assertEqual(FAILED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            # contact should also be stopped
            joe.refresh_from_db()
            self.assertTrue(joe.is_stopped)

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+252788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, "ybs_autocreate_status=OK")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)
            self.assertTrue(msg.sent_on)

            self.assertIn("sms_content=Test+message%0Ahttps%3A%2F%2Fexample.com%2Fattachments%2Fpic.jpg", mock.call_args[0][0])

            self.clear_cache()


class ShaqodoonTest(TembaTest):

    def setUp(self):
        super(ShaqodoonTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'SO', 'SQ', None, '+250788123123',
                                      config={Channel.CONFIG_SEND_URL: 'http://foo.com/send',
                                              Channel.CONFIG_USERNAME: 'username',
                                              Channel.CONFIG_PASSWORD: 'password'},
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_receive(self):
        data = {'from': '252788123456', 'text': 'Hello World!'}
        callback_url = reverse('handlers.shaqodoon_handler', args=['received', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual("+252788123456", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World!", msg.text)

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message ☺", self.admin, trigger_send=False)[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, "Sent")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            self.assertIn("msg=Test+message", mock.call_args[0][0])

            self.clear_cache()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(400, "Error")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

        with patch('requests.get') as mock:
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, "Sent")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertIn("msg=Test+message%0Ahttps%3A%2F%2Fexample.com%2Fattachments%2Fpic.jpg", mock.call_args[0][0])
            self.clear_cache()


class M3TechTest(TembaTest):

    def setUp(self):
        super(M3TechTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'PK', 'M3', None, '+250788123123',
                                      config={Channel.CONFIG_USERNAME: 'username', Channel.CONFIG_PASSWORD: 'password'},
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_receive(self):
        data = {'from': '252788123456', 'text': 'Hello World!'}
        callback_url = reverse('handlers.m3tech_handler', args=['received', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual("+252788123456", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World!", msg.text)

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message ☺", self.admin, trigger_send=False)[0]

        with patch('requests.get') as mock:
            msg.text = "Test message"
            mock.return_value = MockResponse(200,
                                             """[{"Response":"0"}]""")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            self.assertEqual(mock.call_args[1]['params']['SMSType'], '0')

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            self.assertEqual(mock.call_args[1]['params']['SMS'], 'Test message')

            self.clear_cache()

        with patch('requests.get') as mock:
            msg.text = "Test message ☺"
            mock.return_value = MockResponse(200,
                                             """[{"Response":"0"}]""")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            self.assertEqual(mock.call_args[1]['params']['SMSType'], '7')

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            self.clear_cache()

        # bogus json
        with patch('requests.get') as mock:
            msg.text = "Test message"
            mock.return_value = MockResponse(200, """["bad json":}]""")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.clear_cache()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(400, "Error")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            self.clear_cache()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, """[{"Response":"1"}]""")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(FAILED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            self.clear_cache()

        with patch('requests.get') as mock:
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(FAILED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

            self.clear_cache()

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.get') as mock:
            msg.text = "Test message"
            mock.return_value = MockResponse(200,
                                             """[{"Response":"0"}]""")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            self.assertEqual(mock.call_args[1]['params']['SMSType'], '0')
            self.assertEqual(mock.call_args[1]['params']['SMS'],
                             'Test message\nhttps://example.com/attachments/pic.jpg')

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            self.clear_cache()


class KannelTest(TembaTest):

    def setUp(self):
        super(KannelTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'KN', None, '+250788123123',
                                      config=dict(username='kannel-user', password='kannel-pass', send_url='http://foo/'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_status(self):
        # ok, what happens with an invalid uuid?
        data = dict(id="-1", status="4")
        response = self.client.post(reverse('handlers.kannel_handler', args=['status', 'not-real-uuid']), data)
        self.assertEqual(400, response.status_code)

        # ok, try with a valid uuid, but invalid message id -1
        delivery_url = reverse('handlers.kannel_handler', args=['status', self.channel.uuid])
        response = self.client.post(delivery_url, data)
        self.assertEqual(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]

        data['id'] = msg.pk

        def assertStatus(sms, status, assert_status):
            data['status'] = status
            response = self.client.post(reverse('handlers.kannel_handler', args=['status', self.channel.uuid]), data)
            self.assertEqual(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEqual(assert_status, sms.status)

        assertStatus(msg, '4', SENT)
        assertStatus(msg, '1', DELIVERED)
        assertStatus(msg, '16', ERRORED)

        # fail after 3 retries
        msg.error_count = 3
        msg.save()
        assertStatus(msg, '16', FAILED)

    def test_receive(self):
        data = {
            'sender': '0788383383',
            'message': 'Hello World!',
            'id': 'external1',
            'ts': int(calendar.timegm(time.gmtime()))
        }
        callback_url = reverse('handlers.kannel_handler', args=['receive', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual("+250788383383", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World!", msg.text)

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, 'Accepted 201')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            # assert verify was set to true
            self.assertTrue(mock.call_args[1]['verify'])
            self.assertEqual('+250788383383', mock.call_args[1]['params']['to'])
            self.assertEqual(mock.call_args[1]['params']['text'],
                             'Test message\nhttps://example.com/attachments/pic.jpg')

            self.clear_cache()

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, 'Accepted 201')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            # assert verify was set to true
            self.assertTrue(mock.call_args[1]['verify'])
            self.assertEqual('+250788383383', mock.call_args[1]['params']['to'])
            self.assertEqual(mock.call_args[1]['params']['text'], 'Test message')
            self.clear_cache()

        self.channel.config = dict(username='kannel-user', password='kannel-pass',
                                   encoding=Channel.ENCODING_SMART, use_national=True,
                                   send_url='http://foo/', verify_ssl=False)
        self.channel.save()

        msg.text = "No capital accented È!"
        msg.save()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, 'Accepted 201')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            # assert verify was set to true
            self.assertEqual('No capital accented E!', mock.call_args[1]['params']['text'])
            self.assertEqual('788383383', mock.call_args[1]['params']['to'])
            self.assertNotIn('coding', mock.call_args[1]['params'])
            self.assertNotIn('priority', mock.call_args[1]['params'])
            self.clear_cache()

        incoming = Msg.create_incoming(self.channel, "tel:+250788383383", "start")
        msg.text = "Unicode. ☺"
        msg.response_to = incoming
        msg.save()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, 'Accepted 201')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            # assert verify was set to true
            self.assertEqual("Unicode. ☺", mock.call_args[1]['params']['text'])
            self.assertEqual('2', mock.call_args[1]['params']['coding'])
            self.assertEqual('utf8', mock.call_args[1]['params']['charset'])
            self.assertEqual(1, mock.call_args[1]['params']['priority'])

            self.clear_cache()

        msg.text = "Normal"
        msg.save()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, 'Accepted 201')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            # assert verify was set to true
            self.assertEqual("Normal", mock.call_args[1]['params']['text'])
            self.assertNotIn('coding', mock.call_args[1]['params'])
            self.assertNotIn('charset', mock.call_args[1]['params'])
            self.assertEqual('https://%s/c/kn/%s/status?id=%d&status=%%d' % (self.org.get_brand_domain(), self.channel.uuid, msg.id), mock.call_args[1]['params']['dlr-url'])

            self.clear_cache()

        self.channel.config = dict(username='kannel-user', password='kannel-pass',
                                   encoding=Channel.ENCODING_UNICODE,
                                   callback_domain='custom-domain.io',
                                   send_url='http://foo/', verify_ssl=False)
        self.channel.save()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, 'Accepted 201')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            # assert verify was set to true
            self.assertEqual("Normal", mock.call_args[1]['params']['text'])
            self.assertEqual('2', mock.call_args[1]['params']['coding'])
            self.assertEqual('utf8', mock.call_args[1]['params']['charset'])
            self.assertEqual('https://custom-domain.io/c/kn/%s/status?id=%d&status=%%d' % (self.channel.uuid, msg.id), mock.call_args[1]['params']['dlr-url'])

            self.clear_cache()

        self.channel.config = dict(username='kannel-user', password='kannel-pass',
                                   send_url='http://foo/', verify_ssl=False)
        self.channel.save()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(400, "Error")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # assert verify was set to False
            self.assertFalse(mock.call_args[1]['verify'])

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

        with patch('requests.get') as mock:
            mock.side_effect = Exception('Kaboom')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # assert verify was set to False
            self.assertFalse(mock.call_args[1]['verify'])

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))


class NexmoTest(TembaTest):

    def setUp(self):
        super(NexmoTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'NX', None, '+250788123123',
                                      uuid='00000000-0000-0000-0000-000000001234')

        self.nexmo_uuid = str(uuid.uuid4())
        nexmo_config = {NEXMO_KEY: '1234', NEXMO_SECRET: '1234', NEXMO_UUID: self.nexmo_uuid,
                        NEXMO_APP_ID: 'nexmo-app-id', NEXMO_APP_PRIVATE_KEY: 'nexmo-private-key'}

        org = self.channel.org

        config = org.config
        config.update(nexmo_config)
        org.config = config
        org.save()

        self.channel.config = {Channel.CONFIG_NEXMO_APP_ID: nexmo_config[NEXMO_APP_ID],
                               Channel.CONFIG_NEXMO_APP_PRIVATE_KEY: nexmo_config[NEXMO_APP_PRIVATE_KEY],
                               Channel.CONFIG_NEXMO_API_KEY: nexmo_config[NEXMO_KEY],
                               Channel.CONFIG_NEXMO_API_SECRET: nexmo_config[NEXMO_SECRET]}
        self.channel.save()

    def test_status(self):
        # ok, what happens with an invalid uuid and number
        data = dict(to='250788123111', messageId='external1')
        response = self.client.get(reverse('courier.nx', args=['not-real-uuid', 'status']), data)
        self.assertEqual(404, response.status_code)

        # ok, try with a valid uuid, but invalid message id -1, should return 200
        # these are probably multipart message callbacks, which we don't track
        data = dict(to='250788123123', messageId='-1')
        delivery_url = reverse('courier.nx', args=[self.channel.uuid, 'status'])
        response = self.client.get(delivery_url, data)
        self.assertEqual(200, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]
        msg.external_id = 'external1'
        msg.save(update_fields=('external_id',))

        data['messageId'] = 'external1'

        def assertStatus(sms, status, assert_status):
            data['status'] = status
            response = self.client.get(reverse('courier.nx', args=[self.channel.uuid, 'status']), data)
            self.assertEqual(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEqual(assert_status, sms.status)

        assertStatus(msg, 'delivered', DELIVERED)
        assertStatus(msg, 'expired', FAILED)
        assertStatus(msg, 'failed', FAILED)
        assertStatus(msg, 'accepted', SENT)
        assertStatus(msg, 'buffered', SENT)

    def test_receive(self):
        data = dict(to='250788123123', msisdn='250788111222', text='Hello World!', messageId='external1')
        callback_url = reverse('courier.nx', args=[self.channel.uuid, 'receive'])
        response = self.client.get(callback_url, data)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual("+250788111222", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World!", msg.text)
        self.assertEqual('external1', msg.external_id)

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        from temba.orgs.models import NEXMO_KEY, NEXMO_SECRET, NEXMO_APP_ID, NEXMO_APP_PRIVATE_KEY
        org_config = self.org.config
        org_config[NEXMO_KEY] = 'nexmo_key'
        org_config[NEXMO_SECRET] = 'nexmo_secret'
        org_config[NEXMO_APP_ID] = 'nexmo-app-id'
        org_config[NEXMO_APP_PRIVATE_KEY] = 'nexmo-private-key'
        self.org.config = org_config
        self.org.clear_channel_caches()

        self.channel.channel_type = 'NX'
        self.channel.save()

        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        r = get_redis_connection()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, json.dumps(dict(messages=[{'status': 0, 'message-id': 12}])), method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertEqual('12', msg.external_id)

            self.assertEqual(mock.call_args[1]['params']['text'], "Test message")

            self.clear_cache()

            # test some throttling by sending three messages right after another
            start = time.time()
            for i in range(3):
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                r.delete(timezone.now().strftime(MSG_SENT_KEY))

                msg.refresh_from_db()
                self.assertEqual(SENT, msg.status)

            # assert we sent the messages out in a reasonable amount of time
            end = time.time()
            self.assertTrue(2.5 > end - start > 2, "Sending of three messages took: %f" % (end - start))

            self.clear_cache()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, json.dumps(dict(messages=[{'status': 0, 'message-id': 12}])), method='POST')

            msg.text = u"Unicode ☺"
            msg.save()

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertEqual('12', msg.external_id)

            # assert that we were called with unicode
            self.assertEqual(mock.call_args[1]['params']['text'], u'Unicode \u263a')
            self.assertEqual(mock.call_args[1]['params']['from'], u'250788123123')
            self.assertTrue(mock.call_args[1]['params']['callback'])
            self.clear_cache()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(401, "Invalid API token", method='POST')

            # clear out our channel log
            ChannelLog.objects.all().delete()

            # then send it
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check status
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

            # and that we have a decent log
            log = ChannelLog.objects.get(msg=msg)
            self.assertEqual(log.description, "Failed sending message: Invalid API token")

        with patch('requests.get') as mock:
            # this hackery is so that we return a different thing on the second call as the first
            def return_valid(url, params):
                called = getattr(return_valid, 'called', False)

                # on the first call we simulate Nexmo telling us to wait
                if not called:
                    return_valid.called = True
                    err_msg = "Throughput Rate Exceeded - please wait [ 250 ] and retry"
                    return MockResponse(200, json.dumps(dict(messages=[{'status': 1, 'error-text': err_msg}])))

                # on the second, all is well
                else:
                    return MockResponse(200, json.dumps(dict(messages=[{'status': 0, 'message-id': 12}])),
                                        method='POST')
            mock.side_effect = return_valid

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # should be sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)

            self.clear_cache()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(400, "Error", method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        from temba.orgs.models import NEXMO_KEY, NEXMO_SECRET, NEXMO_APP_ID, NEXMO_APP_PRIVATE_KEY
        org_config = self.org.config
        org_config[NEXMO_KEY] = 'nexmo_key'
        org_config[NEXMO_SECRET] = 'nexmo_secret'
        org_config[NEXMO_APP_ID] = 'nexmo-app-id'
        org_config[NEXMO_APP_PRIVATE_KEY] = 'nexmo-private-key'
        self.org.config = org_config
        self.org.clear_channel_caches()

        self.channel.channel_type = 'NX'
        self.channel.save()

        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, json.dumps(dict(messages=[{'status': 0, 'message-id': 12}])), method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertEqual('12', msg.external_id)

            self.assertEqual(mock.call_args[1]['params']['text'],
                             'Test message\nhttps://example.com/attachments/pic.jpg')

            self.clear_cache()


class InfobipTest(TembaTest):

    def setUp(self):
        super(InfobipTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'NG', 'IB', None, '+2347030767144',
                                      config=dict(username='ib-user', password='ib-password'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_received(self):

        now = timezone.now()
        two_hour_ago = now - timedelta(hours=2)

        post_data = {
            "results": [
                {
                    "messageId": "817790313235066447",
                    "from": "2347030767143",
                    "to": "2347030767144",
                    "text": "Hello World",
                    "cleanText": "World",
                    "keyword": "Hello",
                    "receivedAt": two_hour_ago.isoformat(),
                    "smsCount": 1,
                    "price": {
                        "pricePerMessage": 0,
                        "currency": "EUR"
                    },
                    "callbackData": "callbackData"
                }],
            "messageCount": 1,
            "pendingMessageCount": 0
        }

        receive_url = reverse('courier.ib', args=[self.channel.uuid, 'receive'])
        response = self.client.post(receive_url, json.dumps(post_data), content_type='application/json')

        self.assertEqual(201, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual('+2347030767143', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World", msg.text)
        self.assertEqual(two_hour_ago, msg.sent_on)
        self.assertTrue(now > msg.sent_on)
        self.assertTrue(msg.channel_logs.filter(description='Incoming message'))

        Msg.objects.all().delete()

        with patch('iso8601.parse_date') as mock:
            mock.side_effect = [iso8601.ParseError()]

            response = self.client.post(receive_url, json.dumps(post_data), content_type='application/json')

            self.assertEqual(201, response.status_code)

            # load our message
            msg = Msg.objects.get()
            self.assertEqual('+2347030767143', msg.contact.get_urn(TEL_SCHEME).path)
            self.assertEqual(INCOMING, msg.direction)
            self.assertEqual(self.org, msg.org)
            self.assertEqual(self.channel, msg.channel)
            self.assertEqual("Hello World", msg.text)
            self.assertTrue(now < msg.sent_on)

        # try it with an invalid receiver, should fail as UUID and receiver id are mismatched
        post_data['results'][0]['to'] = '2347030767145'
        response = self.client.post(receive_url, json.dumps(post_data), content_type='application/json')

        # should get 404 as the channel wasn't found
        self.assertEqual(404, response.status_code)

        response = self.client.get(receive_url)
        self.assertEqual(405, response.status_code)

        # Invalid JSON should return 400
        response = self.client.post(receive_url, "Invalid", content_type='application/json')
        self.assertEqual(400, response.status_code)

        # ignore when missing results key
        post_data = {
            "unexpected": [
                {
                    "messageId": "817790313235066447",
                    "from": "2347030767143",
                    "to": "2347030767144",
                    "text": "Hello World",
                    "cleanText": "World",
                    "keyword": "Hello",
                    "receivedAt": two_hour_ago.isoformat(),
                    "smsCount": 1,
                    "price": {
                        "pricePerMessage": 0,
                        "currency": "EUR"
                    },
                    "callbackData": "callbackData"
                }],
            "messageCount": 1,
            "pendingMessageCount": 0
        }
        response = self.client.post(receive_url, json.dumps(post_data), content_type='application/json')
        self.assertEqual(400, response.status_code)

        # missing text
        post_data = {
            "results": [
                {
                    "messageId": "817790313235066447",
                    "from": "2347030767143",
                    "to": "2347030767144",
                    "text": None,
                    "cleanText": "World",
                    "keyword": "Hello",
                    "receivedAt": two_hour_ago.isoformat(),
                    "smsCount": 1,
                    "price": {
                        "pricePerMessage": 0,
                        "currency": "EUR"
                    },
                    "callbackData": "callbackData"
                }],
            "messageCount": 1,
            "pendingMessageCount": 0
        }

        response = self.client.post(receive_url, json.dumps(post_data), content_type='application/json')
        self.assertEqual(404, response.status_code)

    def test_delivered(self):
        contact = self.create_contact("Joe", '+2347030767143')
        msg = Msg.create_outgoing(self.org, self.user, contact, "Hi Joe")
        msg.external_id = '254021015120766124'
        msg.save(update_fields=('external_id',))

        post_data = {
            "results": [
                {
                    "messageId": msg.id,
                    "status": {
                        "groupName": "DELIVERED"
                    }
                }
            ]
        }

        delivery_url = reverse('courier.ib', args=[self.channel.uuid, 'delivered'])

        # assert our DELIVERED status
        response = self.client.post(delivery_url, json.dumps(post_data), content_type='application/json')
        self.assertEqual(200, response.status_code)
        msg = Msg.objects.get()
        self.assertEqual(DELIVERED, msg.status)
        msg.status = SENT
        msg.save()
        self.assertTrue(msg.channel_logs.filter(description='Status Updated'))

        # assert our FAILED status
        post_data['results'][0]['status']['groupName'] = 'REJECTED'
        response = self.client.post(delivery_url, json.dumps(post_data), content_type='application/json')
        self.assertEqual(200, response.status_code)
        msg = Msg.objects.get()
        self.assertEqual(FAILED, msg.status)
        msg.status = SENT
        msg.save()

        post_data['results'][0]['status']['groupName'] = 'UNDELIVERABLE'
        response = self.client.post(delivery_url, json.dumps(post_data), content_type='application/json')
        self.assertEqual(200, response.status_code)
        msg = Msg.objects.get()
        self.assertEqual(FAILED, msg.status)

        # Invalid JSON should return 400
        response = self.client.post(delivery_url, "Invalid", content_type='application/json')
        self.assertEqual(400, response.status_code)

        # ignore when missing results key
        post_data = {
            "deliveryReport": [
                {
                    "messageId": msg.id,
                    "status": {
                        "groupName": "DELIVERED"
                    }
                }
            ]
        }

        response = self.client.post(delivery_url, json.dumps(post_data), content_type='application/json')
        self.assertEqual(400, response.status_code)

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps(dict(messages=[{'status': {'groupId': 1}}])))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertIsNone(msg.external_id)

            self.assertEqual(mock.call_args[1]['json']['messages'][0]['text'],
                             "Test message")

            self.assertEqual(mock.call_args[1]['json']['messages'][0]['notifyContentType'],
                             "application/json")

            self.assertEqual(
                mock.call_args[1]['json']['messages'][0]['notifyUrl'],
                'https://%s%s' % (self.org.get_brand_domain(), reverse('courier.ib', args=[self.channel.uuid, 'delivered']))
            )

            self.assertTrue(mock.call_args[1]['json']['messages'][0]['intermediateReport'])

            self.clear_cache()

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(400, "Error", method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

        with patch('requests.post') as mock:
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

        Msg.objects.all().delete()
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps(dict(messages=[
                {'status': {'groupId': 2, 'description': "Request was rejected"}, 'messageid': 12}])))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

            self.assertEqual(msg.channel_logs.all().count(), 1)
            self.assertEqual(msg.channel_logs.all().first().description, "Received error status: Request was rejected")

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps(dict(messages=[{'status': {'groupId': 1}}])))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertIsNone(msg.external_id)

            self.assertEqual(mock.call_args[1]['json']['messages'][0]['text'],
                             "Test message\nhttps://example.com/attachments/pic.jpg")

            self.assertEqual(mock.call_args[1]['json']['messages'][0]['notifyContentType'],
                             "application/json")

            self.assertEqual(
                mock.call_args[1]['json']['messages'][0]['notifyUrl'],
                'https://%s%s' % (self.org.get_brand_domain(), reverse('courier.ib', args=[self.channel.uuid, 'delivered']))
            )

            self.assertTrue(mock.call_args[1]['json']['messages'][0]['intermediateReport'])

            self.clear_cache()


class MacrokioskTest(TembaTest):

    def setUp(self):
        super(MacrokioskTest, self).setUp()

        self.channel.delete()
        config = dict(username='mk-user', password='mk-password')
        config[Channel.CONFIG_MACROKIOSK_SERVICE_ID] = 'SERVICE-ID'
        config[Channel.CONFIG_MACROKIOSK_SENDER_ID] = 'macro'

        self.channel = Channel.create(self.org, self.user, 'NP', 'MK', None, '1212',
                                      config=config, uuid='00000000-0000-0000-0000-000000001234')

    def test_receive(self):

        two_hour_ago = timezone.now() - timedelta(hours=2)

        msg_date = datetime_to_str(two_hour_ago, format="%Y-%m-%d%H:%M:%S")

        data = {'shortcode': '1212', 'from': '+9771488532', 'text': 'Hello World', 'msgid': 'abc1234',
                'time': msg_date}

        encoded_message = urlencode(data)

        callback_url = reverse('handlers.macrokiosk_handler',
                               args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertContains(response, "-1")

        # load our message
        msg = Msg.objects.get()
        self.assertEqual(msg.contact.get_urn(TEL_SCHEME).path, '+9771488532')
        self.assertEqual(msg.direction, INCOMING)
        self.assertEqual(msg.org, self.org)
        self.assertEqual(msg.channel, self.channel)
        self.assertEqual(msg.text, "Hello World")
        self.assertEqual(msg.external_id, 'abc1234')

        message_date = datetime.strptime(msg_date, "%Y-%m-%d%H:%M:%S")
        local_date = pytz.timezone('Asia/Kuala_Lumpur').localize(message_date)
        gmt_date = local_date.astimezone(pytz.utc)
        self.assertEqual(msg.sent_on, gmt_date)

        Msg.objects.all().delete()

        # try longcode and msisdn
        data = {'longcode': '1212', 'msisdn': '+9771488532', 'text': 'Hello World', 'msgid': 'abc1234',
                'time': datetime_to_str(two_hour_ago, format="%Y-%m-%d%H:%M:%S")}

        encoded_message = urlencode(data)

        callback_url = reverse('handlers.macrokiosk_handler',
                               args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertContains(response, "-1")

        # load our message
        msg = Msg.objects.get()
        self.assertEqual(msg.contact.get_urn(TEL_SCHEME).path, '+9771488532')
        self.assertEqual(msg.direction, INCOMING)
        self.assertEqual(msg.org, self.org)
        self.assertEqual(msg.channel, self.channel)
        self.assertEqual(msg.text, "Hello World")
        self.assertEqual(msg.external_id, 'abc1234')

        # mixed param should not be accepted
        data = {'shortcode': '1212', 'msisdn': '+9771488532', 'text': 'Hello World', 'msgid': 'abc1234',
                'time': datetime_to_str(two_hour_ago, format="%Y-%m-%d%H:%M:%S")}

        encoded_message = urlencode(data)

        callback_url = reverse('handlers.macrokiosk_handler', args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 400 as the channel wasn't found
        self.assertEqual(400, response.status_code)

        data = {'longcode': '1212', 'from': '+9771488532', 'text': 'Hello World', 'msgid': 'abc1234',
                'time': datetime_to_str(two_hour_ago, format="%Y-%m-%d%H:%M:%S")}

        encoded_message = urlencode(data)

        callback_url = reverse('handlers.macrokiosk_handler', args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 400 as the channel wasn't found
        self.assertEqual(400, response.status_code)

        # try missing param
        data = {'from': '+9771488532', 'text': 'Hello World', 'msgid': 'abc1234',
                'time': datetime_to_str(two_hour_ago, format="%Y-%m-%d%H:%M:%S")}
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.macrokiosk_handler', args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 400 as the channel wasn't found
        self.assertEqual(400, response.status_code)

        # try it with an invalid receiver, should fail as UUID and receiver id are mismatched
        data['shortcode'] = '1515'
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.macrokiosk_handler', args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 400 as the channel wasn't found
        self.assertEqual(400, response.status_code)

    def test_status(self):
        # an invalid uuid
        data = dict(msgid='-1', status='ACCEPTED')
        response = self.client.get(reverse('handlers.macrokiosk_handler', args=['status', 'not-real-uuid']), data)
        self.assertEqual(400, response.status_code)

        # a valid uuid, but invalid data
        status_url = reverse('handlers.macrokiosk_handler', args=['status', self.channel.uuid])
        response = self.client.get(status_url, dict())
        self.assertEqual(400, response.status_code)

        response = self.client.get(status_url, data)
        self.assertEqual(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]
        msg.external_id = 'msg-uuid'
        msg.save(update_fields=('external_id',))

        data['msgid'] = msg.external_id

        def assertStatus(sms, status, assert_status):
            sms.status = WIRED
            sms.save()
            data['status'] = status
            response = self.client.get(status_url, data)
            self.assertEqual(200, response.status_code)
            sms = Msg.objects.get(external_id=sms.external_id)
            self.assertEqual(assert_status, sms.status)

        assertStatus(msg, 'PROCESSING', WIRED)
        assertStatus(msg, 'ACCEPTED', SENT)
        assertStatus(msg, 'UNDELIVERED', FAILED)
        assertStatus(msg, 'DELIVERED', DELIVERED)

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+9771488532")
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps({'msisdn': '+9771488532',
                                                              'msgid': 'asdf-asdf-asdf-asdf'}))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertEqual('asdf-asdf-asdf-asdf', msg.external_id)

            self.assertEqual(mock.call_args[1]['json']['text'], 'Test message')
            self.assertEqual(mock.call_args[1]['json']['from'], 'macro')

            self.clear_cache()

        msg.text = "Unicode. ☺"
        msg.save()

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps({'msisdn': '+9771488532',
                                                              'msgid': 'asdf-asdf-asdf-asdf'}))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            # assert verify was set to true
            self.assertEqual(mock.call_args[1]['json']['text'], "Unicode. ☺")
            self.assertEqual(mock.call_args[1]['json']['type'], 5)
            self.assertEqual(mock.call_args[1]['json']['servid'], 'SERVICE-ID')

            self.clear_cache()

        # return 400
        with patch('requests.post') as mock:
            mock.return_value = MockResponse(400, json.dumps({'msisdn': "",
                                                              'msgid': ""}), method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

        # return something that isn't JSON
        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, "Error", method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            # we should have "Error" in our error log
            log = ChannelLog.objects.filter(msg=msg).order_by('-pk')[0]
            self.assertEqual("Error", log.response)
            self.assertEqual(200, log.response_status)

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+9771488532")
        msg = joe.send("Test message", self.admin, trigger_send=False,
                       attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps({'msisdn': '+9771488532',
                                                              'msgid': 'asdf-asdf-asdf-asdf'}))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertEqual('asdf-asdf-asdf-asdf', msg.external_id)

            self.assertEqual(mock.call_args[1]['json']['text'],
                             'Test message\nhttps://example.com/attachments/pic.jpg')

            self.clear_cache()


class BlackmynaTest(TembaTest):

    def setUp(self):
        super(BlackmynaTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'NP', 'BM', None, '1212',
                                      config=dict(username='bm-user', password='bm-password'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_received(self):
        data = {'to': '1212', 'from': '+9771488532', 'text': 'Hello World', 'smsc': 'NTNepal5002'}
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.blackmyna_handler', args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual('+9771488532', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World", msg.text)

        # try it with an invalid receiver, should fail as UUID and receiver id are mismatched
        data['to'] = '1515'
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.blackmyna_handler', args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 400 as the channel wasn't found
        self.assertEqual(400, response.status_code)

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+9771488532")
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps([{'recipient': '+9771488532',
                                                               'id': 'asdf-asdf-asdf-asdf'}]))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertEqual('asdf-asdf-asdf-asdf', msg.external_id)

            self.assertEqual(mock.call_args[1]['data']['message'], 'Test message')

            self.clear_cache()

        # return 400
        with patch('requests.post') as mock:
            mock.return_value = MockResponse(400, "Error", method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

        # return something that isn't JSON
        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, "Error", method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            # we should have "Error" in our error log
            log = ChannelLog.objects.filter(msg=msg).order_by('-pk')[0]
            self.assertEqual("Error", log.response)
            self.assertEqual(200, log.response_status)

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+9771488532")
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps([{'recipient': '+9771488532',
                                                               'id': 'asdf-asdf-asdf-asdf'}]))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertEqual('asdf-asdf-asdf-asdf', msg.external_id)

            self.assertEqual(mock.call_args[1]['data']['message'],
                             'Test message\nhttps://example.com/attachments/pic.jpg')

            self.clear_cache()

    def test_status(self):
        # an invalid uuid
        data = dict(id='-1', status='10')
        response = self.client.get(reverse('handlers.blackmyna_handler', args=['status', 'not-real-uuid']), data)
        self.assertEqual(400, response.status_code)

        # a valid uuid, but invalid data
        status_url = reverse('handlers.blackmyna_handler', args=['status', self.channel.uuid])
        response = self.client.get(status_url, dict())
        self.assertEqual(400, response.status_code)

        response = self.client.get(status_url, data)
        self.assertEqual(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]
        msg.external_id = 'msg-uuid'
        msg.save(update_fields=('external_id',))

        data['id'] = msg.external_id

        def assertStatus(sms, status, assert_status):
            sms.status = WIRED
            sms.save()
            data['status'] = status
            response = self.client.get(status_url, data)
            self.assertEqual(200, response.status_code)
            sms = Msg.objects.get(external_id=sms.external_id)
            self.assertEqual(assert_status, sms.status)

        assertStatus(msg, '0', WIRED)
        assertStatus(msg, '1', DELIVERED)
        assertStatus(msg, '2', FAILED)
        assertStatus(msg, '3', WIRED)
        assertStatus(msg, '4', WIRED)
        assertStatus(msg, '8', SENT)
        assertStatus(msg, '16', FAILED)


class SMSCentralTest(TembaTest):

    def setUp(self):
        super(SMSCentralTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'NP', 'SC', None, '1212',
                                      config=dict(username='sc-user', password='sc-password'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_received(self):
        data = {'mobile': '+9771488532', 'message': 'Hello World', 'telco': 'Ncell'}
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.smscentral_handler', args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual('+9771488532', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World", msg.text)

        # try it with an invalid channel
        callback_url = reverse('handlers.smscentral_handler', args=['receive', '1234-asdf']) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 400 as the channel wasn't found
        self.assertEqual(400, response.status_code)

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+9771488532")
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            mock.assert_called_with('http://smail.smscentral.com.np/bp/ApiSms.php',
                                    data={'user': 'sc-user', 'pass': 'sc-password',
                                          'mobile': '9771488532', 'content': "Test message"},
                                    headers=http_headers(),
                                    timeout=30)

            self.clear_cache()

        # return 400
        with patch('requests.post') as mock:
            mock.return_value = MockResponse(400, "Error", method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

        # return 400
        with patch('requests.post') as mock:
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+9771488532")
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            mock.assert_called_with('http://smail.smscentral.com.np/bp/ApiSms.php',
                                    data={'user': 'sc-user', 'pass': 'sc-password',
                                          'mobile': '9771488532',
                                          'content': "Test message\nhttps://example.com/attachments/pic.jpg"},
                                    headers=http_headers(),
                                    timeout=30)

            self.clear_cache()


class Hub9Test(TembaTest):

    def setUp(self):
        super(Hub9Test, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'ID', 'H9', None, '+6289881134567',
                                      config=dict(username='h9-user', password='h9-password'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_received(self):
        # http://localhost:8000/api/v1/hub9/received/9bbffaeb-3b12-4fe1-bcaa-fd50cce2ada2/?
        # userid=testusr&password=test&original=6289881134567&sendto=6282881134567
        # &messageid=99123635&message=Test+sending+sms
        data = {
            'userid': 'testusr',
            'password': 'test',
            'original': '6289881134560',
            'sendto': '6289881134567',
            'message': 'Hello World'
        }
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.hub9_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual('+6289881134560', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World", msg.text)

        # try it with an invalid receiver, should fail as UUID and receiver id are mismatched
        data['sendto'] = '6289881131111'
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.hub9_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 404 as the channel wasn't found
        self.assertEqual(404, response.status_code)

        # the case of 11 digits numer from hub9
        data = {
            'userid': 'testusr',
            'password': 'test',
            'original': '62811999374',
            'sendto': '6289881134567',
            'message': 'Hello Jakarta'
        }
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.hub9_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.all().order_by('-pk').first()
        self.assertEqual('+62811999374', msg.contact.raw_tel())
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello Jakarta", msg.text)

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, "000")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)
            self.assertTrue(msg.sent_on)

            self.assertTrue(mock.call_args[0][0].startswith(HUB9_ENDPOINT))
            self.assertIn("message=Test+message", mock.call_args[0][0])

            self.clear_cache()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(400, "Error", method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, "000")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)
            self.assertTrue(msg.sent_on)

            self.clear_cache()

            self.assertTrue(mock.call_args[0][0].startswith(HUB9_ENDPOINT))
            self.assertIn("message=Test+message%0Ahttps%3A%2F%2Fexample.com%2Fattachments%2Fpic.jpg", mock.call_args[0][0])


class DartMediaTest(TembaTest):

    def setUp(self):
        super(DartMediaTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'ID', 'DA', None, '+6289881134567',
                                      config=dict(username='dartmedia-user', password='dartmedia-password'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_received(self):
        # http://localhost:8000/api/v1/dartmedia/received/9bbffaeb-3b12-4fe1-bcaa-fd50cce2ada2/?
        # userid=testusr&password=test&original=6289881134567&sendto=6282881134567
        # &messageid=99123635&message=Test+sending+sms
        data = {
            'userid': 'testusr',
            'password': 'test',
            'original': '6289881134560',
            'sendto': '6289881134567',
            'message': 'Hello World'
        }
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.dartmedia_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual('+6289881134560', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World", msg.text)

        # try it with an invalid receiver, should fail as UUID and receiver id are mismatched
        data['sendto'] = '6289881131111'
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.dartmedia_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 404 as the channel wasn't found
        self.assertEqual(404, response.status_code)

        # the case of 11 digits number from dartmedia
        data = {
            'userid': 'testusr',
            'password': 'test',
            'original': '62811999374',
            'sendto': '6289881134567',
            'message': 'Hello Jakarta'
        }
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.dartmedia_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.all().order_by('-pk').first()
        self.assertEqual('+62811999374', msg.contact.raw_tel())
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello Jakarta", msg.text)

        # short code do not have + in address
        self.channel.address = '12345'
        self.channel.save()

        # missing parameters
        data = {
            'userid': 'testusr',
            'password': 'test',
            'original': '62811999375',
            'message': 'Hello Indonesia'
        }

        encoded_message = urlencode(data)
        callback_url = reverse('handlers.dartmedia_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertContains(response, "Parameters message, original and sendto should not be null.", status_code=401)

        # all needed params
        data = {
            'userid': 'testusr',
            'password': 'test',
            'original': '62811999375',
            'sendto': '12345',
            'message': 'Hello Indonesia'
        }

        encoded_message = urlencode(data)
        callback_url = reverse('handlers.dartmedia_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.all().order_by('-pk').first()
        self.assertEqual('+62811999375', msg.contact.raw_tel())
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello Indonesia", msg.text)

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, "000")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)
            self.assertTrue(msg.sent_on)

            self.assertIn("message=Test+message", mock.call_args[0][0])

            self.clear_cache()

            self.assertTrue(mock.call_args[0][0].startswith(DART_MEDIA_ENDPOINT))

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(400, "Error", method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, "000")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(SENT, msg.status)
            self.assertTrue(msg.sent_on)

            self.assertIn("message=Test+message%0Ahttps%3A%2F%2Fexample.com%2Fattachments%2Fpic.jpg", mock.call_args[0][0])
            self.clear_cache()
            self.assertTrue(mock.call_args[0][0].startswith(DART_MEDIA_ENDPOINT))


class HighConnectionTest(TembaTest):

    def setUp(self):
        super(HighConnectionTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'FR', 'HX', None, '5151',
                                      config=dict(username='hcnx-user', password='hcnx-password'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_handler(self):
        # http://localhost:8000/api/v1/hcnx/receive/asdf-asdf-asdf-asdf/?FROM=+33610346460&TO=5151&MESSAGE=Hello+World
        data = {'FROM': '+33610346460', 'TO': '5151', 'MESSAGE': 'Hello World', 'RECEPTION_DATE': '2015-04-02T14:26:06'}

        callback_url = reverse('handlers.hcnx_handler', args=['receive', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual('+33610346460', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World", msg.text)
        self.assertEqual(14, msg.sent_on.astimezone(pytz.utc).hour)

        # try it with an invalid receiver, should fail as UUID isn't known
        callback_url = reverse('handlers.hcnx_handler', args=['receive', uuid.uuid4()])
        response = self.client.post(callback_url, data)

        # should get 400 as the channel wasn't found
        self.assertEqual(400, response.status_code)

        # create an outgoing message instead
        contact = msg.contact
        Msg.objects.all().delete()

        contact.send("outgoing message", self.admin)
        msg = Msg.objects.get()

        # now update the status via a callback
        data = {'ret_id': msg.id, 'status': '6'}
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.hcnx_handler', args=['status', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEqual(200, response.status_code)

        msg = Msg.objects.get()
        self.assertEqual(DELIVERED, msg.status)

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, "Sent")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertIn("text=Test+message%0Ahttps%3A%2F%2Fexample.com%2Fattachments%2Fpic.jpg", mock.call_args[0][0])
            self.clear_cache()

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, "Sent")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            self.assertIn("text=Test+message", mock.call_args[0][0])

            self.clear_cache()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(400, "Error")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

        with patch('requests.get') as mock:
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))


class TwilioTest(TembaTest):

    def setUp(self):
        super(TwilioTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'T', None, '+250785551212',
                                      uuid='00000000-0000-0000-0000-000000001234',
                                      config={Channel.CONFIG_AUTH_TOKEN: '0b14d47901387c03f92253a4e4449d5e',
                                              Channel.CONFIG_ACCOUNT_SID: 'ACe54dc36bfd2a3b483b7ed854b2dd40c1',
                                              Channel.CONFIG_APPLICATION_SID: 'AP6fe2069df7f9482a8031cb61dc155de2'})

        # twilio test credentials
        self.account_sid = "ACe54dc36bfd2a3b483b7ed854b2dd40c1"
        self.account_token = "0b14d47901387c03f92253a4e4449d5e"
        self.application_sid = "AP6fe2069df7f9482a8031cb61dc155de2"

        self.channel.org.config = {ACCOUNT_SID: self.account_sid,
                                   ACCOUNT_TOKEN: self.account_token,
                                   APPLICATION_SID: self.application_sid}
        self.channel.org.save()

    def signed_request(self, url, data, validator=None):
        """
        Makes a post to the Twilio handler with a computed signature
        """
        if not validator:
            validator = RequestValidator(self.org.get_twilio_client().auth[1])

        signature = validator.compute_signature('https://' + 'testserver' + url, data)
        return self.client.post(url, data, **{'HTTP_X_TWILIO_SIGNATURE': signature})

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_receive_media(self):
        post_data = dict(To=self.channel.address, From='+250788383383', Body="Test",
                         NumMedia='1', MediaUrl0='https://yourimage.io/IMPOSSIBLE-HASH',
                         MediaContentType0='audio/x-wav')

        twilio_url = reverse('handlers.twilio_handler', args=['receive', self.channel.uuid])

        client = self.org.get_twilio_client()
        validator = RequestValidator(client.auth[1])
        signature = validator.compute_signature('https://' + self.org.get_brand_domain() + twilio_url, post_data)

        with patch('requests.get') as response:
            mock = MockResponse(200, 'Fake Recording Bits')
            mock.add_header('Content-Disposition', 'filename="audio0000.wav"')
            mock.add_header('Content-Type', 'audio/x-wav')
            response.return_value = mock
            response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})
            self.assertEqual(201, response.status_code)

        # should have a single message with text and attachment
        msg = Msg.objects.get()
        self.assertEqual(msg.text, 'Test')
        self.assertEqual(len(msg.attachments), 1)
        self.assertTrue(msg.attachments[0].startswith('audio/x-wav:https://%s' % settings.AWS_BUCKET_DOMAIN))
        self.assertTrue(msg.attachments[0].endswith('.wav'))

        Msg.objects.all().delete()

        # try with no message body
        with patch('requests.get') as response:
            mock = MockResponse(200, 'Fake Recording Bits')
            mock.add_header('Content-Disposition', 'filename="audio0000.wav"')
            mock.add_header('Content-Type', 'audio/x-wav')
            response.return_value = mock

            post_data['Body'] = ''
            signature = validator.compute_signature('https://' + self.org.get_brand_domain() + '/handlers/twilio/', post_data)
            self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})

        # should have a single message with an attachment but no text
        msg = Msg.objects.get()
        self.assertEqual(msg.text, '')
        self.assertEqual(len(msg.attachments), 1)
        self.assertTrue(msg.attachments[0].startswith('audio/x-wav:https://%s' % settings.AWS_BUCKET_DOMAIN))
        self.assertTrue(msg.attachments[0].endswith('.wav'))

        Msg.objects.all().delete()

        with patch('requests.get') as response:
            mock1 = MockResponse(404, 'No such file')
            mock2 = MockResponse(200, 'Fake VCF Bits')
            mock2.add_header('Content-Type', 'text/x-vcard')
            mock2.add_header('Content-Disposition', 'inline')
            response.side_effect = (mock1, mock2)

            post_data['Body'] = ''
            signature = validator.compute_signature('https://' + self.org.get_brand_domain() + '/handlers/twilio/', post_data)
            response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})

        msg = Msg.objects.get()
        self.assertTrue(msg.attachments[0].startswith('text/x-vcard:https://%s' % settings.AWS_BUCKET_DOMAIN))
        self.assertTrue(msg.attachments[0].endswith('.vcf'))

    def test_receive_base64(self):
        post_data = dict(To=self.channel.address, From='+250788383383', Body="QmFubm9uIEV4cGxhaW5zIFRoZSBXb3JsZCAuLi4K4oCcVGhlIENhbXAgb2YgdGhlIFNhaW50c+KA\r")
        twilio_url = reverse('handlers.twilio_handler', args=['receive', self.channel.uuid])
        self.signed_request(twilio_url, post_data)
        self.assertIsNotNone(Msg.objects.filter(text__contains='Bannon Explains').first())

    def test_receive(self):
        post_data = dict(To=self.channel.address, From='+250788383383', Body="Hello World")
        twilio_url = reverse('handlers.twilio_handler', args=['receive', self.channel.uuid])

        response = self.client.post(twilio_url, post_data)
        self.assertEqual(response.status_code, 400)

        # this time sign it appropriately, should work
        client = self.org.get_twilio_client()
        validator = RequestValidator(client.auth[1])

        # remove twilio connection
        self.channel.org.config = {}
        self.channel.org.save()

        signature = validator.compute_signature('https://' + self.org.get_brand_domain() + '/handlers/twilio/', post_data)
        response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})

        self.assertEqual(400, response.status_code)

        # connect twilio again
        self.channel.org.config = {ACCOUNT_SID: self.account_sid,
                                   ACCOUNT_TOKEN: self.account_token,
                                   APPLICATION_SID: self.application_sid}

        self.channel.org.save()

        response = self.signed_request(twilio_url, post_data)
        self.assertEqual(response.status_code, 201)

        # and we should have a new message
        msg1 = Msg.objects.get()
        self.assertEqual("+250788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg1.direction)
        self.assertEqual(self.org, msg1.org)
        self.assertEqual(self.channel, msg1.channel)
        self.assertEqual("Hello World", msg1.text)

        # try without including number, but with country
        del post_data['To']
        post_data['ToCountry'] = 'RW'
        response = self.signed_request(twilio_url, post_data)
        self.assertEqual(response.status_code, 400)

        # try with non-normalized number
        post_data['To'] = '0785551212'
        post_data['ToCountry'] = 'RW'
        response = self.signed_request(twilio_url, post_data)
        self.assertEqual(response.status_code, 201)

        # and we should have another new message
        msg2 = Msg.objects.exclude(pk=msg1.pk).get()
        self.assertEqual(self.channel, msg2.channel)

        # create an outgoing message instead
        contact = msg2.contact
        Msg.objects.all().delete()

        contact.send("outgoing message", self.admin)
        msg = Msg.objects.get()

        # now update the status via a callback
        post_data['SmsStatus'] = 'sent'
        validator = RequestValidator(self.org.get_twilio_client().auth[1])

        # remove twilio connection
        self.channel.org.config = {}
        self.channel.org.save()

        response = self.signed_request(twilio_url + "?action=callback&id=%d" % msg.id, post_data, validator)
        self.assertEqual(response.status_code, 400)

        # connect twilio again
        self.channel.org.config = {ACCOUNT_SID: self.account_sid,
                                   ACCOUNT_TOKEN: self.account_token,
                                   APPLICATION_SID: self.application_sid}
        self.channel.org.save()

        self.channel.config = dict(auth_token=self.account_token,
                                   account_sid=self.account_sid)
        self.channel.save()

        response = self.signed_request(twilio_url + "?action=callback&id=%d" % msg.id, post_data)
        self.assertEqual(response.status_code, 200)

        msg = Msg.objects.get()
        self.assertEqual(SENT, msg.status)

        # try it with a failed SMS
        Msg.objects.all().delete()
        contact.send("outgoing message", self.admin)
        msg = Msg.objects.get()

        # now update the status via a callback
        post_data['SmsStatus'] = 'failed'

        response = self.signed_request(twilio_url + "?action=callback&id=%d" % msg.id, post_data)
        self.assertEqual(response.status_code, 200)

        msg = Msg.objects.get()
        self.assertEqual(FAILED, msg.status)

        # no message with id
        Msg.objects.all().delete()

        response = self.signed_request(twilio_url + "?action=callback&id=%d" % msg.id, post_data)
        self.assertEqual(response.status_code, 400)

        # test TwiML Handler...

        self.channel.delete()
        post_data = dict(To=self.channel.address, From='+250788383300', Body="Hello World")

        # try without signing
        twiml_api_url = reverse('handlers.twiml_api_handler', args=['1234-1234-1234-12345'])
        response = self.client.post(twiml_api_url, post_data)
        self.assertEqual(response.status_code, 400)

        # create new channel
        self.channel = Channel.create(self.org, self.user, 'RW', 'TW', None, '+250785551212',
                                      uuid='00000000-0000-0000-0000-000000001234')

        send_url = "https://api.twilio.com"

        self.channel.config = {Channel.CONFIG_ACCOUNT_SID: self.account_sid,
                               Channel.CONFIG_AUTH_TOKEN: self.account_token,
                               Channel.CONFIG_SEND_URL: send_url}
        self.channel.save()

        post_data = dict(To=self.channel.address, From='+250788383300', Body="Hello World")
        twiml_api_url = reverse('handlers.twiml_api_handler', args=[self.channel.uuid])

        response = self.client.post(twiml_api_url, post_data)
        self.assertEqual(response.status_code, 400)

        client = self.channel.get_twiml_client()
        validator = RequestValidator(client.auth[1])
        signature = validator.compute_signature(
            'https://testserver/handlers/twiml_api/' + self.channel.uuid,
            post_data
        )
        response = self.client.post(twiml_api_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})
        self.assertEqual(201, response.status_code)

        msg1 = Msg.objects.get()
        self.assertEqual("+250788383300", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg1.direction)
        self.assertEqual(self.org, msg1.org)
        self.assertEqual(self.channel, msg1.channel)
        self.assertEqual("Hello World", msg1.text)

    def test_send(self):
        from temba.orgs.models import ACCOUNT_SID, ACCOUNT_TOKEN, APPLICATION_SID
        org_config = self.org.config
        org_config[ACCOUNT_SID] = 'twilio_sid'
        org_config[ACCOUNT_TOKEN] = 'twilio_token'
        org_config[APPLICATION_SID] = 'twilio_sid'
        self.org.config = org_config
        self.org.save()

        joe = self.create_contact("Joe", "+250788383383")

        with self.settings(SEND_MESSAGES=True):
            with patch('twilio.rest.resources.base.make_request') as mock:
                for channel_type in ['T', 'TMS', 'TW']:
                    ChannelLog.objects.all().delete()
                    Msg.objects.all().delete()

                    msg = joe.send("Test message", self.admin, trigger_send=False)[0]

                    self.channel.channel_type = channel_type
                    if channel_type == 'TMS':
                        self.channel.config = dict(messaging_service_sid="MSG-SERVICE-SID",
                                                   auth_token='twilio_token',
                                                   account_sid='twilio_sid')
                    elif channel_type == 'TW':
                        self.channel.config = {Channel.CONFIG_SEND_URL: 'https://api.twilio.com',
                                               Channel.CONFIG_ACCOUNT_SID: 'twilio_sid',
                                               Channel.CONFIG_AUTH_TOKEN: 'twilio_token'}
                    self.channel.save()

                    mock.return_value = MockResponse(200, '{ "account_sid": "ac1232", "sid": "12345"}')
                    mock.side_effect = None
                    self.clear_cache()

                    # manually send it off
                    Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                    # check the status of the message is now sent
                    msg.refresh_from_db()
                    self.assertEqual(WIRED, msg.status)
                    self.assertTrue(msg.sent_on)

                    self.clear_cache()

                    # handle the status callback
                    callback_url = Channel.build_twilio_callback_url(self.channel.callback_domain, channel_type, self.channel.uuid, msg.id)

                    self.assertTrue(callback_url.find("c/%s/%s/status" % (channel_type.lower(), self.channel.uuid)) >= 0)

                    client = self.org.get_twilio_client()
                    validator = RequestValidator(client.auth[1])
                    post_data = dict(SmsStatus='delivered', To='+250788383383')
                    signature = validator.compute_signature(callback_url, post_data)

                    client_kwargs = {
                        'SERVER_NAME': self.channel.callback_domain,
                        'HTTP_X_TWILIO_SIGNATURE': signature,
                    }
                    response = self.client.post(callback_url, post_data, **client_kwargs)

                    self.assertEqual(response.status_code, 200)
                    msg.refresh_from_db()
                    self.assertEqual(msg.status, DELIVERED)

                    msg.status = WIRED
                    msg.save()

                    validator = RequestValidator(client.auth[1])
                    post_data = dict(SmsStatus='sent', To='+250788383383')
                    client_kwargs['HTTP_X_TWILIO_SIGNATURE'] = validator.compute_signature(callback_url, post_data)

                    response = self.client.post(callback_url, post_data, **client_kwargs)

                    self.assertEqual(response.status_code, 200)
                    msg.refresh_from_db()
                    self.assertEqual(msg.status, SENT)

                    msg.status = WIRED
                    msg.save()

                    validator = RequestValidator(client.auth[1])
                    post_data = dict(SmsStatus='failed', To='+250788383383')
                    client_kwargs['HTTP_X_TWILIO_SIGNATURE'] = validator.compute_signature(callback_url, post_data)

                    response = self.client.post(callback_url, post_data, **client_kwargs)

                    self.assertEqual(response.status_code, 200)
                    msg.refresh_from_db()
                    self.assertEqual(msg.status, FAILED)

                    msg.status = WIRED
                    msg.save()

                    # simulate Twilio failing to send the message
                    mock.side_effect = Exception("Request Timeout")

                    # manually send it off
                    Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                    # message should be marked as an error
                    msg.refresh_from_db()
                    self.assertEqual(ERRORED, msg.status)
                    self.assertEqual(1, msg.error_count)
                    self.assertTrue(msg.next_attempt)

                    mock.side_effect = TwilioRestException(400, "https://twilio.com/", "User has opted out", code=21610)

                    # manually send it off
                    Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                    # message should be marked as failed and the contact should be stopped
                    msg.refresh_from_db()
                    self.assertEqual(FAILED, msg.status)
                    self.assertTrue(Contact.objects.get(id=msg.contact_id))

                    msg.channel = None
                    msg.save()
                    response = self.client.post(callback_url, post_data, **client_kwargs)
                    self.assertEqual(response.status_code, 200)

                    missing_sms_callback_url = Channel.build_twilio_callback_url(self.channel.callback_domain, channel_type, self.channel.uuid, msg.id + 100)
                    response = self.client.post(missing_sms_callback_url, post_data, **client_kwargs)
                    self.assertEqual(response.status_code, 400)

                    with patch('temba.orgs.models.Org.is_connected_to_twilio') as mock_connected_twilio:
                        mock_connected_twilio.return_value = False
                        response = self.client.post(callback_url, post_data, **client_kwargs)
                        self.assertEqual(response.status_code, 400)

            # check that our channel log works as well
            self.login(self.admin)

            response = self.client.get(reverse('channels.channellog_list') + "?channel=%d" % (self.channel.pk))

            # there should be three log items for the three times we sent
            self.assertEqual(3, len(response.context['channellog_list']))

            # number of items on this page should be right as well
            self.assertEqual(3, response.context['paginator'].count)
            self.assertEqual(2, self.channel.get_error_log_count())
            self.assertEqual(1, self.channel.get_success_log_count())

            # view the detailed information for one of them
            response = self.client.get(reverse('channels.channellog_read', args=[ChannelLog.objects.order_by('id')[1].id]))

            # check that it contains the log of our exception
            self.assertContains(response, "Request Timeout")

            # delete our error entries
            ChannelLog.objects.filter(is_error=True).delete()

            # our channel counts should be updated
            self.channel = Channel.objects.get(id=self.channel.pk)
            self.assertEqual(0, self.channel.get_error_log_count())
            self.assertEqual(1, self.channel.get_success_log_count())

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        from temba.orgs.models import ACCOUNT_SID, ACCOUNT_TOKEN, APPLICATION_SID
        org_config = self.org.config
        org_config[ACCOUNT_SID] = 'twilio_sid'
        org_config[ACCOUNT_TOKEN] = 'twilio_token'
        org_config[APPLICATION_SID] = 'twilio_sid'
        self.org.config = org_config
        self.org.save()

        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('twilio.rest.resources.messages.Messages.create') as mock:
            mock.return_value = "Sent"

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            self.assertEqual(mock.call_args[1]['media_url'], [])
            self.assertEqual(mock.call_args[1]['body'], "Test message\nhttps://example.com/attachments/pic.jpg")

            self.clear_cache()

            # handle the status callback
            callback_url = Channel.build_twilio_callback_url(self.channel.callback_domain, self.channel.channel_type, self.channel.uuid, msg.id)

            client = self.org.get_twilio_client()
            validator = RequestValidator(client.auth[1])
            post_data = dict(SmsStatus='delivered', To='+250788383383')
            signature = validator.compute_signature(callback_url, post_data)

            response = self.client.post(
                callback_url, post_data,
                **{'SERVER_NAME': self.channel.callback_domain, 'HTTP_X_TWILIO_SIGNATURE': signature}
            )

            self.assertEqual(response.status_code, 200)
            msg.refresh_from_db()
            self.assertEqual(msg.status, DELIVERED)

            self.channel.country = 'US'
            self.channel.save()
            self.clear_cache()

            msg = joe.send("MT", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            self.assertEqual(mock.call_args[1]['media_url'], ['https://example.com/attachments/pic.jpg'])
            self.assertEqual(mock.call_args[1]['body'], "MT")

            self.clear_cache()


class TwilioMessagingServiceTest(TembaTest):

    def setUp(self):
        super(TwilioMessagingServiceTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'US', 'TMS', None, None,
                                      config=dict(messaging_service_sid="MSG-SERVICE-SID"),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_receive(self):
        # twilio test credentials
        account_sid = "ACe54dc36bfd2a3b483b7ed854b2dd40c1"
        account_token = "0b14d47901387c03f92253a4e4449d5e"
        application_sid = "AP6fe2069df7f9482a8031cb61dc155de2"

        self.channel.org.config = {ACCOUNT_SID: account_sid, ACCOUNT_TOKEN: account_token,
                                   APPLICATION_SID: application_sid}
        self.channel.org.save()

        messaging_service_sid = self.channel.config['messaging_service_sid']

        post_data = dict(message_service_sid=messaging_service_sid, From='+250788383383', Body="Hello World")
        twilio_url = reverse('handlers.twilio_messaging_service_handler', args=['receive', self.channel.uuid])

        response = self.client.post(twilio_url, post_data)
        self.assertEqual(response.status_code, 400)

        # this time sign it appropriately, should work
        client = self.org.get_twilio_client()
        validator = RequestValidator(client.auth[1])
        signature = validator.compute_signature(
            'https://testserver/handlers/twilio_messaging_service/receive/' + self.channel.uuid,
            post_data
        )
        response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})

        self.assertEqual(201, response.status_code)

        # and we should have a new message
        msg1 = Msg.objects.get()
        self.assertEqual("+250788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg1.direction)
        self.assertEqual(self.org, msg1.org)
        self.assertEqual(self.channel, msg1.channel)
        self.assertEqual("Hello World", msg1.text)

        # remove twilio connection
        self.channel.org.config = {}
        self.channel.org.save()

        signature = validator.compute_signature(
            'https://testserver/handlers/twilio_messaging_service/receive/' + self.channel.uuid,
            post_data
        )
        response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})

        self.assertEqual(400, response.status_code)


class TelegramTest(TembaTest):

    def setUp(self):
        super(TelegramTest, self).setUp()

        self.channel.delete()

        self.channel = Channel.create(self.org, self.user, None, 'TG', None, 'RapidBot',
                                      config=dict(auth_token='valid'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_receive(self):
        data = """
        {
          "update_id": 174114370,
          "message": {
            "message_id": 41,
            "from": {
              "id": 3527065,
              "first_name": "Nic",
              "last_name": "Pottier"
            },
            "chat": {
              "id": 3527065,
              "first_name": "Nic",
              "last_name": "Pottier",
              "type": "private"
            },
            "date": 1454119029,
            "text": "Hello World"
          }
        }
        """

        receive_url = reverse('handlers.telegram_handler', args=[self.channel.uuid])
        response = self.client.post(receive_url, data, content_type='application/json')
        self.assertEqual(201, response.status_code)

        # and we should have a new message
        msg1 = Msg.objects.get()
        self.assertEqual('3527065', msg1.contact.get_urn(TELEGRAM_SCHEME).path)
        self.assertEqual(INCOMING, msg1.direction)
        self.assertEqual(self.org, msg1.org)
        self.assertEqual(self.channel, msg1.channel)
        self.assertEqual("Hello World", msg1.text)
        self.assertEqual(msg1.contact.name, 'Nic Pottier')

        def test_file_message(data, file_path, content_type, extension, caption=None):

            Msg.objects.all().delete()

            with patch('requests.post') as post:
                with patch('requests.get') as get:

                    post.return_value = MockResponse(200, json.dumps(dict(ok="true", result=dict(file_path=file_path))))
                    get.return_value = MockResponse(200, "Fake image bits", headers={"Content-Type": content_type})

                    response = self.client.post(receive_url, data, content_type='application/json')
                    self.assertEqual(201, response.status_code)

                    # should have a new message
                    msg = Msg.objects.get()
                    self.assertEqual(msg.text, caption or "")
                    # this fails probably because of this bug https://github.com/ahupp/python-magic/issues/152
                    # self.assertTrue(msg.attachments[0].startswith('%s:https://' % content_type))
                    self.assertTrue(msg.attachments[0].endswith(extension))

        # stickers are allowed
        sticker_data = """
        {
          "update_id":174114373,
          "message":{
            "message_id":44,
            "from":{
              "id":3527065,
              "first_name":"Nic",
              "last_name":"Pottier"
            },
            "chat":{
              "id":3527065,
              "first_name":"Nic",
              "last_name":"Pottier",
              "type":"private"
            },
            "date":1454119668,
            "sticker":{
              "width":436,
              "height":512,
              "thumb":{
                "file_id":"AAQDABNW--sqAAS6easb1s1rNdJYAAIC",
                "file_size":2510,
                "width":77,
                "height":90
              },
              "file_id":"BQADAwADRQADyIsGAAHtBskMy6GoLAI",
              "file_size":38440
            }
          }
        }
        """

        photo_data = """
        {
          "update_id":414383172,
          "message":{
            "message_id":52,
            "from":{
              "id":25028612,
              "first_name":"Eric",
              "last_name":"Newcomer",
              "username":"ericn"
            },
            "chat":{
              "id":25028612,
              "first_name":"Eric",
              "last_name":"Newcomer",
              "username":"ericn",
              "type":"private"
            },
            "date":1460845907,
            "photo":[
              {
                "file_id":"AgADAwADJKsxGwTofQF_vVnL5P2C2P8AAewqAARQoXPLPaJRfrgPAQABAg",
                "file_size":1527,
                "width":90,
                "height":67
              },
              {
                "file_id":"AgADAwADJKsxGwTofQF_vVnL5P2C2P8AAewqAATfgqvLofrK17kPAQABAg",
                "file_size":21793,
                "width":320,
                "height":240
              },
              {
                "file_id":"AgADAwADJKsxGwTofQF_vVnL5P2C2P8AAewqAAQn6a6fBlz_KLcPAQABAg",
                "file_size":104602,
                "width":800,
                "height":600
              },
              {
                "file_id":"AgADAwADJKsxGwTofQF_vVnL5P2C2P8AAewqAARtnUHeihUe-LYPAQABAg",
                "file_size":193145,
                "width":1280,
                "height":960
              }
            ]
          }
        }
        """

        video_data = """
        {
          "update_id":414383173,
          "message":{
            "caption": "Check out this amazeballs video",
            "message_id":54,
            "from":{
              "id":25028612,
              "first_name":"Eric",
              "last_name":"Newcomer",
              "username":"ericn"
            },
            "chat":{
              "id":25028612,
              "first_name":"Eric",
              "last_name":"Newcomer",
              "username":"ericn",
              "type":"private"
            },
            "date":1460848768,
            "video":{
              "duration":5,
              "width":640,
              "height":360,
              "thumb":{
                "file_id":"AAQDABNaEOwqAATL2L1LaefkMyccAAIC",
                "file_size":1903,
                "width":90,
                "height":50
              },
              "file_id":"BAADAwADbgADBOh9ARFryoDddM4bAg",
              "file_size":368568
            }
          }
        }
        """

        audio_data = """
        {
          "update_id":414383174,
          "message":{
            "message_id":55,
            "from":{
              "id":25028612,
              "first_name":"Eric",
              "last_name":"Newcomer",
              "username":"ericn"
            },
            "chat":{
              "id":25028612,
              "first_name":"Eric",
              "last_name":"Newcomer",
              "username":"ericn",
              "type":"private"
            },
            "date":1460849148,
            "voice":{
              "duration":2,
              "mime_type":"audio\/ogg",
              "file_id":"AwADAwADbwADBOh9AYp70sKPJ09pAg",
              "file_size":7748
            }
          }
        }
        """

        test_file_message(sticker_data, 'file/image.webp', "image/webp", "webp")
        test_file_message(photo_data, 'file/image.jpg', "image/jpeg", "jpg")
        test_file_message(video_data, 'file/video.mp4', "video/mp4", "mp4", caption="Check out this amazeballs video")
        test_file_message(audio_data, 'file/audio.oga', "audio/ogg", "oga")

        # test with a location which will create an geo attachment
        location_data = """
        {
          "update_id":414383175,
          "message":{
            "message_id":56,
            "from":{
              "id":25028612,
              "first_name":"Eric",
              "last_name":"Newcomer",
              "username":"ericn"
            },
            "chat":{
              "id":25028612,
              "first_name":"Eric",
              "last_name":"Newcomer",
              "username":"ericn",
              "type":"private"
            },
            "date":1460849460,
            "location":{
              "latitude":-2.910574,
              "longitude":-79.000239
            },
            "venue":{
              "location":{
                "latitude":-2.910574,
                "longitude":-79.000239
              },
              "title":"Fogo Mar",
              "address":"Av. Paucarbamba",
              "foursquare_id":"55033319498eed335779a701"
            }
          }
        }
        """
        Msg.objects.all().delete()
        response = self.client.post(receive_url, location_data, content_type='application/json')
        self.assertEqual(response.status_code, 201)

        # location should be a geo attachment and venue title should be the message text
        msg = Msg.objects.get()
        self.assertEqual(msg.attachments, ['geo:-2.910574,-79.000239'])
        self.assertEqual(msg.text, "Fogo Mar")

        # test payload missing message object
        no_message = """
        {
          "channel_post": {
            "caption": "@A_caption",
            "chat": {
              "id": -1001091928432,
              "title": "a title",
              "type": "channel",
              "username": "a_username"
            },
            "date": 1479722450,
            "forward_date": 1479712599,
            "forward_from": {},
            "forward_from_chat": {},
            "forward_from_message_id": 532,
            "from": {
              "first_name": "a_first_name",
              "id": 294674412
            },
            "message_id": 1310,
            "voice": {
              "duration": 191,
              "file_id": "AwADBAAD2AYAAoN65QtM8XVBVS7P5Ao",
              "file_size": 1655713,
              "mime_type": "audio/ogg"
            }
          },
          "update_id": 677142491
        }
        """
        response = self.client.post(receive_url, no_message, content_type='application/json')
        self.assertEqual(response.status_code, 400)

        # test valid message with no content for us to create a message from
        empty_message = """
        {
          "update_id":414383174,
          "message": {
            "message_id":55,
            "from":{
              "id":25028612,
              "first_name":"Eric",
              "last_name":"Newcomer",
              "username":"ericn"
            },
            "chat":{
              "id":25028612,
              "first_name":"Eric",
              "last_name":"Newcomer",
              "username":"ericn",
              "type":"private"
            },
            "date":1460849148
          }
        }
        """
        response = self.client.post(receive_url, empty_message, content_type='application/json')
        self.assertEqual(response.status_code, 201)

        new_conversation_command = """
        {
          "update_id": 174114370,
          "message": {
            "message_id": 41,
            "from": {
              "id": 3527065,
              "first_name": "Nic",
              "last_name": "Pottier"
            },
            "chat": {
              "id": 3527065,
              "first_name": "Nic",
              "last_name": "Pottier",
              "type": "private"
            },
            "date": 1454119029,
            "text": "/start"
          }
        }
        """

        response = self.client.post(receive_url, new_conversation_command, content_type='application/json')
        self.assertEqual(response.status_code, 201)
        response_json = response.json()
        self.assertEqual(response_json.get("description"), "Conversation started")

        # remove all contacts to simulate the contact is new
        Contact.objects.all().delete()

        with AnonymousOrg(self.org):
            response = self.client.post(receive_url, new_conversation_command, content_type='application/json')
            self.assertEqual(response.status_code, 201)
            response_json = response.json()
            self.assertEqual(response_json.get("description"), "Conversation started")

        Msg.objects.all().delete()
        Contact.objects.all().delete()

        no_new_conversation_command = """
        {
          "update_id": 174114370,
          "message": {
            "message_id": 41,
            "from": {
              "id": 3527065,
              "first_name": "Nic",
              "last_name": "Pottier"
            },
            "chat": {
              "id": 3527065,
              "first_name": "Nic",
              "last_name": "Pottier",
              "type": "private"
            },
            "date": 1454119029,
            "text": "/startup"
          }
        }
        """

        response = self.client.post(receive_url, no_new_conversation_command, content_type='application/json')
        self.assertEqual(response.status_code, 201)
        response_json = response.json()
        self.assertEqual(response_json.get("description"), "Message accepted")

        msg1 = Msg.objects.get()
        self.assertEqual('3527065', msg1.contact.get_urn(TELEGRAM_SCHEME).path)
        self.assertEqual(INCOMING, msg1.direction)
        self.assertEqual(self.org, msg1.org)
        self.assertEqual(self.channel, msg1.channel)
        self.assertEqual("/startup", msg1.text)
        self.assertEqual(msg1.contact.name, 'Nic Pottier')

        Msg.objects.all().delete()
        Contact.objects.all().delete()

        no_new_conversation_command = """
        {
          "update_id": 174114370,
          "message": {
            "message_id": 41,
            "from": {
              "id": 3527065,
              "first_name": "Nic",
              "last_name": "Pottier"
            },
            "chat": {
              "id": 3527065,
              "first_name": "Nic",
              "last_name": "Pottier",
              "type": "private"
            },
            "date": 1454119029,
            "text": "start Hello World"
          }
        }
        """

        response = self.client.post(receive_url, no_new_conversation_command, content_type='application/json')
        self.assertEqual(response.status_code, 201)
        response_json = response.json()
        self.assertEqual(response_json.get("description"), "Message accepted")

        msg1 = Msg.objects.get()
        self.assertEqual('3527065', msg1.contact.get_urn(TELEGRAM_SCHEME).path)
        self.assertEqual(INCOMING, msg1.direction)
        self.assertEqual(self.org, msg1.org)
        self.assertEqual(self.channel, msg1.channel)
        self.assertEqual("start Hello World", msg1.text)
        self.assertEqual(msg1.contact.name, 'Nic Pottier')

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Ernie", urn='telegram:1234')
        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps({"result": {"message_id": 1234}}))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)
            self.clear_cache()

            self.assertEqual(mock.call_args[0][0], "https://api.telegram.org/botvalid/sendMessage")
            self.assertEqual(mock.call_args[0][1]['text'], "Test message")
            self.assertEqual(mock.call_args[0][1]['chat_id'], "1234")

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(400, "Error", method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Ernie", urn='telegram:1234')
        msg = joe.send("Test message", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps({"result": {"message_id": 1234}}))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)
            self.clear_cache()

            self.assertEqual(mock.call_args[0][0], "https://api.telegram.org/botvalid/sendPhoto")
            self.assertEqual(mock.call_args[0][1]['photo'], "https://example.com/attachments/pic.jpg")
            self.assertEqual(mock.call_args[0][1]['caption'], "Test message")
            self.assertEqual(mock.call_args[0][1]['chat_id'], "1234")

            msg = joe.send("Test message", self.admin, trigger_send=False,
                           attachments=['audio/mp3:https://example.com/attachments/sound.mp3'])[0]

            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)
            self.clear_cache()

            self.assertEqual(mock.call_args[0][0], "https://api.telegram.org/botvalid/sendAudio")
            self.assertEqual(mock.call_args[0][1]['audio'], "https://example.com/attachments/sound.mp3")
            self.assertEqual(mock.call_args[0][1]['caption'], "Test message")
            self.assertEqual(mock.call_args[0][1]['chat_id'], "1234")

            msg = joe.send("Test message", self.admin, trigger_send=False,
                           attachments=['video/mpeg4:https://example.com/attachments/video.mp4'])[0]

            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)
            self.clear_cache()

            self.assertEqual(mock.call_args[0][0], "https://api.telegram.org/botvalid/sendVideo")
            self.assertEqual(mock.call_args[0][1]['video'], "https://example.com/attachments/video.mp4")
            self.assertEqual(mock.call_args[0][1]['caption'], "Test message")
            self.assertEqual(mock.call_args[0][1]['chat_id'], "1234")

    @override_settings(SEND_MESSAGES=True)
    def test_send_quick_replies(self):
        quick_replies = ['Yes', 'No']
        joe = self.create_contact("Ernie", urn='telegram:1234')
        msg = joe.send("Test message", self.admin, trigger_send=False, quick_replies=quick_replies)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps({"result": {"message_id": 1234}}))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)
            self.clear_cache()

            mock_json = json.loads(mock.call_args[0][1]['reply_markup'])

            self.assertEqual(mock_json['keyboard'][0][0]['text'], "Yes")
            self.assertEqual(mock_json['keyboard'][1][0]['text'], "No")


class PlivoTest(TembaTest):

    def setUp(self):
        super(PlivoTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'PL', None, '+250788123123',
                                      config={Channel.CONFIG_PLIVO_AUTH_ID: 'plivo-auth-id',
                                              Channel.CONFIG_PLIVO_AUTH_TOKEN: 'plivo-auth-token',
                                              Channel.CONFIG_PLIVO_APP_ID: 'plivo-app-id'},
                                      uuid='00000000-0000-0000-0000-000000001234')

        self.joe = self.create_contact("Joe", "+250788383383")

    def test_release(self):
        with self.settings(IS_PROD=True):
            with patch('requests.delete') as mock:
                mock.return_value = MockResponse(200, "Success", method='POST')
                self.channel.release()
                self.channel.refresh_from_db()
                self.assertFalse(self.channel.is_active)
                self.assertTrue(mock.called)

    def test_receive(self):
        response = self.client.get(reverse('handlers.plivo_handler', args=['receive', 'not-real-uuid']), dict())
        self.assertEqual(400, response.status_code)

        data = dict(MessageUUID="msg-uuid", Text="Hey, there", To="254788383383", From="254788383383")
        receive_url = reverse('handlers.plivo_handler', args=['receive', self.channel.uuid])
        response = self.client.get(receive_url, data)
        self.assertEqual(400, response.status_code)

        data = dict(MessageUUID="msg-uuid", Text="Hey, there", To=self.channel.address.lstrip('+'), From="254788383383")
        response = self.client.get(receive_url, data)
        self.assertEqual(200, response.status_code)

        msg1 = Msg.objects.get()
        self.assertEqual("+254788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg1.direction)
        self.assertEqual(self.org, msg1.org)
        self.assertEqual(self.channel, msg1.channel)
        self.assertEqual('Hey, there', msg1.text)

    def test_status(self):
        # an invalid uuid
        data = dict(MessageUUID="-1", Status="delivered", From=self.channel.address.lstrip('+'), To="254788383383")
        response = self.client.get(reverse('handlers.plivo_handler', args=['status', 'not-real-uuid']), data)
        self.assertEqual(400, response.status_code)

        # a valid uuid, but invalid data
        delivery_url = reverse('handlers.plivo_handler', args=['status', self.channel.uuid])
        response = self.client.get(delivery_url, dict())
        self.assertEqual(400, response.status_code)

        response = self.client.get(delivery_url, data)
        self.assertEqual(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]
        msg.external_id = 'msg-uuid'
        msg.save(update_fields=('external_id',))

        data['MessageUUID'] = msg.external_id

        def assertStatus(sms, status, assert_status):
            sms.status = WIRED
            sms.save()
            data['Status'] = status
            response = self.client.get(delivery_url, data)
            self.assertEqual(200, response.status_code)
            sms = Msg.objects.get(external_id=sms.external_id)
            self.assertEqual(assert_status, sms.status)

        assertStatus(msg, 'queued', WIRED)
        assertStatus(msg, 'sent', SENT)
        assertStatus(msg, 'delivered', DELIVERED)
        assertStatus(msg, 'undelivered', SENT)
        assertStatus(msg, 'rejected', FAILED)

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        msg = self.joe.send("Test message", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(202,
                                             json.dumps({"message": "message(s) queued",
                                                         "message_uuid": ["db3ce55a-7f1d-11e1-8ea7-1231380bc196"],
                                                         "api_id": "db342550-7f1d-11e1-8ea7-1231380bc196"}))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            self.assertEqual(mock.call_args[1]['json']['text'], "Test message")
            self.clear_cache()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(400, "Error", method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)

        with patch('requests.get') as mock:
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        msg = self.joe.send("MT", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(202,
                                             json.dumps({"message": "message(s) queued",
                                                         "message_uuid": ["db3ce55a-7f1d-11e1-8ea7-1231380bc196"],
                                                         "api_id": "db342550-7f1d-11e1-8ea7-1231380bc196"}))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)

            self.assertEqual(mock.call_args[1]['json']['text'],
                             "MT\nhttps://example.com/attachments/pic.jpg")

            self.clear_cache()


class TwitterTest(TembaTest):

    def setUp(self):
        super(TwitterTest, self).setUp()

        self.channel.delete()

        # an old style Twitter channel which would use Mage for receiving messages
        self.twitter = Channel.create(self.org, self.user, None, 'TT', None, 'billy_bob',
                                      config={'oauth_token': 'abcdefghijklmnopqrstuvwxyz', 'oauth_token_secret': '0123456789'},
                                      uuid='00000000-0000-0000-0000-000000002345')

        # a new style Twitter channel configured for the Webhooks API
        self.twitter_beta = Channel.create(self.org, self.user, None, 'TWT', None, 'cuenca_facts',
                                           config={'handle_id': 10001,
                                                   'api_key': 'APIKEY',
                                                   'api_secret': 'APISECRET',
                                                   'access_token': 'abcdefghijklmnopqrstuvwxyz',
                                                   'access_token_secret': '0123456789'},
                                           uuid='00000000-0000-0000-0000-000000001234')

        self.joe = self.create_contact("Joe", twitterid='10002')

    def signed_request(self, url, data, api_secret='APISECRET'):
        """
        Makes a post to the Twitter handler with a computed signature
        """
        body = json.dumps(data)
        signature = generate_twitter_signature(body, api_secret)

        return self.client.post(url, body, content_type="application/json", HTTP_X_TWITTER_WEBHOOKS_SIGNATURE=signature)

    def webhook_payload(self, external_id, text, sender, target):
        return {
            "direct_message_events": [
                {
                    "created_timestamp": "1494877823220",
                    "message_create": {
                        "message_data": {
                            "text": text,
                        },
                        "sender_id": sender['id'],
                        "target": {"recipient_id": target['id']}
                    },
                    "type": "message_create",
                    "id": external_id
                }
            ],
            "users": {
                sender['id']: {"id": sender['id'], "name": sender['name'], "screen_name": sender['screen_name']},
                target['id']: {"id": target['id'], "name": target['name'], "screen_name": target['screen_name']}
            }
        }

    def test_crc_check(self):
        # try requesting from a non-existent channel
        response = self.client.get(reverse('handlers.twitter_handler', args=['xyz']) + '?crc_token=123456')
        self.assertEqual(response.status_code, 400)

        response = self.client.get(reverse('handlers.twitter_handler', args=[self.twitter_beta.uuid]) + '?crc_token=123456')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'response_token': generate_twitter_signature('123456', 'APISECRET')})

    def test_receive(self):
        data = self.webhook_payload('ext1', "Thanks for the info!",
                                    dict(id='10002', name="Joe", screen_name="joe81"),
                                    dict(id='10001', name="Cuenca Facts", screen_name="cuenca_facts"))

        # try sending to a non-existent channel
        response = self.signed_request(reverse('handlers.twitter_handler', args=['xyz']), data)
        self.assertEqual(response.status_code, 400)

        # try sending with an invalid request signature
        response = self.signed_request(reverse('handlers.twitter_handler', args=[self.twitter_beta.uuid]), data, api_secret='XYZ')
        self.assertEqual(response.status_code, 400)

        response = self.signed_request(reverse('handlers.twitter_handler', args=[self.twitter_beta.uuid]), data)
        self.assertEqual(response.status_code, 200)

        msg = Msg.objects.get()
        self.assertEqual(msg.text, "Thanks for the info!")
        self.assertEqual(msg.contact, self.joe)
        self.assertEqual(msg.contact_urn, self.joe.get_urns()[0])
        self.assertEqual(msg.external_id, 'ext1')

        # check that a message from us isn't saved
        data = self.webhook_payload('ext2', "It rains a lot in Cuenca",
                                    dict(id='10001', name="Cuenca Facts", screen_name="cuenca_facts"),
                                    dict(id='10002', name="Joe", screen_name="joe81"))
        response = self.signed_request(reverse('handlers.twitter_handler', args=[self.twitter_beta.uuid]), data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Msg.objects.count(), 1)

        # check we do not overwrite the existing contact name
        data = self.webhook_payload('ext3', "Awesome!",
                                    dict(id='10002', name="Davis", screen_name="joe81"),
                                    dict(id='10001', name="Cuenca Facts", screen_name="cuenca_facts"))
        response = self.signed_request(reverse('handlers.twitter_handler', args=[self.twitter_beta.uuid]), data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Msg.objects.count(), 2)
        msg = Msg.objects.filter(text='Awesome!').first()
        self.assertEqual(msg.contact, self.joe)
        self.assertEqual(msg.contact.name, "Joe")  # name should not be updated to Davis
        self.assertEqual(msg.contact_urn, self.joe.get_urns()[0])
        self.assertEqual(msg.external_id, 'ext3')

        # we create a contact if it does not exist yet
        data = self.webhook_payload('ext4', "Hello!",
                                    dict(id='10003', name="Kelly", screen_name="joe81"),
                                    dict(id='10001', name="Cuenca Facts", screen_name="cuenca_facts"))
        response = self.signed_request(reverse('handlers.twitter_handler', args=[self.twitter_beta.uuid]), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Msg.objects.count(), 3)
        msg = Msg.objects.filter(text='Hello!').first()
        self.assertEqual(msg.contact.name, "Kelly")

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        msg = self.joe.send("MT", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('twython.Twython.send_direct_message') as mock:
            mock.return_value = dict(id=1234567890)

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # assert we were only called once
            self.assertEqual(1, mock.call_count)

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertEqual('1234567890', msg.external_id)
            self.assertTrue(msg.sent_on)
            self.assertEqual(mock.call_args[1]['text'], "MT\nhttps://example.com/attachments/pic.jpg")

            self.clear_cache()

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        testers = self.create_group("Testers", [self.joe])

        msg = self.joe.send("This is a long message, longer than just 160 characters, it spans what was before "
                            "more than one message but which is now but one, solitary message, going off into the "
                            "Twitterverse to tweet away.",
                            self.admin, trigger_send=False)[0]

        with patch('requests.sessions.Session.post') as mock:
            mock.return_value = MockResponse(200, json.dumps(dict(id=1234567890)))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # assert we were only called once
            self.assertEqual(1, mock.call_count)
            self.assertEqual("10002", mock.call_args[1]['data']['user_id'])

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertEqual('1234567890', msg.external_id)
            self.assertTrue(msg.sent_on)
            self.assertEqual(mock.call_args[1]['data']['text'], msg.text)

            self.clear_cache()

        ChannelLog.objects.all().delete()

        msg.contact_urn.path = "joe81"
        msg.contact_urn.scheme = 'twitter'
        msg.contact_urn.display = None
        msg.contact_urn.identity = "twitter:joe81"
        msg.contact_urn.save()

        with patch('requests.sessions.Session.post') as mock:
            mock.return_value = MockResponse(200, json.dumps(dict(id=1234567890)))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # assert we were only called once
            self.assertEqual(1, mock.call_count)
            self.assertEqual("joe81", mock.call_args[1]['data']['screen_name'])

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertEqual('1234567890', msg.external_id)
            self.assertTrue(msg.sent_on)
            self.assertEqual(mock.call_args[1]['data']['text'], msg.text)

            self.clear_cache()

        ChannelLog.objects.all().delete()

        with patch('requests.sessions.Session.post') as mock:
            mock.side_effect = TwythonError("Failed to send message")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)
            self.assertEqual("Failed to send message", ChannelLog.objects.get(msg=msg).description)

            self.clear_cache()

        ChannelLog.objects.all().delete()

        with patch('requests.sessions.Session.post') as mock:
            mock.side_effect = TwythonError("Different 403 error.", error_code=403)

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)

            # should not fail the contact
            contact = Contact.objects.get(id=self.joe.id)
            self.assertFalse(contact.is_stopped)
            self.assertEqual(contact.user_groups.count(), 1)

            # should record the right error
            self.assertTrue(ChannelLog.objects.get(msg=msg).description.find("Different 403 error") >= 0)

        with patch('requests.sessions.Session.post') as mock:
            mock.side_effect = TwythonError("You cannot send messages to users who are not following you.",
                                            error_code=403)

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # should fail the message
            msg.refresh_from_db()
            self.assertEqual(FAILED, msg.status)
            self.assertEqual(2, msg.error_count)

            # should be stopped
            contact = Contact.objects.get(id=self.joe.id)
            self.assertTrue(contact.is_stopped)
            self.assertEqual(contact.user_groups.count(), 0)

            self.clear_cache()

        self.joe.is_stopped = False
        self.joe.save()
        testers.update_contacts(self.user, [self.joe], add=True)

        with patch('requests.sessions.Session.post') as mock:
            mock.side_effect = TwythonError("There was an error sending your message: You can't send direct messages to this user right now.",
                                            error_code=403)

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # should fail the message
            msg.refresh_from_db()
            self.assertEqual(FAILED, msg.status)
            self.assertEqual(2, msg.error_count)

            # should fail the contact permanently (i.e. removed from groups)
            contact = Contact.objects.get(id=self.joe.id)
            self.assertTrue(contact.is_stopped)
            self.assertEqual(contact.user_groups.count(), 0)

            self.clear_cache()

        self.joe.is_stopped = False
        self.joe.save()
        testers.update_contacts(self.user, [self.joe], add=True)

        with patch('requests.sessions.Session.post') as mock:
            mock.side_effect = TwythonError("Sorry, that page does not exist.", error_code=404)

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # should fail the message
            msg.refresh_from_db()
            self.assertEqual(msg.status, FAILED)
            self.assertEqual(msg.error_count, 2)

            # should fail the contact permanently (i.e. removed from groups)
            contact = Contact.objects.get(id=self.joe.id)
            self.assertTrue(contact.is_stopped)
            self.assertEqual(contact.user_groups.count(), 0)

            self.clear_cache()

    @override_settings(SEND_MESSAGES=True)
    def test_send_quick_replies(self):
        quick_replies = ['Yes', 'No']
        msg = self.joe.send("Hello, world!", self.admin, trigger_send=False, quick_replies=quick_replies)[0]

        with patch('requests.sessions.Session.post') as mock:
            response_dict = {
                "event": {
                    "created_timestamp": "1504717797522",
                    "message_create": {
                        "message_data": {
                            "text": "Hello, choose\u200b an option, please.",
                            "quick_reply": {
                                "type": "options",
                                "options": [{
                                    "label": "Yes"
                                }, {
                                    "label": "No"
                                }]
                            },
                            "entities": {
                                "symbols": [],
                                "user_mentions": [],
                                "hashtags": [],
                                "urls": []
                            }
                        },
                        "sender_id": "000000",
                        "target": {
                            "recipient_id": "10002"
                        }
                    },
                    "type": "message_create",
                    "id": "000000000000000000"
                }
            }
            mock.return_value = MockResponse(200, json.dumps(response_dict))

            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            mock.assert_called_with('https://api.twitter.com/1.1/direct_messages/events/new.json',
                                    files=None,
                                    data=json.dumps(dict(event=dict(message_create=dict(message_data=dict(
                                        text='Hello, world!',
                                        quick_reply=dict(
                                            type='options',
                                            options=[dict(label='Yes'), dict(label='No')]
                                        )),
                                        target=dict(recipient_id='10002')),
                                        type='message_create')
                                    )))

            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, "000000000000000000")
            self.assertEqual(msg.metadata, dict(quick_replies=quick_replies))
            data_args = json.loads(mock.call_args[1]['data'])
            message_data = data_args['event']['message_create']['message_data']
            self.assertEqual(message_data['quick_reply']['options'][0]['label'], 'Yes')
            self.assertEqual(message_data['quick_reply']['options'][1]['label'], 'No')
            self.clear_cache()


class MageHandlerTest(TembaTest):

    def setUp(self):
        super(MageHandlerTest, self).setUp()

        self.org.webhook = {"url": "http://fake.com/webhook.php"}
        self.org.webhook_events = ALL_EVENTS
        self.org.save()

        self.joe = self.create_contact("Joe", number="+250788383383")

        self.dyn_group = self.create_group("Bobs", query="twitter has bobby81")

    def create_contact_like_mage(self, name, twitter):
        """
        Creates a contact as if it were created in Mage, i.e. no event/group triggering or cache updating
        """
        contact = Contact.objects.create(org=self.org, name=name, is_active=True, is_blocked=False,
                                         uuid=uuid.uuid4(), is_stopped=False,
                                         modified_by=self.user, created_by=self.user,
                                         modified_on=timezone.now(), created_on=timezone.now())
        urn = ContactURN.objects.create(org=self.org, contact=contact,
                                        identity="twitter:%s" % twitter, scheme="twitter", path=twitter, priority="90")
        return contact, urn

    def create_message_like_mage(self, text, contact, contact_urn=None):
        """
        Creates a message as it if were created in Mage, i.e. no topup decrementing or cache updating
        """
        if not contact_urn:
            contact_urn = contact.get_urn(TEL_SCHEME)
        return Msg.objects.create(org=self.org, text=text,
                                  direction=INCOMING, created_on=timezone.now(),
                                  channel=self.channel, contact=contact, contact_urn=contact_urn)

    @override_settings(MAGE_AUTH_TOKEN='abc123')
    def test_handle_message(self):
        url = reverse('handlers.mage_handler', args=['handle_message'])
        headers = dict(HTTP_AUTHORIZATION='Token %s' % settings.MAGE_AUTH_TOKEN)

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_FLOWS])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(1, contact_counts[ContactGroup.TYPE_ALL])
        self.assertEqual(1000, self.org.get_credits_remaining())

        msg = self.create_message_like_mage(text="Hello 1", contact=self.joe)

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_INBOX])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(1, contact_counts[ContactGroup.TYPE_ALL])

        self.assertEqual(1000, self.org.get_credits_remaining())

        # check that GET doesn't work
        response = self.client.get(url, dict(message_id=msg.pk), **headers)
        self.assertEqual(405, response.status_code)

        # check that POST does work
        response = self.client.post(url, dict(message_id=msg.pk, new_contact=False), **headers)
        self.assertEqual(200, response.status_code)

        # check that new message is handled and has a topup
        msg = Msg.objects.get(pk=msg.pk)
        self.assertEqual('H', msg.status)
        self.assertEqual(self.welcome_topup, msg.topup)

        # check for a web hook event
        event = WebHookEvent.objects.get(org=self.org, event=WebHookEvent.TYPE_SMS_RECEIVED).data
        self.assertEqual(msg.id, event['sms'])

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_INBOX])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(1, contact_counts[ContactGroup.TYPE_ALL])

        self.assertEqual(999, self.org.get_credits_remaining())

        # check that a message that has a topup, doesn't decrement twice
        msg = self.create_message_like_mage(text="Hello 2", contact=self.joe)
        (msg.topup_id, amount) = self.org.decrement_credit()
        msg.save()

        self.client.post(url, dict(message_id=msg.pk, new_contact=False), **headers)
        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(2, msg_counts[SystemLabel.TYPE_INBOX])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(1, contact_counts[ContactGroup.TYPE_ALL])

        self.assertEqual(998, self.org.get_credits_remaining())

        # simulate scenario where Mage has added new contact with name that should put it into a dynamic group
        mage_contact, mage_contact_urn = self.create_contact_like_mage("Bob", "bobby81")
        msg = self.create_message_like_mage(text="Hello via Mage", contact=mage_contact, contact_urn=mage_contact_urn)

        response = self.client.post(url, dict(message_id=msg.pk, new_contact=True), **headers)
        self.assertEqual(200, response.status_code)

        msg = Msg.objects.get(pk=msg.pk)
        self.assertEqual('H', msg.status)
        self.assertEqual(self.welcome_topup, msg.topup)

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(3, msg_counts[SystemLabel.TYPE_INBOX])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(2, contact_counts[ContactGroup.TYPE_ALL])

        self.assertEqual(997, self.org.get_credits_remaining())

        # check that contact ended up dynamic group
        self.assertEqual([mage_contact], list(self.dyn_group.contacts.order_by('name')))

        # check invalid auth key
        response = self.client.post(url, dict(message_id=msg.pk), **dict(HTTP_AUTHORIZATION='Token xyz'))
        self.assertEqual(401, response.status_code)

        # check rejection of empty or invalid msgId
        response = self.client.post(url, dict(), **headers)
        self.assertEqual(400, response.status_code)
        response = self.client.post(url, dict(message_id='xx'), **headers)
        self.assertEqual(400, response.status_code)

    @override_settings(MAGE_AUTH_TOKEN='abc123')
    def test_follow_notification(self):
        url = reverse('handlers.mage_handler', args=['follow_notification'])
        headers = dict(HTTP_AUTHORIZATION='Token %s' % settings.MAGE_AUTH_TOKEN)

        flow = self.create_flow()

        channel = Channel.create(self.org, self.user, None, 'TT', "Twitter Channel", address="billy_bob")

        Trigger.objects.create(created_by=self.user, modified_by=self.user, org=self.org,
                               trigger_type=Trigger.TYPE_FOLLOW, flow=flow, channel=channel)

        contact = self.create_contact("Mary Jo", twitter='mary_jo')
        urn = contact.get_urn(TWITTER_SCHEME)

        response = self.client.post(url, dict(channel_id=channel.id, contact_urn_id=urn.id), **headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, flow.runs.all().count())
        self.assertTrue(ChannelEvent.objects.filter(channel=channel, contact=contact,
                                                    event_type=ChannelEvent.TYPE_FOLLOW))

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(2, contact_counts[ContactGroup.TYPE_ALL])

        # simulate a a follow from existing stopped contact
        contact.stop(self.admin)

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(1, contact_counts[ContactGroup.TYPE_ALL])

        response = self.client.post(url, dict(channel_id=channel.id, contact_urn_id=urn.id), **headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, flow.runs.all().count())

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(2, contact_counts[ContactGroup.TYPE_ALL])

        contact.refresh_from_db()
        self.assertFalse(contact.is_stopped)

        # simulate scenario where Mage has added new contact with name that should put it into a dynamic group
        mage_contact, mage_contact_urn = self.create_contact_like_mage("Bob", "bobby81")

        response = self.client.post(url, dict(channel_id=channel.id,
                                              contact_urn_id=mage_contact_urn.id, new_contact=True), **headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(3, flow.runs.all().count())

        # check that contact ended up dynamic group
        self.assertEqual([mage_contact], list(self.dyn_group.contacts.order_by('name')))

        # check contact count updated
        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts[ContactGroup.TYPE_ALL], 3)

        # simulate the follow of a released channel
        channel_events_count = ChannelEvent.objects.filter(channel=channel).count()
        channel.release()

        response = self.client.post(url, dict(channel_id=channel.id, contact_urn_id=urn.id), **headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(ChannelEvent.objects.filter(channel=channel).count(), channel_events_count)

    @override_settings(MAGE_AUTH_TOKEN='abc123')
    def test_stop_contact(self):
        url = reverse('handlers.mage_handler', args=['stop_contact'])
        headers = dict(HTTP_AUTHORIZATION='Token %s' % settings.MAGE_AUTH_TOKEN)
        contact = self.create_contact("Mary Jo", twitter='mary_jo')

        response = self.client.post(url, dict(contact_id=contact.id), **headers)
        self.assertEqual(200, response.status_code)

        # check the contact got stopped
        contact.refresh_from_db()
        self.assertTrue(contact.is_stopped)

        # try with invalid id
        response = self.client.post(url, dict(contact_id=-1), **headers)

        # should get a 401
        self.assertEqual(400, response.status_code)


class StartMobileTest(TembaTest):

    def setUp(self):
        super(StartMobileTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'UA', 'ST', None, '1212',
                                      config=dict(username='st-user', password='st-password'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_received(self):
        body = """
        <message>
        <service type="sms" timestamp="1450450974" auth="asdfasdf" request_id="msg1"/>
        <from>+250788123123</from>
        <to>1515</to>
        <body content-type="content-type" encoding="utf8">Hello World</body>
        </message>
        """
        callback_url = reverse('handlers.start_handler', args=['receive', self.channel.uuid])
        response = self.client.post(callback_url, content_type='application/xml', data=body)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual('+250788123123', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World", msg.text)

        # try it with an invalid body
        response = self.client.post(callback_url, content_type='application/xml', data="invalid body")

        # should get a 400, as the body is invalid
        self.assertEqual(400, response.status_code)

        Msg.objects.all().delete()

        # empty text element from Start Mobile we create "" message
        body = """
        <message>
        <service type="sms" timestamp="1450450974" auth="asdfasdf" request_id="msg1"/>
        <from>+250788123123</from>
        <to>1515</to>
        <body content-type="content-type" encoding="utf8"></body>
        </message>
        """
        response = self.client.post(callback_url, content_type='application/xml', data=body)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual('+250788123123', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("", msg.text)

        # try it with an invalid channel
        callback_url = reverse('handlers.start_handler', args=['receive', '1234-asdf'])
        response = self.client.post(callback_url, content_type='application/xml', data=body)

        # should get 400 as the channel wasn't found
        self.assertEqual(400, response.status_code)

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+977788123123")
        msg = joe.send("Вітаємо в U-Report, системі опитувань про майбутнє країни.Зараз невеличка реєстрація.?",
                       self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200,
                                             """<status date='Wed, 25 May 2016 17:29:56 +0300'>
                                             <id>380502535130309161501</id><state>Accepted</state></status>""")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, "380502535130309161501")

            # check the call that was made
            self.assertEqual('http://bulk.startmobile.com.ua/clients.php', mock.call_args[0][0])
            message_el = ET.fromstring(mock.call_args[1]['data'])
            self.assertEqual(message_el.find('service').attrib, dict(source='1212', id='single', validity='+12 hours'))
            self.assertEqual(message_el.find('body').text, msg.text)

            self.clear_cache()

        # return 400
        with patch('requests.post') as mock:
            mock.return_value = MockResponse(400, "Error", method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(1, msg.error_count)
            self.assertTrue(msg.next_attempt)
            self.clear_cache()

        # return invalid XML
        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, "<error>This is an error</error>", method='POST')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)
            self.clear_cache()

        # unexpected exception
        with patch('requests.post') as mock:
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should be marked as an error
            msg.refresh_from_db()
            self.assertEqual(FAILED, msg.status)
            self.assertEqual(2, msg.error_count)
            self.assertTrue(msg.next_attempt)
            self.clear_cache()

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+977788123123")
        msg = joe.send("MT", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200,
                                             """<status date='Wed, 25 May 2016 17:29:56 +0300'>
                                             <id>380502535130309161501</id><state>Accepted</state></status>""")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(WIRED, msg.status)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, "380502535130309161501")

            # check the call that was made
            self.assertEqual('http://bulk.startmobile.com.ua/clients.php', mock.call_args[0][0])
            message_el = ET.fromstring(mock.call_args[1]['data'])
            self.assertEqual(message_el.find('service').attrib, dict(source='1212', id='single', validity='+12 hours'))
            self.assertEqual(message_el.find('body').text, "MT\nhttps://example.com/attachments/pic.jpg")

            self.clear_cache()


class ChikkaTest(TembaTest):

    def setUp(self):
        super(ChikkaTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'PH', 'CK', None, '920920',
                                      uuid='00000000-0000-0000-0000-000000001234')

        self.channel.config = {Channel.CONFIG_USERNAME: 'username', Channel.CONFIG_PASSWORD: 'password'}
        self.channel.save()

    def test_status(self):
        # try with an invalid channel uuid
        data = dict(message_type='outgoing', message_id=1001, status='FAILED')
        response = self.client.post(reverse('handlers.chikka_handler', args=['not-real-uuid']), data)
        self.assertEqual(400, response.status_code)

        # ok, try with a valid uuid, but invalid message id 1001, should return 400 as well
        response = self.client.post(reverse('handlers.chikka_handler', args=[self.channel.uuid]), data)
        self.assertEqual(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+63911231234")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]
        data['message_id'] = msg.id

        # valid id, invalid status, 400
        data['status'] = 'INVALID'
        response = self.client.post(reverse('handlers.chikka_handler', args=[self.channel.uuid]), data)
        self.assertEqual(400, response.status_code)

        def assertStatus(sms, status, assert_status):
            sms.status = WIRED
            sms.save()

            data['status'] = status
            response = self.client.post(reverse('handlers.chikka_handler', args=[self.channel.uuid]), data)
            self.assertEqual(200, response.status_code)
            updated_sms = Msg.objects.get(pk=sms.id)
            self.assertEqual(assert_status, updated_sms.status)

        assertStatus(msg, 'FAILED', FAILED)
        assertStatus(msg, 'SENT', SENT)

    def test_receive(self):
        data = dict(message_type='incoming', mobile_number='639178020779', request_id='4004',
                    message='Hello World!', timestamp='1457670059.69')
        callback_url = reverse('handlers.chikka_handler', args=[self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual("+639178020779", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World!", msg.text)
        self.assertEqual('4004', msg.external_id)
        self.assertEqual(msg.sent_on.date(), date(day=11, month=3, year=2016))

    def test_send(self):
        joe = self.create_contact("Joe", '+63911231234')

        # incoming message for a reply test
        incoming = Msg.create_incoming(self.channel, 'tel:+63911231234', "incoming message")
        incoming.external_id = '4004'
        incoming.save()

        msg = joe.send("Test message", self.admin, trigger_send=False)[0]

        with self.settings(SEND_MESSAGES=True):

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, "Success", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEqual(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                # check we were called as a send
                self.assertEqual(mock.call_args[1]['data']['message_type'], 'SEND')
                self.clear_cache()

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, "Success", method='POST')

                msg.response_to = incoming
                msg.save()

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEqual(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                # assert that we were called as a reply
                self.assertEqual(mock.call_args[1]['data']['message_type'], 'REPLY')
                self.assertEqual(mock.call_args[1]['data']['request_id'], '4004')

                self.clear_cache()

            with patch('requests.post') as mock:
                error = dict(status=400, message='BAD REQUEST', description='Invalid/Used Request ID')

                # first request (as a reply) is an error, second should be success without request id
                mock.side_effect = [
                    MockResponse(400, json.dumps(error), method='POST'),
                    MockResponse(200, 'Success', method='POST')
                ]

                msg.response_to = incoming
                msg.save()

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEqual(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                first_call_args = mock.call_args_list[0][1]['data']
                second_call_args = mock.call_args_list[1][1]['data']

                # first request is as a reply
                self.assertEqual(first_call_args['message_type'], 'REPLY')
                self.assertEqual(first_call_args['request_id'], '4004')

                # but when that fails, we should try again as a send
                self.assertEqual(second_call_args['message_type'], 'SEND')
                self.assertNotIn('request_id', second_call_args)

                # our message should be succeeded
                msg.refresh_from_db()
                self.assertEqual(WIRED, msg.status)
                self.assertEqual(0, msg.error_count)

                self.clear_cache()

            # test with an invalid request id, then an unexpected error
            with patch('requests.post') as mock:
                error = dict(status=400, message='BAD REQUEST', description='Invalid/Used Request ID')

                # first request (as a reply) is an error, second should be success without request id
                mock.side_effect = [
                    MockResponse(400, json.dumps(error), method='POST'),
                    Exception("Unexpected Error")
                ]

                msg.response_to = incoming
                msg.save()

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEqual(ERRORED, msg.status)
                self.assertEqual(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "{}", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEqual(ERRORED, msg.status)
                self.assertEqual(2, msg.error_count)
                self.assertTrue(msg.next_attempt)

                self.clear_cache()

            with patch('requests.post') as mock:
                mock.side_effect = Exception("Couldn't reach server")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # should also have an error
                msg.refresh_from_db()

                # third try, we should be failed now
                self.assertEqual(FAILED, msg.status)
                self.assertEqual(2, msg.error_count)
                self.assertTrue(msg.next_attempt)

    def test_send_media(self):
        joe = self.create_contact("Joe", '+63911231234')

        # incoming message for a reply test
        incoming = Msg.create_incoming(self.channel, 'tel:+63911231234', "incoming message")
        incoming.external_id = '4004'
        incoming.save()

        msg = joe.send("MT", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with self.settings(SEND_MESSAGES=True):

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, "Success", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEqual(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                # check we were called as a send
                self.assertEqual(mock.call_args[1]['data']['message_type'], 'SEND')
                self.assertEqual(mock.call_args[1]['data']['message'], 'MT\nhttps://example.com/attachments/pic.jpg')
                self.clear_cache()


class JasminTest(TembaTest):

    def setUp(self):
        super(JasminTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'JS', None, '1234',
                                      config=dict(username='jasmin-user', password='jasmin-pass', send_url='http://foo/'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def tearDown(self):
        super(JasminTest, self).tearDown()

    def test_status(self):
        # ok, what happens with an invalid uuid?
        data = dict(id="-1", dlvr="0", err="0")
        response = self.client.post(reverse('handlers.jasmin_handler', args=['status', 'not-real-uuid']), data)
        self.assertEqual(400, response.status_code)

        # ok, try with a valid uuid, but invalid message id -1
        delivery_url = reverse('handlers.jasmin_handler', args=['status', self.channel.uuid])
        response = self.client.post(delivery_url, data)
        self.assertEqual(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]
        msg.external_id = "jasmin-external-id"
        msg.save(update_fields=('external_id',))

        data['id'] = msg.external_id

        def assertStatus(sms, dlvrd, err, assert_status):
            data['dlvrd'] = dlvrd
            data['err'] = err
            response = self.client.post(reverse('handlers.jasmin_handler', args=['status', self.channel.uuid]), data)
            self.assertEqual(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEqual(assert_status, sms.status)

        assertStatus(msg, 0, 0, WIRED)
        assertStatus(msg, 1, 0, DELIVERED)
        assertStatus(msg, 0, 1, FAILED)

    def test_receive(self):
        from temba.utils import gsm7

        data = {
            'to': '1234',
            'from': '0788383383',
            'coding': '0',
            'content': gsm7.encode("événement")[0],
            'id': 'external1'
        }
        callback_url = reverse('handlers.jasmin_handler', args=['receive', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertContains(response, "ACK/Jasmin")

        # load our message
        msg = Msg.objects.get()
        self.assertEqual("+250788383383", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("événement", msg.text)

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        from temba.utils import gsm7

        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("événement", self.admin, trigger_send=False)[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, 'Success "07033084-5cfd-4812-90a4-e4d24ffb6e3d"')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, '07033084-5cfd-4812-90a4-e4d24ffb6e3d')

            # assert we were properly encoded
            self.assertEqual(mock.call_args[1]['params']['content'], gsm7.encode('événement')[0])

            # check DLR related parameters
            self.assertEqual(mock.call_args[1]['params']['dlr'], 'yes')
            self.assertEqual(mock.call_args[1]['params']['dlr-level'], '2')
            self.assertEqual(mock.call_args[1]['params']['dlr-method'], 'POST')
            self.assertRegex(mock.call_args[1]['params']['dlr-url'], '^https?://')

            self.clear_cache()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(412, 'Error “No route found”')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

        with patch('requests.get') as mock:
            # force an exception
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        from temba.utils import gsm7

        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("MT", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, 'Success "07033084-5cfd-4812-90a4-e4d24ffb6e3d"')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, '07033084-5cfd-4812-90a4-e4d24ffb6e3d')

            # assert we were properly encoded
            self.assertEqual(mock.call_args[1]['params']['content'],
                             gsm7.encode('MT\nhttps://example.com/attachments/pic.jpg')[0])

            self.clear_cache()


class JunebugTestMixin(object):

    def mk_event(self, **kwargs):
        default = {
            'event_type': 'submitted',
            'message_id': 'message-id',
            'timestamp': '2017-01-01 00:00:00+0000',
        }
        default.update(kwargs)
        return default

    def mk_ussd_msg(self, session_event='new', session_id=None, **kwargs):
        return self.mk_msg(
            channel_data={'session_event': session_event,
                          'session_id': session_id}, **kwargs)

    def mk_msg(self, **kwargs):
        default = {
            "channel_data": {},
            "from": "+27123456789",
            "channel_id": "channel-id",
            "timestamp": "2017-01-01 00:00:00.00",
            "content": "content",
            "to": "to-addr",
            "reply_to": None,
            "message_id": "message-id"
        }
        default.update(kwargs)
        return default


class JunebugTest(JunebugTestMixin, TembaTest):

    def setUp(self):
        super(JunebugTest, self).setUp()

        self.channel.delete()

        self.channel = Channel.create(
            self.org, self.user, 'RW', 'JN', None, '1234',
            config=dict(username='junebug-user', password='junebug-pass', send_url='http://example.org/'),
            uuid='00000000-0000-0000-0000-000000001234',
            role=Channel.DEFAULT_ROLE)

    def tearDown(self):
        super(JunebugTest, self).tearDown()

    def test_get_request(self):
        response = self.client.get(
            reverse('handlers.junebug_handler',
                    args=['event', self.channel.uuid]))
        self.assertEqual(response.status_code, 400)

    def test_status_with_invalid_event(self):
        delivery_url = reverse('handlers.junebug_handler',
                               args=['event', self.channel.uuid])
        response = self.client.post(delivery_url, data=json.dumps({}),
                                    content_type='application/json')
        self.assertContains(response, 'Missing one of', status_code=400)

    def test_status(self):
        # ok, what happens with an invalid uuid?
        data = self.mk_event()
        response = self.client.post(
            reverse('handlers.junebug_handler',
                    args=['event', 'not-real-uuid']),
            data=json.dumps(data),
            content_type='application/json')
        self.assertEqual(400, response.status_code)

        # ok, try with a valid uuid, but invalid message id -1
        delivery_url = reverse('handlers.junebug_handler',
                               args=['event', self.channel.uuid])
        response = self.client.post(delivery_url, data=json.dumps(data),
                                    content_type='application/json')
        self.assertEqual(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]
        msg.external_id = data['message_id']
        msg.save(update_fields=('external_id',))

        # data['id'] = msg.external_id

        def assertStatus(sms, event_type, assert_status):
            data['event_type'] = event_type
            response = self.client.post(
                reverse('handlers.junebug_handler',
                        args=['event', self.channel.uuid]),
                data=json.dumps(data),
                content_type='application/json')
            self.assertEqual(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEqual(assert_status, sms.status)

        assertStatus(msg, 'submitted', SENT)
        assertStatus(msg, 'delivery_succeeded', DELIVERED)
        assertStatus(msg, 'delivery_failed', FAILED)
        assertStatus(msg, 'rejected', FAILED)

    def test_status_invalid_message_id(self):
        # ok, what happens with an invalid uuid?
        data = self.mk_event()
        response = self.client.post(
            reverse('handlers.junebug_handler',
                    args=['event', self.channel.uuid]),
            data=json.dumps(data),
            content_type='application/json')
        self.assertContains(
            response,
            "Message with external id of '%s' not found" % (
                data['message_id'],), status_code=400)

    def test_status_with_auth(self):
        config = self.channel.config
        config[Channel.CONFIG_SECRET] = "UjOq8ATo2PDS6L08t6vlqSoK"
        self.channel.config = config
        self.channel.save(update_fields=['config'])

        data = self.mk_event()
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]
        msg.external_id = data['message_id']
        msg.save(update_fields=('external_id',))

        def assertStatus(sms, event_type, assert_status):
            data['event_type'] = event_type
            response = self.client.post(
                reverse('handlers.junebug_handler',
                        args=['event', self.channel.uuid]),
                data=json.dumps(data),
                content_type='application/json',
                HTTP_AUTHORIZATION="Token UjOq8ATo2PDS6L08t6vlqSoK")
            self.assertEqual(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEqual(assert_status, sms.status)

        assertStatus(msg, 'submitted', SENT)
        assertStatus(msg, 'delivery_succeeded', DELIVERED)
        assertStatus(msg, 'delivery_failed', FAILED)
        assertStatus(msg, 'rejected', FAILED)

    def test_status_incorrect_auth(self):
        config = self.channel.config
        config[Channel.CONFIG_SECRET] = "UjOq8ATo2PDS6L08t6vlqSoK"
        self.channel.config = config
        self.channel.save(update_fields=['config'])

        # ok, what happens with an invalid uuid?
        data = self.mk_event()
        response = self.client.post(
            reverse('handlers.junebug_handler',
                    args=['event', self.channel.uuid]),
            data=json.dumps(data),
            content_type='application/json',
            HTTP_AUTHORIZATION="Token Not_token")
        self.assertEqual(401, response.status_code)

    def test_receive_with_invalid_message(self):
        callback_url = reverse('handlers.junebug_handler',
                               args=['inbound', self.channel.uuid])
        response = self.client.post(callback_url, json.dumps({}),
                                    content_type='application/json')
        self.assertContains(response, 'Missing one of', status_code=400)

    def test_receive(self):
        data = self.mk_msg(content="événement")
        callback_url = reverse('handlers.junebug_handler',
                               args=['inbound', self.channel.uuid])
        response = self.client.post(callback_url, json.dumps(data),
                                    content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ack')

        # load our message
        msg = Msg.objects.get()
        self.assertEqual(data["from"], msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("événement", msg.text)

    def test_receive_with_auth(self):
        config = self.channel.config
        config[Channel.CONFIG_SECRET] = "UjOq8ATo2PDS6L08t6vlqSoK"
        self.channel.config = config
        self.channel.save(update_fields=['config'])

        data = self.mk_msg(content="événement")
        callback_url = reverse('handlers.junebug_handler',
                               args=['inbound', self.channel.uuid])
        response = self.client.post(callback_url, json.dumps(data),
                                    content_type='application/json',
                                    HTTP_AUTHORIZATION="Token UjOq8ATo2PDS6L08t6vlqSoK")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ack')

        # load our message
        msg = Msg.objects.get()
        self.assertEqual(data["from"], msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(INCOMING, msg.direction)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("événement", msg.text)

    def test_receive_with_incorrect_auth(self):
        config = self.channel.config
        config[Channel.CONFIG_SECRET] = "UjOq8ATo2PDS6L08t6vlqSoK"
        self.channel.config = config
        self.channel.save(update_fields=['config'])

        data = self.mk_msg(content="événement")
        callback_url = reverse('handlers.junebug_handler',
                               args=['inbound', self.channel.uuid])
        response = self.client.post(callback_url, json.dumps(data),
                                    content_type='application/json',
                                    HTTP_AUTHORIZATION="Token Not_token")

        self.assertEqual(response.status_code, 401)

    @override_settings(SEND_MESSAGES=True)
    def test_send_wired(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("événement", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps({
                'result': {
                    'message_id': '07033084-5cfd-4812-90a4-e4d24ffb6e3d',
                }
            }))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(
                msg.external_id, '07033084-5cfd-4812-90a4-e4d24ffb6e3d')

            self.clear_cache()

            self.assertEqual(mock.call_args[1]['json']['content'], "événement")

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("MT", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps({
                'result': {
                    'message_id': '07033084-5cfd-4812-90a4-e4d24ffb6e3d',
                }
            }))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(
                msg.external_id, '07033084-5cfd-4812-90a4-e4d24ffb6e3d')

            self.clear_cache()

            self.assertEqual(mock.call_args[1]['json']['content'], "MT\nhttps://example.com/attachments/pic.jpg")

    @override_settings(SEND_MESSAGES=True)
    def test_send_errored_remote(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("événement", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(499, 'Error')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

    @override_settings(SEND_MESSAGES=True)
    def test_send_errored_exception(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("événement", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            # force an exception
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

    @override_settings(SEND_MESSAGES=True)
    def test_send_deal_with_unexpected_response(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("événement", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps({
                'result': {
                    'unexpected': 'unpleasant surprise',
                }
            }))

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
            self.assertTrue(ChannelLog.objects.filter(description__icontains="Unable to read external message_id"))

    @override_settings(SEND_MESSAGES=True)
    def test_send_adds_auth(self):
        config = self.channel.config
        config[Channel.CONFIG_SECRET] = "UjOq8ATo2PDS6L08t6vlqSoK"
        self.channel.config = config
        self.channel.save(update_fields=['config'])

        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("événement", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps({
                'result': {
                    'message_id': '07033084-5cfd-4812-90a4-e4d24ffb6e3d',
                }
            }))

            # manually send it off
            self.channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
            self.assertEqual(mock.call_args[1]['json']['event_auth_token'],
                             "UjOq8ATo2PDS6L08t6vlqSoK")


class MbloxTest(TembaTest):

    def setUp(self):
        super(MbloxTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'MB', None, '1234',
                                      config=dict(username='mbox-user', password='mblox-pass'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def tearDown(self):
        super(MbloxTest, self).tearDown()

    def test_dlr(self):
        # invalid uuid
        data = dict(batch_id="-1", status="Failed", type="recipient_delivery_report_sms")
        response = self.client.post(reverse('handlers.mblox_handler', args=['not-real-uuid']), json.dumps(data),
                                    content_type="application/json")
        self.assertEqual(400, response.status_code)

        delivery_url = reverse('handlers.mblox_handler', args=[self.channel.uuid])

        # missing batch_id param
        data = dict(status="Failed", type="recipient_delivery_report_sms")
        response = self.client.post(delivery_url, json.dumps(data), content_type="application/json")
        self.assertEqual(400, response.status_code)

        # missing type params
        data = dict(status="Failed")
        response = self.client.post(delivery_url, json.dumps(data), content_type="application/json")
        self.assertEqual(400, response.status_code)

        # valid uuid, invalid batch_id
        data = dict(batch_id="-1", status="Failed", type="recipient_delivery_report_sms")
        response = self.client.post(delivery_url, json.dumps(data), content_type="application/json")
        self.assertEqual(400, response.status_code)

        # create test message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]
        msg.external_id = "mblox-id"
        msg.save(update_fields=('external_id',))

        data['batch_id'] = msg.external_id

        def assertStatus(msg, status, assert_status):
            Msg.objects.filter(id=msg.id).update(status=WIRED)
            data['status'] = status
            response = self.client.post(delivery_url, json.dumps(data), content_type="application/json")
            self.assertContains(response, "SMS Updated: %d" % msg.id)
            msg = Msg.objects.get(pk=msg.id)
            self.assertEqual(assert_status, msg.status)

        assertStatus(msg, "Delivered", DELIVERED)
        assertStatus(msg, "Dispatched", SENT)
        assertStatus(msg, "Aborted", FAILED)
        assertStatus(msg, "Rejected", FAILED)
        assertStatus(msg, "Failed", FAILED)
        assertStatus(msg, "Expired", FAILED)

    def test_receive(self):
        data = {
            "id": "OzQ5UqIOdoY8",
            "from": "12067799294",
            "to": "18444651185",
            "body": "MO",
            "type": "mo_text",
            "received_at": "2016-03-30T19:33:06.643Z"
        }
        callback_url = reverse('handlers.mblox_handler', args=[self.channel.uuid])
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        msg = Msg.objects.get()

        self.assertContains(response, "SMS Accepted: %d" % msg.id)

        # load our message
        self.assertEqual(msg.contact.get_urn(TEL_SCHEME).path, "+12067799294")
        self.assertEqual(msg.direction, INCOMING)
        self.assertEqual(msg.org, self.org)
        self.assertEqual(msg.channel, self.channel)
        self.assertEqual(msg.text, "MO")
        self.assertEqual(msg.sent_on.date(), date(day=30, month=3, year=2016))

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("MT", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "id":"OzYDlvf3SQVc" }')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, 'OzYDlvf3SQVc')
            self.clear_cache()

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(412, 'Error')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            self.assertTrue(mock.called)
            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

        with patch('requests.post') as mock:
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            self.assertTrue(mock.called)
            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("MT", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "id":"OzYDlvf3SQVc" }')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, 'OzYDlvf3SQVc')
            self.clear_cache()

            self.assertEqual(json.loads(mock.call_args[0][1])['body'], "MT\nhttps://example.com/attachments/pic.jpg")


class FacebookWhitelistTest(TembaTest):

    def setUp(self):
        super(FacebookWhitelistTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, None, 'FB', None, '1234',
                                      config={Channel.CONFIG_AUTH_TOKEN: 'auth'},
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_whitelist(self):
        whitelist_url = reverse('channels.channel_facebook_whitelist', args=[self.channel.uuid])
        response = self.client.get(whitelist_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)
        response = self.client.get(reverse('channels.channel_read', args=[self.channel.uuid]))

        self.assertContains(response, whitelist_url)

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(400, '{"error": { "message": "FB Error" } }')
            response = self.client.post(whitelist_url, dict(whitelisted_domain='https://foo.bar'))
            self.assertFormError(response, 'form', None, 'FB Error')

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "ok": "true" }')
            response = self.client.post(whitelist_url, dict(whitelisted_domain='https://foo.bar'))

            mock.assert_called_once_with('https://graph.facebook.com/v2.6/me/thread_settings?access_token=auth',
                                         json=dict(setting_type='domain_whitelisting',
                                                   whitelisted_domains=['https://foo.bar'],
                                                   domain_action_type='add'))

            self.assertNoFormErrors(response)


class FacebookTest(TembaTest):

    TEST_INCOMING = """
    {
        "entry": [{
          "id": "208685479508187",
          "messaging": [{
            "message": {
              "text": "hello world",
              "mid": "external_id"
            },
            "recipient": {
              "id": "1234"
            },
            "sender": {
              "id": "5678"
            },
            "timestamp": 1459991487970
          }],
          "time": 1459991487970
        }]
    }
    """

    def setUp(self):
        super(FacebookTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, None, 'FB', None, '1234',
                                      config={Channel.CONFIG_AUTH_TOKEN: 'auth'},
                                      uuid='00000000-0000-0000-0000-000000001234')

    def tearDown(self):
        super(FacebookTest, self).tearDown()

    def test_dlr(self):
        # invalid uuid
        body = dict()
        response = self.client.post(reverse('handlers.facebook_handler', args=['invalid']), json.dumps(body),
                                    content_type="application/json")
        self.assertEqual(400, response.status_code)

        # invalid body
        response = self.client.post(reverse('handlers.facebook_handler', args=[self.channel.uuid]), json.dumps(body),
                                    content_type="application/json")
        self.assertEqual(400, response.status_code)

        # no known msgs, gracefully ignore
        body = dict(entry=[dict()])
        response = self.client.post(reverse('handlers.facebook_handler', args=[self.channel.uuid]), json.dumps(body),
                                    content_type="application/json")
        self.assertEqual(200, response.status_code)

        # create test message to update
        joe = self.create_contact("Joe Biden", urn='facebook:1234')
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)[0]
        msg.external_id = "fb-message-id-out"
        msg.save(update_fields=('external_id',))

        body = dict(entry=[dict(messaging=[dict(delivery=dict(mids=[msg.external_id]))])])
        response = self.client.post(reverse('handlers.facebook_handler', args=[self.channel.uuid]), json.dumps(body),
                                    content_type='application/json')

        self.assertEqual(response.status_code, 200)

        msg.refresh_from_db()
        self.assertEqual(msg.status, DELIVERED)

        # ignore incoming messages delivery reports
        msg = self.create_msg(direction=INCOMING, contact=joe, text="Read message")
        msg.external_id = "fb-message-id-in"
        msg.save(update_fields=('external_id',))

        status = msg.status

        body = dict(entry=[dict(messaging=[dict(delivery=dict(mids=[msg.external_id]))])])
        response = self.client.post(reverse('handlers.facebook_handler', args=[self.channel.uuid]), json.dumps(body),
                                    content_type='application/json')

        self.assertEqual(response.status_code, 200)

        msg.refresh_from_db()
        self.assertEqual(msg.status, status)

    def test_affinity(self):
        data = json.loads(FacebookTest.TEST_INCOMING)

        with patch('requests.get') as mock_get:
            mock_get.return_value = MockResponse(200, '{"first_name": "Ben","last_name": "Haggerty"}')

            callback_url = reverse('handlers.facebook_handler', args=[self.channel.uuid])
            response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
            self.assertEqual(response.status_code, 200)

            # check the channel affinity for our URN
            urn = ContactURN.objects.get(identity='facebook:5678')
            self.assertEqual(self.channel, urn.channel)

            # create another facebook channel
            channel2 = Channel.create(self.org, self.user, None, 'FB', None, '1234',
                                      config={Channel.CONFIG_AUTH_TOKEN: 'auth'},
                                      uuid='00000000-0000-0000-0000-000000012345')

            # have to change the message so we don't treat it as a duplicate
            data['entry'][0]['messaging'][0]['message']['text'] = '2nd Message'
            data['entry'][0]['messaging'][0]['message']['mid'] = 'external_id_2'

            callback_url = reverse('handlers.facebook_handler', args=[channel2.uuid])
            response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
            self.assertEqual(response.status_code, 200)

            urn = ContactURN.objects.get(identity='facebook:5678')
            self.assertEqual(channel2, urn.channel)

    def test_ignored_webhooks(self):
        TEST_PAYLOAD = """{
          "object": "page",
          "entry": [{
            "id": "208685479508187",
            "time": 1459991487970,
            "messaging": []
          }]
        }"""

        READ_ENTRY = """
        {
          "sender":{ "id":"1001" },
          "recipient":{ "id":"%s" },
          "timestamp":1458668856463,
          "read":{
            "watermark":1458668856253,
            "seq":38
          }
        }
        """

        ECHO_ENTRY = """{
          "sender": {"id": "1001"},
          "recipient": {"id": "%s"},
          "timestamp": 1467905036620,
          "message": {
            "is_echo": true,
            "app_id": 1077392885670130,
            "mid": "mid.1467905036543:c721a8364e45388954",
            "seq": 4,
            "text": "Echo Test"
          }
        }
        """

        LINK_ENTRY = """{
          "sender":{
            "id":"1001"
          },
          "recipient":{
            "id":"%s"
          },
          "timestamp":1234567890,
          "account_linking":{
            "status":"linked",
            "authorization_code":"PASS_THROUGH_AUTHORIZATION_CODE"
          }
        }
        """

        AUTH_ENTRY = """{
          "sender":{
            "id":"1001"
          },
          "recipient":{
            "id":"%s"
          },
          "timestamp":1234567890,
          "optin":{
            "ref":"PASS_THROUGH_PARAM"
          }
        }
        """

        ATTACHMENT_UNAVAILABLE = """{
          "sender":{
            "id":"1001"
          },
          "recipient":{
            "id":"%s"
          },
          "timestamp":1234567890,
          "message":{
            "mid":"mid.1471652393639:4ecd7f5649c8586032",
            "seq":"77866",
            "attachments":[{
              "title":"Attachment Unavailable",
              "url":null,
              "type":"fallback",
              "payload":null
            }]
          }
        }
        """

        callback_url = reverse('handlers.facebook_handler', args=[self.channel.uuid])
        for entry in (READ_ENTRY, ECHO_ENTRY, LINK_ENTRY, AUTH_ENTRY, ATTACHMENT_UNAVAILABLE):
            payload = json.loads(TEST_PAYLOAD)
            payload['entry'][0]['messaging'].append(json.loads(entry % self.channel.address))

            with patch('requests.get') as mock_get:
                mock_get.return_value = MockResponse(200, '{"first_name": "Ben","last_name": "Haggerty"}')
                response = self.client.post(callback_url, json.dumps(payload), content_type="application/json")

                # ignored but 200
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Ignored")

    def test_referrals_optin(self):
        # create two triggers for referrals
        flow = self.get_flow('favorites')
        Trigger.objects.create(org=self.org, trigger_type=Trigger.TYPE_REFERRAL, referrer_id='join',
                               flow=flow, created_by=self.admin, modified_by=self.admin)
        Trigger.objects.create(org=self.org, trigger_type=Trigger.TYPE_REFERRAL, referrer_id='signup',
                               flow=flow, created_by=self.admin, modified_by=self.admin)

        callback_url = reverse('handlers.facebook_handler', args=[self.channel.uuid])

        optin = """
        {
          "sender": { "id": "1122" },
          "recipient": { "id": "PAGE_ID" },
          "timestamp": 1234567890,
          "optin": {
            "ref": "join"
          }
        }
        """
        data = json.loads(FacebookTest.TEST_INCOMING)
        data['entry'][0]['messaging'][0] = json.loads(optin)
        response = self.client.post(callback_url, json.dumps(data), content_type='application/json')
        self.assertContains(response, 'Msg Ignored for recipient id: PAGE_ID')

        response = self.client.post(callback_url, json.dumps(data).replace('PAGE_ID', '1234'), content_type='application/json')
        self.assertEqual(200, response.status_code)

        # check that the user started the flow
        contact1 = Contact.objects.get(org=self.org, urns__path='1122')
        self.assertEqual("What is your favorite color?", contact1.msgs.all().first().text)

        # try an invalid optin (has fields for neither type)
        del data['entry'][0]['messaging'][0]['sender']
        response = self.client.post(callback_url, json.dumps(data).replace('PAGE_ID', '1234'), content_type='application/json')
        self.assertContains(response, '{"status": ["Ignored opt-in, no user_ref or sender"]}')

        # ok, use a user_ref optin instead
        entry = json.loads(optin)
        del entry['sender']
        entry['optin']['user_ref'] = 'user_ref2'
        data = json.loads(FacebookTest.TEST_INCOMING)
        data['entry'][0]['messaging'][0] = entry

        with override_settings(SEND_MESSAGES=True):
            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, '{"recipient_id":"1133", "message_id":"mid.external"}')

                response = self.client.post(callback_url, json.dumps(data).replace('PAGE_ID', '1234'),
                                            content_type='application/json')
                self.assertEqual(200, response.status_code)

                contact2 = Contact.objects.get(org=self.org, urns__path='1133')
                self.assertEqual("What is your favorite color?", contact2.msgs.all().first().text)

                # contact should have two URNs now
                fb_urn = contact2.urns.get(scheme=FACEBOOK_SCHEME)
                self.assertEqual(fb_urn.path, '1133')
                self.assertEqual(fb_urn.channel, self.channel)

                ext_urn = contact2.urns.get(scheme=EXTERNAL_SCHEME)
                self.assertEqual(ext_urn.path, 'user_ref2')
                self.assertIsNone(ext_urn.channel)

    def test_referrals_params(self):
        # create two triggers for referrals
        favorites = self.get_flow('favorites')
        pick = self.get_flow('pick_a_number')

        Trigger.objects.create(org=self.org, trigger_type=Trigger.TYPE_REFERRAL, referrer_id='join',
                               flow=favorites, created_by=self.admin, modified_by=self.admin)
        Trigger.objects.create(org=self.org, trigger_type=Trigger.TYPE_REFERRAL, referrer_id='signup',
                               flow=favorites, created_by=self.admin, modified_by=self.admin)
        Trigger.objects.create(org=self.org, trigger_type=Trigger.TYPE_REFERRAL, referrer_id='',
                               flow=pick, created_by=self.admin, modified_by=self.admin)

        callback_url = reverse('handlers.facebook_handler', args=[self.channel.uuid])

        referral = """
        {
          "sender": { "id": "1122" },
          "recipient": { "id": "PAGE_ID" },
          "timestamp": 1234567890,
          "referral": {
            "ref": "JOIN",
            "source": "SHORTLINK",
            "type": "OPEN_THREAD"
          }
        }
        """
        data = json.loads(FacebookTest.TEST_INCOMING)
        data['entry'][0]['messaging'][0] = json.loads(referral)
        response = self.client.post(callback_url, json.dumps(data), content_type='application/json')
        self.assertContains(response, 'Msg Ignored for recipient id: PAGE_ID')

        response = self.client.post(callback_url, json.dumps(data).replace('PAGE_ID', '1234'), content_type='application/json')
        self.assertEqual(200, response.status_code)

        # check that the user started the flow
        contact1 = Contact.objects.get(org=self.org, urns__path='1122')
        self.assertEqual("What is your favorite color?", contact1.msgs.order_by('id').last().text)

        # check if catchall trigger starts a different flow
        referral = """
        {
          "referral": {
            "ref": "not_handled",
            "source": "SHORTLINK",
            "type": "OPEN_THREAD"
          }
        }
        """
        del data['entry'][0]['messaging'][0]['referral']
        data['entry'][0]['messaging'][0].update(json.loads(referral))
        response = self.client.post(callback_url, json.dumps(data).replace('PAGE_ID', '1234'),
                                    content_type='application/json')
        self.assertContains(response, '{"status": ["Triggered flow for ref: not_handled"]}')

        # check that the user started the flow
        contact1 = Contact.objects.get(org=self.org, urns__path='1122')
        self.assertEqual("Pick a number between 1-10.", contact1.msgs.order_by('id').last().text)

        # check referral params in postback
        postback = """
        {
          "postback": {
            "payload": "postback",
            "referral": {
              "ref": "signup",
              "source": "SHORTLINK",
              "type": "OPEN_THREAD"
            }
          }
        }
        """
        del data['entry'][0]['messaging'][0]['referral']
        data['entry'][0]['messaging'][0].update(json.loads(postback))
        response = self.client.post(callback_url, json.dumps(data).replace('PAGE_ID', '1234'), content_type='application/json')
        self.assertContains(response, '{"status": ["Referral posted with referral id: signup"]}')

        # check that the user started the flow
        contact1 = Contact.objects.get(org=self.org, urns__path='1122')
        self.assertEqual("What is your favorite color?", contact1.msgs.all().first().text)

        # and that we created an event for it
        self.assertTrue(ChannelEvent.objects.filter(contact=contact1, event_type=ChannelEvent.TYPE_REFERRAL))

    def test_receive(self):
        data = json.loads(FacebookTest.TEST_INCOMING)
        callback_url = reverse('handlers.facebook_handler', args=[self.channel.uuid])

        with patch('requests.get') as mock_get:
            mock_get.return_value = MockResponse(200, '{"first_name": "Ben","last_name": "Haggerty"}')
            response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

            msg = Msg.objects.get()

            self.assertEqual(response.status_code, 200)

            # load our message
            self.assertEqual(msg.contact.get_urn(FACEBOOK_SCHEME).path, "5678")
            self.assertEqual(msg.direction, INCOMING)
            self.assertEqual(msg.org, self.org)
            self.assertEqual(msg.channel, self.channel)
            self.assertEqual(msg.text, "hello world")
            self.assertEqual(msg.external_id, "external_id")

            # make sure our contact's name was populated
            self.assertEqual(msg.contact.name, 'Ben Haggerty')

            Msg.objects.all().delete()
            Contact.all().delete()

        # simulate a failure to fetch contact data
        with patch('requests.get') as mock_get:
            mock_get.return_value = MockResponse(400, '{"error": "Unable to look up profile data"}')
            response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

            self.assertEqual(response.status_code, 200)

            msg = Msg.objects.get()

            self.assertEqual(msg.contact.get_urn(FACEBOOK_SCHEME).path, "5678")
            self.assertIsNone(msg.contact.name)

            Msg.objects.all().delete()
            Contact.all().delete()

        # simulate an exception
        with patch('requests.get') as mock_get:
            mock_get.return_value = MockResponse(200, 'Invalid JSON')
            response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

            self.assertEqual(response.status_code, 200)

            msg = Msg.objects.get()

            self.assertEqual(msg.contact.get_urn(FACEBOOK_SCHEME).path, "5678")
            self.assertIsNone(msg.contact.name)

            Msg.objects.all().delete()
            Contact.all().delete()

        # now with a anon org, shouldn't try to look things up
        self.org.is_anon = True
        self.org.save()

        with patch('requests.get') as mock_get:
            response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

            self.assertEqual(response.status_code, 200)

            msg = Msg.objects.get()

            self.assertEqual(msg.contact.get_urn(FACEBOOK_SCHEME).path, "5678")
            self.assertIsNone(msg.contact.name)
            self.assertEqual(mock_get.call_count, 0)

            Msg.objects.all().delete()
            self.org.is_anon = False
            self.org.save()

        # rich media
        data = """
        {
        "entry": [{
          "id": 208685479508187,
          "messaging": [{
            "message": {
              "attachments": [{
                "payload": { "url": "http://mediaurl.com/img.gif" }
              }],
              "mid": "external_id"
            },
            "recipient": {
              "id": 1234
            },
            "sender": {
              "id": 5678
            },
            "timestamp": 1459991487970
          }],
          "time": 1459991487970
        }]}
        """
        data = json.loads(data)
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        msg = Msg.objects.get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(msg.text, "http://mediaurl.com/img.gif")

        # quick reply
        data = """{
        "entry": [{
          "id": 208685479508187,
          "messaging": [{
            "timestamp": 1502820098870,
            "message": {
              "text": "Test",
              "mid": "external_id",
              "seq": 1234,
              "quick_reply": {
                "payload": "Test"
              }
            },
            "recipient": {
              "id": "1234"
            },
            "sender": {
              "id": "1234"
            }
          }],
          "time": 1459991487970
          }
        ]}
        """
        Msg.objects.all().delete()

        data = json.loads(data)
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        msg = Msg.objects.get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(msg.text, "Test")

        # link attachment
        data = """{
          "object":"page",
          "entry":[{
            "id":"32408604530",
            "time":1468418021822,
            "messaging":[{
              "sender":{"id":"5678"},
              "recipient":{"id":"1234"},
              "timestamp":1468417833159,
              "message": {
                "mid":"external_id",
                "seq":11242,
                "attachments":[{
                  "title":"Get in touch with us.",
                  "url": "http:\x5c/\x5c/m.me\x5c/",
                  "type": "fallback",
                  "payload": null
                }]
              }
            }]
          }]
        }
        """
        Msg.objects.all().delete()

        data = json.loads(data)
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        msg = Msg.objects.get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(msg.text, "Get in touch with us.\nhttp://m.me/")

        # link attachment without title
        data = """{
          "object":"page",
          "entry":[{
            "id":"32408604530",
            "time":1468418021822,
            "messaging":[{
              "sender":{"id":"5678"},
              "recipient":{"id":"1234"},
              "timestamp":1468417833159,
              "message": {
                "mid":"external_id",
                "seq":11242,
                "attachments":[{
                  "title": null,
                  "url": "http:\x5c/\x5c/m.me\x5c/",
                  "type": "fallback",
                  "payload": null
                }]
              }
            }]
          }]
        }
        """
        Msg.objects.all().delete()

        data = json.loads(data)
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        msg = Msg.objects.get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(msg.text, "http://m.me/")

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", urn="facebook:1234")
        msg = joe.send("Facebook Msg", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{"recipient_id":"1234", '
                                                  '"message_id":"mid.external"}')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, 'mid.external')
            self.clear_cache()

            self.assertEqual(mock.call_args[0][0], 'https://graph.facebook.com/v2.5/me/messages')
            self.assertEqual(json.loads(mock.call_args[0][1]),
                             dict(recipient=dict(id="1234"), message=dict(text="Facebook Msg")))

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(412, 'Error')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

        with patch('requests.post') as mock:
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", urn="facebook:1234")
        msg = joe.send("Facebook Msg", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{"recipient_id":"1234", '
                                                  '"message_id":"mid.external"}')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, 'mid.external')
            self.clear_cache()

            self.assertEqual(mock.call_count, 2)

            self.assertEqual(mock.call_args_list[0][0][0], 'https://graph.facebook.com/v2.5/me/messages')

            self.assertEqual(json.loads(mock.call_args_list[0][0][1]),
                             dict(recipient=dict(id="1234"),
                                  message=dict(text="Facebook Msg")))

            self.assertEqual(mock.call_args_list[1][0][0], 'https://graph.facebook.com/v2.5/me/messages')

            self.assertEqual(json.loads(mock.call_args_list[1][0][1]),
                             dict(recipient=dict(id="1234"),
                                  message=dict(attachment=dict(type="image",
                                                               payload=dict(
                                                                   url="https://example.com/attachments/pic.jpg")))))

        with patch('requests.get') as mock:
            mock.return_value = [MockResponse(200, '{"recipient_id":"1234", '
                                                   '"message_id":"mid.external"}'),
                                 MockResponse(412, 'Error')]

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

        with patch('requests.post') as mock:
            mock.side_effect = [MockResponse(200, '{"recipient_id":"1234", '
                                                  '"message_id":"mid.external"}'),
                                Exception('Kaboom!')]

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

    @override_settings(SEND_MESSAGES=True)
    def test_send_quick_replies(self):
        joe = self.create_contact("Joe", urn="facebook:1234")
        quick_replies = ['Yes', 'No']
        msg = joe.send("Facebook Msg", self.admin, trigger_send=False, quick_replies=quick_replies)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{"recipient_id": "1234", "message_id": "mid.external"}')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, 'mid.external')
            self.clear_cache()

            self.assertEqual(mock.call_args[0][0], 'https://graph.facebook.com/v2.5/me/messages')

            self.assertEqual(json.loads(mock.call_args[0][1])['recipient']['id'], '1234')
            self.assertEqual(json.loads(mock.call_args[0][1])['message']['text'], 'Facebook Msg')
            self.assertEqual(json.loads(mock.call_args[0][1])['message']['quick_replies'][0]['title'], 'Yes')
            self.assertEqual(json.loads(mock.call_args[0][1])['message']['quick_replies'][1]['title'], 'No')

        joe_test = self.create_contact("Joe", urn="facebook:12345", is_test=True)
        quick_replies = ['Yes', 'No']
        msg2 = joe_test.send("Facebook Msg", self.admin, trigger_send=False, quick_replies=quick_replies)[0]

        with self.settings(SEND_MESSAGES=True):

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, '{"recipient_id": "12345", "message_id": "mid.external"}')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg2.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.clear_cache()

                self.assertEqual(mock.call_args[0][0], 'https://graph.facebook.com/v2.5/me/messages')
                self.assertEqual(json.loads(mock.call_args[0][1])['recipient']['id'], '12345')
                self.assertEqual(json.loads(mock.call_args[0][1])['message']['text'], 'Facebook Msg')
                self.assertEqual(json.loads(mock.call_args[0][1])['message']['quick_replies'][0]['title'], 'Yes')
                self.assertEqual(json.loads(mock.call_args[0][1])['message']['quick_replies'][1]['title'], 'No')


class JiochatTest(TembaTest):

    def setUp(self):
        super(JiochatTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, None, 'JC', None, '1212',
                                      config={'jiochat_app_id': 'app-id',
                                              'jiochat_app_secret': 'app-secret',
                                              'secret': Channel.generate_secret(32)},
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_refresh_jiochat_access_tokens_task(self):
        with patch('requests.post') as mock:
            mock.return_value = MockResponse(400, '{ "error":"Failed" }')

            self.assertFalse(ChannelLog.objects.all())
            refresh_jiochat_access_tokens()

            self.assertEqual(ChannelLog.objects.all().count(), 1)
            self.assertTrue(ChannelLog.objects.filter(is_error=True).count(), 1)

            self.assertEqual(mock.call_count, 1)
            channel_client = JiochatClient.from_channel(self.channel)

            self.assertIsNone(channel_client.get_access_token())

            mock.reset_mock()
            mock.return_value = MockResponse(200, '{ "access_token":"ABC1234" }')

            refresh_jiochat_access_tokens()

            self.assertEqual(ChannelLog.objects.all().count(), 2)
            self.assertTrue(ChannelLog.objects.filter(is_error=True).count(), 1)
            self.assertTrue(ChannelLog.objects.filter(is_error=False).count(), 1)
            self.assertEqual(mock.call_count, 1)

            self.assertEqual(channel_client.get_access_token(), force_bytes('ABC1234'))
            self.assertEqual(mock.call_args_list[0][1]['data'], {'client_secret': u'app-secret',
                                                                 'grant_type': 'client_credentials',
                                                                 'client_id': u'app-id'})

            self.login(self.admin)
            response = self.client.get(reverse("channels.channellog_list") + '?channel=%d&others=1' % self.channel.id,
                                       follow=True)
            self.assertEqual(len(response.context['object_list']), 2)

    @patch('temba.utils.jiochat.JiochatClient.refresh_access_token')
    def test_url_verification(self, mock_refresh_access_token):
        mock_refresh_access_token.return_value = MockResponse(200, '{ "access_token":"ABC1234" }')

        # invalid UUID
        response = self.client.get(reverse('handlers.jiochat_handler', args=['00000000-0000-0000-0000-000000000000']))
        self.assertEqual(response.status_code, 400)

        timestamp = str(time.time())
        nonce = 'nonce'

        value = "".join(sorted([self.channel.config[Channel.CONFIG_SECRET], timestamp, nonce]))

        hash_object = hashlib.sha1(value.encode('utf-8'))
        signature = hash_object.hexdigest()

        callback_url = reverse('handlers.jiochat_handler', args=[self.channel.uuid])

        response = self.client.get(callback_url +
                                   "?signature=%s&timestamp=%s&nonce=%s&echostr=SUCCESS" % (signature, timestamp,
                                                                                            nonce))
        self.assertContains(response, 'SUCCESS')
        self.assertTrue(mock_refresh_access_token.called)

        mock_refresh_access_token.reset_mock()

        # fail verification
        response = self.client.get(callback_url +
                                   "?signature=%s&timestamp=%s&nonce=%s&echostr=SUCCESS" % (signature, timestamp,
                                                                                            'other'))

        self.assertEqual(response.status_code, 400)
        self.assertFalse(mock_refresh_access_token.called)  # we did not fire task to refresh access token

    @override_settings(SEND_MESSAGES=True)
    @patch('temba.utils.jiochat.JiochatClient.get_access_token')
    def test_send(self, mock_access_token):
        mock_access_token.return_value = 'ABC1234'

        joe = self.create_contact("Joe", urn="jiochat:1234")
        msg = joe.send("Test Msg", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{"errcode":0,"errmsg":"Request succeeded"}')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.clear_cache()

            self.assertEqual(mock.call_args[0][0], 'https://channels.jiochat.com/custom/custom_send.action')
            self.assertEqual(mock.call_args[1]['json'],
                             dict(touser="1234", text=dict(content="Test Msg"), msgtype='text'))

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(412, 'Error')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(msg.status, ERRORED)

        with patch('requests.post') as mock:
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(msg.status, ERRORED)

    @patch('temba.utils.jiochat.JiochatClient.get_user_detail')
    def test_follow(self, mock_get_user_detail):
        mock_get_user_detail.return_value = {'nickname': "Bob"}

        callback_url = reverse('handlers.jiochat_handler', args=[self.channel.uuid])
        an_hour_ago = timezone.now() - timedelta(hours=1)

        flow = self.create_flow()

        data = {
            'ToUsername': '12121212121212',
            'FromUserName': '1234',
            'CreateTime': time.mktime(an_hour_ago.timetuple()),
            'MsgType': 'event',
            'Event': 'subscribe',
        }

        Contact.objects.all().delete()

        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 200)

        contact = Contact.objects.get()
        self.assertEqual(contact.get_urn(JIOCHAT_SCHEME).path, "1234")

        self.assertEqual(0, flow.runs.all().count())

        Trigger.objects.create(created_by=self.user, modified_by=self.user, org=self.org,
                               trigger_type=Trigger.TYPE_FOLLOW, flow=flow, channel=self.channel)

        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, flow.runs.all().count())

        # we ignore unsubscribe event
        data['Event'] = 'unsubscribe'
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 400)

    @patch('requests.get')
    @patch('temba.utils.jiochat.JiochatClient.get_access_token')
    def test_receive(self, mock_access_token, mock_get):
        mock_access_token.return_value = 'ABC1234'
        mock_get.return_value = MockResponse(400, '{"error":"Not found"}')

        # invalid UUID
        response = self.client.post(reverse('handlers.jiochat_handler', args=['00000000-0000-0000-0000-000000000000']))
        self.assertEqual(response.status_code, 400)

        callback_url = reverse('handlers.jiochat_handler', args=[self.channel.uuid])

        # POST invalid JSON data
        response = self.client.post(callback_url, "not json", content_type="application/json")
        self.assertEqual(response.status_code, 400)

        # POST missing data
        response = self.client.post(callback_url, json.dumps({}), content_type="application/json")
        self.assertEqual(response.status_code, 400)

        an_hour_ago = timezone.now() - timedelta(hours=1)

        data = {
            'ToUsername': '12121212121212',
            'FromUserName': '1234',
            'CreateTime': time.mktime(an_hour_ago.timetuple()),
            'MsgType': 'blabla',
            'MsgId': '123456',
            "Content": "Test",
        }

        self.assertEqual(ChannelLog.objects.all().count(), 0)

        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 400)

        self.assertEqual(ChannelLog.objects.all().count(), 1)
        self.assertEqual(ChannelLog.objects.filter(is_error=True).count(), 1)

        data = {
            'ToUsername': '12121212121212',
            'FromUserName': '1234',
            'CreateTime': time.mktime(an_hour_ago.timetuple()),
            'MsgType': 'text',
            'MsgId': '123456',
            "Content": "Test",
        }

        Contact.objects.all().delete()
        ChannelLog.objects.all().delete()

        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 200)

        self.assertEqual(ChannelLog.objects.all().count(), 1)
        self.assertEqual(ChannelLog.objects.filter(is_error=True).count(), 1)

        msg = Msg.objects.get()
        self.assertContains(response, "Msgs Accepted: %d" % msg.id)

        # load our message
        self.assertIsNone(msg.contact.name)
        self.assertEqual(msg.contact.get_urn(JIOCHAT_SCHEME).path, "1234")
        self.assertEqual(msg.direction, INCOMING)
        self.assertEqual(msg.org, self.org)
        self.assertEqual(msg.channel, self.channel)
        self.assertEqual(msg.text, "Test")
        self.assertEqual(msg.sent_on.date(), an_hour_ago.date())

        Msg.objects.all().delete()
        Contact.objects.all().delete()
        ChannelLog.objects.all().delete()

        mock_get.return_value = MockResponse(200, '{"nickname":"Shinonda"}')
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        self.assertEqual(ChannelLog.objects.all().count(), 1)
        self.assertEqual(ChannelLog.objects.filter(is_error=False).count(), 1)

        msg = Msg.objects.get()
        self.assertContains(response, "Msgs Accepted: %d" % msg.id)

        # load our message
        self.assertEqual(msg.contact.name, "Shinonda")
        self.assertEqual(msg.contact.get_urn(JIOCHAT_SCHEME).path, "1234")
        self.assertEqual(msg.direction, INCOMING)
        self.assertEqual(msg.org, self.org)
        self.assertEqual(msg.channel, self.channel)
        self.assertEqual(msg.text, "Test")
        self.assertEqual(msg.sent_on.date(), an_hour_ago.date())

        mock_get.return_value = MockResponse(200, '{"nickname":"Kendrick"}')
        other_data = {
            'ToUsername': '12121212121212',
            'FromUserName': '1234',
            'CreateTime': time.mktime(an_hour_ago.timetuple()),
            'MsgType': 'text',
            'MsgId': '1234567',
            "Content": "Hello",
        }

        response = self.client.post(callback_url, json.dumps(other_data), content_type="application/json")
        self.assertEqual(response.status_code, 200)

        self.assertEqual(ChannelLog.objects.all().count(), 1)
        self.assertEqual(ChannelLog.objects.filter(is_error=False).count(), 1)

        msg = Msg.objects.all().last()
        self.assertContains(response, "Msgs Accepted: %d" % msg.id)

        # load our message
        self.assertEqual(msg.contact.name, "Shinonda")  # the name should not change to Kendrick
        self.assertEqual(msg.contact.get_urn(JIOCHAT_SCHEME).path, "1234")
        self.assertEqual(msg.direction, INCOMING)
        self.assertEqual(msg.org, self.org)
        self.assertEqual(msg.channel, self.channel)
        self.assertEqual(msg.text, "Hello")
        self.assertEqual(msg.sent_on.date(), an_hour_ago.date())

        Msg.objects.all().delete()
        Contact.objects.all().delete()
        ChannelLog.objects.all().delete()

        with AnonymousOrg(self.org):
            response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

            self.assertEqual(ChannelLog.objects.all().count(), 0)

            msg = Msg.objects.get()
            self.assertContains(response, "Msgs Accepted: %d" % msg.id)

            # load our message
            self.assertIsNone(msg.contact.name)
            self.assertEqual(msg.contact.get_urn(JIOCHAT_SCHEME).path, "1234")
            self.assertEqual(msg.direction, INCOMING)
            self.assertEqual(msg.org, self.org)
            self.assertEqual(msg.channel, self.channel)
            self.assertEqual(msg.text, "Test")
            self.assertEqual(msg.sent_on.date(), an_hour_ago.date())

        Msg.objects.all().delete()
        Contact.objects.all().delete()
        ChannelLog.objects.all().delete()

        data = {
            'ToUsername': '12121212121212',
            'FromUserName': '1234',
            'CreateTime': time.mktime(an_hour_ago.timetuple()),
            'MsgType': 'image',
            'MsgId': '123456',
            "MediaId": "12",
        }

        with patch('requests.get') as mock_get:
            mock_get.side_effect = [MockResponse(200, '{"nickname": "Shinonda. ☺"}'),
                                    MockResponse(400, 'Error'),
                                    MockResponse(200, "IMG_BITS",
                                                 headers={"Content-Type": "image/jpeg",
                                                          "Content-Disposition":
                                                          'attachment; filename="image_name.jpg"'})]

            with patch('temba.orgs.models.Org.save_media') as mock_save_media:
                mock_save_media.return_value = '<MEDIA_SAVED_URL>'

                response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
                self.assertEqual(response.status_code, 200)

                self.assertEqual(ChannelLog.objects.all().count(), 2)
                self.assertEqual(ChannelLog.objects.filter(is_error=False).count(), 2)
                self.assertEqual(ChannelLog.objects.all().first().response, '{"nickname": "Shinonda. \\u263a"}')

                msg = Msg.objects.get()
                self.assertContains(response, "Msgs Accepted: %d" % msg.id)

                # load our message
                self.assertEqual(msg.contact.get_urn(JIOCHAT_SCHEME).path, "1234")
                self.assertEqual(msg.direction, INCOMING)
                self.assertEqual(msg.org, self.org)
                self.assertEqual(msg.channel, self.channel)
                self.assertEqual(msg.text, "")
                self.assertEqual(msg.sent_on.date(), an_hour_ago.date())
                self.assertEqual(msg.attachments[0], 'image/jpeg:<MEDIA_SAVED_URL>')

                self.assertEqual(mock_get.call_count, 3)

        Msg.objects.all().delete()
        Contact.objects.all().delete()
        ChannelLog.objects.all().delete()

        with patch('requests.get') as mock_get:
            mock_get.return_value = MockResponse(400, 'Error')

            with patch('temba.orgs.models.Org.save_media') as mock_save_media:
                mock_save_media.return_value = '<MEDIA_SAVED_URL>'

                response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
                self.assertEqual(response.status_code, 200)

                self.assertEqual(ChannelLog.objects.all().count(), 2)
                self.assertEqual(ChannelLog.objects.filter(is_error=True).count(), 2)


class GlobeTest(TembaTest):

    def setUp(self):
        super(GlobeTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'PH', 'GL', None, '21586380',
                                      config=dict(app_id='AppId', app_secret='AppSecret', passphrase='Passphrase'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_receive(self):
        # invalid UUID
        response = self.client.post(reverse('handlers.globe_handler', args=['receive', '00000000-0000-0000-0000-000000000000']))
        self.assertEqual(response.status_code, 400)

        data = {
            "inboundSMSMessageList": {
                "inboundSMSMessage": [{
                    "dateTime": "Fri Nov 22 2013 12:12:13 GMT+0000 (UTC)",
                    "destinationAddress": "tel:21586380",
                    "messageId": None,
                    "message": "Hello",
                    "resourceURL": None,
                    "senderAddress": "tel:9171234567"
                }]
            }
        }
        callback_url = reverse('handlers.globe_handler', args=['receive', self.channel.uuid])

        # try a GET
        response = self.client.get(callback_url)
        self.assertEqual(response.status_code, 405)

        # POST invalid JSON data
        response = self.client.post(callback_url, "not json", content_type="application/json")
        self.assertEqual(response.status_code, 400)

        # POST missing data
        response = self.client.post(callback_url, json.dumps({}), content_type="application/json")
        self.assertEqual(response.status_code, 400)

        # POST missing fields in msg
        bad_data = copy.deepcopy(data)
        del bad_data['inboundSMSMessageList']['inboundSMSMessage'][0]['message']
        response = self.client.post(callback_url, json.dumps(bad_data), content_type="application/json")
        self.assertEqual(response.status_code, 400)

        # POST, invalid sender address
        bad_data = copy.deepcopy(data)
        bad_data['inboundSMSMessageList']['inboundSMSMessage'][0]['senderAddress'] = '9999'
        response = self.client.post(callback_url, json.dumps(bad_data), content_type="application/json")
        self.assertEqual(response.status_code, 400)

        # POST, invalid destination address
        bad_data = copy.deepcopy(data)
        bad_data['inboundSMSMessageList']['inboundSMSMessage'][0]['destinationAddress'] = '9999'
        response = self.client.post(callback_url, json.dumps(bad_data), content_type="application/json")
        self.assertEqual(response.status_code, 400)

        # POST, different destination address accepted (globe does mapping on their side)
        bad_data = copy.deepcopy(data)
        bad_data['inboundSMSMessageList']['inboundSMSMessage'][0]['destinationAddress'] = 'tel:9999'
        response = self.client.post(callback_url, json.dumps(bad_data), content_type="application/json")
        self.assertEqual(response.status_code, 200)

        msg = Msg.objects.get()
        self.assertEqual(msg.channel, self.channel)
        self.assertContains(response, "Msgs Accepted: %d" % msg.id)
        Msg.objects.all().delete()

        # another valid post on the right address
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        msg = Msg.objects.get()
        self.assertContains(response, "Msgs Accepted: %d" % msg.id)

        # load our message
        self.assertEqual(msg.contact.get_urn(TEL_SCHEME).path, "+639171234567")
        self.assertEqual(msg.direction, INCOMING)
        self.assertEqual(msg.org, self.org)
        self.assertEqual(msg.channel, self.channel)
        self.assertEqual(msg.text, "Hello")
        self.assertEqual(msg.sent_on.date(), date(day=22, month=11, year=2013))

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", "+639171234567")
        msg = joe.send("MT", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "status":"accepted" }')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            mock.assert_called_once_with('https://devapi.globelabs.com.ph/smsmessaging/v1/outbound/21586380/requests',
                                         headers={'User-agent': 'RapidPro'},
                                         data={'message': 'MT', 'app_secret': 'AppSecret', 'app_id': 'AppId',
                                               'passphrase': 'Passphrase', 'address': '639171234567'},
                                         timeout=5)

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.clear_cache()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(401, 'Error')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.clear_cache()

        with patch('requests.get') as mock:
            mock.side_effect = Exception("Unable to reach host")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.clear_cache()

        with patch('requests.post') as mock:
            mock.side_effect = Exception('Kaboom!')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEqual(FAILED, msg.status)
            self.clear_cache()

            self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                              "referenced before assignment"))

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", "+639171234567")
        msg = joe.send("MT", self.admin, trigger_send=False, attachments=["image/jpeg:https://example.com/attachments/pic.jpg"])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "status":"accepted" }')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            mock.assert_called_once_with('https://devapi.globelabs.com.ph/smsmessaging/v1/outbound/21586380/requests',
                                         headers={'User-agent': 'RapidPro'},
                                         data={'message': 'MT\nhttps://example.com/attachments/pic.jpg',
                                               'app_secret': 'AppSecret', 'app_id': 'AppId',
                                               'passphrase': 'Passphrase', 'address': '639171234567'},
                                         timeout=5)

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.clear_cache()


class LineTest(TembaTest):

    def setUp(self):
        super(LineTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, None, 'LN', '123456789', '123456789',
                                      config=dict(channel_id='1234', secret='1234', channel_mid='1234', auth_token='abcdefgij'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_receive(self):

        data = {
            "events": [{
                "replyToken": "abcdefghij",
                "type": "message",
                "timestamp": 1451617200000,
                "source": {
                    "type": "user",
                    "userId": "uabcdefghij"
                },
                "message": {
                    "id": "100001",
                    "type": "text",
                    "text": "Hello, world"
                }
            }, {
                "replyToken": "abcdefghijklm",
                "type": "message",
                "timestamp": 1451617210000,
                "source": {
                    "type": "user",
                    "userId": "uabcdefghij"
                },
                "message": {
                    "id": "100002",
                    "type": "sticker",
                    "packageId": "1",
                    "stickerId": "1"
                }
            }]
        }

        callback_url = reverse('handlers.line_handler', args=[self.channel.uuid])
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual("uabcdefghij", msg.contact.get_urn(LINE_SCHEME).path)
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello, world", msg.text)

        response = self.client.get(callback_url)
        self.assertEqual(400, response.status_code)

        data = {
            "events": [{
                "replyToken": "abcdefghij",
                "type": "message",
                "timestamp": 1451617200000,
                "source": {
                    "type": "user",
                    "userId": "uabcdefghij"
                }
            }]
        }

        callback_url = reverse('handlers.line_handler', args=[self.channel.uuid])
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        self.assertEqual(400, response.status_code)

    def test_send(self):
        joe = self.create_contact("Joe", urn="line:uabcdefghijkl")
        msg = joe.send("Hello, world!", self.admin, trigger_send=False)[0]

        with self.settings(SEND_MESSAGES=True):

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, '{}')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEqual(msg.status, WIRED)
                self.assertTrue(msg.sent_on)
                self.clear_cache()

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEqual(ERRORED, msg.status)
                self.assertEqual(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

            with patch('requests.post') as mock:
                mock.side_effect = Exception('Kaboom!')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEqual(ERRORED, msg.status)
                self.assertEqual(2, msg.error_count)
                self.assertTrue(msg.next_attempt)

                self.assertFalse(ChannelLog.objects.filter(description__icontains="local variable 'response' "
                                                                                  "referenced before assignment"))

    def test_send_media(self):
        joe = self.create_contact("Joe", urn="line:uabcdefghijkl")
        msg = joe.send("Hello, world!", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with self.settings(SEND_MESSAGES=True):

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, '{}')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEqual(msg.status, WIRED)
                self.assertTrue(msg.sent_on)
                self.clear_cache()

                self.assertEqual(json.loads(mock.call_args[1]['data'])['messages'][0]['text'],
                                 "Hello, world!\nhttps://example.com/attachments/pic.jpg")


class ViberPublicTest(TembaTest):

    def setUp(self):
        super(ViberPublicTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, None, 'VP', None, '1001',
                                      uuid='00000000-0000-0000-0000-000000001234',
                                      config={Channel.CONFIG_AUTH_TOKEN: "auth_token"})

        self.callback_url = reverse('handlers.viber_public_handler', args=[self.channel.uuid])

    def test_receive_on_anon(self):
        with AnonymousOrg(self.org):
            data = {
                "event": "message",
                "timestamp": 1481142112807,
                "message_token": 4987381189870374000,
                "sender": {
                    "id": "xy5/5y6O81+/kbWHpLhBoA==",
                    "name": "ET3",
                },
                "message": {
                    "text": "incoming msg",
                    "type": "text",
                    "tracking_data": "3055"
                }
            }

            response = self.client.post(self.callback_url, json.dumps(data), content_type="application/json",
                                        HTTP_X_VIBER_CONTENT_SIGNATURE='ab4ea2337c1bb9a49eff53dd182f858817707df97cbc82368769e00c56d38419')
            self.assertEqual(response.status_code, 200)

            msg = Msg.objects.get()
            self.assertEqual(response.content, "Msg Accepted: %d" % msg.id)

            self.assertEqual(msg.contact.get_urn(VIBER_SCHEME).path, "xy5/5y6O81+/kbWHpLhBoA==")
            self.assertEqual(msg.contact.name, None)
            self.assertEqual(msg.direction, INCOMING)
            self.assertEqual(msg.org, self.org)
            self.assertEqual(msg.channel, self.channel)
            self.assertEqual(msg.text, "incoming msg")
            self.assertEqual(msg.sent_on.date(), date(day=7, month=12, year=2016))
            self.assertEqual(msg.external_id, "4987381189870374000")

    def test_receive(self):
        # invalid UUID
        response = self.client.post(reverse('handlers.viber_public_handler', args=['00000000-0000-0000-0000-000000000000']))
        self.assertEqual(response.status_code, 200)

        data = {
            "event": "message",
            "timestamp": 1481142112807,
            "message_token": 4987381189870374000,
            "sender": {
                "id": "xy5/5y6O81+/kbWHpLhBoA==",
                "name": "ET3",
            },
            "message": {
                "text": "incoming msg",
                "type": "text",
                "tracking_data": "3055"
            }
        }

        # try a GET
        response = self.client.get(self.callback_url)
        self.assertEqual(response.status_code, 405)

        # POST invalid JSON data
        response = self.client.post(self.callback_url, "not json", content_type="application/json")
        self.assertEqual(response.status_code, 400)

        # Invalid signature
        response = self.client.post(self.callback_url, json.dumps({}), content_type="application/json",
                                    HTTP_X_VIBER_CONTENT_SIGNATURE='bad_sig')
        self.assertEqual(response.status_code, 400)

        # POST missing data
        response = self.client.post(self.callback_url, json.dumps({}), content_type="application/json",
                                    HTTP_X_VIBER_CONTENT_SIGNATURE='a182e13e58cbe9bb893cc03c055a1218fba31e8efa6f3ab74a54d4f8542ae376')
        self.assertEqual(response.status_code, 400)

        # ok, valid post
        response = self.client.post(self.callback_url, json.dumps(data), content_type="application/json",
                                    HTTP_X_VIBER_CONTENT_SIGNATURE='ab4ea2337c1bb9a49eff53dd182f858817707df97cbc82368769e00c56d38419')
        self.assertEqual(response.status_code, 200)

        msg = Msg.objects.get()
        self.assertEqual(response.content, "Msg Accepted: %d" % msg.id)

        self.assertEqual(msg.contact.get_urn(VIBER_SCHEME).path, "xy5/5y6O81+/kbWHpLhBoA==")
        self.assertEqual(msg.contact.name, "ET3")
        self.assertEqual(msg.direction, INCOMING)
        self.assertEqual(msg.org, self.org)
        self.assertEqual(msg.channel, self.channel)
        self.assertEqual(msg.text, "incoming msg")
        self.assertEqual(msg.sent_on.date(), date(day=7, month=12, year=2016))
        self.assertEqual(msg.external_id, "4987381189870374000")

        # set our contact name to something else
        contact = msg.contact
        contact.name = "ET4"
        contact.save(update_fields=['name'])
        response = self.client.post(self.callback_url, json.dumps(data), content_type="application/json",
                                    HTTP_X_VIBER_CONTENT_SIGNATURE='ab4ea2337c1bb9a49eff53dd182f858817707df97cbc82368769e00c56d38419')
        self.assertEqual(response.status_code, 200)

        # refresh our contact, name shouldn't have changed
        contact.refresh_from_db()
        self.assertEqual("ET4", contact.name)

    def assertSignedRequest(self, payload, expected_status=200):
        from temba.channels.handlers import ViberPublicHandler

        signature = ViberPublicHandler.calculate_sig(payload, "auth_token")
        response = self.client.post(self.callback_url, payload, content_type="application/json",
                                    HTTP_X_VIBER_CONTENT_SIGNATURE=signature)

        self.assertEqual(response.status_code, expected_status, response.content)
        return response

    def assertMessageReceived(self, msg_type, payload_name, payload_value, assert_text, assert_media=None):
        data = {
            "event": "message",
            "timestamp": 1481142112807,
            "message_token": 4987381189870374000,
            "sender": {
                "id": "xy5/5y6O81+/kbWHpLhBoA==",
                "name": "ET3",
            },
            "message": {
                "text": "incoming msg",
                "type": "undefined",
                "tracking_data": "3055",
            }
        }

        data['message']['type'] = msg_type
        data['message'][payload_name] = payload_value

        self.assertSignedRequest(force_bytes(json.dumps(data)))

        msg = Msg.objects.get()
        self.assertEqual(msg.text, assert_text)

        if assert_media:
            self.assertIn(assert_media, msg.attachments)

    def test_reject_message_missing_text(self):
        for viber_msg_type in ['text', 'picture', 'video']:
            data = {
                "event": "message",
                "timestamp": 1481142112807,
                "message_token": 4987381189870374000,
                "sender": {
                    "id": "xy5/5y6O81+/kbWHpLhBoA==",
                    "name": "ET3",
                },
                "message": {
                    "type": viber_msg_type,
                    "tracking_data": "3055",
                }
            }

            response = self.assertSignedRequest(force_bytes(json.dumps(data)), 400)
            self.assertContains(response, "Missing text or media in message in request body.", status_code=400)
            Msg.objects.all().delete()

    def test_receive_picture_missing_media_key(self):
        self.assertMessageReceived('picture', None, None, 'incoming msg', None)

    def test_receive_video_missing_media_key(self):
        self.assertMessageReceived('video', None, None, 'incoming msg', None)

    def test_receive_contact(self):
        self.assertMessageReceived('contact', 'contact', dict(name="Alex", phone_number="+12067799191"), 'Alex: +12067799191')

    def test_receive_url(self):
        self.assertMessageReceived('url', 'media', 'http://foo.com/', 'http://foo.com/')

    def test_receive_gps(self):
        self.assertMessageReceived('location', 'location', dict(lat='1.2', lon='-1.3'), 'incoming msg')

    def test_webhook_check(self):
        data = {
            "event": "webhook",
            "timestamp": 4987034606158369000,
            "message_token": 1481059480858
        }
        self.assertSignedRequest(force_bytes(json.dumps(data)))

    def test_subscribed(self):
        data = {
            "event": "subscribed",
            "timestamp": 1457764197627,
            "user": {
                "id": "01234567890A=",
                "name": "yarden",
                "avatar": "http://avatar_url",
                "country": "IL",
                "language": "en",
                "api_version": 1
            },
            "message_token": 4912661846655238145
        }
        self.assertSignedRequest(force_bytes(json.dumps(data)))

        # check that the contact was created
        contact = Contact.objects.get(org=self.org, urns__path='01234567890A=', urns__scheme=VIBER_SCHEME)
        self.assertEqual(contact.name, "yarden")

        data = {
            "event": "unsubscribed",
            "timestamp": 1457764197627,
            "user_id": "01234567890A=",
            "message_token": 4912661846655238145
        }
        self.assertSignedRequest(force_bytes(json.dumps(data)))
        contact.refresh_from_db()
        self.assertTrue(contact.is_stopped)

        # use a user id we haven't seen before
        data['user_id'] = "01234567890B="
        self.assertSignedRequest(force_bytes(json.dumps(data)))

        # should not create contacts we don't already know about
        self.assertIsNone(Contact.from_urn(self.org, URN.from_viber("01234567890B=")))

    def test_subscribed_on_anon(self):
        with AnonymousOrg(self.org):
            data = {
                "event": "subscribed",
                "timestamp": 1457764197627,
                "user": {
                    "id": "01234567890A=",
                    "name": "yarden",
                    "avatar": "http://avatar_url",
                    "country": "IL",
                    "language": "en",
                    "api_version": 1
                },
                "message_token": 4912661846655238145
            }
            self.assertSignedRequest(force_bytes(json.dumps(data)))

            # check that the contact was created
            contact = Contact.objects.get(org=self.org, urns__path='01234567890A=', urns__scheme=VIBER_SCHEME)
            self.assertEqual(contact.name, None)

            # and a new channel event for the conversation
            self.assertTrue(ChannelEvent.objects.filter(channel=self.channel, contact=contact,
                                                        event_type=ChannelEvent.TYPE_NEW_CONVERSATION))

    def test_conversation_started(self):
        # this is a no-op
        data = {
            "event": "conversation_started",
            "timestamp": 1457764197627,
            "message_token": 4912661846655238145,
            "type": "open",
            "context": "context information",
            "user": {
                "id": "01234567890A=",
                "name": "yarden",
                "avatar": "http://avatar_url",
                "country": "IL",
                "language": "en",
                "api_version": 1
            }
        }
        self.assertSignedRequest(force_bytes(json.dumps(data)))

    @override_settings(SEND_MESSAGES=True)
    def test_send(self):
        joe = self.create_contact("Joe", urn="viber:xy5/5y6O81+/kbWHpLhBoA==")
        msg = joe.send("MT", self.admin, trigger_send=False)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{"status":0,"status_message":"ok","message_token":4987381194038857789}')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            mock.assert_called_with('https://chatapi.viber.com/pa/send_message',
                                    headers={'Accept': u'application/json', u'User-agent': u'RapidPro'},
                                    json={'text': u'MT',
                                          'auth_token': u'auth_token',
                                          'tracking_data': msg.id,
                                          'type': u'text',
                                          'receiver': u'xy5/5y6O81+/kbWHpLhBoA=='},
                                    timeout=5)

            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, "4987381194038857789")
            self.clear_cache()

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{"status":3, "status_message":"invalidAuthToken"}')
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
            msg.refresh_from_db()
            self.assertEqual(msg.status, FAILED)
            self.clear_cache()

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(401, '{"status":"5"}')
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.clear_cache()

        with patch('requests.post') as mock:
            mock.side_effect = Exception("Unable to reach host")
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
            msg.refresh_from_db()
            self.assertEqual(ERRORED, msg.status)
            self.clear_cache()

    @override_settings(SEND_MESSAGES=True)
    def test_send_media(self):
        joe = self.create_contact("Joe", urn="viber:xy5/5y6O81+/kbWHpLhBoA==")
        msg = joe.send("MT", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{"status":0,"status_message":"ok","message_token":4987381194038857789}')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            mock.assert_called_with('https://chatapi.viber.com/pa/send_message',
                                    headers={'Accept': u'application/json', u'User-agent': u'RapidPro'},
                                    json={'text': u'MT\nhttps://example.com/attachments/pic.jpg',
                                          'auth_token': u'auth_token',
                                          'tracking_data': msg.id,
                                          'type': u'text',
                                          'receiver': u'xy5/5y6O81+/kbWHpLhBoA=='},
                                    timeout=5)

            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, "4987381194038857789")
            self.clear_cache()

    @override_settings(SEND_MESSAGES=True)
    def test_send_quick_replies(self):
        quick_replies = ['Yes', 'No']
        joe = self.create_contact("Joe", urn="viber:FXLP/JstS7kDuoiUGihkgA==")
        msg = joe.send("Hello, world!", self.admin, trigger_send=False, quick_replies=quick_replies)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "status":0, "status_message": "ok", "message_token": "999" }')

            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            mock.assert_called_with('https://chatapi.viber.com/pa/send_message',
                                    headers={'Accept': u'application/json', u'User-agent': u'RapidPro'},
                                    json=dict(
                                        auth_token='auth_token',
                                        receiver="FXLP/JstS7kDuoiUGihkgA==",
                                        text="Hello, world!",
                                        type='text',
                                        tracking_data=msg.id,
                                        keyboard=dict(
                                            Type="keyboard",
                                            DefaultHeight=True,
                                            Buttons=[
                                                {
                                                    "Text": "Yes",
                                                    "ActionBody": "Yes",
                                                    "ActionType": "reply",
                                                    "TextSize": "regular"
                                                },
                                                {
                                                    "Text": "No",
                                                    "ActionBody": "No",
                                                    "ActionType": "reply",
                                                    "TextSize": "regular"
                                                }
                                            ]
                                        )
                                    ),
                                    timeout=5)

            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, "999")
            self.assertEqual(msg.metadata, dict(quick_replies=quick_replies))
            self.clear_cache()
            self.assertEqual(mock.call_args[1]['json']['keyboard']['Buttons'][0]['Text'], 'Yes')
            self.assertEqual(mock.call_args[1]['json']['keyboard']['Buttons'][1]['Text'], 'No')


class FcmTest(TembaTest):

    def setUp(self):
        super(FcmTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, None, 'FCM', 'FCM Channel', 'fcm-channel',
                                      config=dict(FCM_KEY='123456789', FCM_TITLE='FCM Channel',
                                                  FCM_NOTIFICATION=True),
                                      uuid='00000000-0000-0000-0000-000000001234')
        self.receive_url = reverse('handlers.fcm_handler', args=['receive', self.channel.uuid])
        self.register_url = reverse('handlers.fcm_handler', args=['register', self.channel.uuid])

    def test_receive(self):
        # invalid UUID
        response = self.client.post(reverse('handlers.fcm_handler', args=['receive',
                                                                          '00000000-0000-0000-0000-000000000000']))
        self.assertEqual(response.status_code, 404)

        # try GET
        response = self.client.get(self.receive_url)
        self.assertEqual(response.status_code, 405)

        data = {'from': '12345abcde', 'msg': 'Hello World!', 'date': '2017-01-01T08:50:00.000'}
        response = self.client.post(self.receive_url, data)
        self.assertEqual(400, response.status_code)

        data = {'from': '12345abcde', 'msg': 'Hello World!', 'date': '2017-01-01T08:50:00.000',
                'fcm_token': '1234567890qwertyuiop'}
        response = self.client.post(self.receive_url, data)
        self.assertEqual(200, response.status_code)

        data = {'from': '12345abcde', 'msg': 'Hello World!', 'date': '2017-01-01T08:50:00.000',
                'fcm_token': '12345678901qwertyuiopq'}
        response = self.client.post(self.receive_url, data)
        self.assertEqual(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEqual("12345678901qwertyuiopq", msg.contact.get_urn(FCM_SCHEME).auth)
        self.assertEqual("fcm:12345abcde", six.text_type(msg.contact.get_urn(FCM_SCHEME)))
        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.channel, msg.channel)
        self.assertEqual("Hello World!", msg.text)

    def test_register(self):
        data = {'urn': '12345abcde'}
        response = self.client.post(self.register_url, data)
        self.assertEqual(400, response.status_code)

        data = {'urn': '12345abcde', 'fcm_token': '1234567890qwertyuiop'}
        response = self.client.post(self.register_url, data)
        self.assertEqual(200, response.status_code)
        contact = response.json()

        data = {'urn': '12345abcde', 'fcm_token': 'qwertyuiop1234567890'}
        response = self.client.post(self.register_url, data)
        self.assertEqual(200, response.status_code)
        updated_contact = response.json()

        self.assertEqual(contact.get('contact_uuid'), updated_contact.get('contact_uuid'))

        data = {'urn': '12345abcde', 'fcm_token': '1234567890qwertyuiop', 'contact_uuid': contact.get('contact_uuid')}
        response = self.client.post(self.register_url, data)
        self.assertEqual(200, response.status_code)
        updated_contact = response.json()

        self.assertEqual(contact.get('contact_uuid'), updated_contact.get('contact_uuid'))

    def test_send(self):
        joe = self.create_contact("Joe", urn="fcm:12345abcde", auth="123456abcdef")
        msg = joe.send("Hello, world!", self.admin, trigger_send=False)[0]

        with self.settings(SEND_MESSAGES=True):

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, '{ "success": 1, "multicast_id": 123456, "failures": 0 }')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEqual(msg.status, WIRED)
                self.assertTrue(msg.sent_on)

                data = json.dumps({
                    'data': {
                        'type': 'rapidpro',
                        'title': 'FCM Channel',
                        'message': 'Hello, world!',
                        'message_id': msg.id
                    },
                    'content_available': True,
                    'to': '123456abcdef',
                    'priority': 'high',
                    'notification': {
                        'title': 'FCM Channel',
                        'body': 'Hello, world!'
                    }
                })

                mock.assert_called_once_with('https://fcm.googleapis.com/fcm/send',
                                             data=data,
                                             headers={
                                                 'Content-Type': 'application/json',
                                                 'Authorization': 'key=123456789',
                                                 'User-agent': 'RapidPro'
                                             },
                                             timeout=5)

                self.clear_cache()

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEqual(ERRORED, msg.status)
                self.assertEqual(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

    def test_send_media(self):
        joe = self.create_contact("Joe", urn="fcm:12345abcde", auth="123456abcdef")
        msg = joe.send("Hello, world!", self.admin, trigger_send=False, attachments=['image/jpeg:https://example.com/attachments/pic.jpg'])[0]

        with self.settings(SEND_MESSAGES=True):

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, '{ "success": 1, "multicast_id": 123456, "failures": 0 }')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEqual(msg.status, WIRED)
                self.assertTrue(msg.sent_on)

                data = json.dumps({
                    'data': {
                        'type': 'rapidpro',
                        'title': 'FCM Channel',
                        'message': 'Hello, world!\nhttps://example.com/attachments/pic.jpg',
                        'message_id': msg.id
                    },
                    'content_available': True,
                    'to': '123456abcdef',
                    'priority': 'high',
                    'notification': {
                        'title': 'FCM Channel',
                        'body': 'Hello, world!\nhttps://example.com/attachments/pic.jpg'
                    }
                })

                mock.assert_called_once_with('https://fcm.googleapis.com/fcm/send',
                                             data=data,
                                             headers={
                                                 'Content-Type': 'application/json',
                                                 'Authorization': 'key=123456789',
                                                 'User-agent': 'RapidPro'
                                             },
                                             timeout=5)

                self.clear_cache()

    @override_settings(SEND_MESSAGES=True)
    def test_send_quick_replies(self):
        quick_replies = ['Yes', 'No']
        joe = self.create_contact("Joe", urn="fcm:12345abcde", auth="123456abcdef")
        msg = joe.send("Hello, world!", self.admin, trigger_send=False, quick_replies=quick_replies)[0]

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "success": 1, "multicast_id": 123456, "failures": 0 }')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)

            data = json.dumps({
                'data': {
                    'type': 'rapidpro',
                    'title': 'FCM Channel',
                    'message': 'Hello, world!',
                    'message_id': msg.id,
                    'quick_replies': [{"payload": "Yes", "title": "Yes"},
                                      {"payload": "No", "title": "No"}]
                },
                'content_available': True,
                'to': '123456abcdef',
                'priority': 'high',
                'notification': {
                    'title': 'FCM Channel',
                    'body': 'Hello, world!'
                }
            })

            mock.assert_called_once_with('https://fcm.googleapis.com/fcm/send',
                                         data=data,
                                         headers={
                                             'Content-Type': 'application/json',
                                             'Authorization': 'key=123456789',
                                             'User-agent': 'RapidPro'
                                         },
                                         timeout=5)

            self.clear_cache()


class CourierTest(TembaTest):
    def test_queue_to_courier(self):
        with self.settings(COURIER_CHANNELS=['T']):
            self.channel.channel_type = 'T'
            self.channel.save()

            bob = self.create_contact("Bob", urn='tel:+12065551111')
            incoming = self.create_msg(contact=bob, text="Hello", direction="I", external_id="external-id")

            # create some outgoing messages for our channel
            msg1 = Msg.create_outgoing(self.org, self.admin, 'tel:+12065551111', "Outgoing 1",
                                       attachments=['image/jpg:https://example.com/test.jpg', 'image/jpg:https://example.com/test2.jpg'])
            msg2 = Msg.create_outgoing(self.org, self.admin, 'tel:+12065552222', "Outgoing 2", response_to=incoming,
                                       attachments=[])
            msg3 = Msg.create_outgoing(self.org, self.admin, 'tel:+12065553333', "Outgoing 3", high_priority=False,
                                       attachments=None)
            msg4 = Msg.create_outgoing(self.org, self.admin, 'tel:+12065554444', "Outgoing 4", high_priority=True)
            msg5 = Msg.create_outgoing(self.org, self.admin, 'tel:+12065554444', "Outgoing 5", high_priority=True)
            all_msgs = [msg1, msg2, msg3, msg4, msg5]

            Msg.send_messages(all_msgs)

            # we should have been queued to our courier queues and our msgs should be marked as such
            for msg in all_msgs:
                msg.refresh_from_db()
                self.assertEqual(msg.status, QUEUED)

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

            self.assertEqual([[m['text'] for m in b] for b in high_priority_msgs], [["Outgoing 4", "Outgoing 5"]])
            self.assertEqual([[m['text'] for m in b] for b in low_priority_msgs], [["Outgoing 1"], ["Outgoing 2"], ["Outgoing 3"]])

            self.assertEqual(low_priority_msgs[0][0]['attachments'], ['image/jpg:https://example.com/test.jpg', 'image/jpg:https://example.com/test2.jpg'])
            self.assertEqual(low_priority_msgs[0][0]['tps_cost'], 2)
            self.assertIsNone(low_priority_msgs[1][0]['attachments'])
            self.assertEqual(low_priority_msgs[1][0]['tps_cost'], 1)
            self.assertEqual(low_priority_msgs[1][0]['response_to_external_id'], "external-id")
            self.assertIsNone(low_priority_msgs[2][0]['attachments'])


class HandleEventTest(TembaTest):
    def test_new_conversation_channel_event(self):
        self.joe = self.create_contact("Joe", "+12065551212")
        flow = self.get_flow('favorites')
        Trigger.create(self.org, self.admin, Trigger.TYPE_NEW_CONVERSATION, flow)

        event = ChannelEvent.create(self.channel, "tel:+12065551212", ChannelEvent.TYPE_NEW_CONVERSATION, timezone.now())
        push_task(self.org, HANDLER_QUEUE, HANDLE_EVENT_TASK, dict(type=CHANNEL_EVENT, event_id=event.id))

        # should have been started in our flow
        self.assertTrue(FlowRun.objects.filter(flow=flow, contact=self.joe))

    def test_stop_contact_channel_event(self):
        self.joe = self.create_contact("Joe", "+12065551212")

        self.assertFalse(self.joe.is_stopped)
        event = ChannelEvent.create(self.channel, "tel:+12065551212", ChannelEvent.TYPE_STOP_CONTACT, timezone.now())
        push_task(self.org, HANDLER_QUEUE, HANDLE_EVENT_TASK, dict(type=CHANNEL_EVENT, event_id=event.id))

        self.joe.refresh_from_db()
        self.assertTrue(self.joe.is_stopped)
