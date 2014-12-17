from __future__ import unicode_literals
import hashlib

import json
import os
import phonenumbers
import requests

from datetime import timedelta
from django.contrib.auth.models import User, Group
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models import Q, Max
from django.db.models.signals import pre_save
from django.conf import settings
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _
from django.dispatch import receiver
from django_countries.fields import CountryField
from django.core.cache import cache
from gcm.gcm import GCM, GCMNotRegisteredException
from phonenumbers import NumberParseException
from redis_cache import get_redis_connection
from smartmin.models import SmartModel
from temba.nexmo import NexmoClient
from temba.orgs.models import Org, OrgLock, APPLICATION_SID, NEXMO_UUID
from temba.temba_email import send_temba_email
from temba.utils import analytics, random_string, dict_to_struct, dict_to_json
from twilio.rest import TwilioRestClient
from twython import Twython
from uuid import uuid4
from urllib import quote_plus

AFRICAS_TALKING = 'AT'
ANDROID = 'A'
EXTERNAL = 'EX'
HUB9 = 'H9'
INFOBIP = 'IB'
KANNEL = 'KN'
NEXMO = 'NX'
TWILIO = 'T'
TWITTER = 'TT'
VUMI = 'VM'
ZENVIA = 'ZV'
SHAQODOON = 'SQ'
VERBOICE = 'VB'

SEND_URL = 'send_url'
SEND_METHOD = 'method'
USERNAME = 'username'
PASSWORD = 'password'
KEY = 'key'

SEND = 'S'
RECEIVE = 'R'
CALL = 'C'
ANSWER = 'A'

RELAYER_TYPE_CHOICES = ((ANDROID, _("Android")),
                        (TWILIO, _("Twilio")),
                        (AFRICAS_TALKING, _("Africa's Talking")),
                        (ZENVIA, _("Zenvia")),
                        (NEXMO, _("Nexmo")),
                        (INFOBIP, _("Infobip")),
                        (VERBOICE, _("Verboice")),
                        (HUB9, _("Hub9")),
                        (VUMI, _("Vumi")),
                        (KANNEL, _("Kannel")),
                        (EXTERNAL, _("External")),
                        (TWITTER, _("Twitter")),
                        (SHAQODOON, _("Shaqodoon")))

# how many outgoing messages we will queue at once
SEND_QUEUE_DEPTH = 500

# how big each batch of outgoing messages can be
SEND_BATCH_SIZE = 100

RELAYER_TYPE_CONFIG = {
    ANDROID: dict(scheme='tel', max_length=-1),
    TWILIO: dict(scheme='tel', max_length=1600),
    AFRICAS_TALKING: dict(scheme='tel', max_length=160),
    ZENVIA: dict(scheme='tel', max_length=150),
    EXTERNAL: dict(scheme='tel', max_length=160),
    NEXMO: dict(scheme='tel', max_length=1600),
    INFOBIP: dict(scheme='tel', max_length=1600),
    VERBOICE: dict(scheme='tel', max_length=1600),
    VUMI: dict(scheme='tel', max_length=1600),
    KANNEL: dict(scheme='tel', max_length=1600),
    HUB9: dict(scheme='tel', max_length=1600, send_batch_size=100, send_queue_depth=100),
    TWITTER: dict(scheme='twitter', max_length=140),
    SHAQODOON: dict(scheme='tel', max_length=1600),
}

TEMBA_HEADERS = {'User-agent': 'RapidPro'}

# Some providers need a static ip to whitelist, route them through our proxy
proxies = {"http": "http://proxy.rapidpro.io:3128"}


class Channel(SmartModel):
    channel_type = models.CharField(verbose_name=_("Channel Type"), max_length=3, choices=RELAYER_TYPE_CHOICES,
                                    default=ANDROID, help_text=_("Type of this channel, whether Android, Twilio or SMSC"))
    name = models.CharField(verbose_name=_("Name"), max_length=64, blank=True, null=True,
                            help_text=_("Descriptive label for this channel"))
    address = models.CharField(verbose_name=_("Address"), max_length=16, blank=True, null=True,
                               help_text=_("Address with which this channel communicates"))
    country = CountryField(verbose_name=_("Country"), null=True, blank=True,
                           help_text=_("Country which this channel is for"))
    org = models.ForeignKey(Org, verbose_name=_("Org"), related_name="channels", blank=True, null=True,
                            help_text=_("Organization using this channel"))
    gcm_id = models.CharField(verbose_name=_("GCM ID"), max_length=255, blank=True, null=True,
                              help_text=_("The registration id for using Google Cloud Messaging"))
    uuid = models.CharField(verbose_name=_("UUID"), max_length=36, blank=True, null=True, db_index=True,
                            help_text=_("UUID for this channel"))
    claim_code = models.CharField(verbose_name=_("Claim Code"), max_length=16, blank=True, null=True, unique=True,
                                  help_text=_("The token the user will us to claim this channel"))
    secret = models.CharField(verbose_name=_("Secret"), max_length=64, blank=True, null=True, unique=True,
                              help_text=_("The secret token this channel should use when signing requests"))
    last_seen = models.DateTimeField(verbose_name=_("Last Seen"), auto_now_add=True,
                                     help_text=_("The last time this channel contacted the server"))
    device = models.CharField(verbose_name=_("Device"), max_length=255, null=True, blank=True,
                              help_text=_("The type of Android device this channel is running on"))
    os = models.CharField(verbose_name=_("OS"), max_length=255, null=True, blank=True,
                          help_text=_("What Android OS version this channel is running on"))
    alert_email = models.EmailField(verbose_name=_("Alert Email"), null=True, blank=True,
                                    help_text=_("We will send email alerts to this address if experiencing issues sending"))
    config = models.TextField(verbose_name=_("Config"), null=True,
                              help_text=_("Any channel specific configuration, used for the various aggregators"))
    role = models.CharField(verbose_name="Channel Role", max_length=4, default=SEND+RECEIVE,
                            help_text=_("The roles this channel can fulfill"))
    parent = models.ForeignKey('self', blank=True, null=True,
                               help_text=_("The channel this channel is working on behalf of"))

    def get_scheme(self):
        return RELAYER_TYPE_CONFIG[self.channel_type]['scheme']

    @classmethod
    def types_for_scheme(cls, scheme):
        """
        Gets the channel types which support the given scheme
        """
        return [t for t, config in RELAYER_TYPE_CONFIG.iteritems() if config['scheme'] == scheme]

    @classmethod
    def derive_country_from_phone(cls, phone):
        """
        Given a phone number in E164 returns the two letter country code for it.  ex: +250788383383 -> RW
        """
        try:
            parsed = phonenumbers.parse(phone, None)
            return phonenumbers.region_code_for_number(parsed)
        except:
            return None

    @classmethod
    def add_authenticated_external_channel(cls, org, user, country, phone_number, username, password, channel_type):
        try:
            parsed = phonenumbers.parse(phone_number, None)
            phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        except:
            # this is a shortcode, just use it plain
            phone = phone_number

        config = dict(username=username, password=password)

        return Channel.objects.create(channel_type=channel_type, country=country, name=phone,
                                      address=phone_number, uuid=uuid4(), config=json.dumps(config),
                                      org=org, created_by=user, modified_by=user)

    @classmethod
    def add_config_external_channel(cls, org, user, country, phone_number, channel_type, config, role=SEND+RECEIVE, parent=None):
        return Channel.objects.create(channel_type=channel_type, country=country, name=phone_number,
                                      address=phone_number, uuid=str(uuid4()), config=json.dumps(config),
                                      role=role, parent=parent,
                                      org=org, created_by=user, modified_by=user)


    @classmethod
    def add_nexmo_channel(cls, org, user, country, phone_number):
        client = org.get_nexmo_client()
        org_uuid = org.config_json().get(NEXMO_UUID)

        nexmo_phones = client.get_numbers(phone_number)
        is_shortcode = False

        # try it with just the national code (for short codes)
        if not nexmo_phones:
            parsed = phonenumbers.parse(phone_number, None)
            shortcode = str(parsed.national_number)
            nexmo_phones = client.get_numbers(shortcode)

            if nexmo_phones:
                is_shortcode = True
                phone_number = shortcode

        # buy the number if we have to
        if not nexmo_phones:
            try:
                client.buy_number(country, phone_number)
            except Exception as e:
                    raise Exception(_("There was a problem claiming that number, please check the balance on your account. " +
                                      "Note that you can only claim numbers after adding credit to your Nexmo account.") + "\n" +
                                      str(e))

        mo_path = reverse('api.nexmo_handler', args=['receive', org_uuid])

        # update the delivery URLs for it
        from temba.settings import TEMBA_HOST
        try:
            client.update_number(country, phone_number,
                               'http://%s%s' % (TEMBA_HOST, mo_path))

        except Exception as e:
            # shortcodes don't seem to claim right on nexmo, move forward anyways
            if not is_shortcode:
                raise Exception(_("There was a problem claiming that number, please check the balance on your account.") +
                                "\n" + str(e))

        if is_shortcode:
            phone = phone_number
            nexmo_phone_number = phone_number
        else:
            parsed = phonenumbers.parse(phone_number, None)
            phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)

            # nexmo ships numbers around as E164 without the leading +
            nexmo_phone_number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164).strip('+')

        return Channel.objects.create(channel_type=NEXMO, country=country,
                                      name=phone, address=phone_number, uuid=nexmo_phone_number,
                                      org=org, created_by=user, modified_by=user)

    @classmethod
    def add_twilio_channel(cls, org, user, phone_number, country):
        client = org.get_twilio_client()
        twilio_phones = client.phone_numbers.list(phone_number=phone_number)
        
        config = org.config_json()
        application_sid = config.get(APPLICATION_SID)

        # make sure our application id still exists on this account
        exists = False
        for app in client.applications.list():
            if app.sid == application_sid:
                exists = True
                break

        if not exists:
            raise Exception(_("Your Twilio account is no longer connected. First remove your Twilio account, reconnect it and try again."))

        if twilio_phones:
            twilio_phone = twilio_phones[0]
            client.phone_numbers.update(twilio_phone.sid,
                                        voice_application_sid=application_sid,
                                        sms_application_sid=application_sid)
                                        
        else:
            twilio_phone = client.phone_numbers.purchase(phone_number=phone_number,
                                                         voice_application_sid=application_sid,
                                                         sms_application_sid=application_sid)

        phone = phonenumbers.format_number(phonenumbers.parse(phone_number, None),
                                           phonenumbers.PhoneNumberFormat.NATIONAL)

        return Channel.objects.create(channel_type=TWILIO, country=country,
                                      name=phone, address=phone_number, uuid=twilio_phone.sid,
                                      org=org, created_by=user, modified_by=user, role=SEND+RECEIVE+CALL+ANSWER)

    @classmethod
    def add_africas_talking_channel(cls, org, user, phone, username, api_key):
        config = dict(username=username,
                      api_key=api_key)

        return Channel.objects.create(channel_type=AFRICAS_TALKING, country='KE',
                                      name="Africa's Talking: %s" % phone, address=phone, uuid=str(uuid4()),
                                      config=json.dumps(config),
                                      org=org, created_by=user, modified_by=user)

    @classmethod
    def add_zenvia_channel(cls, org, user, phone, account, code):
        config = dict(account=account, code=code)

        return Channel.objects.create(channel_type=ZENVIA, country='BR',
                                      name="Zenvia: %s" % phone, address=phone, uuid=str(uuid4()),
                                      config=json.dumps(config),
                                      org=org, created_by=user, modified_by=user)

    @classmethod
    def add_send_channel(cls, user, channel):
        # nexmo ships numbers around as E164 without the leading +
        parsed = phonenumbers.parse(channel.address, None)
        nexmo_phone_number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164).strip('+')
        return Channel.objects.create(name="Nexmo Sender", channel_type=NEXMO, address=channel.address,
                                      uuid=nexmo_phone_number, country=channel.country,
                                      created_by=user, modified_by=user,
                                      role=SEND, org=user.get_org(), parent=channel)

    @classmethod
    def add_call_channel(cls, org, user, channel):
        return Channel.objects.create(channel_type=TWILIO, country=channel.country,
                                      name="Twilio Caller", address=channel.address, org=org, created_by=user,
                                      modified_by=user, role=CALL, parent=channel)

    @classmethod
    def add_twitter_channel(cls, org, user, screen_name, handle_id, oauth_token, oauth_token_secret):
        config = json.dumps(dict(handle_id=long(handle_id),
                                 oauth_token=oauth_token,
                                 oauth_token_secret=oauth_token_secret))

        with org.lock_on(OrgLock.channels):
            channel = Channel.objects.filter(org=org, channel_type=TWITTER, address=screen_name, is_active=True).first()
            if channel:
                channel.config = config
                channel.modified_by = user
                channel.save()
            else:
                channel = Channel.objects.create(channel_type=TWITTER, address=screen_name,
                                                 org=org, role=SEND+RECEIVE, uuid=str(uuid4()),
                                                 config=config, name="Twitter", created_by=user, modified_by=user)

                # notify Mage so that it receives messages for this channel
                from .tasks import notify_mage_task
                notify_mage_task.delay(channel.uuid, 'add')

        return channel

    @classmethod
    def from_gcm_and_status_cmds(cls, gcm, status):
        # gcm command must be the first one
        gcm_id = gcm['gcm_id']
        uuid = gcm.get('uuid', None)

        country = status['cc']
        device = status['dev']

        # look for any unclaimed channel
        existing = Channel.objects.filter(gcm_id=gcm_id, uuid=uuid, org=None, is_active=True)
        if existing:
            return existing[0]
        else:
            secret = random_string(64)
            claim_code = random_string(9)
            while Channel.objects.filter(claim_code=claim_code): # pragma: no cover
                claim_code = random_string(9)
            anon = User.objects.get(pk=-1)

            return Channel.objects.create(gcm_id=gcm_id,
                                          uuid=uuid,
                                          country=country,
                                          device=device,
                                          claim_code=claim_code,
                                          secret=secret,
                                          created_by=anon,
                                          modified_by=anon)

    def has_sending_log(self):
        return self.channel_type != 'A'

    def has_configuration_page(self):
        """
        Whether or not this channel supports a configuration/settings page
        """
        return self.channel_type not in ('T', 'A', 'TT')

    def get_delegate_channels(self):
        if not self.org:  # detached channels can't have delegates
            return Channel.objects.none()

        return self.org.channels.filter(parent=self, is_active=True, org=self.org).order_by('-role')

    def get_delegate(self, role):
        """
        Get the channel that should perform a given action. Could just be us
        (the same channel), but may be a delegate channel working on our behalf.
        """
        if self.role == role:
            delegate = self
        else:
            # if we have a delegate channel for this role, use that
            delegate = self.get_delegate_channels().filter(role=role).first()

        if not delegate and role in self.role:
            delegate = self

        return delegate

    def get_sender(self):
        return self.get_delegate(SEND)

    def get_caller(self):
        return self.get_delegate(CALL)

    def get_parent_channel(self):
        """
        If we are a delegate channel, this will get us the parent channel.
        Otherwise, it will just return ourselves if we are the parent channel
        """
        if self.parent:
            return self.parent
        return self

    def is_delegate_sender(self):
        return self.parent and SEND in self.role

    def is_delegate_caller(self):
        return self.parent and CALL in self.role

    def get_ivr_client(self):
        if self.channel_type == TWILIO:
            return self.org.get_twilio_client()
        if self.channel_type == VERBOICE:
            return self.org.get_verboice_client()
        return None

    def ensure_normalized_contacts(self):
        from temba.contacts.models import ContactURN
        urns = ContactURN.objects.filter(org=self.org, path__startswith="+")
        for urn in urns:
            urn.ensure_number_normalization(self)

    def supports_ivr(self):
        return CALL in self.role or ANSWER in self.role

    def get_name(self):  # pragma: no cover
        if self.name:
            return self.name
        elif self.device:
            return self.device
        else:
            return _("Android Phone")

    def get_channel_type_name(self):
        channel_type_display = self.get_channel_type_display()

        if self.channel_type == ANDROID:
            return _("Android Phone")
        else:
            return _("%s Channel" % channel_type_display)

    def get_address_display(self, e164=False):
        from temba.contacts.models import TEL_SCHEME
        if not self.address:
            return ''

        if self.address and self.get_scheme() == TEL_SCHEME and self.country:
            # assume that a number not starting with + is a short code and return as is
            if self.address[0] != '+':
                return self.address

            try:
                normalized = phonenumbers.parse(self.address, str(self.country))
                fmt = phonenumbers.PhoneNumberFormat.E164 if e164 else phonenumbers.PhoneNumberFormat.INTERNATIONAL
                return phonenumbers.format_number(normalized, fmt)
            except NumberParseException as e:
                # the number may be alphanumeric in the case of short codes
                pass

        return self.address

    def build_message_context(self):
        from temba.contacts.models import TEL_SCHEME

        address = self.get_address_display()
        default = address if address else self.__unicode__()

        # for backwards compatibility
        if self.get_scheme() == TEL_SCHEME:
            tel = address
            tel_e164 = self.get_address_display(e164=True)
        else:
            tel = ''
            tel_e164 = ''

        return dict(__default__=default, name=self.get_name(), address=address, tel=tel, tel_e164=tel_e164)

    def config_json(self):
        if self.config:
            return json.loads(self.config)
        else:
            return dict()

    @classmethod
    def get_cached_channel(cls, channel_id):
        """
        Fetches this channel's configuration from our cache, also populating it with the channel uuid
        """
        key = 'channel_config:%d' % channel_id
        cached = cache.get(key, None)

        if cached is None:
            channel = Channel.objects.filter(pk=channel_id).exclude(org=None).first()

            # channel has been disconnected, ignore
            if not channel:
                return None
            else:
                cached = channel.as_cached_json()
                cache.set(key, dict_to_json(cached), 900)
        else:
            cached = json.loads(cached)

        return dict_to_struct('ChannelStruct', cached)

    @classmethod
    def clear_cached_channel(cls, channel_id):
        key = 'channel_config:%d' % channel_id
        cache.delete(key)

    def as_cached_json(self):
        # also save our org config, as it has twilio and nexmo keys
        org_config = self.org.config_json()

        return dict(id=self.id, org=self.org_id, country=str(self.country), address=self.address, uuid=self.uuid,
                    secret=self.secret, channel_type=self.channel_type, name=self.name, config=self.config_json(),
                    org_config=org_config)

    def build_registration_command(self):
        # create a claim code if we don't have one
        if not self.claim_code:
            self.claim_code = random_string(9)
            while Channel.objects.filter(claim_code=self.claim_code): # pragma: no cover
                self.claim_code = random_string(9)
            self.save()

        # create a secret if we don't have one
        if not self.secret:
            self.secret = random_string(64)
            self.save()

        # return our command
        return dict(cmd='reg',
                    relayer_claim_code=self.claim_code,
                    relayer_secret=self.secret,
                    relayer_id=self.id)

    def get_latest_sent_message(self):
        # all message states that are successfully sent
        messages = self.msgs.filter(status__in=['S', 'D']).exclude(sent_on=None).order_by('-sent_on')

        # only outgoing messages
        messages = messages.filter(direction='O')

        latest_message = None
        if messages:
            latest_message = messages[0]

        return latest_message

    def get_delayed_outgoing_messages(self):
        messages = self.get_unsent_messages()
        latest_sent_message = self.get_latest_sent_message()

        # ignore really recent unsent messages
        messages = messages.exclude(created_on__gt=timezone.now() - timedelta(hours=1))
        
        # if there is one message successfully sent ignore also all message created before it was sent 
        if latest_sent_message:
            messages = messages.exclude(created_on__lt=latest_sent_message.sent_on)        
        
        return messages

    def get_recent_syncs(self):
        return self.syncevent_set.filter(created_on__gt=timezone.now() - timedelta(hours=1)).order_by('-created_on')

    def get_last_sync(self):
        if not hasattr(self, '_last_sync'):
            last_sync = self.syncevent_set.order_by('-created_on').first()

            self._last_sync = last_sync

        return self._last_sync

    def get_last_power(self):
        last = self.get_last_sync()
        return last.power_level if last else -1

    def get_last_power_status(self):
        last = self.get_last_sync()
        return last.power_status if last else None

    def get_last_power_source(self):
        last = self.get_last_sync()
        return last.power_source if last else None

    def get_last_network_type(self):
        last = self.get_last_sync()
        return last.network_type if last else None

    def get_unsent_messages(self):
        # all message states that are incomplete
        messages = self.msgs.filter(status__in=['P', 'Q'])

        # only outgoing messages on real contacts
        messages = messages.filter(direction='O', contact__is_test=False)
        return messages

    def is_new(self):
        # is this channel newer than an hour
        return self.created_on > timezone.now() - timedelta(hours=1) or not self.get_last_sync()

    def claim(self, org, phone, user):
        if not self.country:
            self.country = Channel.derive_country_from_phone(phone)

        self.alert_email = user.email
        self.org = org
        self.is_active = True
        self.claim_code = None
        self.address = phone
        self.save()

    def release(self, trigger_sync=True, notify_mage=True):

        org = self.org

        # release any channels working on our behalf as well
        for delegate_channel in Channel.objects.filter(parent=self, org=self.org):
            delegate_channel.release()

        # if we are a twilio channel, remove our sms application from twilio to handle the incoming sms
        if self.channel_type == TWILIO:
            client = self.org.get_twilio_client()
            number_update_args = dict()

            if not self.is_delegate_sender():
                number_update_args['sms_application_sid'] = ""

            if self.supports_ivr():
                number_update_args['voice_application_sid'] = ""

            try:
                client.phone_numbers.update(self.uuid,
                                            **number_update_args)

            except Exception as e:
                if client:
                    matching = client.phone_numbers.list(phone_number=self.address)
                    if matching:
                        client.phone_numbers.update(matching[0].sid,
                                                    **number_update_args)

        # save off our gcm id so we can trigger a sync
        gcm_id = self.gcm_id

        # remove all identifying bits from the client
        self.org = None
        self.gcm_id = None
        self.secret = None
        self.claim_code = None
        self.is_active = False
        self.save()

        # mark any messages in sending mode as failed for this channel
        from temba.msgs.models import Msg
        Msg.objects.filter(channel=self, status__in=['Q', 'P', 'E']).update(status='F')

        # trigger the orphaned channel
        if trigger_sync and self.channel_type == ANDROID: # pragma: no cover
            self.trigger_sync(gcm_id)

        # clear our cache for this channel
        Channel.clear_cached_channel(self.id)

        if notify_mage and self.channel_type == TWITTER:
            # notify Mage so that it stops receiving messages for this channel
            from .tasks import notify_mage_task
            notify_mage_task.delay(self.uuid, 'remove')

        # if we just lost calling capabilities archive our voice flows
        if CALL in self.role:
            if not org.get_schemes(CALL):
                # archive any IVR flows
                from temba.flows.models import Flow
                for flow in Flow.objects.filter(org=org, flow_type=Flow.VOICE):
                    flow.archive()

        # if we just lost answering capabilities, archive our inbound call trigger
        if ANSWER in self.role:
            if not org.get_schemes(ANSWER):
                from temba.triggers.models import Trigger, INBOUND_CALL_TRIGGER
                Trigger.objects.filter(trigger_type=INBOUND_CALL_TRIGGER, org=org, is_archived=False).update(is_archived=True)

    def trigger_sync(self, gcm_id=None):  # pragma: no cover
        """
        Sends a GCM command to trigger a sync on the client
        """
        # androids sync via GCM
        if self.channel_type == ANDROID:
            if getattr(settings, 'GCM_API_KEY', None):
                from .tasks import sync_channel_task
                if not gcm_id: gcm_id = self.gcm_id
                if gcm_id:
                    sync_channel_task.delay(gcm_id, channel_id=self.pk)

        # otherwise this is an aggregator, no-op
        else:
            raise Exception("Trigger sync called on non Android channel. [%d]" % self.pk)

    @classmethod
    def sync_channel(cls, gcm_id, channel=None): # pragma: no cover
        try:
            gcm = GCM(settings.GCM_API_KEY)
            gcm.plaintext_request(registration_id=gcm_id, data=dict(msg='sync'))
        except GCMNotRegisteredException as e:
            if channel:
                # this gcm id is invalid now, clear it out
                channel.gcm_id = None
                channel.save()

    @classmethod
    def build_send_url(cls, url, variables):
        for key in variables.keys():
            url = url.replace("{{%s}}" % key, quote_plus(variables[key].encode('utf-8')))

        return url

    @classmethod
    def send_kannel_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED

        # build our callback dlr url, kannel will call this when our message is sent or delivered
        dlr_url = 'https://%s%s?id=%d&status=%%d' % (settings.HOSTNAME, reverse('api.kannel_handler', args=['status', channel.uuid]), msg.id)
        dlr_mask = 31

        # build our payload
        payload = dict()
        payload['from'] = channel.address
        payload['username'] = channel.config[USERNAME]
        payload['password'] = channel.config[PASSWORD]
        payload['text'] = text
        payload['to'] = msg.urn_path
        payload['dlr-url'] = dlr_url
        payload['dlr-mask'] = dlr_mask

        log_payload = payload.copy()
        log_payload['password'] = 'x' * len(log_payload['password'])

        log_url = channel.config[SEND_URL]
        if log_url.find("?") >= 0:
            log_url += "&" + urlencode(log_payload)
        else:
            log_url += "?" + urlencode(log_payload)

        try:
            response = requests.get(channel.config[SEND_URL], params=payload)
        except Exception as e:
            payload['password'] = 'x' * len(payload['password'])
            raise SendException(unicode(e),
                                method='GET',
                                url=log_url,
                                request="",
                                response="",
                                response_status=503)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from Kannel" % response.status_code,
                                method='GET',
                                url=log_url,
                                request="",
                                response=response.text,
                                response_status=response.status_code)

        Msg.mark_sent(channel.config['r'], msg, WIRED)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='GET',
                               url=log_url,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_shaqodoon_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED

        # requests are signed with a key built as follows:
        # signing_key = md5(username|password|from|to|msg|key|current_date)
        # where current_date is in the format: d/m/y H
        payload = {'from': channel.address.lstrip('+'), 'to': msg.urn_path.lstrip('+'),
                   'username': channel.config[USERNAME], 'password': channel.config[PASSWORD],
                   'msg': text, 'key': channel.config[KEY],
                   'date': '{dt.day}/{dt.month}/{dt.year}'.format(dt=timezone.now())}

        # build our signature
        fingerprint = "%(username)s|%(password)s|%(from)s|%(to)s|%(msg)s|%(key)s|%(date)s" % payload
        signing_key = hashlib.md5(fingerprint).hexdigest()

        # remove unused parameters in the our payload
        del payload['date']
        del payload['key']

        # build our send URL
        url = channel.config[SEND_URL] + "?" + urlencode(payload)
        log_payload = ""

        try:
            # these guys use a self signed certificate
            response = requests.get(url, headers=TEMBA_HEADERS, timeout=15, verify=False)

        except Exception as e:
            raise SendException(unicode(e),
                                method='GET',
                                url=url,
                                request=log_payload,
                                response="",
                                response_status=503)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                method='GET',
                                url=url,
                                request=log_payload,
                                response=response.text,
                                response_status=response.status_code)

        Msg.mark_sent(channel.config['r'], msg, WIRED)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='GET',
                               url=url,
                               request=log_payload,
                               response=response.text,
                               response_status=response.status_code)


    @classmethod
    def send_external_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED
        payload = {'id': str(msg.id), 'text': text, 'to': msg.urn_path, 'from': channel.address, 'channel': str(channel.id)}

        # build our send URL
        url = Channel.build_send_url(channel.config[SEND_URL], payload)
        log_payload = None

        try:
            method = channel.config.get(SEND_METHOD, 'POST')
            if method == 'POST':
                response = requests.post(url, data=payload, headers=TEMBA_HEADERS, timeout=5)
            elif method == 'PUT':
                response = requests.put(url, data=payload, headers=TEMBA_HEADERS, timeout=5)
                log_payload = urlencode(payload)
            else:
                response = requests.get(url, headers=TEMBA_HEADERS, timeout=5)
                log_payload = urlencode(payload)

        except Exception as e:
            raise SendException(unicode(e),
                                method=method,
                                url=url,
                                request=log_payload,
                                response="",
                                response_status=503)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                method=method,
                                url=url,
                                request=log_payload,
                                response=response.text,
                                response_status=response.status_code)

        Msg.mark_sent(channel.config['r'], msg, WIRED)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method=method,
                               url=url,
                               request=log_payload,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_vumi_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED

        channel.config['transport_name'] = 'mtech_ng_smpp_transport'

        payload = dict(message_id=msg.id,
                       in_reply_to=None,
                       session_event=None,
                       to_addr=msg.urn_path,
                       from_addr=channel.address,
                       content=text,
                       transport_name=channel.config['transport_name'],
                       transport_type='sms',
                       transport_metadata={},
                       helper_metadata={})

        payload = json.dumps(payload)

        headers = dict(TEMBA_HEADERS)
        headers['content-type'] = 'application/json'

        url = 'https://go.vumi.org/api/v1/go/http_api_nostream/%s/messages.json' % channel.config['conversation_key']

        try:
            response = requests.put(url,
                                    data=payload,
                                    headers=headers,
                                    timeout=30,
                                    auth=(channel.config['account_key'], channel.config['access_token']))

        except Exception as e:
            raise SendException(unicode(e),
                                method='PUT',
                                url=url,
                                request=payload,
                                response="",
                                response_status=503)

        if response.status_code != 200 and response.status_code != 201:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                method='PUT',
                                url=url,
                                request=payload,
                                response=response.text,
                                response_status=response.status_code)

        # parse our response
        body = response.json()

        # mark our message as sent
        Msg.mark_sent(channel.config['r'], msg, WIRED, body.get('message_id', ''))

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='PUT',
                               url=url,
                               request=payload,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_nexmo_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, SENT
        from temba.orgs.models import NEXMO_KEY, NEXMO_SECRET

        client = NexmoClient(channel.org_config[NEXMO_KEY], channel.org_config[NEXMO_SECRET])
        (message_id, response) = client.send_message(channel.address,  msg.urn_path, text)

        Msg.mark_sent(channel.config['r'], msg, SENT, message_id)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered to Nexmo",
                               method=response.request.method,
                               url=response.request.url,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_infobip_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, SENT

        API_URL = 'http://api.infobip.com/api/v3/sendsms/json'
        BACKUP_API_URL = 'http://api2.infobip.com/api/v3/sendsms/json'

        url = API_URL

        # build our message dict
        message = dict(sender=channel.address.lstrip('+'),
                       text=text,
                       recipients=[dict(gsm=msg.urn_path.lstrip('+'))])

        # infobip requires that long messages have a different type
        if len(text) > 160:
            message['type'] = 'longSMS'

        payload = {'authentication': dict(username=channel.config['username'], password=channel.config['password']),
                   'messages': [message]}

        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        headers.update(TEMBA_HEADERS)

        try:
            response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=5)
        except:
            try:
                # we failed to connect, try our backup URL
                url = BACKUP_API_URL
                response = requests.post(url, params=payload, headers=headers, timeout=5)
            except Exception as e:
                payload['authentication']['password'] = 'x' * len(payload['authentication']['password'])
                raise SendException(u"Unable to send message: %s" % unicode(e),
                                    url=url,
                                    method='POST',
                                    request=json.dumps(payload),
                                    response=response.text,
                                    response_status=response.status_code)

        if response.status_code != 200 and response.status_code != 201:
            payload['authentication']['password'] = 'x' * len(payload['authentication']['password'])
            raise SendException("Received non 200 status: %d" % response.status_code,
                                url=url,
                                method='POST',
                                request=json.dumps(payload),
                                response=response.text,
                                response_status=response.status_code)

        response_json = response.json()
        messages = response_json['results']

        # if it wasn't successfully delivered, throw
        if int(messages[0]['status']) != 0:
            raise SendException("Received non-zero status code [%s]" % messages[0]['status'],
                                url=url,
                                method='POST',
                                request=json.dumps(payload),
                                response=response.text,
                                response_status=response.status_code)

        Msg.mark_sent(channel.config['r'], msg, SENT, messages[0]['messageid'])

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               url=url,
                               method='POST',
                               request=json.dumps(payload),
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_hub9_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, SENT

        # http://175.103.48.29:28078/testing/smsmt.php?
        #   userid=xxx
        #   &password=xxxx
        #   &original=6282881134567
        #   &sendto=628159152565
        #   &messagetype=0
        #   &messageid=1897869768
        #   &message=Test+Normal+Single+Message&dcs=0
        #   &udhl=0&charset=utf-8
        #
        from temba.settings import HUB9_ENDPOINT
        url = HUB9_ENDPOINT
        payload = dict(userid=channel.config['username'], password=channel.config['password'],
                       original=channel.address.lstrip('+'), sendto=msg.urn_path.lstrip('+'),
                       messageid=msg.id, message=text, dcs=0, udhl=0)

        # build up our querystring and send it as a get
        send_url = "%s?%s" % (url, urlencode(payload))
        payload['password'] = 'x' * len(payload['password'])
        masked_url = "%s?%s" % (url, urlencode(payload))

        try:
            response = requests.get(send_url, proxies=proxies, headers=TEMBA_HEADERS, timeout=15)
            if not response:
                raise SendException("Unable to send message",
                                    url=masked_url,
                                    method='GET',
                                    response="Empty response",
                                    response_status=503)

            if response.status_code != 200 and response.status_code != 201:
                raise SendException("Received non 200 status: %d" % response.status_code,
                                    url=masked_url,
                                    method='GET',
                                    request=None,
                                    response=response.text,
                                    response_status=response.status_code)

            # if it wasn't successfully delivered, throw
            if response.text != "000":
                error = "Unknown error"
                if response.text == "001":
                    error = "Error 001: Authentication Error"
                elif response.text == "101":
                    error = "Error 101: Account expired or invalid parameters"

                raise SendException(error,
                                    url=masked_url,
                                    method='GET',
                                    request=None,
                                    response=response.text,
                                    response_status=response.status_code)

            Msg.mark_sent(channel.config['r'], msg, SENT)

            ChannelLog.log_success(msg=msg,
                                   description="Successfully delivered",
                                   url=masked_url,
                                   method='GET',
                                   response=response.text,
                                   response_status=response.status_code)

        except SendException as e:
            raise e
        except Exception as e:
            reason = "Unknown error"
            try:
                if e.message and e.message.reason:
                    reason = e.message.reason
            except:
                pass
            raise SendException(u"Unable to send message: %s" % unicode(reason)[:64],
                                url=masked_url,
                                method='GET',
                                request=None,
                                response=reason,
                                response_status=503)

    @classmethod
    def send_zenvia_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED

        # Zenvia accepts messages via a GET
        # http://www.zenvia360.com.br/GatewayIntegration/msgSms.do?dispatch=send&account=temba&
        # code=abc123&to=5511996458779&msg=my message content&id=123&callbackOption=1
        payload = dict(dispatch='send',
                       account=channel.config['account'],
                       code=channel.config['code'],
                       msg=text,
                       to=msg.urn_path,
                       id=msg.id,
                       callbackOption=1)

        zenvia_url = "http://www.zenvia360.com.br/GatewayIntegration/msgSms.do"
        headers = {'Content-Type': "text/html", 'Accept-Charset': 'ISO-8859-1'}
        headers.update(TEMBA_HEADERS)

        try:
            response = requests.get(zenvia_url,
                                    params=payload, headers=headers, timeout=5)
        except Exception as e:
            raise SendException(u"Unable to send message: %s" % unicode(e),
                                url=zenvia_url,
                                method='POST',
                                request=json.dumps(payload),
                                response=response.text,
                                response_status=response.status_code)

        if response.status_code != 200 and response.status_code != 201:
            raise SendException("Got non-200 response from API: %d" % response.status_code,
                                url=zenvia_url,
                                method='POST',
                                request=json.dumps(payload),
                                response=response.text,
                                response_status=response.status_code)

        response_code = int(response.text[:3])

        if response_code != 0:
            raise Exception("Got non-zero response from Zenvia: %s" % response.text)

        Msg.mark_sent(channel.config['r'], msg, WIRED)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               url=zenvia_url,
                               method='POST',
                               request=json.dumps(payload),
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_africas_talking_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, SENT

        payload = dict(username=channel.config['username'],
                       to=msg.urn_path,
                       message=text)
        payload['from'] = channel.address

        headers = dict(Accept='application/json', apikey=channel.config['api_key'])
        headers.update(TEMBA_HEADERS)

        api_url = "https://api.africastalking.com/version1/messaging"

        try:
            response = requests.post(api_url,
                                     data=payload, headers=headers, timeout=5)
        except Exception as e:
            raise SendException(u"Unable to send message: %s" % unicode(e),
                                url=api_url,
                                method='POST',
                                request=json.dumps(payload),
                                response=response.text,
                                response_status=response.status_code)

        if response.status_code != 200 and response.status_code != 201:
            raise SendException("Got non-200 response from API: %d" % response.status_code,
                                url=api_url,
                                method='POST',
                                request=json.dumps(payload),
                                response=response.text,
                                response_status=response.status_code)

        response_data = response.json()

        # set our external id so we know when it is actually sent, this is missing in cases where
        # it wasn't sent, in which case we'll become an errored message
        external_id = response_data['SMSMessageData']['Recipients'][0]['messageId']

        Msg.mark_sent(channel.config['r'], msg, SENT, external_id)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               url=api_url,
                               method='POST',
                               request=json.dumps(payload),
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_twilio_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED
        from temba.orgs.models import ACCOUNT_SID, ACCOUNT_TOKEN

        callback_url = Channel.build_twilio_callback_url(msg.id)
        client = TwilioRestClient(channel.org_config[ACCOUNT_SID], channel.org_config[ACCOUNT_TOKEN])
        message = client.messages.create(to=msg.urn_path,
                                         from_=channel.address,
                                         body=text,
                                         status_callback=callback_url)

        Msg.mark_sent(channel.config['r'], msg, WIRED)
        ChannelLog.log_success(msg, "Successfully delivered message")

    @classmethod
    def send_twitter_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED

        consumer_key = settings.TWITTER_API_KEY
        consumer_secret = settings.TWITTER_API_SECRET
        oauth_token = channel.config['oauth_token']
        oauth_token_secret = channel.config['oauth_token_secret']

        twitter = Twython(consumer_key, consumer_secret, oauth_token, oauth_token_secret)
        dm = twitter.send_direct_message(screen_name=msg.urn_path, text=text)
        external_id = dm['id']

        Msg.mark_sent(channel.config['r'], msg, WIRED, external_id)
        ChannelLog.log_success(msg, "Successfully delivered message")

    @classmethod
    def get_pending_messages(cls, org):
        """
        We want all messages that are:
            1. Pending, ie, never queued
            2. Queued over two hours ago (something went awry and we need to re-queue)
            3. Errored and are ready for a retry
        """
        from temba.msgs.models import Msg, PENDING, QUEUED, ERRORED, OUTGOING

        now = timezone.now()
        hours_ago = now - timedelta(hours=2)

        pending = Msg.objects.filter(org=org, direction=OUTGOING).filter(Q(status=PENDING) |
                                                                         Q(status=QUEUED, queued_on__lte=hours_ago) |
                                                                         Q(status=ERRORED, next_attempt__lte=now)).exclude(channel__channel_type=ANDROID)

        # only SMS'es that have a topup and aren't the test contact
        pending = pending.exclude(topup=None).exclude(contact__is_test=True)

        # order then first by priority, then date
        pending = pending.order_by('-priority', 'created_on')
        return pending

    @classmethod
    def send_message(cls, msg): # pragma: no cover
        from temba.msgs.models import Msg, QUEUED, WIRED
        r = get_redis_connection()

        # check whether this message was already sent somehow
        if r.get('sms_sent_%d' % msg.id):
            Msg.mark_sent(r, msg, WIRED)
            print "!! [%d] prevented duplicate send" % (msg.id)
            return

        # get our cached channel
        channel = Channel.get_cached_channel(msg.channel)

        # channel can be none in the case where the channel has been removed
        if not channel:
            Msg.mark_error(msg, fatal=True)
            ChannelLog.log_error(msg, _("Message no longer has a way of being sent, marking as failed."))
            return

        # populate redis in our config
        channel.config['r'] = r
        type_config = RELAYER_TYPE_CONFIG[channel.channel_type]

        send_funcs = {AFRICAS_TALKING: Channel.send_africas_talking_message,
                      EXTERNAL: Channel.send_external_message,
                      HUB9: Channel.send_hub9_message,
                      INFOBIP: Channel.send_infobip_message,
                      KANNEL: Channel.send_kannel_message,
                      NEXMO: Channel.send_nexmo_message,
                      TWILIO: Channel.send_twilio_message,
                      TWITTER: Channel.send_twitter_message,
                      VUMI: Channel.send_vumi_message,
                      SHAQODOON: Channel.send_shaqodoon_message,
                      ZENVIA: Channel.send_zenvia_message}

        sent_count = 0
        parts = Msg.get_text_parts(msg.text, type_config['max_length'])
        for part in parts:
            sent_count += 1
            try:
                channel_type = channel.channel_type

                # never send in debug unless overridden
                if not settings.SEND_MESSAGES:
                    Msg.mark_sent(r, msg, WIRED, timezone.now())
                    print "FAKED SEND for [%d] - %s" % (msg.id, part)
                elif channel_type in send_funcs:
                    send_funcs[channel_type](channel, msg, part)
                else:
                    sent_count -= 1
                    raise Exception(_("Unknown channel type: %(channel)s") % {'channel': channel.channel_type})
            except SendException as e:
                ChannelLog.log_exception(msg, e)

                import traceback
                traceback.print_exc(e)

                Msg.mark_error(msg)
                sent_count -= 1

            except Exception as e:
                ChannelLog.log_error(msg, unicode(e))

                import traceback
                traceback.print_exc(e)

                Msg.mark_error(msg)
                sent_count -= 1

            finally:
                # if we are still in a queued state, mark ourselves as an error
                if msg.status == QUEUED:
                    print "!! [%d] marking queued message as error" % msg.id
                    Msg.mark_error(msg)
                    sent_count -= 1

        # update the number of sms it took to send this if it was more than 1
        if len(parts) > 1:
            Msg.objects.filter(pk=msg.id).update(msg_count=len(parts))

    @classmethod
    def track_status(cls, channel, status):
        # track success, errors and failures
        analytics.track(channel.created_by.username, 'temba.channel_%s' % status.lower(), dict(channel_type=channel.get_channel_type_display()))

    @classmethod
    def build_twilio_callback_url(cls, sms_id):
        url = "https://" + settings.TEMBA_HOST + "/api/v1/twilio/?action=callback&id=%d" % sms_id
        return url

    def __unicode__(self): # pragma: no cover
        if self.name:
            return self.name
        elif self.device:
            return self.device
        elif self.address:
            return self.address
        else:
            return unicode(self.pk)

    class Meta:
        ordering = ('-last_seen', '-pk')

SOURCE_AC = "AC"
SOURCE_USB = "USB"
SOURCE_WIRELESS = "WIR"
SOURCE_BATTERY = "BAT"

STATUS_UNKNOWN = "UNK"
STATUS_CHARGING = "CHA"
STATUS_DISCHARGING = "DIS"
STATUS_NOT_CHARGING = "NOT"
STATUS_FULL = "FUL"

class SendException(Exception):

    def __init__(self, description, url, method, request, response, response_status):
        super(SendException, self).__init__(description)

        self.description = description
        self.url = url
        self.method = method
        self.request = request
        self.response = response
        self.response_status = response_status


class ChannelLog(models.Model):
    msg = models.ForeignKey('msgs.Msg')
    description = models.CharField(max_length=255)
    is_error = models.BooleanField(default=None)
    url = models.TextField(null=True)
    method = models.CharField(max_length=16, null=True)
    request = models.TextField(null=True)
    response = models.TextField(null=True)
    response_status = models.IntegerField(null=True)
    created_on = models.DateTimeField(auto_now_add=True)

    @classmethod
    def write(cls, log):
        if log.is_error:
            print("[%d] ERROR - %s %s \"%s\" %s \"%s\"" %
                  (log.msg.pk, log.method, log.url, log.request, log.response_status, log.response))
        else:
            print("[%d] SENT - %s %s \"%s\" %s \"%s\"" %
                  (log.msg.pk, log.method, log.url, log.request, log.response_status, log.response))


    @classmethod
    def log_exception(cls, msg, e):
        cls.write(ChannelLog.objects.create(msg_id=msg.id,
                                            is_error=True,
                                            description=unicode(e.description)[:255],
                                            method=e.method,
                                            url=e.url,
                                            request=e.request,
                                            response=e.response,
                                            response_status=e.response_status))

        cls.trim_for_org(msg.org)

    @classmethod
    def log_error(cls, msg, description):
        cls.write(ChannelLog.objects.create(msg_id=msg.id,
                                            is_error=True,
                                            description=description[:255]))

        cls.trim_for_org(msg.org)

    @classmethod
    def log_success(cls, msg, description, method=None, url=None, request=None, response=None, response_status=None):
        cls.write(ChannelLog.objects.create(msg_id=msg.id,
                                            is_error=False,
                                            description=description[:255],
                                            method=method,
                                            url=url,
                                            request=request,
                                            response=response,
                                            response_status=response_status))

        cls.trim_for_org(msg.org)

    @classmethod
    def trim_for_org(cls, org_id):
        # keep only the most recent 100 errors for each org
        #for error in ChannelLog.objects.filter(msg__channel__org_id=org_id).order_by('-created_on')[100:]: # pragma: no cover
        #    error.delete()
        pass

class SyncEvent(SmartModel):
    channel = models.ForeignKey(Channel, verbose_name=_("Channel"),
                                help_text = _("The channel that synced to the server"))
    power_source = models.CharField(verbose_name=_("Power Source"), max_length=64,
                                    help_text=_("The power source the device is using"))
    power_status = models.CharField(verbose_name=_("Power Status"), max_length=64, default="STATUS_UNKNOWN",
                                    help_text=_("The power status. eg: Charging, Full or Discharging"))
    power_level = models.IntegerField(verbose_name=_("Power Level"), help_text=_("The power level of the battery"))
    network_type = models.CharField(verbose_name=_("Network Type"), max_length=128,
                                    help_text=_("The data network type to which the channel is connected"))
    lifetime = models.IntegerField(verbose_name=_("Lifetime"), null=True, blank=True, default=0)
    pending_message_count = models.IntegerField(verbose_name=_("Pending Messages Count"),
                                                help_text=_("The number of messages on the channel in PENDING state"), default=0)
    retry_message_count = models.IntegerField(verbose_name=_("Retry Message Count"),
                                              help_text=_("The number of messages on the channel in RETRY state"), default=0)
    incoming_command_count = models.IntegerField(verbose_name=_("Incoming Command Count"),
                                                 help_text=_("The number of commands that the channel gave us"), default=0)
    outgoing_command_count = models.IntegerField(verbose_name=_("Outgoing Command Count"),
                                                 help_text=_("The number of commands that we gave the channel"), default=0)

    @classmethod
    def create(cls, channel, cmd, incoming_commands):
        # update country, device and OS on our channel
        country = cmd.get('cc', None)
        device = cmd.get('dev', None)
        os = cmd.get('os', None)

        # update our channel if anything is new
        if channel.country != country or channel.device != device or channel.os != os:
            Channel.objects.filter(pk=channel.pk).update(country=country, device=device, os=os)

        args = dict()

        args['power_source'] = cmd.get('p_src', cmd.get('power_source'))
        args['power_status'] = cmd.get('p_sts', cmd.get('power_status'))
        args['power_level'] = cmd.get('p_lvl', cmd.get('power_level'))

        args['network_type'] = cmd.get('net', cmd.get('network_type'))

        args['pending_message_count'] = len(cmd.get('pending', cmd.get('pending_messages')))
        args['retry_message_count'] = len(cmd.get('retry', cmd.get('retry_messages')))
        args['incoming_command_count'] = max(len(incoming_commands)-2, 0)

        anon_user = User.objects.get(pk=-1)
        args['channel'] = channel
        args['created_by'] = anon_user
        args['modified_by'] = anon_user

        sync_event = SyncEvent.objects.create(**args)
        sync_event.pending_messages = cmd.get('pending', cmd.get('pending_messages'))
        sync_event.retry_messages = cmd.get('retry', cmd.get('retry_messages'))

        # trim any extra events
        cls.trim()

        return sync_event

    def get_pending_messages(self):
        return getattr(self, 'pending_messages', [])

    def get_retry_messages(self):
        return getattr(self, 'retry_messages', [])

    @classmethod
    def trim(cls):
        month_ago = timezone.now() - timedelta(days=30)
        cls.objects.filter(created_on__lte=month_ago).delete()


@receiver(pre_save, sender=SyncEvent)
def pre_save(sender, instance, **kwargs):
    if kwargs['raw']: return

    if not instance.pk:
        last_sync_event = SyncEvent.objects.filter(channel=instance.channel).order_by('-created_on').first()
        if last_sync_event:
            td = (timezone.now() - last_sync_event.created_on)
            last_sync_event.lifetime = td.seconds + td.days * 24 * 3600
            last_sync_event.save()
    

ALERT_DISCONNECTED = 'D'
ALERT_POWER = 'P'
ALERT_SMS = 'S'


class Alert(SmartModel):
    ALERT_TYPES = ((ALERT_POWER, _("Power")),                 # channel has low power
                   (ALERT_DISCONNECTED, _("Disconnected")),   # channel hasn't synced in a while
                   (ALERT_SMS, _("SMS")))                     # channel has many unsent messages

    channel = models.ForeignKey(Channel, verbose_name=_("Channel"),
                                help_text=_("The channel that this alert is for"))
    sync_event = models.ForeignKey(SyncEvent, verbose_name=_("Sync Event"), null=True,
                                   help_text=_("The sync event that caused this alert to be sent (if any)"))
    alert_type = models.CharField(verbose_name=_("Alert Type"), max_length=1, choices=ALERT_TYPES,
                                  help_text=_("The type of alert the channel is sending"))
    ended_on = models.DateTimeField(verbose_name=_("Ended On"), blank=True, null=True)

    host = models.CharField(max_length=32, help_text=_("The host this alert was created on"))

    @classmethod
    def check_power_alert(cls, sync):
        alert_user = get_alert_user()

        if (sync.power_status == STATUS_DISCHARGING or
            sync.power_status == STATUS_UNKNOWN or
            sync.power_status == STATUS_NOT_CHARGING) and int(sync.power_level) < 25:

            alerts = Alert.objects.filter(sync_event__channel=sync.channel, alert_type=ALERT_POWER, ended_on=None)

            if not alerts:
                host = getattr(settings, 'HOSTNAME', 'rapidpro.io')
                new_alert = Alert.objects.create(channel=sync.channel,
                                                 host=host,
                                                 sync_event=sync,
                                                 alert_type=ALERT_POWER,
                                                 created_by=alert_user,
                                                 modified_by=alert_user)
                new_alert.send_alert()

        if sync.power_status == STATUS_CHARGING or sync.power_status == STATUS_FULL:
            alerts = Alert.objects.filter(sync_event__channel=sync.channel, alert_type=ALERT_POWER, ended_on=None).order_by('-created_on')

            # end our previous alert
            if alerts and int(alerts[0].sync_event.power_level) < 25:
                for alert in alerts:
                    alert.ended_on = timezone.now()
                    alert.save()
                    last_alert = alert
                last_alert.send_resolved()

    @classmethod
    def check_alerts(cls):
        from temba.msgs.models import Msg

        alert_user = get_alert_user()
        thirty_minutes_ago = timezone.now() - timedelta(minutes=30)

        # end any alerts that no longer seem valid
        for alert in Alert.objects.filter(alert_type=ALERT_DISCONNECTED, ended_on=None):
            # if we've seen the channel since this alert went out, then clear the alert
            if alert.channel.last_seen > alert.created_on:
                alert.ended_on = alert.channel.last_seen
                alert.save()
                alert.send_resolved()

        for channel in Channel.objects.filter(channel_type=ANDROID, is_active=True).exclude(org=None).exclude(last_seen__gte=thirty_minutes_ago):
            # have we already sent an alert for this channel
            if not Alert.objects.filter(channel=channel, alert_type=ALERT_DISCONNECTED, ended_on=None):
                host = getattr(settings, 'HOSTNAME', 'rapidpro.io')
                alert = Alert.objects.create(channel=channel, alert_type=ALERT_DISCONNECTED, host=host,
                                             modified_by=alert_user, created_by=alert_user)
                alert.send_alert()


        day_ago = timezone.now() - timedelta(days=1)
        hour_ago = timezone.now() - timedelta(hours=1)
        six_hours_ago = timezone.now() - timedelta(hours=6)

        # end any sms alerts that are open and no longer seem valid
        for alert in Alert.objects.filter(alert_type=ALERT_SMS, ended_on=None):
            # are there still queued messages?

            if not Msg.objects.filter(status__in=['Q', 'P'], channel=alert.channel, contact__is_test=False, created_on__lte=thirty_minutes_ago).exclude(created_on__lte=day_ago):
                alert.ended_on = timezone.now()
                alert.save()

        # now look for channels that have many unsent messages
        queued_messages = Msg.objects.filter(status__in=['Q', 'P'], contact__is_test=False).order_by('channel', 'created_on').exclude(created_on__gte=thirty_minutes_ago).exclude(created_on__lte=day_ago).exclude(channel=None).values('channel').annotate(latest_queued=Max('created_on'))
        sent_messages = Msg.objects.filter(status__in=['S', 'D'], contact__is_test=False).exclude(created_on__lte=day_ago).exclude(channel=None).order_by('channel', 'sent_on').values('channel').annotate(latest_sent=Max('sent_on'))

        channels = dict()
        for queued in queued_messages:
            if queued['channel']:
                channels[queued['channel']] = dict(queued=queued['latest_queued'], sent=None)

        for sent in sent_messages:
            existing = channels.get(sent['channel'], dict(queued=None))
            existing['sent'] = sent['latest_sent']

        for (channel_id, value) in channels.items():
            if not value['sent'] or value['sent'] < value['queued']:
                channel = Channel.objects.get(pk=channel_id)

                # never alert on channels that have no org
                if channel.org is None:
                    continue

                if channels[channel_id]['sent']:
                    if not Alert.objects.filter(channel=channel).filter(Q(created_on__gt=six_hours_ago)):
                        host = getattr(settings, 'HOSTNAME', 'rapidpro.io')
                        alert = Alert.objects.create(channel=channel, alert_type=ALERT_SMS, host=host,
                                                     modified_by=alert_user, created_by=alert_user)
                        alert.send_alert()

                else:
                    # This is for the case if there is no successfully sent message and no open alert 
                    if not Alert.objects.filter(channel=channel, ended_on=None):
                        host = getattr(settings, 'HOSTNAME', 'rapidpro.io')
                        alert = Alert.objects.create(channel=channel, alert_type=ALERT_SMS, host=host,
                                                     modified_by=alert_user, created_by=alert_user)
                        alert.send_alert()
            

    def send_alert(self):
        from .tasks import send_alert_task
        send_alert_task.delay(self.id, resolved=False)

    def send_resolved(self):
        from .tasks import send_alert_task
        send_alert_task.delay(self.id, resolved=True)

    def send_email(self, resolved):
        from temba.msgs.models import Msg

        # no-op if this channel has no alert email
        if not self.channel.alert_email:
            return

        # no-op if the channel is not tied to an org
        if not self.channel.org:
            return

        if self.alert_type == ALERT_POWER:
            if resolved:
                subject = "Your Android phone is now charging"
                template = 'channels/email/power_charging_alert'                
            else:
                subject = "Your Android phone battery is low"
                template = 'channels/email/power_alert'

        elif self.alert_type == ALERT_DISCONNECTED:
            if resolved:
                subject = "Your Android phone is now connected"
                template = 'channels/email/connected_alert'
            else:
                subject = "Your Android phone is disconnected"
                template = 'channels/email/disconnected_alert'

        elif self.alert_type == ALERT_SMS:
            subject = "Your %s is having trouble sending messages" % self.channel.get_channel_type_name()
            template = 'channels/email/sms_alert'
        else: # pragma: no cover
            raise Exception(_("Unknown alert type: %(alert)s") % {'alert':self.alert_type})

        from temba.middleware import BrandingMiddleware
        branding = BrandingMiddleware.get_branding_for_host(self.host)

        context = dict(org=self.channel.org, channel=self.channel, now=timezone.now(),
                       last_seen=self.channel.last_seen, sync=self.sync_event)
        context['unsent_count'] = Msg.objects.filter(channel=self.channel, status__in=['Q', 'P'], contact__is_test=False).count()
        context['subject'] = subject

        send_temba_email(self.channel.alert_email, subject, template, context, branding)

def get_alert_user():
    user = User.objects.filter(username='alert').first()
    if user:
        return user
    else:
        user = User.objects.create_user('alert')
        user.groups.add(Group.objects.get(name='Service Users'))
        return user

def get_twilio_application_sid():
    return os.environ.get('TWILIO_APPLICATION_SID', settings.TWILIO_APPLICATION_SID)

def get_twilio_client():
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID', settings.TWILIO_ACCOUNT_SID)
    auth_token = os.environ.get('TWILIO_AUTH_TOKEN', settings.TWILIO_AUTH_TOKEN)
    from temba.ivr.models.clients import TwilioClient
    return TwilioClient(account_sid, auth_token)
