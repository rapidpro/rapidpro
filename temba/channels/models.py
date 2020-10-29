import logging
import time
from abc import ABCMeta
from datetime import timedelta
from enum import Enum
from xml.sax.saxutils import escape

import phonenumbers
from django_countries.fields import CountryField
from phonenumbers import NumberParseException
from pyfcm import FCMNotification
from smartmin.models import SmartModel
from twilio.base.exceptions import TwilioRestException

from django.conf import settings
from django.conf.urls import url
from django.contrib.auth.models import Group, User
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import Count, Max, Q, Sum
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.template import Context, Engine, TemplateDoesNotExist
from django.utils import timezone
from django.utils.http import urlquote_plus
from django.utils.translation import ugettext_lazy as _

from temba import mailroom
from temba.orgs.models import Org
from temba.utils import analytics, get_anonymous_user, json, on_transaction_commit, redact
from temba.utils.email import send_template_email
from temba.utils.gsm7 import calculate_num_segments
from temba.utils.models import JSONAsTextField, SquashableModel, TembaModel, generate_uuid
from temba.utils.text import random_string

logger = logging.getLogger(__name__)


class ChannelType(metaclass=ABCMeta):
    """
    Base class for all dynamic channel types
    """

    class Category(Enum):
        PHONE = 1
        SOCIAL_MEDIA = 2
        API = 4

    class IVRProtocol(Enum):
        IVR_PROTOCOL_TWIML = 1
        IVR_PROTOCOL_NCCO = 2

    code = None
    slug = None
    category = None

    # the courier handling URL, will be wired automatically for use in templates, but wired to a null handler
    courier_url = None

    name = None
    icon = "icon-channel-external"
    schemes = None
    show_config_page = True

    available_timezones = None
    recommended_timezones = None

    claim_blurb = None
    claim_view = None
    claim_view_kwargs = None

    configuration_blurb = None
    configuration_urls = None
    show_public_addresses = False

    update_form = None

    max_length = -1
    max_tps = None
    attachment_support = False
    free_sending = False
    quick_reply_text_size = 20

    extra_links = None

    ivr_protocol = None

    # Whether this channel should be activated in the a celery task, useful to turn off if there's a chance for errors
    # during activation. Channels should make sure their claim view is non-atomic if a callback will be involved
    async_activation = True

    redact_request_keys = set()
    redact_response_keys = set()

    def is_available_to(self, user):
        """
        Determines whether this channel type is available to the given user, e.g. check timezone
        """
        if self.available_timezones is not None:
            timezone = user.get_org().timezone
            return timezone and str(timezone) in self.available_timezones
        else:
            return True

    def is_recommended_to(self, user):
        """
        Determines whether this channel type is recommended to the given user.
        """
        if self.recommended_timezones is not None:
            timezone = user.get_org().timezone
            return timezone and str(timezone) in self.recommended_timezones
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
        claim_view_kwargs = self.claim_view_kwargs if self.claim_view_kwargs else {}
        claim_view_kwargs["channel_type"] = self
        return url(r"^claim$", self.claim_view.as_view(**claim_view_kwargs), name="claim")

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

    def has_attachment_support(self, channel):
        """
        Whether the given channel instance supports message attachments
        """
        return self.attachment_support

    def get_configuration_context_dict(self, channel):
        return dict(channel=channel, ip_addresses=settings.IP_ADDRESSES)

    def get_configuration_template(self, channel):
        try:
            return (
                Engine.get_default()
                .get_template("channels/types/%s/config.html" % self.slug)
                .render(context=Context(self.get_configuration_context_dict(channel)))
            )
        except TemplateDoesNotExist:
            return ""

    def get_configuration_blurb(self, channel):
        """
        Allows ChannelTypes to define the blurb to show on the channel configuration page.
        """
        if self.__class__.configuration_blurb is not None:
            return (
                Engine.get_default()
                .from_string(str(self.configuration_blurb))
                .render(context=Context(dict(channel=channel)))
            )
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
                urls.append(
                    dict(
                        label=engine.from_string(url_config.get("label", "")).render(context=context),
                        url=engine.from_string(url_config.get("url", "")).render(context=context),
                        description=engine.from_string(url_config.get("description", "")).render(context=context),
                    )
                )

            return urls

        else:
            return ""

    def __str__(self):
        return self.name


def _get_default_channel_scheme():
    return ["tel"]


class UnsupportedAndroidChannelError(Exception):
    def __init__(self, message):
        self.message = message


class Channel(TembaModel):
    """
    Notes:
        - we want to reuse keys as much as possible (2018-10-11)
        - prefixed keys are legacy and should be avoided (2018-10-11)
    """

    # keys for various config options stored in the channel config dict
    CONFIG_BASE_URL = "base_url"
    CONFIG_SEND_URL = "send_url"

    CONFIG_USERNAME = "username"
    CONFIG_PASSWORD = "password"
    CONFIG_KEY = "key"
    CONFIG_API_ID = "api_id"
    CONFIG_API_KEY = "api_key"
    CONFIG_VERIFY_SSL = "verify_ssl"
    CONFIG_USE_NATIONAL = "use_national"
    CONFIG_ENCODING = "encoding"
    CONFIG_PAGE_NAME = "page_name"
    CONFIG_PLIVO_AUTH_ID = "PLIVO_AUTH_ID"
    CONFIG_PLIVO_AUTH_TOKEN = "PLIVO_AUTH_TOKEN"
    CONFIG_PLIVO_APP_ID = "PLIVO_APP_ID"
    CONFIG_AUTH_TOKEN = "auth_token"
    CONFIG_SECRET = "secret"
    CONFIG_CHANNEL_ID = "channel_id"
    CONFIG_CHANNEL_MID = "channel_mid"
    CONFIG_FCM_ID = "FCM_ID"
    CONFIG_MACROKIOSK_SENDER_ID = "macrokiosk_sender_id"
    CONFIG_MACROKIOSK_SERVICE_ID = "macrokiosk_service_id"
    CONFIG_RP_HOSTNAME_OVERRIDE = "rp_hostname_override"
    CONFIG_CALLBACK_DOMAIN = "callback_domain"
    CONFIG_ACCOUNT_SID = "account_sid"
    CONFIG_APPLICATION_SID = "application_sid"
    CONFIG_NUMBER_SID = "number_sid"
    CONFIG_MESSAGING_SERVICE_SID = "messaging_service_sid"
    CONFIG_MAX_CONCURRENT_EVENTS = "max_concurrent_events"
    CONFIG_ALLOW_INTERNATIONAL = "allow_international"

    CONFIG_NEXMO_API_KEY = "nexmo_api_key"
    CONFIG_NEXMO_API_SECRET = "nexmo_api_secret"
    CONFIG_NEXMO_APP_ID = "nexmo_app_id"
    CONFIG_NEXMO_APP_PRIVATE_KEY = "nexmo_app_private_key"

    CONFIG_SHORTCODE_MATCHING_PREFIXES = "matching_prefixes"

    ENCODING_DEFAULT = "D"  # we just pass the text down to the endpoint
    ENCODING_SMART = "S"  # we try simple substitutions to GSM7 then go to unicode if it still isn't GSM7
    ENCODING_UNICODE = "U"  # we send everything as unicode

    ENCODING_CHOICES = (
        (ENCODING_DEFAULT, _("Default Encoding")),
        (ENCODING_SMART, _("Smart Encoding")),
        (ENCODING_UNICODE, _("Unicode Encoding")),
    )

    # the role types for our channels
    ROLE_SEND = "S"
    ROLE_RECEIVE = "R"
    ROLE_CALL = "C"
    ROLE_ANSWER = "A"
    ROLE_USSD = "U"

    DEFAULT_ROLE = ROLE_SEND + ROLE_RECEIVE

    ROLE_CONFIG = {
        ROLE_SEND: "send",
        ROLE_RECEIVE: "receive",
        ROLE_CALL: "call",
        ROLE_ANSWER: "answer",
        ROLE_USSD: "ussd",
    }

    CONTENT_TYPE_URLENCODED = "urlencoded"
    CONTENT_TYPE_JSON = "json"
    CONTENT_TYPE_XML = "xml"

    CONTENT_TYPES = {
        CONTENT_TYPE_URLENCODED: "application/x-www-form-urlencoded",
        CONTENT_TYPE_JSON: "application/json",
        CONTENT_TYPE_XML: "text/xml; charset=utf-8",
    }

    CONTENT_TYPE_CHOICES = (
        (CONTENT_TYPE_URLENCODED, _("URL Encoded - application/x-www-form-urlencoded")),
        (CONTENT_TYPE_JSON, _("JSON - application/json")),
        (CONTENT_TYPE_XML, _("XML - text/xml; charset=utf-8")),
    )

    SIMULATOR_CHANNEL = {
        "uuid": "440099cf-200c-4d45-a8e7-4a564f4a0e8b",
        "name": "Simulator Channel",
        "address": "+18005551212",
        "schemes": ["tel"],
        "roles": ["send"],
    }

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="channels", null=True)

    channel_type = models.CharField(max_length=3)

    name = models.CharField(
        verbose_name=_("Name"), max_length=64, blank=True, null=True, help_text=_("Descriptive label for this channel")
    )

    address = models.CharField(
        verbose_name=_("Address"),
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Address with which this channel communicates"),
    )

    country = CountryField(
        verbose_name=_("Country"), null=True, blank=True, help_text=_("Country which this channel is for")
    )

    claim_code = models.CharField(
        verbose_name=_("Claim Code"),
        max_length=16,
        blank=True,
        null=True,
        unique=True,
        help_text=_("The token the user will us to claim this channel"),
    )

    secret = models.CharField(
        verbose_name=_("Secret"),
        max_length=64,
        blank=True,
        null=True,
        unique=True,
        help_text=_("The secret token this channel should use when signing requests"),
    )

    last_seen = models.DateTimeField(
        verbose_name=_("Last Seen"), auto_now_add=True, help_text=_("The last time this channel contacted the server")
    )

    device = models.CharField(
        verbose_name=_("Device"),
        max_length=255,
        null=True,
        blank=True,
        help_text=_("The type of Android device this channel is running on"),
    )

    os = models.CharField(
        verbose_name=_("OS"),
        max_length=255,
        null=True,
        blank=True,
        help_text=_("What Android OS version this channel is running on"),
    )

    alert_email = models.EmailField(
        verbose_name=_("Alert Email"),
        null=True,
        blank=True,
        help_text=_("We will send email alerts to this address if experiencing issues sending"),
    )

    config = JSONAsTextField(null=True, default=dict)

    schemes = ArrayField(models.CharField(max_length=16), default=_get_default_channel_scheme)

    role = models.CharField(max_length=4, default=DEFAULT_ROLE)

    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True)

    bod = models.TextField(null=True)

    tps = models.IntegerField(
        verbose_name=_("Maximum Transactions per Second"),
        null=True,
        help_text=_("The max number of messages that will be sent per second"),
    )

    @classmethod
    def create(
        cls,
        org,
        user,
        country,
        channel_type,
        name=None,
        address=None,
        config=None,
        role=DEFAULT_ROLE,
        schemes=None,
        **kwargs,
    ):
        if isinstance(channel_type, str):
            channel_type = cls.get_type_from_code(channel_type)

        if schemes:
            if channel_type.schemes and not set(channel_type.schemes).intersection(schemes):
                raise ValueError("Channel type '%s' cannot support schemes %s" % (channel_type, schemes))
        else:
            schemes = channel_type.schemes

        if not schemes:
            raise ValueError("Cannot create channel without schemes")

        if country and schemes[0] not in ["tel", "whatsapp"]:
            raise ValueError("Only channels handling phone numbers can be country specific")

        if config is None:
            config = {}

        create_args = dict(
            org=org,
            country=country,
            channel_type=channel_type.code,
            name=name,
            address=address,
            config=config,
            role=role,
            schemes=schemes,
            created_by=user,
            modified_by=user,
        )
        create_args.update(kwargs)

        if "uuid" not in create_args:
            create_args["uuid"] = generate_uuid()

        channel = cls.objects.create(**create_args)

        # normalize any telephone numbers that we may now have a clue as to country
        if org and country:
            org.normalize_contact_tels()

        # track our creation
        analytics.track(user.username, "temba.channel_created", dict(channel_type=channel_type.code))

        if settings.IS_PROD:
            if channel_type.async_activation:
                on_transaction_commit(lambda: channel_type.activate(channel))
            else:
                try:
                    channel_type.activate(channel)

                except Exception as e:
                    # release our channel, raise error upwards
                    channel.release()
                    raise e

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

        return TYPES.values()

    def get_type(self):
        return self.get_type_from_code(self.channel_type)

    @classmethod
    def add_authenticated_external_channel(
        cls,
        org,
        user,
        country,
        phone_number,
        username,
        password,
        channel_type,
        url,
        role=DEFAULT_ROLE,
        extra_config=None,
    ):

        try:
            parsed = phonenumbers.parse(phone_number, None)
            phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        except Exception:
            # this is a shortcode, just use it plain
            phone = phone_number

        config = dict(username=username, password=password, send_url=url)
        if extra_config:
            config.update(extra_config)

        return Channel.create(
            org, user, country, channel_type, name=phone, address=phone_number, config=config, role=role
        )

    @classmethod
    def add_config_external_channel(
        cls,
        org,
        user,
        country,
        address,
        channel_type,
        config,
        role=DEFAULT_ROLE,
        schemes=("tel",),
        parent=None,
        name=None,
    ):
        return Channel.create(
            org,
            user,
            country,
            channel_type,
            name=name or address,
            address=address,
            config=config,
            role=role,
            schemes=schemes,
            parent=parent,
        )

    @classmethod
    def add_nexmo_bulk_sender(cls, user, channel):
        # nexmo ships numbers around as E164 without the leading +
        parsed = phonenumbers.parse(channel.address, None)
        nexmo_phone_number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164).strip("+")

        org = user.get_org()
        config = {
            Channel.CONFIG_NEXMO_API_KEY: org.config[Org.CONFIG_NEXMO_KEY],
            Channel.CONFIG_NEXMO_API_SECRET: org.config[Org.CONFIG_NEXMO_SECRET],
            Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain(),
        }

        return Channel.create(
            user.get_org(),
            user,
            channel.country,
            "NX",
            name="Nexmo Sender",
            config=config,
            tps=1,
            address=channel.address,
            role=Channel.ROLE_SEND,
            parent=channel,
            bod=nexmo_phone_number,
        )

    @classmethod
    def add_call_channel(cls, org, user, channel):
        return Channel.create(
            org,
            user,
            channel.country,
            "T",
            name="Twilio Caller",
            address=channel.address,
            role=Channel.ROLE_CALL,
            parent=channel,
            config={
                "account_sid": org.config[Org.CONFIG_TWILIO_SID],
                "auth_token": org.config[Org.CONFIG_TWILIO_TOKEN],
            },
        )

    @classmethod
    def get_or_create_android(cls, registration_data, status):
        """
        Creates a new Android channel from the fcm and status commands sent during device registration
        """
        fcm_id = registration_data.get("fcm_id")
        uuid = registration_data.get("uuid")
        country = status.get("cc")
        device = status.get("dev")

        if not fcm_id or not uuid:
            gcm_id = registration_data.get("gcm_id")
            if gcm_id:
                raise UnsupportedAndroidChannelError("Unsupported Android client app.")
            else:
                raise ValueError("Can't create Android channel without UUID or FCM ID")

        # look for existing active channel with this UUID
        existing = Channel.objects.filter(uuid=uuid, is_active=True).first()

        # if device exists reset some of the settings (ok because device clearly isn't in use if it's registering)
        if existing:
            config = existing.config
            config.update({Channel.CONFIG_FCM_ID: fcm_id})
            existing.config = config
            existing.claim_code = cls.generate_claim_code()
            existing.secret = cls.generate_secret()
            existing.country = country
            existing.device = device
            existing.save(update_fields=("config", "secret", "claim_code", "country", "device"))

            return existing

        # if any inactive channel has this UUID, we can steal it
        for ch in Channel.objects.filter(uuid=uuid, is_active=False):
            ch.uuid = generate_uuid()
            ch.save(update_fields=("uuid",))

        # generate random secret and claim code
        claim_code = cls.generate_claim_code()
        secret = cls.generate_secret()
        anon = get_anonymous_user()
        config = {Channel.CONFIG_FCM_ID: fcm_id}

        return Channel.create(
            None,
            anon,
            country,
            cls.get_type_from_code("A"),
            None,
            None,
            config=config,
            uuid=uuid,
            device=device,
            claim_code=claim_code,
            secret=secret,
        )

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

    def is_android(self):
        """
        Is this an Android channel
        """
        from .types.android.type import AndroidType

        return self.channel_type == AndroidType.code

    def get_delegate_channels(self):
        # detached channels can't have delegates
        if not self.org:  # pragma: no cover
            return Channel.objects.none()

        return self.org.channels.filter(parent=self, is_active=True, org=self.org).order_by("-role")

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
        callback_domain = self.config.get(Channel.CONFIG_CALLBACK_DOMAIN)

        if callback_domain:
            return callback_domain
        else:
            return self.org.get_brand_domain()

    def is_delegate_sender(self):
        return self.parent and Channel.ROLE_SEND in self.role

    def is_delegate_caller(self):
        return self.parent and Channel.ROLE_CALL in self.role

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
        if self.is_android():
            return _("Android Phone")
        else:
            return _("%s Channel" % self.get_channel_type_display())

    def get_address_display(self, e164=False):
        from temba.contacts.models import URN

        if not self.address:
            return ""

        if self.address and URN.TEL_SCHEME in self.schemes and self.country:
            # assume that a number not starting with + is a short code and return as is
            if self.address[0] != "+":
                return self.address

            try:
                normalized = phonenumbers.parse(self.address, str(self.country))
                fmt = phonenumbers.PhoneNumberFormat.E164 if e164 else phonenumbers.PhoneNumberFormat.INTERNATIONAL
                return phonenumbers.format_number(normalized, fmt)
            except NumberParseException:  # pragma: needs cover
                # the number may be alphanumeric in the case of short codes
                pass

        elif URN.TWITTER_SCHEME in self.schemes:
            return "@%s" % self.address

        elif URN.FACEBOOK_SCHEME in self.schemes:
            return "%s (%s)" % (self.config.get(Channel.CONFIG_PAGE_NAME, self.name), self.address)

        return self.address

    def build_registration_command(self):
        # create a claim code if we don't have one
        if not self.claim_code:
            self.claim_code = self.generate_claim_code()
            self.save(update_fields=("claim_code",))

        # create a secret if we don't have one
        if not self.secret:
            self.secret = self.generate_secret()
            self.save(update_fields=("secret",))

        # return our command
        return dict(cmd="reg", relayer_claim_code=self.claim_code, relayer_secret=self.secret, relayer_id=self.id)

    def get_last_sent_message(self):
        from temba.msgs.models import SENT, DELIVERED, OUTGOING

        # find last successfully sent message
        return (
            self.msgs.filter(status__in=[SENT, DELIVERED], direction=OUTGOING)
            .exclude(sent_on=None)
            .order_by("-sent_on")
            .first()
        )

    def get_delayed_outgoing_messages(self):
        from temba.msgs.models import Msg

        one_hour_ago = timezone.now() - timedelta(hours=1)
        latest_sent_message = self.get_last_sent_message()

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
        return self.sync_events.filter(created_on__gt=timezone.now() - timedelta(hours=1)).order_by("-created_on")

    def get_last_sync(self):
        if not hasattr(self, "_last_sync"):
            last_sync = self.sync_events.order_by("-created_on").first()

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

        return Msg.objects.filter(org=self.org.id, status__in=["P", "Q"], direction="O", visibility="V", channel=self)

    def is_new(self):
        # is this channel newer than an hour
        return self.created_on > timezone.now() - timedelta(hours=1) or not self.get_last_sync()

    def calculate_tps_cost(self, msg):
        """
        Calculates the TPS cost for sending the passed in message. We look at the URN type and for any
        `tel` URNs we just use the calculated segments here. All others have a cost of 1.

        In the case of attachments, our cost is the number of attachments.
        """
        from temba.contacts.models import URN

        cost = 1
        if msg.contact_urn.scheme == URN.TEL_SCHEME:
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
        Releases this channel making it inactive
        """
        dependent_flows_count = self.dependent_flows.count()
        if dependent_flows_count > 0:
            raise ValueError(f"Cannot delete Channel: {self.get_name()}, used by {dependent_flows_count} flows")

        channel_type = self.get_type()

        # release any channels working on our behalf as well
        for delegate_channel in Channel.objects.filter(parent=self, org=self.org):
            delegate_channel.release()

        # unassociate them
        Channel.objects.filter(parent=self).update(parent=None)

        # release any alerts we sent
        for alert in self.alerts.all():
            alert.release()

        # any related sync events
        for sync_event in self.sync_events.all():
            sync_event.release()

        # only call out to external aggregator services if we are on prod servers
        if settings.IS_PROD:
            try:
                # if channel is a new style type, deactivate it
                channel_type.deactivate(self)

            except TwilioRestException as e:
                raise e

            except Exception as e:  # pragma: no cover
                # proceed with removing this channel but log the problem
                logger.error(f"Unable to deactivate a channel: {str(e)}", exc_info=True)

        # interrupt any sessions using this channel as a connection
        mailroom.queue_interrupt(self.org, channel=self)

        # save off our org and fcm id before nullifying
        org = self.org
        registration_id = self.config.get(Channel.CONFIG_FCM_ID)

        # make the channel inactive
        self.config.pop(Channel.CONFIG_FCM_ID, None)
        self.is_active = False
        self.save(update_fields=["is_active", "config", "modified_on"])

        # mark any messages in sending mode as failed for this channel
        from temba.msgs.models import Msg, OUTGOING, PENDING, QUEUED, ERRORED, FAILED

        Msg.objects.filter(channel=self, direction=OUTGOING, status__in=[QUEUED, PENDING, ERRORED]).update(
            status=FAILED
        )

        # trigger the orphaned channel
        if trigger_sync and self.is_android() and registration_id:
            self.trigger_sync(registration_id)

        from temba.triggers.models import Trigger

        Trigger.objects.filter(channel=self, org=org).update(is_active=False)

        # and any triggers associated with our channel get archived
        for trigger in Trigger.objects.filter(org=self.org, channel=self).all():
            trigger.channel = None
            trigger.save(update_fields=("channel",))
            trigger.archive(self.modified_by)

    def trigger_sync(self, registration_id=None):  # pragma: no cover
        """
        Sends a FCM command to trigger a sync on the client
        """
        # androids sync via FCM
        if self.is_android():
            fcm_id = self.config.get(Channel.CONFIG_FCM_ID)

            if fcm_id is not None:
                if getattr(settings, "FCM_API_KEY", None):
                    from .tasks import sync_channel_fcm_task

                    if not registration_id:
                        registration_id = fcm_id
                    if registration_id:
                        on_transaction_commit(lambda: sync_channel_fcm_task.delay(registration_id, channel_id=self.pk))

        # otherwise this is an aggregator, no-op
        else:
            raise Exception("Trigger sync called on non Android channel. [%d]" % self.pk)

    @classmethod
    def sync_channel_fcm(cls, registration_id, channel=None):  # pragma: no cover
        push_service = FCMNotification(api_key=settings.FCM_API_KEY)
        fcm_failed = False
        try:
            result = push_service.notify_single_device(registration_id=registration_id, data_message=dict(msg="sync"))
            if not result.get("success", 0):
                fcm_failed = True
        except Exception:
            fcm_failed = True

        if fcm_failed:
            valid_registration_ids = push_service.clean_registration_ids([registration_id])
            if registration_id not in valid_registration_ids:
                # this fcm id is invalid now, clear it out
                channel.config.pop(Channel.CONFIG_FCM_ID, None)
                channel.save(update_fields=["config"])

    @classmethod
    def replace_variables(cls, text, variables, content_type=CONTENT_TYPE_URLENCODED):
        for key in variables.keys():
            replacement = str(variables[key])

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
    def get_pending_messages(cls, org):
        """
        We want all messages that are:
            1. Pending, ie, never queued
            2. Queued over twelve hours ago (something went awry and we need to re-queue)
            3. Errored and are ready for a retry
        """

        from temba.channels.types.android import AndroidType
        from temba.msgs.models import Msg, PENDING, QUEUED, ERRORED, OUTGOING

        now = timezone.now()
        hours_ago = now - timedelta(hours=12)
        five_minutes_ago = now - timedelta(minutes=5)

        return (
            Msg.objects.filter(org=org, direction=OUTGOING)
            .filter(
                Q(status=PENDING, created_on__lte=five_minutes_ago)
                | Q(status=QUEUED, queued_on__lte=hours_ago)
                | Q(status=ERRORED, next_attempt__lte=now)
            )
            .exclude(channel__channel_type=AndroidType.code)
            .order_by("created_on")
        )

    def get_count(self, count_types):
        count = (
            ChannelCount.objects.filter(channel=self, count_type__in=count_types)
            .aggregate(Sum("count"))
            .get("count__sum", 0)
        )

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
        return (
            ChannelLog.objects.filter(channel=self)
            .exclude(connection=None)
            .order_by("connection")
            .distinct("connection")
            .count()
        )

    def get_non_ivr_log_count(self):
        return self.get_log_count() - self.get_ivr_log_count()

    def __str__(self):  # pragma: no cover
        if self.name:
            return self.name
        elif self.device:
            return self.device
        elif self.address:
            return self.address
        else:
            return str(self.id)

    class Meta:
        ordering = ("-last_seen", "-pk")


class ChannelCount(SquashableModel):
    """
    This model is maintained by Postgres triggers and maintains the daily counts of messages and ivr interactions
    on each day. This allows for fast visualizations of activity on the channel read page as well as summaries
    of message usage over the course of time.
    """

    SQUASH_OVER = ("channel_id", "count_type", "day")

    INCOMING_MSG_TYPE = "IM"  # Incoming message
    OUTGOING_MSG_TYPE = "OM"  # Outgoing message
    INCOMING_IVR_TYPE = "IV"  # Incoming IVR step
    OUTGOING_IVR_TYPE = "OV"  # Outgoing IVR step
    SUCCESS_LOG_TYPE = "LS"  # ChannelLog record
    ERROR_LOG_TYPE = "LE"  # ChannelLog record that is an error

    COUNT_TYPE_CHOICES = (
        (INCOMING_MSG_TYPE, _("Incoming Message")),
        (OUTGOING_MSG_TYPE, _("Outgoing Message")),
        (INCOMING_IVR_TYPE, _("Incoming Voice")),
        (OUTGOING_IVR_TYPE, _("Outgoing Voice")),
        (SUCCESS_LOG_TYPE, _("Success Log Record")),
        (ERROR_LOG_TYPE, _("Error Log Record")),
    )

    channel = models.ForeignKey(
        Channel,
        on_delete=models.PROTECT,
        related_name="counts",
        help_text=_("The channel this is a daily summary count for"),
    )
    count_type = models.CharField(
        choices=COUNT_TYPE_CHOICES, max_length=2, help_text=_("What type of message this row is counting")
    )
    day = models.DateField(null=True, help_text=_("The day this count is for"))
    count = models.IntegerField(default=0, help_text=_("The count of messages on this day and type"))

    @classmethod
    def get_day_count(cls, channel, count_type, day):
        count = ChannelCount.objects.filter(channel=channel, count_type=count_type, day=day)
        count = count.order_by("day", "count_type").aggregate(count_sum=Sum("count"))

        return count["count_sum"] if count["count_sum"] is not None else 0

    @classmethod
    def get_squash_query(cls, distinct_set):
        if distinct_set.day:
            sql = """
            WITH removed as (
                DELETE FROM %(table)s WHERE "channel_id" = %%s AND "count_type" = %%s AND "day" = %%s RETURNING "count"
            )
            INSERT INTO %(table)s("channel_id", "count_type", "day", "count", "is_squashed")
            VALUES (%%s, %%s, %%s, GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
            """ % {
                "table": cls._meta.db_table
            }

            params = (distinct_set.channel_id, distinct_set.count_type, distinct_set.day) * 2
        else:
            sql = """
            WITH removed as (
                DELETE FROM %(table)s WHERE "channel_id" = %%s AND "count_type" = %%s AND "day" IS NULL RETURNING "count"
            )
            INSERT INTO %(table)s("channel_id", "count_type", "day", "count", "is_squashed")
            VALUES (%%s, %%s, NULL, GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
            """ % {
                "table": cls._meta.db_table
            }

            params = (distinct_set.channel_id, distinct_set.count_type) * 2

        return sql, params

    def __str__(self):  # pragma: no cover
        return "ChannelCount(%d) %s %s count: %d" % (self.channel_id, self.count_type, self.day, self.count)

    class Meta:
        index_together = ["channel", "count_type", "day"]


class ChannelEvent(models.Model):
    """
    An event other than a message that occurs between a channel and a contact. Can be used to trigger flows etc.
    """

    TYPE_UNKNOWN = "unknown"
    TYPE_CALL_OUT = "mt_call"
    TYPE_CALL_OUT_MISSED = "mt_miss"
    TYPE_CALL_IN = "mo_call"
    TYPE_CALL_IN_MISSED = "mo_miss"
    TYPE_NEW_CONVERSATION = "new_conversation"
    TYPE_REFERRAL = "referral"
    TYPE_STOP_CONTACT = "stop_contact"
    TYPE_WELCOME_MESSAGE = "welcome_message"

    # single char flag, human readable name, API readable name
    TYPE_CONFIG = (
        (TYPE_UNKNOWN, _("Unknown Call Type"), "unknown"),
        (TYPE_CALL_OUT, _("Outgoing Call"), "call-out"),
        (TYPE_CALL_OUT_MISSED, _("Missed Outgoing Call"), "call-out-missed"),
        (TYPE_CALL_IN, _("Incoming Call"), "call-in"),
        (TYPE_CALL_IN_MISSED, _("Missed Incoming Call"), "call-in-missed"),
        (TYPE_STOP_CONTACT, _("Stop Contact"), "stop-contact"),
        (TYPE_NEW_CONVERSATION, _("New Conversation"), "new-conversation"),
        (TYPE_REFERRAL, _("Referral"), "referral"),
        (TYPE_WELCOME_MESSAGE, _("Welcome Message"), "welcome-message"),
    )

    TYPE_CHOICES = [(t[0], t[1]) for t in TYPE_CONFIG]

    CALL_TYPES = {TYPE_CALL_OUT, TYPE_CALL_OUT_MISSED, TYPE_CALL_IN, TYPE_CALL_IN_MISSED}

    org = models.ForeignKey(Org, on_delete=models.PROTECT)
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT)
    event_type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    contact = models.ForeignKey("contacts.Contact", on_delete=models.PROTECT, related_name="channel_events")
    contact_urn = models.ForeignKey(
        "contacts.ContactURN", on_delete=models.PROTECT, null=True, related_name="channel_events"
    )
    extra = JSONAsTextField(null=True, default=dict)
    occurred_on = models.DateTimeField()
    created_on = models.DateTimeField(default=timezone.now)

    @classmethod
    def create_relayer_event(cls, channel, urn, event_type, occurred_on, extra=None):
        from temba.contacts.models import Contact

        contact, contact_urn = Contact.resolve(channel, urn)

        event = cls.objects.create(
            org=channel.org,
            channel=channel,
            contact=contact,
            contact_urn=contact_urn,
            occurred_on=occurred_on,
            event_type=event_type,
            extra=extra,
        )

        if event_type == cls.TYPE_CALL_IN_MISSED:
            # pass off handling of the message to mailroom after we commit
            on_transaction_commit(lambda: mailroom.queue_mo_miss_event(event))

        return event

    @classmethod
    def get_all(cls, org):
        return cls.objects.filter(org=org)

    def release(self):
        self.delete()


class ChannelLog(models.Model):
    """
    A log of an call made to or from a channel
    """

    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, related_name="logs")
    msg = models.ForeignKey("msgs.Msg", on_delete=models.PROTECT, related_name="channel_logs", null=True)
    connection = models.ForeignKey(
        "channels.ChannelConnection", on_delete=models.PROTECT, related_name="channel_logs", null=True
    )
    description = models.CharField(max_length=255)
    is_error = models.BooleanField(default=False)
    url = models.TextField(null=True)
    method = models.CharField(max_length=16, null=True)
    request = models.TextField(null=True)
    response = models.TextField(null=True)
    response_status = models.IntegerField(null=True)
    created_on = models.DateTimeField(auto_now_add=True)
    request_time = models.IntegerField(null=True)

    @classmethod
    def log_error(cls, msg, description):
        print("[%d] ERROR - %s" % (msg.id, description))
        return ChannelLog.objects.create(
            channel_id=msg.channel, msg_id=msg.id, is_error=True, description=description[:255]
        )

    @classmethod
    def log_channel_request(cls, channel_id, description, event, start, is_error=False):
        request_time = 0 if not start else time.time() - start
        request_time_ms = request_time * 1000

        return ChannelLog.objects.create(
            channel_id=channel_id,
            request=str(event.request_body),
            response=str(event.response_body),
            url=event.url,
            method=event.method,
            is_error=is_error,
            response_status=event.status_code,
            description=description[:255],
            request_time=request_time_ms,
        )

    def log_group(self):
        if self.msg:
            return ChannelLog.objects.filter(msg=self.msg).order_by("-created_on")

        return ChannelLog.objects.filter(id=self.id)

    def get_url_display(self, user, anon_mask):
        """
        Gets the URL as it should be displayed to the given user
        """
        return self._get_display_value(user, self.url, anon_mask)

    def get_request_display(self, user, anon_mask):
        """
        Gets the request trace as it should be displayed to the given user
        """
        redact_keys = Channel.get_type_from_code(self.channel.channel_type).redact_request_keys

        return self._get_display_value(user, self.request, anon_mask, redact_keys)

    def get_response_display(self, user, anon_mask):
        """
        Gets the response trace as it should be displayed to the given user
        """
        redact_keys = Channel.get_type_from_code(self.channel.channel_type).redact_response_keys

        return self._get_display_value(user, self.response, anon_mask, redact_keys)

    def _get_display_value(self, user, original, mask, redact_keys=()):
        """
        Get a part of the log which may or may not have to be redacted to hide sensitive information in anon orgs
        """

        if not self.channel.org.is_anon or user.has_org_perm(self.channel.org, "contacts.contact_break_anon"):
            return original

        # if this log doesn't have a msg then we don't know what to redact, so redact completely
        if not self.msg_id:
            return mask

        needle = self.msg.contact_urn.path

        if redact_keys:
            redacted = redact.http_trace(original, needle, mask, redact_keys)
        else:
            redacted = redact.text(original, needle, mask)

        # if nothing was redacted, don't risk returning sensitive information we didn't find
        if original == redacted:
            return mask

        return redacted

    def release(self):
        self.delete()


class SyncEvent(SmartModel):
    """
    A record of a sync from an Android channel
    """

    SOURCE_AC = "AC"
    SOURCE_USB = "USB"
    SOURCE_WIRELESS = "WIR"
    SOURCE_BATTERY = "BAT"
    SOURCE_CHOICES = (
        (SOURCE_AC, "A/C"),
        (SOURCE_USB, "USB"),
        (SOURCE_WIRELESS, "Wireless"),
        (SOURCE_BATTERY, "Battery"),
    )

    STATUS_UNKNOWN = "UNK"
    STATUS_CHARGING = "CHA"
    STATUS_DISCHARGING = "DIS"
    STATUS_NOT_CHARGING = "NOT"
    STATUS_FULL = "FUL"
    STATUS_CHOICES = (
        (STATUS_UNKNOWN, "Unknown"),
        (STATUS_CHARGING, "Charging"),
        (STATUS_DISCHARGING, "Discharging"),
        (STATUS_NOT_CHARGING, "Not Charging"),
        (STATUS_FULL, "FUL"),
    )

    channel = models.ForeignKey(Channel, related_name="sync_events", on_delete=models.PROTECT)

    # power status of the device
    power_source = models.CharField(max_length=64, choices=SOURCE_CHOICES)
    power_status = models.CharField(max_length=64, choices=STATUS_CHOICES, default=STATUS_UNKNOWN)
    power_level = models.IntegerField()

    network_type = models.CharField(max_length=128)
    lifetime = models.IntegerField(null=True, blank=True, default=0)

    # counts of what was synced
    pending_message_count = models.IntegerField(default=0)
    retry_message_count = models.IntegerField(default=0)
    incoming_command_count = models.IntegerField(default=0)
    outgoing_command_count = models.IntegerField(default=0)

    @classmethod
    def create(cls, channel, cmd, incoming_commands):
        # update country, device and OS on our channel
        device = cmd.get("dev", None)
        os = cmd.get("os", None)

        # update our channel if anything is new
        if channel.device != device or channel.os != os:  # pragma: no cover
            channel.device = device
            channel.os = os
            channel.save(update_fields=["device", "os"])

        args = dict()

        args["power_source"] = cmd.get("p_src", cmd.get("power_source"))
        args["power_status"] = cmd.get("p_sts", cmd.get("power_status"))
        args["power_level"] = cmd.get("p_lvl", cmd.get("power_level"))

        args["network_type"] = cmd.get("net", cmd.get("network_type"))

        args["pending_message_count"] = len(cmd.get("pending", cmd.get("pending_messages")))
        args["retry_message_count"] = len(cmd.get("retry", cmd.get("retry_messages")))
        args["incoming_command_count"] = max(len(incoming_commands) - 2, 0)

        anon_user = get_anonymous_user()
        args["channel"] = channel
        args["created_by"] = anon_user
        args["modified_by"] = anon_user

        sync_event = SyncEvent.objects.create(**args)
        sync_event.pending_messages = cmd.get("pending", cmd.get("pending_messages"))
        sync_event.retry_messages = cmd.get("retry", cmd.get("retry_messages"))

        return sync_event

    def release(self):
        for alert in self.alerts.all():
            alert.release()
        self.delete()

    def get_pending_messages(self):
        return getattr(self, "pending_messages", [])

    def get_retry_messages(self):
        return getattr(self, "retry_messages", [])

    @classmethod
    def trim(cls):
        week_ago = timezone.now() - timedelta(days=7)

        channels_with_sync_events = (
            SyncEvent.objects.filter(created_on__lte=week_ago)
            .values("channel")
            .annotate(Count("id"))
            .filter(id__count__gt=1)
        )
        for channel_sync_events in channels_with_sync_events:
            sync_events = SyncEvent.objects.filter(
                created_on__lte=week_ago, channel_id=channel_sync_events["channel"]
            ).order_by("-created_on")[1:]
            for event in sync_events:
                event.release()


@receiver(pre_save, sender=SyncEvent)
def pre_save(sender, instance, **kwargs):
    if kwargs["raw"]:  # pragma: no cover
        return

    if not instance.pk:
        last_sync_event = SyncEvent.objects.filter(channel=instance.channel).order_by("-created_on").first()
        if last_sync_event:
            td = timezone.now() - last_sync_event.created_on
            last_sync_event.lifetime = td.seconds + td.days * 24 * 3600
            last_sync_event.save()


class Alert(SmartModel):
    TYPE_DISCONNECTED = "D"
    TYPE_POWER = "P"
    TYPE_SMS = "S"

    TYPE_CHOICES = (
        (TYPE_POWER, _("Power")),  # channel has low power
        (TYPE_DISCONNECTED, _("Disconnected")),  # channel hasn't synced in a while
        (TYPE_SMS, _("SMS")),
    )  # channel has many unsent messages

    channel = models.ForeignKey(
        Channel,
        related_name="alerts",
        on_delete=models.PROTECT,
        verbose_name=_("Channel"),
        help_text=_("The channel that this alert is for"),
    )
    sync_event = models.ForeignKey(
        SyncEvent,
        related_name="alerts",
        on_delete=models.PROTECT,
        verbose_name=_("Sync Event"),
        null=True,
        help_text=_("The sync event that caused this alert to be sent (if any)"),
    )
    alert_type = models.CharField(
        verbose_name=_("Alert Type"),
        max_length=1,
        choices=TYPE_CHOICES,
        help_text=_("The type of alert the channel is sending"),
    )
    ended_on = models.DateTimeField(verbose_name=_("Ended On"), blank=True, null=True)

    @classmethod
    def check_power_alert(cls, sync):
        alert_user = get_alert_user()

        if (
            sync.power_status
            in (SyncEvent.STATUS_DISCHARGING, SyncEvent.STATUS_UNKNOWN, SyncEvent.STATUS_NOT_CHARGING)
            and int(sync.power_level) < 25
        ):

            alerts = Alert.objects.filter(sync_event__channel=sync.channel, alert_type=cls.TYPE_POWER, ended_on=None)

            if not alerts:
                new_alert = Alert.objects.create(
                    channel=sync.channel,
                    sync_event=sync,
                    alert_type=cls.TYPE_POWER,
                    created_by=alert_user,
                    modified_by=alert_user,
                )
                new_alert.send_alert()

        if sync.power_status == SyncEvent.STATUS_CHARGING or sync.power_status == SyncEvent.STATUS_FULL:
            alerts = Alert.objects.filter(sync_event__channel=sync.channel, alert_type=cls.TYPE_POWER, ended_on=None)
            alerts = alerts.order_by("-created_on")

            # end our previous alert
            if alerts and int(alerts[0].sync_event.power_level) < 25:
                for alert in alerts:
                    alert.ended_on = timezone.now()
                    alert.save()
                    last_alert = alert
                last_alert.send_resolved()

    @classmethod
    def check_alerts(cls):
        from temba.channels.types.android import AndroidType
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

        for channel in (
            Channel.objects.filter(channel_type=AndroidType.code, is_active=True)
            .exclude(org=None)
            .exclude(last_seen__gte=thirty_minutes_ago)
        ):
            # have we already sent an alert for this channel
            if not Alert.objects.filter(channel=channel, alert_type=cls.TYPE_DISCONNECTED, ended_on=None):
                alert = Alert.objects.create(
                    channel=channel, alert_type=cls.TYPE_DISCONNECTED, modified_by=alert_user, created_by=alert_user
                )
                alert.send_alert()

        day_ago = timezone.now() - timedelta(days=1)
        six_hours_ago = timezone.now() - timedelta(hours=6)

        # end any sms alerts that are open and no longer seem valid
        for alert in Alert.objects.filter(alert_type=cls.TYPE_SMS, ended_on=None).distinct("channel_id"):
            # are there still queued messages?

            if (
                not Msg.objects.filter(
                    status__in=["Q", "P"], channel_id=alert.channel_id, created_on__lte=thirty_minutes_ago
                )
                .exclude(created_on__lte=day_ago)
                .exists()
            ):
                Alert.objects.filter(alert_type=cls.TYPE_SMS, ended_on=None, channel_id=alert.channel_id).update(
                    ended_on=timezone.now()
                )

        # now look for channels that have many unsent messages
        queued_messages = (
            Msg.objects.filter(status__in=["Q", "P"])
            .order_by("channel", "created_on")
            .exclude(created_on__gte=thirty_minutes_ago)
            .exclude(created_on__lte=day_ago)
            .exclude(channel=None)
            .values("channel")
            .annotate(latest_queued=Max("created_on"))
        )
        sent_messages = (
            Msg.objects.filter(status__in=["S", "D"])
            .exclude(created_on__lte=day_ago)
            .exclude(channel=None)
            .order_by("channel", "sent_on")
            .values("channel")
            .annotate(latest_sent=Max("sent_on"))
        )

        channels = dict()
        for queued in queued_messages:
            if queued["channel"]:
                channels[queued["channel"]] = dict(queued=queued["latest_queued"], sent=None)

        for sent in sent_messages:
            existing = channels.get(sent["channel"], dict(queued=None))
            existing["sent"] = sent["latest_sent"]

        for (channel_id, value) in channels.items():
            # we haven't sent any messages in the past six hours
            if not value["sent"] or value["sent"] < six_hours_ago:
                channel = Channel.objects.get(pk=channel_id)

                # never alert on channels that have no org
                if channel.org is None:  # pragma: no cover
                    continue

                # if we haven't sent an alert in the past six ours
                if not Alert.objects.filter(channel=channel).filter(Q(created_on__gt=six_hours_ago)).exists():
                    alert = Alert.objects.create(
                        channel=channel, alert_type=cls.TYPE_SMS, modified_by=alert_user, created_by=alert_user
                    )
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
                template = "channels/email/power_charging_alert"
            else:
                subject = "Your Android phone battery is low"
                template = "channels/email/power_alert"

        elif self.alert_type == self.TYPE_DISCONNECTED:
            if resolved:
                subject = "Your Android phone is now connected"
                template = "channels/email/connected_alert"
            else:
                subject = "Your Android phone is disconnected"
                template = "channels/email/disconnected_alert"

        elif self.alert_type == self.TYPE_SMS:
            subject = "Your %s is having trouble sending messages" % self.channel.get_channel_type_name()
            template = "channels/email/sms_alert"
        else:  # pragma: no cover
            raise Exception(_("Unknown alert type: %(alert)s") % {"alert": self.alert_type})

        context = dict(
            org=self.channel.org,
            channel=self.channel,
            now=timezone.now(),
            last_seen=self.channel.last_seen,
            sync=self.sync_event,
        )
        context["unsent_count"] = Msg.objects.filter(channel=self.channel, status__in=["Q", "P"]).count()
        context["subject"] = subject

        send_template_email(self.channel.alert_email, subject, template, context, self.channel.org.get_branding())

    def release(self):
        self.delete()


def get_alert_user():
    user = User.objects.filter(username="alert").first()
    if user:
        return user
    else:
        user = User.objects.create_user("alert")
        user.groups.add(Group.objects.get(name="Service Users"))
        return user


class ChannelConnection(models.Model):
    """
    Base for IVR sessions which require a connection to specific channel
    """

    PENDING = "P"  # initial state for all sessions
    QUEUED = "Q"  # the session is queued internally
    WIRED = "W"  # the API provider has confirmed that it successfully received the API request

    # valid for IVR sessions
    RINGING = "R"  # the call in ringing
    IN_PROGRESS = "I"  # the call has been answered
    BUSY = "B"  # the call is busy or rejected by the user
    FAILED = "F"  # the platform failed to initiate the call (bad phone number)
    NO_ANSWER = "N"  # the call has timed-out or ringed-out
    CANCELED = "C"  # the call was terminated by platform
    COMPLETED = "D"  # the call was completed successfully

    # valid for USSD sessions
    TRIGGERED = "T"
    INTERRUPTED = "X"
    INITIATED = "A"
    ENDING = "E"

    DONE = (COMPLETED, BUSY, FAILED, NO_ANSWER, CANCELED, INTERRUPTED)
    RETRY_CALL = (BUSY, NO_ANSWER, FAILED)

    INCOMING = "I"
    OUTGOING = "O"

    IVR = "F"  # deprecated use VOICE
    VOICE = "V"

    DIRECTION_CHOICES = ((INCOMING, "Incoming"), (OUTGOING, "Outgoing"))

    TYPE_CHOICES = ((VOICE, "Voice"),)

    STATUS_CHOICES = (
        (PENDING, "Pending"),
        (QUEUED, "Queued"),
        (WIRED, "Wired"),
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
        (ENDING, "Ending"),
    )

    created_on = models.DateTimeField(
        default=timezone.now, editable=False, blank=True, help_text="When this item was originally created"
    )

    modified_on = models.DateTimeField(
        default=timezone.now, editable=False, blank=True, help_text="When this item was last modified"
    )

    external_id = models.CharField(max_length=255, help_text="The external id for this session, our twilio id usually")
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=PENDING)
    channel = models.ForeignKey("Channel", on_delete=models.PROTECT, related_name="connections")
    contact = models.ForeignKey("contacts.Contact", on_delete=models.PROTECT, related_name="connections")
    contact_urn = models.ForeignKey("contacts.ContactURN", on_delete=models.PROTECT, related_name="connections")
    direction = models.CharField(max_length=1, choices=DIRECTION_CHOICES)
    started_on = models.DateTimeField(null=True, blank=True)
    ended_on = models.DateTimeField(null=True, blank=True)
    org = models.ForeignKey(Org, on_delete=models.PROTECT)
    connection_type = models.CharField(max_length=1, choices=TYPE_CHOICES)
    duration = models.IntegerField(default=0, null=True, help_text="The length of this connection in seconds")

    retry_count = models.IntegerField(
        default=0, verbose_name=_("Retry Count"), help_text="The number of times this call has been retried"
    )
    error_count = models.IntegerField(
        default=0, verbose_name=_("Error Count"), help_text="The number of times this call has errored"
    )
    next_attempt = models.DateTimeField(
        verbose_name=_("Next Attempt"), help_text="When we should next attempt to make this call", null=True
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        """ Since the FK is bound to ChannelConnection, when it initializes an instance from
        DB we need to specify the class based on `connection_type` so we can access
        all the methods the proxy model implements. """

        if type(self) is ChannelConnection:
            if self.connection_type == self.IVR:
                from temba.ivr.models import IVRCall

                self.__class__ = IVRCall

    def has_logs(self):
        """
        Returns whether this connection has any channel logs
        """
        return self.channel.is_active and self.channel_logs.count() > 0

    def get_duration(self):
        """
        Either gets the set duration as reported by provider, or tries to calculate it
        """
        duration = self.duration or 0

        if not duration and self.status == self.IN_PROGRESS and self.started_on:
            duration = (timezone.now() - self.started_on).seconds

        return timedelta(seconds=duration)

    def get_session(self):
        """
        There is a one-to-one relationship between flow sessions and connections, but as connection can be null
        it can throw an exception
        """
        try:
            return self.session
        except ObjectDoesNotExist:  # pragma: no cover
            return None

    def release(self):
        for run in self.runs.all():
            run.release()

        for log in self.channel_logs.all():
            log.release()

        session = self.get_session()
        if session:
            session.release()

        for msg in self.msgs.all():
            msg.release()

        self.delete()
