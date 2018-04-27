# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import logging
import phonenumbers
import six
import time
from six.moves.urllib.parse import urlparse

from abc import ABCMeta

from django.template import Engine
from enum import Enum
from datetime import timedelta
from django.template import Context
from django.conf import settings
from django.conf.urls import url
from django.contrib.auth.models import User, Group
from django.contrib.postgres.fields import ArrayField
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models import Q, Max, Sum
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.template import TemplateDoesNotExist
from django.utils import timezone
from django.utils.http import urlquote_plus
from django.utils.translation import ugettext_lazy as _
from django_countries.fields import CountryField
from django_redis import get_redis_connection
from gcm.gcm import GCM, GCMNotRegisteredException
from phonenumbers import NumberParseException
from pyfcm import FCMNotification
from smartmin.models import SmartModel
from temba.orgs.models import Org, CHATBASE_TYPE_AGENT, NEXMO_APP_ID, NEXMO_APP_PRIVATE_KEY, NEXMO_KEY, NEXMO_SECRET
from temba.utils import analytics, dict_to_struct, dict_to_json, on_transaction_commit, get_anonymous_user
from temba.utils.email import send_template_email
from temba.utils.gsm7 import is_gsm7, replace_non_gsm7_accents, calculate_num_segments
from temba.utils.http import HttpEvent
from temba.utils.nexmo import NCCOResponse
from temba.utils.models import SquashableModel, TembaModel, generate_uuid, JSONAsTextField
from temba.utils.text import random_string
from twilio import twiml, TwilioRestException
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

# Hub9 is an aggregator in Indonesia, set this to the endpoint for your service
# and make sure you send from a whitelisted IP Address
HUB9_ENDPOINT = 'http://175.103.48.29:28078/testing/smsmt.php'

# Dart Media is another aggregator in Indonesia, set this to the endpoint for your service
DART_MEDIA_ENDPOINT = 'http://202.43.169.11/APIhttpU/receive2waysms.php'

# the event type for channel events in the handler queue
CHANNEL_EVENT = 'channel_event'


class Encoding(Enum):
    GSM7 = 1
    REPLACED = 2
    UNICODE = 3


class ChannelType(six.with_metaclass(ABCMeta)):
    """
    Base class for all dynamic channel types
    """
    class Category(Enum):
        PHONE = 1
        SOCIAL_MEDIA = 2
        USSD = 3
        API = 4

    class IVRProtocol(Enum):
        IVR_PROTOCOL_TWIML = 1
        IVR_PROTOCOL_NCCO = 2

    code = None
    slug = None
    category = None

    name = None
    icon = 'icon-channel-external'
    schemes = None
    show_config_page = True

    available_timezones = None
    recommended_timezones = None

    claim_blurb = None
    claim_view = None

    configuration_blurb = None
    configuration_urls = None
    show_public_addresses = False

    update_form = None

    max_length = -1
    max_tps = None
    attachment_support = False
    free_sending = False
    quick_reply_text_size = 20

    ivr_protocol = None

    def is_available_to(self, user):
        """
        Determines whether this channel type is available to the given user, e.g. check timezone
        """
        if self.available_timezones is not None:
            timezone = user.get_org().timezone
            return timezone and six.text_type(timezone) in self.available_timezones
        else:
            return True

    def is_recommended_to(self, user):
        """
        Determines whether this channel type is recommended to the given user.
        """
        if self.recommended_timezones is not None:
            timezone = user.get_org().timezone
            return timezone and six.text_type(timezone) in self.recommended_timezones
        else:
            return False

    def get_claim_blurb(self):
        """
        Gets the blurb for use on the claim page list of channel types
        """
        return Engine.get_default().from_string(self.claim_blurb)

    def get_urls(self):
        """
        Returns all the URLs this channel exposes to Django, the URL should be relative.
        """
        if self.claim_view:
            return [self.get_claim_url()]
        else:
            return []

    def get_claim_url(self):
        """
        Gets the URL/view configuration for this channel types's claim page
        """
        return url(r'^claim$', self.claim_view.as_view(channel_type=self), name='claim')

    def get_update_form(self):
        if self.update_form is None:
            from .views import UpdateChannelForm
            return UpdateChannelForm
        return self.update_form

    def activate(self, channel):
        """
        Called when a channel of this type has been created. Can be used to setup things like callbacks required by the
        channel. Note: this will only be called if IS_PROD setting is True.
        """

    def deactivate(self, channel):
        """
        Called when a channel of this type has been released. Can be used to cleanup things like callbacks which were
        used by the channel. Note: this will only be called if IS_PROD setting is True.
        """

    def activate_trigger(self, trigger):
        """
        Called when a trigger that is bound to a channel of this type is being created or restored. Note: this will only
        be called if IS_PROD setting is True.
        """

    def deactivate_trigger(self, trigger):
        """
        Called when a trigger that is bound to a channel of this type is being released. Note: this will only be called
        if IS_PROD setting is True.
        """

    def send(self, channel, msg, text):  # pragma: no cover
        """
        Sends the given message struct. Note: this will only be called if SEND_MESSAGES setting is True.
        """
        raise NotImplemented("sending for channel type '%s' should be done via Courier" % self.__class__.code)

    def has_attachment_support(self, channel):
        """
        Whether the given channel instance supports message attachments
        """
        return self.attachment_support

    def setup_periodic_tasks(self, sender):
        """
        Allows a ChannelType to register periodic tasks it wants celery to run.
        ex: sender.add_periodic_task(300, remap_twitter_ids)
        """

    def get_configuration_context_dict(self, channel):
        return dict(channel=channel, ip_addresses=settings.IP_ADDRESSES)

    def get_configuration_template(self, channel):
        try:
            return Engine.get_default().get_template('channels/types/%s/config.html' % self.slug).render(context=Context(self.get_configuration_context_dict(channel)))
        except TemplateDoesNotExist:
            return ""

    def get_configuration_blurb(self, channel):
        """
        Allows ChannelTypes to define the blurb to show on the channel configuration page.
        """
        if self.__class__.configuration_blurb is not None:
            return Engine.get_default().from_string(self.configuration_blurb).render(context=Context(dict(channel=channel)))
        else:
            return ""

    def get_configuration_urls(self, channel):
        """
        Allows ChannelTypes to specify a list of URLs to show with a label and description on the
        configuration page.
        """
        if self.__class__.configuration_urls is not None:
            context = Context(dict(channel=channel))
            engine = Engine.get_default()

            urls = []
            for url_config in self.__class__.configuration_urls:
                urls.append(dict(
                    label=engine.from_string(url_config.get('label', "")).render(context=context),
                    url=engine.from_string(url_config.get('url', "")).render(context=context),
                    description=engine.from_string(url_config.get('description', "")).render(context=context),
                ))

            return urls

        else:
            return ""

    def __str__(self):
        return self.name


@six.python_2_unicode_compatible
class Channel(TembaModel):
    TYPE_ANDROID = 'A'
    TYPE_DUMMY = 'DM'

    # keys for various config options stored in the channel config dict
    CONFIG_BASE_URL = 'base_url'
    CONFIG_SEND_URL = 'send_url'
    CONFIG_SEND_METHOD = 'method'
    CONFIG_SEND_BODY = 'body'
    CONFIG_DEFAULT_SEND_BODY = 'id={{id}}&text={{text}}&to={{to}}&to_no_plus={{to_no_plus}}&from={{from}}&from_no_plus={{from_no_plus}}&channel={{channel}}'
    CONFIG_USERNAME = 'username'
    CONFIG_PASSWORD = 'password'
    CONFIG_KEY = 'key'
    CONFIG_API_ID = 'api_id'
    CONFIG_API_KEY = 'api_key'
    CONFIG_CONTENT_TYPE = 'content_type'
    CONFIG_VERIFY_SSL = 'verify_ssl'
    CONFIG_USE_NATIONAL = 'use_national'
    CONFIG_ENCODING = 'encoding'
    CONFIG_PAGE_NAME = 'page_name'
    CONFIG_PLIVO_AUTH_ID = 'PLIVO_AUTH_ID'
    CONFIG_PLIVO_AUTH_TOKEN = 'PLIVO_AUTH_TOKEN'
    CONFIG_PLIVO_APP_ID = 'PLIVO_APP_ID'
    CONFIG_AUTH_TOKEN = 'auth_token'
    CONFIG_SECRET = 'secret'
    CONFIG_CHANNEL_ID = 'channel_id'
    CONFIG_CHANNEL_MID = 'channel_mid'
    CONFIG_FCM_ID = 'FCM_ID'
    CONFIG_MAX_LENGTH = 'max_length'
    CONFIG_MACROKIOSK_SENDER_ID = 'macrokiosk_sender_id'
    CONFIG_MACROKIOSK_SERVICE_ID = 'macrokiosk_service_id'
    CONFIG_RP_HOSTNAME_OVERRIDE = 'rp_hostname_override'
    CONFIG_CALLBACK_DOMAIN = 'callback_domain'
    CONFIG_ACCOUNT_SID = 'account_sid'
    CONFIG_APPLICATION_SID = 'application_sid'
    CONFIG_NUMBER_SID = 'number_sid'
    CONFIG_MESSAGING_SERVICE_SID = 'messaging_service_sid'

    CONFIG_NEXMO_API_KEY = 'nexmo_api_key'
    CONFIG_NEXMO_API_SECRET = 'nexmo_api_secret'
    CONFIG_NEXMO_APP_ID = 'nexmo_app_id'
    CONFIG_NEXMO_APP_PRIVATE_KEY = 'nexmo_app_private_key'

    CONFIG_SHORTCODE_MATCHING_PREFIXES = 'matching_prefixes'

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

    YO_API_URL_1 = 'http://smgw1.yo.co.ug:9100/sendsms'
    YO_API_URL_2 = 'http://41.220.12.201:9100/sendsms'
    YO_API_URL_3 = 'http://164.40.148.210:9100/sendsms'

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

    # our default max tps is 50
    DEFAULT_TPS = 50

    # various hard coded settings for the channel types
    CHANNEL_SETTINGS = {
        TYPE_ANDROID: dict(schemes=['tel'], max_length=-1),
        TYPE_DUMMY: dict(schemes=['tel'], max_length=160),
    }

    TYPE_CHOICES = ((TYPE_ANDROID, "Android"),
                    (TYPE_DUMMY, "Dummy"))

    TYPE_ICONS = {
        TYPE_ANDROID: "icon-channel-android",
    }

    HIDE_CONFIG_PAGE = [TYPE_ANDROID]

    SIMULATOR_CONTEXT = dict(__default__='(800) 555-1212', name='Simulator', tel='(800) 555-1212', tel_e164='+18005551212')

    channel_type = models.CharField(verbose_name=_("Channel Type"), max_length=3)

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

    config = JSONAsTextField(verbose_name=_("Config"), null=True, default=dict,
                             help_text=_("Any channel specific configuration, used for the various aggregators"))

    schemes = ArrayField(models.CharField(max_length=16), default=['tel'],
                         verbose_name="URN Schemes", help_text=_("The URN schemes this channel supports"))

    role = models.CharField(verbose_name="Channel Role", max_length=4, default=DEFAULT_ROLE,
                            help_text=_("The roles this channel can fulfill"))

    parent = models.ForeignKey('self', blank=True, null=True,
                               help_text=_("The channel this channel is working on behalf of"))

    bod = models.TextField(verbose_name=_("Optional Data"), null=True,
                           help_text=_("Any channel specific state data"))

    tps = models.IntegerField(verbose_name=_("Maximum Transactions per Second"), null=True,
                              help_text=_("The max number of messages that will be sent per second"))

    @classmethod
    def create(cls, org, user, country, channel_type, name=None, address=None, config=None, role=DEFAULT_ROLE, schemes=None, **kwargs):
        if isinstance(channel_type, six.string_types):
            channel_type = cls.get_type_from_code(channel_type)

        if schemes:
            if channel_type.schemes and not set(channel_type.schemes).intersection(schemes):
                raise ValueError("Channel type '%s' cannot support schemes %s" % (channel_type, schemes))
        else:
            schemes = channel_type.schemes

        if not schemes:
            raise ValueError("Cannot create channel without schemes")

        if country and schemes[0] not in ['tel', 'whatsapp']:
            raise ValueError("Only channels handling phone numbers can be country specific")

        if config is None:
            config = {}

        create_args = dict(org=org, created_by=user, modified_by=user,
                           country=country,
                           channel_type=channel_type.code,
                           name=name, address=address,
                           config=config,
                           role=role, schemes=schemes)
        create_args.update(kwargs)

        if 'uuid' not in create_args:
            create_args['uuid'] = generate_uuid()

        channel = cls.objects.create(**create_args)

        # normalize any telephone numbers that we may now have a clue as to country
        if org and country:
            org.normalize_contact_tels()

        if settings.IS_PROD:
            on_transaction_commit(lambda: channel_type.activate(channel))

        return channel

    @classmethod
    def get_type_from_code(cls, code):
        from .types import TYPES
        try:
            return TYPES[code]
        except KeyError:  # pragma: no cover
            raise ValueError("Unrecognized channel type code: %s" % code)

    @classmethod
    def get_types(cls):
        from .types import TYPES
        return six.itervalues(TYPES)

    @classmethod
    def get_by_category(cls, org, category):
        category_channel_types = [c_type.code for c_type in Channel.get_types() if c_type.category == category]
        return org.channels.filter(is_active=True, channel_type__in=category_channel_types)

    def get_type(self):
        return self.get_type_from_code(self.channel_type)

    @classmethod
    def add_authenticated_external_channel(cls, org, user, country, phone_number, username, password, channel_type,
                                           url, role=DEFAULT_ROLE, extra_config=None):

        try:
            parsed = phonenumbers.parse(phone_number, None)
            phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        except Exception:
            # this is a shortcode, just use it plain
            phone = phone_number

        config = dict(username=username, password=password, send_url=url)
        if extra_config:
            config.update(extra_config)

        return Channel.create(org, user, country, channel_type, name=phone, address=phone_number, config=config,
                              role=role)

    @classmethod
    def add_config_external_channel(cls, org, user, country, address, channel_type, config, role=DEFAULT_ROLE,
                                    schemes=['tel'], parent=None):
        return Channel.create(org, user, country, channel_type, name=address, address=address,
                              config=config, role=role, schemes=schemes, parent=parent)

    @classmethod
    def add_send_channel(cls, user, channel):
        # nexmo ships numbers around as E164 without the leading +
        parsed = phonenumbers.parse(channel.address, None)
        nexmo_phone_number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164).strip('+')

        org = user.get_org()
        org_config = org.config

        config = {Channel.CONFIG_NEXMO_APP_ID: org_config.get(NEXMO_APP_ID),
                  Channel.CONFIG_NEXMO_APP_PRIVATE_KEY: org_config[NEXMO_APP_PRIVATE_KEY],
                  Channel.CONFIG_NEXMO_API_KEY: org_config[NEXMO_KEY],
                  Channel.CONFIG_NEXMO_API_SECRET: org_config[NEXMO_SECRET],
                  Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain()}

        return Channel.create(user.get_org(), user, channel.country, 'NX', name="Nexmo Sender", config=config, tps=1,
                              address=channel.address, role=Channel.ROLE_SEND, parent=channel, bod=nexmo_phone_number)

    @classmethod
    def add_call_channel(cls, org, user, channel):
        return Channel.create(org, user, channel.country, 'T', name="Twilio Caller",
                              address=channel.address, role=Channel.ROLE_CALL, parent=channel)

    @classmethod
    def refresh_all_jiochat_access_token(cls, channel_id=None):
        from temba.utils.jiochat import JiochatClient

        jiochat_channels = Channel.objects.filter(channel_type='JC', is_active=True)
        if channel_id:
            jiochat_channels = jiochat_channels.filter(id=channel_id)

        for channel in jiochat_channels:
            client = JiochatClient.from_channel(channel)
            if client is not None:
                client.refresh_access_token(channel.id)

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
            config = existing.config
            config.update({Channel.CONFIG_FCM_ID: fcm_id})
            existing.config = config
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
        anon = get_anonymous_user()
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
    def generate_secret(cls, length=64):
        """
        Generates a secret value used for command signing
        """
        code = random_string(length)
        while cls.objects.filter(secret=code):  # pragma: no cover
            code = random_string(length)
        return code

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

    def has_channel_log(self):
        return self.channel_type != Channel.TYPE_ANDROID

    def get_delegate_channels(self):
        # detached channels can't have delegates
        if not self.org:  # pragma: no cover
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
        return self.get_delegate(Channel.ROLE_SEND)

    def get_caller(self):
        return self.get_delegate(Channel.ROLE_CALL)

    @property
    def callback_domain(self):
        """
        Returns the domain to use for callbacks, this can be channel specific if set on the config, otherwise the brand domain
        """
        callback_domain = self.config.get(Channel.CONFIG_CALLBACK_DOMAIN, None)
        if callback_domain is None:
            callback_domain = self.org.get_brand_domain()

        return callback_domain

    def get_ussd_delegate(self):
        return self.get_delegate(Channel.ROLE_USSD)

    def is_delegate_sender(self):
        return self.parent and Channel.ROLE_SEND in self.role

    def is_delegate_caller(self):
        return self.parent and Channel.ROLE_CALL in self.role

    def generate_ivr_response(self):
        ivr_protocol = Channel.get_type_from_code(self.channel_type).ivr_protocol
        if ivr_protocol == ChannelType.IVRProtocol.IVR_PROTOCOL_TWIML:
            return twiml.Response()
        if ivr_protocol == ChannelType.IVRProtocol.IVR_PROTOCOL_NCCO:
            return NCCOResponse()

    def get_ivr_client(self):

        # no client for released channels
        if not (self.is_active and self.org):
            return None

        if self.channel_type == 'T':
            return self.org.get_twilio_client()
        elif self.channel_type == 'TW':
            return self.get_twiml_client()
        elif self.channel_type == 'VB':  # pragma: no cover
            return self.org.get_verboice_client()
        elif self.channel_type == 'NX':
            return self.org.get_nexmo_client()

    def get_twiml_client(self):
        from temba.ivr.clients import TwilioClient

        if self.config:
            account_sid = self.config.get(Channel.CONFIG_ACCOUNT_SID, None)
            auth_token = self.config.get(Channel.CONFIG_AUTH_TOKEN, None)
            base = self.config.get(Channel.CONFIG_SEND_URL, None)

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

    def get_channel_type_display(self):
        return self.get_type().name

    def get_channel_type_name(self):
        channel_type_display = self.get_channel_type_display()

        if self.channel_type == Channel.TYPE_ANDROID:
            return _("Android Phone")
        else:
            return _("%s Channel" % channel_type_display)

    def get_address_display(self, e164=False):
        from temba.contacts.models import TEL_SCHEME, TWITTER_SCHEME, FACEBOOK_SCHEME
        if not self.address:
            return ''

        if self.address and TEL_SCHEME in self.schemes and self.country:
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

        elif TWITTER_SCHEME in self.schemes:
            return '@%s' % self.address

        elif FACEBOOK_SCHEME in self.schemes:
            return "%s (%s)" % (self.config.get(Channel.CONFIG_PAGE_NAME, self.name), self.address)

        return self.address

    def build_expressions_context(self):
        from temba.contacts.models import TEL_SCHEME

        address = self.get_address_display()
        default = address if address else six.text_type(self)

        # for backwards compatibility
        if TEL_SCHEME in self.schemes:
            tel = address
            tel_e164 = self.get_address_display(e164=True)
        else:
            tel = ''
            tel_e164 = ''

        return dict(__default__=default, name=self.get_name(), address=address, tel=tel, tel_e164=tel_e164)

    @classmethod
    def get_cached_channel(cls, channel_id):
        """
        Fetches this channel's configuration from our cache, also populating it with the channel uuid
        """
        key = 'channel_config:%d' % channel_id
        cached = cache.get(key, None)

        if cached is None:
            channel = Channel.objects.filter(pk=channel_id, is_active=True).first()

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
        org_config = self.org.config

        return dict(id=self.id, org=self.org_id, country=six.text_type(self.country), address=self.address,
                    uuid=self.uuid, secret=self.secret, channel_type=self.channel_type, name=self.name,
                    callback_domain=self.callback_domain, config=self.config, org_config=org_config)

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
        from temba.msgs.models import Msg

        one_hour_ago = timezone.now() - timedelta(hours=1)
        latest_sent_message = self.get_latest_sent_message()

        # if the last sent message was in the last hour, assume this channel is ok
        if latest_sent_message and latest_sent_message.sent_on > one_hour_ago:  # pragma: no cover
            return Msg.objects.none()

        messages = self.get_unsent_messages()

        # channels have an hour to send messages before we call them delays, so ignore all messages created in last hour
        messages = messages.filter(created_on__lt=one_hour_ago)

        # if we have a successfully sent message, we're only interested a new failures since then. Note that we use id
        # here instead of created_on because we won't hit the outbox index if we use a range condition on created_on.
        if latest_sent_message:
            messages = messages.filter(id__gt=latest_sent_message.id)

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

    def calculate_tps_cost(self, msg):
        """
        Calculates the TPS cost for sending the passed in message. We look at the URN type and for any
        `tel` URNs we just use the calculated segments here. All others have a cost of 1.

        In the case of attachments, our cost is the number of attachments.
        """
        from temba.contacts.models import TEL_SCHEME
        cost = 1
        if msg.contact_urn.scheme == TEL_SCHEME:
            cost = calculate_num_segments(msg.text)

        # if we have attachments then use that as our cost (MMS bundles text into the attachment, but only one per)
        if msg.attachments:
            cost = len(msg.attachments)

        return cost

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

    def release(self, trigger_sync=True):
        """
        Releases this channel, removing it from the org and making it inactive
        """
        channel_type = self.get_type()

        # release any channels working on our behalf as well
        for delegate_channel in Channel.objects.filter(parent=self, org=self.org):
            delegate_channel.release()

        # only call out to external aggregator services if we are on prod servers
        if settings.IS_PROD:
            try:
                # if channel is a new style type, deactivate it
                channel_type.deactivate(self)
            except TwilioRestException as e:
                raise e

            except Exception as e:  # pragma: no cover
                # proceed with removing this channel but log the problem
                logger.exception(six.text_type(e))

            # hangup all its calls
            from temba.ivr.models import IVRCall
            for call in IVRCall.objects.filter(channel=self):
                call.close()

        # save off our org and gcm id before nullifying
        org = self.org
        fcm_id = self.config.get(Channel.CONFIG_FCM_ID)

        if fcm_id is not None:
            registration_id = fcm_id
        else:
            registration_id = self.gcm_id

        # make the channel inactive
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

        from temba.triggers.models import Trigger
        Trigger.objects.filter(channel=self, org=org).update(is_active=False)

    def trigger_sync(self, registration_id=None):  # pragma: no cover
        """
        Sends a GCM command to trigger a sync on the client
        """
        # androids sync via FCM or GCM(for old apps installs)
        if self.channel_type == Channel.TYPE_ANDROID:
            fcm_id = self.config.get(Channel.CONFIG_FCM_ID)

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
                config = channel.config
                config.pop(Channel.CONFIG_FCM_ID, None)
                channel.config = config
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

            # Sending data to Chatbase API
            if hasattr(msg, 'is_org_connected_to_chatbase'):
                chatbase_version = msg.chatbase_version if hasattr(msg, 'chatbase_version') else None
                Msg.send_chatbase_log(msg.chatbase_api_key, chatbase_version, channel.name, msg.text, msg.contact,
                                      CHATBASE_TYPE_AGENT)

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

        # order by date created
        return pending.order_by('created_on')

    @classmethod
    def send_message(cls, msg):  # pragma: no cover
        from temba.msgs.models import Msg, Attachment, QUEUED, WIRED, MSG_SENT_KEY
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

        channel_type = Channel.get_type_from_code(channel.channel_type)

        # Check whether we need to throttle ourselves
        # This isn't an ideal implementation, in that if there is only one Channel with tons of messages
        # and a low throttle rate, we will have lots of threads waiting, but since throttling is currently
        # a rare event, this is an ok stopgap.
        if channel_type.max_tps:
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
                    if count < channel_type.max_tps:
                        r.zadd(tps_set_name, now, now)
                        r.zremrangebyscore(tps_set_name, "-inf", last_second)
                        r.expire(tps_set_name, 5)
                        break

                # too many sent in the last second, sleep a bit and try again
                time.sleep(1 / float(channel_type.max_tps))

        sent_count = 0

        # append media url if our channel doesn't support it
        text = msg.text

        if msg.attachments and not Channel.get_type_from_code(channel.channel_type).has_attachment_support(channel):
            # for now we only support sending one attachment per message but this could change in future
            attachment = Attachment.parse_all(msg.attachments)[0]
            text = '%s\n%s' % (text, attachment.url)

            # don't send as media
            msg.attachments = None

        parts = Msg.get_text_parts(text, channel.config.get(Channel.CONFIG_MAX_LENGTH, channel_type.max_length))

        for part in parts:
            sent_count += 1
            try:
                # never send in debug unless overridden
                if not settings.SEND_MESSAGES:
                    Msg.mark_sent(r, msg, WIRED, -1)
                    print("FAKED SEND for [%d] - %s" % (msg.id, part))
                else:
                    channel_type.send(channel, msg, part)

            except SendException as e:
                ChannelLog.log_exception(channel, msg, e)

                import traceback
                traceback.print_exc()

                Msg.mark_error(r, channel, msg, fatal=e.fatal)
                sent_count -= 1

            except Exception as e:
                ChannelLog.log_error(msg, six.text_type(e))

                import traceback
                traceback.print_exc()

                Msg.mark_error(r, channel, msg)
                sent_count -= 1

            finally:
                # if we are still in a queued state, mark ourselves as an error
                if msg.status == QUEUED:
                    print("!! [%d] marking queued message as error" % msg.id)
                    Msg.mark_error(r, channel, msg)
                    sent_count -= 1

                    # make sure media isn't sent more than once
                    msg.attachments = None

        # update the number of sms it took to send this if it was more than 1
        if len(parts) > 1:
            Msg.objects.filter(id=msg.id).update(msg_count=len(parts))

    @classmethod
    def track_status(cls, channel, status):
        if channel:
            # track success, errors and failures
            analytics.gauge('temba.channel_%s_%s' % (status.lower(), channel.channel_type.lower()))

    @classmethod
    def build_twilio_callback_url(cls, domain, channel_type, channel_uuid, sms_id):
        if channel_type == 'T':
            url = reverse('courier.t', args=[channel_uuid, 'status'])
        elif channel_type == 'TMS':
            url = reverse('courier.tms', args=[channel_uuid, 'status'])
        elif channel_type == 'TW':
            url = reverse('courier.tw', args=[channel_uuid, 'status'])

        url = "https://" + domain + url + "?action=callback&id=%d" % sms_id
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
        return ChannelLog.objects.filter(channel=self).exclude(connection=None).order_by('connection').distinct('connection').count()

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

SEND_FUNCTIONS = {
    Channel.TYPE_DUMMY: Channel.send_dummy_message,
}


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
    TYPE_NEW_CONVERSATION = 'new_conversation'
    TYPE_REFERRAL = 'referral'
    TYPE_FOLLOW = 'follow'
    TYPE_STOP_CONTACT = 'stop_contact'

    EXTRA_REFERRER_ID = 'referrer_id'

    # single char flag, human readable name, API readable name
    TYPE_CONFIG = ((TYPE_UNKNOWN, _("Unknown Call Type"), 'unknown'),
                   (TYPE_CALL_OUT, _("Outgoing Call"), 'call-out'),
                   (TYPE_CALL_OUT_MISSED, _("Missed Outgoing Call"), 'call-out-missed'),
                   (TYPE_CALL_IN, _("Incoming Call"), 'call-in'),
                   (TYPE_CALL_IN_MISSED, _("Missed Incoming Call"), 'call-in-missed'),
                   (TYPE_STOP_CONTACT, _("Stop Contact"), 'stop-contact'),
                   (TYPE_NEW_CONVERSATION, _("New Conversation"), 'new-conversation'),
                   (TYPE_REFERRAL, _("Referral"), 'referral'),
                   (TYPE_FOLLOW, _("Follow"), 'follow'))

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
    extra = JSONAsTextField(verbose_name=_("Extra"), null=True, default=dict,
                            help_text=_("Any extra properties on this event as JSON"))
    occurred_on = models.DateTimeField(verbose_name=_("Occurred On"),
                                       help_text=_("When this event took place"))
    created_on = models.DateTimeField(verbose_name=_("Created On"), default=timezone.now,
                                      help_text=_("When this event was created"))

    @classmethod
    def create(cls, channel, urn, event_type, occurred_on, extra=None):
        from temba.api.models import WebHookEvent
        from temba.contacts.models import Contact
        from temba.triggers.models import Trigger

        org = channel.org
        contact, contact_urn = Contact.get_or_create(org, urn, channel, name=None, user=get_anonymous_user())

        event = cls.objects.create(org=org, channel=channel, contact=contact, contact_urn=contact_urn,
                                   occurred_on=occurred_on, event_type=event_type, extra=extra)

        if event_type in cls.CALL_TYPES:
            analytics.gauge('temba.call_%s' % event.get_event_type_display().lower().replace(' ', '_'))
            WebHookEvent.trigger_call_event(event)

        if event_type == cls.TYPE_CALL_IN_MISSED:
            Trigger.catch_triggers(event, Trigger.TYPE_MISSED_CALL, channel)

        return event

    @classmethod
    def get_all(cls, org):
        return cls.objects.filter(org=org)

    def handle(self):
        """
        Handles takes care of any processing of this channel event that needs to take place, such as
        trigger any flows based on new conversations or referrals.
        """
        from temba.contacts.models import Contact
        from temba.triggers.models import Trigger
        handled = False

        if self.event_type == ChannelEvent.TYPE_NEW_CONVERSATION:
            handled = Trigger.catch_triggers(self, Trigger.TYPE_NEW_CONVERSATION, self.channel)

        elif self.event_type == ChannelEvent.TYPE_REFERRAL:
            handled = Trigger.catch_triggers(self, Trigger.TYPE_REFERRAL, self.channel,
                                             referrer_id=self.extra.get('referrer_id'), extra=self.extra)

        elif self.event_type == ChannelEvent.TYPE_FOLLOW:
            handled = Trigger.catch_triggers(self, Trigger.TYPE_FOLLOW, self.channel)

        elif self.event_type == ChannelEvent.TYPE_STOP_CONTACT:
            user = get_anonymous_user()
            contact, urn_obj = Contact.get_or_create(self.org, self.contact_urn.urn, self.channel)

            contact.stop(user)
            handled = True

        return handled

    def release(self):
        self.delete()


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

    connection = models.ForeignKey('channels.ChannelSession', related_name='channel_logs', null=True,
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
                                  connection_id=call.id,
                                  request=six.text_type(event.request_body),
                                  response=six.text_type(event.response_body),
                                  url=event.url,
                                  method=event.method,
                                  is_error=is_error,
                                  response_status=event.status_code,
                                  description=description[:255])

    @classmethod
    def log_channel_request(cls, channel_id, description, event, start, is_error=False):
        request_time = 0 if not start else time.time() - start
        request_time_ms = request_time * 1000

        ChannelLog.objects.create(channel_id=channel_id,
                                  request=six.text_type(event.request_body),
                                  response=six.text_type(event.response_body),
                                  url=event.url,
                                  method=event.method,
                                  is_error=is_error,
                                  response_status=event.status_code,
                                  description=description[:255],
                                  request_time=request_time_ms)

    def get_url_host(self):
        parsed = urlparse(self.url)
        return '%s://%s%s' % (parsed.scheme, parsed.hostname, parsed.path)

    def log_group(self):
        if self.msg:
            return ChannelLog.objects.filter(msg=self.msg).order_by('-created_on')

        return ChannelLog.objects.filter(id=self.id)

    def get_request_formatted(self):
        if not self.request:
            return "%s %s" % (self.method, self.url)

        try:
            return json.dumps(json.loads(self.request), indent=2)
        except Exception:
            return self.request

    def get_response_formatted(self):
        try:
            return json.dumps(json.loads(self.response), indent=2)
        except Exception:
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

        anon_user = get_anonymous_user()
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
        on_transaction_commit(lambda: send_alert_task.delay(self.id, resolved=False))

    def send_resolved(self):
        from .tasks import send_alert_task
        on_transaction_commit(lambda: send_alert_task.delay(self.id, resolved=True))

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
        if self.session_type == self.IVR:
            from temba.ivr.models import IVRCall
            return IVRCall.objects.filter(id=self.id).first()
        if self.session_type == self.USSD:
            from temba.ussd.models import USSDSession
            return USSDSession.objects.filter(id=self.id).first()
        return self  # pragma: no cover

    def get_session(self):
        """
        There is a one-to-one relationship between flow sessions and connections, but as connection can be null
        it can throw an exception
        """
        try:
            return self.session
        except ObjectDoesNotExist:
            return None
