from __future__ import absolute_import, unicode_literals

import json
import time
import urlparse
import os
import phonenumbers
import plivo
import regex
import requests
import telegram
import re

from enum import Enum
from datetime import timedelta
from django.contrib.auth.models import User, Group
from django.core.urlresolvers import reverse
from django.db import models, connection
from django.db.models import Q, Max, Sum
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
from temba.utils.email import send_template_email
from temba.utils import analytics, random_string, dict_to_struct, dict_to_json
from time import sleep
from twilio.rest import TwilioRestClient
from twython import Twython
from temba.utils.gsm7 import is_gsm7, replace_non_gsm7_accents
from temba.utils.models import TembaModel, generate_uuid
from urllib import quote_plus
from xml.sax.saxutils import quoteattr, escape

TEMBA_HEADERS = {'User-agent': 'RapidPro'}

# Some providers need a static ip to whitelist, route them through our proxy
OUTGOING_PROXIES = settings.OUTGOING_PROXIES


class Encoding(Enum):
    GSM7 = 1
    REPLACED = 2
    UNICODE = 3


class Channel(TembaModel):
    TYPE_AFRICAS_TALKING = 'AT'
    TYPE_ANDROID = 'A'
    TYPE_BLACKMYNA = 'BM'
    TYPE_CHIKKA = 'CK'
    TYPE_CLICKATELL = 'CT'
    TYPE_EXTERNAL = 'EX'
    TYPE_FACEBOOK = 'FB'
    TYPE_GLOBE = 'GL'
    TYPE_HIGH_CONNECTION = 'HX'
    TYPE_HUB9 = 'H9'
    TYPE_INFOBIP = 'IB'
    TYPE_JASMIN = 'JS'
    TYPE_KANNEL = 'KN'
    TYPE_M3TECH = 'M3'
    TYPE_MBLOX = 'MB'
    TYPE_NEXMO = 'NX'
    TYPE_PLIVO = 'PL'
    TYPE_SHAQODOON = 'SQ'
    TYPE_SMSCENTRAL = 'SC'
    TYPE_START = 'ST'
    TYPE_TELEGRAM = 'TG'
    TYPE_TWILIO = 'T'
    TYPE_TWILIO_MESSAGING_SERVICE = 'TMS'
    TYPE_TWITTER = 'TT'
    TYPE_VERBOICE = 'VB'
    TYPE_VIBER = 'VI'
    TYPE_VUMI = 'VM'
    TYPE_VUMI_USSD = 'VMU'
    TYPE_YO = 'YO'
    TYPE_ZENVIA = 'ZV'

    # keys for various config options stored in the channel config dict
    CONFIG_SEND_URL = 'send_url'
    CONFIG_SEND_METHOD = 'method'
    CONFIG_SEND_BODY = 'body'
    CONFIG_DEFAULT_SEND_BODY = 'id={{id}}&text={{text}}&to={{to}}&to_no_plus={{to_no_plus}}&from={{from}}&from_no_plus={{from_no_plus}}&channel={{channel}}'
    CONFIG_USERNAME = 'username'
    CONFIG_PASSWORD = 'password'
    CONFIG_KEY = 'key'
    CONFIG_API_ID = 'api_id'
    CONFIG_VERIFY_SSL = 'verify_ssl'
    CONFIG_USE_NATIONAL = 'use_national'
    CONFIG_ENCODING = 'encoding'
    CONFIG_PAGE_NAME = 'page_name'
    CONFIG_PLIVO_AUTH_ID = 'PLIVO_AUTH_ID'
    CONFIG_PLIVO_AUTH_TOKEN = 'PLIVO_AUTH_TOKEN'
    CONFIG_PLIVO_APP_ID = 'PLIVO_APP_ID'
    CONFIG_AUTH_TOKEN = 'auth_token'

    ENCODING_DEFAULT = 'D'  # we just pass the text down to the endpoint
    ENCODING_SMART = 'S'  # we try simple substitutions to GSM7 then go to unicode if it still isn't GSM7
    ENCODING_UNICODE = 'U'  # we send everything as unicode

    ENCODING_CHOICES = ((ENCODING_DEFAULT, _("Default Encoding")),
                        (ENCODING_SMART, _("Smart Encoding")),
                        (ENCODING_UNICODE, _("Unicode Encoding")))

    # the role types for our channels
    ROLE_SEND = 'S'
    ROLE_RECEIVE = 'R'
    ROLE_CALL = 'C'
    ROLE_ANSWER = 'A'

    # how many outgoing messages we will queue at once
    SEND_QUEUE_DEPTH = 500

    # how big each batch of outgoing messages can be
    SEND_BATCH_SIZE = 100

    TWITTER_FATAL_403S = ("messages to this user right now",  # handle is suspended
                          "users who are not following you")  # handle no longer follows us

    YO_API_URL_1 = 'http://smgw1.yo.co.ug:9100/sendsms'
    YO_API_URL_2 = 'http://41.220.12.201:9100/sendsms'
    YO_API_URL_3 = 'http://164.40.148.210:9100/sendsms'

    # various hard coded settings for the channel types
    CHANNEL_SETTINGS = {
        TYPE_AFRICAS_TALKING: dict(scheme='tel', max_length=160),
        TYPE_ANDROID: dict(scheme='tel', max_length=-1),
        TYPE_BLACKMYNA: dict(scheme='tel', max_length=1600),
        TYPE_CHIKKA: dict(scheme='tel', max_length=160),
        TYPE_CLICKATELL: dict(scheme='tel', max_length=420),
        TYPE_EXTERNAL: dict(max_length=160),
        TYPE_FACEBOOK: dict(scheme='facebook', max_length=320),
        TYPE_GLOBE: dict(scheme='tel', max_length=160),
        TYPE_HIGH_CONNECTION: dict(scheme='tel', max_length=320),
        TYPE_HUB9: dict(scheme='tel', max_length=1600),
        TYPE_INFOBIP: dict(scheme='tel', max_length=1600),
        TYPE_JASMIN: dict(scheme='tel', max_length=1600),
        TYPE_KANNEL: dict(scheme='tel', max_length=1600),
        TYPE_M3TECH: dict(scheme='tel', max_length=160),
        TYPE_NEXMO: dict(scheme='tel', max_length=1600, max_tps=1),
        TYPE_MBLOX: dict(scheme='tel', max_length=459),
        TYPE_PLIVO: dict(scheme='tel', max_length=1600),
        TYPE_SHAQODOON: dict(scheme='tel', max_length=1600),
        TYPE_SMSCENTRAL: dict(scheme='tel', max_length=1600),
        TYPE_START: dict(scheme='tel', max_length=1600),
        TYPE_TELEGRAM: dict(scheme='telegram', max_length=1600),
        TYPE_TWILIO: dict(scheme='tel', max_length=1600),
        TYPE_TWILIO_MESSAGING_SERVICE: dict(scheme='tel', max_length=1600),
        TYPE_TWITTER: dict(scheme='twitter', max_length=10000),
        TYPE_VERBOICE: dict(scheme='tel', max_length=1600),
        TYPE_VIBER: dict(scheme='tel', max_length=1000),
        TYPE_VUMI: dict(scheme='tel', max_length=1600),
        TYPE_VUMI_USSD: dict(scheme='tel', max_length=182),
        TYPE_YO: dict(scheme='tel', max_length=1600),
        TYPE_ZENVIA: dict(scheme='tel', max_length=150),
    }

    TYPE_CHOICES = ((TYPE_AFRICAS_TALKING, "Africa's Talking"),
                    (TYPE_ANDROID, "Android"),
                    (TYPE_BLACKMYNA, "Blackmyna"),
                    (TYPE_CLICKATELL, "Clickatell"),
                    (TYPE_EXTERNAL, "External"),
                    (TYPE_FACEBOOK, "Facebook"),
                    (TYPE_GLOBE, "Globe Labs"),
                    (TYPE_HIGH_CONNECTION, "High Connection"),
                    (TYPE_HUB9, "Hub9"),
                    (TYPE_INFOBIP, "Infobip"),
                    (TYPE_JASMIN, "Jasmin"),
                    (TYPE_KANNEL, "Kannel"),
                    (TYPE_M3TECH, "M3 Tech"),
                    (TYPE_MBLOX, "Mblox"),
                    (TYPE_NEXMO, "Nexmo"),
                    (TYPE_PLIVO, "Plivo"),
                    (TYPE_SHAQODOON, "Shaqodoon"),
                    (TYPE_SMSCENTRAL, "SMSCentral"),
                    (TYPE_START, "Start Mobile"),
                    (TYPE_TELEGRAM, "Telegram"),
                    (TYPE_TWILIO, "Twilio"),
                    (TYPE_TWILIO_MESSAGING_SERVICE, "Twilio Messaging Service"),
                    (TYPE_TWITTER, "Twitter"),
                    (TYPE_VERBOICE, "Verboice"),
                    (TYPE_VIBER, "Viber"),
                    (TYPE_VUMI, "Vumi"),
                    (TYPE_VUMI_USSD, "Vumi USSD"),
                    (TYPE_YO, "Yo!"),
                    (TYPE_ZENVIA, "Zenvia"))

    # list of all USSD channels
    USSD_CHANNELS = [TYPE_VUMI_USSD]

    GET_STARTED = 'get_started'
    VIBER_NO_SERVICE_ID = 'no_service_id'

    channel_type = models.CharField(verbose_name=_("Channel Type"), max_length=3, choices=TYPE_CHOICES,
                                    default=TYPE_ANDROID, help_text=_("Type of this channel, whether Android, Twilio or SMSC"))

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

    scheme = models.CharField(verbose_name="URN Scheme", max_length=8, default='tel',
                              help_text=_("The URN scheme this channel can handle"))

    role = models.CharField(verbose_name="Channel Role", max_length=4, default=ROLE_SEND + ROLE_RECEIVE,
                            help_text=_("The roles this channel can fulfill"))

    parent = models.ForeignKey('self', blank=True, null=True,
                               help_text=_("The channel this channel is working on behalf of"))

    bod = models.TextField(verbose_name=_("Optional Data"), null=True,
                           help_text=_("Any channel specific state data"))

    @classmethod
    def create(cls, org, user, country, channel_type, name=None, address=None, config=None, role=ROLE_SEND + ROLE_RECEIVE, scheme=None, **kwargs):
        type_settings = Channel.CHANNEL_SETTINGS[channel_type]
        fixed_scheme = type_settings.get('scheme')

        if scheme:
            if fixed_scheme and fixed_scheme != scheme:
                raise ValueError("Channel type %s cannot support scheme %s" % (channel_type, scheme))
        else:
            scheme = fixed_scheme

        if not scheme:
            raise ValueError("Cannot create channel without scheme")

        if country and scheme != 'tel':
            raise ValueError("Only channels handling phone numbers can be country specific")

        if config is None:
            config = {}

        create_args = dict(org=org, created_by=user, modified_by=user,
                           country=country,
                           channel_type=channel_type,
                           name=name, address=address,
                           config=json.dumps(config),
                           role=role, scheme=scheme)
        create_args.update(kwargs)

        if 'uuid' not in create_args:
            create_args['uuid'] = generate_uuid()

        channel = cls.objects.create(**create_args)

        # normalize any telephone numbers that we may now have a clue as to country
        if org:
            org.normalize_contact_tels()

        return channel

    @classmethod
    def add_telegram_channel(cls, org, user, auth_token):
        """
        Creates a new telegram channel from the passed in auth token
        """
        from temba.contacts.models import TELEGRAM_SCHEME
        bot = telegram.Bot(auth_token)
        me = bot.getMe()

        channel = Channel.create(org, user, None, Channel.TYPE_TELEGRAM, name=me.first_name, address=me.username,
                                 config={Channel.CONFIG_AUTH_TOKEN: auth_token}, scheme=TELEGRAM_SCHEME)

        bot.setWebhook("https://" + settings.TEMBA_HOST +
                       "%s" % reverse('handlers.telegram_handler', args=[channel.uuid]))
        return channel

    @classmethod
    def add_viber_channel(cls, org, user, name):
        return Channel.create(org, user, None, Channel.TYPE_VIBER, name=name, address=Channel.VIBER_NO_SERVICE_ID)

    @classmethod
    def add_authenticated_external_channel(cls, org, user, country, phone_number,
                                           username, password, channel_type, url):
        try:
            parsed = phonenumbers.parse(phone_number, None)
            phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        except Exception:
            # this is a shortcode, just use it plain
            phone = phone_number

        config = dict(username=username, password=password, send_url=url)
        return Channel.create(org, user, country, channel_type, name=phone, address=phone_number, config=config)

    @classmethod
    def add_config_external_channel(cls, org, user, country, address, channel_type, config, role=ROLE_SEND + ROLE_RECEIVE,
                                    scheme='tel', parent=None):
        return Channel.create(org, user, country, channel_type, name=address, address=address,
                              config=config, role=role, scheme=scheme, parent=parent)

    @classmethod
    def add_plivo_channel(cls, org, user, country, phone_number, auth_id, auth_token):
        plivo_uuid = generate_uuid()
        app_name = "%s/%s" % (settings.TEMBA_HOST.lower(), plivo_uuid)

        client = plivo.RestAPI(auth_id, auth_token)

        message_url = "https://" + settings.TEMBA_HOST + "%s" % reverse('handlers.plivo_handler', args=['receive', plivo_uuid])
        answer_url = "https://" + settings.AWS_BUCKET_DOMAIN + "/plivo_voice_unavailable.xml"

        plivo_response_status, plivo_response = client.create_application(params=dict(app_name=app_name,
                                                                                      answer_url=answer_url,
                                                                                      message_url=message_url))

        if plivo_response_status in [201, 200, 202]:
            plivo_app_id = plivo_response['app_id']
        else:
            plivo_app_id = None

        plivo_config = {Channel.CONFIG_PLIVO_AUTH_ID: auth_id,
                        Channel.CONFIG_PLIVO_AUTH_TOKEN: auth_token,
                        Channel.CONFIG_PLIVO_APP_ID: plivo_app_id}

        plivo_number = phone_number.strip('+ ').replace(' ', '')

        plivo_response_status, plivo_response = client.get_number(params=dict(number=plivo_number))

        if plivo_response_status != 200:
            plivo_response_status, plivo_response = client.buy_phone_number(params=dict(number=plivo_number))

            if plivo_response_status != 201:
                raise Exception(_("There was a problem claiming that number, please check the balance on your account."))

            plivo_response_status, plivo_response = client.get_number(params=dict(number=plivo_number))

        if plivo_response_status == 200:
            plivo_response_status, plivo_response = client.modify_number(params=dict(number=plivo_number,
                                                                                     app_id=plivo_app_id))
            if plivo_response_status != 202:
                raise Exception(_("There was a problem updating that number, please try again."))

        phone_number = '+' + plivo_number
        phone = phonenumbers.format_number(phonenumbers.parse(phone_number, None),
                                           phonenumbers.PhoneNumberFormat.NATIONAL)

        return Channel.create(org, user, country, Channel.TYPE_PLIVO, name=phone, address=phone_number,
                              config=plivo_config, uuid=plivo_uuid)

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
                raise Exception(_("There was a problem claiming that number, "
                                  "please check the balance on your account. " +
                                  "Note that you can only claim numbers after "
                                  "adding credit to your Nexmo account.") + "\n" + str(e))

        mo_path = reverse('handlers.nexmo_handler', args=['receive', org_uuid])

        # update the delivery URLs for it
        from temba.settings import TEMBA_HOST
        try:
            client.update_number(country, phone_number, 'http://%s%s' % (TEMBA_HOST, mo_path))

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

        return Channel.create(org, user, country, Channel.TYPE_NEXMO, name=phone, address=phone_number, bod=nexmo_phone_number)

    @classmethod
    def add_twilio_channel(cls, org, user, phone_number, country, role):
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
            raise Exception(_("Your Twilio account is no longer connected. "
                              "First remove your Twilio account, reconnect it and try again."))

        is_short_code = len(phone_number) <= 6

        if is_short_code:
            short_codes = client.sms.short_codes.list(short_code=phone_number)

            if short_codes:
                short_code = short_codes[0]
                twilio_sid = short_code.sid
                app_url = "https://" + settings.TEMBA_HOST + "%s" % reverse('handlers.twilio_handler')
                client.sms.short_codes.update(twilio_sid, sms_url=app_url)

                role = Channel.ROLE_SEND + Channel.ROLE_RECEIVE
                phone = phone_number

            else:
                raise Exception(_("Short code not found on your Twilio Account. "
                                  "Please check you own the short code and Try again"))
        else:
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

            twilio_sid = twilio_phone.sid

        return Channel.create(org, user, country, Channel.TYPE_TWILIO, name=phone, address=phone_number, role=role, bod=twilio_sid)

    @classmethod
    def add_twilio_messaging_service_channel(cls, org, user, messaging_service_sid, country):
        config = dict(messaging_service_sid=messaging_service_sid)

        return Channel.create(org, user, country, Channel.TYPE_TWILIO_MESSAGING_SERVICE,
                              name=messaging_service_sid, address=None, config=config)

    @classmethod
    def add_africas_talking_channel(cls, org, user, country, phone, username, api_key, is_shared=False):
        config = dict(username=username, api_key=api_key, is_shared=is_shared)

        return Channel.create(org, user, country, Channel.TYPE_AFRICAS_TALKING,
                              name="Africa's Talking: %s" % phone, address=phone, config=config)

    @classmethod
    def add_zenvia_channel(cls, org, user, phone, account, code):
        config = dict(account=account, code=code)

        return Channel.create(org, user, 'BR', Channel.TYPE_ZENVIA, name="Zenvia: %s" % phone, address=phone, config=config)

    @classmethod
    def add_send_channel(cls, user, channel):
        # nexmo ships numbers around as E164 without the leading +
        parsed = phonenumbers.parse(channel.address, None)
        nexmo_phone_number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164).strip('+')

        return Channel.create(user.get_org(), user, channel.country, Channel.TYPE_NEXMO, name="Nexmo Sender",
                              address=channel.address, role=Channel.ROLE_SEND, parent=channel, bod=nexmo_phone_number)

    @classmethod
    def add_call_channel(cls, org, user, channel):
        return Channel.create(org, user, channel.country, Channel.TYPE_TWILIO, name="Twilio Caller",
                              address=channel.address, role=Channel.ROLE_CALL, parent=channel)

    @classmethod
    def add_facebook_channel(cls, org, user, page_name, page_id, page_access_token):
        channel = Channel.create(org, user, None, Channel.TYPE_FACEBOOK, name=page_name, address=page_id,
                                 config={Channel.CONFIG_AUTH_TOKEN: page_access_token, Channel.CONFIG_PAGE_NAME: page_name},
                                 secret=Channel.generate_secret())

        return channel

    @classmethod
    def add_twitter_channel(cls, org, user, screen_name, handle_id, oauth_token, oauth_token_secret):
        config = dict(handle_id=long(handle_id),
                      oauth_token=oauth_token,
                      oauth_token_secret=oauth_token_secret)

        with org.lock_on(OrgLock.channels):
            channel = Channel.objects.filter(org=org, channel_type=Channel.TYPE_TWITTER, address=screen_name, is_active=True).first()
            if channel:
                channel.config = json.dumps(config)
                channel.modified_by = user
                channel.save()
            else:
                channel = Channel.create(org, user, None, Channel.TYPE_TWITTER, name="@%s" % screen_name, address=screen_name,
                                         config=config)

                # notify Mage so that it activates this channel
                from .tasks import MageStreamAction, notify_mage_task
                notify_mage_task.delay(channel.uuid, MageStreamAction.activate)

        return channel

    @classmethod
    def get_or_create_android(cls, gcm, status):
        """
        Creates a new Android channel from the gcm and status commands sent during device registration
        """
        gcm_id = gcm.get('gcm_id')
        uuid = gcm.get('uuid')
        country = status.get('cc')
        device = status.get('dev')

        if not gcm_id or not uuid:
            raise ValueError("Can't create Android channel without UUID and GCM ID")

        # look for existing active channel with this UUID
        existing = Channel.objects.filter(uuid=uuid, is_active=True).first()

        # if device exists reset some of the settings (ok because device clearly isn't in use if it's registering)
        if existing:
            existing.gcm_id = gcm_id
            existing.claim_code = cls.generate_claim_code()
            existing.secret = cls.generate_secret()
            existing.country = country
            existing.device = device
            existing.save(update_fields=('gcm_id', 'secret', 'claim_code', 'country', 'device'))

            return existing

        # if any inactive channel has this UUID, we can steal it
        for ch in Channel.objects.filter(uuid=uuid, is_active=False):
            ch.uuid = generate_uuid()
            ch.save(update_fields=('uuid',))

        # generate random secret and claim code
        claim_code = cls.generate_claim_code()
        secret = cls.generate_secret()
        anon = User.objects.get(username=settings.ANONYMOUS_USER_NAME)

        return Channel.create(None, anon, country, Channel.TYPE_ANDROID, None, None, gcm_id=gcm_id, uuid=uuid,
                              device=device, claim_code=claim_code, secret=secret)

    @classmethod
    def generate_claim_code(cls):
        """
        Generates a random and guaranteed unique claim code
        """
        code = random_string(9)
        while cls.objects.filter(claim_code=code):  # pragma: no cover
            code = random_string(9)
        return code

    @classmethod
    def generate_secret(cls):
        """
        Generates a secret value used for command signing
        """
        return random_string(64)

    @classmethod
    def determine_encoding(cls, text, replace=False):
        """
        Determines what type of encoding should be used for the passed in SMS text.
        """
        # if this is plain gsm7, then we are good to go
        if is_gsm7(text):
            return Encoding.GSM7, text

        # if this doesn't look like GSM7 try to replace characters that are close enough
        if replace:
            replaced = replace_non_gsm7_accents(text)

            # great, this is now GSM7, let's send that
            if is_gsm7(replaced):
                return Encoding.REPLACED, replaced

        # otherwise, this is unicode
        return Encoding.UNICODE, text

    def has_sending_log(self):
        return self.channel_type != Channel.TYPE_ANDROID

    def has_configuration_page(self):
        """
        Whether or not this channel supports a configuration/settings page
        """
        return self.channel_type not in (Channel.TYPE_TWILIO, Channel.TYPE_ANDROID, Channel.TYPE_TWITTER, Channel.TYPE_TELEGRAM)

    def get_delegate_channels(self):
        if not self.org:  # detached channels can't have delegates
            return Channel.objects.none()

        return self.org.channels.filter(parent=self, is_active=True, org=self.org).order_by('-role')

    def set_fb_call_to_action_payload(self, payload):
        # register for get_started events
        url = 'https://graph.facebook.com/v2.6/%s/thread_settings' % self.address
        body = dict(setting_type='call_to_actions', thread_state='new_thread', call_to_actions=[])

        # if we have a payload, set it, otherwise, clear it
        if payload:
            body['call_to_actions'].append(dict(payload=payload))

        access_token = self.config_json()[Channel.CONFIG_AUTH_TOKEN]

        response = requests.post(url, json.dumps(body),
                                 params=dict(access_token=access_token),
                                 headers={'Content-Type': 'application/json'})

        if response.status_code != 200:
            raise Exception(_("Unable to update call to action: %s" % response.content))

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
        return self.get_delegate(Channel.ROLE_SEND)

    def get_caller(self):
        return self.get_delegate(Channel.ROLE_CALL)

    def get_parent_channel(self):
        """
        If we are a delegate channel, this will get us the parent channel.
        Otherwise, it will just return ourselves if we are the parent channel
        """
        if self.parent:
            return self.parent
        return self

    def is_delegate_sender(self):
        return self.parent and Channel.ROLE_SEND in self.role

    def is_delegate_caller(self):
        return self.parent and Channel.ROLE_CALL in self.role

    def get_ivr_client(self):
        if self.channel_type == Channel.TYPE_TWILIO:
            return self.org.get_twilio_client()
        if self.channel_type == Channel.TYPE_VERBOICE:
            return self.org.get_verboice_client()
        return None

    def supports_ivr(self):
        return Channel.ROLE_CALL in self.role or Channel.ROLE_ANSWER in self.role

    def get_name(self):  # pragma: no cover
        if self.name:
            return self.name
        elif self.device:
            return self.device
        else:
            return _("Android Phone")

    def get_channel_type_name(self):
        channel_type_display = self.get_channel_type_display()

        if self.channel_type == Channel.TYPE_ANDROID:
            return _("Android Phone")
        else:
            return _("%s Channel" % channel_type_display)

    def get_address_display(self, e164=False):
        from temba.contacts.models import TEL_SCHEME
        if not self.address:
            return ''

        if self.address and self.scheme == TEL_SCHEME and self.country:
            # assume that a number not starting with + is a short code and return as is
            if self.address[0] != '+':
                return self.address

            try:
                normalized = phonenumbers.parse(self.address, str(self.country))
                fmt = phonenumbers.PhoneNumberFormat.E164 if e164 else phonenumbers.PhoneNumberFormat.INTERNATIONAL
                return phonenumbers.format_number(normalized, fmt)
            except NumberParseException:
                # the number may be alphanumeric in the case of short codes
                pass

        elif self.channel_type == Channel.TYPE_TWITTER:
            return '@%s' % self.address

        elif self.channel_type == Channel.TYPE_FACEBOOK:
            return "%s (%s)" % (self.config_json().get(Channel.CONFIG_PAGE_NAME, self.name), self.address)

        return self.address

    def build_message_context(self):
        from temba.contacts.models import TEL_SCHEME

        address = self.get_address_display()
        default = address if address else self.__unicode__()

        # for backwards compatibility
        if self.scheme == TEL_SCHEME:
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
            self.claim_code = self.generate_claim_code()
            self.save(update_fields=('claim_code',))

        # create a secret if we don't have one
        if not self.secret:
            self.secret = self.generate_secret()
            self.save(update_fields=('secret',))

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
        # use our optimized index for our org outbox
        from temba.msgs.models import Msg
        return Msg.objects.filter(org=self.org.id, status__in=['P', 'Q'], direction='O',
                                  visibility='V').filter(channel=self, contact__is_test=False)

    def is_new(self):
        # is this channel newer than an hour
        return self.created_on > timezone.now() - timedelta(hours=1) or not self.get_last_sync()

    def is_ussd(self):
        return self.channel_type in Channel.USSD_CHANNELS

    def claim(self, org, user, phone):
        """
        Claims this channel for the given org/user
        """
        from temba.contacts.models import ContactURN

        if not self.country:
            self.country = ContactURN.derive_country_from_tel(phone)

        self.alert_email = user.email
        self.org = org
        self.is_active = True
        self.claim_code = None
        self.address = phone
        self.save()

        org.normalize_contact_tels()

    def release(self, trigger_sync=True, notify_mage=True):
        """
        Releases this channel, removing it from the org and making it inactive
        """
        # release any channels working on our behalf as well
        for delegate_channel in Channel.objects.filter(parent=self, org=self.org):
            delegate_channel.release()

        if not settings.DEBUG:
            # only call out to external aggregator services if not in debug mode

            # delete Plivo application
            if self.channel_type == Channel.TYPE_PLIVO:
                client = plivo.RestAPI(self.config_json()[Channel.CONFIG_PLIVO_AUTH_ID], self.config_json()[Channel.CONFIG_PLIVO_AUTH_TOKEN])
                client.delete_application(params=dict(app_id=self.config_json()[Channel.CONFIG_PLIVO_APP_ID]))

            # delete Twilio SMS application
            elif self.channel_type == Channel.TYPE_TWILIO:
                client = self.org.get_twilio_client()
                number_update_args = dict()

                if not self.is_delegate_sender():
                    number_update_args['sms_application_sid'] = ""

                if self.supports_ivr():
                    number_update_args['voice_application_sid'] = ""

                try:
                    client.phone_numbers.update(self.bod, **number_update_args)
                except Exception:
                    if client:
                        matching = client.phone_numbers.list(phone_number=self.address)
                        if matching:
                            client.phone_numbers.update(matching[0].sid, **number_update_args)

            # unsubscribe from facebook events for this page
            elif self.channel_type == Channel.TYPE_FACEBOOK:
                page_access_token = self.config_json()[Channel.CONFIG_AUTH_TOKEN]
                requests.delete('https://graph.facebook.com/v2.5/me/subscribed_apps',
                                params=dict(access_token=page_access_token))

        # save off our org and gcm id before nullifying
        org = self.org
        gcm_id = self.gcm_id

        # remove all identifying bits from the client
        self.org = None
        self.gcm_id = None
        self.secret = None
        self.claim_code = None
        self.is_active = False
        self.save()

        # mark any messages in sending mode as failed for this channel
        from temba.msgs.models import Msg, OUTGOING, PENDING, QUEUED, ERRORED, FAILED
        Msg.objects.filter(channel=self, direction=OUTGOING, status__in=[QUEUED, PENDING, ERRORED]).update(status=FAILED)

        # trigger the orphaned channel
        if trigger_sync and self.channel_type == Channel.TYPE_ANDROID:  # pragma: no cover
            self.trigger_sync(gcm_id)

        # clear our cache for this channel
        Channel.clear_cached_channel(self.id)

        if notify_mage and self.channel_type == Channel.TYPE_TWITTER:
            # notify Mage so that it deactivates this channel
            from .tasks import MageStreamAction, notify_mage_task
            notify_mage_task.delay(self.uuid, MageStreamAction.deactivate)

        # if we just lost calling capabilities archive our voice flows
        if Channel.ROLE_CALL in self.role:
            if not org.get_schemes(Channel.ROLE_CALL):
                # archive any IVR flows
                from temba.flows.models import Flow
                for flow in Flow.objects.filter(org=org, is_active=True, flow_type=Flow.VOICE):
                    flow.archive()

        # if we just lost answering capabilities, archive our inbound call trigger
        if Channel.ROLE_ANSWER in self.role:
            if not org.get_schemes(Channel.ROLE_ANSWER):
                from temba.triggers.models import Trigger
                Trigger.objects.filter(trigger_type=Trigger.TYPE_INBOUND_CALL, org=org, is_archived=False).update(is_archived=True)

        from temba.triggers.models import Trigger
        Trigger.objects.filter(channel=self, org=org).update(is_active=False)

    def trigger_sync(self, gcm_id=None):  # pragma: no cover
        """
        Sends a GCM command to trigger a sync on the client
        """
        # androids sync via GCM
        if self.channel_type == Channel.TYPE_ANDROID:
            if getattr(settings, 'GCM_API_KEY', None):
                from .tasks import sync_channel_task
                if not gcm_id:
                    gcm_id = self.gcm_id
                if gcm_id:
                    sync_channel_task.delay(gcm_id, channel_id=self.pk)

        # otherwise this is an aggregator, no-op
        else:
            raise Exception("Trigger sync called on non Android channel. [%d]" % self.pk)

    @classmethod
    def sync_channel(cls, gcm_id, channel=None):  # pragma: no cover
        try:
            gcm = GCM(settings.GCM_API_KEY)
            gcm.plaintext_request(registration_id=gcm_id, data=dict(msg='sync'))
        except GCMNotRegisteredException:
            if channel:
                # this gcm id is invalid now, clear it out
                channel.gcm_id = None
                channel.save()

    @classmethod
    def build_send_url(cls, url, variables):
        for key in variables.keys():
            url = url.replace("{{%s}}" % key, quote_plus(unicode(variables[key]).encode('utf-8')))

        return url

    @classmethod
    def send_jasmin_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED
        from temba.utils import gsm7

        # build our callback dlr url, jasmin will call this when our message is sent or delivered
        dlr_url = 'https://%s%s' % (settings.HOSTNAME, reverse('handlers.jasmin_handler', args=['status', channel.uuid]))

        # encode to GSM7
        encoded = gsm7.encode(text, 'replace')[0]

        # build our payload
        payload = dict()
        payload['from'] = channel.address.lstrip('+')
        payload['to'] = msg.urn_path.lstrip('+')
        payload['username'] = channel.config[Channel.CONFIG_USERNAME]
        payload['password'] = channel.config[Channel.CONFIG_PASSWORD]
        payload['dlr'] = dlr_url
        payload['dlr-level'] = '2'
        payload['dlr-method'] = 'POST'
        payload['coding'] = '0'
        payload['content'] = encoded

        log_payload = payload.copy()
        log_payload['password'] = 'x' * len(log_payload['password'])

        log_url = channel.config[Channel.CONFIG_SEND_URL] + "?" + urlencode(log_payload)
        start = time.time()

        try:
            response = requests.get(channel.config[Channel.CONFIG_SEND_URL], verify=True, params=payload, timeout=15)
        except Exception as e:
            raise SendException(unicode(e),
                                method='GET',
                                url=log_url,
                                request="",
                                response="",
                                response_status=503)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from Jasmin" % response.status_code,
                                method='GET',
                                url=log_url,
                                request="",
                                response=response.text,
                                response_status=response.status_code)

        # save the external id, response should be in format:
        # Success "07033084-5cfd-4812-90a4-e4d24ffb6e3d"
        external_id = None
        match = re.match(r"Success \"(.*)\"", response.text)
        if match:
            external_id = match.group(1)

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start, external_id)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='GET',
                               url=log_url,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_facebook_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED

        # build our payload
        payload = dict()
        payload['recipient'] = dict(id=msg.urn_path)
        payload['message'] = dict(text=text)
        payload = json.dumps(payload)

        url = "https://graph.facebook.com/v2.5/me/messages"
        params = dict(access_token=channel.config[Channel.CONFIG_AUTH_TOKEN])
        headers = {'Content-Type': 'application/json'}
        start = time.time()

        try:
            response = requests.post(url, payload, params=params, headers=headers, timeout=15)
        except Exception as e:
            raise SendException(unicode(e),
                                method='POST',
                                url=url,
                                request=payload,
                                response="",
                                response_status=503)

        if response.status_code != 200:
            raise SendException("Got non-200 response [%d] from Facebook" % response.status_code,
                                method='POST',
                                url=url,
                                request=payload,
                                response=response.text,
                                response_status=response.status_code)

        # grab our external id out, Facebook response is in format:
        # "{"recipient_id":"997011467086879","message_id":"mid.1459532331848:2534ddacc3993a4b78"}"
        external_id = None
        try:
            external_id = response.json()['message_id']
        except Exception as e:
            # if we can't pull out our message id, that's ok, we still sent
            pass

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start, external_id)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='POST',
                               url=url,
                               request=payload,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_mblox_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED

        # build our payload
        payload = dict()
        payload['from'] = channel.address.lstrip('+')
        payload['to'] = [msg.urn_path.lstrip('+')]
        payload['body'] = text
        payload['delivery_report'] = 'per_recipient'

        request_body = json.dumps(payload)

        url = 'https://api.mblox.com/xms/v1/%s/batches' % channel.config[Channel.CONFIG_USERNAME]
        headers = {'Content-Type': 'application/json',
                   'Authorization': 'Bearer %s' % channel.config[Channel.CONFIG_PASSWORD]}

        start = time.time()

        try:
            response = requests.post(url, request_body, headers=headers, timeout=15)
        except Exception as e:
            raise SendException(unicode(e),
                                method='POST',
                                url=url,
                                request=request_body,
                                response="",
                                response_status=503)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from MBlox" % response.status_code,
                                method='POST',
                                url=url,
                                request=request_body,
                                response=response.text,
                                response_status=response.status_code)

        # response in format:
        # {
        #  "id": "Oyi75urq5_yB",
        #  "to": [ "593997290044" ],
        #  "from": "18444651185",
        #  "canceled": false,
        #  "body": "Hello world.",
        #  "type": "mt_text",
        #  "created_at": "2016-03-30T17:55:03.683Z",
        #  "modified_at": "2016-03-30T17:55:03.683Z",
        #  "delivery_report": "none",
        #  "expire_at": "2016-04-02T17:55:03.683Z"
        # }
        try:
            response_json = response.json()
            external_id = response_json['id']
        except:
            raise SendException("Unable to parse response body from MBlox",
                                method='POST',
                                url=url,
                                request=request_body,
                                response=response.text,
                                response_status=response.status_code)

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start, external_id)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='POST',
                               url=url,
                               request=request_body,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_kannel_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED

        # build our callback dlr url, kannel will call this when our message is sent or delivered
        dlr_url = 'https://%s%s?id=%d&status=%%d' % (settings.HOSTNAME, reverse('handlers.kannel_handler', args=['status', channel.uuid]), msg.id)
        dlr_mask = 31

        # build our payload
        payload = dict()
        payload['from'] = channel.address
        payload['username'] = channel.config[Channel.CONFIG_USERNAME]
        payload['password'] = channel.config[Channel.CONFIG_PASSWORD]
        payload['text'] = text
        payload['to'] = msg.urn_path
        payload['dlr-url'] = dlr_url
        payload['dlr-mask'] = dlr_mask

        # should our to actually be in national format?
        use_national = channel.config.get(Channel.CONFIG_USE_NATIONAL, False)
        if use_national:
            # parse and remap our 'to' address
            parsed = phonenumbers.parse(msg.urn_path)
            payload['to'] = str(parsed.national_number)

        # figure out if we should send encoding or do any of our own substitution
        desired_encoding = channel.config.get(Channel.CONFIG_ENCODING, Channel.ENCODING_DEFAULT)

        # they want unicde, they get unicode!
        if desired_encoding == Channel.ENCODING_UNICODE:
            payload['coding'] = '2'

        # otherwise, if this is smart encoding, try to derive it
        elif desired_encoding == Channel.ENCODING_SMART:
            # if this is smart encoding, figure out what encoding we will use
            encoding, text = Channel.determine_encoding(text, replace=True)
            payload['text'] = text

            if encoding == Encoding.UNICODE:
                payload['coding'] = '2'

        log_payload = payload.copy()
        log_payload['password'] = 'x' * len(log_payload['password'])

        log_url = channel.config[Channel.CONFIG_SEND_URL]
        if log_url.find("?") >= 0:
            log_url += "&" + urlencode(log_payload)
        else:
            log_url += "?" + urlencode(log_payload)

        start = time.time()

        try:
            if channel.config.get(Channel.CONFIG_VERIFY_SSL, True):
                response = requests.get(channel.config[Channel.CONFIG_SEND_URL], verify=True, params=payload, timeout=15)
            else:
                response = requests.get(channel.config[Channel.CONFIG_SEND_URL], verify=False, params=payload, timeout=15)
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

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start)

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
                   'username': channel.config[Channel.CONFIG_USERNAME], 'password': channel.config[Channel.CONFIG_PASSWORD],
                   'msg': text}

        # build our send URL
        url = channel.config[Channel.CONFIG_SEND_URL] + "?" + urlencode(payload)
        log_payload = ""
        start = time.time()

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

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start)

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
        payload = {
            'id': str(msg.id),
            'text': text,
            'to': msg.urn_path,
            'to_no_plus': msg.urn_path.lstrip('+'),
            'from': channel.address,
            'from_no_plus': channel.address.lstrip('+'),
            'channel': str(channel.id)
        }

        # build our send URL
        url = Channel.build_send_url(channel.config[Channel.CONFIG_SEND_URL], payload)
        start = time.time()

        method = channel.config.get(Channel.CONFIG_SEND_METHOD, 'POST')

        headers = TEMBA_HEADERS.copy()
        if method in ('POST', 'PUT'):
            body = channel.config.get(Channel.CONFIG_SEND_BODY, Channel.CONFIG_DEFAULT_SEND_BODY)
            body = Channel.build_send_url(body, payload)
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            log_payload = body
        else:
            log_payload = None

        try:
            if method == 'POST':
                response = requests.post(url, data=body, headers=headers, timeout=5)
            elif method == 'PUT':
                response = requests.put(url, data=body, headers=headers, timeout=5)
            else:
                response = requests.get(url, headers=headers, timeout=5)

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

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method=method,
                               url=url,
                               request=log_payload,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_chikka_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED
        payload = {
            'message_type': 'SEND',
            'mobile_number': msg.urn_path.lstrip('+'),
            'shortcode': channel.address,
            'message_id': msg.id,
            'message': msg.text,
            'request_cost': 'FREE',
            'client_id': channel.config[Channel.CONFIG_USERNAME],
            'secret_key': channel.config[Channel.CONFIG_PASSWORD]
        }

        # if this is a response to a user SMS, then we need to set this as a reply
        if msg.response_to_id:
            response_to = Msg.objects.filter(id=msg.response_to_id).first()
            if response_to:
                payload['message_type'] = 'REPLY'
                payload['request_id'] = response_to.external_id

        # build our send URL
        url = 'https://post.chikka.com/smsapi/request'
        log_payload = payload.copy()
        log_payload['secret_key'] = 'x' * len(log_payload['secret_key'])

        start = time.time()

        try:
            response = requests.post(url, data=payload, headers=TEMBA_HEADERS, timeout=5)

        except Exception as e:
            raise SendException(unicode(e),
                                method='POST',
                                url=url,
                                request=log_payload,
                                response="",
                                response_status=503)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                method='POST',
                                url=url,
                                request=log_payload,
                                response=response.text,
                                response_status=response.status_code)

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='POST',
                               url=url,
                               request=log_payload,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_high_connection_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED
        payload = {
            'accountid': channel.config[Channel.CONFIG_USERNAME],
            'password': channel.config[Channel.CONFIG_PASSWORD],
            'text': text,
            'to': msg.urn_path,
            'ret_id': msg.id,
            'datacoding': 8,
            'userdata': 'textit',
            'ret_url': 'https://%s%s' % (settings.HOSTNAME, reverse('handlers.hcnx_handler', args=['status', channel.uuid])),
            'ret_mo_url': 'https://%s%s' % (settings.HOSTNAME, reverse('handlers.hcnx_handler', args=['receive', channel.uuid]))
        }

        # build our send URL
        url = 'https://highpushfastapi-v2.hcnx.eu/api' + '?' + urlencode(payload)
        log_payload = None
        start = time.time()

        try:
            response = requests.get(url, headers=TEMBA_HEADERS, timeout=30)
            log_payload = urlencode(payload)
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

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='GET',
                               url=url,
                               request=log_payload,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_blackmyna_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED
        payload = {
            'address': msg.urn_path,
            'senderaddress': channel.address,
            'message': text,
        }

        url = 'http://api.blackmyna.com/2/smsmessaging/outbound'
        log_payload = None
        external_id = None
        start = time.time()

        try:
            log_payload = urlencode(payload)

            response = requests.post(url, data=payload, headers=TEMBA_HEADERS, timeout=30,
                                     auth=(channel.config[Channel.CONFIG_USERNAME], channel.config[Channel.CONFIG_PASSWORD]))
            # parse our response, should be JSON that looks something like:
            # [{
            #   "recipient" : recipient_number_1,
            #   "id" : Unique_identifier (universally unique identifier UUID)
            # }]
            response_json = response.json()

            # we only care about the first piece
            if response_json and len(response_json) > 0:
                external_id = response_json[0].get('id', None)

        except Exception as e:
            raise SendException(unicode(e),
                                method='POST',
                                url=url,
                                request=log_payload,
                                response=response.text if response else '',
                                response_status=503)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                method='POST',
                                url=url,
                                request=log_payload,
                                response=response.text,
                                response_status=response.status_code)

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start, external_id=external_id)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='POST',
                               url=url,
                               request=log_payload,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_start_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED

        post_body = u"""
          <message>
            <service id="single" source=$$FROM$$ />
            <to>$$TO$$</to>
            <body content-type="plain/text" encoding="plain">$$BODY$$</body>
          </message>
        """
        post_body = post_body.replace("$$FROM$$", quoteattr(channel.address))
        post_body = post_body.replace("$$TO$$", escape(msg.urn_path))
        post_body = post_body.replace("$$BODY$$", escape(msg.text))
        post_body = post_body.encode('utf8')

        url = 'http://bulk.startmobile.com.ua/clients.php'

        start = time.time()
        try:
            headers = {'Content-Type': 'application/xml; charset=utf8'}
            headers.update(TEMBA_HEADERS)

            response = requests.post(url,
                                     data=post_body,
                                     headers=headers,
                                     auth=(channel.config[Channel.CONFIG_USERNAME], channel.config[Channel.CONFIG_PASSWORD]),
                                     timeout=30)
        except Exception as e:
            raise SendException(unicode(e),
                                method='POST',
                                url=url,
                                request=post_body.decode('utf8'),
                                response='',
                                response_status=503)

        if (response.status_code != 200 and response.status_code != 201) or response.text.find("error") >= 0:
            raise SendException("Error Sending Message",
                                method='POST',
                                url=url,
                                request=post_body.decode('utf8'),
                                response=response.text,
                                response_status=response.status_code)

        # parse out our id, this is XML but we only care about the id
        external_id = None
        start = response.text.find("<id>")
        end = response.text.find("</id>")
        if end > start > 0:
            external_id = response.text[start + 4:end]

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start, external_id=external_id)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='POST',
                               url=url,
                               request=post_body.decode('utf8'),
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_smscentral_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED

        # strip a leading +
        mobile = msg.urn_path[1:] if msg.urn_path.startswith('+') else msg.urn_path

        payload = {
            'user': channel.config[Channel.CONFIG_USERNAME], 'pass': channel.config[Channel.CONFIG_PASSWORD], 'mobile': mobile, 'content': text,
        }

        url = 'http://smail.smscentral.com.np/bp/ApiSms.php'
        log_payload = urlencode(payload)
        start = time.time()

        try:
            response = requests.post(url, data=payload, headers=TEMBA_HEADERS, timeout=30)

        except Exception as e:
            raise SendException(unicode(e),
                                method='POST',
                                url=url,
                                request=log_payload,
                                response='',
                                response_status=503)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                method='POST',
                                url=url,
                                request=log_payload,
                                response=response.text,
                                response_status=response.status_code)

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='POST',
                               url=url,
                               request=log_payload,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_vumi_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED
        from temba.contacts.models import Contact

        is_ussd = channel.channel_type in Channel.USSD_CHANNELS
        channel.config['transport_name'] = 'ussd_transport' if is_ussd else 'mtech_ng_smpp_transport'

        payload = dict(message_id=msg.id,
                       in_reply_to=None,
                       session_event="resume" if is_ussd else None,
                       to_addr=msg.urn_path,
                       from_addr=channel.address,
                       content=text,
                       transport_name=channel.config['transport_name'],
                       transport_type='ussd' if is_ussd else 'sms',
                       transport_metadata={},
                       helper_metadata={})

        payload = json.dumps(payload)

        headers = dict(TEMBA_HEADERS)
        headers['content-type'] = 'application/json'

        url = 'https://go.vumi.org/api/v1/go/http_api_nostream/%s/messages.json' % channel.config['conversation_key']
        start = time.time()

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

        if response.status_code not in (200, 201):
            # this is a fatal failure, don't retry
            fatal = response.status_code == 400

            # if this is fatal due to the user opting out, stop them
            if response.text and response.text.find('has opted out') >= 0:
                contact = Contact.objects.get(id=msg.contact)
                contact.stop(contact.modified_by)
                fatal = True

            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                method='PUT',
                                url=url,
                                request=payload,
                                response=response.text,
                                response_status=response.status_code,
                                fatal=fatal)

        # parse our response
        body = response.json()

        # mark our message as sent
        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start, external_id=body.get('message_id', ''))

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='PUT',
                               url=url,
                               request=payload,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_globe_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED

        payload = {
            'address': msg.urn_path.lstrip('+'),
            'message': text,
            'passphrase': channel.config['passphrase'],
            'app_id': channel.config['app_id'],
            'app_secret': channel.config['app_secret']
        }
        headers = dict(TEMBA_HEADERS)

        url = 'https://devapi.globelabs.com.ph/smsmessaging/v1/outbound/%s/requests' % channel.address
        start = time.time()

        try:
            response = requests.post(url,
                                     data=payload,
                                     headers=headers,
                                     timeout=5)
        except Exception as e:
            raise SendException(unicode(e),
                                method='POST',
                                url=url,
                                request=payload,
                                response="",
                                response_status=503)

        if response.status_code != 200 and response.status_code != 201:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                method='POST',
                                url=url,
                                request=payload,
                                response=response.text,
                                response_status=response.status_code)

        # parse our response
        response.json()

        # mark our message as sent
        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='POST',
                               url=url,
                               request=payload,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_nexmo_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, SENT
        from temba.orgs.models import NEXMO_KEY, NEXMO_SECRET

        client = NexmoClient(channel.org_config[NEXMO_KEY], channel.org_config[NEXMO_SECRET])
        start = time.time()

        attempts = 0
        response = None
        while not response:
            try:
                (message_id, response) = client.send_message(channel.address, msg.urn_path, text)
            except SendException as e:
                match = regex.match(r'.*Throughput Rate Exceeded - please wait \[ (\d+) \] and retry.*', e.response)

                # this is a throughput failure, attempt to wait up to three times
                if match and attempts < 3:
                    sleep(float(match.group(1)) / 1000)
                    attempts += 1
                else:
                    raise e

        Msg.mark_sent(channel.config['r'], channel, msg, SENT, time.time() - start, external_id=message_id)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered to Nexmo",
                               method=response.request.method,
                               url=response.request.url,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_yo_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, SENT
        from temba.contacts.models import Contact

        # build our message dict
        params = dict(origin=channel.address.lstrip('+'),
                      sms_content=text,
                      destinations=msg.urn_path.lstrip('+'),
                      ybsacctno=channel.config['username'],
                      password=channel.config['password'])
        log_params = params.copy()
        log_params['password'] = 'x' * len(log_params['password'])

        start = time.time()
        failed = False
        fatal = False

        for send_url in [Channel.YO_API_URL_1, Channel.YO_API_URL_2, Channel.YO_API_URL_3]:
            url = send_url + '?' + urlencode(params)
            log_url = send_url + '?' + urlencode(log_params)

            failed = False
            try:
                response = requests.get(url, headers=TEMBA_HEADERS, timeout=5)
                response_qs = urlparse.parse_qs(response.text)
            except Exception:
                failed = True

            if not failed and response.status_code != 200 and response.status_code != 201:
                failed = True

            # if it wasn't successfully delivered, throw
            if not failed and response_qs.get('ybs_autocreate_status', [''])[0] != 'OK':
                failed = True

            # check if we failed permanently (they blocked us)
            if failed and response_qs.get('ybs_autocreate_message', [''])[0].find('BLACKLISTED') >= 0:
                contact = Contact.objects.get(id=msg.contact)
                contact.stop(contact.modified_by)
                fatal = True
                break

            # if we sent the message, then move on
            if not failed:
                break

        if failed:
            raise SendException("Received error from Yo! API",
                                url=log_url,
                                method='GET',
                                request='',
                                response=response.text,
                                response_status=response.status_code,
                                fatal=fatal)

        Msg.mark_sent(channel.config['r'], channel, msg, SENT, time.time() - start)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               url=log_url,
                               method='GET',
                               request='',
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
        start = time.time()

        try:
            response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=5)
        except Exception:
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

        Msg.mark_sent(channel.config['r'], channel, msg, SENT, time.time() - start, external_id=messages[0]['messageid'])

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
        start = time.time()

        try:
            response = requests.get(send_url, proxies=OUTGOING_PROXIES, headers=TEMBA_HEADERS, timeout=15)
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

            Msg.mark_sent(channel.config['r'], channel, msg, SENT, time.time() - start)

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
            except Exception:
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
        start = time.time()

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

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start)

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

        # if this isn't a shared shortcode, send the from address
        if not channel.config.get('is_shared', False):
            payload['from'] = channel.address

        headers = dict(Accept='application/json', apikey=channel.config['api_key'])
        headers.update(TEMBA_HEADERS)

        api_url = "https://api.africastalking.com/version1/messaging"
        start = time.time()

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

        Msg.mark_sent(channel.config['r'], channel, msg, SENT, time.time() - start, external_id=external_id)

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
        start = time.time()

        if channel.channel_type == Channel.TYPE_TWILIO_MESSAGING_SERVICE:
            messaging_service_sid = channel.config['messaging_service_sid']
            client.messages.create(to=msg.urn_path,
                                   messaging_service_sid=messaging_service_sid,
                                   body=text,
                                   status_callback=callback_url)
        else:
            client.messages.create(to=msg.urn_path,
                                   from_=channel.address,
                                   body=text,
                                   status_callback=callback_url)

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start)
        ChannelLog.log_success(msg, "Successfully delivered message")

    @classmethod
    def send_telegram_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED
        start = time.time()

        auth_token = channel.config[Channel.CONFIG_AUTH_TOKEN]
        send_url = 'https://api.telegram.org/bot%s/sendMessage' % auth_token
        post_body = dict(chat_id=msg.urn_path, text=text)

        try:
            response = requests.post(send_url, post_body)
            external_id = response.json()['result']['message_id']
        except Exception as e:
            raise SendException(str(e),
                                send_url,
                                'POST',
                                urlencode(post_body),
                                response.content,
                                505)

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start, external_id=external_id)
        ChannelLog.log_success(msg, "Successfully delivered message")

    @classmethod
    def send_twitter_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED
        from temba.contacts.models import Contact

        consumer_key = settings.TWITTER_API_KEY
        consumer_secret = settings.TWITTER_API_SECRET
        oauth_token = channel.config['oauth_token']
        oauth_token_secret = channel.config['oauth_token_secret']

        twitter = Twython(consumer_key, consumer_secret, oauth_token, oauth_token_secret)
        start = time.time()

        try:
            dm = twitter.send_direct_message(screen_name=msg.urn_path, text=text)
        except Exception as e:
            error_code = getattr(e, 'error_code', 400)
            fatal = False

            if error_code == 404:  # handle doesn't exist
                fatal = True
            elif error_code == 403:
                for err in Channel.TWITTER_FATAL_403S:
                    if unicode(e).find(err) >= 0:
                        fatal = True
                        break

            # if message can never be sent, stop them contact
            if fatal:
                contact = Contact.objects.get(id=msg.contact)
                contact.stop(contact.modified_by)

            raise SendException(str(e),
                                'https://api.twitter.com/1.1/direct_messages/new.json',
                                'POST',
                                urlencode(dict(screen_name=msg.urn_path, text=text)),  # not complete, but useful in the log
                                str(e),
                                error_code,
                                fatal=fatal)

        external_id = dm['id']

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start, external_id=external_id)
        ChannelLog.log_success(msg, "Successfully delivered message")

    @classmethod
    def send_clickatell_message(cls, channel, msg, text):
        """
        Sends a message to Clickatell, they expect a GET in the following format:
             https://api.clickatell.com/http/sendmsg?api_id=xxx&user=xxxx&password=xxxx&to=xxxxx&text=xxxx
        """
        from temba.msgs.models import Msg, WIRED

        # determine our encoding
        encoding, text = Channel.determine_encoding(text, replace=True)

        # if this looks like unicode, ask clickatell to send as unicode
        if encoding == Encoding.UNICODE:
            unicode_switch = 1
        else:
            unicode_switch = 0

        url = 'https://api.clickatell.com/http/sendmsg'
        payload = {'api_id': channel.config[Channel.CONFIG_API_ID],
                   'user': channel.config[Channel.CONFIG_USERNAME],
                   'password': channel.config[Channel.CONFIG_PASSWORD],
                   'from': channel.address.lstrip('+'),
                   'concat': 3,
                   'callback': 7,
                   'mo': 1,
                   'unicode': unicode_switch,
                   'to': msg.urn_path.lstrip('+'),
                   'text': text}
        start = time.time()

        try:
            response = requests.get(url, params=payload, headers=TEMBA_HEADERS, timeout=5)
            log_payload = urlencode(payload)

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

        # parse out the external id for the message, comes in the format: "ID: id12312312312"
        external_id = None
        if response.text.startswith("ID: "):
            external_id = response.text[4:]

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start, external_id=external_id)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='GET',
                               url=url,
                               request=log_payload,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_plivo_message(cls, channel, msg, text):
        import plivo
        from temba.msgs.models import Msg, WIRED

        # url used for logs and exceptions
        url = 'https://api.plivo.com/v1/Account/%s/Message/' % channel.config[Channel.CONFIG_PLIVO_AUTH_ID]

        client = plivo.RestAPI(channel.config[Channel.CONFIG_PLIVO_AUTH_ID], channel.config[Channel.CONFIG_PLIVO_AUTH_TOKEN])
        status_url = "https://" + settings.TEMBA_HOST + "%s" % reverse('handlers.plivo_handler',
                                                                       args=['status', channel.uuid])

        payload = {'src': channel.address.lstrip('+'),
                   'dst': msg.urn_path.lstrip('+'),
                   'text': text,
                   'url': status_url,
                   'method': 'POST'}
        start = time.time()

        try:
            plivo_response_status, plivo_response = client.send_message(params=payload)
        except Exception as e:
            raise SendException(unicode(e),
                                method='POST',
                                url=url,
                                request=json.dumps(payload),
                                response="",
                                response_status=503)

        if plivo_response_status != 200 and plivo_response_status != 201 and plivo_response_status != 202:
            raise SendException("Got non-200 response [%d] from API" % plivo_response_status,
                                method='POST',
                                url=url,
                                request=json.dumps(payload),
                                response=plivo_response,
                                response_status=plivo_response_status)

        external_id = plivo_response['message_uuid'][0]
        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start, external_id)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='POST',
                               url=url,
                               request=json.dumps(payload),
                               response=plivo_response,
                               response_status=plivo_response_status)

    @classmethod
    def send_m3tech_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED

        # determine our encoding
        encoding, text = Channel.determine_encoding(text, replace=True)

        # if this looks like unicode, ask m3tech to send as unicode
        if encoding == Encoding.UNICODE:
            sms_type = '7'
        else:
            sms_type = '0'

        url = 'https://secure.m3techservice.com/GenericServiceRestAPI/api/SendSMS'
        payload = {'AuthKey': 'm3-Tech',
                   'UserId': channel.config[Channel.CONFIG_USERNAME],
                   'Password': channel.config[Channel.CONFIG_PASSWORD],
                   'MobileNo': msg.urn_path.lstrip('+'),
                   'MsgId': msg.id,
                   'SMS': text,
                   'MsgHeader': channel.address.lstrip('+'),
                   'SMSType': sms_type,
                   'HandsetPort': '0',
                   'SMSChannel': '0',
                   'Telco': '0'}

        start = time.time()

        log_payload = urlencode(payload)

        try:
            response = requests.get(url, params=payload, headers=TEMBA_HEADERS, timeout=5)

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

        # our response is JSON and should contain a 0 as a status code:
        # [{"Response":"0"}]
        response_code = ""
        try:
            response_code = json.loads(response.text)[0]["Response"]
        except Exception as e:
            response_code = str(e)

        # <Response>0</Response>
        if response_code != "0":
            raise SendException("Received non-zero status from API: %s" % str(response_code),
                                method='GET',
                                url=url,
                                request=log_payload,
                                response=response.text,
                                response_status=response.status_code)

        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='GET',
                               url=url,
                               request=log_payload,
                               response=response.text,
                               response_status=response.status_code)

    @classmethod
    def send_viber_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED

        url = 'https://services.viber.com/vibersrvc/1/send_message'
        payload = {'service_id': int(channel.address),
                   'dest': msg.urn_path.lstrip('+'),
                   'seq': msg.id,
                   'type': 206,
                   'message': {
                       '#txt': text,
                       '#tracking_data': 'tracking_id:%d' % msg.id}}
        start = time.time()

        headers = dict(Accept='application/json')
        headers.update(TEMBA_HEADERS)

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=5)
            response_json = response.json()
        except Exception as e:
            raise SendException(unicode(e),
                                method='POST',
                                url=url,
                                request=json.dumps(payload),
                                response="",
                                response_status=503)

        if response.status_code not in [200, 201, 202]:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                method='POST',
                                url=url,
                                request=json.dumps(payload),
                                response=response.content,
                                response_status=response.status_code)

        # success is 0, everything else is a failure
        if response_json['status'] != 0:
            print "failing: %s" % response.content
            raise SendException("Got non-0 status [%d] from API" % response_json['status'],
                                method='POST',
                                url=url,
                                request=json.dumps(payload),
                                response=response.content,
                                response_status=response.status_code,
                                fatal=True)

        external_id = response.json().get('message_token', None)
        Msg.mark_sent(channel.config['r'], channel, msg, WIRED, time.time() - start, external_id)

        ChannelLog.log_success(msg=msg,
                               description="Successfully delivered",
                               method='POST',
                               url=url,
                               request=json.dumps(payload),
                               response=response.content,
                               response_status=response.status_code)

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
        hours_ago = now - timedelta(hours=12)

        pending = Msg.objects.filter(org=org, direction=OUTGOING)
        pending = pending.filter(Q(status=PENDING) |
                                 Q(status=QUEUED, queued_on__lte=hours_ago) |
                                 Q(status=ERRORED, next_attempt__lte=now))
        pending = pending.exclude(channel__channel_type=Channel.TYPE_ANDROID)

        # only SMS'es that have a topup and aren't the test contact
        pending = pending.exclude(topup=None).exclude(contact__is_test=True)

        # order then first by priority, then date
        pending = pending.order_by('-priority', 'created_on')
        return pending

    @classmethod
    def send_message(cls, msg):  # pragma: no cover
        from temba.msgs.models import Msg, QUEUED, WIRED, MSG_SENT_KEY
        r = get_redis_connection()

        # check whether this message was already sent somehow
        pipe = r.pipeline()
        pipe.sismember(timezone.now().strftime(MSG_SENT_KEY), str(msg.id))
        pipe.sismember((timezone.now() - timedelta(days=1)).strftime(MSG_SENT_KEY), str(msg.id))
        (sent_today, sent_yesterday) = pipe.execute()

        # get our cached channel
        channel = Channel.get_cached_channel(msg.channel)

        if sent_today or sent_yesterday:
            Msg.mark_sent(r, channel, msg, WIRED, -1)
            print "!! [%d] prevented duplicate send" % (msg.id)
            return

        # channel can be none in the case where the channel has been removed
        if not channel:
            Msg.mark_error(r, None, msg, fatal=True)
            ChannelLog.log_error(msg, _("Message no longer has a way of being sent, marking as failed."))
            return

        # populate redis in our config
        channel.config['r'] = r

        type_settings = Channel.CHANNEL_SETTINGS[channel.channel_type]

        # Check whether we need to throttle ourselves
        # This isn't an ideal implementation, in that if there is only one Channel with tons of messages
        # and a low throttle rate, we will have lots of threads waiting, but since throttling is currently
        # a rare event, this is an ok stopgap.
        max_tps = type_settings.get('max_tps', 0)
        if max_tps:
            tps_set_name = 'channel_tps_%d' % channel.id
            lock_name = '%s_lock' % tps_set_name

            while True:
                # only one thread should be messing with the map at once
                with r.lock(lock_name, timeout=5):
                    # check how many were sent in the last second
                    now = time.time()
                    last_second = time.time() - 1

                    # how many have been sent in the past second?
                    count = r.zcount(tps_set_name, last_second, now)

                    # we're within our tps, add ourselves to the list and go on our way
                    if count < max_tps:
                        r.zadd(tps_set_name, now, now)
                        r.zremrangebyscore(tps_set_name, "-inf", last_second)
                        r.expire(tps_set_name, 5)
                        break

                # too many sent in the last second, sleep a bit and try again
                time.sleep(1 / float(max_tps))

        sent_count = 0
        parts = Msg.get_text_parts(msg.text, type_settings['max_length'])
        for part in parts:
            sent_count += 1
            try:
                channel_type = channel.channel_type

                # never send in debug unless overridden
                if not settings.SEND_MESSAGES:
                    Msg.mark_sent(r, channel, msg, WIRED, -1)
                    print "FAKED SEND for [%d] - %s" % (msg.id, part)
                elif channel_type in SEND_FUNCTIONS:
                    SEND_FUNCTIONS[channel_type](channel, msg, part)
                else:
                    sent_count -= 1
                    raise Exception(_("Unknown channel type: %(channel)s") % {'channel': channel.channel_type})
            except SendException as e:
                ChannelLog.log_exception(msg, e)

                import traceback
                traceback.print_exc(e)

                Msg.mark_error(r, channel, msg, fatal=e.fatal)
                sent_count -= 1

            except Exception as e:
                ChannelLog.log_error(msg, unicode(e))

                import traceback
                traceback.print_exc(e)

                Msg.mark_error(r, channel, msg)
                sent_count -= 1

            finally:
                # if we are still in a queued state, mark ourselves as an error
                if msg.status == QUEUED:
                    print "!! [%d] marking queued message as error" % msg.id
                    Msg.mark_error(r, channel, msg)
                    sent_count -= 1

        # update the number of sms it took to send this if it was more than 1
        if len(parts) > 1:
            Msg.objects.filter(pk=msg.id).update(msg_count=len(parts))

    @classmethod
    def track_status(cls, channel, status):
        if channel:
            # track success, errors and failures
            analytics.gauge('temba.channel_%s_%s' % (status.lower(), channel.channel_type.lower()))

    @classmethod
    def build_twilio_callback_url(cls, sms_id):
        url = "https://" + settings.TEMBA_HOST + "/api/v1/twilio/?action=callback&id=%d" % sms_id
        return url

    def __unicode__(self):  # pragma: no cover
        if self.name:
            return self.name
        elif self.device:
            return self.device
        elif self.address:
            return self.address
        else:
            return unicode(self.pk)

    def get_count(self, count_types):
        count = ChannelCount.objects.filter(channel=self, count_type__in=count_types)\
                                    .aggregate(Sum('count')).get('count__sum', 0)

        return 0 if count is None else count

    def get_msg_count(self):
        return self.get_count([ChannelCount.INCOMING_MSG_TYPE, ChannelCount.OUTGOING_MSG_TYPE])

    def get_ivr_count(self):
        return self.get_count([ChannelCount.INCOMING_IVR_TYPE, ChannelCount.OUTGOING_IVR_TYPE])

    def get_log_count(self):
        return self.get_count([ChannelCount.SUCCESS_LOG_TYPE, ChannelCount.ERROR_LOG_TYPE])

    def get_error_log_count(self):
        return self.get_count([ChannelCount.ERROR_LOG_TYPE])

    def get_success_log_count(self):
        return self.get_count([ChannelCount.SUCCESS_LOG_TYPE])

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

SEND_FUNCTIONS = {Channel.TYPE_AFRICAS_TALKING: Channel.send_africas_talking_message,
                  Channel.TYPE_BLACKMYNA: Channel.send_blackmyna_message,
                  Channel.TYPE_CHIKKA: Channel.send_chikka_message,
                  Channel.TYPE_CLICKATELL: Channel.send_clickatell_message,
                  Channel.TYPE_EXTERNAL: Channel.send_external_message,
                  Channel.TYPE_FACEBOOK: Channel.send_facebook_message,
                  Channel.TYPE_GLOBE: Channel.send_globe_message,
                  Channel.TYPE_HIGH_CONNECTION: Channel.send_high_connection_message,
                  Channel.TYPE_HUB9: Channel.send_hub9_message,
                  Channel.TYPE_INFOBIP: Channel.send_infobip_message,
                  Channel.TYPE_JASMIN: Channel.send_jasmin_message,
                  Channel.TYPE_KANNEL: Channel.send_kannel_message,
                  Channel.TYPE_M3TECH: Channel.send_m3tech_message,
                  Channel.TYPE_MBLOX: Channel.send_mblox_message,
                  Channel.TYPE_NEXMO: Channel.send_nexmo_message,
                  Channel.TYPE_PLIVO: Channel.send_plivo_message,
                  Channel.TYPE_SHAQODOON: Channel.send_shaqodoon_message,
                  Channel.TYPE_SMSCENTRAL: Channel.send_smscentral_message,
                  Channel.TYPE_START: Channel.send_start_message,
                  Channel.TYPE_TELEGRAM: Channel.send_telegram_message,
                  Channel.TYPE_TWILIO: Channel.send_twilio_message,
                  Channel.TYPE_TWILIO_MESSAGING_SERVICE: Channel.send_twilio_message,
                  Channel.TYPE_TWITTER: Channel.send_twitter_message,
                  Channel.TYPE_VIBER: Channel.send_viber_message,
                  Channel.TYPE_VUMI: Channel.send_vumi_message,
                  Channel.TYPE_VUMI_USSD: Channel.send_vumi_message,
                  Channel.TYPE_YO: Channel.send_yo_message,
                  Channel.TYPE_ZENVIA: Channel.send_zenvia_message}


class ChannelCount(models.Model):
    """
    This model is maintained by Postgres triggers and maintains the daily counts of messages and ivr interactions
    on each day. This allows for fast visualizations of activity on the channel read page as well as summaries
    of message usage over the course of time.
    """
    LAST_SQUASH_KEY = 'last_channelcount_squash'

    INCOMING_MSG_TYPE = 'IM'  # Incoming message
    OUTGOING_MSG_TYPE = 'OM'  # Outgoing message
    INCOMING_IVR_TYPE = 'IV'  # Incoming IVR step
    OUTGOING_IVR_TYPE = 'OV'  # Outgoing IVR step
    SUCCESS_LOG_TYPE = 'LS'   # ChannelLog record
    ERROR_LOG_TYPE = 'LE'     # ChannelLog record that is an error

    COUNT_TYPE_CHOICES = ((INCOMING_MSG_TYPE, _("Incoming Message")),
                          (OUTGOING_MSG_TYPE, _("Outgoing Message")),
                          (INCOMING_IVR_TYPE, _("Incoming Voice")),
                          (OUTGOING_IVR_TYPE, _("Outgoing Voice")),
                          (SUCCESS_LOG_TYPE, _("Success Log Record")),
                          (ERROR_LOG_TYPE, _("Error Log Record")))

    channel = models.ForeignKey(Channel,
                                help_text=_("The channel this is a daily summary count for"))
    count_type = models.CharField(choices=COUNT_TYPE_CHOICES, max_length=2,
                                  help_text=_("What type of message this row is counting"))
    day = models.DateField(null=True, help_text=_("The day this count is for"))
    count = models.IntegerField(default=0,
                                help_text=_("The count of messages on this day and type"))

    @classmethod
    def get_day_count(cls, channel, count_type, day):
        count = ChannelCount.objects.filter(channel=channel, count_type=count_type, day=day)
        count = count.order_by('day', 'count_type').aggregate(count_sum=Sum('count'))

        return 0 if not count else count['count_sum']

    @classmethod
    def squash_counts(cls):
        # get the id of the last count we squashed
        r = get_redis_connection()
        last_squash = r.get(ChannelCount.LAST_SQUASH_KEY)
        if not last_squash:
            last_squash = 0

        # get the unique ids for all new ones
        start = time.time()
        squash_count = 0
        for count in ChannelCount.objects.filter(id__gt=last_squash).order_by('channel_id', 'count_type', 'day')\
                                                                    .distinct('channel_id', 'count_type', 'day'):

            # perform our atomic squash in SQL by calling our squash method
            with connection.cursor() as c:
                c.execute("SELECT temba_squash_channelcount(%s, %s, %s);", (count.channel_id, count.count_type, count.day))

            squash_count += 1

        # insert our new top squashed id
        max_id = ChannelCount.objects.all().order_by('-id').first()
        if max_id:
            r.set(ChannelCount.LAST_SQUASH_KEY, max_id.id)

        print "Squashed channel counts for %d pairs in %0.3fs" % (squash_count, time.time() - start)

    def __unicode__(self):
        return "ChannelCount(%d) %s %s count: %d" % (self.channel_id, self.count_type, self.day, self.count)

    class Meta:
        index_together = ['channel', 'count_type', 'day']


class ChannelEvent(models.Model):
    """
    An event other than a message that occurs between a channel and a contact. Can be used to trigger flows etc.
    """
    TYPE_UNKNOWN = 'unknown'
    TYPE_CALL_OUT = 'mt_call'
    TYPE_CALL_OUT_MISSED = 'mt_miss'
    TYPE_CALL_IN = 'mo_call'
    TYPE_CALL_IN_MISSED = 'mo_miss'

    # single char flag, human readable name, API readable name
    TYPE_CONFIG = ((TYPE_UNKNOWN, _("Unknown Call Type"), 'unknown'),
                   (TYPE_CALL_OUT, _("Outgoing Call"), 'call-out'),
                   (TYPE_CALL_OUT_MISSED, _("Missed Outgoing Call"), 'call-out-missed'),
                   (TYPE_CALL_IN, _("Incoming Call"), 'call-in'),
                   (TYPE_CALL_IN_MISSED, _("Missed Incoming Call"), 'call-in-missed'))

    TYPE_CHOICES = [(t[0], t[1]) for t in TYPE_CONFIG]

    CALL_TYPES = {TYPE_CALL_OUT, TYPE_CALL_OUT_MISSED, TYPE_CALL_IN, TYPE_CALL_IN_MISSED}

    org = models.ForeignKey(Org, verbose_name=_("Org"),
                            help_text=_("The org this event is connected to"))
    channel = models.ForeignKey(Channel, verbose_name=_("Channel"),
                                help_text=_("The channel on which this event took place"))
    event_type = models.CharField(max_length=16, choices=TYPE_CHOICES, verbose_name=_("Event Type"),
                                  help_text=_("The type of event"))
    contact = models.ForeignKey('contacts.Contact', verbose_name=_("Contact"), related_name='channel_events',
                                help_text=_("The contact associated with this event"))
    contact_urn = models.ForeignKey('contacts.ContactURN', null=True, verbose_name=_("URN"), related_name='channel_events',
                                    help_text=_("The contact URN associated with this event"))
    time = models.DateTimeField(verbose_name=_("Time"),
                                help_text=_("When this event took place"))
    duration = models.IntegerField(default=0, verbose_name=_("Duration"),
                                   help_text=_("Duration in seconds if event is a call"))
    created_on = models.DateTimeField(verbose_name=_("Created On"), default=timezone.now,
                                      help_text=_("When this event was created"))
    is_active = models.BooleanField(default=True,
                                    help_text="Whether this item is active, use this instead of deleting")

    @classmethod
    def create(cls, channel, urn, event_type, date, duration=0):
        from temba.api.models import WebHookEvent
        from temba.contacts.models import Contact
        from temba.triggers.models import Trigger

        org = channel.org
        user = User.objects.get(username=settings.ANONYMOUS_USER_NAME)

        contact = Contact.get_or_create(org, user, name=None, urns=[urn], channel=channel)
        contact_urn = contact.urn_objects[urn]

        event = cls.objects.create(org=org, channel=channel, contact=contact, contact_urn=contact_urn,
                                   time=date, duration=duration, event_type=event_type)

        if event_type in cls.CALL_TYPES:
            analytics.gauge('temba.call_%s' % event.get_event_type_display().lower().replace(' ', '_'))

            WebHookEvent.trigger_call_event(event)

        if event_type == cls.TYPE_CALL_IN_MISSED:
            Trigger.catch_triggers(event, Trigger.TYPE_MISSED_CALL, channel)

        return event

    @classmethod
    def get_all(cls, org):
        return cls.objects.filter(org=org, is_active=True)

    def release(self):
        self.is_active = False
        self.save(update_fields=('is_active',))


class SendException(Exception):

    def __init__(self, description, url, method, request, response, response_status, fatal=False):
        super(SendException, self).__init__(description)

        self.description = description
        self.url = url
        self.method = method
        self.request = request
        self.response = response
        self.response_status = response_status
        self.fatal = fatal


class ChannelLog(models.Model):
    channel = models.ForeignKey(Channel, related_name='logs',
                                help_text=_("The channel the message was sent on"))
    msg = models.ForeignKey('msgs.Msg', related_name='channel_logs',
                            help_text=_("The message that was sent"))
    description = models.CharField(max_length=255,
                                   help_text=_("A description of the status of this message send"))
    is_error = models.BooleanField(default=None,
                                   help_text=_("Whether an error was encountered when sending the message"))
    url = models.TextField(null=True,
                           help_text=_("The URL used when sending the message"))
    method = models.CharField(max_length=16, null=True,
                              help_text=_("The HTTP method used when sending the message"))
    request = models.TextField(null=True,
                               help_text=_("The body of the request used when sending the message"))
    response = models.TextField(null=True,
                                help_text=_("The body of the response received when sending the message"))
    response_status = models.IntegerField(null=True,
                                          help_text=_("The response code received when sending the message"))
    created_on = models.DateTimeField(auto_now_add=True,
                                      help_text=_("When this log message was logged"))

    @classmethod
    def write(cls, log):
        if log.is_error:
            print(u"[%d] ERROR - %s %s \"%s\" %s \"%s\"" %
                  (log.msg.pk, log.method, log.url, log.request, log.response_status, log.response))
        else:
            print(u"[%d] SENT - %s %s \"%s\" %s \"%s\"" %
                  (log.msg.pk, log.method, log.url, log.request, log.response_status, log.response))

    @classmethod
    def log_exception(cls, msg, e):
        cls.write(ChannelLog.objects.create(channel_id=msg.channel,
                                            msg_id=msg.id,
                                            is_error=True,
                                            description=unicode(e.description)[:255],
                                            method=e.method,
                                            url=e.url,
                                            request=e.request,
                                            response=e.response,
                                            response_status=e.response_status))

    @classmethod
    def log_error(cls, msg, description):
        cls.write(ChannelLog.objects.create(channel_id=msg.channel,
                                            msg_id=msg.id,
                                            is_error=True,
                                            description=description[:255]))

    @classmethod
    def log_success(cls, msg, description, method=None, url=None, request=None, response=None, response_status=None):
        cls.write(ChannelLog.objects.create(channel_id=msg.channel,
                                            msg_id=msg.id,
                                            is_error=False,
                                            description=description[:255],
                                            method=method,
                                            url=url,
                                            request=request,
                                            response=response,
                                            response_status=response_status))


class SyncEvent(SmartModel):
    channel = models.ForeignKey(Channel, verbose_name=_("Channel"),
                                help_text=_("The channel that synced to the server"))
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
        device = cmd.get('dev', None)
        os = cmd.get('os', None)

        # update our channel if anything is new
        if channel.device != device or channel.os != os:
            Channel.objects.filter(pk=channel.pk).update(device=device, os=os)

        args = dict()

        args['power_source'] = cmd.get('p_src', cmd.get('power_source'))
        args['power_status'] = cmd.get('p_sts', cmd.get('power_status'))
        args['power_level'] = cmd.get('p_lvl', cmd.get('power_level'))

        args['network_type'] = cmd.get('net', cmd.get('network_type'))

        args['pending_message_count'] = len(cmd.get('pending', cmd.get('pending_messages')))
        args['retry_message_count'] = len(cmd.get('retry', cmd.get('retry_messages')))
        args['incoming_command_count'] = max(len(incoming_commands) - 2, 0)

        anon_user = User.objects.get(username=settings.ANONYMOUS_USER_NAME)
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
    if kwargs['raw']:
        return

    if not instance.pk:
        last_sync_event = SyncEvent.objects.filter(channel=instance.channel).order_by('-created_on').first()
        if last_sync_event:
            td = (timezone.now() - last_sync_event.created_on)
            last_sync_event.lifetime = td.seconds + td.days * 24 * 3600
            last_sync_event.save()


class Alert(SmartModel):
    TYPE_DISCONNECTED = 'D'
    TYPE_POWER = 'P'
    TYPE_SMS = 'S'

    TYPE_CHOICES = ((TYPE_POWER, _("Power")),                 # channel has low power
                    (TYPE_DISCONNECTED, _("Disconnected")),   # channel hasn't synced in a while
                    (TYPE_SMS, _("SMS")))                     # channel has many unsent messages

    channel = models.ForeignKey(Channel, verbose_name=_("Channel"),
                                help_text=_("The channel that this alert is for"))
    sync_event = models.ForeignKey(SyncEvent, verbose_name=_("Sync Event"), null=True,
                                   help_text=_("The sync event that caused this alert to be sent (if any)"))
    alert_type = models.CharField(verbose_name=_("Alert Type"), max_length=1, choices=TYPE_CHOICES,
                                  help_text=_("The type of alert the channel is sending"))
    ended_on = models.DateTimeField(verbose_name=_("Ended On"), blank=True, null=True)

    @classmethod
    def check_power_alert(cls, sync):
        alert_user = get_alert_user()

        if sync.power_status in (STATUS_DISCHARGING, STATUS_UNKNOWN, STATUS_NOT_CHARGING) and int(sync.power_level) < 25:

            alerts = Alert.objects.filter(sync_event__channel=sync.channel, alert_type=cls.TYPE_POWER, ended_on=None)

            if not alerts:
                new_alert = Alert.objects.create(channel=sync.channel,
                                                 sync_event=sync,
                                                 alert_type=cls.TYPE_POWER,
                                                 created_by=alert_user,
                                                 modified_by=alert_user)
                new_alert.send_alert()

        if sync.power_status == STATUS_CHARGING or sync.power_status == STATUS_FULL:
            alerts = Alert.objects.filter(sync_event__channel=sync.channel, alert_type=cls.TYPE_POWER, ended_on=None)
            alerts = alerts.order_by('-created_on')

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
        for alert in Alert.objects.filter(alert_type=cls.TYPE_DISCONNECTED, ended_on=None):
            # if we've seen the channel since this alert went out, then clear the alert
            if alert.channel.last_seen > alert.created_on:
                alert.ended_on = alert.channel.last_seen
                alert.save()
                alert.send_resolved()

        for channel in Channel.objects.filter(channel_type=Channel.TYPE_ANDROID, is_active=True).exclude(org=None).exclude(last_seen__gte=thirty_minutes_ago):
            # have we already sent an alert for this channel
            if not Alert.objects.filter(channel=channel, alert_type=cls.TYPE_DISCONNECTED, ended_on=None):
                alert = Alert.objects.create(channel=channel, alert_type=cls.TYPE_DISCONNECTED,
                                             modified_by=alert_user, created_by=alert_user)
                alert.send_alert()

        day_ago = timezone.now() - timedelta(days=1)
        six_hours_ago = timezone.now() - timedelta(hours=6)

        # end any sms alerts that are open and no longer seem valid
        for alert in Alert.objects.filter(alert_type=cls.TYPE_SMS, ended_on=None):
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
            # we haven't sent any messages in the past six hours
            if not value['sent'] or value['sent'] < six_hours_ago:
                channel = Channel.objects.get(pk=channel_id)

                # never alert on channels that have no org
                if channel.org is None:
                    continue

                # if we haven't sent an alert in the past six ours
                if not Alert.objects.filter(channel=channel).filter(Q(created_on__gt=six_hours_ago)):
                    alert = Alert.objects.create(channel=channel, alert_type=cls.TYPE_SMS,
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

        if self.alert_type == self.TYPE_POWER:
            if resolved:
                subject = "Your Android phone is now charging"
                template = 'channels/email/power_charging_alert'
            else:
                subject = "Your Android phone battery is low"
                template = 'channels/email/power_alert'

        elif self.alert_type == self.TYPE_DISCONNECTED:
            if resolved:
                subject = "Your Android phone is now connected"
                template = 'channels/email/connected_alert'
            else:
                subject = "Your Android phone is disconnected"
                template = 'channels/email/disconnected_alert'

        elif self.alert_type == self.TYPE_SMS:
            subject = "Your %s is having trouble sending messages" % self.channel.get_channel_type_name()
            template = 'channels/email/sms_alert'
        else:  # pragma: no cover
            raise Exception(_("Unknown alert type: %(alert)s") % {'alert': self.alert_type})

        context = dict(org=self.channel.org, channel=self.channel, now=timezone.now(),
                       last_seen=self.channel.last_seen, sync=self.sync_event)
        context['unsent_count'] = Msg.objects.filter(channel=self.channel, status__in=['Q', 'P'], contact__is_test=False).count()
        context['subject'] = subject

        send_template_email(self.channel.alert_email, subject, template, context, self.channel.org.get_branding())


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
    from temba.ivr.clients import TwilioClient
    return TwilioClient(account_sid, auth_token)
