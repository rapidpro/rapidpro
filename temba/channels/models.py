from __future__ import absolute_import, print_function, unicode_literals

import json
import time
import urlparse
import phonenumbers
import plivo
import regex
import requests
import telegram
import re
import six

from enum import Enum
from datetime import timedelta
from django.contrib.auth.models import User, Group
from django.core.urlresolvers import reverse
from django.core.validators import URLValidator
from django.db import models
from django.db.models import Q, Max, Sum
from django.db.models.signals import pre_save
from django.conf import settings
from django.utils import timezone
from django.utils.http import urlencode, urlquote_plus
from django.utils.translation import ugettext_lazy as _
from django.dispatch import receiver
from django_countries.fields import CountryField
from django.core.cache import cache
from django_redis import get_redis_connection
from gcm.gcm import GCM, GCMNotRegisteredException
from phonenumbers import NumberParseException
from pyfcm import FCMNotification
from smartmin.models import SmartModel
from temba.orgs.models import Org, OrgLock, APPLICATION_SID, NEXMO_UUID, NEXMO_APP_ID
from temba.utils import analytics, random_string, dict_to_struct, dict_to_json, on_transaction_commit
from temba.utils.email import send_template_email
from temba.utils.gsm7 import is_gsm7, replace_non_gsm7_accents
from temba.utils.http import HttpEvent
from temba.utils.nexmo import NexmoClient, NCCOResponse
from temba.utils.models import SquashableModel, TembaModel, generate_uuid
from temba.utils.twitter import TembaTwython
from time import sleep
from twilio import twiml, TwilioRestException
from xml.sax.saxutils import quoteattr, escape


TEMBA_HEADERS = {'User-agent': 'RapidPro'}

# Hub9 is an aggregator in Indonesia, set this to the endpoint for your service
# and make sure you send from a whitelisted IP Address
HUB9_ENDPOINT = 'http://175.103.48.29:28078/testing/smsmt.php'

# Dart Media is another aggregator in Indonesia, set this to the endpoint for your service
DART_MEDIA_ENDPOINT = 'http://202.43.169.11/APIhttpU/receive2waysms.php'


class Encoding(Enum):
    GSM7 = 1
    REPLACED = 2
    UNICODE = 3


@six.python_2_unicode_compatible
class Channel(TembaModel):
    TYPE_AFRICAS_TALKING = 'AT'
    TYPE_ANDROID = 'A'
    TYPE_BLACKMYNA = 'BM'
    TYPE_CHIKKA = 'CK'
    TYPE_CLICKATELL = 'CT'
    TYPE_DARTMEDIA = 'DA'
    TYPE_DUMMY = 'DM'
    TYPE_EXTERNAL = 'EX'
    TYPE_FACEBOOK = 'FB'
    TYPE_FCM = 'FCM'
    TYPE_GLOBE = 'GL'
    TYPE_HIGH_CONNECTION = 'HX'
    TYPE_HUB9 = 'H9'
    TYPE_INFOBIP = 'IB'
    TYPE_JASMIN = 'JS'
    TYPE_JUNEBUG = 'JN'
    TYPE_JUNEBUG_USSD = 'JNU'
    TYPE_KANNEL = 'KN'
    TYPE_LINE = 'LN'
    TYPE_MACROKIOSK = 'MK'
    TYPE_M3TECH = 'M3'
    TYPE_MBLOX = 'MB'
    TYPE_NEXMO = 'NX'
    TYPE_PLIVO = 'PL'
    TYPE_RED_RABBIT = 'RR'
    TYPE_SHAQODOON = 'SQ'
    TYPE_SMSCENTRAL = 'SC'
    TYPE_START = 'ST'
    TYPE_TELEGRAM = 'TG'
    TYPE_TWILIO = 'T'
    TYPE_TWIML = 'TW'
    TYPE_TWILIO_MESSAGING_SERVICE = 'TMS'
    TYPE_TWITTER = 'TT'
    TYPE_VERBOICE = 'VB'
    TYPE_VIBER = 'VI'
    TYPE_VIBER_PUBLIC = 'VP'
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
    CONFIG_CONTENT_TYPE = 'content_type'
    CONFIG_VERIFY_SSL = 'verify_ssl'
    CONFIG_USE_NATIONAL = 'use_national'
    CONFIG_ENCODING = 'encoding'
    CONFIG_PAGE_NAME = 'page_name'
    CONFIG_PLIVO_AUTH_ID = 'PLIVO_AUTH_ID'
    CONFIG_PLIVO_AUTH_TOKEN = 'PLIVO_AUTH_TOKEN'
    CONFIG_PLIVO_APP_ID = 'PLIVO_APP_ID'
    CONFIG_AUTH_TOKEN = 'auth_token'
    CONFIG_CHANNEL_ID = 'channel_id'
    CONFIG_CHANNEL_SECRET = 'channel_secret'
    CONFIG_CHANNEL_MID = 'channel_mid'
    CONFIG_FCM_ID = 'FCM_ID'
    CONFIG_FCM_KEY = 'FCM_KEY'
    CONFIG_FCM_TITLE = 'FCM_TITLE'
    CONFIG_FCM_NOTIFICATION = 'FCM_NOTIFICATION'
    CONFIG_MAX_LENGTH = 'max_length'
    CONFIG_MACROKIOSK_SERVICE_ID = 'macrokiosk_service_id'

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
    ROLE_USSD = 'U'

    DEFAULT_ROLE = ROLE_SEND + ROLE_RECEIVE

    # how many outgoing messages we will queue at once
    SEND_QUEUE_DEPTH = 500

    # how big each batch of outgoing messages can be
    SEND_BATCH_SIZE = 100

    TWITTER_FATAL_403S = ("messages to this user right now",  # handle is suspended
                          "users who are not following you")  # handle no longer follows us

    YO_API_URL_1 = 'http://smgw1.yo.co.ug:9100/sendsms'
    YO_API_URL_2 = 'http://41.220.12.201:9100/sendsms'
    YO_API_URL_3 = 'http://164.40.148.210:9100/sendsms'

    VUMI_GO_API_URL = 'https://go.vumi.org/api/v1/go/http_api_nostream'

    CONTENT_TYPE_URLENCODED = 'urlencoded'
    CONTENT_TYPE_JSON = 'json'
    CONTENT_TYPE_XML = 'xml'

    CONTENT_TYPES = {
        CONTENT_TYPE_URLENCODED: "application/x-www-form-urlencoded",
        CONTENT_TYPE_JSON: "application/json",
        CONTENT_TYPE_XML: "text/xml; charset=utf-8"
    }

    CONTENT_TYPE_CHOICES = ((CONTENT_TYPE_URLENCODED, _("URL Encoded - application/x-www-form-urlencoded")),
                            (CONTENT_TYPE_JSON, _("JSON - application/json")),
                            (CONTENT_TYPE_XML, _("XML - text/xml; charset=utf-8")))

    # various hard coded settings for the channel types
    CHANNEL_SETTINGS = {
        TYPE_AFRICAS_TALKING: dict(scheme='tel', max_length=160),
        TYPE_ANDROID: dict(scheme='tel', max_length=-1),
        TYPE_BLACKMYNA: dict(scheme='tel', max_length=1600),
        TYPE_CHIKKA: dict(scheme='tel', max_length=160),
        TYPE_CLICKATELL: dict(scheme='tel', max_length=420),
        TYPE_DARTMEDIA: dict(scheme='tel', max_length=160),
        TYPE_DUMMY: dict(scheme='tel', max_length=160),
        TYPE_EXTERNAL: dict(max_length=160),
        TYPE_FACEBOOK: dict(scheme='facebook', max_length=320),
        TYPE_FCM: dict(scheme='fcm', max_length=10000),
        TYPE_GLOBE: dict(scheme='tel', max_length=160),
        TYPE_HIGH_CONNECTION: dict(scheme='tel', max_length=1500),
        TYPE_HUB9: dict(scheme='tel', max_length=1600),
        TYPE_INFOBIP: dict(scheme='tel', max_length=1600),
        TYPE_JASMIN: dict(scheme='tel', max_length=1600),
        TYPE_JUNEBUG: dict(scheme='tel', max_length=1600),
        TYPE_JUNEBUG_USSD: dict(scheme='tel', max_length=1600),
        TYPE_KANNEL: dict(scheme='tel', max_length=1600),
        TYPE_LINE: dict(scheme='line', max_length=1600),
        TYPE_MACROKIOSK: dict(scheme='tel', max_length=1600),
        TYPE_M3TECH: dict(scheme='tel', max_length=160),
        TYPE_NEXMO: dict(scheme='tel', max_length=1600, max_tps=1),
        TYPE_MBLOX: dict(scheme='tel', max_length=459),
        TYPE_PLIVO: dict(scheme='tel', max_length=1600),
        TYPE_RED_RABBIT: dict(scheme='tel', max_length=1600),
        TYPE_SHAQODOON: dict(scheme='tel', max_length=1600),
        TYPE_SMSCENTRAL: dict(scheme='tel', max_length=1600),
        TYPE_START: dict(scheme='tel', max_length=1600),
        TYPE_TELEGRAM: dict(scheme='telegram', max_length=1600),
        TYPE_TWILIO: dict(scheme='tel', max_length=1600),
        TYPE_TWIML: dict(scheme='tel', max_length=1600),
        TYPE_TWILIO_MESSAGING_SERVICE: dict(scheme='tel', max_length=1600),
        TYPE_TWITTER: dict(scheme='twitter', max_length=10000),
        TYPE_VERBOICE: dict(scheme='tel', max_length=1600),
        TYPE_VIBER: dict(scheme='tel', max_length=1000),
        TYPE_VIBER_PUBLIC: dict(scheme='viber', max_length=7000),
        TYPE_VUMI: dict(scheme='tel', max_length=1600),
        TYPE_VUMI_USSD: dict(scheme='tel', max_length=182),
        TYPE_YO: dict(scheme='tel', max_length=1600),
        TYPE_ZENVIA: dict(scheme='tel', max_length=150),
    }

    TYPE_CHOICES = ((TYPE_AFRICAS_TALKING, "Africa's Talking"),
                    (TYPE_ANDROID, "Android"),
                    (TYPE_BLACKMYNA, "Blackmyna"),
                    (TYPE_CLICKATELL, "Clickatell"),
                    (TYPE_DARTMEDIA, "Dart Media"),
                    (TYPE_DUMMY, "Dummy"),
                    (TYPE_EXTERNAL, "External"),
                    (TYPE_FACEBOOK, "Facebook"),
                    (TYPE_FCM, "Firebase Cloud Messaging"),
                    (TYPE_GLOBE, "Globe Labs"),
                    (TYPE_HIGH_CONNECTION, "High Connection"),
                    (TYPE_HUB9, "Hub9"),
                    (TYPE_INFOBIP, "Infobip"),
                    (TYPE_JASMIN, "Jasmin"),
                    (TYPE_JUNEBUG, "Junebug"),
                    (TYPE_JUNEBUG_USSD, "Junebug USSD"),
                    (TYPE_KANNEL, "Kannel"),
                    (TYPE_LINE, "Line"),
                    (TYPE_M3TECH, "M3 Tech"),
                    (TYPE_MBLOX, "Mblox"),
                    (TYPE_NEXMO, "Nexmo"),
                    (TYPE_PLIVO, "Plivo"),
                    (TYPE_RED_RABBIT, "Red Rabbit"),
                    (TYPE_SHAQODOON, "Shaqodoon"),
                    (TYPE_SMSCENTRAL, "SMSCentral"),
                    (TYPE_START, "Start Mobile"),
                    (TYPE_TELEGRAM, "Telegram"),
                    (TYPE_TWILIO, "Twilio"),
                    (TYPE_TWIML, "TwiML Rest API"),
                    (TYPE_TWILIO_MESSAGING_SERVICE, "Twilio Messaging Service"),
                    (TYPE_TWITTER, "Twitter"),
                    (TYPE_VERBOICE, "Verboice"),
                    (TYPE_VIBER, "Viber"),
                    (TYPE_VIBER_PUBLIC, "Viber Public Channels"),
                    (TYPE_VUMI, "Vumi"),
                    (TYPE_VUMI_USSD, "Vumi USSD"),
                    (TYPE_YO, "Yo!"),
                    (TYPE_ZENVIA, "Zenvia"))

    # list of all USSD channels
    USSD_CHANNELS = [TYPE_VUMI_USSD, TYPE_JUNEBUG_USSD]

    TWIML_CHANNELS = [TYPE_TWILIO, TYPE_VERBOICE, TYPE_TWIML]

    NCCO_CHANNELS = [TYPE_NEXMO]

    MEDIA_CHANNELS = [TYPE_TWILIO, TYPE_TWIML, TYPE_TWILIO_MESSAGING_SERVICE, TYPE_TELEGRAM, TYPE_FACEBOOK]

    GET_STARTED = 'get_started'
    VIBER_NO_SERVICE_ID = 'no_service_id'

    SIMULATOR_CONTEXT = dict(__default__='(800) 555-1212', name='Simulator', tel='(800) 555-1212', tel_e164='+18005551212')

    channel_type = models.CharField(verbose_name=_("Channel Type"), max_length=3, choices=TYPE_CHOICES,
                                    default=TYPE_ANDROID, help_text=_("Type of this channel, whether Android, Twilio or SMSC"))

    name = models.CharField(verbose_name=_("Name"), max_length=64, blank=True, null=True,
                            help_text=_("Descriptive label for this channel"))

    address = models.CharField(verbose_name=_("Address"), max_length=255, blank=True, null=True,
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

    role = models.CharField(verbose_name="Channel Role", max_length=4, default=DEFAULT_ROLE,
                            help_text=_("The roles this channel can fulfill"))

    parent = models.ForeignKey('self', blank=True, null=True,
                               help_text=_("The channel this channel is working on behalf of"))

    bod = models.TextField(verbose_name=_("Optional Data"), null=True,
                           help_text=_("Any channel specific state data"))

    @classmethod
    def create(cls, org, user, country, channel_type, name=None, address=None, config=None, role=DEFAULT_ROLE, scheme=None, **kwargs):
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
    def add_viber_public_channel(cls, org, user, auth_token):
        from temba.contacts.models import VIBER_SCHEME
        response = requests.post('https://chatapi.viber.com/pa/get_account_info', json=dict(auth_token=auth_token))
        if response.status_code != 200:  # pragma: no cover
            raise Exception(_("Invalid authentication token, please check."))

        response_json = response.json()
        if response_json['status'] != 0:  # pragma: no cover
            raise Exception(_("Invalid authentication token: %s" % response_json['status_message']))

        channel = Channel.create(org, user, None, Channel.TYPE_VIBER_PUBLIC,
                                 name=response_json['uri'], address=response_json['id'],
                                 config={Channel.CONFIG_AUTH_TOKEN: auth_token}, scheme=VIBER_SCHEME)

        # set the webhook for the channel
        # {
        #   "auth_token": "4453b6ac1s345678-e02c5f12174805f9-daec9cbb5448c51r",
        #   "url": "https://my.host.com",
        #   "event_types": ["delivered", "seen", "failed", "conversation_started"]
        # }
        response = requests.post('https://chatapi.viber.com/pa/set_webhook',
                                 json=dict(auth_token=auth_token,
                                           url="https://" + settings.TEMBA_HOST + "%s" % reverse('handlers.viber_public_handler', args=[channel.uuid]),
                                           event_types=['delivered', 'failed', 'conversation_started']))
        if response.status_code != 200:  # pragma: no cover
            channel.delete()
            raise Exception(_("Unable to set webhook for channel: %s", response.text))

        response_json = response.json()
        if response_json['status'] != 0:  # pragma: no cover
            raise Exception(_("Unable to set Viber webhook: %s" % response_json['status_message']))

        return channel

    @classmethod
    def add_fcm_channel(cls, org, user, data):
        """
        Creates a new Firebase Cloud Messaging channel
        """
        from temba.contacts.models import FCM_SCHEME

        assert Channel.CONFIG_FCM_KEY in data and Channel.CONFIG_FCM_TITLE in data, "%s and %s are required" % (
            Channel.CONFIG_FCM_KEY, Channel.CONFIG_FCM_TITLE)

        return Channel.create(org, user, None, Channel.TYPE_FCM, name=data.get(Channel.CONFIG_FCM_TITLE),
                              address=data.get(Channel.CONFIG_FCM_KEY), config=data, scheme=FCM_SCHEME)

    @classmethod
    def add_authenticated_external_channel(cls, org, user, country, phone_number,
                                           username, password, channel_type, url, role=DEFAULT_ROLE):
        try:
            parsed = phonenumbers.parse(phone_number, None)
            phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        except Exception:
            # this is a shortcode, just use it plain
            phone = phone_number

        config = dict(username=username, password=password, send_url=url)
        return Channel.create(org, user, country, channel_type, name=phone, address=phone_number, config=config,
                              role=role)

    @classmethod
    def add_config_external_channel(cls, org, user, country, address, channel_type, config, role=DEFAULT_ROLE,
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
        else:  # pragma: no cover
            plivo_app_id = None

        plivo_config = {Channel.CONFIG_PLIVO_AUTH_ID: auth_id,
                        Channel.CONFIG_PLIVO_AUTH_TOKEN: auth_token,
                        Channel.CONFIG_PLIVO_APP_ID: plivo_app_id}

        plivo_number = phone_number.strip('+ ').replace(' ', '')

        plivo_response_status, plivo_response = client.get_number(params=dict(number=plivo_number))

        if plivo_response_status != 200:
            plivo_response_status, plivo_response = client.buy_phone_number(params=dict(number=plivo_number))

            if plivo_response_status != 201:  # pragma: no cover
                raise Exception(_("There was a problem claiming that number, please check the balance on your account."))

            plivo_response_status, plivo_response = client.get_number(params=dict(number=plivo_number))

        if plivo_response_status == 200:
            plivo_response_status, plivo_response = client.modify_number(params=dict(number=plivo_number,
                                                                                     app_id=plivo_app_id))
            if plivo_response_status != 202:  # pragma: no cover
                raise Exception(_("There was a problem updating that number, please try again."))

        phone_number = '+' + plivo_number
        phone = phonenumbers.format_number(phonenumbers.parse(phone_number, None),
                                           phonenumbers.PhoneNumberFormat.NATIONAL)

        return Channel.create(org, user, country, Channel.TYPE_PLIVO, name=phone, address=phone_number,
                              config=plivo_config, uuid=plivo_uuid)

    @classmethod
    def add_nexmo_channel(cls, org, user, country, phone_number):
        client = org.get_nexmo_client()
        org_config = org.config_json()
        org_uuid = org_config.get(NEXMO_UUID)
        app_id = org_config.get(NEXMO_APP_ID)

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
                client.buy_nexmo_number(country, phone_number)
            except Exception as e:
                raise Exception(_("There was a problem claiming that number, "
                                  "please check the balance on your account. " +
                                  "Note that you can only claim numbers after "
                                  "adding credit to your Nexmo account.") + "\n" + str(e))

        mo_path = reverse('handlers.nexmo_handler', args=['receive', org_uuid])

        channel_uuid = generate_uuid()

        nexmo_phones = client.get_numbers(phone_number)

        features = [elt.upper() for elt in nexmo_phones[0]['features']]
        role = ''
        if 'SMS' in features:
            role += Channel.ROLE_SEND + Channel.ROLE_RECEIVE

        if 'VOICE' in features:
            role += Channel.ROLE_ANSWER + Channel.ROLE_CALL

        # update the delivery URLs for it
        from temba.settings import TEMBA_HOST
        try:
            client.update_nexmo_number(country, phone_number, 'https://%s%s' % (TEMBA_HOST, mo_path), app_id)

        except Exception as e:  # pragma: no cover
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

        return Channel.create(org, user, country, Channel.TYPE_NEXMO, name=phone, address=phone_number, role=role,
                              bod=nexmo_phone_number, uuid=channel_uuid)

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

        if not exists:  # pragma: no cover
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

            else:  # pragma: no cover
                raise Exception(_("Short code not found on your Twilio Account. "
                                  "Please check you own the short code and Try again"))
        else:
            if twilio_phones:
                twilio_phone = twilio_phones[0]
                client.phone_numbers.update(twilio_phone.sid,
                                            voice_application_sid=application_sid,
                                            sms_application_sid=application_sid)

            else:  # pragma: needs cover
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
    def add_twiml_api_channel(cls, org, user, country, address, config, role):
        is_short_code = len(address) <= 6

        name = address

        if is_short_code:
            role = Channel.ROLE_SEND + Channel.ROLE_RECEIVE
        else:
            address = "+%s" % address
            name = phonenumbers.format_number(phonenumbers.parse(address, None), phonenumbers.PhoneNumberFormat.NATIONAL)

        existing = Channel.objects.filter(address=address, org=org, channel_type=Channel.TYPE_TWIML).first()
        if existing:
            existing.name = name
            existing.address = address
            existing.config = json.dumps(config)
            existing.country = country
            existing.role = role
            existing.save()
            return existing

        return Channel.create(org, user, country, Channel.TYPE_TWIML, name=name, address=address, config=config, role=role)

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
    def add_line_channel(cls, org, user, credentials, name):
        channel_id = credentials.get('channel_id')
        channel_secret = credentials.get('channel_secret')
        channel_mid = credentials.get('channel_mid')
        channel_access_token = credentials.get('channel_access_token')

        return Channel.create(org, user, None, Channel.TYPE_LINE, name=name, address=channel_mid, config={Channel.CONFIG_AUTH_TOKEN: channel_access_token, Channel.CONFIG_CHANNEL_ID: channel_id, Channel.CONFIG_CHANNEL_SECRET: channel_secret, Channel.CONFIG_CHANNEL_MID: channel_mid})

    @classmethod
    def add_twitter_channel(cls, org, user, screen_name, handle_id, oauth_token, oauth_token_secret):
        config = dict(handle_id=int(handle_id),
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
                on_transaction_commit(lambda: notify_mage_task.delay(channel.uuid, MageStreamAction.activate.name))

        return channel

    @classmethod
    def get_or_create_android(cls, registration_data, status):
        """
        Creates a new Android channel from the gcm and status commands sent during device registration
        """
        gcm_id = registration_data.get('gcm_id')
        fcm_id = registration_data.get('fcm_id')
        uuid = registration_data.get('uuid')
        country = status.get('cc')
        device = status.get('dev')

        if (not gcm_id and not fcm_id) or not uuid:  # pragma: no cover
            raise ValueError("Can't create Android channel without UUID, FCM ID and GCM ID")

        # Clear and Ignore the GCM ID if we have the FCM ID
        if fcm_id:
            gcm_id = None

        # look for existing active channel with this UUID
        existing = Channel.objects.filter(uuid=uuid, is_active=True).first()

        # if device exists reset some of the settings (ok because device clearly isn't in use if it's registering)
        if existing:
            config = existing.config_json()
            config.update({Channel.CONFIG_FCM_ID: fcm_id})

            existing.config = json.dumps(config)
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
        config = {Channel.CONFIG_FCM_ID: fcm_id}

        return Channel.create(None, anon, country, Channel.TYPE_ANDROID, None, None, gcm_id=gcm_id, config=config,
                              uuid=uuid, device=device, claim_code=claim_code, secret=secret)

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

    @classmethod
    def supports_media(cls, channel):
        """
        Can this channel send images, audio, and video. This is static to work
        with ChannelStructs or Channels
        """
        if channel.channel_type in Channel.MEDIA_CHANNELS:
            # twilio only supports mms in the US and Canada
            if channel.channel_type in Channel.TWIML_CHANNELS and channel.country not in ('US', 'CA'):
                return False
            return True
        return False

    def has_channel_log(self):
        return self.channel_type != Channel.TYPE_ANDROID

    def has_configuration_page(self):
        """
        Whether or not this channel supports a configuration/settings page
        """
        return self.channel_type not in (Channel.TYPE_TWILIO, Channel.TYPE_ANDROID, Channel.TYPE_TWITTER, Channel.TYPE_TELEGRAM)

    def get_delegate_channels(self):
        # detached channels can't have delegates
        if not self.org:  # pragma: no cover
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

        if response.status_code != 200:  # pragma: no cover
            raise Exception(_("Unable to update call to action: %s" % response.text))

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

    def get_ussd_delegate(self):
        return self.get_delegate(Channel.ROLE_USSD)

    def is_delegate_sender(self):
        return self.parent and Channel.ROLE_SEND in self.role

    def is_delegate_caller(self):
        return self.parent and Channel.ROLE_CALL in self.role

    def generate_ivr_response(self):
        if self.channel_type in Channel.TWIML_CHANNELS:
            return twiml.Response()
        if self.channel_type in Channel.NCCO_CHANNELS:
            return NCCOResponse()

    def get_ivr_client(self):

        # no client for released channels
        if not (self.is_active and self.org):
            return None

        if self.channel_type == Channel.TYPE_TWILIO:
            return self.org.get_twilio_client()
        elif self.channel_type == Channel.TYPE_TWIML:
            return self.get_twiml_client()
        elif self.channel_type == Channel.TYPE_VERBOICE:  # pragma: no cover
            return self.org.get_verboice_client()
        elif self.channel_type == Channel.TYPE_NEXMO:
            return self.org.get_nexmo_client()

    def get_twiml_client(self):
        from temba.ivr.clients import TwilioClient
        from temba.orgs.models import ACCOUNT_SID, ACCOUNT_TOKEN

        config = self.config_json()

        if config:
            account_sid = config.get(ACCOUNT_SID, None)
            auth_token = config.get(ACCOUNT_TOKEN, None)
            base = config.get(Channel.CONFIG_SEND_URL, None)

            if account_sid and auth_token:
                return TwilioClient(account_sid, auth_token, org=self, base=base)

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
            except NumberParseException:  # pragma: needs cover
                # the number may be alphanumeric in the case of short codes
                pass

        elif self.channel_type == Channel.TYPE_TWITTER:
            return '@%s' % self.address

        elif self.channel_type == Channel.TYPE_FACEBOOK:
            return "%s (%s)" % (self.config_json().get(Channel.CONFIG_PAGE_NAME, self.name), self.address)

        return self.address

    def build_expressions_context(self):
        from temba.contacts.models import TEL_SCHEME

        address = self.get_address_display()
        default = address if address else six.text_type(self)

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
        else:  # pragma: no cover
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
            if not channel:  # pragma: no cover
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

        return dict(id=self.id, org=self.org_id, country=six.text_type(self.country), address=self.address,
                    uuid=self.uuid, secret=self.secret, channel_type=self.channel_type, name=self.name,
                    config=self.config_json(), org_config=org_config)

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

        if not self.country:  # pragma: needs cover
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

        if settings.IS_PROD:
            # only call out to external aggregator services if we are on prod servers

            # hangup all its calls
            from temba.ivr.models import IVRCall
            for call in IVRCall.objects.filter(channel=self):
                call.close()

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

            # unsubscribe from Viber events
            elif self.channel_type == Channel.TYPE_VIBER_PUBLIC:
                auth_token = self.config_json()[Channel.CONFIG_AUTH_TOKEN]
                requests.post('https://chatapi.viber.com/pa/set_webhook', json=dict(auth_token=auth_token, url=''))

        # save off our org and gcm id before nullifying
        org = self.org
        config = self.config_json()
        fcm_id = config.pop(Channel.CONFIG_FCM_ID, None)

        if fcm_id is not None:
            registration_id = fcm_id
        else:
            registration_id = self.gcm_id

        # remove all identifying bits from the client
        self.org = None
        self.gcm_id = None
        self.config = json.dumps(config)
        self.secret = None
        self.claim_code = None
        self.is_active = False
        self.save()

        # mark any messages in sending mode as failed for this channel
        from temba.msgs.models import Msg, OUTGOING, PENDING, QUEUED, ERRORED, FAILED
        Msg.objects.filter(channel=self, direction=OUTGOING, status__in=[QUEUED, PENDING, ERRORED]).update(status=FAILED)

        # trigger the orphaned channel
        if trigger_sync and self.channel_type == Channel.TYPE_ANDROID:  # pragma: no cover
            self.trigger_sync(registration_id)

        # clear our cache for this channel
        Channel.clear_cached_channel(self.id)

        if notify_mage and self.channel_type == Channel.TYPE_TWITTER:
            # notify Mage so that it deactivates this channel
            from .tasks import MageStreamAction, notify_mage_task
            on_transaction_commit(lambda: notify_mage_task.delay(self.uuid, MageStreamAction.deactivate.name))

        from temba.triggers.models import Trigger
        Trigger.objects.filter(channel=self, org=org).update(is_active=False)

    def trigger_sync(self, registration_id=None):  # pragma: no cover
        """
        Sends a GCM command to trigger a sync on the client
        """
        # androids sync via FCM or GCM(for old apps installs)
        if self.channel_type == Channel.TYPE_ANDROID:
            config = self.config_json()
            fcm_id = config.get(Channel.CONFIG_FCM_ID)

            if fcm_id is not None:
                if getattr(settings, 'FCM_API_KEY', None):
                    from .tasks import sync_channel_fcm_task
                    if not registration_id:
                        registration_id = fcm_id
                    if registration_id:
                        on_transaction_commit(lambda: sync_channel_fcm_task.delay(registration_id, channel_id=self.pk))

            elif self.gcm_id:
                if getattr(settings, 'GCM_API_KEY', None):
                    from .tasks import sync_channel_gcm_task
                    if not registration_id:
                        registration_id = self.gcm_id
                    if registration_id:
                        on_transaction_commit(lambda: sync_channel_gcm_task.delay(registration_id, channel_id=self.pk))

        # otherwise this is an aggregator, no-op
        else:
            raise Exception("Trigger sync called on non Android channel. [%d]" % self.pk)

    @classmethod
    def sync_channel_fcm(cls, registration_id, channel=None):  # pragma: no cover
        push_service = FCMNotification(api_key=settings.FCM_API_KEY)
        result = push_service.notify_single_device(registration_id=registration_id, data_message=dict(msg='sync'))

        if not result.get('success', 0):
            valid_registration_ids = push_service.clean_registration_ids([registration_id])
            if registration_id not in valid_registration_ids:
                # this fcm id is invalid now, clear it out
                config = channel.config_json()
                config.pop(Channel.CONFIG_FCM_ID, None)
                channel.config = json.dumps(config)
                channel.save()

    @classmethod
    def sync_channel_gcm(cls, registration_id, channel=None):  # pragma: no cover
        try:
            gcm = GCM(settings.GCM_API_KEY)
            gcm.plaintext_request(registration_id=registration_id, data=dict(msg='sync'))
        except GCMNotRegisteredException:
            if channel:
                # this gcm id is invalid now, clear it out
                channel.gcm_id = None
                channel.save()

    @classmethod
    def replace_variables(cls, text, variables, content_type=CONTENT_TYPE_URLENCODED):
        for key in variables.keys():
            replacement = six.text_type(variables[key])

            # encode based on our content type
            if content_type == Channel.CONTENT_TYPE_URLENCODED:
                replacement = urlquote_plus(replacement)

            # if this is JSON, need to wrap in quotes (and escape them)
            elif content_type == Channel.CONTENT_TYPE_JSON:
                replacement = json.dumps(replacement)

            # XML needs to be escaped
            elif content_type == Channel.CONTENT_TYPE_XML:
                replacement = escape(replacement)

            text = text.replace("{{%s}}" % key, replacement)

        return text

    @classmethod
    def success(cls, channel, msg, msg_status, start, external_id=None, event=None, events=None):
        request_time = time.time() - start

        from temba.msgs.models import Msg
        Msg.mark_sent(channel.config['r'], msg, msg_status, external_id)

        # record stats for analytics
        if msg.queued_on:
            analytics.gauge('temba.sending_latency', (msg.sent_on - msg.queued_on).total_seconds())

        # logs that a message was sent for this channel type if our latency is known
        if request_time > 0:
            analytics.gauge('temba.msg_sent_%s' % channel.channel_type.lower(), request_time)

        # log our request time in ms
        request_time_ms = request_time * 1000

        if events is None and event:
            events = [event]

        for event in events:
            # write to our log file
            print(u"[%d] %0.3fs SENT - %s %s \"%s\" %s \"%s\"" %
                  (msg.id, request_time, event.method, event.url, event.request_body, event.status_code, event.response_body))

            # lastly store a ChannelLog object for the user
            ChannelLog.objects.create(channel_id=msg.channel,
                                      msg_id=msg.id,
                                      is_error=False,
                                      description='Successfully delivered',
                                      method=event.method,
                                      url=event.url,
                                      request=event.request_body,
                                      response=event.response_body,
                                      response_status=event.status_code,
                                      request_time=request_time_ms)

    @classmethod
    def send_fcm_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED
        start = time.time()

        url = 'https://fcm.googleapis.com/fcm/send'
        title = channel.config.get(Channel.CONFIG_FCM_TITLE)
        data = {
            'data': {
                'type': 'rapidpro',
                'title': title,
                'message': text,
                'message_id': msg.id
            },
            'content_available': False,
            'to': msg.auth,
            'priority': 'high'
        }

        if channel.config.get(Channel.CONFIG_FCM_NOTIFICATION):
            data['notification'] = {
                'title': title,
                'body': text
            }
            data['content_available'] = True

        payload = json.dumps(data)
        headers = {'Content-Type': 'application/json',
                   'Authorization': 'key=%s' % channel.config.get(Channel.CONFIG_FCM_KEY)}
        headers.update(TEMBA_HEADERS)

        event = HttpEvent('POST', url, payload)

        try:
            response = requests.post(url, data=payload, headers=headers, timeout=5)
            result = json.loads(response.text) if response.status_code == 200 else None

            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:  # pragma: no cover
            raise SendException(unicode(e), event, start=start)

        if result and 'success' in result and result.get('success') == 1:
            external_id = result.get('multicast_id')
            Channel.success(channel, msg, WIRED, start, events=[event], external_id=external_id)
        else:
            raise SendException("Got non-200 response [%d] from Firebase Cloud Messaging" % response.status_code,
                                event, start=start)

    @classmethod
    def send_red_rabbit_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED
        encoding, text = Channel.determine_encoding(text, replace=True)

        # http://http1.javna.com/epicenter/gatewaysendG.asp?LoginName=xxxx&Password=xxxx&Tracking=1&Mobtyp=1&MessageRecipients=962796760057&MessageBody=hi&SenderName=Xxx
        params = dict()
        params['LoginName'] = channel.config[Channel.CONFIG_USERNAME]
        params['Password'] = channel.config[Channel.CONFIG_PASSWORD]
        params['Tracking'] = 1
        params['Mobtyp'] = 1
        params['MessageRecipients'] = msg.urn_path.lstrip('+')
        params['MessageBody'] = text
        params['SenderName'] = channel.address.lstrip('+')

        # we are unicode
        if encoding == Encoding.UNICODE:
            params['Msgtyp'] = 10 if len(text) >= 70 else 9
        elif len(text) > 160:
            params['Msgtyp'] = 5

        url = 'http://http1.javna.com/epicenter/GatewaySendG.asp'
        event = HttpEvent('GET', url + '?' + urlencode(params))
        start = time.time()

        try:
            response = requests.get(url, params=params, headers=TEMBA_HEADERS, timeout=15)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:  # pragma: no cover
            raise SendException(six.text_type(e), event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)

    @classmethod
    def send_jasmin_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED
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

        event = HttpEvent('GET', log_url, log_payload)

        try:
            response = requests.get(channel.config[Channel.CONFIG_SEND_URL], verify=True, params=payload, timeout=15)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e),
                                event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from Jasmin" % response.status_code,
                                event=event, start=start)

        # save the external id, response should be in format:
        # Success "07033084-5cfd-4812-90a4-e4d24ffb6e3d"
        external_id = None
        match = re.match(r"Success \"(.*)\"", response.text)
        if match:
            external_id = match.group(1)

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)

    @classmethod
    def send_junebug_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED, Msg
        from temba.ussd.models import USSDSession

        session = None

        # the event url Junebug will relay events to
        event_url = 'https://%s%s' % (
            settings.HOSTNAME,
            reverse('handlers.junebug_handler',
                    args=['event', channel.uuid]))

        is_ussd = channel.channel_type == Channel.TYPE_JUNEBUG_USSD

        # build our payload
        payload = dict()
        payload['event_url'] = event_url
        payload['content'] = text

        if is_ussd:
            session = USSDSession.objects.get_session_with_status_only(msg.session_id)
            external_id = Msg.objects.values_list('external_id', flat=True).filter(pk=msg.response_to_id).first()
            # NOTE: Only one of `to` or `reply_to` may be specified
            payload['reply_to'] = external_id
            payload['channel_data'] = {
                'continue_session': session and not session.should_end or False,
            }
        else:
            payload['from'] = channel.address
            payload['to'] = msg.urn_path

        log_url = channel.config[Channel.CONFIG_SEND_URL]
        start = time.time()

        event = HttpEvent('POST', log_url, json.dumps(payload))
        headers = {'Content-Type': 'application/json'}
        headers.update(TEMBA_HEADERS)

        try:
            response = requests.post(
                channel.config[Channel.CONFIG_SEND_URL], verify=True,
                json=payload, timeout=15, headers=headers,
                auth=(channel.config[Channel.CONFIG_USERNAME],
                      channel.config[Channel.CONFIG_PASSWORD]))

            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(unicode(e), event=event, start=start)

        if not (200 <= response.status_code < 300):
            raise SendException("Received a non 200 response %d from Junebug" % response.status_code,
                                event=event, start=start)

        data = response.json()

        if is_ussd and session and session.should_end:
            session.close()

        try:
            message_id = data['result']['message_id']
            Channel.success(channel, msg, WIRED, start, event=event, external_id=message_id)
        except KeyError, e:
            raise SendException("Unable to read external message_id: %r" % (e,),
                                event=HttpEvent('POST', log_url,
                                                request_body=json.dumps(json.dumps(payload)),
                                                response_body=json.dumps(data)),
                                start=start)

    @classmethod
    def send_facebook_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED
        from temba.contacts.models import Contact, ContactURN, URN

        # build our payload
        payload = dict()

        # this is a ref facebook id, temporary just for this message
        if URN.is_path_fb_ref(msg.urn_path):
            payload['recipient'] = dict(user_ref=URN.fb_ref_from_path(msg.urn_path))
        else:
            payload['recipient'] = dict(id=msg.urn_path)

        message = dict(text=text)
        payload['message'] = message
        payload = json.dumps(payload)

        url = "https://graph.facebook.com/v2.5/me/messages"
        params = dict(access_token=channel.config[Channel.CONFIG_AUTH_TOKEN])
        headers = {'Content-Type': 'application/json'}
        start = time.time()

        event = HttpEvent('POST', url, payload)

        try:
            response = requests.post(url, payload, params=params, headers=headers, timeout=15)
            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        from temba.msgs.models import Msg
        media_type, media_url = Msg.get_media(msg)

        if media_type and media_url:
            media_type = media_type.split('/')[0]

            payload = json.loads(payload)
            payload['message'] = dict(attachment=dict(type=media_type, payload=dict(url=media_url)))
            payload = json.dumps(payload)

            event = HttpEvent('POST', url, payload)

            try:
                response = requests.post(url, payload, params=params, headers=headers, timeout=15)
                event.status_code = response.status_code
                event.response_body = response.text
            except Exception as e:
                raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200:
            raise SendException("Got non-200 response [%d] from Facebook" % response.status_code,
                                event=event, start=start)

        # grab our external id out, Facebook response is in format:
        # "{"recipient_id":"997011467086879","message_id":"mid.1459532331848:2534ddacc3993a4b78"}"
        external_id = None
        try:
            external_id = response.json()['message_id']
        except Exception as e:  # pragma: no cover
            # if we can't pull out our message id, that's ok, we still sent
            pass

        # if we sent Facebook a user_ref, look up the real Facebook id for this contact, should be in 'recipient_id'
        if URN.is_path_fb_ref(msg.urn_path):
            contact_obj = Contact.objects.get(id=msg.contact)
            org_obj = Org.objects.get(id=channel.org)
            channel_obj = Channel.objects.get(id=channel.id)

            try:
                real_fb_id = response.json()['recipient_id']

                # associate this contact with our real FB id
                ContactURN.get_or_create(org_obj, contact_obj, URN.from_facebook(real_fb_id), channel=channel_obj)

                # save our ref_id as an external URN on this contact
                ContactURN.get_or_create(org_obj, contact_obj, URN.from_external(URN.fb_ref_from_path(msg.urn_path)))

                # finally, disassociate our temp ref URN with this contact
                ContactURN.objects.filter(id=msg.contact_urn).update(contact=None)

            except Exception as e:   # pragma: no cover
                # if we can't pull out the recipient id, that's ok, msg was sent
                pass

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)

    @classmethod
    def send_line_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

        channel_access_token = channel.config.get(Channel.CONFIG_AUTH_TOKEN)

        data = json.dumps({'to': msg.urn_path, 'messages': [{'type': 'text', 'text': text}]})

        start = time.time()
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer %s' % channel_access_token}
        headers.update(TEMBA_HEADERS)
        send_url = 'https://api.line.me/v2/bot/message/push'

        event = HttpEvent('POST', send_url, data)

        try:
            response = requests.post(send_url, data=data, headers=headers)
            response.json()

            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code not in [200, 201, 202]:  # pragma: needs cover
            raise SendException("Got non-200 response [%d] from Line" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)

    @classmethod
    def send_mblox_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

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

        event = HttpEvent('POST', url, request_body)

        try:
            response = requests.post(url, request_body, headers=headers, timeout=15)
            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:  # pragma: no cover
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from MBlox" % response.status_code,
                                event=event, start=start)

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

        external_id = None
        try:
            response_json = response.json()
            external_id = response_json['id']
        except:  # pragma: no cover
            raise SendException("Unable to parse response body from MBlox",
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)

    @classmethod
    def send_kannel_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

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

        # if this a reply to a message, set a higher priority
        if msg.response_to_id:
            payload['priority'] = 1

        # should our to actually be in national format?
        use_national = channel.config.get(Channel.CONFIG_USE_NATIONAL, False)
        if use_national:
            # parse and remap our 'to' address
            parsed = phonenumbers.parse(msg.urn_path)
            payload['to'] = str(parsed.national_number)

        # figure out if we should send encoding or do any of our own substitution
        desired_encoding = channel.config.get(Channel.CONFIG_ENCODING, Channel.ENCODING_DEFAULT)

        # they want unicode, they get unicode!
        if desired_encoding == Channel.ENCODING_UNICODE:
            payload['coding'] = '2'
            payload['charset'] = 'utf8'

        # otherwise, if this is smart encoding, try to derive it
        elif desired_encoding == Channel.ENCODING_SMART:
            # if this is smart encoding, figure out what encoding we will use
            encoding, text = Channel.determine_encoding(text, replace=True)
            payload['text'] = text

            if encoding == Encoding.UNICODE:
                payload['coding'] = '2'
                payload['charset'] = 'utf8'

        log_payload = payload.copy()
        log_payload['password'] = 'x' * len(log_payload['password'])

        url = channel.config[Channel.CONFIG_SEND_URL]
        log_url = url
        if log_url.find("?") >= 0:  # pragma: no cover
            log_url += "&" + urlencode(log_payload)
        else:
            log_url += "?" + urlencode(log_payload)

        event = HttpEvent('GET', log_url)
        start = time.time()

        try:
            if channel.config.get(Channel.CONFIG_VERIFY_SSL, True):
                response = requests.get(url, verify=True, params=payload, timeout=15)
            else:
                response = requests.get(url, verify=False, params=payload, timeout=15)

            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from Kannel" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)

    @classmethod
    def send_shaqodoon_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

        # requests are signed with a key built as follows:
        # signing_key = md5(username|password|from|to|msg|key|current_date)
        # where current_date is in the format: d/m/y H
        payload = {'from': channel.address.lstrip('+'), 'to': msg.urn_path.lstrip('+'),
                   'username': channel.config[Channel.CONFIG_USERNAME], 'password': channel.config[Channel.CONFIG_PASSWORD],
                   'msg': text}

        # build our send URL
        url = channel.config[Channel.CONFIG_SEND_URL] + "?" + urlencode(payload)
        start = time.time()

        event = HttpEvent('GET', url)

        try:
            # these guys use a self signed certificate
            response = requests.get(url, headers=TEMBA_HEADERS, timeout=15, verify=False)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)

    @classmethod
    def send_dummy_message(cls, channel, msg, text):  # pragma: no cover
        from temba.msgs.models import WIRED

        delay = channel.config.get('delay', 1000)
        start = time.time()

        # sleep that amount
        time.sleep(delay / float(1000))

        event = HttpEvent('GET', 'http://fake')

        # record the message as sent
        Channel.success(channel, msg, WIRED, start, event=event)

    @classmethod
    def send_external_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

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
        url = Channel.replace_variables(channel.config[Channel.CONFIG_SEND_URL], payload)
        start = time.time()

        method = channel.config.get(Channel.CONFIG_SEND_METHOD, 'POST')

        headers = TEMBA_HEADERS.copy()
        content_type = channel.config.get(Channel.CONFIG_CONTENT_TYPE, Channel.CONTENT_TYPE_URLENCODED)
        headers['Content-Type'] = Channel.CONTENT_TYPES[content_type]

        event = HttpEvent(method, url)

        if method in ('POST', 'PUT'):
            body = channel.config.get(Channel.CONFIG_SEND_BODY, Channel.CONFIG_DEFAULT_SEND_BODY)
            body = Channel.replace_variables(body, payload, content_type)
            event.request_body = body

        try:
            if method == 'POST':
                response = requests.post(url, data=body.encode('utf8'), headers=headers, timeout=5)
            elif method == 'PUT':
                response = requests.put(url, data=body.encode('utf8'), headers=headers, timeout=5)
            else:
                response = requests.get(url, headers=headers, timeout=5)

            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)

    @classmethod
    def send_chikka_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED

        payload = {
            'message_type': 'SEND',
            'mobile_number': msg.urn_path.lstrip('+'),
            'shortcode': channel.address,
            'message_id': msg.id,
            'message': text,
            'request_cost': 'FREE',
            'client_id': channel.config[Channel.CONFIG_USERNAME],
            'secret_key': channel.config[Channel.CONFIG_PASSWORD]
        }

        # if this is a response to a user SMS, then we need to set this as a reply
        # response ids are only valid for up to 24 hours
        response_window = timedelta(hours=24)
        if msg.response_to_id and msg.created_on > timezone.now() - response_window:
            response_to = Msg.objects.filter(id=msg.response_to_id).first()
            if response_to:
                payload['message_type'] = 'REPLY'
                payload['request_id'] = response_to.external_id

        # build our send URL
        url = 'https://post.chikka.com/smsapi/request'
        start = time.time()

        log_payload = payload.copy()
        log_payload['secret_key'] = 'x' * len(log_payload['secret_key'])

        event = HttpEvent('POST', url, log_payload)
        events = [event]

        try:
            response = requests.post(url, data=payload, headers=TEMBA_HEADERS, timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        # if they reject our request_id, send it as a normal send
        if response.status_code == 400 and 'request_id' in payload:
            error = response.json()
            if error.get('message', None) == 'BAD REQUEST' and error.get('description', None) == 'Invalid/Used Request ID':
                try:

                    # operate on a copy so we can still inspect our original call
                    payload = payload.copy()
                    del payload['request_id']
                    payload['message_type'] = 'SEND'

                    event = HttpEvent('POST', url, payload)
                    events.append(event)

                    response = requests.post(url, data=payload, headers=TEMBA_HEADERS, timeout=5)
                    event.status_code = response.status_code
                    event.response_body = response.text

                    log_payload = payload.copy()
                    log_payload['secret_key'] = 'x' * len(log_payload['secret_key'])

                except Exception as e:
                    raise SendException(six.text_type(e), events=events, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                events=events, start=start)

        Channel.success(channel, msg, WIRED, start, events=events)

    @classmethod
    def send_high_connection_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

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
        log_payload = urlencode(payload)
        start = time.time()

        event = HttpEvent('GET', url, log_payload)

        try:
            response = requests.get(url, headers=TEMBA_HEADERS, timeout=30)
            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)

    @classmethod
    def send_blackmyna_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

        payload = {
            'address': msg.urn_path,
            'senderaddress': channel.address,
            'message': text,
        }

        url = 'http://api.blackmyna.com/2/smsmessaging/outbound'
        external_id = None
        start = time.time()

        event = HttpEvent('POST', url, payload)

        try:
            response = requests.post(url, data=payload, headers=TEMBA_HEADERS, timeout=30,
                                     auth=(channel.config[Channel.CONFIG_USERNAME], channel.config[Channel.CONFIG_PASSWORD]))
            # parse our response, should be JSON that looks something like:
            # [{
            #   "recipient" : recipient_number_1,
            #   "id" : Unique_identifier (universally unique identifier UUID)
            # }]
            event.status_code = response.status_code
            event.response_body = response.text

            response_json = response.json()

            # we only care about the first piece
            if response_json and len(response_json) > 0:
                external_id = response_json[0].get('id', None)

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:  # pragma: needs cover
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)

    @classmethod
    def send_start_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

        url = 'http://bulk.startmobile.com.ua/clients.php'
        post_body = u"""
          <message>
            <service id="single" source=$$FROM$$ validity=$$VALIDITY$$/>
            <to>$$TO$$</to>
            <body content-type="plain/text" encoding="plain">$$BODY$$</body>
          </message>
        """
        post_body = post_body.replace("$$FROM$$", quoteattr(channel.address))

        # tell Start to attempt to deliver this message for up to 12 hours
        post_body = post_body.replace("$$VALIDITY$$", quoteattr("+12 hours"))
        post_body = post_body.replace("$$TO$$", escape(msg.urn_path))
        post_body = post_body.replace("$$BODY$$", escape(text))
        event = HttpEvent('POST', url, post_body)
        post_body = post_body.encode('utf8')

        start = time.time()
        try:
            headers = {'Content-Type': 'application/xml; charset=utf8'}
            headers.update(TEMBA_HEADERS)

            response = requests.post(url,
                                     data=post_body,
                                     headers=headers,
                                     auth=(channel.config[Channel.CONFIG_USERNAME], channel.config[Channel.CONFIG_PASSWORD]),
                                     timeout=30)

            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if (response.status_code != 200 and response.status_code != 201) or response.text.find("error") >= 0:
            raise SendException("Error Sending Message", event=event, start=start)

        # parse out our id, this is XML but we only care about the id
        external_id = None
        start_idx = response.text.find("<id>")
        end_idx = response.text.find("</id>")
        if end_idx > start_idx > 0:
            external_id = response.text[start_idx + 4:end_idx]

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)

    @classmethod
    def send_macrokiosk_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

        # determine our encoding
        encoding, text = Channel.determine_encoding(text, replace=True)

        # if this looks like unicode, ask macrokiosk to send as unicode
        if encoding == Encoding.UNICODE:
            message_type = 5
        else:
            message_type = 0

        # strip a leading +
        recipient = msg.urn_path[1:] if msg.urn_path.startswith('+') else msg.urn_path

        payload = {
            'user': channel.config[Channel.CONFIG_USERNAME], 'pass': channel.config[Channel.CONFIG_PASSWORD],
            'to': recipient, 'text': text, 'from': channel.address.lstrip('+'),
            'servid': channel.config[Channel.CONFIG_MACROKIOSK_SERVICE_ID], 'type': message_type
        }

        url = 'https://www.etracker.cc/bulksms/send'
        payload_json = json.dumps(payload)

        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        headers.update(TEMBA_HEADERS)

        event = HttpEvent('POST', url, payload_json)

        start = time.time()

        try:
            response = requests.post(url, data=payload, headers=headers, timeout=30)
            event.status_code = response.status_code
            event.response_body = response.text

            external_id = response.json().get('msgid', None)

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code not in [200, 201, 202]:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)

    @classmethod
    def send_smscentral_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

        # strip a leading +
        mobile = msg.urn_path[1:] if msg.urn_path.startswith('+') else msg.urn_path

        payload = {
            'user': channel.config[Channel.CONFIG_USERNAME], 'pass': channel.config[Channel.CONFIG_PASSWORD], 'mobile': mobile, 'content': text,
        }

        url = 'http://smail.smscentral.com.np/bp/ApiSms.php'
        log_payload = urlencode(payload)

        event = HttpEvent('POST', url, log_payload)

        start = time.time()

        try:
            response = requests.post(url, data=payload, headers=TEMBA_HEADERS, timeout=30)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)

    @classmethod
    def send_vumi_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED, Msg
        from temba.contacts.models import Contact
        from temba.ussd.models import USSDSession

        is_ussd = channel.channel_type in Channel.USSD_CHANNELS
        channel.config['transport_name'] = 'ussd_transport' if is_ussd else 'mtech_ng_smpp_transport'

        session = None
        session_event = None
        in_reply_to = None

        if is_ussd:
            session = USSDSession.objects.get_session_with_status_only(msg.session_id)
            if session and session.should_end:
                session_event = "close"
            else:
                session_event = "resume"

        if msg.response_to_id:
            in_reply_to = Msg.objects.values_list('external_id', flat=True).filter(pk=msg.response_to_id).first()

        payload = dict(message_id=msg.id,
                       in_reply_to=in_reply_to,
                       session_event=session_event,
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

        api_url_base = channel.config.get('api_url', cls.VUMI_GO_API_URL)

        url = "%s/%s/messages.json" % (api_url_base, channel.config['conversation_key'])

        event = HttpEvent('PUT', url, json.dumps(payload))

        start = time.time()

        validator = URLValidator()
        validator(url)

        try:
            response = requests.put(url,
                                    data=payload,
                                    headers=headers,
                                    timeout=30,
                                    auth=(channel.config['account_key'], channel.config['access_token']))

            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code not in (200, 201):
            # this is a fatal failure, don't retry
            fatal = response.status_code == 400

            # if this is fatal due to the user opting out, stop them
            if response.text and response.text.find('has opted out') >= 0:
                contact = Contact.objects.get(id=msg.contact)
                contact.stop(contact.modified_by)
                fatal = True

            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, fatal=fatal, start=start)

        # parse our response
        body = response.json()
        external_id = body.get('message_id', '')

        if is_ussd and session and session.should_end:
            session.close()

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)

    @classmethod
    def send_globe_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

        payload = {
            'address': msg.urn_path.lstrip('+'),
            'message': text,
            'passphrase': channel.config['passphrase'],
            'app_id': channel.config['app_id'],
            'app_secret': channel.config['app_secret']
        }
        headers = dict(TEMBA_HEADERS)

        url = 'https://devapi.globelabs.com.ph/smsmessaging/v1/outbound/%s/requests' % channel.address

        event = HttpEvent('POST', url, json.dumps(payload))

        start = time.time()

        try:
            response = requests.post(url,
                                     data=payload,
                                     headers=headers,
                                     timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        # parse our response
        response.json()

        Channel.success(channel, msg, WIRED, start, event=event)

    @classmethod
    def send_nexmo_message(cls, channel, msg, text):
        from temba.msgs.models import SENT
        from temba.orgs.models import NEXMO_KEY, NEXMO_SECRET, NEXMO_APP_ID, NEXMO_APP_PRIVATE_KEY

        client = NexmoClient(channel.org_config[NEXMO_KEY], channel.org_config[NEXMO_SECRET],
                             channel.org_config[NEXMO_APP_ID], channel.org_config[NEXMO_APP_PRIVATE_KEY])
        start = time.time()

        event = None
        attempts = 0
        while not event:
            try:
                (message_id, event) = client.send_message_via_nexmo(channel.address, msg.urn_path, text)
            except SendException as e:
                match = regex.match(r'.*Throughput Rate Exceeded - please wait \[ (\d+) \] and retry.*', e.events[0].response_body)

                # this is a throughput failure, attempt to wait up to three times
                if match and attempts < 3:
                    sleep(float(match.group(1)) / 1000)
                    attempts += 1
                else:
                    raise e

        Channel.success(channel, msg, SENT, start, event=event, external_id=message_id)

    @classmethod
    def send_yo_message(cls, channel, msg, text):
        from temba.msgs.models import SENT
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
        events = []

        for send_url in [Channel.YO_API_URL_1, Channel.YO_API_URL_2, Channel.YO_API_URL_3]:
            url = send_url + '?' + urlencode(params)
            log_url = send_url + '?' + urlencode(log_params)

            event = HttpEvent('GET', log_url)
            events.append(event)

            failed = False
            try:
                response = requests.get(url, headers=TEMBA_HEADERS, timeout=5)
                event.status_code = response.status_code
                event.response_body = response.text

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
                                events=events, fatal=fatal, start=start)

        Channel.success(channel, msg, SENT, start, events=events)

    @classmethod
    def send_infobip_message(cls, channel, msg, text):
        from temba.msgs.models import SENT

        API_URL = 'http://api.infobip.com/api/v3/sendsms/json'
        BACKUP_API_URL = 'http://api2.infobip.com/api/v3/sendsms/json'

        url = API_URL

        # build our message dict
        message = dict(sender=channel.address.lstrip('+'),
                       text=text,
                       recipients=[dict(gsm=msg.urn_path.lstrip('+'))])

        # infobip requires that long messages have a different type
        if len(text) > 160:  # pragma: no cover
            message['type'] = 'longSMS'

        payload = {'authentication': dict(username=channel.config['username'], password=channel.config['password']),
                   'messages': [message]}
        payload_json = json.dumps(payload)

        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        headers.update(TEMBA_HEADERS)

        event = HttpEvent('POST', url, payload_json)
        events = [event]
        start = time.time()

        try:
            response = requests.post(url, data=payload_json, headers=headers, timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text
        except Exception:  # pragma: no cover
            try:
                # we failed to connect, try our backup URL
                url = BACKUP_API_URL
                event = HttpEvent('POST', url, payload_json)
                events.append(event)
                response = requests.post(url, data=payload_json, headers=headers, timeout=5)
                event.status_code = response.status_code
                event.response_body = response.text
            except Exception as e:
                payload['authentication']['password'] = 'x' * len(payload['authentication']['password'])
                raise SendException(u"Unable to send message: %s" % six.text_type(e),
                                    events=events, start=start)

        if response.status_code != 200 and response.status_code != 201:
            payload['authentication']['password'] = 'x' * len(payload['authentication']['password'])
            raise SendException("Received non 200 status: %d" % response.status_code,
                                events=events, start=start)

        response_json = response.json()
        messages = response_json['results']

        # if it wasn't successfully delivered, throw
        if int(messages[0]['status']) != 0:  # pragma: no cover
            raise SendException("Received non-zero status code [%s]" % messages[0]['status'],
                                events=events, start=start)

        external_id = messages[0]['messageid']
        Channel.success(channel, msg, SENT, start, events=events, external_id=external_id)

    @classmethod
    def send_hub9_or_dartmedia_message(cls, channel, msg, text):
        from temba.msgs.models import SENT

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
        if channel.channel_type == Channel.TYPE_HUB9:
            url = HUB9_ENDPOINT
        elif channel.channel_type == Channel.TYPE_DARTMEDIA:
            url = DART_MEDIA_ENDPOINT

        payload = dict(userid=channel.config['username'], password=channel.config['password'],
                       original=channel.address.lstrip('+'), sendto=msg.urn_path.lstrip('+'),
                       messageid=msg.id, message=text, dcs=0, udhl=0)

        # build up our querystring and send it as a get
        send_url = "%s?%s" % (url, urlencode(payload))
        payload['password'] = 'x' * len(payload['password'])
        masked_url = "%s?%s" % (url, urlencode(payload))

        event = HttpEvent('GET', masked_url)

        start = time.time()

        try:
            response = requests.get(send_url, headers=TEMBA_HEADERS, timeout=15)
            event.status_code = response.status_code
            event.response_body = response.text
            if not response:  # pragma: no cover
                raise SendException("Unable to send message",
                                    event=event, start=start)

            if response.status_code != 200 and response.status_code != 201:
                raise SendException("Received non 200 status: %d" % response.status_code,
                                    event=event, start=start)

            # if it wasn't successfully delivered, throw
            if response.text != "000":  # pragma: no cover
                error = "Unknown error"
                if response.text == "001":
                    error = "Error 001: Authentication Error"
                elif response.text == "101":
                    error = "Error 101: Account expired or invalid parameters"

                raise SendException(error, event=event, start=start)

            Channel.success(channel, msg, SENT, start, event=event)

        except SendException as e:
            raise e
        except Exception as e:  # pragma: no cover
            reason = "Unknown error"
            try:
                if e.message and e.message.reason:
                    reason = e.message.reason
            except Exception:
                pass
            raise SendException(u"Unable to send message: %s" % six.text_type(reason)[:64],
                                event=event, start=start)

    @classmethod
    def send_zenvia_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

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

        event = HttpEvent('POST', zenvia_url, urlencode(payload))

        start = time.time()

        try:
            response = requests.get(zenvia_url, params=payload, headers=headers, timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(u"Unable to send message: %s" % six.text_type(e),
                                event=event, start=start)

        if response.status_code != 200 and response.status_code != 201:
            raise SendException("Got non-200 response from API: %d" % response.status_code,
                                event=event, start=start)

        response_code = int(response.text[:3])

        if response_code != 0:
            raise Exception("Got non-zero response from Zenvia: %s" % response.text)

        Channel.success(channel, msg, WIRED, start, event=event)

    @classmethod
    def send_africas_talking_message(cls, channel, msg, text):
        from temba.msgs.models import SENT

        payload = dict(username=channel.config['username'],
                       to=msg.urn_path,
                       message=text)

        # if this isn't a shared shortcode, send the from address
        if not channel.config.get('is_shared', False):
            payload['from'] = channel.address

        headers = dict(Accept='application/json', apikey=channel.config['api_key'])
        headers.update(TEMBA_HEADERS)

        api_url = "https://api.africastalking.com/version1/messaging"

        event = HttpEvent('POST', api_url, urlencode(payload))

        start = time.time()

        try:
            response = requests.post(api_url,
                                     data=payload, headers=headers, timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:
            raise SendException(u"Unable to send message: %s" % six.text_type(e),
                                event=event, start=start)

        if response.status_code != 200 and response.status_code != 201:
            raise SendException("Got non-200 response from API: %d" % response.status_code,
                                event=event, start=start)

        response_data = response.json()

        # grab the status out of our response
        status = response_data['SMSMessageData']['Recipients'][0]['status']
        if status != 'Success':
            raise SendException("Got non success status from API: %s" % status,
                                event=event, start=start)

        # set our external id so we know when it is actually sent, this is missing in cases where
        # it wasn't sent, in which case we'll become an errored message
        external_id = response_data['SMSMessageData']['Recipients'][0]['messageId']

        Channel.success(channel, msg, SENT, start, event=event, external_id=external_id)

    @classmethod
    def send_twilio_message(cls, channel, msg, text):
        from temba.msgs.models import Msg, WIRED
        from temba.orgs.models import ACCOUNT_SID, ACCOUNT_TOKEN
        from temba.utils.twilio import TembaTwilioRestClient

        callback_url = Channel.build_twilio_callback_url(msg.id)

        start = time.time()
        media_url = []

        if msg.media:
            (media_type, media_url) = Msg.get_media(msg)
            media_url = [media_url]

        if channel.channel_type == Channel.TYPE_TWIML:  # pragma: no cover
            config = channel.config
            client = TembaTwilioRestClient(config.get(ACCOUNT_SID), config.get(ACCOUNT_TOKEN),
                                           base=config.get(Channel.CONFIG_SEND_URL))
        else:
            client = TembaTwilioRestClient(channel.org_config[ACCOUNT_SID], channel.org_config[ACCOUNT_TOKEN])

        try:
            if channel.channel_type == Channel.TYPE_TWILIO_MESSAGING_SERVICE:
                messaging_service_sid = channel.config['messaging_service_sid']
                client.messages.create(to=msg.urn_path,
                                       messaging_service_sid=messaging_service_sid,
                                       body=text,
                                       media_url=media_url,
                                       status_callback=callback_url)
            else:
                client.messages.create(to=msg.urn_path,
                                       from_=channel.address,
                                       body=text,
                                       media_url=media_url,
                                       status_callback=callback_url)

            Channel.success(channel, msg, WIRED, start, events=client.messages.events)

        except TwilioRestException as e:
            fatal = False

            # user has blacklisted us, stop the contact
            if e.code == 21610:
                from temba.contacts.models import Contact
                fatal = True
                contact = Contact.objects.get(id=msg.contact)
                contact.stop(contact.modified_by)

            raise SendException(e.msg, events=client.messages.events, fatal=fatal)

        except Exception as e:
            raise SendException(six.text_type(e), events=client.messages.events)

    @classmethod
    def send_telegram_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

        auth_token = channel.config[Channel.CONFIG_AUTH_TOKEN]
        send_url = 'https://api.telegram.org/bot%s/sendMessage' % auth_token
        post_body = dict(chat_id=msg.urn_path, text=text)

        start = time.time()

        from temba.msgs.models import Msg
        media_type, media_url = Msg.get_media(msg)

        if media_type and media_url:
            media_type = media_type.split('/')[0]
            if media_type == 'image':
                send_url = 'https://api.telegram.org/bot%s/sendPhoto' % auth_token
                post_body['photo'] = media_url
                post_body['caption'] = text
                del post_body['text']
            elif media_type == 'video':
                send_url = 'https://api.telegram.org/bot%s/sendVideo' % auth_token
                post_body['video'] = media_url
                post_body['caption'] = text
                del post_body['text']
            elif media_type == 'audio':
                send_url = 'https://api.telegram.org/bot%s/sendAudio' % auth_token
                post_body['audio'] = media_url
                post_body['caption'] = text
                del post_body['text']

        event = HttpEvent('POST', send_url, urlencode(post_body))
        external_id = None

        try:
            response = requests.post(send_url, post_body)
            event.status_code = response.status_code
            event.response_body = response.text

            external_id = response.json()['result']['message_id']
        except Exception as e:
            raise SendException(str(e), event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)

    @classmethod
    def send_twitter_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED
        from temba.contacts.models import Contact

        consumer_key = settings.TWITTER_API_KEY
        consumer_secret = settings.TWITTER_API_SECRET
        oauth_token = channel.config['oauth_token']
        oauth_token_secret = channel.config['oauth_token_secret']

        twitter = TembaTwython(consumer_key, consumer_secret, oauth_token, oauth_token_secret)

        start = time.time()

        try:
            # TODO: Wrap in such a way that we can get full request/response details
            dm = twitter.send_direct_message(screen_name=msg.urn_path, text=text)
        except Exception as e:
            error_code = getattr(e, 'error_code', 400)
            fatal = False

            if error_code == 404:  # handle doesn't exist
                fatal = True
            elif error_code == 403:
                for err in Channel.TWITTER_FATAL_403S:
                    if six.text_type(e).find(err) >= 0:
                        fatal = True
                        break

            # if message can never be sent, stop them contact
            if fatal:
                contact = Contact.objects.get(id=msg.contact)
                contact.stop(contact.modified_by)

            raise SendException(str(e), events=twitter.events, fatal=fatal, start=start)

        external_id = dm['id']
        Channel.success(channel, msg, WIRED, start, events=twitter.events, external_id=external_id)

    @classmethod
    def send_clickatell_message(cls, channel, msg, text):
        """
        Sends a message to Clickatell, they expect a GET in the following format:
             https://api.clickatell.com/http/sendmsg?api_id=xxx&user=xxxx&password=xxxx&to=xxxxx&text=xxxx
        """
        from temba.msgs.models import WIRED

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

        event = HttpEvent('GET', url + "?" + urlencode(payload))

        start = time.time()

        try:
            response = requests.get(url, params=payload, headers=TEMBA_HEADERS, timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        # parse out the external id for the message, comes in the format: "ID: id12312312312"
        external_id = None
        if response.text.startswith("ID: "):
            external_id = response.text[4:]

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)

    @classmethod
    def send_plivo_message(cls, channel, msg, text):
        import plivo
        from temba.msgs.models import WIRED

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

        event = HttpEvent('POST', url, json.dumps(payload))

        start = time.time()

        try:
            # TODO: Grab real request and response here
            plivo_response_status, plivo_response = client.send_message(params=payload)
            event.status_code = plivo_response_status
            event.response_body = plivo_response

        except Exception as e:  # pragma: no cover
            raise SendException(six.text_type(e), event=event, start=start)

        if plivo_response_status != 200 and plivo_response_status != 201 and plivo_response_status != 202:
            raise SendException("Got non-200 response [%d] from API" % plivo_response_status,
                                event=event, start=start)

        external_id = plivo_response['message_uuid'][0]
        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)

    @classmethod
    def send_m3tech_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

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

        event = HttpEvent('GET', url + "?" + urlencode(payload))

        start = time.time()

        try:
            response = requests.get(url, params=payload, headers=TEMBA_HEADERS, timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        # our response is JSON and should contain a 0 as a status code:
        # [{"Response":"0"}]
        try:
            response_code = json.loads(response.text)[0]["Response"]
        except Exception as e:
            response_code = str(e)

        # <Response>0</Response>
        if response_code != "0":
            raise SendException("Received non-zero status from API: %s" % str(response_code),
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)

    @classmethod
    def send_viber_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

        url = 'https://services.viber.com/vibersrvc/1/send_message'
        payload = {'service_id': int(channel.address),
                   'dest': msg.urn_path.lstrip('+'),
                   'seq': msg.id,
                   'type': 206,
                   'message': {
                       '#txt': text,
                       '#tracking_data': 'tracking_id:%d' % msg.id}}

        event = HttpEvent('POST', url, json.dumps(payload))

        start = time.time()

        headers = dict(Accept='application/json')
        headers.update(TEMBA_HEADERS)

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text

            response_json = response.json()
        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code not in [200, 201, 202]:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        # success is 0, everything else is a failure
        if response_json['status'] != 0:
            raise SendException("Got non-0 status [%d] from API" % response_json['status'],
                                event=event, fatal=True, start=start)

        external_id = response.json().get('message_token', None)
        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)

    @classmethod
    def send_viber_public_message(cls, channel, msg, text):
        from temba.msgs.models import WIRED

        url = 'https://chatapi.viber.com/pa/send_message'
        payload = dict(auth_token=channel.config[Channel.CONFIG_AUTH_TOKEN],
                       receiver=msg.urn_path,
                       text=text,
                       type='text',
                       tracking_data=msg.id)

        event = HttpEvent('POST', url, json.dumps(payload))

        start = time.time()

        headers = dict(Accept='application/json')
        headers.update(TEMBA_HEADERS)

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text

            response_json = response.json()
        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code not in [200, 201, 202]:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        # success is 0, everything else is a failure
        if response_json['status'] != 0:
            raise SendException("Got non-0 status [%d] from API" % response_json['status'],
                                event=event, fatal=True, start=start)

        external_id = response.json().get('message_token', None)
        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)

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
        five_minutes_ago = now - timedelta(minutes=5)

        pending = Msg.objects.filter(org=org, direction=OUTGOING)
        pending = pending.filter(Q(status=PENDING, created_on__lte=five_minutes_ago) |
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
            Msg.mark_sent(r, msg, WIRED, -1)
            print("!! [%d] prevented duplicate send" % msg.id)
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

        # append media url if our channel doesn't support it
        text = msg.text

        if msg.media and not Channel.supports_media(channel):
            media_type, media_url = Msg.get_media(msg)
            if media_type and media_url:
                text = '%s\n%s' % (text, media_url)

            # don't send as media
            msg.media = None

        parts = Msg.get_text_parts(text, channel.config.get(Channel.CONFIG_MAX_LENGTH, type_settings[Channel.CONFIG_MAX_LENGTH]))

        for part in parts:
            sent_count += 1
            try:
                channel_type = channel.channel_type

                # never send in debug unless overridden
                if not settings.SEND_MESSAGES:
                    Msg.mark_sent(r, msg, WIRED, -1)
                    print("FAKED SEND for [%d] - %s" % (msg.id, part))
                elif channel_type in SEND_FUNCTIONS:
                    SEND_FUNCTIONS[channel_type](channel, msg, part)
                else:
                    sent_count -= 1
                    raise Exception(_("Unknown channel type: %(channel)s") % {'channel': channel.channel_type})
            except SendException as e:
                ChannelLog.log_exception(channel, msg, e)

                import traceback
                traceback.print_exc(e)

                Msg.mark_error(r, channel, msg, fatal=e.fatal)
                sent_count -= 1

            except Exception as e:
                ChannelLog.log_error(msg, six.text_type(e))

                import traceback
                traceback.print_exc(e)

                Msg.mark_error(r, channel, msg)
                sent_count -= 1

            finally:
                # if we are still in a queued state, mark ourselves as an error
                if msg.status == QUEUED:
                    print("!! [%d] marking queued message as error" % msg.id)
                    Msg.mark_error(r, channel, msg)
                    sent_count -= 1

                    # make sure media isn't sent more than once
                    msg.media = None

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
        url = "https://" + settings.TEMBA_HOST + reverse('handlers.twilio_handler') + "?action=callback&id=%d" % sms_id
        return url

    def __str__(self):  # pragma: no cover
        if self.name:
            return self.name
        elif self.device:
            return self.device
        elif self.address:
            return self.address
        else:
            return six.text_type(self.pk)

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
        return self.get_count([ChannelCount.ERROR_LOG_TYPE]) + self.get_ivr_log_count()

    def get_success_log_count(self):
        return self.get_count([ChannelCount.SUCCESS_LOG_TYPE])

    def get_ivr_log_count(self):
        return ChannelLog.objects.filter(channel=self).exclude(session=None).order_by('session').distinct('session').count()

    def get_non_ivr_log_count(self):
        return self.get_log_count() - self.get_ivr_log_count()

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
                  Channel.TYPE_DARTMEDIA: Channel.send_hub9_or_dartmedia_message,
                  Channel.TYPE_DUMMY: Channel.send_dummy_message,
                  Channel.TYPE_EXTERNAL: Channel.send_external_message,
                  Channel.TYPE_FACEBOOK: Channel.send_facebook_message,
                  Channel.TYPE_FCM: Channel.send_fcm_message,
                  Channel.TYPE_GLOBE: Channel.send_globe_message,
                  Channel.TYPE_HIGH_CONNECTION: Channel.send_high_connection_message,
                  Channel.TYPE_HUB9: Channel.send_hub9_or_dartmedia_message,
                  Channel.TYPE_INFOBIP: Channel.send_infobip_message,
                  Channel.TYPE_JASMIN: Channel.send_jasmin_message,
                  Channel.TYPE_JUNEBUG: Channel.send_junebug_message,
                  Channel.TYPE_JUNEBUG_USSD: Channel.send_junebug_message,
                  Channel.TYPE_KANNEL: Channel.send_kannel_message,
                  Channel.TYPE_LINE: Channel.send_line_message,
                  Channel.TYPE_M3TECH: Channel.send_m3tech_message,
                  Channel.TYPE_MACROKIOSK: Channel.send_macrokiosk_message,
                  Channel.TYPE_MBLOX: Channel.send_mblox_message,
                  Channel.TYPE_NEXMO: Channel.send_nexmo_message,
                  Channel.TYPE_PLIVO: Channel.send_plivo_message,
                  Channel.TYPE_RED_RABBIT: Channel.send_red_rabbit_message,
                  Channel.TYPE_SHAQODOON: Channel.send_shaqodoon_message,
                  Channel.TYPE_SMSCENTRAL: Channel.send_smscentral_message,
                  Channel.TYPE_START: Channel.send_start_message,
                  Channel.TYPE_TELEGRAM: Channel.send_telegram_message,
                  Channel.TYPE_TWILIO: Channel.send_twilio_message,
                  Channel.TYPE_TWIML: Channel.send_twilio_message,
                  Channel.TYPE_TWILIO_MESSAGING_SERVICE: Channel.send_twilio_message,
                  Channel.TYPE_TWITTER: Channel.send_twitter_message,
                  Channel.TYPE_VIBER: Channel.send_viber_message,
                  Channel.TYPE_VIBER_PUBLIC: Channel.send_viber_public_message,
                  Channel.TYPE_VUMI: Channel.send_vumi_message,
                  Channel.TYPE_VUMI_USSD: Channel.send_vumi_message,
                  Channel.TYPE_YO: Channel.send_yo_message,
                  Channel.TYPE_ZENVIA: Channel.send_zenvia_message}


@six.python_2_unicode_compatible
class ChannelCount(SquashableModel):
    """
    This model is maintained by Postgres triggers and maintains the daily counts of messages and ivr interactions
    on each day. This allows for fast visualizations of activity on the channel read page as well as summaries
    of message usage over the course of time.
    """
    SQUASH_OVER = ('channel_id', 'count_type', 'day')

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

        return count['count_sum'] if count['count_sum'] is not None else 0

    @classmethod
    def get_squash_query(cls, distinct_set):
        if distinct_set.day:
            sql = """
            WITH removed as (
                DELETE FROM %(table)s WHERE "channel_id" = %%s AND "count_type" = %%s AND "day" = %%s RETURNING "count"
            )
            INSERT INTO %(table)s("channel_id", "count_type", "day", "count", "is_squashed")
            VALUES (%%s, %%s, %%s, GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
            """ % {'table': cls._meta.db_table}

            params = (distinct_set.channel_id, distinct_set.count_type, distinct_set.day) * 2
        else:
            sql = """
            WITH removed as (
                DELETE FROM %(table)s WHERE "channel_id" = %%s AND "count_type" = %%s AND "day" IS NULL RETURNING "count"
            )
            INSERT INTO %(table)s("channel_id", "count_type", "day", "count", "is_squashed")
            VALUES (%%s, %%s, NULL, GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
            """ % {'table': cls._meta.db_table}

            params = (distinct_set.channel_id, distinct_set.count_type) * 2

        return sql, params

    def __str__(self):  # pragma: no cover
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

    def __init__(self, description, event=None, events=None, fatal=False, start=None):
        super(SendException, self).__init__(description)

        if events is None and event:
            events = [event]

        self.description = description
        self.events = events
        self.fatal = fatal
        self.start = start


class ChannelLog(models.Model):
    channel = models.ForeignKey(Channel, related_name='logs',
                                help_text=_("The channel the message was sent on"))
    msg = models.ForeignKey('msgs.Msg', related_name='channel_logs', null=True,
                            help_text=_("The message that was sent"))

    session = models.ForeignKey('channels.ChannelSession', related_name='channel_logs', null=True,
                                help_text=_("The channel session for this log"))

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
    request_time = models.IntegerField(null=True, help_text=_('Time it took to process this request'))

    @classmethod
    def log_exception(cls, channel, msg, e):
        # calculate our request time if possible
        request_time = 0 if not e.start else time.time() - e.start

        for event in e.events:
            print(u"[%d] %0.3fs ERROR - %s %s \"%s\" %s \"%s\"" %
                  (msg.id, request_time, event.method, event.url, event.request_body, event.status_code, event.response_body))

            # log our request time in ms
            request_time_ms = request_time * 1000

            ChannelLog.objects.create(channel_id=msg.channel,
                                      msg_id=msg.id,
                                      is_error=True,
                                      description=six.text_type(e.description)[:255],
                                      method=event.method,
                                      url=event.url,
                                      request=event.request_body,
                                      response=event.response_body,
                                      response_status=event.status_code,
                                      request_time=request_time_ms)

        if request_time > 0:
            analytics.gauge('temba.msg_sent_%s' % channel.channel_type.lower(), request_time)

    @classmethod
    def log_error(cls, msg, description):
        print(u"[%d] ERROR - %s" % (msg.id, description))
        ChannelLog.objects.create(channel_id=msg.channel,
                                  msg_id=msg.id,
                                  is_error=True,
                                  description=description[:255])

    @classmethod
    def log_message(cls, msg, description, event, is_error=False):
        ChannelLog.objects.create(channel_id=msg.channel_id,
                                  msg=msg,
                                  request=event.request_body,
                                  response=event.response_body,
                                  url=event.url,
                                  method=event.method,
                                  is_error=is_error,
                                  response_status=event.status_code,
                                  description=description[:255])

    @classmethod
    def log_ivr_interaction(cls, call, description, event, is_error=False):
        ChannelLog.objects.create(channel_id=call.channel_id,
                                  session_id=call.id,
                                  request=str(event.request_body),
                                  response=str(event.response_body),
                                  url=event.url,
                                  method=event.method,
                                  is_error=is_error,
                                  response_status=event.status_code,
                                  description=description[:255])

    def get_url_host(self):
        parsed = urlparse.urlparse(self.url)
        return '%s://%s%s' % (parsed.scheme, parsed.hostname, parsed.path)

    def get_request_formatted(self):

        if self.method == 'GET':
            return self.url

        try:
            return json.dumps(json.loads(self.request), indent=2)
        except:
            return self.request

    def get_response_formatted(self):
        try:
            return json.dumps(json.loads(self.response), indent=2)
        except:
            if not self.response:
                self.response = self.description
            return self.response


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
        if channel.device != device or channel.os != os:  # pragma: no cover
            channel.device = device
            channel.os = os
            channel.save(update_fields=['device', 'os'])

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
    if kwargs['raw']:  # pragma: no cover
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
                if channel.org is None:  # pragma: no cover
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


class ChannelSession(SmartModel):
    PENDING = 'P'
    QUEUED = 'Q'
    RINGING = 'R'
    IN_PROGRESS = 'I'
    COMPLETED = 'D'
    BUSY = 'B'
    FAILED = 'F'
    NO_ANSWER = 'N'
    CANCELED = 'C'
    TRIGGERED = 'T'
    INTERRUPTED = 'X'
    INITIATED = 'A'
    ENDING = 'E'

    DONE = [COMPLETED, BUSY, FAILED, NO_ANSWER, CANCELED, INTERRUPTED]

    INCOMING = 'I'
    OUTGOING = 'O'

    IVR = 'F'
    USSD = 'U'

    DIRECTION_CHOICES = ((INCOMING, "Incoming"),
                         (OUTGOING, "Outgoing"))

    TYPE_CHOICES = ((IVR, "IVR"), (USSD, "USSD"),)

    STATUS_CHOICES = ((PENDING, "Pending"),
                      (QUEUED, "Queued"),
                      (RINGING, "Ringing"),
                      (IN_PROGRESS, "In Progress"),
                      (COMPLETED, "Complete"),
                      (BUSY, "Busy"),
                      (FAILED, "Failed"),
                      (NO_ANSWER, "No Answer"),
                      (CANCELED, "Canceled"),
                      (INTERRUPTED, "Interrupted"),
                      (TRIGGERED, "Triggered"),
                      (INITIATED, "Initiated"),
                      (ENDING, "Ending"))

    external_id = models.CharField(max_length=255,
                                   help_text="The external id for this session, our twilio id usually")
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=PENDING,
                              help_text="The status of this session")
    channel = models.ForeignKey('Channel',
                                help_text="The channel that created this session")
    contact = models.ForeignKey('contacts.Contact', related_name='sessions',
                                help_text="Who this session is with")
    contact_urn = models.ForeignKey('contacts.ContactURN', verbose_name=_("Contact URN"),
                                    help_text=_("The URN this session is communicating with"))
    direction = models.CharField(max_length=1, choices=DIRECTION_CHOICES,
                                 help_text="The direction of this session, either incoming or outgoing")
    started_on = models.DateTimeField(null=True, blank=True,
                                      help_text="When this session was connected and started")
    ended_on = models.DateTimeField(null=True, blank=True,
                                    help_text="When this session ended")
    org = models.ForeignKey(Org,
                            help_text="The organization this session belongs to")
    session_type = models.CharField(max_length=1, choices=TYPE_CHOICES,
                                    help_text="What sort of session this is")
    duration = models.IntegerField(default=0, null=True,
                                   help_text="The length of this session in seconds")

    def __init__(self, *args, **kwargs):
        super(ChannelSession, self).__init__(*args, **kwargs)

        """ This is needed when referencing `session` from `FlowRun`. Since
        the FK is bound to ChannelSession, when it initializes an instance from
        DB we need to specify the class based on `session_type` so we can access
        all the methods the proxy model implements. """

        if type(self) is ChannelSession:
            if self.session_type == self.USSD:
                from temba.ussd.models import USSDSession
                self.__class__ = USSDSession
            elif self.session_type == self.IVR:
                from temba.ivr.models import IVRCall
                self.__class__ = IVRCall

    def get_logs(self):
        return self.channel_logs.all().order_by('created_on')

    def get_duration(self):
        return timedelta(seconds=self.duration)

    def is_done(self):
        return self.status in self.DONE

    def is_ivr(self):
        return self.session_type == self.IVR

    def close(self):  # pragma: no cover
        pass

    def get(self):
        if self.session_type == ChannelSession.IVR:
            from temba.ivr.models import IVRCall
            return IVRCall.objects.filter(id=self.id).first()
        if self.session_type == ChannelSession.USSD:
            from temba.ussd.models import USSDSession
            return USSDSession.objects.filter(id=self.id).first()
        return self  # pragma: no cover
