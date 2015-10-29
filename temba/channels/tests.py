# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import base64
import hashlib
import hmac
import json
import phonenumbers
import time
import urllib2


from datetime import timedelta
from django.conf import settings
from django.db.models import Sum
from django.contrib.auth.models import User, Group
from django.core import mail
from django.core.cache import cache
from django.core.urlresolvers import reverse
from django.test.utils import override_settings
from django.utils import timezone
from django.template import loader, Context
from mock import patch
from smartmin.tests import SmartminTest
from mock import Mock
from temba.contacts.models import Contact, ContactGroup, ContactURN, TEL_SCHEME, TWITTER_SCHEME
from temba.middleware import BrandingMiddleware
from temba.msgs.models import Msg, Broadcast, Call, IVR
from temba.channels.models import Channel, ChannelCount, SyncEvent, Alert, SMART_ENCODING, ENCODING
from temba.channels.models import ALERT_DISCONNECTED, ALERT_SMS, TWILIO, ANDROID, TWITTER
from temba.channels.models import PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN, PLIVO_APP_ID, ENCODING, SMART_ENCODING
from temba.orgs.models import Org, ACCOUNT_SID, ACCOUNT_TOKEN, APPLICATION_SID
from temba.tests import TembaTest, MockResponse, MockTwilioClient, MockRequestValidator
from temba.orgs.models import FREE_PLAN
from temba.utils import dict_to_struct
from .tasks import check_channels_task


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

    def test_delegate_channels(self):

        # we don't support IVR yet
        self.assertFalse(self.org.supports_ivr())

        # add a delegate caller
        Channel.add_call_channel(self.org, self.user, self.tel_channel)

        # now we should be IVR capable
        self.assertTrue(self.org.supports_ivr())

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
        tigo = Channel.objects.create(name="Tigo", org=self.org, country='RW',
                                      channel_type='T', address="+250725551212",
                                      created_by=self.user, modified_by=self.user, secret="11111", gcm_id="456")

        # new contact on MTN should send with the MTN channel
        sms = self.send_message(['+250788382382'], "Sent to an MTN number")
        self.assertEquals(mtn, self.org.get_send_channel(contact_urn=sms.contact_urn))
        self.assertEquals(mtn, sms.channel)

        # new contact on Tigo should send with the Tigo channel
        sms = self.send_message(['+250728382382'], "Sent to a Tigo number")
        self.assertEquals(tigo, self.org.get_send_channel(contact_urn=sms.contact_urn))
        self.assertEquals(tigo, sms.channel)

        # now our MTN contact texts, the tigo number which should change their affinity
        sms = Msg.create_incoming(tigo, (TEL_SCHEME, "+250788382382"), "Send an inbound message to Tigo")
        self.assertEquals(tigo, sms.channel)
        self.assertEquals(tigo, self.org.get_send_channel(contact_urn=sms.contact_urn))
        self.assertEquals(tigo, ContactURN.objects.get(path='+250788382382').channel)

        # new contact on Airtel (some overlap) should send with the Tigo channel since it is newest
        sms = self.send_message(['+250738382382'], "Sent to a Airtel number")
        self.assertEquals(tigo, self.org.get_send_channel(contact_urn=sms.contact_urn))
        self.assertEquals(tigo, sms.channel)

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
        sms = self.send_message(['+250788382382'], "Sent to an MTN number, but with shortcode channels")
        self.assertEquals(tigo, sms.channel)
        self.assertEquals(tigo, self.org.get_send_channel(contact_urn=sms.contact_urn))

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

        msg = Msg.create_outgoing(self.org, self.user, (TEL_SCHEME, '+250738382382'), 'x' * 400)  # 400 chars long
        Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
        self.assertEqual(3, Msg.objects.get(pk=msg.id).msg_count)

        # Nexmo limit is 1600
        self.tel_channel.channel_type = 'NX'
        self.tel_channel.save()
        cache.clear()  # clear the channel from cache

        msg = Msg.create_outgoing(self.org, self.user, (TEL_SCHEME, '+250738382382'), 'y' * 400)
        Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))
        self.assertEqual(self.tel_channel, Msg.objects.get(pk=msg.id).channel)
        self.assertEqual(1, Msg.objects.get(pk=msg.id).msg_count)

    def test_ensure_normalization(self):
        self.tel_channel.country = 'RW'
        self.tel_channel.save()

        contact1 = self.create_contact("contact1", "0788111222")
        contact2 = self.create_contact("contact2", "+250788333444")
        contact3 = self.create_contact("contact3", "+18006927753")

        self.tel_channel.ensure_normalized_contacts()

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
        call = Call.create_call(self.tel_channel, "250788383385", timezone.now(), 5, 'mo', self.user)

        self.assertEqual(self.org, msg.org)
        self.assertEqual(self.tel_channel, msg.channel)
        self.assertEquals(1, Msg.get_messages(self.org).count())
        self.assertEquals(1, Call.get_calls(self.org).count())
        self.assertEquals(1, Broadcast.get_broadcasts(self.org).count())

        # start off in the pending state
        self.assertEquals('P', msg.status)

        response = self.fetch_protected(reverse('channels.channel_delete', args=[self.tel_channel.pk]), self.user)
        self.assertContains(response, 'Test Channel')

        response = self.fetch_protected(reverse('channels.channel_delete', args=[self.tel_channel.pk]),
                                        post_data=dict(remove=True), user=self.user)
        self.assertRedirect(response, reverse("orgs.org_home"))

        msg = Msg.objects.get(pk=msg.pk)
        self.assertIsNotNone(msg.channel)
        self.assertIsNone(msg.channel.gcm_id)
        self.assertIsNone(msg.channel.secret)
        self.assertEquals(self.org, msg.org)

        # queued messages for the channel should get marked as failed
        self.assertEquals('F', msg.status)

        call = Call.objects.get(pk=call.pk)
        self.assertIsNotNone(call.channel)
        self.assertIsNone(call.channel.gcm_id)
        self.assertIsNone(call.channel.secret)

        self.assertEquals(self.org, call.org)

        broadcast = Broadcast.objects.get(pk=msg.broadcast.pk)
        self.assertEquals(self.org, broadcast.org)

        # should still be considered that user's message, call and broadcast
        self.assertEquals(1, Msg.get_messages(self.org).count())
        self.assertEquals(1, Call.get_calls(self.org).count())
        self.assertEquals(1, Broadcast.get_broadcasts(self.org).count())

        # syncing this channel should result in a release
        post_data = dict(cmds=[dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="60", net="UMTS", pending=[], retry=[])])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # our response should contain a release
        self.assertHasCommand('rel', response)

        # create a channel
        channel = Channel.objects.create(name="Test Channel", address="0785551212", country='RW',
                                         org=self.org, created_by=self.user, modified_by=self.user,
                                         secret="12345", gcm_id="123")

        response = self.fetch_protected(reverse('channels.channel_delete', args=[channel.pk]), self.superuser)
        self.assertContains(response, 'Test Channel')

        response = self.fetch_protected(reverse('channels.channel_delete', args=[channel.pk]),
                                        post_data=dict(remove=True), user=self.superuser)
        self.assertRedirect(response, reverse("orgs.org_home"))

        # create a channel
        channel = Channel.objects.create(name="Test Channel", address="0785551212", country='RW',
                                         org=self.org, created_by=self.user, modified_by=self.user,
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
        self.assertRedirect(response, reverse('channels.channel_read', args=[self.tel_channel.id]))

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
        channel = Channel.objects.create(org=self.org, channel_type=ANDROID,
                                         address="+250781112222", gcm_id="asdf", secret="asdf",
                                         created_by=self.user, modified_by=self.user)
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
        msg = Msg.create_outgoing(self.org, self.user, (TEL_SCHEME, '250788123123'), "test")
        response = self.client.get('/', Follow=True)
        self.assertIn('delayed_syncevents', response.context)
        self.assertNotIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")

        # but put it in the past
        msg.delete()
        msg = Msg.create_outgoing(self.org, self.user, (TEL_SCHEME, '250788123123'), "test",
                                  created_on=timezone.now() - timedelta(hours=3))
        response = self.client.get('/', Follow=True)
        self.assertIn('delayed_syncevents', response.context)
        self.assertIn('unsent_msgs', response.context, msg="Found unsent_msgs in context")

        # if there is a successfully sent message after sms was created we do not consider it as delayed
        success_msg = Msg.create_outgoing(self.org, self.user, (TEL_SCHEME, '+250788123123'), "success-send",
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
        #self.assertIn('channel_type', response.context)

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
        channel.channel_type = TWILIO
        channel.save()

        response = self.client.get(update_url)
        self.assertFalse('address' in response.context['form'].fields)

        # bring it back to android
        channel.channel_type = ANDROID
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
        channel.channel_type = TWITTER
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
        response = self.client.get(reverse('channels.channel_read', args=[self.tel_channel.id]))
        self.assertLoginRedirect(response)

        # org users can
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.id]), self.user)

        self.assertEquals(len(response.context['source_stats']), len(SyncEvent.objects.values_list('power_source', flat=True).distinct()))
        self.assertEquals('AC',response.context['source_stats'][0][0])
        self.assertEquals(1,response.context['source_stats'][0][1])
        self.assertEquals('BAT',response.context['source_stats'][1][0])
        self.assertEquals(1,response.context['source_stats'][0][1])

        self.assertEquals(len(response.context['network_stats']), len(SyncEvent.objects.values_list('network_type', flat=True).distinct()))
        self.assertEquals('UMTS',response.context['network_stats'][0][0])
        self.assertEquals(1,response.context['network_stats'][0][1])
        self.assertEquals('WIFI',response.context['network_stats'][1][0])
        self.assertEquals(1,response.context['network_stats'][1][1])

        self.assertTrue(len(response.context['latest_sync_events']) <= 5)

        self.org.administrators.add(self.user)
        response = self.fetch_protected(reverse('orgs.org_home'), self.user)
        self.assertNotContains(response, 'Enable Voice')

        # Add twilio credentials to make sure we can add calling for our android channel
        twilio_config = {ACCOUNT_SID: 'SID', ACCOUNT_TOKEN: 'TOKEN', APPLICATION_SID: 'APP SID'}
        config = self.org.config_json()
        config.update(twilio_config)
        self.org.config = json.dumps(config)
        self.org.save(update_fields=['config'])

        response = self.fetch_protected(reverse('orgs.org_home'), self.user)
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
        msg = Msg.create_outgoing(self.org, self.user, (TEL_SCHEME, '250785551212'), 'delayed message', created_on=two_hours_ago)

        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.id]), self.user)
        self.assertIn('delayed_sync_event', response.context_data.keys())
        self.assertIn('unsent_msgs_count', response.context_data.keys())

        # with superuser
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.id]), self.superuser)
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
        Msg.create_incoming(self.tel_channel, (TEL_SCHEME, test_contact.get_urn().path), 'This incoming message will not be counted')
        Msg.create_outgoing(self.org, self.user, test_contact, 'This outgoing message will not be counted')

        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.id]), self.superuser)
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
        Msg.create_incoming(self.tel_channel, (TEL_SCHEME, joe.get_urn(TEL_SCHEME).path), 'This incoming message will be counted')
        Msg.create_outgoing(self.org, self.user, joe, 'This outgoing message will be counted')

        # now we have an inbound message and two outbounds
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.id]), self.superuser)
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
        Msg.create_incoming(self.tel_channel, (TEL_SCHEME, test_contact.get_urn().path), 'incoming ivr as a test contact', msg_type=IVR)
        Msg.create_outgoing(self.org, self.user, test_contact, 'outgoing ivr as a test contact', msg_type=IVR)
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.id]), self.superuser)

        # nothing should have changed
        self.assertEquals(2, len(response.context['message_stats']))

        self.assertEquals(1, len(response.context['message_stats_table']))
        self.assertEquals(1, response.context['message_stats_table'][0]['incoming_messages_count'])
        self.assertEquals(2, response.context['message_stats_table'][0]['outgoing_messages_count'])
        self.assertEquals(0, response.context['message_stats_table'][0]['incoming_ivr_count'])
        self.assertEquals(0, response.context['message_stats_table'][0]['outgoing_ivr_count'])

        # now let's create an ivr interaction from a real contact
        Msg.create_incoming(self.tel_channel, (TEL_SCHEME, joe.get_urn().path), 'incoming ivr', msg_type=IVR)
        Msg.create_outgoing(self.org, self.user, joe, 'outgoing ivr', msg_type=IVR)
        response = self.fetch_protected(reverse('channels.channel_read', args=[self.tel_channel.id]), self.superuser)

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
        ts = int(time.time()) - 60*16
        response = self.client.post("%s?signature=sig&ts=%d" % (reverse('sync', args=[self.tel_channel.pk]), ts), content_type='application/json')
        self.assertEquals(401, response.status_code)
        self.assertEquals(3, json.loads(response.content)['error_id'])

    def test_register_and_claim(self):
        self.org.administrators.add(self.user)
        self.user.set_org(self.org)
        post_data = json.dumps(dict(cmds=[dict(cmd="gcm", gcm_id="claim_test", uuid='uuid'), dict(cmd='status', cc='RW', dev='Nexus')]))

        # must be a post
        response = self.client.get(reverse('register'), content_type='application/json')
        self.assertEquals(500, response.status_code)

        # try a legit register
        response = self.client.post(reverse('register'), content_type='application/json', data=post_data)
        self.assertEquals(200, response.status_code)

        channel_object = Channel.objects.get(gcm_id="claim_test")
        self.assertEquals('RW', channel_object.country)
        self.assertEquals('Nexus', channel_object.device)
        channel = json.loads(response.content)['cmds'][0]
        self.assertEquals(channel['relayer_id'], channel_object.pk)

        response = self.client.post(reverse('register'), content_type='application/json', data=post_data)
        self.assertEquals(200, response.status_code)
        channel = json.loads(response.content)['cmds'][0]
        self.assertEquals(channel['relayer_id'], channel_object.pk)

        # try to claim with an invalid phone number
        response = self.fetch_protected(reverse('channels.channel_claim_android'), self.user,
                                        post_data=dict(claim_code=channel['relayer_claim_code'],
                                                       phone_number="078123"),
                                        failOnFormValidation=False)
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "Invalid phone number")

        # claim our channel
        response = self.fetch_protected(reverse('channels.channel_claim_android'), self.user,
                                        post_data=dict(claim_code=channel['relayer_claim_code'],
                                                       phone_number="0788123123"))

        # alert email should default to the currently logged in user
        new_channel = Channel.objects.get(org=self.org, address='+250788123123')
        self.assertEquals(self.user.email, new_channel.alert_email)
        self.assertTrue('success' in response.get('Location', None))
        self.assertRedirect(response, reverse('public.public_welcome'))

        # try having a device register again
        response = self.client.post(reverse('register'), content_type='application/json', data=post_data)
        self.assertEquals(200, response.status_code)

        # should be two channels with that gcm id
        self.assertEquals(2, Channel.objects.filter(gcm_id='claim_test').count())

        # but only one with an org
        active = Channel.objects.filter(gcm_id='claim_test').exclude(org=None)
        self.assertEquals(1, len(active))
        active = active[0]
        self.assertEquals(channel['relayer_id'], active.pk)
        self.assertEquals('+250788123123', active.address)

        # but if we claim our new one, we'll clear out our previous one
        new_channel = Channel.objects.get(gcm_id='claim_test', org=None)
        response = self.fetch_protected(reverse('channels.channel_claim_android'), self.user,
                                        post_data=dict(claim_code=new_channel.claim_code, phone_number="+250788123124"))
        self.assertRedirect(response, reverse('public.public_welcome'))

        channel = Channel.objects.get(gcm_id='claim_test', is_active=True)
        self.assertEquals(channel.pk, new_channel.pk)
        self.assertEquals('+250788123124', channel.address)

        # try to claim a bogus channel
        response = self.fetch_protected(reverse('channels.channel_claim_android'), self.user, post_data=dict(claim_code="Your Mom"), failOnFormValidation=False)
        self.assertEquals(200, response.status_code)
        self.assertContains(response, 'Invalid claim code')

        # check our primary tel channel is the same as our outgoing
        self.assertEquals(self.org.get_receive_channel(TEL_SCHEME), self.org.get_send_channel(TEL_SCHEME))
        self.assertFalse(self.org.get_send_channel(TEL_SCHEME).is_delegate_sender())

        channel = self.org.get_send_channel(TEL_SCHEME).pk
        # now claim a bulk sender
        self.fetch_protected("%s?connection=NX&channel=%d" % (reverse('channels.channel_create_bulk_sender'), channel),
                             self.user, post_data=dict(connection='NX', channel=channel), failOnFormValidation=False)

        # shouldn't work without a Nexmo account connected
        self.assertFalse(self.org.get_send_channel(TEL_SCHEME).is_delegate_sender())
        self.assertFalse(self.org.is_connected_to_nexmo())

        # now connect to nexmo
        with patch('temba.nexmo.NexmoClient.update_account') as connect:
            connect.return_value = True
            self.org.connect_nexmo('123', '456')
            self.org.save()
        self.assertTrue(self.org.is_connected_to_nexmo())

        # now adding our bulk sender should work
        self.fetch_protected("%s?connection=NX&channel=%d" % (reverse('channels.channel_create_bulk_sender'), channel),
                             self.user, post_data=dict(connection='NX', channel=channel))
        self.assertTrue(self.org.get_send_channel(TEL_SCHEME).is_delegate_sender())

        # now we should have a new outgoing sender
        self.assertNotEqual(self.org.get_receive_channel(TEL_SCHEME), self.org.get_send_channel(TEL_SCHEME))
        self.assertTrue(self.org.get_send_channel(TEL_SCHEME).is_delegate_sender())
        self.assertFalse(self.org.get_receive_channel(TEL_SCHEME).is_delegate_sender())

        # create a US channel and try claiming it next to our RW channels
        post_data = json.dumps(dict(cmds=[dict(cmd="gcm", gcm_id="claim_test", uuid='uuid'),
                                          dict(cmd='status', cc='US', dev='Nexus')]))
        response = self.client.post(reverse('register'), content_type='application/json', data=post_data)
        channel = json.loads(response.content)['cmds'][0]

        response = self.fetch_protected(reverse('channels.channel_claim_android'), self.user,
                                        post_data=dict(claim_code=channel['relayer_claim_code'],
                                                       phone_number="0788382382"),
                                        failOnFormValidation=False)
        self.assertEquals(200, response.status_code, "Claimed channels from two different countries")
        self.assertContains(response, "you can only add numbers for the same country")

        # but if we submit with a fully qualified Rwandan number it should work
        response = self.fetch_protected(reverse('channels.channel_claim_android'), self.user,
                                        post_data=dict(claim_code=channel['relayer_claim_code'],
                                                       phone_number="+250788382382"),
                                        failOnFormValidation=False)

        self.assertRedirect(response, reverse('public.public_welcome'))

        # should be added with RW as a country
        self.assertTrue(Channel.objects.get(address='+250788382382', country='RW', org=self.org))

        response = self.fetch_protected(reverse('channels.channel_claim'), self.user)
        self.assertEquals(200, response.status_code)
        self.assertEquals(response.context['twilio_countries'], "Belgium, Canada, Finland, Norway, Poland, Spain, "
                                                                "Sweden, United Kingdom or United States")

        # Test both old and new Cameroon phone format
        number = phonenumbers.parse('+23761234567', 'CM')
        self.assertTrue(phonenumbers.is_possible_number(number))
        number = phonenumbers.parse('+237661234567', 'CM')
        self.assertTrue(phonenumbers.is_possible_number(number))

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
                Channel.objects.get(channel_type='T', org=self.org)

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
                session[PLIVO_AUTH_ID] = 'auth-id'
                session[PLIVO_AUTH_TOKEN] = 'auth-token'
                session.save()

                self.assertTrue(PLIVO_AUTH_ID in self.client.session)
                self.assertTrue(PLIVO_AUTH_TOKEN in self.client.session)

                response = self.client.post(claim_plivo_url, dict(phone_number='+1 606-268-1435', country='US'))
                self.assertRedirects(response, reverse('public.public_welcome') + "?success")

                # make sure it is actually connected
                channel = Channel.objects.get(channel_type='PL', org=self.org)
                self.assertEquals(channel.config_json(), {PLIVO_AUTH_ID:'auth-id',
                                                          PLIVO_AUTH_TOKEN: 'auth-token',
                                                          PLIVO_APP_ID: 'app-id'})
                self.assertEquals(channel.address, "+16062681435")
                # no more credential in the session
                self.assertFalse(PLIVO_AUTH_ID in self.client.session)
                self.assertFalse(PLIVO_AUTH_TOKEN in self.client.session)

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

    def send_message(self, numbers, message, org=None, user=None):
        if not org:
            org = self.org

        if not user:
            user = self.user

        group = ContactGroup.get_or_create(org, user, 'Numbers: %s' % ','.join(numbers))
        contacts = list()
        for number in numbers:
            contacts.append(Contact.get_or_create(org, user, name=None, urns=[(TEL_SCHEME, number)]))

        group.contacts.add(*contacts)

        broadcast = Broadcast.create(org, user, message, [group])
        broadcast.send()

        sms = Msg.objects.filter(broadcast=broadcast).order_by('text', 'pk')
        if len(numbers) == 1:
            return sms.first()
        else:
            return list(sms)

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
        msg1 = self.send_message(['250788382382'], "How is it going?")

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

            # outgoing
            dict(cmd="call", phone="+250788383383", type='mo', dur=5, ts=date),

            # a new incoming message
            dict(cmd="mo_sms", phone="+250788383383", msg="This is giving me trouble", p_id="1", ts=date)])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # new batch, our ack and our claim command for new org
        self.assertEquals(2, len(json.loads(response.content)['cmds']))

        # check that our messages were updated accordingly
        self.assertEqual(2, Msg.objects.filter(channel=self.tel_channel, status='S', direction='O').count())
        self.assertEqual(1, Msg.objects.filter(channel=self.tel_channel, status='D', direction='O').count())
        self.assertEqual(1, Msg.objects.filter(channel=self.tel_channel, status='E', direction='O').count())
        self.assertEqual(1, Msg.objects.filter(channel=self.tel_channel, status='F', direction='O').count())

        # we should now have a new incoming message
        self.assertEqual(1, Msg.objects.filter(direction='I').count())

        # We should now have one sync
        self.assertEquals(1, SyncEvent.objects.filter(channel=self.tel_channel).count())

        # check our channel gcm and uuid were updated
        self.tel_channel = Channel.objects.get(pk=self.tel_channel.pk)
        self.assertEquals('12345', self.tel_channel.gcm_id)
        self.assertEquals('abcde', self.tel_channel.uuid)

        # set an email on our channel
        self.tel_channel.alert_email = 'fred@worldrelif.org'
        self.tel_channel.save()

        # We should not have an alert this time
        self.assertEquals(0, Alert.objects.all().count())

        # the case the status must be be reported
        post_data = dict(cmds=[
                # device details status
                dict(cmd="status", p_sts="DIS", p_src="BAT", p_lvl="20", net="UMTS", retry=[], pending=[])])


        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # we should now have an Alert
        self.assertEquals(1, Alert.objects.all().count())

        # and at this time it must be not ended
        self.assertEquals(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # the case the status must be be reported but already notification sent
        post_data = dict(cmds=[
                # device details status
                dict(cmd="status", p_sts="DIS", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # we should not create a new alert
        self.assertEquals(1, Alert.objects.all().count())

        # still not ended
        self.assertEquals(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # Let plug the channel to charger
        post_data = dict(cmds=[
                # device details status
                dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])])

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
                dict(cmd="status", p_sts="UNK", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # we should now create a new alert
        self.assertEquals(1, Alert.objects.all().count())

        # one alert not ended
        self.assertEquals(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # Let plug the channel to charger to end this unknown power status
        post_data = dict(cmds=[

                # device details status
                dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])])

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
                dict(cmd="status", p_sts="NOT", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])])

        # now send the channel's updates
        response = self.sync(self.tel_channel, post_data)

        # we should now create a new alert
        self.assertEquals(1, Alert.objects.all().count())

        # one alert not ended
        self.assertEquals(1, Alert.objects.filter(sync_event__channel=self.tel_channel, ended_on=None, alert_type='P').count())

        # Let plug the channel to charger to end this unknown power status
        post_data = dict(cmds=[

                # device details status
                dict(cmd="status", p_sts="CHA", p_src="BAT", p_lvl="15", net="UMTS", pending=[], retry=[])])

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
            dict(cmd="mo_sms", phone="2505551212", msg="A second message", p_id="3", ts=date)])

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
        self.assertEqual(2, Msg.objects.filter(direction='I').count())

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

class SyncEventTest(SmartminTest):

    def setUp(self):
        self.superuser = User.objects.create_superuser(username="super", email="super@user.com", password="super")
        self.user = self.create_user("tito")
        self.org = Org.objects.create(name="Temba", timezone="Africa/Kigali", created_by=self.user, modified_by=self.user)
        self.tel_channel = Channel.objects.create(name="Test Channel", address="0785551212", org=self.org,
                                                  created_by=self.user, modified_by=self.user, country='RW',
                                                  secret="12345", gcm_id="123")

    def test_sync_event_model(self):
        self.sync_event = SyncEvent.create(self.tel_channel, dict(p_src="AC", p_sts="DIS", p_lvl=80, net="WIFI",
                                                                  pending=[1, 2], retry=[3, 4], cc='RW'), [1,2])
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

    def test_external(self):
        from temba.channels.models import EXTERNAL

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
        self.assertEquals(EXTERNAL, channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('api.external_handler', args=['sent', channel.uuid]))
        self.assertContains(response, reverse('api.external_handler', args=['delivered', channel.uuid]))
        self.assertContains(response, reverse('api.external_handler', args=['failed', channel.uuid]))
        self.assertContains(response, reverse('api.external_handler', args=['received', channel.uuid]))

        # test substitution in our url
        self.assertEquals('http://test.com/send.php?from=5080&text=test&to=%2B250788383383',
                          channel.build_send_url(url, { 'from':"5080", 'text':"test", 'to':"+250788383383" }))

        # test substitution with unicode
        self.assertEquals('http://test.com/send.php?from=5080&text=Reply+%E2%80%9C1%E2%80%9D+for+good&to=%2B250788383383',
                          channel.build_send_url(url, { 'from':"5080", 'text':u"Reply “1” for good", 'to':"+250788383383" }))

    def test_clickatell(self):
        from temba.channels.models import CLICKATELL

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
        self.assertEquals(CLICKATELL, channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('api.clickatell_handler', args=['status', channel.uuid]))
        self.assertContains(response, reverse('api.clickatell_handler', args=['receive', channel.uuid]))

    def test_high_connection(self):
        from temba.channels.models import HIGH_CONNECTION

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
        self.assertEquals(HIGH_CONNECTION, channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('api.hcnx_handler', args=['receive', channel.uuid]))

    def test_shaqodoon(self):
        from temba.channels.models import SHAQODOON

        Channel.objects.all().delete()

        self.login(self.admin)

        # try to claim a channel
        response = self.client.get(reverse('channels.channel_claim_shaqodoon'))
        post_data = response.context['form'].initial

        url = 'http://test.com/send.php'

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
        self.assertEquals(SHAQODOON, channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('api.shaqodoon_handler', args=['received', channel.uuid]))

    def test_kannel(self):
        from temba.channels.models import KANNEL
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
        post_data['encoding'] = SMART_ENCODING

        response = self.client.post(reverse('channels.channel_claim_kannel'), post_data)

        channel = Channel.objects.get()

        self.assertEquals('RW', channel.country)
        self.assertTrue(channel.uuid)
        self.assertEquals(post_data['number'], channel.address)
        self.assertEquals(post_data['url'], channel.config_json()['send_url'])
        self.assertEquals(False, channel.config_json()['verify_ssl'])
        self.assertEquals(SMART_ENCODING, channel.config_json()[ENCODING])

        # make sure we generated a username and password
        self.assertTrue(channel.config_json()['username'])
        self.assertTrue(channel.config_json()['password'])
        self.assertEquals(KANNEL, channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        # our configuration page should list our receive URL
        self.assertContains(response, reverse('api.kannel_handler', args=['receive', channel.uuid]))

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

        self.assertContains(response, reverse('api.zenvia_handler', args=['status', channel.uuid]))
        self.assertContains(response, reverse('api.zenvia_handler', args=['receive', channel.uuid]))

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

        self.assertContains(response, reverse('api.africas_talking_handler', args=['callback', channel.uuid]))
        self.assertContains(response, reverse('api.africas_talking_handler', args=['delivery', channel.uuid]))

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
        self.assertEquals(ALERT_DISCONNECTED, alert.alert_type)
        self.assertFalse(alert.ended_on)

        self.assertTrue(len(mail.outbox) == 1)
        template = 'channels/email/disconnected_alert.txt'
        branding = BrandingMiddleware.get_branding_for_host(settings.HOSTNAME)
        context = dict(org=self.channel.org, channel=self.channel, now=timezone.now(),
                       branding=branding,
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
        branding = BrandingMiddleware.get_branding_for_host(settings.HOSTNAME)
        context = dict(org=self.channel.org, channel=self.channel, now=timezone.now(),
                       branding=branding,
                       last_seen=self.channel.last_seen, sync=alert.sync_event)

        text_template = loader.get_template(template)
        text = text_template.render(Context(context))

        self.assertEquals(mail.outbox[1].body, text)

    def test_m3tech(self):
        from temba.channels.models import M3TECH

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
        self.assertEquals(M3TECH, channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('api.m3tech_handler', args=['received', channel.uuid]))
        self.assertContains(response, reverse('api.m3tech_handler', args=['sent', channel.uuid]))
        self.assertContains(response, reverse('api.m3tech_handler', args=['failed', channel.uuid]))
        self.assertContains(response, reverse('api.m3tech_handler', args=['delivered', channel.uuid]))

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

        self.assertContains(response, reverse('api.infobip_handler', args=['received', channel.uuid]))
        self.assertContains(response, reverse('api.infobip_handler', args=['delivered', channel.uuid]))

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
        self.assertEquals(ALERT_SMS, alert.alert_type)
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
        self.assertEquals(ALERT_SMS, alert.alert_type)
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
        Msg.create_incoming(None, (TEL_SCHEME, '+250788111222'), "Test Message", org=self.org)

        # still no channel counts
        self.assertFalse(ChannelCount.objects.all())

        # incoming msg with a channel
        msg = Msg.create_incoming(self.channel, (TEL_SCHEME, '+250788111222'), "Test Message", org=self.org)
        self.assertDailyCount(self.channel, 1, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        # delete it, back to 0
        msg.delete()
        self.assertDailyCount(self.channel, 0, ChannelCount.INCOMING_MSG_TYPE, msg.created_on.date())

        ChannelCount.objects.all().delete()

        # ok, test outgoing now
        real_contact = Contact.get_or_create(self.org, self.admin, urns=[(TEL_SCHEME, '+250788111222')])
        msg = Msg.create_outgoing(self.org, self.admin, real_contact, "Real Message", channel=self.channel)
        self.assertDailyCount(self.channel, 1, ChannelCount.OUTGOING_MSG_TYPE, msg.created_on.date())

        # delete it, should be gone now
        msg.delete()
        self.assertDailyCount(self.channel, 0, ChannelCount.OUTGOING_MSG_TYPE, msg.created_on.date())

        ChannelCount.objects.all().delete()

        # incoming IVR
        msg = Msg.create_incoming(self.channel, (TEL_SCHEME, '+250788111222'),
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


