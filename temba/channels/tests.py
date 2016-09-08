# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import base64
import calendar
import copy
import hashlib
import hmac
import json
import pytz
import telegram
import time
import urllib2
import uuid

from datetime import timedelta, date
from django.conf import settings
from django.contrib.auth.models import User, Group
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.test.utils import override_settings
from django.utils import timezone
from django.template import loader, Context
from mock import patch
from redis_cache import get_redis_connection
from smartmin.tests import SmartminTest
from temba.api.models import WebHookEvent, SMS_RECEIVED
from temba.contacts.models import Contact, ContactGroup, ContactURN, URN, TEL_SCHEME, TWITTER_SCHEME, EXTERNAL_SCHEME
from temba.contacts.models import TELEGRAM_SCHEME, FACEBOOK_SCHEME
from temba.ivr.models import IVRCall, PENDING, RINGING
from temba.msgs.models import Broadcast, Msg, IVR, WIRED, FAILED, SENT, DELIVERED, ERRORED, INCOMING, INTERRUPTED
from temba.msgs.models import MSG_SENT_KEY, SystemLabel
from temba.orgs.models import Org, ALL_EVENTS, ACCOUNT_SID, ACCOUNT_TOKEN, APPLICATION_SID, NEXMO_KEY, NEXMO_SECRET, FREE_PLAN, NEXMO_UUID
from temba.tests import TembaTest, MockResponse, MockTwilioClient, MockRequestValidator
from temba.triggers.models import Trigger
from temba.utils import dict_to_struct
from telegram import User as TelegramUser
from twilio import TwilioRestException
from twilio.util import RequestValidator
from twython import TwythonError
from urllib import urlencode
from .models import Channel, ChannelCount, ChannelEvent, SyncEvent, Alert, ChannelLog, TEMBA_HEADERS
from .tasks import check_channels_task, squash_channelcounts
from .views import TWILIO_SUPPORTED_COUNTRIES


class ChannelTest(TembaTest):

    def setUp(self):
        super(ChannelTest, self).setUp()

        self.channel.delete()

        self.tel_channel = Channel.create(self.org, self.user, 'RW', 'A', name="Test Channel", address="+250785551212",
                                          role="SR", secret="12345", gcm_id="123")

        self.twitter_channel = Channel.create(self.org, self.user, None, 'TT', name="Twitter Channel",
                                              address="billy_bob", role="SR", scheme='twitter')

        self.released_channel = Channel.create(None, self.user, None, 'NX', name="Released Channel", address=None,
                                               secret=None, gcm_id="000")

    def send_message(self, numbers, message, org=None, user=None):
        if not org:
            org = self.org

        if not user:
            user = self.user

        group = ContactGroup.get_or_create(org, user, 'Numbers: %s' % ','.join(numbers))
        contacts = list()
        for number in numbers:
            contacts.append(Contact.get_or_create(org, user, name=None, urns=[URN.from_tel(number)]))

        group.contacts.add(*contacts)

        broadcast = Broadcast.create(org, user, message, [group])
        broadcast.send()

        msg = Msg.all_messages.filter(broadcast=broadcast).order_by('text', 'pk')
        if len(numbers) == 1:
            return msg.first()
        else:
            return list(msg)

    def assertHasCommand(self, cmd_name, response):
        self.assertEquals(200, response.status_code)
        data = json.loads(response.content)

        for cmd in data['cmds']:
            if cmd['cmd'] == cmd_name:
                return

        raise Exception("Did not find '%s' cmd in response: '%s'" % (cmd_name, response.content))

    def test_message_context(self):
        context = self.tel_channel.build_message_context()
        self.assertEqual(context['__default__'], '+250 785 551 212')
        self.assertEqual(context['name'], 'Test Channel')
        self.assertEqual(context['address'], '+250 785 551 212')
        self.assertEqual(context['tel'], '+250 785 551 212')
        self.assertEqual(context['tel_e164'], '+250785551212')

        context = self.twitter_channel.build_message_context()
        self.assertEqual(context['__default__'], '@billy_bob')
        self.assertEqual(context['name'], 'Twitter Channel')
        self.assertEqual(context['address'], '@billy_bob')
        self.assertEqual(context['tel'], '')
        self.assertEqual(context['tel_e164'], '')

        context = self.released_channel.build_message_context()
        self.assertEqual(context['__default__'], 'Released Channel')
        self.assertEqual(context['name'], 'Released Channel')
        self.assertEqual(context['address'], '')
        self.assertEqual(context['tel'], '')
        self.assertEqual(context['tel_e164'], '')

    def test_deactivate(self):
        self.login(self.admin)
        self.tel_channel.is_active = False
        self.tel_channel.save()
        response = self.client.get(reverse('channels.channel_read', args=[self.tel_channel.uuid]))
        self.assertEquals(404, response.status_code)

    def test_delegate_channels(self):

        self.login(self.admin)

        # we don't support IVR yet
        self.assertFalse(self.org.supports_ivr())

        # pretend we are connected to twiliko
        self.org.config = json.dumps(dict(ACCOUNT_SID='AccountSid', ACCOUNT_TOKEN='AccountToken', APPLICATION_SID='AppSid'))
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
        self.assertEquals(200, response.status_code)
        self.assertEquals('Sorry, a caller cannot be added for that number', response.context['form'].errors['channel'][0])

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

        self.assertEquals('A connection to a Twilio account is required', response.context['form'].errors['connection'][0])

    def test_get_channel_type_name(self):
        self.assertEquals(self.tel_channel.get_channel_type_name(), "Android Phone")
        self.assertEquals(self.twitter_channel.get_channel_type_name(), "Twitter Channel")
        self.assertEquals(self.released_channel.get_channel_type_name(), "Nexmo Channel")

    def test_channel_selection(self):
        # make our default tel channel MTN
        mtn = self.tel_channel
        mtn.name = "MTN"
        mtn.save()

        # create a channel for Tigo too
        tigo = Channel.create(self.org, self.user, 'RW', 'A', "Tigo", "+250725551212", secret="11111", gcm_id="456")

        # new contact on MTN should send with the MTN channel
        msg = self.send_message(['+250788382382'], "Sent to an MTN number")
        self.assertEquals(mtn, self.org.get_send_channel(contact_urn=msg.contact_urn))
        self.assertEquals(mtn, msg.channel)

        # new contact on Tigo should send with the Tigo channel
        msg = self.send_message(['+250728382382'], "Sent to a Tigo number")
        self.assertEquals(tigo, self.org.get_send_channel(contact_urn=msg.contact_urn))
        self.assertEquals(tigo, msg.channel)

        # now our MTN contact texts, the tigo number which should change their affinity
        msg = Msg.create_incoming(tigo, "tel:+250788382382", "Send an inbound message to Tigo")
        self.assertEquals(tigo, msg.channel)
        self.assertEquals(tigo, self.org.get_send_channel(contact_urn=msg.contact_urn))
        self.assertEquals(tigo, ContactURN.objects.get(path='+250788382382').channel)

        # new contact on Airtel (some overlap) should send with the Tigo channel since it is newest
        msg = self.send_message(['+250738382382'], "Sent to a Airtel number")
        self.assertEquals(tigo, self.org.get_send_channel(contact_urn=msg.contact_urn))
        self.assertEquals(tigo, msg.channel)

        # add a voice caller
        caller = Channel.add_call_channel(self.org, self.user, self.tel_channel)

        # set our affinity to the caller (ie, they were on an ivr call)
        ContactURN.objects.filter(path='+250788382382').update(channel=caller)
        self.assertEquals(mtn, self.org.get_send_channel(contact_urn=ContactURN.objects.get(path='+250788382382')))

        # change channel numbers to be shortcodes, i.e. no overlap with contact numbers
        mtn.address = '1234'
        mtn.save()
        tigo.address = '1235'
        tigo.save()

        # should return the newest channel which is TIGO
        msg = self.send_message(['+250788382382'], "Sent to an MTN number, but with shortcode channels")
        self.assertEquals(tigo, msg.channel)
        self.assertEquals(tigo, self.org.get_send_channel(contact_urn=msg.contact_urn))

        # check for twitter
        self.assertEquals(self.twitter_channel, self.org.get_send_channel(scheme=TWITTER_SCHEME))

        contact = self.create_contact("Billy", number="+250722222222", twitter="billy_bob")
        twitter_urn = contact.get_urn(schemes=[TWITTER_SCHEME])
        self.assertEquals(self.twitter_channel, self.org.get_send_channel(contact_urn=twitter_urn))

        # calling without scheme or urn should raise exception
        self.assertRaises(ValueError, self.org.get_send_channel)

    def test_message_splitting(self):
        # external API requires messages to be <= 160 chars
        self.tel_channel.channel_type = 'EX'
        self.tel_channel.save()

        msg = Msg.create_outgoing(self.org, self.user, 'tel:+250738382382', 'x' * 400)  # 400 chars long
        Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
        self.assertEqual(3, Msg.all_messages.get(pk=msg.id).msg_count)

        # Nexmo limit is 1600
        self.tel_channel.channel_type = 'NX'
        self.tel_channel.save()
        cache.clear()  # clear the channel from cache

        msg = Msg.create_outgoing(self.org, self.user, 'tel:+250738382382', 'y' * 400)
        Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
        self.assertEqual(self.tel_channel, Msg.all_messages.get(pk=msg.id).channel)
        self.assertEqual(1, Msg.all_messages.get(pk=msg.id).msg_count)

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

        self.assertEquals(norm_c1.get_urn(TEL_SCHEME).path, "+250788111222")
        self.assertEquals(norm_c2.get_urn(TEL_SCHEME).path, "+250788333444")
        self.assertEquals(norm_c3.get_urn(TEL_SCHEME).path, "+18006927753")

    def test_delete(self):
        self.org.administrators.add(self.user)
        self.user.set_org(self.org)
        self.login(self.user)

        # a message, a call, and a broadcast
        msg = self.send_message(['250788382382'], "How is it going?")
        call = ChannelEvent.create(self.tel_channel, "tel:+250788383385", ChannelEvent.TYPE_CALL_IN, timezone.now(), 5)

        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.tel_channel, msg.channel)
        self.assertEquals(1, Msg.get_messages(self.org).count())
        self.assertEquals(1, ChannelEvent.get_all(self.org).count())
        self.assertEquals(1, Broadcast.get_broadcasts(self.org).count())

        # start off in the pending state
        self.assertEquals('P', msg.status)

        response = self.fetch_protected(reverse('channels.channel_delete', args=[self.tel_channel.pk]), self.user)
        self.assertContains(response, 'Test Channel')

        response = self.fetch_protected(reverse('channels.channel_delete', args=[self.tel_channel.pk]),
                                        post_data=dict(remove=True), user=self.user)
        self.assertRedirect(response, reverse("orgs.org_home"))

        msg = Msg.all_messages.get(pk=msg.pk)
        self.assertIsNotNone(msg.channel)
        self.assertIsNone(msg.channel.gcm_id)
        self.assertIsNone(msg.channel.secret)
        self.assertEquals(self.org, msg.org)

        # queued messages for the channel should get marked as failed
        self.assertEquals('F', msg.status)

        call = ChannelEvent.objects.get(pk=call.pk)
        self.assertIsNotNone(call.channel)
        self.assertIsNone(call.channel.gcm_id)
        self.assertIsNone(call.channel.secret)

        self.assertEquals(self.org, call.org)

        broadcast = Broadcast.objects.get(pk=msg.broadcast.pk)
        self.assertEquals(self.org, broadcast.org)

        # should still be considered that user's message, call and broadcast
        self.assertEquals(1, Msg.get_messages(self.org).count())
        self.assertEquals(1, ChannelEvent.get_all(self.org).count())
        self.assertEquals(1, Broadcast.get_broadcasts(self.org).count())

        # syncing this channel should result in a release
        post_data = dict(cmds=[dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="60", net="UMTS", pending=[], retry=[])])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # our response should contain a release
        self.assertHasCommand('rel', response)

        # create a channel
        channel = Channel.create(self.org, self.user, 'RW', 'A', "Test Channel", "0785551212",
                                 secret="12345", gcm_id="123")

        response = self.fetch_protected(reverse('channels.channel_delete', args=[channel.pk]), self.superuser)
        self.assertContains(response, 'Test Channel')

        response = self.fetch_protected(reverse('channels.channel_delete', args=[channel.pk]),
                                        post_data=dict(remove=True), user=self.superuser)
        self.assertRedirect(response, reverse("orgs.org_home"))

        # create a channel
        channel = Channel.create(self.org, self.user, 'RW', 'A', "Test Channel", "0785551212",
                                 secret="12345", gcm_id="123")

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
        channel = Channel.create(self.org, self.user, None, Channel.TYPE_ANDROID, None, "+250781112222", gcm_id="asdf", secret="asdf")
        channel.created_on = timezone.now() - timedelta(hours=2)
        channel.save()

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

        self.assertEquals(self.org2, other_user.get_org())
        response = self.client.get('/', follow=True)
        self.assertNotIn('channel_type', response.context, msg="Found channel_type in context")

        other_user.set_org(self.org)

        self.assertEquals(1, self.org.channels.filter(is_active=True).count())
        self.assertEquals(self.org, other_user.get_org())

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
            signature = hmac.new(key=key, msg=bytes(post_data), digestmod=hashlib.sha256).digest()

            # base64 and url sanitize
            signature = urllib2.quote(base64.urlsafe_b64encode(signature))

        return self.client.post("%s?signature=%s&ts=%d" % (reverse('sync', args=[channel.pk]), signature, ts),
                                content_type='application/json', data=post_data)

    def test_update(self):
        update_url = reverse('channels.channel_update', args=[self.tel_channel.id])

        # only user of the org can view the update page of a channel
        self.client.logout()
        self.login(self.user)
        response = self.client.get(update_url)
        self.assertEquals(302, response.status_code)

        self.login(self.user)
        # visit the channel's update page as a manager within the channel's organization
        self.org.administrators.add(self.user)
        response = self.fetch_protected(update_url, self.user)
        self.assertEquals(200, response.status_code)
        self.assertEquals(response.request['PATH_INFO'], update_url)

        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEquals(channel.name, "Test Channel")
        self.assertEquals(channel.address, "+250785551212")

        postdata = dict()
        postdata['name'] = "Test Channel Update1"
        postdata['address'] = "+250785551313"

        self.login(self.user)
        response = self.client.post(update_url, postdata, follow=True)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEquals(channel.name, "Test Channel Update1")
        self.assertEquals(channel.address, "+250785551313")

        # if we change the channel to a twilio type, shouldn't be able to edit our address
        channel.channel_type = Channel.TYPE_TWILIO
        channel.save()

        response = self.client.get(update_url)
        self.assertFalse('address' in response.context['form'].fields)

        # bring it back to android
        channel.channel_type = Channel.TYPE_ANDROID
        channel.save()

        # visit the channel's update page as administrator
        self.org.administrators.add(self.user)
        self.user.set_org(self.org)
        response = self.fetch_protected(update_url, self.user)
        self.assertEquals(200, response.status_code)
        self.assertEquals(response.request['PATH_INFO'], update_url)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEquals(channel.name, "Test Channel Update1")
        self.assertEquals(channel.address, "+250785551313")

        postdata = dict()
        postdata['name'] = "Test Channel Update2"
        postdata['address'] = "+250785551414"

        response = self.fetch_protected(update_url, self.user, postdata)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEquals(channel.name, "Test Channel Update2")
        self.assertEquals(channel.address, "+250785551414")

        # visit the channel's update page as superuser
        self.superuser.set_org(self.org)
        response = self.fetch_protected(update_url, self.superuser)
        self.assertEquals(200, response.status_code)
        self.assertEquals(response.request['PATH_INFO'], update_url)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEquals(channel.name, "Test Channel Update2")
        self.assertEquals(channel.address, "+250785551414")

        postdata = dict()
        postdata['name'] = "Test Channel Update3"
        postdata['address'] = "+250785551515"

        response = self.fetch_protected(update_url, self.superuser, postdata)
        channel = Channel.objects.get(pk=self.tel_channel.id)
        self.assertEquals(channel.name, "Test Channel Update3")
        self.assertEquals(channel.address, "+250785551515")

        # make sure channel works with alphanumeric numbers
        channel.address = "EATRIGHT"
        self.assertEquals("EATRIGHT", channel.get_address_display())
        self.assertEquals("EATRIGHT", channel.get_address_display(e164=True))

        # change channel type to Twitter
        channel.channel_type = Channel.TYPE_TWITTER
        channel.address = 'billy_bob'
        channel.scheme = 'twitter'
        channel.config = json.dumps({'handle_id': 12345, 'oauth_token': 'abcdef', 'oauth_token_secret': '23456'})
        channel.save()

        self.assertEquals('@billy_bob', channel.get_address_display())
        self.assertEquals('@billy_bob', channel.get_address_display(e164=True))

        response = self.fetch_protected(update_url, self.user)
        self.assertEquals(200, response.status_code)
        self.assertIn('name', response.context['fields'])
        self.assertIn('alert_email', response.context['fields'])
        self.assertIn('address', response.context['fields'])
        self.assertNotIn('country', response.context['fields'])

        postdata = dict()
        postdata['name'] = "Twitter2"
        postdata['alert_email'] = "bob@example.com"
        postdata['address'] = "billy_bob"

        with patch('temba.utils.mage.MageClient.refresh_twitter_stream') as refresh_twitter_stream:
            refresh_twitter_stream.return_value = dict()

            self.fetch_protected(update_url, self.user, postdata)
            channel = Channel.objects.get(pk=self.tel_channel.id)
            self.assertEquals(channel.name, "Twitter2")
            self.assertEquals(channel.alert_email, "bob@example.com")
            self.assertEquals(channel.address, "billy_bob")

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
        self.assertEquals(2, SyncEvent.objects.all().count())

        # non-org users can't view our channels
        self.login(self.non_org_user)
        response = self.client.get(reverse('channels.channel_read', args=[self.tel_channel.uuid]))
        self.assertLoginRedirect(response)

        # org users can
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.user)

        self.assertEquals(len(response.context['source_stats']), len(SyncEvent.objects.values_list('power_source', flat=True).distinct()))
        self.assertEquals('AC', response.context['source_stats'][0][0])
        self.assertEquals(1, response.context['source_stats'][0][1])
        self.assertEquals('BAT', response.context['source_stats'][1][0])
        self.assertEquals(1, response.context['source_stats'][0][1])

        self.assertEquals(len(response.context['network_stats']), len(SyncEvent.objects.values_list('network_type', flat=True).distinct()))
        self.assertEquals('UMTS', response.context['network_stats'][0][0])
        self.assertEquals(1, response.context['network_stats'][0][1])
        self.assertEquals('WIFI', response.context['network_stats'][1][0])
        self.assertEquals(1, response.context['network_stats'][1][1])

        self.assertTrue(len(response.context['latest_sync_events']) <= 5)

        response = self.fetch_protected(reverse('orgs.org_home'), self.admin)
        self.assertNotContains(response, 'Enable Voice')

        # Add twilio credentials to make sure we can add calling for our android channel
        twilio_config = {ACCOUNT_SID: 'SID', ACCOUNT_TOKEN: 'TOKEN', APPLICATION_SID: 'APP SID'}
        config = self.org.config_json()
        config.update(twilio_config)
        self.org.config = json.dumps(config)
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
        self.assertEquals(200, response.status_code)

        # now that we can access the channel, which messages do we display in the chart?
        joe = self.create_contact('Joe', '+2501234567890')
        test_contact = Contact.get_test_contact(self.admin)

        # should have two series, one for incoming one for outgoing
        self.assertEquals(2, len(response.context['message_stats']))

        # but only an outgoing message so far
        self.assertEquals(0, len(response.context['message_stats'][0]['data']))
        self.assertEquals(1, response.context['message_stats'][1]['data'][-1]['count'])

        # we have one row for the message stats table
        self.assertEquals(1, len(response.context['message_stats_table']))
        # only one outgoing message
        self.assertEquals(0, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEquals(1, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEquals(0, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEquals(0, response.context['message_stats_table'][0]['outgoing_ivr_count'])

        # send messages with a test contact
        Msg.create_incoming(self.tel_channel, test_contact.get_urn().urn, 'This incoming message will not be counted')
        Msg.create_outgoing(self.org, self.user, test_contact, 'This outgoing message will not be counted')

        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.superuser)
        self.assertEquals(200, response.status_code)

        # nothing should change since it's a test contact
        self.assertEquals(0, len(response.context['message_stats'][0]['data']))
        self.assertEquals(1, response.context['message_stats'][1]['data'][-1]['count'])

        # no change on the table starts too
        self.assertEquals(1, len(response.context['message_stats_table']))
        self.assertEquals(0, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEquals(1, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEquals(0, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEquals(0, response.context['message_stats_table'][0]['outgoing_ivr_count'])

        # send messages with a normal contact
        Msg.create_incoming(self.tel_channel, joe.get_urn(TEL_SCHEME).urn, 'This incoming message will be counted')
        Msg.create_outgoing(self.org, self.user, joe, 'This outgoing message will be counted')

        # now we have an inbound message and two outbounds
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.superuser)
        self.assertEquals(200, response.status_code)
        self.assertEquals(1, response.context['message_stats'][0]['data'][-1]['count'])

        # this assertion is problematic causing time-sensitive failures, to reconsider
        # self.assertEquals(2, response.context['message_stats'][1]['data'][-1]['count'])

        # message stats table have an inbound and two outbounds in the last month
        self.assertEquals(1, len(response.context['message_stats_table']))
        self.assertEquals(1, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEquals(2, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEquals(0, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEquals(0, response.context['message_stats_table'][0]['outgoing_ivr_count'])

        # test cases for IVR messaging, make our relayer accept calls
        self.tel_channel.role = 'SCAR'
        self.tel_channel.save()

        from temba.msgs.models import IVR
        Msg.create_incoming(self.tel_channel, test_contact.get_urn().urn, 'incoming ivr as a test contact', msg_type=IVR)
        Msg.create_outgoing(self.org, self.user, test_contact, 'outgoing ivr as a test contact', msg_type=IVR)
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.superuser)

        # nothing should have changed
        self.assertEquals(2, len(response.context['message_stats']))

        self.assertEquals(1, len(response.context['message_stats_table']))
        self.assertEquals(1, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEquals(2, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEquals(0, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEquals(0, response.context['message_stats_table'][0]['outgoing_ivr_count'])

        # now let's create an ivr interaction from a real contact
        Msg.create_incoming(self.tel_channel, joe.get_urn().urn, 'incoming ivr', msg_type=IVR)
        Msg.create_outgoing(self.org, self.user, joe, 'outgoing ivr', msg_type=IVR)
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.uuid]), self.superuser)

        self.assertEquals(4, len(response.context['message_stats']))
        self.assertEquals(1, response.context['message_stats'][2]['data'][0]['count'])
        self.assertEquals(1, response.context['message_stats'][3]['data'][0]['count'])

        self.assertEquals(1, len(response.context['message_stats_table']))
        self.assertEquals(1, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEquals(2, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEquals(1, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEquals(1, response.context['message_stats_table'][0]['outgoing_ivr_count'])

    def test_invalid(self):

        # Must be POST
        response = self.client.get("%s?signature=sig&ts=123" % (reverse('sync', args=[100])), content_type='application/json')
        self.assertEquals(500, response.status_code)

        # Unknown channel
        response = self.client.post("%s?signature=sig&ts=123" % (reverse('sync', args=[999])), content_type='application/json')
        self.assertEquals(200, response.status_code)
        self.assertEquals('rel', json.loads(response.content)['cmds'][0]['cmd'])

        # too old
        ts = int(time.time()) - 60 * 16
        response = self.client.post("%s?signature=sig&ts=%d" % (reverse('sync', args=[self.tel_channel.pk]), ts), content_type='application/json')
        self.assertEquals(401, response.status_code)
        self.assertEquals(3, json.loads(response.content)['error_id'])

    def test_is_ussd_channel(self):
        Channel.objects.all().delete()
        self.login(self.admin)

        # add a non USSD channel
        reg_data = dict(cmds=[dict(cmd="gcm", gcm_id="GCM111", uuid='uuid'),
                              dict(cmd='status', cc='RW', dev='Nexus')])

        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        self.assertEqual(200, response.status_code)

        # add a USSD channel
        post_data = {
            "country": "ZA",
            "number": "+273454325324",
            "account_key": "account1",
            "conversation_key": "conversation1",
            "transport_name": ""
        }

        response = self.client.post(reverse('channels.channel_claim_vumi_ussd'), post_data)
        self.assertEqual(302, response.status_code)

        self.assertEqual(Channel.objects.first().channel_type, Channel.TYPE_VUMI_USSD)
        self.assertTrue(Channel.objects.first().is_ussd())
        self.assertFalse(Channel.objects.last().is_ussd())

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
        self.assertEqual(android1.created_by.username, settings.ANONYMOUS_USER_NAME)

        # check channel JSON in response
        response_json = json.loads(response.content)
        self.assertEqual(response_json, dict(cmds=[dict(cmd='reg',
                                                        relayer_claim_code=android1.claim_code,
                                                        relayer_secret=android1.secret,
                                                        relayer_id=android1.id)]))

        # try registering again with same details
        response = self.client.post(reverse('register'), json.dumps(reg_data), content_type='application/json')
        self.assertEqual(response.status_code, 200)

        android1 = Channel.objects.get()
        response_json = json.loads(response.content)

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
        self.assertTrue('success' in response.get('Location', None))
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
        claim_code = json.loads(response.content)['cmds'][0]['relayer_claim_code']
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

        # try to claim a bulk Nexmo sender (without adding Nexmo account to org)
        claim_nexmo_url = reverse('channels.channel_create_bulk_sender') + "?connection=NX&channel=%d" % android2.pk
        response = self.client.post(claim_nexmo_url, dict(connection='NX', channel=android2.pk))
        self.assertFormError(response, 'form', 'connection', "A connection to a Nexmo account is required")

        # send channel is still our Android device
        self.assertEqual(self.org.get_send_channel(TEL_SCHEME), android2)
        self.assertFalse(self.org.is_connected_to_nexmo())

        # now connect to nexmo
        with patch('temba.nexmo.NexmoClient.update_account') as connect:
            connect.return_value = True
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

        claim_code = json.loads(response.content)['cmds'][0]['relayer_claim_code']

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
        claim_code = json.loads(response.content)['cmds'][0]['relayer_claim_code']

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

    @patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_claim_twilio(self):
        self.login(self.admin)

        # remove any existing channels
        self.org.channels.update(is_active=False, org=None)

        # make sure twilio is on the claim page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, "Twilio")
        self.assertContains(response, reverse('orgs.org_twilio_connect'))

        twilio_config = dict()
        twilio_config[ACCOUNT_SID] = 'account-sid'
        twilio_config[ACCOUNT_TOKEN] = 'account-token'
        twilio_config[APPLICATION_SID] = 'TwilioTestSid'

        self.org.config = json.dumps(twilio_config)
        self.org.save()

        # hit the claim page, should now have a claim twilio link
        claim_twilio = reverse('channels.channel_claim_twilio')
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, claim_twilio)

        response = self.client.get(claim_twilio)
        self.assertTrue('account_trial' in response.context)
        self.assertFalse(response.context['account_trial'])

        with patch('temba.orgs.models.Org.get_twilio_client') as mock_get_twilio_client:
            mock_get_twilio_client.return_value = None

            response = self.client.get(claim_twilio)
            self.assertRedirects(response, reverse('channels.channel_claim'))

            mock_get_twilio_client.side_effect = TwilioRestException(401, 'http://twilio', msg='Authentication Failure', code=20003)

            response = self.client.get(claim_twilio)
            self.assertRedirects(response, reverse('channels.channel_claim'))

        with patch('temba.tests.MockTwilioClient.MockAccounts.get') as mock_get:
            mock_get.return_value = MockTwilioClient.MockAccount('Trial')

            response = self.client.get(claim_twilio)
            self.assertTrue('account_trial' in response.context)
            self.assertTrue(response.context['account_trial'])

        with patch('temba.tests.MockTwilioClient.MockPhoneNumbers.list') as mock_numbers:
            mock_numbers.return_value = [MockTwilioClient.MockPhoneNumber('+12062345678')]

            with patch('temba.tests.MockTwilioClient.MockShortCodes.list') as mock_short_codes:
                mock_short_codes.return_value = []

                response = self.client.get(claim_twilio)
                self.assertContains(response, '206-234-5678')

                # claim it
                response = self.client.post(claim_twilio, dict(country='US', phone_number='12062345678'))
                self.assertRedirects(response, reverse('public.public_welcome') + "?success")

                # make sure it is actually connected
                channel = Channel.objects.get(channel_type='T', org=self.org)
                self.assertEqual(channel.role, Channel.ROLE_CALL + Channel.ROLE_ANSWER + Channel.ROLE_SEND + Channel.ROLE_RECEIVE)

        # voice only number
        with patch('temba.tests.MockTwilioClient.MockPhoneNumbers.list') as mock_numbers:
            mock_numbers.return_value = [MockTwilioClient.MockPhoneNumber('+554139087835')]

            with patch('temba.tests.MockTwilioClient.MockShortCodes.list') as mock_short_codes:
                mock_short_codes.return_value = []
                Channel.objects.all().delete()

                response = self.client.get(claim_twilio)
                self.assertContains(response, '+55 41 3908-7835')

                # claim it
                response = self.client.post(claim_twilio, dict(country='BR', phone_number='554139087835'))
                self.assertRedirects(response, reverse('public.public_welcome') + "?success")

                # make sure it is actually connected
                channel = Channel.objects.get(channel_type='T', org=self.org)
                self.assertEqual(channel.role, Channel.ROLE_CALL + Channel.ROLE_ANSWER)

        with patch('temba.tests.MockTwilioClient.MockPhoneNumbers.list') as mock_numbers:
            mock_numbers.return_value = [MockTwilioClient.MockPhoneNumber('+4545335500')]

            with patch('temba.tests.MockTwilioClient.MockShortCodes.list') as mock_short_codes:
                mock_short_codes.return_value = []

                Channel.objects.all().delete()

                response = self.client.get(claim_twilio)
                self.assertContains(response, '45 33 55 00')

                # claim it
                response = self.client.post(claim_twilio, dict(country='DK', phone_number='4545335500'))
                self.assertRedirects(response, reverse('public.public_welcome') + "?success")

                # make sure it is actually connected
                Channel.objects.get(channel_type='T', org=self.org)

        with patch('temba.tests.MockTwilioClient.MockPhoneNumbers.list') as mock_numbers:
            mock_numbers.return_value = []

            with patch('temba.tests.MockTwilioClient.MockShortCodes.list') as mock_short_codes:
                mock_short_codes.return_value = [MockTwilioClient.MockShortCode('8080')]
                Channel.objects.all().delete()

                self.org.timezone = 'America/New_York'
                self.org.save()

                response = self.client.get(claim_twilio)
                self.assertContains(response, '8080')
                self.assertContains(response, 'class="country">US')  # we look up the country from the timezone

                # claim it
                response = self.client.post(claim_twilio, dict(country='US', phone_number='8080'))
                self.assertRedirects(response, reverse('public.public_welcome') + "?success")

                # make sure it is actually connected
                Channel.objects.get(channel_type='T', org=self.org)

        twilio_channel = self.org.channels.all().first()
        self.assertEquals('T', twilio_channel.channel_type)

        with patch('temba.tests.MockTwilioClient.MockPhoneNumbers.update') as mock_numbers:

            # our twilio channel removal should fail on bad auth
            mock_numbers.side_effect = TwilioRestException(401, 'http://twilio', msg='Authentication Failure', code=20003)
            self.client.post(reverse('channels.channel_delete', args=[twilio_channel.pk]))
            self.assertIsNotNone(self.org.channels.all().first())

            # or other arbitrary twilio errors
            mock_numbers.side_effect = TwilioRestException(400, 'http://twilio', msg='Twilio Error', code=123)
            self.client.post(reverse('channels.channel_delete', args=[twilio_channel.pk]))
            self.assertIsNotNone(self.org.channels.all().first())

            # now lets be successful
            mock_numbers.side_effect = None
            self.client.post(reverse('channels.channel_delete', args=[twilio_channel.pk]))
            self.assertIsNone(self.org.channels.all().first())

    @patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_claim_twilio_messaging_service(self):

        self.login(self.admin)

        # remove any existing channels
        self.org.channels.all().delete()

        # make sure twilio is on the claim page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, "Twilio")
        self.assertContains(response, reverse('orgs.org_twilio_connect'))

        twilio_config = dict()
        twilio_config[ACCOUNT_SID] = 'account-sid'
        twilio_config[ACCOUNT_TOKEN] = 'account-token'
        twilio_config[APPLICATION_SID] = 'TwilioTestSid'

        self.org.config = json.dumps(twilio_config)
        self.org.save()

        claim_twilio_ms = reverse('channels.channel_claim_twilio_messaging_service')
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, claim_twilio_ms)

        response = self.client.get(claim_twilio_ms)
        self.assertTrue('account_trial' in response.context)
        self.assertFalse(response.context['account_trial'])

        with patch('temba.orgs.models.Org.get_twilio_client') as mock_get_twilio_client:
            mock_get_twilio_client.return_value = None

            response = self.client.get(claim_twilio_ms)
            self.assertRedirects(response, reverse('channels.channel_claim'))

            mock_get_twilio_client.side_effect = TwilioRestException(401, 'http://twilio', msg='Authentication Failure', code=20003)

            response = self.client.get(claim_twilio_ms)
            self.assertRedirects(response, reverse('channels.channel_claim'))

        with patch('temba.tests.MockTwilioClient.MockAccounts.get') as mock_get:
            mock_get.return_value = MockTwilioClient.MockAccount('Trial')

            response = self.client.get(claim_twilio_ms)
            self.assertTrue('account_trial' in response.context)
            self.assertTrue(response.context['account_trial'])

        response = self.client.get(claim_twilio_ms)
        self.assertEqual(response.context['form'].fields['country'].choices, list(TWILIO_SUPPORTED_COUNTRIES))
        self.assertContains(response, "icon-channel-twilio")

        response = self.client.post(claim_twilio_ms, dict())
        self.assertTrue(response.context['form'].errors)

        response = self.client.post(claim_twilio_ms, dict(country='US', messaging_service_sid='MSG-SERVICE-SID'))
        channel = self.org.channels.get()
        self.assertRedirects(response, reverse('channels.channel_configuration', args=[channel.pk]))
        self.assertEqual(channel.channel_type, "TMS")
        self.assertEqual(channel.config_json(), dict(messaging_service_sid="MSG-SERVICE-SID"))

    def test_claim_facebook(self):
        self.login(self.admin)

        # remove any existing channels
        Channel.objects.all().delete()

        claim_facebook_url = reverse('channels.channel_claim_facebook')
        token = 'x' * 200

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(400, json.dumps(dict(error=dict(message="Failed validation"))))

            # try to claim facebook, should fail because our verification of the token fails
            response = self.client.post(claim_facebook_url, dict(page_access_token=token))

            # assert we got a normal 200 and it says our token is wrong
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Failed validation")

        # ok this time claim with a success
        with patch('requests.get') as mock_get:
            mock_get.return_value = MockResponse(200, json.dumps(dict(name='Temba', id=10)))
            response = self.client.post(claim_facebook_url, dict(page_access_token=token), follow=True)

            # assert our channel got created
            channel = Channel.objects.get()
            self.assertEqual(channel.config_json()[Channel.CONFIG_AUTH_TOKEN], token)
            self.assertEqual(channel.config_json()[Channel.CONFIG_PAGE_NAME], 'Temba')
            self.assertEqual(channel.address, '10')

            # should be on our configuration page displaying our secret
            self.assertContains(response, channel.secret)

            # test validating our secret
            handler_url = reverse('handlers.facebook_handler', args=['invalid'])
            response = self.client.get(handler_url)
            self.assertEqual(response.status_code, 400)

            # test invalid token
            handler_url = reverse('handlers.facebook_handler', args=[channel.uuid])
            payload = {'hub.mode': 'subscribe', 'hub.verify_token': 'invalid', 'hub.challenge': 'challenge'}
            response = self.client.get(handler_url, payload)
            self.assertEqual(response.status_code, 400)

            # test actual token
            payload['hub.verify_token'] = channel.secret

            # try with unsuccessful callback to subscribe (this fails silently)
            with patch('requests.post') as mock_post:
                mock_post.return_value = MockResponse(400, json.dumps(dict(success=False)))

                response = self.client.get(handler_url, payload)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'challenge')

                # assert we subscribed to events
                self.assertEqual(mock_post.call_count, 1)

            # but try again and we should try again
            with patch('requests.post') as mock_post:
                mock_post.return_value = MockResponse(200, json.dumps(dict(success=True)))

                response = self.client.get(handler_url, payload)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'challenge')

                # assert we subscribed to events
                self.assertEqual(mock_post.call_count, 1)

            # release the channel
            with patch('requests.delete') as mock_delete:
                mock_delete.return_value = MockResponse(200, json.dumps(dict(success=True)))
                channel.release()

                mock_delete.assert_called_once_with('https://graph.facebook.com/v2.5/me/subscribed_apps',
                                                    params=dict(access_token=channel.config_json()[Channel.CONFIG_AUTH_TOKEN]))

    def test_claim_nexmo(self):
        self.login(self.admin)

        # remove any existing channels
        self.org.channels.update(is_active=False, org=None)

        # make sure nexmo is on the claim page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, "Nexmo")
        self.assertContains(response, reverse('orgs.org_nexmo_connect'))

        nexmo_config = dict(NEXMO_KEY='nexmo-key', NEXMO_SECRET='nexmo-secret', NEXMO_UUID='nexmo-uuid')
        self.org.config = json.dumps(nexmo_config)
        self.org.save()

        # hit the claim page, should now have a claim nexmo link
        claim_nexmo = reverse('channels.channel_claim_nexmo')
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, claim_nexmo)

        # let's add a number already connected to the account
        with patch('requests.get') as nexmo_get:
            with patch('requests.post') as nexmo_post:
                nexmo_get.return_value = MockResponse(200, '{"count":1,"numbers":[{"type":"mobile-lvn","country":"US","msisdn":"13607884540"}] }')
                nexmo_post.return_value = MockResponse(200, '{"error-code": "200"}')

                # make sure our number appears on the claim page
                response = self.client.get(claim_nexmo)
                self.assertFalse('account_trial' in response.context)
                self.assertContains(response, '360-788-4540')

                # claim it
                response = self.client.post(claim_nexmo, dict(country='US', phone_number='13607884540'))
                self.assertRedirects(response, reverse('public.public_welcome') + "?success")

                # make sure it is actually connected
                channel = Channel.objects.get(channel_type='NX', org=self.org)
                self.assertEqual(channel.role, Channel.ROLE_SEND + Channel.ROLE_RECEIVE)

                # test the update page for nexmo
                update_url = reverse('channels.channel_update', args=[channel.pk])
                response = self.client.get(update_url)

                # try changing our address
                updated = response.context['form'].initial
                updated['address'] = 'MTN'
                updated['alert_email'] = 'foo@bar.com'

                response = self.client.post(update_url, updated)
                channel = Channel.objects.get(pk=channel.id)

                self.assertEquals('MTN', channel.address)

                # add a canada number
                nexmo_get.return_value = MockResponse(200, '{"count":1,"numbers":[{"type":"mobile-lvn","country":"CA","msisdn":"15797884540"}] }')
                nexmo_post.return_value = MockResponse(200, '{"error-code": "200"}')

                # make sure our number appears on the claim page
                response = self.client.get(claim_nexmo)
                self.assertFalse('account_trial' in response.context)
                self.assertContains(response, '579-788-4540')

                # claim it
                response = self.client.post(claim_nexmo, dict(country='CA', phone_number='15797884540'))
                self.assertRedirects(response, reverse('public.public_welcome') + "?success")

                # make sure it is actually connected
                self.assertTrue(Channel.objects.filter(channel_type='NX', org=self.org, address='+15797884540').first())

                # as is our old one
                self.assertTrue(Channel.objects.filter(channel_type='NX', org=self.org, address='MTN').first())

    def test_claim_plivo(self):
        self.login(self.admin)

        # remove any existing channels
        self.org.channels.update(is_active=False, org=None)

        connect_plivo_url = reverse('orgs.org_plivo_connect')
        claim_plivo_url = reverse('channels.channel_claim_plivo')

        # make sure plivo is on the claim page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, "Connect plivo")
        self.assertContains(response, reverse('orgs.org_plivo_connect'))

        with patch('requests.get') as plivo_get:
            plivo_get.return_value = MockResponse(400, json.dumps(dict()))

            # try hit the claim page, should be redirected; no credentials in session
            response = self.client.get(claim_plivo_url, follow=True)
            self.assertFalse('account_trial' in response.context)
            self.assertContains(response, connect_plivo_url)

        # let's add a number already connected to the account
        with patch('requests.get') as plivo_get:
            with patch('requests.post') as plivo_post:
                plivo_get.return_value = MockResponse(200,
                                                      json.dumps(dict(objects=[dict(number='16062681435',
                                                                                    region="California, UNITED STATES"),
                                                                               dict(number='8080',
                                                                                    region='GUADALAJARA, MEXICO')])))

                plivo_post.return_value = MockResponse(202, json.dumps(dict(status='changed', app_id='app-id')))

                # make sure our numbers appear on the claim page
                response = self.client.get(claim_plivo_url)
                self.assertContains(response, "+1 606-268-1435")
                self.assertContains(response, "8080")
                self.assertContains(response, 'US')
                self.assertContains(response, 'MX')

                # claim it the US number
                session = self.client.session
                session[Channel.CONFIG_PLIVO_AUTH_ID] = 'auth-id'
                session[Channel.CONFIG_PLIVO_AUTH_TOKEN] = 'auth-token'
                session.save()

                self.assertTrue(Channel.CONFIG_PLIVO_AUTH_ID in self.client.session)
                self.assertTrue(Channel.CONFIG_PLIVO_AUTH_TOKEN in self.client.session)

                response = self.client.post(claim_plivo_url, dict(phone_number='+1 606-268-1435', country='US'))
                self.assertRedirects(response, reverse('public.public_welcome') + "?success")

                # make sure it is actually connected
                channel = Channel.objects.get(channel_type='PL', org=self.org)
                self.assertEqual(channel.role, Channel.ROLE_SEND + Channel.ROLE_RECEIVE)
                self.assertEquals(channel.config_json(), {Channel.CONFIG_PLIVO_AUTH_ID: 'auth-id',
                                                          Channel.CONFIG_PLIVO_AUTH_TOKEN: 'auth-token',
                                                          Channel.CONFIG_PLIVO_APP_ID: 'app-id'})
                self.assertEquals(channel.address, "+16062681435")
                # no more credential in the session
                self.assertFalse(Channel.CONFIG_PLIVO_AUTH_ID in self.client.session)
                self.assertFalse(Channel.CONFIG_PLIVO_AUTH_TOKEN in self.client.session)

        # delete existing channels
        Channel.objects.all().delete()

        with patch('temba.channels.views.plivo.RestAPI.get_account') as mock_plivo_get_account:
            with patch('temba.channels.views.plivo.RestAPI.create_application') as mock_plivo_create_application:

                with patch('temba.channels.models.plivo.RestAPI.get_number') as mock_plivo_get_number:
                    with patch('temba.channels.models.plivo.RestAPI.buy_phone_number') as mock_plivo_buy_phone_number:
                        mock_plivo_get_account.return_value = (200, MockResponse(200, json.dumps(dict())))

                        mock_plivo_create_application.return_value = (200, dict(app_id='app-id'))

                        mock_plivo_get_number.return_value = (400, MockResponse(400, json.dumps(dict())))

                        response_body = json.dumps({
                            'status': 'fulfilled',
                            'message': 'created',
                            'numbers': [{'status': 'Success', 'number': '27816855210'}],
                            'api_id': '4334c747-9e83-11e5-9147-22000acb8094'
                        })
                        mock_plivo_buy_phone_number.return_value = (201, MockResponse(201, response_body))

                        # claim it the US number
                        session = self.client.session
                        session[Channel.CONFIG_PLIVO_AUTH_ID] = 'auth-id'
                        session[Channel.CONFIG_PLIVO_AUTH_TOKEN] = 'auth-token'
                        session.save()

                        self.assertTrue(Channel.CONFIG_PLIVO_AUTH_ID in self.client.session)
                        self.assertTrue(Channel.CONFIG_PLIVO_AUTH_TOKEN in self.client.session)

                        response = self.client.post(claim_plivo_url, dict(phone_number='+1 606-268-1440', country='US'))
                        self.assertRedirects(response, reverse('public.public_welcome') + "?success")

                        # make sure it is actually connected
                        channel = Channel.objects.get(channel_type='PL', org=self.org)
                        self.assertEquals(channel.config_json(), {
                            Channel.CONFIG_PLIVO_AUTH_ID: 'auth-id',
                            Channel.CONFIG_PLIVO_AUTH_TOKEN: 'auth-token',
                            Channel.CONFIG_PLIVO_APP_ID: 'app-id'
                        })
                        self.assertEquals(channel.address, "+16062681440")
                        # no more credential in the session
                        self.assertFalse(Channel.CONFIG_PLIVO_AUTH_ID in self.client.session)
                        self.assertFalse(Channel.CONFIG_PLIVO_AUTH_TOKEN in self.client.session)

    def test_claim_globe(self):
        # disassociate all of our channels
        self.org.channels.all().update(org=None, is_active=False)

        self.login(self.admin)
        claim_url = reverse('channels.channel_claim_globe')

        response = self.client.get(claim_url)
        self.assertEqual(200, response.status_code)

        response = self.client.post(claim_url, dict(number=21586380, app_id="AppId", app_secret="AppSecret", passphrase="Passphrase"), follow=True)
        self.assertEqual(200, response.status_code)

        channel = Channel.objects.get(channel_type=Channel.TYPE_GLOBE)
        self.assertEqual('21586380', channel.address)
        self.assertEqual('PH', channel.country)
        config = channel.config_json()
        self.assertEqual(config['app_secret'], 'AppSecret')
        self.assertEqual(config['app_id'], 'AppId')
        self.assertEqual(config['passphrase'], 'Passphrase')

    def test_claim_telegram(self):

        # disassociate all of our channels
        self.org.channels.all().update(org=None, is_active=False)

        self.login(self.admin)
        claim_url = reverse('channels.channel_claim_telegram')

        # can fetch the claim page
        response = self.client.get(claim_url)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, 'Telegram Bot')

        # claim with an invalid token
        with patch('telegram.Bot.getMe') as get_me:
            get_me.side_effect = telegram.TelegramError('Boom')
            response = self.client.post(claim_url, dict(auth_token='invalid'))
            self.assertEqual(200, response.status_code)
            self.assertEqual('Your authentication token is invalid, please check and try again', response.context['form'].errors['auth_token'][0])

        with patch('telegram.Bot.getMe') as get_me:
            user = TelegramUser(123, 'Rapid')
            user.last_name = 'Bot'
            user.username = 'rapidbot'
            get_me.return_value = user

            with patch('telegram.Bot.setWebhook') as set_webhook:
                set_webhook.return_value = ''

                response = self.client.post(claim_url, dict(auth_token='184875172:BAEKbsOKAL23CXufXG4ksNV7Dq7e_1qi3j8'))
                channel = Channel.objects.all().order_by('-pk').first()
                self.assertIsNotNone(channel)
                self.assertEqual(channel.channel_type, Channel.TYPE_TELEGRAM)
                self.assertRedirect(response, reverse('channels.channel_read', args=[channel.uuid]))
                self.assertEqual(302, response.status_code)

                response = self.client.post(claim_url, dict(auth_token='184875172:BAEKbsOKAL23CXufXG4ksNV7Dq7e_1qi3j8'))
                self.assertEqual('A telegram channel for this bot already exists on your account.', response.context['form'].errors['auth_token'][0])

                contact = self.create_contact('Telegram User', urn=URN.from_telegram('1234'))

                # make sure we our telegram channel satisfies as a send channel
                self.login(self.admin)
                response = self.client.get(reverse('contacts.contact_read', args=[contact.uuid]))
                send_channel = response.context['send_channel']
                self.assertIsNotNone(send_channel)
                self.assertEqual(Channel.TYPE_TELEGRAM, send_channel.channel_type)

    def test_claim_twitter(self):
        self.login(self.admin)

        self.twitter_channel.delete()  # remove existing twitter channel

        claim_url = reverse('channels.channel_claim_twitter')

        with patch('twython.Twython.get_authentication_tokens') as get_authentication_tokens:
            get_authentication_tokens.return_value = dict(oauth_token='abcde',
                                                          oauth_token_secret='12345',
                                                          auth_url='http://example.com/auth')
            response = self.client.get(claim_url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['twitter_auth_url'], 'http://example.com/auth')
            self.assertEqual(self.client.session['twitter_oauth_token'], 'abcde')
            self.assertEqual(self.client.session['twitter_oauth_token_secret'], '12345')

        with patch('temba.utils.mage.MageClient.activate_twitter_stream') as activate_twitter_stream:
            activate_twitter_stream.return_value = dict()

            with patch('twython.Twython.get_authorized_tokens') as get_authorized_tokens:
                get_authorized_tokens.return_value = dict(screen_name='billy_bob',
                                                          user_id=123,
                                                          oauth_token='bcdef',
                                                          oauth_token_secret='23456')

                response = self.client.get(claim_url, {'oauth_verifier': 'vwxyz'}, follow=True)
                self.assertNotIn('twitter_oauth_token', self.client.session)
                self.assertNotIn('twitter_oauth_token_secret', self.client.session)
                self.assertEqual(response.status_code, 200)

                channel = response.context['object']
                self.assertEqual(channel.address, 'billy_bob')
                self.assertEqual(channel.name, '@billy_bob')
                config = json.loads(channel.config)
                self.assertEqual(config['handle_id'], 123)
                self.assertEqual(config['oauth_token'], 'bcdef')
                self.assertEqual(config['oauth_token_secret'], '23456')

            # re-add same account but with different auth credentials
            s = self.client.session
            s['twitter_oauth_token'] = 'cdefg'
            s['twitter_oauth_token_secret'] = '34567'
            s.save()

            with patch('twython.Twython.get_authorized_tokens') as get_authorized_tokens:
                get_authorized_tokens.return_value = dict(screen_name='billy_bob',
                                                          user_id=123,
                                                          oauth_token='defgh',
                                                          oauth_token_secret='45678')

                response = self.client.get(claim_url, {'oauth_verifier': 'uvwxy'}, follow=True)
                self.assertEqual(response.status_code, 200)

                channel = response.context['object']
                self.assertEqual(channel.address, 'billy_bob')
                config = json.loads(channel.config)
                self.assertEqual(config['handle_id'], 123)
                self.assertEqual(config['oauth_token'], 'defgh')
                self.assertEqual(config['oauth_token_secret'], '45678')

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
        with patch('temba.nexmo.NexmoClient.update_account') as connect:
            connect.return_value = True
            self.org.connect_nexmo('123', '456', self.admin)
            self.org.save()

        claim_nexmo_url = reverse('channels.channel_create_bulk_sender') + "?connection=NX&channel=%d" % android.pk
        self.client.post(claim_nexmo_url, dict(connection='NX', channel=android.pk))
        nexmo = Channel.objects.get(channel_type='NX')

        android.release()

        # check that some details are cleared and channel is now in active
        self.assertIsNone(android.org)
        self.assertIsNone(android.gcm_id)
        self.assertIsNone(android.secret)
        self.assertFalse(android.is_active)

        # Nexmo delegate should have been released as well
        nexmo.refresh_from_db()
        self.assertIsNone(nexmo.org)
        self.assertFalse(nexmo.is_active)

    def test_unclaimed(self):
        response = self.sync(self.released_channel)
        self.assertEquals(200, response.status_code)
        response = json.loads(response.content)

        # should be a registration command containing a new claim code
        self.assertEquals(response['cmds'][0]['cmd'], 'reg')

        post_data = dict(cmds=[dict(cmd="status",
                                    org_id=self.released_channel.pk,
                                    p_lvl=84,
                                    net="WIFI",
                                    p_sts="CHA",
                                    p_src="USB",
                                    pending=[],
                                    retry=[])])

        # try syncing against the released channel that has a secret
        self.released_channel.secret = "999"
        self.released_channel.save()

        response = self.sync(self.released_channel, post_data=post_data)
        response = json.loads(response.content)

        # registration command
        self.assertEquals(response['cmds'][0]['cmd'], 'reg')

        # claim the channel on the site
        self.released_channel.org = self.org
        self.released_channel.save()

        post_data = dict(cmds=[dict(cmd="status",
                                    org_id="-1",
                                    p_lvl=84,
                                    net="WIFI",
                                    p_sts="STATUS_CHARGING",
                                    p_src="USB",
                                    pending=[],
                                    retry=[])])

        response = self.sync(self.released_channel, post_data=post_data)
        response = json.loads(response.content)

        # should now be a claim command in return
        self.assertEquals(response['cmds'][0]['cmd'], 'claim')

        # now try releasing the channel from the client
        post_data = dict(cmds=[dict(cmd="reset", p_id=1)])

        response = self.sync(self.released_channel, post_data=post_data)
        response = json.loads(response.content)

        # channel should be released now
        channel = Channel.objects.get(pk=self.released_channel.pk)
        self.assertFalse(channel.org)
        self.assertFalse(channel.is_active)

    def test_quota_exceeded(self):
        # set our org to be on the trial plan
        self.org.plan = FREE_PLAN
        self.org.save()
        self.org.topups.all().update(credits=10)

        self.assertEquals(10, self.org.get_credits_remaining())
        self.assertEquals(0, self.org.get_credits_used())

        # if we sync should get one message back
        self.send_message(['250788382382'], "How is it going?")

        response = self.sync(self.tel_channel)
        self.assertEquals(200, response.status_code)
        response = json.loads(response.content)
        self.assertEqual(1, len(response['cmds']))

        self.assertEquals(9, self.org.get_credits_remaining())
        self.assertEquals(1, self.org.get_credits_used())

        # let's create 10 other messages, this will put our last message above our quota
        for i in range(10):
            self.send_message(['250788382%03d' % i], "This is message # %d" % i)

        # should get the 10 messages we are allotted back, not the 11 that exist
        response = self.sync(self.tel_channel)
        self.assertEquals(200, response.status_code)
        response = json.loads(response.content)
        self.assertEqual(10, len(response['cmds']))

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

        self.org.administrators.add(self.user)
        self.user.set_org(self.org)

        # Check our sync point has all three messages queued for delivery
        response = self.sync(self.tel_channel)
        self.assertEquals(200, response.status_code)
        response = json.loads(response.content)
        cmds = response['cmds']
        self.assertEqual(4, len(cmds))

        # assert that our first command is the two message broadcast
        cmd = cmds[0]
        self.assertEquals("How is it going?", cmd['msg'])
        self.assertTrue('+250788382382' in [m['phone'] for m in cmd['to']])
        self.assertTrue('+250788383383' in [m['phone'] for m in cmd['to']])

        self.assertTrue(msg1.pk in [m['id'] for m in cmd['to']])
        self.assertTrue(msg2.pk in [m['id'] for m in cmd['to']])

        # add another message we'll pretend is in retry to see that we exclude them from sync
        msg6 = self.send_message(['250788382382'], "Pretend this message is in retry on the client, don't send it on sync")

        # a pending outgoing message should be included
        Msg.create_outgoing(self.org, self.admin, msg6.contact, "Hello, we heard from you.")

        post_data = dict(cmds=[

            # device gcm data
            dict(cmd='gcm', gcm_id='12345', uuid='abcde'),

            # device details status
            dict(cmd="status", p_sts="DIS", p_src="BAT", p_lvl="60",
                 net="UMTS", org_id=8, retry=[msg6.pk], pending=[]),

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

        # new batch, our ack and our claim command for new org
        self.assertEquals(4, len(json.loads(response.content)['cmds']))
        self.assertContains(response, "Hello, we heard from you.")
        self.assertContains(response, "mt_bcast")

        # check that our messages were updated accordingly
        self.assertEqual(2, Msg.all_messages.filter(channel=self.tel_channel, status='S', direction='O').count())
        self.assertEqual(1, Msg.all_messages.filter(channel=self.tel_channel, status='D', direction='O').count())
        self.assertEqual(1, Msg.all_messages.filter(channel=self.tel_channel, status='E', direction='O').count())
        self.assertEqual(1, Msg.all_messages.filter(channel=self.tel_channel, status='F', direction='O').count())

        # we should now have two incoming messages
        self.assertEqual(2, Msg.all_messages.filter(direction='I').count())

        # one of them should have an empty 'tel'
        self.assertTrue(Msg.all_messages.filter(direction='I', contact_urn__path='empty'))

        # We should now have one sync
        self.assertEquals(1, SyncEvent.objects.filter(channel=self.tel_channel).count())

        # check our channel gcm and uuid were updated
        self.tel_channel = Channel.objects.get(pk=self.tel_channel.pk)
        self.assertEquals('12345', self.tel_channel.gcm_id)
        self.assertEquals('abcde', self.tel_channel.uuid)

        # should ignore incoming messages without text
        post_data = dict(cmds=[
            # incoming msg without text
            dict(cmd="mo_sms", phone="+250788383383", p_id="1", ts=date),

        ])

        msgs_count = Msg.all_messages.all().count()
        response = self.sync(self.tel_channel, post_data)

        # no new message
        self.assertEqual(Msg.all_messages.all().count(), msgs_count)

        # set an email on our channel
        self.tel_channel.alert_email = 'fred@worldrelif.org'
        self.tel_channel.save()

        # We should not have an alert this time
        self.assertEquals(0, Alert.objects.all().count())

        # the case the status must be be reported
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="DIS", p_src="BAT", p_lvl="20", net="UMTS", retry=[], pending=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # we should now have an Alert
        self.assertEquals(1, Alert.objects.all().count())

        # and at this time it must be not ended
        self.assertEquals(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # the case the status must be be reported but already notification sent
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="DIS", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # we should not create a new alert
        self.assertEquals(1, Alert.objects.all().count())

        # still not ended
        self.assertEquals(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # Let plug the channel to charger
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # only one alert
        self.assertEquals(1, Alert.objects.all().count())

        # and we end all alert related to this issue
        self.assertEquals(0, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

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
        self.assertEquals(1, Alert.objects.all().count())

        # one alert not ended
        self.assertEquals(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # Let plug the channel to charger to end this unknown power status
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # still only one alert
        self.assertEquals(1, Alert.objects.all().count())

        # and we end all alert related to this issue
        self.assertEquals(0, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

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
        self.assertEquals(1, Alert.objects.all().count())

        # one alert not ended
        self.assertEquals(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # Let plug the channel to charger to end this unknown power status
        post_data = dict(cmds=[
            # device details status
            dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])
        ])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # first we have a new alert created
        self.assertEquals(1, Alert.objects.all().count())

        # and we end all alert related to this issue
        self.assertEquals(0, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

    def test_signing(self):
        # good signature
        self.assertEquals(200, self.sync(self.tel_channel).status_code)

        # bad signature, should result in 401 Unauthorized
        self.assertEquals(401, self.sync(self.tel_channel, signature="badsig").status_code)

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
        self.assertEquals(200, response.status_code)

        responses = json.loads(response.content)
        cmds = responses['cmds']

        # check the server gave us responses for our messages
        r0 = self.get_response(cmds, '1')
        r1 = self.get_response(cmds, '2')
        r2 = self.get_response(cmds, '3')

        self.assertIsNotNone(r0)
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)

        # first two should have the same server id
        self.assertEquals(r0['extra'], r1['extra'])

        # One was a duplicate, should only have 2
        self.assertEqual(2, Msg.all_messages.filter(direction='I').count())

    def get_response(self, responses, p_id):
        for response in responses:
            if 'p_id' in response and response['p_id'] == p_id:
                return response


class ChannelBatchTest(TembaTest):

    def test_time_utils(self):
        from temba.utils import datetime_to_ms, ms_to_datetime
        now = timezone.now()
        now = now.replace(microsecond=now.microsecond / 1000 * 1000)

        epoch = datetime_to_ms(now)
        self.assertEquals(ms_to_datetime(epoch), now)


class ChannelEventTest(TembaTest):

    def test_create(self):
        now = timezone.now()
        event = ChannelEvent.create(self.channel, "tel:+250783535665", ChannelEvent.TYPE_CALL_OUT, now, 300)

        contact = Contact.objects.get()
        self.assertEqual(contact.get_urn().urn, "tel:+250783535665")

        self.assertEqual(event.org, self.org)
        self.assertEqual(event.channel, self.channel)
        self.assertEqual(event.contact, contact)
        self.assertEqual(event.event_type, ChannelEvent.TYPE_CALL_OUT)
        self.assertEqual(event.time, now)
        self.assertEqual(event.duration, 300)


class ChannelEventCRUDLTest(TembaTest):

    def test_calls(self):
        now = timezone.now()
        ChannelEvent.create(self.channel, "tel:12345", ChannelEvent.TYPE_CALL_IN, now, 600)
        ChannelEvent.create(self.channel, "tel:890", ChannelEvent.TYPE_CALL_IN_MISSED, now, 0)
        ChannelEvent.create(self.channel, "tel:456767", ChannelEvent.TYPE_UNKNOWN, now, 0)

        list_url = reverse('channels.channelevent_calls')

        response = self.fetch_protected(list_url, self.user)

        self.assertEquals(response.context['object_list'].count(), 2)
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
        self.assertEquals(SyncEvent.objects.all().count(), 1)
        self.assertEquals(self.sync_event.get_pending_messages(), [1, 2])
        self.assertEquals(self.sync_event.get_retry_messages(), [3, 4])
        self.assertEquals(self.sync_event.incoming_command_count, 0)

        self.sync_event = SyncEvent.create(self.tel_channel, dict(p_src="AC", p_sts="DIS", p_lvl=80, net="WIFI",
                                                                  pending=[1, 2], retry=[3, 4], cc='US'), [1])
        self.assertEquals(self.sync_event.incoming_command_count, 0)
        self.tel_channel = Channel.objects.get(pk=self.tel_channel.pk)

        # we shouldn't update country once the relayer is claimed
        self.assertEquals('RW', self.tel_channel.country)


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

    def test_external(self):
        Channel.objects.all().delete()

        self.login(self.admin)

        # should see the general channel claim page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, reverse('channels.channel_claim_external'))

        # try to claim a channel
        response = self.client.get(reverse('channels.channel_claim_external'))
        post_data = response.context['form'].initial

        url = 'http://test.com/send.php?from={{from}}&text={{text}}&to={{to}}'

        post_data['number'] = '12345'
        post_data['country'] = 'RW'
        post_data['url'] = url
        post_data['method'] = 'GET'
        post_data['scheme'] = 'tel'

        response = self.client.post(reverse('channels.channel_claim_external'), post_data)

        channel = Channel.objects.get()

        self.assertEquals('RW', channel.country)
        self.assertTrue(channel.uuid)
        self.assertEquals(post_data['number'], channel.address)
        self.assertEquals(post_data['url'], channel.config_json()['send_url'])
        self.assertEquals(post_data['method'], channel.config_json()['method'])
        self.assertEquals(Channel.TYPE_EXTERNAL, channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('handlers.external_handler', args=['sent', channel.uuid]))
        self.assertContains(response, reverse('handlers.external_handler', args=['delivered', channel.uuid]))
        self.assertContains(response, reverse('handlers.external_handler', args=['failed', channel.uuid]))
        self.assertContains(response, reverse('handlers.external_handler', args=['received', channel.uuid]))

        # test substitution in our url
        self.assertEqual('http://test.com/send.php?from=5080&text=test&to=%2B250788383383',
                         channel.build_send_url(url, {'from': "5080", 'text': "test", 'to': "+250788383383"}))

        # test substitution with unicode
        self.assertEqual('http://test.com/send.php?from=5080&text=Reply+%E2%80%9C1%E2%80%9D+for+good&to=%2B250788383383',
                         channel.build_send_url(url, {
                             'from': "5080",
                             'text': "Reply “1” for good",
                             'to': "+250788383383"
                         }))

    def test_clickatell(self):
        Channel.objects.all().delete()

        self.login(self.admin)

        # should see the general channel claim page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, reverse('channels.channel_claim_clickatell'))

        # try to claim a channel
        response = self.client.get(reverse('channels.channel_claim_clickatell'))
        post_data = response.context['form'].initial

        post_data['api_id'] = '12345'
        post_data['username'] = 'uname'
        post_data['password'] = 'pword'
        post_data['country'] = 'US'
        post_data['number'] = '(206) 555-1212'

        response = self.client.post(reverse('channels.channel_claim_clickatell'), post_data)

        channel = Channel.objects.get()

        self.assertEquals('US', channel.country)
        self.assertTrue(channel.uuid)
        self.assertEquals('+12065551212', channel.address)
        self.assertEquals(post_data['api_id'], channel.config_json()['api_id'])
        self.assertEquals(post_data['username'], channel.config_json()['username'])
        self.assertEquals(post_data['password'], channel.config_json()['password'])
        self.assertEquals(Channel.TYPE_CLICKATELL, channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('handlers.clickatell_handler', args=['status', channel.uuid]))
        self.assertContains(response, reverse('handlers.clickatell_handler', args=['receive', channel.uuid]))

    def test_high_connection(self):
        Channel.objects.all().delete()

        self.login(self.admin)

        # try to claim a channel
        response = self.client.get(reverse('channels.channel_claim_high_connection'))
        post_data = response.context['form'].initial

        post_data['username'] = 'uname'
        post_data['password'] = 'pword'
        post_data['number'] = '5151'
        post_data['country'] = 'FR'

        response = self.client.post(reverse('channels.channel_claim_high_connection'), post_data)

        channel = Channel.objects.get()

        self.assertEquals('FR', channel.country)
        self.assertTrue(channel.uuid)
        self.assertEquals(post_data['number'], channel.address)
        self.assertEquals(post_data['username'], channel.config_json()['username'])
        self.assertEquals(post_data['password'], channel.config_json()['password'])
        self.assertEquals(Channel.TYPE_HIGH_CONNECTION, channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('handlers.hcnx_handler', args=['receive', channel.uuid]))

    def test_shaqodoon(self):
        Channel.objects.all().delete()

        self.login(self.admin)

        # try to claim a channel
        response = self.client.get(reverse('channels.channel_claim_shaqodoon'))
        post_data = response.context['form'].initial

        post_data['username'] = 'uname'
        post_data['password'] = 'pword'
        post_data['url'] = 'http://test.com/send.php'
        post_data['key'] = 'secret_key'
        post_data['number'] = '301'

        response = self.client.post(reverse('channels.channel_claim_shaqodoon'), post_data)

        channel = Channel.objects.get()

        self.assertEquals('SO', channel.country)
        self.assertTrue(channel.uuid)
        self.assertEquals(post_data['number'], channel.address)
        self.assertEquals(post_data['url'], channel.config_json()['send_url'])
        self.assertEquals(post_data['username'], channel.config_json()['username'])
        self.assertEquals(post_data['password'], channel.config_json()['password'])
        self.assertEquals(post_data['key'], channel.config_json()['key'])
        self.assertEquals(Channel.TYPE_SHAQODOON, channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('handlers.shaqodoon_handler', args=['received', channel.uuid]))

    def test_kannel(self):
        Channel.objects.all().delete()

        self.login(self.admin)

        # should see the general channel claim page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, reverse('channels.channel_claim_kannel'))

        # try to claim a channel
        response = self.client.get(reverse('channels.channel_claim_kannel'))
        post_data = response.context['form'].initial

        post_data['number'] = '3071'
        post_data['country'] = 'RW'
        post_data['url'] = 'http://kannel.temba.com/cgi-bin/sendsms'
        post_data['verify_ssl'] = False
        post_data['encoding'] = Channel.ENCODING_SMART

        response = self.client.post(reverse('channels.channel_claim_kannel'), post_data)

        channel = Channel.objects.get()

        self.assertEquals('RW', channel.country)
        self.assertTrue(channel.uuid)
        self.assertEquals(post_data['number'], channel.address)
        self.assertEquals(post_data['url'], channel.config_json()['send_url'])
        self.assertEquals(False, channel.config_json()['verify_ssl'])
        self.assertEquals(Channel.ENCODING_SMART, channel.config_json()[Channel.CONFIG_ENCODING])

        # make sure we generated a username and password
        self.assertTrue(channel.config_json()['username'])
        self.assertTrue(channel.config_json()['password'])
        self.assertEquals(Channel.TYPE_KANNEL, channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        # our configuration page should list our receive URL
        self.assertContains(response, reverse('handlers.kannel_handler', args=['receive', channel.uuid]))

    def test_zenvia(self):
        Channel.objects.all().delete()

        self.login(self.admin)

        # shouldn't be able to see the claim zenvia page if we aren't part of that group
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertNotContains(response, "Zenvia")

        # but if we are in the proper time zone
        self.org.timezone = 'America/Sao_Paulo'
        self.org.save()

        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, "Zenvia")

        # try to claim a channel
        response = self.client.get(reverse('channels.channel_claim_zenvia'))
        post_data = response.context['form'].initial

        post_data['account'] = 'rapidpro.gw'
        post_data['code'] = 'h7GpAIEp85'
        post_data['shortcode'] = '28595'

        response = self.client.post(reverse('channels.channel_claim_zenvia'), post_data)

        channel = Channel.objects.get()

        self.assertEquals('BR', channel.country)
        self.assertEquals(post_data['account'], channel.config_json()['account'])
        self.assertEquals(post_data['code'], channel.config_json()['code'])
        self.assertEquals(post_data['shortcode'], channel.address)
        self.assertEquals('ZV', channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('handlers.zenvia_handler', args=['status', channel.uuid]))
        self.assertContains(response, reverse('handlers.zenvia_handler', args=['receive', channel.uuid]))

    def test_claim_africa(self):
        Channel.objects.all().delete()
        self.login(self.admin)

        # visit the africa's talking page
        response = self.client.get(reverse('channels.channel_claim_africas_talking'))
        self.assertEquals(200, response.status_code)
        post_data = response.context['form'].initial

        post_data['shortcode'] = '5259'
        post_data['username'] = 'temba'
        post_data['api_key'] = 'asdf-asdf-asdf-asdf-asdf'
        post_data['country'] = 'KE'

        response = self.client.post(reverse('channels.channel_claim_africas_talking'), post_data)

        channel = Channel.objects.get()

        self.assertEquals('temba', channel.config_json()['username'])
        self.assertEquals('asdf-asdf-asdf-asdf-asdf', channel.config_json()['api_key'])
        self.assertEquals('5259', channel.address)
        self.assertEquals('KE', channel.country)
        self.assertEquals('AT', channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('handlers.africas_talking_handler', args=['callback', channel.uuid]))
        self.assertContains(response, reverse('handlers.africas_talking_handler', args=['delivery', channel.uuid]))

    def test_claim_viber(self):
        Channel.objects.all().delete()
        self.login(self.admin)

        response = self.client.get(reverse('channels.channel_create_viber'))
        self.assertEquals(200, response.status_code)
        response = self.client.post(reverse('channels.channel_create_viber'), dict(name="Macklemore"))

        # should create a new viber channel, but without an address
        channel = Channel.objects.get()

        self.assertEqual(channel.address, Channel.VIBER_NO_SERVICE_ID)
        self.assertIsNone(channel.country.code)
        self.assertEqual(channel.name, "Macklemore")
        self.assertEquals(Channel.TYPE_VIBER, channel.channel_type)

        # we should be redirecting to the claim page to enter in our service id
        claim_url = reverse('channels.channel_claim_viber', args=[channel.id])
        self.assertRedirect(response, claim_url)

        response = self.client.get(claim_url)

        self.assertContains(response, reverse('handlers.viber_handler', args=['status', channel.uuid]))
        self.assertContains(response, reverse('handlers.viber_handler', args=['receive', channel.uuid]))

        # going to our account home should link to our claim page
        response = self.client.get(reverse('orgs.org_home'))
        self.assertContains(response, claim_url)

        # ok, enter our service id
        response = self.client.post(claim_url, dict(service_id=1001))

        # refetch our channel
        channel.refresh_from_db()

        # should now have an address
        self.assertEqual(channel.address, '1001')

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)

        self.assertContains(response, reverse('handlers.viber_handler', args=['status', channel.uuid]))
        self.assertContains(response, reverse('handlers.viber_handler', args=['receive', channel.uuid]))

        # once claimed, account page should go to read page
        response = self.client.get(reverse('orgs.org_home'))
        self.assertContains(response, reverse('channels.channel_read', args=[channel.uuid]))

    def test_claim_chikka(self):
        Channel.objects.all().delete()
        self.login(self.admin)

        response = self.client.get(reverse('channels.channel_claim_chikka'))
        self.assertEquals(200, response.status_code)
        post_data = response.context['form'].initial

        post_data['number'] = '5259'
        post_data['username'] = 'chikka'
        post_data['password'] = 'password'

        response = self.client.post(reverse('channels.channel_claim_chikka'), post_data)

        channel = Channel.objects.get()

        self.assertEquals('chikka', channel.config_json()[Channel.CONFIG_USERNAME])
        self.assertEquals('password', channel.config_json()[Channel.CONFIG_PASSWORD])
        self.assertEquals('5259', channel.address)
        self.assertEquals('PH', channel.country)
        self.assertEquals(Channel.TYPE_CHIKKA, channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('handlers.chikka_handler', args=[channel.uuid]))

    def test_claim_vumi_ussd(self):
        Channel.objects.all().delete()
        self.login(self.admin)

        response = self.client.get(reverse('channels.channel_claim_vumi_ussd'))
        self.assertEquals(200, response.status_code)

        post_data = {
            "country": "ZA",
            "number": "+273454325324",
            "account_key": "account1",
            "conversation_key": "conversation1",
            "transport_name": ""
        }

        response = self.client.post(reverse('channels.channel_claim_vumi_ussd'), post_data)

        channel = Channel.objects.get()

        self.assertTrue(uuid.UUID(channel.config_json()['access_token'], version=4))
        self.assertEquals(channel.country, post_data['country'])
        self.assertEquals(channel.address, post_data['number'])
        self.assertEquals(channel.config_json()['account_key'], post_data['account_key'])
        self.assertEquals(channel.config_json()['conversation_key'], post_data['conversation_key'])
        self.assertEquals(channel.channel_type, Channel.TYPE_VUMI_USSD)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('handlers.vumi_handler', args=['receive', channel.uuid]))
        self.assertContains(response, reverse('handlers.vumi_handler', args=['event', channel.uuid]))

    @override_settings(SEND_EMAILS=True)
    def test_disconnected_alert(self):
        # set our last seen to a while ago
        self.channel.alert_email = 'fred@unicef.org'
        self.channel.last_seen = timezone.now() - timedelta(minutes=40)
        self.channel.save()

        check_channels_task()

        # should have created one alert
        alert = Alert.objects.get()
        self.assertEquals(self.channel, alert.channel)
        self.assertEquals(Alert.TYPE_DISCONNECTED, alert.alert_type)
        self.assertFalse(alert.ended_on)

        self.assertTrue(len(mail.outbox) == 1)
        template = 'channels/email/disconnected_alert.txt'
        context = dict(org=self.channel.org, channel=self.channel, now=timezone.now(),
                       branding=self.channel.org.get_branding(),
                       last_seen=self.channel.last_seen, sync=alert.sync_event)

        text_template = loader.get_template(template)
        text = text_template.render(Context(context))

        self.assertEquals(mail.outbox[0].body, text)

        # call it again
        check_channels_task()

        # still only one alert
        self.assertEquals(1, Alert.objects.all().count())
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
        text = text_template.render(Context(context))

        self.assertEquals(mail.outbox[1].body, text)

    def test_m3tech(self):
        Channel.objects.all().delete()

        self.login(self.admin)

        # try to claim a channel
        response = self.client.get(reverse('channels.channel_claim_m3tech'))
        post_data = response.context['form'].initial

        post_data['country'] = 'PK'
        post_data['number'] = '250788123123'
        post_data['username'] = 'user1'
        post_data['password'] = 'pass1'

        response = self.client.post(reverse('channels.channel_claim_m3tech'), post_data)

        channel = Channel.objects.get()

        self.assertEquals('PK', channel.country)
        self.assertEquals(post_data['username'], channel.config_json()['username'])
        self.assertEquals(post_data['password'], channel.config_json()['password'])
        self.assertEquals('+250788123123', channel.address)
        self.assertEquals(Channel.TYPE_M3TECH, channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('handlers.m3tech_handler', args=['received', channel.uuid]))
        self.assertContains(response, reverse('handlers.m3tech_handler', args=['sent', channel.uuid]))
        self.assertContains(response, reverse('handlers.m3tech_handler', args=['failed', channel.uuid]))
        self.assertContains(response, reverse('handlers.m3tech_handler', args=['delivered', channel.uuid]))

    def test_infobip(self):
        Channel.objects.all().delete()

        self.login(self.admin)

        # try to claim a channel
        response = self.client.get(reverse('channels.channel_claim_infobip'))
        post_data = response.context['form'].initial

        post_data['country'] = 'NI'
        post_data['number'] = '250788123123'
        post_data['username'] = 'user1'
        post_data['password'] = 'pass1'

        response = self.client.post(reverse('channels.channel_claim_infobip'), post_data)

        channel = Channel.objects.get()

        self.assertEquals('NI', channel.country)
        self.assertEquals(post_data['username'], channel.config_json()['username'])
        self.assertEquals(post_data['password'], channel.config_json()['password'])
        self.assertEquals('+250788123123', channel.address)
        self.assertEquals('IB', channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('handlers.infobip_handler', args=['received', channel.uuid]))
        self.assertContains(response, reverse('handlers.infobip_handler', args=['delivered', channel.uuid]))

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
        self.assertEquals(Alert.objects.all().count(), 1)

        alert = Alert.objects.get()
        self.assertEquals(self.channel, alert.channel)
        self.assertEquals(Alert.TYPE_SMS, alert.alert_type)
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
        self.assertEquals(Alert.objects.all().count(), 1)

        # consider the sent message was sent before our queued msg
        sent_msg.sent_on = three_hours_ago
        sent_msg.save()

        msg1.delete()
        msg1 = self.create_msg(text="Message One", contact=contact, created_on=two_hours_ago, status='Q')

        # check our channel again
        check_channels_task()

        #  no new alert created because we sent one in the past hour
        self.assertEquals(Alert.objects.all().count(), 1)

        sent_msg.sent_on = six_hours_ago
        sent_msg.save()

        alert = Alert.objects.all()[0]
        alert.created_on = six_hours_ago
        alert.save()

        # check our channel again
        check_channels_task()

        # this time we have a new alert and should create only one
        self.assertEquals(Alert.objects.all().count(), 2)

        # get the alert which is not ended
        alert = Alert.objects.get(ended_on=None)
        self.assertEquals(self.channel, alert.channel)
        self.assertEquals(Alert.TYPE_SMS, alert.alert_type)
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


class CountTest(TembaTest):

    def assertDailyCount(self, channel, assert_count, count_type, day):
        calculated_count = ChannelCount.get_day_count(channel, count_type, day)
        self.assertEquals(assert_count, calculated_count)

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
        self.assertEquals(ChannelCount.objects.all().count(), 1)

        # delete it, back to 1
        msg.delete()
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        ChannelCount.objects.all().delete()

        # ok, test outgoing now
        real_contact = Contact.get_or_create(self.org, self.admin, urns=['tel:+250788111222'])
        msg = Msg.create_outgoing(self.org, self.admin, real_contact, "Real Message", channel=self.channel)
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_MSG_TYPE, msg.created_on.date())

        # delete it, should be gone now
        msg.delete()
        self.assertDailyCount(self.channel, 0, ChannelCount.OUTGOING_MSG_TYPE, msg.created_on.date())

        ChannelCount.objects.all().delete()

        # incoming IVR
        msg = Msg.create_incoming(self.channel, 'tel:+250788111222',
                                  "Test Message", org=self.org, msg_type=IVR)
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_IVR_TYPE, msg.created_on.date())

        # delete it, should be gone now
        msg.delete()
        self.assertDailyCount(self.channel, 0, ChannelCount.INCOMING_IVR_TYPE, msg.created_on.date())

        ChannelCount.objects.all().delete()

        # outgoing ivr
        msg = Msg.create_outgoing(self.org, self.admin, real_contact, "Real Voice",
                                  channel=self.channel, msg_type=IVR)
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_IVR_TYPE, msg.created_on.date())

        # delete it, should be gone now
        msg.delete()
        self.assertDailyCount(self.channel, 0, ChannelCount.OUTGOING_IVR_TYPE, msg.created_on.date())


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

        self.assertEquals(404, response.status_code)

        # ok, try with a valid uuid, but invalid message id
        delivery_url = reverse('handlers.africas_talking_handler', args=['delivery', self.channel.uuid])
        response = self.client.post(delivery_url, post_data)

        self.assertEquals(404, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        msg.external_id = "external1"
        msg.save(update_fields=('external_id',))

        def assertStatus(sms, post_status, assert_status):
            post_data['status'] = post_status
            response = self.client.post(delivery_url, post_data)
            self.assertEquals(200, response.status_code)
            sms = Msg.all_messages.get(pk=sms.id)
            self.assertEquals(assert_status, sms.status)

        assertStatus(msg, 'Success', DELIVERED)
        assertStatus(msg, 'Sent', SENT)
        assertStatus(msg, 'Buffered', SENT)
        assertStatus(msg, 'Failed', FAILED)
        assertStatus(msg, 'Rejected', FAILED)

    def test_callback(self):
        post_data = {'from': "0788123123", 'text': "Hello World"}
        callback_url = reverse('handlers.africas_talking_handler', args=['callback', self.channel.uuid])

        response = self.client.post(callback_url, post_data)

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals("+254788123123", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World", msg.text)

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, json.dumps(dict(SMSMessageData=dict(Recipients=[dict(messageId='msg1')]))))

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(SENT, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals('msg1', msg.external_id)

                # check that our from was set
                self.assertEquals(self.channel.address, mock.call_args[1]['data']['from'])

                self.clear_cache()

            # test with a non-dedicated shortcode
            self.channel.config = json.dumps(dict(username='at-user', api_key='africa-key', is_shared=True))
            self.channel.save()

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, json.dumps(dict(SMSMessageData=dict(Recipients=[dict(messageId='msg1')]))))

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # assert we didn't send the short code in our data
                self.assertTrue('from' not in mock.call_args[1]['data'])
                self.clear_cache()

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class ExternalTest(TembaTest):

    def setUp(self):
        super(ExternalTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'BR', 'EX', None, '+250788123123', scheme='tel',
                                      config={Channel.CONFIG_SEND_URL: 'http://foo.com/send', Channel.CONFIG_SEND_METHOD: 'POST'},
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_status(self):
        # ok, what happens with an invalid uuid?
        data = dict(id="-1")
        response = self.client.post(reverse('handlers.external_handler', args=['sent', 'not-real-uuid']), data)

        self.assertEquals(400, response.status_code)

        # ok, try with a valid uuid, but invalid message id -1
        delivery_url = reverse('handlers.external_handler', args=['sent', self.channel.uuid])
        response = self.client.post(delivery_url, data)

        self.assertEquals(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)

        data['id'] = msg.pk

        def assertStatus(sms, status, assert_status):
            response = self.client.post(reverse('handlers.external_handler', args=[status, self.channel.uuid]), data)
            self.assertEquals(200, response.status_code)
            sms = Msg.all_messages.get(pk=sms.id)
            self.assertEquals(assert_status, sms.status)

        assertStatus(msg, 'delivered', DELIVERED)
        assertStatus(msg, 'sent', SENT)
        assertStatus(msg, 'failed', FAILED)

        # check when called with phone number rather than UUID
        response = self.client.post(reverse('handlers.external_handler', args=['sent', '250788123123']), {'id': msg.pk})
        self.assertEquals(200, response.status_code)
        msg.refresh_from_db()
        self.assertEqual(msg.status, SENT)

    def test_receive(self):
        data = {'from': '5511996458779', 'text': 'Hello World!'}
        callback_url = reverse('handlers.external_handler', args=['received', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals("+5511996458779", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World!", msg.text)

        data = {'from': "", 'text': "Hi there"}
        response = self.client.post(callback_url, data)

        self.assertEquals(400, response.status_code)

        Msg.all_messages.all().delete()

        # receive with a date
        data = {'from': '5511996458779', 'text': 'Hello World!', 'date': '2012-04-23T18:25:43.511Z'}
        callback_url = reverse('handlers.external_handler', args=['received', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEquals(200, response.status_code)

        # load our message, make sure the date was saved properly
        msg = Msg.all_messages.get()
        self.assertEquals(2012, msg.created_on.year)
        self.assertEquals(18, msg.created_on.hour)

    def test_receive_external(self):
        self.channel.scheme = 'ext'
        self.channel.save()

        data = {'from': 'lynch24', 'text': 'Beast Mode!'}
        callback_url = reverse('handlers.external_handler', args=['received', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEquals(200, response.status_code)

        # check our message
        msg = Msg.all_messages.get()
        self.assertEquals('lynch24', msg.contact.get_urn(EXTERNAL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals('Beast Mode!', msg.text)

    def test_send_replacement(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        self.channel.config = json.dumps({Channel.CONFIG_SEND_URL: 'http://foo.com/send&text={{text}}&to={{to_no_plus}}',
                                          Channel.CONFIG_SEND_METHOD: 'GET'})
        self.channel.save()

        with self.settings(SEND_MESSAGES=True):
            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "Sent")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                self.assertEqual(mock.call_args[0][0], 'http://foo.com/send&text=Test+message&to=250788383383')

        self.channel.config = json.dumps({Channel.CONFIG_SEND_URL: 'http://foo.com/send',
                                          Channel.CONFIG_SEND_METHOD: 'POST'})
        self.channel.save()
        self.clear_cache()

        with self.settings(SEND_MESSAGES=True):
            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, "Sent")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                self.assertEqual(mock.call_args[0][0], 'http://foo.com/send')
                self.assertEqual(mock.call_args[1]['data'], 'id=%d&text=Test+message&to=%%2B250788383383&to_no_plus=250788383383&'
                                                            'from=%%2B250788123123&from_no_plus=250788123123&'
                                                            'channel=%d' % (msg.id, self.channel.id))

        self.channel.config = json.dumps({Channel.CONFIG_SEND_URL: 'http://foo.com/send',
                                          Channel.CONFIG_SEND_BODY: 'text={{text}}&to={{to_no_plus}}',
                                          Channel.CONFIG_SEND_METHOD: 'POST'})
        self.channel.save()
        self.clear_cache()

        with self.settings(SEND_MESSAGES=True):
            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, "Sent")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                self.assertEqual(mock.call_args[0][0], 'http://foo.com/send')
                self.assertEqual(mock.call_args[1]['data'], 'text=Test+message&to=250788383383')

        self.channel.config = json.dumps({Channel.CONFIG_SEND_URL: 'http://foo.com/send',
                                          Channel.CONFIG_SEND_BODY: 'text={{text}}&to={{to_no_plus}}',
                                          Channel.CONFIG_SEND_METHOD: 'PUT'})

        self.channel.save()
        self.clear_cache()

        with self.settings(SEND_MESSAGES=True):
            with patch('requests.put') as mock:
                mock.return_value = MockResponse(200, "Sent")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                self.assertEqual(mock.call_args[0][0], 'http://foo.com/send')
                self.assertEqual(mock.call_args[1]['data'], 'text=Test+message&to=250788383383')

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, "Sent")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "Error")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False

        # view the log item for our send
        self.login(self.admin)
        log_item = ChannelLog.objects.all().order_by('created_on').first()
        response = self.client.get(reverse('channels.channellog_read', args=[log_item.pk]))
        self.assertEquals(response.context['object'].description, 'Successfully delivered')

        # make sure we can't see it as anon
        self.org.is_anon = True
        self.org.save()

        response = self.client.get(reverse('channels.channellog_read', args=[log_item.pk]))
        self.assertEquals(302, response.status_code)

        # change our admin to be a CS rep, see if they can see the page
        self.admin.groups.add(Group.objects.get(name='Customer Support'))
        response = self.client.get(reverse('channels.channellog_read', args=[log_item.pk]))
        self.assertEquals(response.context['object'].description, 'Successfully delivered')


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

        call = IVRCall.create_outgoing(self.channel, contact, contact.get_urn(TEL_SCHEME), None, self.admin)
        call.external_id = "12345"
        call.save()

        self.assertEqual(call.status, PENDING)

        response = self.client.get(callback_url + "?From=250788456456&CallStatus=ringing&CallSid=12345")

        self.assertEqual(response.status_code, 200)
        call = IVRCall.objects.get(pk=call.pk)
        self.assertEqual(call.status, RINGING)


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

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals("+252788123123", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World", msg.text)

        # fails if missing sender
        response = self.client.get(callback_url + "?sender=252788123123")
        self.assertEquals(400, response.status_code)

        # fails if missing message
        response = self.client.get(callback_url + "?message=Hello+World")
        self.assertEquals(400, response.status_code)

    def test_send(self):
        joe = self.create_contact("Joe", "+252788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "ybs_autocreate_status=OK")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(SENT, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.side_effect = [MockResponse(401, "Error"), MockResponse(200, 'ybs_autocreate_status=OK')]

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(SENT, msg.status)
                self.assertTrue(msg.sent_on)

                # check that requests was called twice, using the backup URL the second time
                self.assertEquals(2, mock.call_count)
                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Kaboom")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "ybs_autocreate_status=ERROR&ybs_autocreate_message=" +
                                                      "YBS+AutoCreate+Subsystem%3A+Access+denied" +
                                                      "+due+to+wrong+authorization+code")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(2, msg.error_count)
                self.assertTrue(msg.next_attempt)

                # contact should not be stopped
                joe.refresh_from_db()
                self.assertFalse(joe.is_stopped)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "ybs_autocreate_status=ERROR&ybs_autocreate_message=" +
                                                 "256794224665%3ABLACKLISTED")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as a failure
                msg.refresh_from_db()
                self.assertEquals(FAILED, msg.status)
                self.assertEquals(2, msg.error_count)
                self.assertTrue(msg.next_attempt)

                # contact should also be stopped
                joe.refresh_from_db()
                self.assertTrue(joe.is_stopped)

        finally:
            settings.SEND_MESSAGES = False


class ShaqodoonTest(TembaTest):

    def setUp(self):
        super(ShaqodoonTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'SO', 'SQ', None, '+250788123123',
                                      config={Channel.CONFIG_SEND_URL: 'http://foo.com/send',
                                              Channel.CONFIG_USERNAME: 'username',
                                              Channel.CONFIG_PASSWORD: 'password',
                                              Channel.CONFIG_KEY: 'key'},
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_receive(self):
        data = {'from': '252788123456', 'text': 'Hello World!'}
        callback_url = reverse('handlers.shaqodoon_handler', args=['received', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals("+252788123456", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World!", msg.text)

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message ☺", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "Sent")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


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

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals("+252788123456", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World!", msg.text)

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message ☺", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                msg.text = "Test message"
                mock.return_value = MockResponse(200,
                                                 """[{"Response":"0"}]""")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                self.assertEqual(mock.call_args[1]['params']['SMSType'], '0')

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

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
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200,
                                                 """[{"Response":"1"}]""")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(2, msg.error_count)
                self.assertTrue(msg.next_attempt)

                self.clear_cache()
        finally:
            settings.SEND_MESSAGES = False


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
        self.assertEquals(400, response.status_code)

        # ok, try with a valid uuid, but invalid message id -1
        delivery_url = reverse('handlers.kannel_handler', args=['status', self.channel.uuid])
        response = self.client.post(delivery_url, data)
        self.assertEquals(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)

        data['id'] = msg.pk

        def assertStatus(sms, status, assert_status):
            data['status'] = status
            response = self.client.post(reverse('handlers.kannel_handler', args=['status', self.channel.uuid]), data)
            self.assertEquals(200, response.status_code)
            sms = Msg.all_messages.get(pk=sms.id)
            self.assertEquals(assert_status, sms.status)

        assertStatus(msg, '4', SENT)
        assertStatus(msg, '1', DELIVERED)
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

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals("+250788383383", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World!", msg.text)

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, 'Accepted 201')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                # assert verify was set to true
                self.assertTrue(mock.call_args[1]['verify'])
                self.assertEquals('+250788383383', mock.call_args[1]['params']['to'])

                self.clear_cache()

            self.channel.config = json.dumps(dict(username='kannel-user', password='kannel-pass',
                                                  encoding=Channel.ENCODING_SMART, use_national=True,
                                                  send_url='http://foo/', verify_ssl=False))
            self.channel.save()

            msg.text = "No capital accented È!"
            msg.save()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, 'Accepted 201')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                # assert verify was set to true
                self.assertEquals('No capital accented E!', mock.call_args[1]['params']['text'])
                self.assertEquals('788383383', mock.call_args[1]['params']['to'])
                self.assertFalse('coding' in mock.call_args[1]['params'])
                self.clear_cache()

            msg.text = "Unicode. ☺"
            msg.save()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, 'Accepted 201')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                # assert verify was set to true
                self.assertEquals("Unicode. ☺", mock.call_args[1]['params']['text'])
                self.assertEquals('2', mock.call_args[1]['params']['coding'])

                self.clear_cache()

            msg.text = "Normal"
            msg.save()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, 'Accepted 201')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                # assert verify was set to true
                self.assertEquals("Normal", mock.call_args[1]['params']['text'])
                self.assertFalse('coding' in mock.call_args[1]['params'])

                self.clear_cache()

            self.channel.config = json.dumps(dict(username='kannel-user', password='kannel-pass',
                                                  encoding=Channel.ENCODING_UNICODE,
                                                  send_url='http://foo/', verify_ssl=False))
            self.channel.save()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, 'Accepted 201')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                # assert verify was set to true
                self.assertEquals("Normal", mock.call_args[1]['params']['text'])
                self.assertEquals('2', mock.call_args[1]['params']['coding'])

                self.clear_cache()

            self.channel.config = json.dumps(dict(username='kannel-user', password='kannel-pass',
                                                  send_url='http://foo/', verify_ssl=False))
            self.channel.save()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # assert verify was set to False
                self.assertFalse(mock.call_args[1]['verify'])

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class NexmoTest(TembaTest):

    def setUp(self):
        super(NexmoTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'NX', None, '+250788123123',
                                      uuid='00000000-0000-0000-0000-000000001234')

        self.nexmo_uuid = str(uuid.uuid4())
        nexmo_config = {NEXMO_KEY: '1234', NEXMO_SECRET: '1234', NEXMO_UUID: self.nexmo_uuid}

        org = self.channel.org

        config = org.config_json()
        config.update(nexmo_config)
        org.config = json.dumps(config)
        org.save()

    def test_status(self):
        # ok, what happens with an invalid uuid and number
        data = dict(to='250788123111', messageId='external1')
        response = self.client.get(reverse('handlers.nexmo_handler', args=['status', 'not-real-uuid']), data)
        self.assertEquals(404, response.status_code)

        # ok, try with a valid uuid, but invalid message id -1, should return 200
        # these are probably multipart message callbacks, which we don't track
        data = dict(to='250788123123', messageId='-1')
        delivery_url = reverse('handlers.nexmo_handler', args=['status', self.nexmo_uuid])
        response = self.client.get(delivery_url, data)
        self.assertEquals(200, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        msg.external_id = 'external1'
        msg.save(update_fields=('external_id',))

        data['messageId'] = 'external1'

        def assertStatus(sms, status, assert_status):
            data['status'] = status
            response = self.client.get(reverse('handlers.nexmo_handler', args=['status', self.nexmo_uuid]), data)
            self.assertEquals(200, response.status_code)
            sms = Msg.all_messages.get(pk=sms.id)
            self.assertEquals(assert_status, sms.status)

        assertStatus(msg, 'delivered', DELIVERED)
        assertStatus(msg, 'expired', FAILED)
        assertStatus(msg, 'failed', FAILED)
        assertStatus(msg, 'accepted', SENT)
        assertStatus(msg, 'buffered', SENT)

    def test_receive(self):
        data = dict(to='250788123123', msisdn='250788111222', text='Hello World!', messageId='external1')
        callback_url = reverse('handlers.nexmo_handler', args=['receive', self.nexmo_uuid])
        response = self.client.get(callback_url, data)

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals("+250788111222", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World!", msg.text)
        self.assertEquals('external1', msg.external_id)

    def test_send(self):
        from temba.orgs.models import NEXMO_KEY, NEXMO_SECRET
        org_config = self.org.config_json()
        org_config[NEXMO_KEY] = 'nexmo_key'
        org_config[NEXMO_SECRET] = 'nexmo_secret'
        self.org.config = json.dumps(org_config)

        self.channel.channel_type = Channel.TYPE_NEXMO
        self.channel.save()

        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True
            r = get_redis_connection()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, json.dumps(dict(messages=[{'status': 0, 'message-id': 12}])), method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(SENT, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals('12', msg.external_id)

                self.clear_cache()

                # test some throttling by sending three messages right after another
                start = time.time()
                for i in range(3):
                    Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
                    r.delete(timezone.now().strftime(MSG_SENT_KEY))

                    msg.refresh_from_db()
                    self.assertEquals(SENT, msg.status)

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
                self.assertEquals(SENT, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals('12', msg.external_id)

                # assert that we were called with unicode
                mock.assert_called_once_with('https://rest.nexmo.com/sms/json',
                                             params={'from': u'250788123123',
                                                     'api_secret': u'1234',
                                                     'status-report-req': 1,
                                                     'to': u'250788383383',
                                                     'text': u'Unicode \u263a',
                                                     'api_key': u'1234',
                                                     'type': 'unicode'})

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(401, "Invalid API token", method='POST')

                # clear out our channel log
                ChannelLog.objects.all().delete()

                # then send it
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check status
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)

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
                self.assertEquals(SENT, msg.status)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(2, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class VumiTest(TembaTest):

    def setUp(self):
        super(VumiTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'VM', None, '+250788123123',
                                      config=dict(account_key='vumi-key', access_token='vumi-token', conversation_key='key'),
                                      uuid='00000000-0000-0000-0000-000000001234')

        self.trey = self.create_contact("Trey Anastasio", "250788382382")

    def test_receive(self):
        callback_url = reverse('handlers.vumi_handler', args=['receive', self.channel.uuid])

        response = self.client.get(callback_url)
        self.assertEqual(response.status_code, 405)

        response = self.client.post(callback_url, json.dumps(dict()), content_type="application/json")
        self.assertEqual(response.status_code, 400)

        data = dict(timestamp="2014-04-18 03:54:20.570618", message_id="123456", from_addr="+250788383383",
                    content="Hello from Vumi")

        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        self.assertEqual(response.status_code, 200)

        msg = Msg.all_messages.get()
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello from Vumi", msg.text)
        self.assertEquals('123456', msg.external_id)

    def test_delivery_reports(self):

        msg = self.create_msg(direction='O', text='Outgoing message', contact=self.trey, status=WIRED,
                              external_id=unicode(uuid.uuid4()),)

        data = dict(event_type='delivery_report',
                    event_id=unicode(uuid.uuid4()),
                    message_type='event',
                    delivery_status='failed',
                    user_message_id=msg.external_id)

        callback_url = reverse('handlers.vumi_handler', args=['event', self.channel.uuid])

        # response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
        # self.assertEquals(200, response.status_code)

        # check that we've become errored
        # sms = Msg.all_messages.get(pk=sms.pk)
        # self.assertEquals(ERRORED, sms.status)

        # couple more failures should move to failure
        # Msg.all_messages.filter(pk=sms.pk).update(status=WIRED)
        # self.client.post(callback_url, json.dumps(data), content_type="application/json")

        # Msg.all_messages.filter(pk=sms.pk).update(status=WIRED)
        # self.client.post(callback_url, json.dumps(data), content_type="application/json")

        # sms = Msg.all_messages.get(pk=sms.pk)
        # self.assertEquals(FAILED, sms.status)

        # successful deliveries shouldn't stomp on failures
        # del data['delivery_status']
        # self.client.post(callback_url, json.dumps(data), content_type="application/json")
        # sms = Msg.all_messages.get(pk=sms.pk)
        # self.assertEquals(FAILED, sms.status)

        # if we are wired we can now be successful again
        data['delivery_status'] = 'delivered'
        Msg.all_messages.filter(pk=msg.pk).update(status=WIRED)
        self.client.post(callback_url, json.dumps(data), content_type="application/json")
        msg.refresh_from_db()
        self.assertEquals(DELIVERED, msg.status)

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        self.create_group("Reporters", [joe])
        msg = joe.send("Test message", self.admin, trigger_send=False)
        r = get_redis_connection()

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.put') as mock:
                mock.return_value = MockResponse(200, '{ "message_id": "1515" }')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals("1515", msg.external_id)
                self.assertEquals(1, mock.call_count)

                # should have a failsafe that it was sent
                self.assertTrue(r.sismember(timezone.now().strftime(MSG_SENT_KEY), str(msg.id)))

                # try sending again, our failsafe should kick in
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # we shouldn't have been called again
                self.assertEquals(1, mock.call_count)

                # simulate Vumi calling back to us telling us it failed
                data = dict(event_type='delivery_report',
                            event_id=unicode(uuid.uuid4()),
                            message_type='event',
                            delivery_status='failed',
                            user_message_id=msg.external_id)
                callback_url = reverse('handlers.vumi_handler', args=['event', self.channel.uuid])
                self.client.post(callback_url, json.dumps(data), content_type="application/json")

                # get the message again
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                # self.assertTrue(msg.next_attempt)
                # self.assertFalse(r.sismember(timezone.now().strftime(MSG_SENT_KEY), str(msg.id)))

                self.clear_cache()

            with patch('requests.put') as mock:
                mock.return_value = MockResponse(500, "Error")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as errored, we'll retry in a bit
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt > timezone.now())
                self.assertEquals(1, mock.call_count)

                self.clear_cache()

            with patch('requests.put') as mock:
                mock.return_value = MockResponse(503, "<html><body><h1>503 Service Unavailable</h1>")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as errored, we'll retry in a bit
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(2, msg.error_count)
                self.assertTrue(msg.next_attempt > timezone.now())
                self.assertEquals(1, mock.call_count)

                # Joe shouldn't be stopped and should still be in a group
                joe = Contact.objects.get(id=joe.id)
                self.assertFalse(joe.is_stopped)
                self.assertTrue(ContactGroup.user_groups.filter(contacts=joe))

                self.clear_cache()

            with patch('requests.put') as mock:
                # set our next attempt as if we are trying anew
                msg.next_attempt = timezone.now()
                msg.save()

                mock.return_value = MockResponse(400, "User has opted out")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as failed
                msg.refresh_from_db()
                self.assertEquals(FAILED, msg.status)
                self.assertEquals(2, msg.error_count)
                self.assertTrue(msg.next_attempt < timezone.now())
                self.assertEquals(1, mock.call_count)

                # could should now be stopped as well and in no groups
                joe = Contact.objects.get(id=joe.id)
                self.assertTrue(joe.is_stopped)
                self.assertFalse(ContactGroup.user_groups.filter(contacts=joe))

        finally:
            settings.SEND_MESSAGES = False


class VumiUssdTest(TembaTest):

    def setUp(self):
        super(VumiUssdTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', Channel.TYPE_VUMI_USSD, None, '+250788123123',
                                      config=dict(account_key='vumi-key', access_token='vumi-token', conversation_key='key'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_receive(self):
        callback_url = reverse('handlers.vumi_handler', args=['receive', self.channel.uuid])

        response = self.client.get(callback_url)
        self.assertEqual(response.status_code, 405)

        response = self.client.post(callback_url, json.dumps(dict()), content_type="application/json")
        self.assertEqual(response.status_code, 404)

        data = dict(timestamp="2016-04-18 03:54:20.570618", message_id="123456", from_addr="+250788383383",
                    content="Hello from Vumi", transport_type='ussd')

        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        self.assertEqual(response.status_code, 200)

        msg = Msg.all_messages.get()
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello from Vumi", msg.text)
        self.assertEquals('123456', msg.external_id)

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        self.create_group("Reporters", [joe])
        msg = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing message
        msg.refresh_from_db()
        r = get_redis_connection()

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.put') as mock:
                mock.return_value = MockResponse(200, '{ "message_id": "1515" }')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals("1515", msg.external_id)
                self.assertEquals(1, mock.call_count)

                # should have a failsafe that it was sent
                self.assertTrue(r.sismember(timezone.now().strftime(MSG_SENT_KEY), str(msg.id)))

                # try sending again, our failsafe should kick in
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # we shouldn't have been called again
                self.assertEquals(1, mock.call_count)

                self.clear_cache()
        finally:
            settings.SEND_MESSAGES = False

    def test_ack(self):
        joe = self.create_contact("Joe", "+250788383383")
        self.create_group("Reporters", [joe])
        msg = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing message
        msg.refresh_from_db()

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.put') as mock:
                mock.return_value = MockResponse(200, '{ "message_id": "1515" }')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals("1515", msg.external_id)
                self.assertEquals(1, mock.call_count)

                # simulate Vumi calling back to us sending an ACK event
                data = {
                    "transport_name": "ussd_transport",
                    "event_type": "ack",
                    "event_id": unicode(uuid.uuid4()),
                    "sent_message_id": unicode(uuid.uuid4()),
                    "helper_metadata": {},
                    "routing_metadata": {},
                    "message_version": "20110921",
                    "timestamp": unicode(timezone.now()),
                    "transport_metadata": {},
                    "user_message_id": msg.external_id,
                    "message_type": "event"
                }
                callback_url = reverse('handlers.vumi_handler', args=['event', self.channel.uuid])
                self.client.post(callback_url, json.dumps(data), content_type="application/json")

                # it should be SENT now
                msg.refresh_from_db()
                self.assertEquals(SENT, msg.status)

                self.clear_cache()
        finally:
            settings.SEND_MESSAGES = False

    def test_nack(self):
        joe = self.create_contact("Joe", "+250788383383")
        self.create_group("Reporters", [joe])
        msg = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing message
        msg.refresh_from_db()
        r = get_redis_connection()

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.put') as mock:
                mock.return_value = MockResponse(200, '{ "message_id": "1515" }')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals("1515", msg.external_id)
                self.assertEquals(1, mock.call_count)

                # should have a failsafe that it was sent
                self.assertTrue(r.sismember(timezone.now().strftime(MSG_SENT_KEY), str(msg.id)))

                # simulate Vumi calling back to us sending an NACK event
                data = {
                    "transport_name": "ussd_transport",
                    "event_type": "nack",
                    "nack_reason": "Unknown address.",
                    "event_id": unicode(uuid.uuid4()),
                    "timestamp": unicode(timezone.now()),
                    "message_version": "20110921",
                    "transport_metadata": {},
                    "user_message_id": msg.external_id,
                    "message_type": "event"
                }
                callback_url = reverse('handlers.vumi_handler', args=['event', self.channel.uuid])
                response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

                self.assertEqual(response.status_code, 200)
                self.assertTrue(self.create_contact("Joe", "+250788383383").is_stopped)

                self.clear_cache()
        finally:
            settings.SEND_MESSAGES = False

    @patch('temba.msgs.models.Msg.create_incoming')
    def test_interrupt(self, create_incoming):
        callback_url = reverse('handlers.vumi_handler', args=['receive', self.channel.uuid])

        response = self.client.get(callback_url)
        self.assertEqual(response.status_code, 405)

        response = self.client.post(callback_url, json.dumps(dict()), content_type="application/json")
        self.assertEqual(response.status_code, 404)

        data = dict(timestamp="2016-04-18 03:54:20.570618", message_id="123456", from_addr="+250788383383",
                    content="Hello from Vumi", transport_type='ussd', session_event="close")

        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        self.assertEqual(response.status_code, 200)

        # no real messages stored
        self.assertEquals(Msg.all_messages.count(), 0)

        self.assertTrue(create_incoming.called)
        self.assertEqual(create_incoming.call_count, 1)

        args, kwargs = create_incoming.call_args
        self.assertEqual(kwargs['status'], INTERRUPTED)


class ZenviaTest(TembaTest):

    def setUp(self):
        super(ZenviaTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'BR', 'ZV', None, '+250788123123',
                                      config=dict(account='zv-account', code='zv-code'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_status(self):
        # ok, what happens with an invalid uuid?
        data = dict(id="-1", status="500")
        response = self.client.get(reverse('handlers.zenvia_handler', args=['status', 'not-real-uuid']), data)

        self.assertEquals(404, response.status_code)

        # ok, try with a valid uuid, but invalid message id -1
        delivery_url = reverse('handlers.zenvia_handler', args=['status', self.channel.uuid])
        response = self.client.get(delivery_url, data)

        self.assertEquals(404, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)

        data['id'] = msg.pk

        def assertStatus(sms, status, assert_status):
            data['status'] = status
            response = self.client.get(delivery_url, data)
            self.assertEquals(200, response.status_code)
            sms = Msg.all_messages.get(pk=sms.id)
            self.assertEquals(assert_status, sms.status)

        assertStatus(msg, '120', DELIVERED)
        assertStatus(msg, '111', SENT)
        assertStatus(msg, '140', FAILED)
        assertStatus(msg, '999', FAILED)
        assertStatus(msg, '131', FAILED)

    def test_receive(self):
        data = {'from': '5511996458779', 'date': '31/07/2013 14:45:00'}
        encoded_message = "?msg=H%E9llo World%21"

        callback_url = reverse('handlers.zenvia_handler', args=['receive', self.channel.uuid]) + encoded_message
        response = self.client.post(callback_url, data)

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals("+5511996458779", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Héllo World!", msg.text)

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, '000-ok', method='GET')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class InfobipTest(TembaTest):

    def setUp(self):
        super(InfobipTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'NG', 'IB', None, '+2347030767144',
                                      config=dict(username='ib-user', password='ib-password'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_received(self):
        data = {'receiver': '2347030767144', 'sender': '2347030767143', 'text': 'Hello World'}
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.infobip_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals('+2347030767143', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World", msg.text)

        # try it with an invalid receiver, should fail as UUID and receiver id are mismatched
        data['receiver'] = '2347030767145'
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.infobip_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 404 as the channel wasn't found
        self.assertEquals(404, response.status_code)

    def test_delivered(self):
        contact = self.create_contact("Joe", '+2347030767143')
        msg = Msg.create_outgoing(self.org, self.user, contact, "Hi Joe")
        msg.external_id = '254021015120766124'
        msg.save(update_fields=('external_id',))

        # mark it as delivered
        base_body = '<DeliveryReport><message id="254021015120766124" sentdate="2014/02/10 16:12:07" ' \
                    ' donedate="2014/02/10 16:13:00" status="STATUS" gsmerror="0" price="0.65" /></DeliveryReport>'
        delivery_url = reverse('handlers.infobip_handler', args=['delivered', self.channel.uuid])

        # assert our SENT status
        response = self.client.post(delivery_url, data=base_body.replace('STATUS', 'SENT'), content_type='application/xml')
        self.assertEquals(200, response.status_code)
        msg = Msg.all_messages.get()
        self.assertEquals(SENT, msg.status)

        # assert our DELIVERED status
        response = self.client.post(delivery_url, data=base_body.replace('STATUS', 'DELIVERED'), content_type='application/xml')
        self.assertEquals(200, response.status_code)
        msg = Msg.all_messages.get()
        self.assertEquals(DELIVERED, msg.status)

        # assert our FAILED status
        response = self.client.post(delivery_url, data=base_body.replace('STATUS', 'NOT_SENT'), content_type='application/xml')
        self.assertEquals(200, response.status_code)
        msg = Msg.all_messages.get()
        self.assertEquals(FAILED, msg.status)

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, json.dumps(dict(results=[{'status': 0, 'messageid': 12}])))

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(SENT, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals('12', msg.external_id)

                self.clear_cache()

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class BlackmynaTest(TembaTest):

    def setUp(self):
        super(BlackmynaTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'NP', 'BM', None, '1212',
                                      config=dict(username='bm-user', password='bm-password'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_received(self):
        data = {'to': '1212', 'from': '+977788123123', 'text': 'Hello World', 'smsc': 'NTNepal5002'}
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.blackmyna_handler', args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals('+977788123123', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World", msg.text)

        # try it with an invalid receiver, should fail as UUID and receiver id are mismatched
        data['to'] = '1515'
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.blackmyna_handler', args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 400 as the channel wasn't found
        self.assertEquals(400, response.status_code)

    def test_send(self):
        joe = self.create_contact("Joe", "+977788123123")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, json.dumps([{'recipient': '+977788123123',
                                                                   'id': 'asdf-asdf-asdf-asdf'}]))

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals('asdf-asdf-asdf-asdf', msg.external_id)

                self.clear_cache()

            # return 400
            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

            # return something that isn't JSON
            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(2, msg.error_count)
                self.assertTrue(msg.next_attempt)

                # we should have "Error" in our error log
                log = ChannelLog.objects.filter(msg=msg).order_by('-pk')[0]
                self.assertEquals("Error", log.response)
                self.assertEquals(503, log.response_status)

        finally:
            settings.SEND_MESSAGES = False

    def test_status(self):
        # an invalid uuid
        data = dict(id='-1', status='10')
        response = self.client.get(reverse('handlers.blackmyna_handler', args=['status', 'not-real-uuid']), data)
        self.assertEquals(400, response.status_code)

        # a valid uuid, but invalid data
        status_url = reverse('handlers.blackmyna_handler', args=['status', self.channel.uuid])
        response = self.client.get(status_url, dict())
        self.assertEquals(400, response.status_code)

        response = self.client.get(status_url, data)
        self.assertEquals(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        msg.external_id = 'msg-uuid'
        msg.save(update_fields=('external_id',))

        data['id'] = msg.external_id

        def assertStatus(sms, status, assert_status):
            sms.status = WIRED
            sms.save()
            data['status'] = status
            response = self.client.get(status_url, data)
            self.assertEquals(200, response.status_code)
            sms = Msg.all_messages.get(external_id=sms.external_id)
            self.assertEquals(assert_status, sms.status)

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
        data = {'mobile': '+977788123123', 'message': 'Hello World', 'telco': 'Ncell'}
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.smscentral_handler', args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals('+977788123123', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World", msg.text)

        # try it with an invalid channel
        callback_url = reverse('handlers.smscentral_handler', args=['receive', '1234-asdf']) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 400 as the channel wasn't found
        self.assertEquals(400, response.status_code)

    def test_send(self):
        joe = self.create_contact("Joe", "+977788123123")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, '')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                mock.assert_called_with('http://smail.smscentral.com.np/bp/ApiSms.php',
                                        data={'user': 'sc-user', 'pass': 'sc-password',
                                              'mobile': '977788123123', 'content': "Test message"},
                                        headers=TEMBA_HEADERS,
                                        timeout=30)

                self.clear_cache()

            # return 400
            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

        finally:
            settings.SEND_MESSAGES = False


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

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals('+6289881134560', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World", msg.text)

        # try it with an invalid receiver, should fail as UUID and receiver id are mismatched
        data['sendto'] = '6289881131111'
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.hub9_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 404 as the channel wasn't found
        self.assertEquals(404, response.status_code)

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

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.all().order_by('-pk').first()
        self.assertEquals('+62811999374', msg.contact.raw_tel())
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello Jakarta", msg.text)

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "000")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(SENT, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


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

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals('+33610346460', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World", msg.text)
        self.assertEquals(14, msg.created_on.astimezone(pytz.utc).hour)

        # try it with an invalid receiver, should fail as UUID isn't known
        callback_url = reverse('handlers.hcnx_handler', args=['receive', uuid.uuid4()])
        response = self.client.post(callback_url, data)

        # should get 400 as the channel wasn't found
        self.assertEquals(400, response.status_code)

        # create an outgoing message instead
        contact = msg.contact
        Msg.all_messages.all().delete()

        contact.send("outgoing message", self.admin)
        msg = Msg.all_messages.get()

        # now update the status via a callback
        data = {'ret_id': msg.id, 'status': '6'}
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.hcnx_handler', args=['status', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEquals(200, response.status_code)

        msg = Msg.all_messages.get()
        self.assertEquals(DELIVERED, msg.status)

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "Sent")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class TwilioTest(TembaTest):

    def setUp(self):
        super(TwilioTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'T', None, '+250785551212',
                                      uuid='00000000-0000-0000-0000-000000001234')

        # twilio test credentials
        self.account_sid = "ACe54dc36bfd2a3b483b7ed854b2dd40c1"
        self.account_token = "0b14d47901387c03f92253a4e4449d5e"
        self.application_sid = "AP6fe2069df7f9482a8031cb61dc155de2"

        self.channel.org.config = json.dumps({ACCOUNT_SID: self.account_sid,
                                              ACCOUNT_TOKEN: self.account_token,
                                              APPLICATION_SID: self.application_sid})
        self.channel.org.save()

    @patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_receive_mms(self):
        post_data = dict(To=self.channel.address, From='+250788383383', Body="Test",
                         NumMedia='1', MediaUrl0='https://yourimage.io/IMPOSSIBLE-HASH',
                         MediaContentType0='audio/x-wav')

        twilio_url = reverse('handlers.twilio_handler')

        client = self.org.get_twilio_client()
        validator = RequestValidator(client.auth[1])
        signature = validator.compute_signature('https://' + settings.TEMBA_HOST + '/handlers/twilio/', post_data)

        with patch('requests.get') as response:
            mock = MockResponse(200, 'Fake Recording Bits')
            mock.add_header('Content-Disposition', 'filename="audio0000.wav"')
            mock.add_header('Content-Type', 'audio/x-wav')
            response.return_value = mock
            response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})
            self.assertEquals(201, response.status_code)

        # we should have two messages, one for the text, the other for the media
        msgs = Msg.all_messages.all().order_by('-created_on')
        self.assertEqual(2, msgs.count())
        self.assertEqual('Test', msgs[0].text)
        self.assertIsNone(msgs[0].media)
        self.assertTrue(msgs[1].media.startswith('audio/x-wav:https://%s' % settings.AWS_BUCKET_DOMAIN))
        self.assertTrue(msgs[1].media.endswith('.wav'))

        # text should have the url (without the content type)
        self.assertTrue(msgs[1].text.startswith('https://%s' % settings.AWS_BUCKET_DOMAIN))
        self.assertTrue(msgs[1].text.endswith('.wav'))

        Msg.all_messages.all().delete()

        # try with no message body
        with patch('requests.get') as response:
            mock = MockResponse(200, 'Fake Recording Bits')
            mock.add_header('Content-Disposition', 'filename="audio0000.wav"')
            mock.add_header('Content-Type', 'audio/x-wav')
            response.return_value = mock

            post_data['Body'] = ''
            signature = validator.compute_signature('https://' + settings.TEMBA_HOST + '/handlers/twilio/', post_data)
            response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})

        # just a single message this time
        msg = Msg.all_messages.get()
        self.assertTrue(msg.media.startswith('audio/x-wav:https://%s' % settings.AWS_BUCKET_DOMAIN))
        self.assertTrue(msg.media.endswith('.wav'))

        Msg.all_messages.all().delete()

        with patch('requests.get') as response:
            mock1 = MockResponse(404, 'No such file')
            mock2 = MockResponse(200, 'Fake VCF Bits')
            mock2.add_header('Content-Type', 'text/x-vcard')
            mock2.add_header('Content-Disposition', 'inline')
            response.side_effect = (mock1, mock2)

            post_data['Body'] = ''
            signature = validator.compute_signature('https://' + settings.TEMBA_HOST + '/handlers/twilio/', post_data)
            response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})

        msg = Msg.all_messages.get()
        self.assertTrue(msg.media.startswith('text/x-vcard:https://%s' % settings.AWS_BUCKET_DOMAIN))
        self.assertTrue(msg.media.endswith('.vcf'))

    def test_receive(self):
        post_data = dict(To=self.channel.address, From='+250788383383', Body="Hello World")
        twilio_url = reverse('handlers.twilio_handler')

        try:
            self.client.post(twilio_url, post_data)
            self.fail("Invalid signature, should have failed")
        except ValidationError:
            pass

        # this time sign it appropriately, should work
        client = self.org.get_twilio_client()
        validator = RequestValidator(client.auth[1])
        signature = validator.compute_signature('https://' + settings.TEMBA_HOST + '/handlers/twilio/', post_data)
        response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})

        self.assertEquals(201, response.status_code)

        # and we should have a new message
        msg1 = Msg.all_messages.get()
        self.assertEquals("+250788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg1.direction)
        self.assertEquals(self.org, msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals("Hello World", msg1.text)

        # try with non-normalized number
        post_data['To'] = '0785551212'
        post_data['ToCountry'] = 'RW'
        signature = validator.compute_signature('https://' + settings.TEMBA_HOST + '/handlers/twilio/', post_data)
        response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})
        self.assertEquals(201, response.status_code)

        # and we should have another new message
        msg2 = Msg.all_messages.exclude(pk=msg1.pk).get()
        self.assertEquals(self.channel, msg2.channel)

        # create an outgoing message instead
        contact = msg2.contact
        Msg.all_messages.all().delete()

        contact.send("outgoing message", self.admin)
        msg = Msg.all_messages.get()

        # now update the status via a callback
        twilio_url = reverse('handlers.twilio_handler') + "?action=callback&id=%d" % msg.id
        post_data['SmsStatus'] = 'sent'

        signature = validator.compute_signature('https://' + settings.TEMBA_HOST + '%s' % twilio_url, post_data)
        response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})

        self.assertEquals(200, response.status_code)

        msg = Msg.all_messages.get()
        self.assertEquals(SENT, msg.status)

        # try it with a failed SMS
        Msg.all_messages.all().delete()
        contact.send("outgoing message", self.admin)
        msg = Msg.all_messages.get()

        # now update the status via a callback (also test old api/v1 URL)
        twilio_url = reverse('handlers.twilio_handler') + "?action=callback&id=%d" % msg.id
        post_data['SmsStatus'] = 'failed'

        signature = validator.compute_signature('https://' + settings.TEMBA_HOST + '%s' % twilio_url, post_data)
        response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})

        self.assertEquals(200, response.status_code)
        msg = Msg.all_messages.get()
        self.assertEquals(FAILED, msg.status)

    def test_send(self):
        from temba.orgs.models import ACCOUNT_SID, ACCOUNT_TOKEN, APPLICATION_SID
        org_config = self.org.config_json()
        org_config[ACCOUNT_SID] = 'twilio_sid'
        org_config[ACCOUNT_TOKEN] = 'twilio_token'
        org_config[APPLICATION_SID] = 'twilio_sid'
        self.org.config = json.dumps(org_config)
        self.org.save()

        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('twilio.rest.resources.Messages.create') as mock:
                mock.return_value = "Sent"

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('twilio.rest.resources.Messages.create') as mock:
                mock.side_effect = Exception("Failed to send message")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

            # check that our channel log works as well
            self.login(self.admin)

            response = self.client.get(reverse('channels.channellog_list') + "?channel=%d" % (self.channel.pk))

            # there should be two log items for the two times we sent
            self.assertEquals(2, len(response.context['channellog_list']))

            # of items on this page should be right as well
            self.assertEquals(2, response.context['paginator'].count)

            # the counts on our relayer should be correct as well
            self.channel = Channel.objects.get(id=self.channel.pk)
            self.assertEquals(1, self.channel.get_error_log_count())
            self.assertEquals(1, self.channel.get_success_log_count())

            # view the detailed information for one of them
            response = self.client.get(reverse('channels.channellog_read', args=[ChannelLog.objects.all()[1].pk]))

            # check that it contains the log of our exception
            self.assertContains(response, "Failed to send message")

            # delete our error entry
            ChannelLog.objects.filter(is_error=True).delete()

            # our counts should be right
            # the counts on our relayer should be correct as well
            self.channel = Channel.objects.get(id=self.channel.pk)
            self.assertEquals(0, self.channel.get_error_log_count())
            self.assertEquals(1, self.channel.get_success_log_count())

        finally:
            settings.SEND_MESSAGES = False


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

        self.channel.org.config = json.dumps({ACCOUNT_SID: account_sid, ACCOUNT_TOKEN: account_token,
                                              APPLICATION_SID: application_sid})
        self.channel.org.save()

        messaging_service_sid = self.channel.config_json()['messaging_service_sid']

        post_data = dict(message_service_sid=messaging_service_sid, From='+250788383383', Body="Hello World")
        twilio_url = reverse('handlers.twilio_messaging_service_handler', args=['receive', self.channel.uuid])

        try:
            self.client.post(twilio_url, post_data)
            self.fail("Invalid signature, should have failed")
        except ValidationError:
            pass

        # this time sign it appropriately, should work
        client = self.org.get_twilio_client()
        validator = RequestValidator(client.auth[1])
        signature = validator.compute_signature(
            'https://' + settings.HOSTNAME + '/handlers/twilio_messaging_service/receive/' + self.channel.uuid,
            post_data
        )
        response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})

        self.assertEquals(201, response.status_code)

        # and we should have a new message
        msg1 = Msg.all_messages.get()
        self.assertEquals("+250788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg1.direction)
        self.assertEquals(self.org, msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals("Hello World", msg1.text)

    def test_send(self):
        from temba.orgs.models import ACCOUNT_SID, ACCOUNT_TOKEN, APPLICATION_SID
        org_config = self.org.config_json()
        org_config[ACCOUNT_SID] = 'twilio_sid'
        org_config[ACCOUNT_TOKEN] = 'twilio_token'
        org_config[APPLICATION_SID] = 'twilio_sid'
        self.org.config = json.dumps(org_config)
        self.org.save()

        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('twilio.rest.resources.Messages.create') as mock:
                mock.return_value = "Sent"

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('twilio.rest.resources.Messages.create') as mock:
                mock.side_effect = Exception("Failed to send message")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

            # check that our channel log works as well
            self.login(self.admin)

            response = self.client.get(reverse('channels.channellog_list') + "?channel=%d" % self.channel.pk)

            # there should be two log items for the two times we sent
            self.assertEquals(2, len(response.context['channellog_list']))

            # of items on this page should be right as well
            self.assertEquals(2, response.context['paginator'].count)

            # the counts on our relayer should be correct as well
            self.channel = Channel.objects.get(id=self.channel.pk)
            self.assertEquals(1, self.channel.get_error_log_count())
            self.assertEquals(1, self.channel.get_success_log_count())

            # view the detailed information for one of them
            response = self.client.get(reverse('channels.channellog_read', args=[ChannelLog.objects.all()[1].pk]))

            # check that it contains the log of our exception
            self.assertContains(response, "Failed to send message")

            # delete our error entry
            ChannelLog.objects.filter(is_error=True).delete()

            # our counts should be right
            # the counts on our relayer should be correct as well
            self.channel = Channel.objects.get(id=self.channel.pk)
            self.assertEquals(0, self.channel.get_error_log_count())
            self.assertEquals(1, self.channel.get_success_log_count())

        finally:
            settings.SEND_MESSAGES = False


class ClickatellTest(TembaTest):

    def setUp(self):
        super(ClickatellTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'CT', None, '+250788123123',
                                      config=dict(username='uname', password='pword', api_id='api1'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_receive_utf16(self):
        self.channel.org.config = json.dumps({Channel.CONFIG_API_ID: '12345', Channel.CONFIG_USERNAME: 'uname', Channel.CONFIG_PASSWORD: 'pword'})
        self.channel.org.save()

        data = {'to': self.channel.address,
                'from': '250788383383',
                'timestamp': '2012-10-10 10:10:10',
                'moMsgId': 'id1234'}

        encoded_message = urlencode(data)
        encoded_message += "&text=%00m%00e%00x%00i%00c%00o%00+%00k%00+%00m%00i%00s%00+%00p%00a%00p%00a%00s%00+%00n%00o%00+%00t%00e%00n%00%ED%00a%00+%00d%00i%00n%00e%00r%00o%00+%00p%00a%00r%00a%00+%00c%00o%00m%00p%00r%00a%00r%00n%00o%00s%00+%00l%00o%00+%00q%00+%00q%00u%00e%00r%00%ED%00a%00m%00o%00s%00.%00."
        encoded_message += "&charset=UTF-16BE"
        receive_url = reverse('handlers.clickatell_handler', args=['receive', self.channel.uuid]) + '?' + encoded_message

        response = self.client.get(receive_url)

        self.assertEquals(200, response.status_code)

        # and we should have a new message
        msg1 = Msg.all_messages.get()
        self.assertEquals("+250788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg1.direction)
        self.assertEquals(self.org, msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals(u"mexico k mis papas no ten\xeda dinero para comprarnos lo q quer\xedamos..", msg1.text)
        self.assertEquals(2012, msg1.created_on.year)
        self.assertEquals('id1234', msg1.external_id)

    def test_receive_iso_8859_1(self):
        self.channel.org.config = json.dumps({Channel.CONFIG_API_ID: '12345', Channel.CONFIG_USERNAME: 'uname', Channel.CONFIG_PASSWORD: 'pword'})
        self.channel.org.save()

        data = {'to': self.channel.address,
                'from': '250788383383',
                'timestamp': '2012-10-10 10:10:10',
                'moMsgId': 'id1234'}

        encoded_message = urlencode(data)
        encoded_message += "&text=%05%EF%BF%BD%EF%BF%BD%034%02%02i+mapfumbamwe+vana+4+kuwacha+handingapedze+izvozvo+ndozvikukonzera+kt+varoorwe+varipwere+ngapaonekwe+ipapo+ndatenda."
        encoded_message += "&charset=ISO-8859-1"
        receive_url = reverse('handlers.clickatell_handler', args=['receive', self.channel.uuid]) + '?' + encoded_message

        response = self.client.get(receive_url)

        self.assertEquals(200, response.status_code)

        # and we should have a new message
        msg1 = Msg.all_messages.get()
        self.assertEquals("+250788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg1.direction)
        self.assertEquals(self.org, msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals(u'\x05\x034\x02\x02i mapfumbamwe vana 4 kuwacha handingapedze izvozvo ndozvikukonzera kt varoorwe varipwere ngapaonekwe ipapo ndatenda.', msg1.text)
        self.assertEquals(2012, msg1.created_on.year)
        self.assertEquals('id1234', msg1.external_id)

        Msg.all_messages.all().delete()

        encoded_message = urlencode(data)
        encoded_message += "&text=Artwell+S%ECbbnda"
        encoded_message += "&charset=ISO-8859-1"
        receive_url = reverse('handlers.clickatell_handler', args=['receive', self.channel.uuid]) + '?' + encoded_message

        response = self.client.get(receive_url)

        self.assertEquals(200, response.status_code)
        # and we should have a new message
        msg1 = Msg.all_messages.get()
        self.assertEquals("+250788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg1.direction)
        self.assertEquals(self.org, msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals("Artwell Sìbbnda", msg1.text)
        self.assertEquals(2012, msg1.created_on.year)
        self.assertEquals('id1234', msg1.external_id)

        Msg.all_messages.all().delete()

        encoded_message = urlencode(data)
        encoded_message += "&text=a%3F+%A3irvine+stinta%3F%A5.++"
        encoded_message += "&charset=ISO-8859-1"
        receive_url = reverse('handlers.clickatell_handler', args=['receive', self.channel.uuid]) + '?' + encoded_message

        response = self.client.get(receive_url)

        self.assertEquals(200, response.status_code)
        # and we should have a new message
        msg1 = Msg.all_messages.get()
        self.assertEquals("+250788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg1.direction)
        self.assertEquals(self.org, msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals("a? £irvine stinta?¥.  ", msg1.text)
        self.assertEquals(2012, msg1.created_on.year)
        self.assertEquals('id1234', msg1.external_id)

        Msg.all_messages.all().delete()

        data['text'] = 'when? or What? is this '

        encoded_message = urlencode(data)
        encoded_message += "&charset=ISO-8859-1"
        receive_url = reverse('handlers.clickatell_handler', args=['receive', self.channel.uuid]) + '?' + encoded_message

        response = self.client.get(receive_url)

        self.assertEquals(200, response.status_code)
        # and we should have a new message
        msg1 = Msg.all_messages.get()
        self.assertEquals("+250788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg1.direction)
        self.assertEquals(self.org, msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals("when? or What? is this ", msg1.text)
        self.assertEquals(2012, msg1.created_on.year)
        self.assertEquals('id1234', msg1.external_id)

    def test_receive(self):
        self.channel.org.config = json.dumps({Channel.CONFIG_API_ID: '12345', Channel.CONFIG_USERNAME: 'uname', Channel.CONFIG_PASSWORD: 'pword'})
        self.channel.org.save()

        data = {'to': self.channel.address,
                'from': '250788383383',
                'text': "Hello World",
                'timestamp': '2012-10-10 10:10:10',
                'moMsgId': 'id1234'}

        encoded_message = urlencode(data)
        receive_url = reverse('handlers.clickatell_handler', args=['receive', self.channel.uuid]) + '?' + encoded_message

        response = self.client.get(receive_url)

        self.assertEquals(200, response.status_code)

        # and we should have a new message
        msg1 = Msg.all_messages.get()
        self.assertEquals("+250788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg1.direction)
        self.assertEquals(self.org, msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals("Hello World", msg1.text)
        self.assertEquals(2012, msg1.created_on.year)

        # times are sent as GMT+2
        self.assertEquals(8, msg1.created_on.hour)
        self.assertEquals('id1234', msg1.external_id)

    def test_status(self):
        self.channel.org.config = json.dumps({Channel.CONFIG_API_ID: '12345', Channel.CONFIG_USERNAME: 'uname', Channel.CONFIG_PASSWORD: 'pword'})
        self.channel.org.save()

        contact = self.create_contact("Joe", "+250788383383")
        msg = Msg.create_outgoing(self.org, self.user, contact, "test")
        msg.external_id = 'id1234'
        msg.save(update_fields=('external_id',))

        data = {'apiMsgId': 'id1234', 'status': '001'}
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.clickatell_handler', args=['status', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEquals(200, response.status_code)

        # reload our message
        msg = Msg.all_messages.get(pk=msg.pk)

        # make sure it is marked as failed
        self.assertEquals(FAILED, msg.status)

        # reset our status to WIRED
        msg.status = WIRED
        msg.save()

        # and do it again with a received state
        data = {'apiMsgId': 'id1234', 'status': '004'}
        encoded_message = urlencode(data)

        callback_url = reverse('handlers.clickatell_handler', args=['status', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # load our message
        msg = Msg.all_messages.all().order_by('-pk').first()

        # make sure it is marked as delivered
        self.assertEquals(DELIVERED, msg.status)

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                msg.text = "Test message"
                mock.return_value = MockResponse(200, "000")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)
                params = {'api_id': 'api1',
                          'user': 'uname',
                          'password': 'pword',
                          'from': '250788123123',
                          'concat': 3,
                          'callback': 7,
                          'mo': 1,
                          'unicode': 0,
                          'to': "250788383383",
                          'text': "Test message"}
                mock.assert_called_with('https://api.clickatell.com/http/sendmsg', params=params, headers=TEMBA_HEADERS,
                                        timeout=5)

                self.clear_cache()

            with patch('requests.get') as mock:
                msg.text = "Test message ☺"
                mock.return_value = MockResponse(200, "000")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)
                params = {'api_id': 'api1',
                          'user': 'uname',
                          'password': 'pword',
                          'from': '250788123123',
                          'concat': 3,
                          'callback': 7,
                          'mo': 1,
                          'unicode': 1,
                          'to': "250788383383",
                          'text': "Test message ☺"}
                mock.assert_called_with('https://api.clickatell.com/http/sendmsg', params=params, headers=TEMBA_HEADERS,
                                        timeout=5)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class TelegramTest(TembaTest):

    def setUp(self):
        super(TelegramTest, self).setUp()

        self.channel.delete()

        self.channel = Channel.create(self.org, self.user, None, Channel.TYPE_TELEGRAM, None, 'RapidBot',
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
        response = self.client.post(receive_url, data, content_type='application/json', post_data=data)
        self.assertEquals(200, response.status_code)

        # and we should have a new message
        msg1 = Msg.all_messages.get()
        self.assertEquals('3527065', msg1.contact.get_urn(TELEGRAM_SCHEME).path)
        self.assertEquals(INCOMING, msg1.direction)
        self.assertEquals(self.org, msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals("Hello World", msg1.text)
        self.assertEqual(msg1.contact.name, 'Nic Pottier')

        def test_file_message(data, file_path, content_type, extension, caption=None):

            Msg.all_messages.all().delete()

            with patch('requests.post') as post:
                with patch('requests.get') as get:

                    post.return_value = MockResponse(200, json.dumps(dict(ok="true", result=dict(file_path=file_path))))
                    get.return_value = MockResponse(200, "Fake image bits", headers={"Content-Type": content_type})

                    response = self.client.post(receive_url, data, content_type='application/json', post_data=data)
                    self.assertEquals(200, response.status_code)

                    # should have a media message now with an image
                    msgs = Msg.all_messages.all().order_by('-pk')

                    if caption:
                        self.assertEqual(msgs.count(), 2)
                        self.assertEqual(msgs[1].text, caption)
                    else:
                        self.assertEqual(msgs.count(), 1)

                    self.assertTrue(msgs[0].media.startswith('%s:https://' % content_type))
                    self.assertTrue(msgs[0].media.endswith(extension))
                    self.assertTrue(msgs[0].text.startswith('https://'))
                    self.assertTrue(msgs[0].text.endswith(extension))

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

        # with patch('requests.post') as post:
        # post.return_value = MockResponse(200, json.dumps(dict(ok="true", result=dict(file_path=file_path))))
        Msg.all_messages.all().delete()
        response = self.client.post(receive_url, location_data, content_type='application/json', post_data=location_data)
        self.assertEquals(200, response.status_code)

        # should have a media message now with an image
        msgs = Msg.all_messages.all().order_by('-created_on')
        self.assertEqual(msgs.count(), 1)
        self.assertTrue(msgs[0].media.startswith('geo:'))
        self.assertTrue('Fogo Mar' in msgs[0].text)

    def test_send(self):
        joe = self.create_contact("Ernie", urn='telegram:1234')
        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, json.dumps({"result": {"message_id": 1234}}))

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)
                self.clear_cache()

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

        finally:
            settings.SEND_MESSAGES = False


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

    def test_receive(self):
        response = self.client.get(reverse('handlers.plivo_handler', args=['receive', 'not-real-uuid']), dict())
        self.assertEquals(400, response.status_code)

        data = dict(MessageUUID="msg-uuid", Text="Hey, there", To="254788383383", From="254788383383")
        receive_url = reverse('handlers.plivo_handler', args=['receive', self.channel.uuid])
        response = self.client.get(receive_url, data)
        self.assertEquals(400, response.status_code)

        data = dict(MessageUUID="msg-uuid", Text="Hey, there", To=self.channel.address.lstrip('+'), From="254788383383")
        response = self.client.get(receive_url, data)
        self.assertEquals(200, response.status_code)

        msg1 = Msg.all_messages.get()
        self.assertEquals("+254788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg1.direction)
        self.assertEquals(self.org, msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals('Hey, there', msg1.text)

    def test_status(self):
        # an invalid uuid
        data = dict(MessageUUID="-1", Status="delivered", From=self.channel.address.lstrip('+'), To="254788383383")
        response = self.client.get(reverse('handlers.plivo_handler', args=['status', 'not-real-uuid']), data)
        self.assertEquals(400, response.status_code)

        # a valid uuid, but invalid data
        delivery_url = reverse('handlers.plivo_handler', args=['status', self.channel.uuid])
        response = self.client.get(delivery_url, dict())
        self.assertEquals(400, response.status_code)

        response = self.client.get(delivery_url, data)
        self.assertEquals(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        msg.external_id = 'msg-uuid'
        msg.save(update_fields=('external_id',))

        data['MessageUUID'] = msg.external_id

        def assertStatus(sms, status, assert_status):
            sms.status = WIRED
            sms.save()
            data['Status'] = status
            response = self.client.get(delivery_url, data)
            self.assertEquals(200, response.status_code)
            sms = Msg.all_messages.get(external_id=sms.external_id)
            self.assertEquals(assert_status, sms.status)

        assertStatus(msg, 'queued', WIRED)
        assertStatus(msg, 'sent', SENT)
        assertStatus(msg, 'delivered', DELIVERED)
        assertStatus(msg, 'undelivered', SENT)
        assertStatus(msg, 'rejected', FAILED)

    def test_send(self):
        msg = self.joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(202,
                                                 json.dumps({"message": "message(s) queued",
                                                             "message_uuid": ["db3ce55a-7f1d-11e1-8ea7-1231380bc196"],
                                                             "api_id": "db342550-7f1d-11e1-8ea7-1231380bc196"}))

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class TwitterTest(TembaTest):

    def setUp(self):
        super(TwitterTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, None, 'TT', None, 'billy_bob',
                                      config={'oauth_token': 'abcdefghijklmnopqrstuvwxyz',
                                              'oauth_token_secret': '0123456789'},
                                      uuid='00000000-0000-0000-0000-000000001234')

        self.joe = self.create_contact("Joe", "+250788383383")

    def test_send(self):
        joe = self.create_contact("Joe", number="+250788383383", twitter="joe1981")
        testers = self.create_group("Testers", [joe])

        msg = joe.send("This is a long message, longer than just 160 characters, it spans what was before "
                       "more than one message but which is now but one, solitary message, going off into the "
                       "Twitterverse to tweet away.",
                       self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('twython.Twython.send_direct_message') as mock:
                mock.return_value = dict(id=1234567890)

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # assert we were only called once
                self.assertEquals(1, mock.call_count)

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertEquals('1234567890', msg.external_id)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            ChannelLog.objects.all().delete()

            with patch('twython.Twython.send_direct_message') as mock:
                mock.side_effect = TwythonError("Failed to send message")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
                self.assertEquals("Failed to send message", ChannelLog.objects.get(msg=msg).description)

                self.clear_cache()

            ChannelLog.objects.all().delete()

            with patch('twython.Twython.send_direct_message') as mock:
                mock.side_effect = TwythonError("Different 403 error.", error_code=403)

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(2, msg.error_count)
                self.assertTrue(msg.next_attempt)

                # should not fail the contact
                contact = Contact.objects.get(pk=joe.pk)
                self.assertFalse(contact.is_stopped)
                self.assertEqual(contact.user_groups.count(), 1)

                # should record the right error
                self.assertTrue(ChannelLog.objects.get(msg=msg).description.find("Different 403 error") >= 0)

            with patch('twython.Twython.send_direct_message') as mock:
                mock.side_effect = TwythonError("You cannot send messages to users who are not following you.",
                                                error_code=403)

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # should fail the message
                msg.refresh_from_db()
                self.assertEquals(FAILED, msg.status)
                self.assertEquals(2, msg.error_count)

                # should be stopped
                contact = Contact.objects.get(pk=joe.pk)
                self.assertTrue(contact.is_stopped)
                self.assertEqual(contact.user_groups.count(), 0)

                self.clear_cache()

            joe.is_stopped = False
            joe.save()
            testers.update_contacts(self.user, [joe], add=True)

            with patch('twython.Twython.send_direct_message') as mock:
                mock.side_effect = TwythonError("There was an error sending your message: You can't send direct messages to this user right now.",
                                                error_code=403)

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # should fail the message
                msg.refresh_from_db()
                self.assertEquals(FAILED, msg.status)
                self.assertEquals(2, msg.error_count)

                # should fail the contact permanently (i.e. removed from groups)
                contact = Contact.objects.get(pk=joe.pk)
                self.assertTrue(contact.is_stopped)
                self.assertEqual(contact.user_groups.count(), 0)

                self.clear_cache()

            joe.is_stopped = False
            joe.save()
            testers.update_contacts(self.user, [joe], add=True)

            with patch('twython.Twython.send_direct_message') as mock:
                mock.side_effect = TwythonError("Sorry, that page does not exist.", error_code=404)

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # should fail the message
                msg.refresh_from_db()
                self.assertEqual(msg.status, FAILED)
                self.assertEqual(msg.error_count, 2)

                # should fail the contact permanently (i.e. removed from groups)
                contact = Contact.objects.get(pk=joe.pk)
                self.assertTrue(contact.is_stopped)
                self.assertEqual(contact.user_groups.count(), 0)

                self.clear_cache()

        finally:
            settings.SEND_MESSAGES = False


class MageHandlerTest(TembaTest):

    def setUp(self):
        super(MageHandlerTest, self).setUp()

        self.org.webhook = u'{"url": "http://fake.com/webhook.php"}'
        self.org.webhook_events = ALL_EVENTS
        self.org.save()

        self.joe = self.create_contact("Joe", number="+250788383383")

        self.dyn_group = self.create_group("Bobs", query="name has Bob")

    def create_contact_like_mage(self, name, twitter):
        """
        Creates a contact as if it were created in Mage, i.e. no event/group triggering or cache updating
        """
        contact = Contact.objects.create(org=self.org, name=name, is_active=True, is_blocked=False,
                                         uuid=uuid.uuid4(), is_stopped=False,
                                         modified_by=self.user, created_by=self.user,
                                         modified_on=timezone.now(), created_on=timezone.now())
        urn = ContactURN.objects.create(org=self.org, contact=contact,
                                        urn="twitter:%s" % twitter, scheme="twitter", path=twitter, priority="90")
        return contact, urn

    def create_message_like_mage(self, text, contact, contact_urn=None):
        """
        Creates a message as it if were created in Mage, i.e. no topup decrementing or cache updating
        """
        if not contact_urn:
            contact_urn = contact.get_urn(TEL_SCHEME)
        return Msg.all_messages.create(org=self.org, text=text,
                                       direction=INCOMING, created_on=timezone.now(),
                                       channel=self.channel, contact=contact, contact_urn=contact_urn)

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
        msg = Msg.all_messages.get(pk=msg.pk)
        self.assertEqual('H', msg.status)
        self.assertEqual(self.welcome_topup, msg.topup)

        # check for a web hook event
        event = json.loads(WebHookEvent.objects.get(org=self.org, event=SMS_RECEIVED).data)
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

        msg = Msg.all_messages.get(pk=msg.pk)
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

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(2, contact_counts[ContactGroup.TYPE_ALL])

        # simulate scenario where Mage has added new contact with name that should put it into a dynamic group
        mage_contact, mage_contact_urn = self.create_contact_like_mage("Bob", "bobby81")

        response = self.client.post(url, dict(channel_id=channel.id,
                                              contact_urn_id=mage_contact_urn.id, new_contact=True), **headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, flow.runs.all().count())

        # check that contact ended up dynamic group
        self.assertEqual([mage_contact], list(self.dyn_group.contacts.order_by('name')))

        # check contact count updated
        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts[ContactGroup.TYPE_ALL], 3)


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

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals('+250788123123', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World", msg.text)

        # try it with an invalid body
        response = self.client.post(callback_url, content_type='application/xml', data="invalid body")

        # should get a 400, as the body is invalid
        self.assertEquals(400, response.status_code)

        Msg.all_messages.all().delete()

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

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals('+250788123123', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("", msg.text)

        # try it with an invalid channel
        callback_url = reverse('handlers.start_handler', args=['receive', '1234-asdf'])
        response = self.client.post(callback_url, content_type='application/xml', data=body)

        # should get 400 as the channel wasn't found
        self.assertEquals(400, response.status_code)

    def test_send(self):
        joe = self.create_contact("Joe", "+977788123123")
        msg = joe.send("Вітаємо в U-Report, системі опитувань про майбутнє країни.Зараз невеличка реєстрація.?",
                       self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200,
                                                 """<status date='Wed, 25 May 2016 17:29:56 +0300'>
                                                 <id>380502535130309161501</id><state>Accepted</state></status>""")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEqual(msg.external_id, "380502535130309161501")

                self.assertEqual('http://bulk.startmobile.com.ua/clients.php', mock.call_args[0][0])
                self.clear_cache()

            # return 400
            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
                self.clear_cache()

            # return invalid XML
            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, "<error>This is an error</error>", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(2, msg.error_count)
                self.assertTrue(msg.next_attempt)
                self.clear_cache()

        finally:
            settings.SEND_MESSAGES = False


class ChikkaTest(TembaTest):

    def setUp(self):
        super(ChikkaTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'PH', Channel.TYPE_CHIKKA, None, '920920',
                                      uuid='00000000-0000-0000-0000-000000001234')

        config = {Channel.CONFIG_USERNAME: 'username', Channel.CONFIG_PASSWORD: 'password'}
        self.channel.config = json.dumps(config)
        self.channel.save()

    def test_status(self):
        # try with an invalid channel uuid
        data = dict(message_type='outgoing', message_id=1001, status='FAILED')
        response = self.client.post(reverse('handlers.chikka_handler', args=['not-real-uuid']), data)
        self.assertEquals(400, response.status_code)

        # ok, try with a valid uuid, but invalid message id 1001, should return 400 as well
        response = self.client.post(reverse('handlers.chikka_handler', args=[self.channel.uuid]), data)
        self.assertEquals(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+63911231234")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        data['message_id'] = msg.id

        # valid id, invalid status, 400
        data['status'] = 'INVALID'
        response = self.client.post(reverse('handlers.chikka_handler', args=[self.channel.uuid]), data)
        self.assertEquals(400, response.status_code)

        def assertStatus(sms, status, assert_status):
            sms.status = WIRED
            sms.save()

            data['status'] = status
            response = self.client.post(reverse('handlers.chikka_handler', args=[self.channel.uuid]), data)
            self.assertEquals(200, response.status_code)
            updated_sms = Msg.all_messages.get(pk=sms.id)
            self.assertEquals(assert_status, updated_sms.status)

        assertStatus(msg, 'FAILED', FAILED)
        assertStatus(msg, 'SENT', SENT)

    def test_receive(self):
        data = dict(message_type='incoming', mobile_number='639178020779', request_id='4004',
                    message='Hello World!', timestamp='1457670059.69')
        callback_url = reverse('handlers.chikka_handler', args=[self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals("+639178020779", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World!", msg.text)
        self.assertEquals('4004', msg.external_id)
        self.assertEquals(msg.created_on.date(), date(day=11, month=3, year=2016))

    def test_send(self):
        joe = self.create_contact("Joe", '+63911231234')

        # incoming message for a reply test
        incoming = Msg.create_incoming(self.channel, 'tel:+63911231234', "incoming message")
        incoming.external_id = '4004'
        incoming.save()

        msg = joe.send("Test message", self.admin, trigger_send=False)

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, "Success", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
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
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                # assert that we were called as a reply
                self.assertEqual(mock.call_args[1]['data']['message_type'], 'REPLY')
                self.assertEqual(mock.call_args[1]['data']['request_id'], '4004')
                self.clear_cache()

            with patch('requests.get') as mock:
                mock.side_effect = Exception("Couldn't reach server")
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.side_effect = Exception("Couldn't reach server")
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # should also have an error
                msg.refresh_from_db()
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(2, msg.error_count)
                self.assertTrue(msg.next_attempt)

        finally:
            settings.SEND_MESSAGES = False


class JasminTest(TembaTest):

    def setUp(self):
        super(JasminTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'JS', None, '1234',
                                      config=dict(username='jasmin-user', password='jasmin-pass', send_url='http://foo/'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def tearDown(self):
        super(JasminTest, self).tearDown()
        settings.SEND_MESSAGES = False

    def test_status(self):
        # ok, what happens with an invalid uuid?
        data = dict(id="-1", dlvr="0", err="0")
        response = self.client.post(reverse('handlers.jasmin_handler', args=['status', 'not-real-uuid']), data)
        self.assertEquals(400, response.status_code)

        # ok, try with a valid uuid, but invalid message id -1
        delivery_url = reverse('handlers.jasmin_handler', args=['status', self.channel.uuid])
        response = self.client.post(delivery_url, data)
        self.assertEquals(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        msg.external_id = "jasmin-external-id"
        msg.save(update_fields=('external_id',))

        data['id'] = msg.external_id

        def assertStatus(sms, dlvrd, err, assert_status):
            data['dlvrd'] = dlvrd
            data['err'] = err
            response = self.client.post(reverse('handlers.jasmin_handler', args=['status', self.channel.uuid]), data)
            self.assertEquals(200, response.status_code)
            sms = Msg.all_messages.get(pk=sms.id)
            self.assertEquals(assert_status, sms.status)

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

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, "ACK/Jasmin")

        # load our message
        msg = Msg.all_messages.get()
        self.assertEquals("+250788383383", msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("événement", msg.text)

    def test_send(self):
        from temba.utils import gsm7

        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("événement", self.admin, trigger_send=False)

        settings.SEND_MESSAGES = True

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

            self.clear_cache()

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(412, 'Error “No route found”')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEquals(ERRORED, msg.status)


class MbloxTest(TembaTest):

    def setUp(self):
        super(MbloxTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'MB', None, '1234',
                                      config=dict(username='mbox-user', password='mblox-pass'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def tearDown(self):
        super(MbloxTest, self).tearDown()
        settings.SEND_MESSAGES = False

    def test_dlr(self):
        # invalid uuid
        data = dict(batch_id="-1", status="Failed", type="recipient_delivery_report_sms")
        response = self.client.post(reverse('handlers.mblox_handler', args=['not-real-uuid']), json.dumps(data),
                                    content_type="application/json")
        self.assertEquals(400, response.status_code)

        delivery_url = reverse('handlers.mblox_handler', args=[self.channel.uuid])

        # missing batch_id param
        data = dict(status="Failed", type="recipient_delivery_report_sms")
        response = self.client.post(delivery_url, json.dumps(data), content_type="application/json")
        self.assertEquals(400, response.status_code)

        # missing type params
        data = dict(status="Failed")
        response = self.client.post(delivery_url, json.dumps(data), content_type="application/json")
        self.assertEquals(400, response.status_code)

        # valid uuid, invalid batch_id
        data = dict(batch_id="-1", status="Failed", type="recipient_delivery_report_sms")
        response = self.client.post(delivery_url, json.dumps(data), content_type="application/json")
        self.assertEquals(400, response.status_code)

        # create test message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        msg.external_id = "mblox-id"
        msg.save(update_fields=('external_id',))

        data['batch_id'] = msg.external_id

        def assertStatus(msg, status, assert_status):
            Msg.all_messages.filter(id=msg.id).update(status=WIRED)
            data['status'] = status
            response = self.client.post(delivery_url, json.dumps(data), content_type="application/json")
            self.assertEquals(200, response.status_code)
            self.assertEqual(response.content, "SMS Updated: %d" % msg.id)
            msg = Msg.all_messages.get(pk=msg.id)
            self.assertEquals(assert_status, msg.status)

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

        msg = Msg.all_messages.get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, "SMS Accepted: %d" % msg.id)

        # load our message
        self.assertEqual(msg.contact.get_urn(TEL_SCHEME).path, "+12067799294")
        self.assertEqual(msg.direction, INCOMING)
        self.assertEqual(msg.org, self.org)
        self.assertEqual(msg.channel, self.channel)
        self.assertEqual(msg.text, "MO")
        self.assertEqual(msg.created_on.date(), date(day=30, month=3, year=2016))

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        msg = joe.send("MT", self.admin, trigger_send=False)

        settings.SEND_MESSAGES = True

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

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(412, 'Error')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEquals(ERRORED, msg.status)


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
        settings.SEND_MESSAGES = False

    def test_dlr(self):
        # invalid uuid
        body = dict()
        response = self.client.post(reverse('handlers.facebook_handler', args=['invalid']), json.dumps(body),
                                    content_type="application/json")
        self.assertEquals(400, response.status_code)

        # invalid body
        response = self.client.post(reverse('handlers.facebook_handler', args=[self.channel.uuid]), json.dumps(body),
                                    content_type="application/json")
        self.assertEquals(400, response.status_code)

        # no known msgs, gracefully ignore
        body = dict(entry=[dict()])
        response = self.client.post(reverse('handlers.facebook_handler', args=[self.channel.uuid]), json.dumps(body),
                                    content_type="application/json")
        self.assertEquals(200, response.status_code)

        # create test message to update
        joe = self.create_contact("Joe Biden", urn='facebook:1234')
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        msg.external_id = "mblox-id"
        msg.save(update_fields=('external_id',))

        body = dict(entry=[dict(messaging=[dict(delivery=dict(mids=[msg.external_id]))])])
        response = self.client.post(reverse('handlers.facebook_handler', args=[self.channel.uuid]), json.dumps(body),
                                    content_type='application/json')

        self.assertEqual(response.status_code, 200)

        msg.refresh_from_db()
        self.assertEqual(msg.status, DELIVERED)

    def test_affinity(self):
        data = json.loads(FacebookTest.TEST_INCOMING)

        with patch('requests.get') as mock_get:
            mock_get.return_value = MockResponse(200, '{"first_name": "Ben","last_name": "Haggerty"}')

            callback_url = reverse('handlers.facebook_handler', args=[self.channel.uuid])
            response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
            self.assertEqual(response.status_code, 200)

            # check the channel affinity for our URN
            urn = ContactURN.objects.get(urn='facebook:5678')
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

            urn = ContactURN.objects.get(urn='facebook:5678')
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

    def test_receive(self):
        data = json.loads(FacebookTest.TEST_INCOMING)
        callback_url = reverse('handlers.facebook_handler', args=[self.channel.uuid])

        with patch('requests.get') as mock_get:
            mock_get.return_value = MockResponse(200, '{"first_name": "Ben","last_name": "Haggerty"}')
            response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

            msg = Msg.all_messages.get()

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

            Msg.all_messages.all().delete()
            Contact.all().delete()

        # simulate a failure to fetch contact data
        with patch('requests.get') as mock_get:
            mock_get.return_value = MockResponse(400, '{"error": "Unable to look up profile data"}')
            response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

            self.assertEqual(response.status_code, 200)

            msg = Msg.all_messages.get()

            self.assertEqual(msg.contact.get_urn(FACEBOOK_SCHEME).path, "5678")
            self.assertIsNone(msg.contact.name)

            Msg.all_messages.all().delete()
            Contact.all().delete()

        # simulate an exception
        with patch('requests.get') as mock_get:
            mock_get.return_value = MockResponse(200, 'Invalid JSON')
            response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

            self.assertEqual(response.status_code, 200)

            msg = Msg.all_messages.get()

            self.assertEqual(msg.contact.get_urn(FACEBOOK_SCHEME).path, "5678")
            self.assertIsNone(msg.contact.name)

            Msg.all_messages.all().delete()
            Contact.all().delete()

        # now with a anon org, shouldn't try to look things up
        self.org.is_anon = True
        self.org.save()

        with patch('requests.get') as mock_get:
            response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

            self.assertEqual(response.status_code, 200)

            msg = Msg.all_messages.get()

            self.assertEqual(msg.contact.get_urn(FACEBOOK_SCHEME).path, "5678")
            self.assertIsNone(msg.contact.name)
            self.assertEqual(mock_get.call_count, 0)

            Msg.all_messages.all().delete()
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

        msg = Msg.all_messages.get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(msg.text, "http://mediaurl.com/img.gif")

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
        Msg.all_messages.all().delete()

        data = json.loads(data)
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        msg = Msg.all_messages.get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(msg.text, "Get in touch with us.\nhttp://m.me/")

    def test_send(self):
        joe = self.create_contact("Joe", urn="facebook:1234")
        msg = joe.send("Facebook Msg", self.admin, trigger_send=False)

        settings.SEND_MESSAGES = True

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

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(412, 'Error')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEquals(ERRORED, msg.status)


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

        # POST, invalid destination Address
        bad_data = copy.deepcopy(data)
        bad_data['inboundSMSMessageList']['inboundSMSMessage'][0]['destinationAddress'] = '9999'
        response = self.client.post(callback_url, json.dumps(bad_data), content_type="application/json")
        self.assertEqual(response.status_code, 400)

        # POST, mismatched destination address
        bad_data = copy.deepcopy(data)
        bad_data['inboundSMSMessageList']['inboundSMSMessage'][0]['destinationAddress'] = 'tel:9999'
        response = self.client.post(callback_url, json.dumps(bad_data), content_type="application/json")
        self.assertEqual(response.status_code, 400)

        # POST, invalid sender address
        bad_data = copy.deepcopy(data)
        bad_data['inboundSMSMessageList']['inboundSMSMessage'][0]['senderAddress'] = '9999'
        response = self.client.post(callback_url, json.dumps(bad_data), content_type="application/json")
        self.assertEqual(response.status_code, 400)

        # ok, valid post
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 200)

        msg = Msg.all_messages.get()
        self.assertEqual(response.content, "Msgs Accepted: %d" % msg.id)

        # load our message
        self.assertEqual(msg.contact.get_urn(TEL_SCHEME).path, "+639171234567")
        self.assertEqual(msg.direction, INCOMING)
        self.assertEqual(msg.org, self.org)
        self.assertEqual(msg.channel, self.channel)
        self.assertEqual(msg.text, "Hello")
        self.assertEqual(msg.created_on.date(), date(day=22, month=11, year=2013))

    def test_send(self):
        joe = self.create_contact("Joe", "+639171234567")
        msg = joe.send("MT", self.admin, trigger_send=False)

        settings.SEND_MESSAGES = True

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
            self.assertEquals(ERRORED, msg.status)
            self.clear_cache()

        with patch('requests.get') as mock:
            mock.side_effect = Exception("Unable to reach host")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEquals(ERRORED, msg.status)
            self.clear_cache()


class ViberTest(TembaTest):

    def setUp(self):
        super(ViberTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, None, Channel.TYPE_VIBER, None, '1001',
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_status(self):
        data = {
            "message_token": 99999,
            "message_status": 0
        }
        # ok, what happens with an invalid uuid?
        response = self.client.post(reverse('handlers.viber_handler', args=['status', 'not-real-uuid']), json.dumps(data),
                                    content_type="application/json")
        self.assertEquals(400, response.status_code)

        # ok, try with a valid uuid, but invalid message id (no msg yet)
        status_url = reverse('handlers.viber_handler', args=['status', self.channel.uuid])
        response = self.client.post(status_url, json.dumps(data), content_type="application/json")
        self.assertEquals(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        msg.external_id = "99999"
        msg.save(update_fields=('external_id',))

        response = self.client.post(status_url, json.dumps(data), content_type="application/json")
        self.assertEquals(200, response.status_code)

        msg = Msg.all_messages.get(pk=msg.id)
        self.assertEquals(DELIVERED, msg.status)

    def test_receive(self):
        # invalid UUID
        response = self.client.post(reverse('handlers.viber_handler', args=['receive', '00000000-0000-0000-0000-000000000000']))
        self.assertEqual(response.status_code, 400)

        data = {
            "message_token": 44444444444444,
            "phone_number": "972512222222",
            "time": 1471906585,
            "message": {
                "text": "a message to the service",
                "tracking_data": "tracking_id:100035"
            }
        }
        callback_url = reverse('handlers.viber_handler', args=['receive', self.channel.uuid])

        # try a GET
        response = self.client.get(callback_url)
        self.assertEqual(response.status_code, 405)

        # POST invalid JSON data
        response = self.client.post(callback_url, "not json", content_type="application/json")
        self.assertEqual(response.status_code, 400)

        # POST missing data
        response = self.client.post(callback_url, json.dumps({}), content_type="application/json")
        self.assertEqual(response.status_code, 400)

        # ok, valid post
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 200)

        msg = Msg.all_messages.get()
        self.assertEqual(response.content, "Msg Accepted: %d" % msg.id)

        # load our message
        self.assertEqual(msg.contact.get_urn(TEL_SCHEME).path, "+972512222222")
        self.assertEqual(msg.direction, INCOMING)
        self.assertEqual(msg.org, self.org)
        self.assertEqual(msg.channel, self.channel)
        self.assertEqual(msg.text, "a message to the service")
        self.assertEqual(msg.created_on.date(), date(day=22, month=8, year=2016))
        self.assertEqual(msg.external_id, "44444444444444")

    def test_send(self):
        joe = self.create_contact("Joe", "+639171234567")
        msg = joe.send("MT", self.admin, trigger_send=False)

        settings.SEND_MESSAGES = True
        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "status":0, "seq": 123456, "message_token": "999" }')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message is now sent
            msg.refresh_from_db()
            self.assertEqual(msg.status, WIRED)
            self.assertTrue(msg.sent_on)
            self.assertEqual(msg.external_id, "999")
            self.clear_cache()

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{"status":3}')

            # send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # message should have failed permanently
            msg.refresh_from_db()
            self.assertEqual(msg.status, FAILED)
            self.clear_cache()

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(401, 'Error')

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEquals(ERRORED, msg.status)
            self.clear_cache()

        with patch('requests.post') as mock:
            mock.side_effect = Exception("Unable to reach host")

            # manually send it off
            Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

            # check the status of the message now errored
            msg.refresh_from_db()
            self.assertEquals(ERRORED, msg.status)
            self.clear_cache()
