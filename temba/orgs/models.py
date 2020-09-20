import itertools
import logging
import os
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from urllib.parse import quote, urlencode, urlparse

import pycountry
import pytz
import regex
import stripe
import stripe.error
from django_redis import get_redis_connection
from packaging.version import Version
from requests import Session
from smartmin.models import SmartModel
from timezone_field import TimeZoneField
from twilio.rest import Client as TwilioClient

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.temp import NamedTemporaryFile
from django.db import models, transaction
from django.db.models import Count, F, Prefetch, Q, Sum
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.text import slugify
from django.utils.translation import ugettext_lazy as _

from temba import mailroom
from temba.archives.models import Archive
from temba.bundles import get_brand_bundles, get_bundle_map
from temba.locations.models import AdminBoundary, BoundaryAlias
from temba.utils import chunk_list, json, languages
from temba.utils.cache import get_cacheable_attr, get_cacheable_result, incrby_existing
from temba.utils.currencies import currency_for_country
from temba.utils.dates import datetime_to_str, str_to_datetime
from temba.utils.email import send_template_email
from temba.utils.models import JSONAsTextField, SquashableModel
from temba.utils.s3 import public_file_storage
from temba.utils.text import generate_token, random_string
from temba.utils.uuid import uuid4

logger = logging.getLogger(__name__)

# cache keys and TTLs
ORG_LOCK_KEY = "org:%d:lock:%s"
ORG_CREDITS_TOTAL_CACHE_KEY = "org:%d:cache:credits_total"
ORG_CREDITS_PURCHASED_CACHE_KEY = "org:%d:cache:credits_purchased"
ORG_CREDITS_USED_CACHE_KEY = "org:%d:cache:credits_used"
ORG_ACTIVE_TOPUP_KEY = "org:%d:cache:active_topup"
ORG_ACTIVE_TOPUP_REMAINING = "org:%d:cache:credits_remaining:%d"
ORG_CREDIT_EXPIRING_CACHE_KEY = "org:%d:cache:credits_expiring_soon"
ORG_LOW_CREDIT_THRESHOLD_CACHE_KEY = "org:%d:cache:low_credits_threshold"

ORG_LOCK_TTL = 60  # 1 minute
ORG_CREDITS_CACHE_TTL = 7 * 24 * 60 * 60  # 1 week


class OrgLock(Enum):
    """
    Org-level lock types
    """

    contacts = 1
    channels = 2
    credits = 3
    field = 4


class OrgCache(Enum):
    """
    Org-level cache types
    """

    display = 1
    credits = 2


class Org(SmartModel):
    """
    An Org can have several users and is the main component that holds all Flows, Messages, Contacts, etc. Orgs
    know their country so they can deal with locally formatted numbers (numbers provided without a country code).
    As such, each org can only add phone channels from one country.

    Users will create new Org for Flows that should be kept separate (say for distinct projects), or for
    each country where they are deploying messaging applications.
    """

    DATE_FORMAT_DAY_FIRST = "D"
    DATE_FORMAT_MONTH_FIRST = "M"
    DATE_FORMATS = ((DATE_FORMAT_DAY_FIRST, "DD-MM-YYYY"), (DATE_FORMAT_MONTH_FIRST, "MM-DD-YYYY"))

    CONFIG_VERIFIED = "verified"
    CONFIG_SMTP_SERVER = "smtp_server"
    CONFIG_TWILIO_SID = "ACCOUNT_SID"
    CONFIG_TWILIO_TOKEN = "ACCOUNT_TOKEN"
    CONFIG_NEXMO_KEY = "NEXMO_KEY"
    CONFIG_NEXMO_SECRET = "NEXMO_SECRET"
    CONFIG_DTONE_LOGIN = "TRANSFERTO_ACCOUNT_LOGIN"
    CONFIG_DTONE_API_TOKEN = "TRANSFERTO_AIRTIME_API_TOKEN"
    CONFIG_DTONE_CURRENCY = "TRANSFERTO_ACCOUNT_CURRENCY"
    CONFIG_CHATBASE_AGENT_NAME = "CHATBASE_AGENT_NAME"
    CONFIG_CHATBASE_API_KEY = "CHATBASE_API_KEY"
    CONFIG_CHATBASE_VERSION = "CHATBASE_VERSION"

    # items in export JSON
    EXPORT_VERSION = "version"
    EXPORT_SITE = "site"
    EXPORT_FLOWS = "flows"
    EXPORT_CAMPAIGNS = "campaigns"
    EXPORT_TRIGGERS = "triggers"
    EXPORT_FIELDS = "fields"
    EXPORT_GROUPS = "groups"

    EARLIEST_IMPORT_VERSION = "3"
    CURRENT_EXPORT_VERSION = "13"

    uuid = models.UUIDField(unique=True, default=uuid4)

    name = models.CharField(verbose_name=_("Name"), max_length=128)
    plan = models.CharField(
        verbose_name=_("Plan"),
        max_length=16,
        default=settings.DEFAULT_PLAN,
        help_text=_("What plan your organization is on"),
    )
    plan_end = models.DateTimeField(null=True)

    stripe_customer = models.CharField(
        verbose_name=_("Stripe Customer"),
        max_length=32,
        null=True,
        blank=True,
        help_text=_("Our Stripe customer id for your organization"),
    )

    administrators = models.ManyToManyField(
        User,
        verbose_name=_("Administrators"),
        related_name="org_admins",
        help_text=_("The administrators in your organization"),
    )

    viewers = models.ManyToManyField(
        User, verbose_name=_("Viewers"), related_name="org_viewers", help_text=_("The viewers in your organization")
    )

    editors = models.ManyToManyField(
        User, verbose_name=_("Editors"), related_name="org_editors", help_text=_("The editors in your organization")
    )

    surveyors = models.ManyToManyField(
        User,
        verbose_name=_("Surveyors"),
        related_name="org_surveyors",
        help_text=_("The users can login via Android for your organization"),
    )

    language = models.CharField(
        verbose_name=_("Language"),
        max_length=64,
        null=True,
        blank=True,
        choices=settings.LANGUAGES,
        help_text=_("The main language used by this organization"),
    )

    timezone = TimeZoneField(verbose_name=_("Timezone"))

    date_format = models.CharField(
        verbose_name=_("Date Format"),
        max_length=1,
        choices=DATE_FORMATS,
        default=DATE_FORMAT_DAY_FIRST,
        help_text=_("Whether day comes first or month comes first in dates"),
    )

    country = models.ForeignKey(
        "locations.AdminBoundary",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text="The country this organization should map results for.",
    )

    config = JSONAsTextField(
        null=True,
        default=dict,
        verbose_name=_("Configuration"),
        help_text=_("More Organization specific configuration"),
    )

    slug = models.SlugField(
        verbose_name=_("Slug"),
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        error_messages=dict(unique=_("This slug is not available")),
    )

    is_anon = models.BooleanField(
        default=False, help_text=_("Whether this organization anonymizes the phone numbers of contacts within it")
    )

    is_flagged = models.BooleanField(default=False, help_text=_("Whether this organization is currently flagged."))

    is_suspended = models.BooleanField(default=False, help_text=_("Whether this organization is currently suspended."))

    uses_topups = models.BooleanField(default=True, help_text=_("Whether this organization uses topups."))

    is_multi_org = models.BooleanField(
        default=False, help_text=_("Whether this organization can have child workspaces")
    )

    is_multi_user = models.BooleanField(
        default=False, help_text=_("Whether this organization can have multiple logins")
    )

    primary_language = models.ForeignKey(
        "orgs.Language",
        null=True,
        blank=True,
        related_name="orgs",
        help_text=_("The primary language will be used for contacts with no language preference."),
        on_delete=models.PROTECT,
    )

    brand = models.CharField(
        max_length=128,
        default=settings.DEFAULT_BRAND,
        verbose_name=_("Brand"),
        help_text=_("The brand used in emails"),
    )

    surveyor_password = models.CharField(
        null=True, max_length=128, default=None, help_text=_("A password that allows users to register as surveyors")
    )

    parent = models.ForeignKey(
        "orgs.Org",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text=_("The parent org that manages this org"),
    )

    @classmethod
    def get_unique_slug(cls, name):
        slug = slugify(name)

        unique_slug = slug
        if unique_slug:
            existing = Org.objects.filter(slug=unique_slug).exists()
            count = 2
            while existing:
                unique_slug = "%s-%d" % (slug, count)
                existing = Org.objects.filter(slug=unique_slug).exists()
                count += 1

            return unique_slug

    def create_sub_org(self, name, timezone=None, created_by=None):
        if self.is_multi_org:
            if not timezone:
                timezone = self.timezone

            if not created_by:
                created_by = self.created_by

            # generate a unique slug
            slug = Org.get_unique_slug(name)

            brand = settings.BRANDING[self.brand]

            org = Org.objects.create(
                name=name,
                timezone=timezone,
                brand=self.brand,
                parent=self,
                slug=slug,
                created_by=created_by,
                modified_by=created_by,
                plan=brand.get("default_plan", settings.DEFAULT_PLAN),
                is_multi_user=self.is_multi_user,
                is_multi_org=self.is_multi_org,
            )

            org.administrators.add(created_by)

            # initialize our org, but without any credits
            org.initialize(branding=org.get_branding(), topup_size=0)

            return org

    def get_branding(self):
        from temba.middleware import BrandingMiddleware

        return BrandingMiddleware.get_branding_for_host(self.brand)

    def get_brand_domain(self):
        return self.get_branding()["domain"]

    def lock_on(self, lock, qualifier=None):
        """
        Creates the requested type of org-level lock
        """
        r = get_redis_connection()
        lock_key = ORG_LOCK_KEY % (self.pk, lock.name)
        if qualifier:
            lock_key += ":%s" % qualifier

        return r.lock(lock_key, ORG_LOCK_TTL)

    def has_contacts(self):
        """
        Gets whether this org has any contacts
        """
        from temba.contacts.models import ContactGroup

        counts = ContactGroup.get_system_group_counts(self, (ContactGroup.TYPE_ALL, ContactGroup.TYPE_BLOCKED))
        return (counts[ContactGroup.TYPE_ALL] + counts[ContactGroup.TYPE_BLOCKED]) > 0

    @cached_property
    def has_ticketer(self):
        """
        Gets whether this org has an active ticketer configured
        """
        return self.ticketers.filter(is_active=True)

    def clear_credit_cache(self):
        """
        Clears the given cache types (currently just credits) for this org. Returns number of keys actually deleted
        """
        r = get_redis_connection()
        active_topup_keys = [ORG_ACTIVE_TOPUP_REMAINING % (self.pk, topup.pk) for topup in self.topups.all()]
        return r.delete(
            ORG_CREDITS_TOTAL_CACHE_KEY % self.pk,
            ORG_CREDIT_EXPIRING_CACHE_KEY % self.pk,
            ORG_CREDITS_USED_CACHE_KEY % self.pk,
            ORG_CREDITS_PURCHASED_CACHE_KEY % self.pk,
            ORG_LOW_CREDIT_THRESHOLD_CACHE_KEY % self.pk,
            ORG_ACTIVE_TOPUP_KEY % self.pk,
            *active_topup_keys,
        )

    def flag(self):
        self.is_flagged = True
        self.save(update_fields=("is_flagged", "modified_on"))

    def unflag(self):
        self.is_flagged = False
        self.save(update_fields=("is_flagged", "modified_on"))

    def verify(self):
        """
        Unflags org and marks as verified so it won't be flagged automatically in future
        """
        self.is_flagged = False
        self.config[Org.CONFIG_VERIFIED] = True
        self.save(update_fields=("is_flagged", "config", "modified_on"))

    def is_verified(self):
        """
        A verified org is not subject to automatic flagging for suspicious activity
        """
        return self.config.get(Org.CONFIG_VERIFIED, False)

    def import_app(self, export_json, user, site=None, legacy=False):
        """
        Imports previously exported JSON
        """

        from temba.campaigns.models import Campaign
        from temba.contacts.models import ContactField, ContactGroup
        from temba.flows.models import Flow, FlowRevision
        from temba.triggers.models import Trigger

        # only required field is version
        if Org.EXPORT_VERSION not in export_json:
            raise ValueError("Export missing version field")

        export_version = Version(str(export_json[Org.EXPORT_VERSION]))
        export_site = export_json.get(Org.EXPORT_SITE)

        # determine if this app is being imported from the same site
        same_site = False
        if export_site and site:
            same_site = urlparse(export_site).netloc == urlparse(site).netloc

        # do we have a supported export version?
        if not (Version(Org.EARLIEST_IMPORT_VERSION) <= export_version <= Version(Org.CURRENT_EXPORT_VERSION)):
            raise ValueError(f"Unsupported export version {export_version}")

        # do we need to migrate the export forward?
        if export_version < Version(Flow.CURRENT_SPEC_VERSION):
            export_json = FlowRevision.migrate_export(self, export_json, same_site, export_version, legacy=legacy)

        export_fields = export_json.get(Org.EXPORT_FIELDS, [])
        export_groups = export_json.get(Org.EXPORT_GROUPS, [])
        export_campaigns = export_json.get(Org.EXPORT_CAMPAIGNS, [])
        export_triggers = export_json.get(Org.EXPORT_TRIGGERS, [])

        dependency_mapping = {}  # dependency UUIDs in import => new UUIDs

        with transaction.atomic():
            ContactField.import_fields(self, user, export_fields)
            ContactGroup.import_groups(self, user, export_groups, dependency_mapping)

            new_flows = Flow.import_flows(self, user, export_json, dependency_mapping, same_site)

            # these depend on flows so are imported last
            Campaign.import_campaigns(self, user, export_campaigns, same_site)
            Trigger.import_triggers(self, user, export_triggers, same_site)

        # with all the flows and dependencies committed, we can now have mailroom do full validation
        for flow in new_flows:
            mailroom.get_client().flow_inspect(self.id, flow.as_json())

    @classmethod
    def export_definitions(cls, site_link, components, include_fields=True, include_groups=True):
        from temba.contacts.models import ContactField
        from temba.campaigns.models import Campaign
        from temba.flows.models import Flow
        from temba.triggers.models import Trigger

        exported_flows = []
        exported_campaigns = []
        exported_triggers = []

        # users can't choose which fields/groups to export - we just include all the dependencies
        fields = set()
        groups = set()

        for component in components:
            if isinstance(component, Flow):
                component.ensure_current_version()  # only export current versions
                exported_flows.append(component.as_json(expand_contacts=True))

                if include_groups:
                    groups.update(component.group_dependencies.all())
                if include_fields:
                    fields.update(component.field_dependencies.all())

            elif isinstance(component, Campaign):
                exported_campaigns.append(component.as_export_def())

                if include_groups:
                    groups.add(component.group)
                if include_fields:
                    for event in component.events.all():
                        if event.relative_to.field_type == ContactField.FIELD_TYPE_USER:
                            fields.add(event.relative_to)

            elif isinstance(component, Trigger):
                exported_triggers.append(component.as_export_def())

                if include_groups:
                    groups.update(component.groups.all())

        return {
            Org.EXPORT_VERSION: Org.CURRENT_EXPORT_VERSION,
            Org.EXPORT_SITE: site_link,
            Org.EXPORT_FLOWS: exported_flows,
            Org.EXPORT_CAMPAIGNS: exported_campaigns,
            Org.EXPORT_TRIGGERS: exported_triggers,
            Org.EXPORT_FIELDS: [f.as_export_def() for f in sorted(fields, key=lambda f: f.key)],
            Org.EXPORT_GROUPS: [g.as_export_def() for g in sorted(groups, key=lambda g: g.name)],
        }

    def can_add_sender(self):  # pragma: needs cover
        """
        If an org's telephone send channel is an Android device, let them add a bulk sender
        """
        from temba.contacts.models import TEL_SCHEME

        send_channel = self.get_send_channel(TEL_SCHEME)
        return send_channel and send_channel.is_android()

    def can_add_caller(self):  # pragma: needs cover
        return not self.supports_ivr() and self.is_connected_to_twilio()

    def supports_ivr(self):
        return self.get_call_channel() or self.get_answer_channel()

    def get_channel(self, scheme, country_code, role):
        """
        Gets a channel for this org which supports the given scheme and role
        """
        from temba.channels.models import Channel

        channels = self.channels.filter(is_active=True, role__contains=role).order_by("-pk")

        if scheme is not None:
            channels = channels.filter(schemes__contains=[scheme])

        channel = None
        if country_code:
            channel = channels.filter(country=country_code).first()

        # no channel? try without country
        if not channel:
            channel = channels.first()

        if channel and (role == Channel.ROLE_SEND or role == Channel.ROLE_CALL):
            return channel.get_delegate(role)
        else:
            return channel

    @cached_property
    def cached_all_contacts_group(self):
        from temba.contacts.models import ContactGroup

        return ContactGroup.all_groups.get(org=self, group_type=ContactGroup.TYPE_ALL)

    def get_channel_for_role(self, role, scheme=None, contact_urn=None, country_code=None):
        from temba.contacts.models import TEL_SCHEME
        from temba.channels.models import Channel
        from temba.contacts.models import ContactURN

        if contact_urn:
            scheme = contact_urn.scheme

            # if URN has a previously used channel that is still active, use that
            if contact_urn.channel and contact_urn.channel.is_active:  # pragma: no cover
                previous_sender = contact_urn.channel.get_delegate(role)
                if previous_sender:
                    return previous_sender

            if scheme == TEL_SCHEME:
                path = contact_urn.path

                # we don't have a channel for this contact yet, let's try to pick one from the same carrier
                # we need at least one digit to overlap to infer a channel
                contact_number = path.strip("+")
                prefix = 1
                channel = None

                # try to use only a channel in the same country
                if not country_code:
                    country_code = ContactURN.derive_country_from_tel(path)

                channels = []
                if country_code:
                    for c in self.channels.filter(is_active=True):
                        if c.country == country_code and TEL_SCHEME in c.schemes:
                            channels.append(c)

                # no country specific channel, try to find any channel at all
                if not channels:
                    channels = [c for c in self.channels.filter(is_active=True) if TEL_SCHEME in c.schemes]

                # filter based on role and activity (we do this in python as channels can be prefetched so it is quicker in those cases)
                senders = []
                for c in channels:
                    if c.is_active and c.address and role in c.role and not c.parent_id:
                        senders.append(c)
                senders.sort(key=lambda chan: chan.id)

                # if we have more than one match, find the one with the highest overlap
                if len(senders) > 1:
                    for sender in senders:
                        config = sender.config
                        channel_prefixes = config.get(Channel.CONFIG_SHORTCODE_MATCHING_PREFIXES, [])
                        if not channel_prefixes or not isinstance(channel_prefixes, list):
                            channel_prefixes = [sender.address.strip("+")]

                        for chan_prefix in channel_prefixes:
                            for idx in range(prefix, len(chan_prefix) + 1):
                                if idx >= prefix and chan_prefix[0:idx] == contact_number[0:idx]:
                                    prefix = idx
                                    channel = sender
                                else:
                                    break
                elif senders:
                    channel = senders[0]

                if channel:
                    if role == Channel.ROLE_SEND:
                        return channel.get_delegate(Channel.ROLE_SEND)
                    else:  # pragma: no cover
                        return channel

        # get any send channel without any country or URN hints
        return self.get_channel(scheme, country_code, role)

    def get_send_channel(self, scheme=None, contact_urn=None):
        from temba.channels.models import Channel

        return self.get_channel_for_role(Channel.ROLE_SEND, scheme=scheme, contact_urn=contact_urn)

    def get_receive_channel(self, scheme=None):
        from temba.channels.models import Channel

        return self.get_channel_for_role(Channel.ROLE_RECEIVE, scheme=scheme)

    def get_call_channel(self):
        from temba.contacts.models import TEL_SCHEME
        from temba.channels.models import Channel

        return self.get_channel_for_role(Channel.ROLE_CALL, scheme=TEL_SCHEME)

    def get_answer_channel(self):
        from temba.contacts.models import TEL_SCHEME
        from temba.channels.models import Channel

        return self.get_channel_for_role(Channel.ROLE_ANSWER, scheme=TEL_SCHEME)

    def get_schemes(self, role):
        """
        Gets all URN schemes which this org has org has channels configured for
        """
        cache_attr = "__schemes__%s" % role
        if hasattr(self, cache_attr):
            return getattr(self, cache_attr)

        schemes = set()
        for channel in self.channels.filter(is_active=True, role__contains=role):
            for scheme in channel.schemes:
                schemes.add(scheme)

        setattr(self, cache_attr, schemes)
        return schemes

    def normalize_contact_tels(self):
        """
        Attempts to normalize any contacts which don't have full e164 phone numbers
        """
        from .tasks import normalize_contact_tels_task

        normalize_contact_tels_task.delay(self.pk)

    def get_resthooks(self):
        """
        Returns the resthooks configured on this Org
        """
        return self.resthooks.filter(is_active=True).order_by("slug")

    def get_channel_countries(self):
        channel_countries = []

        if not self.is_connected_to_dtone():
            return channel_countries

        channel_country_codes = self.channels.filter(is_active=True).exclude(country=None)
        channel_country_codes = set(channel_country_codes.values_list("country", flat=True))

        for country_code in channel_country_codes:
            country_obj = pycountry.countries.get(alpha_2=country_code)
            country_name = country_obj.name
            currency = currency_for_country(country_code)
            channel_countries.append(
                dict(code=country_code, name=country_name, currency_code=currency.alpha_3, currency_name=currency.name)
            )

        return sorted(channel_countries, key=lambda k: k["name"])

    @classmethod
    def get_possible_countries(cls):
        return AdminBoundary.objects.filter(level=0).order_by("name")

    def trigger_send(self, msgs=None):
        """
        Triggers either our Android channels to sync, or for all our pending messages to be queued
        to send.
        """

        from temba.channels.models import Channel
        from temba.channels.types.android import AndroidType
        from temba.msgs.models import Msg

        # if we have msgs, then send just those
        if msgs is not None:
            ids = [m.id for m in msgs]

            # trigger syncs for our android channels
            for channel in self.channels.filter(is_active=True, channel_type=AndroidType.code, msgs__id__in=ids):
                channel.trigger_sync()

            # and send those messages
            Msg.send_messages(msgs)

        # otherwise, sync all pending messages and channels
        else:
            for channel in self.channels.filter(is_active=True, channel_type=AndroidType.code):  # pragma: needs cover
                channel.trigger_sync()

            # otherwise, send any pending messages on our channels
            r = get_redis_connection()

            key = "trigger_send_%d" % self.pk

            # only try to send all pending messages if nobody is doing so already
            if not r.get(key):
                with r.lock(key, timeout=900):
                    pending = Channel.get_pending_messages(self)
                    Msg.send_messages(pending)

    def add_smtp_config(self, from_email, host, username, password, port, user):
        username = quote(username)
        password = quote(password, safe="")
        query = urlencode({"from": f"{from_email.strip()}", "tls": "true"})

        self.config.update({Org.CONFIG_SMTP_SERVER: f"smtp://{username}:{password}@{host}:{port}/?{query}"})
        self.modified_by = user
        self.save(update_fields=("config", "modified_by", "modified_on"))

    def remove_smtp_config(self, user):
        if self.config:
            self.config.pop(Org.CONFIG_SMTP_SERVER, None)
            self.modified_by = user
            self.save(update_fields=("config", "modified_by", "modified_on"))

    def has_smtp_config(self):
        if self.config:
            return bool(self.config.get(Org.CONFIG_SMTP_SERVER))
        return False

    def has_airtime_transfers(self):
        from temba.airtime.models import AirtimeTransfer

        return AirtimeTransfer.objects.filter(org=self).exists()

    def connect_nexmo(self, api_key, api_secret, user):
        self.config.update({Org.CONFIG_NEXMO_KEY: api_key.strip(), Org.CONFIG_NEXMO_SECRET: api_secret.strip()})
        self.modified_by = user
        self.save(update_fields=("config", "modified_by", "modified_on"))

    def connect_twilio(self, account_sid, account_token, user):
        self.config.update({Org.CONFIG_TWILIO_SID: account_sid, Org.CONFIG_TWILIO_TOKEN: account_token})
        self.modified_by = user
        self.save(update_fields=("config", "modified_by", "modified_on"))

    def connect_dtone(self, account_login, airtime_api_token, user):
        self.config.update(
            {Org.CONFIG_DTONE_LOGIN: account_login.strip(), Org.CONFIG_DTONE_API_TOKEN: airtime_api_token.strip()}
        )
        self.modified_by = user
        self.save(update_fields=("config", "modified_by", "modified_on"))

    def connect_chatbase(self, agent_name, api_key, version, user):
        self.config.update(
            {
                Org.CONFIG_CHATBASE_AGENT_NAME: agent_name,
                Org.CONFIG_CHATBASE_API_KEY: api_key,
                Org.CONFIG_CHATBASE_VERSION: version,
            }
        )
        self.modified_by = user
        self.save(update_fields=("config", "modified_by", "modified_on"))

    def is_connected_to_nexmo(self):
        if self.config:
            return self.config.get(Org.CONFIG_NEXMO_KEY) and self.config.get(Org.CONFIG_NEXMO_SECRET)
        return False

    def is_connected_to_twilio(self):
        if self.config:
            return self.config.get(Org.CONFIG_TWILIO_SID) and self.config.get(Org.CONFIG_TWILIO_TOKEN)
        return False

    def is_connected_to_dtone(self):
        if self.config:
            return self.config.get(Org.CONFIG_DTONE_LOGIN) and self.config.get(Org.CONFIG_DTONE_API_TOKEN)
        return False

    def remove_nexmo_account(self, user):
        if self.config:
            # release any nexmo channels
            for channel in self.channels.filter(is_active=True, channel_type="NX"):  # pragma: needs cover
                channel.release()

            self.config.pop(Org.CONFIG_NEXMO_KEY, None)
            self.config.pop(Org.CONFIG_NEXMO_SECRET, None)
            self.modified_by = user
            self.save(update_fields=("config", "modified_by", "modified_on"))

    def remove_twilio_account(self, user):
        if self.config:
            # release any Twilio and Twilio Messaging Service channels
            for channel in self.channels.filter(is_active=True, channel_type__in=["T", "TMS"]):
                channel.release()

            self.config.pop(Org.CONFIG_TWILIO_SID, None)
            self.config.pop(Org.CONFIG_TWILIO_TOKEN, None)
            self.modified_by = user
            self.save(update_fields=("config", "modified_by", "modified_on"))

    def remove_dtone_account(self, user):
        if self.config:
            self.config.pop(Org.CONFIG_DTONE_LOGIN, None)
            self.config.pop(Org.CONFIG_DTONE_API_TOKEN, None)
            self.config.pop(Org.CONFIG_DTONE_CURRENCY, None)
            self.modified_by = user
            self.save(update_fields=("config", "modified_by", "modified_on"))

    def remove_chatbase_account(self, user):
        if self.config:
            self.config.pop(Org.CONFIG_CHATBASE_AGENT_NAME, None)
            self.config.pop(Org.CONFIG_CHATBASE_API_KEY, None)
            self.config.pop(Org.CONFIG_CHATBASE_VERSION, None)
            self.modified_by = user
            self.save(update_fields=("config", "modified_by", "modified_on"))

    def refresh_dtone_account_currency(self):
        client = self.get_dtone_client()
        response = client.check_wallet()
        account_currency = response.get("currency", "")

        self.config.update({Org.CONFIG_DTONE_CURRENCY: account_currency})
        self.save(update_fields=("config", "modified_on"))

    def get_twilio_client(self):
        account_sid = self.config.get(Org.CONFIG_TWILIO_SID)
        auth_token = self.config.get(Org.CONFIG_TWILIO_TOKEN)
        if account_sid and auth_token:
            return TwilioClient(account_sid, auth_token)
        return None

    def get_nexmo_client(self):
        from temba.channels.types.nexmo.client import NexmoClient

        api_key = self.config.get(Org.CONFIG_NEXMO_KEY)
        api_secret = self.config.get(Org.CONFIG_NEXMO_SECRET)
        if api_key and api_secret:
            return NexmoClient(api_key, api_secret)
        return None

    def get_dtone_client(self):
        from temba.airtime.dtone import DTOneClient

        login = self.config.get(Org.CONFIG_DTONE_LOGIN)
        api_token = self.config.get(Org.CONFIG_DTONE_API_TOKEN)

        if login and api_token:
            return DTOneClient(login, api_token)
        return None

    def get_chatbase_credentials(self):
        if self.config:
            return self.config.get(Org.CONFIG_CHATBASE_API_KEY), self.config.get(Org.CONFIG_CHATBASE_VERSION)
        return None, None

    def get_country_code(self):
        """
        Gets the 2-digit country code, e.g. RW, US
        """
        return get_cacheable_attr(self, "_country_code", lambda: self.calculate_country_code())

    def calculate_country_code(self):
        # first try the actual country field
        if self.country:
            try:
                country = pycountry.countries.get(name=self.country.name)
                if country:
                    return country.alpha_2
            except KeyError:  # pragma: no cover
                # pycountry blows up if we pass it a country name it doesn't know
                pass

        # if that isn't set and we only have have one country set for our channels, use that
        countries = self.channels.filter(is_active=True).exclude(country=None).order_by("country")
        countries = countries.distinct("country").values_list("country", flat=True)
        if len(countries) == 1:
            return countries[0]

        return None

    def get_language_codes(self):
        return get_cacheable_attr(self, "_language_codes", lambda: {l.iso_code for l in self.languages.all()})

    def set_languages(self, user, iso_codes, primary):
        """
        Sets languages for this org, creating and deleting language objects as necessary
        """
        for iso_code in iso_codes:
            name = languages.get_language_name(iso_code)
            language = self.languages.filter(iso_code=iso_code).first()

            # if it's valid and doesn't exist yet, create it
            if name and not language:
                language = self.languages.create(iso_code=iso_code, name=name, created_by=user, modified_by=user)

            if iso_code == primary:
                self.primary_language = language
                self.save(update_fields=("primary_language",))

        # unset the primary language if not in the new list of codes
        if self.primary_language and self.primary_language.iso_code not in iso_codes:
            self.primary_language = None
            self.save(update_fields=("primary_language",))

        # remove any languages that are not in the new list
        self.languages.exclude(iso_code__in=iso_codes).delete()

        if hasattr(self, "_language_codes"):  # invalidate language cache if set
            delattr(self, "_language_codes")

    def get_dayfirst(self):
        return self.date_format == Org.DATE_FORMAT_DAY_FIRST

    def get_datetime_formats(self):
        if self.date_format == Org.DATE_FORMAT_DAY_FIRST:
            format_date = "%d-%m-%Y"
        else:
            format_date = "%m-%d-%Y"

        format_datetime = format_date + " %H:%M"

        return format_date, format_datetime

    def format_datetime(self, d, show_time=True):
        """
        Formats a datetime with or without time using this org's date format
        """
        formats = self.get_datetime_formats()
        format = formats[1] if show_time else formats[0]
        return datetime_to_str(d, format, self.timezone)

    def parse_datetime(self, s):
        assert isinstance(s, str)

        return str_to_datetime(s, self.timezone, self.get_dayfirst())

    def parse_number(self, s):
        assert isinstance(s, str)

        parsed = None
        try:
            parsed = Decimal(s)

            if not parsed.is_finite() or parsed > Decimal("999999999999999999999999"):
                parsed = None
        except Exception:
            pass

        return parsed

    def generate_location_query(self, name, level, is_alias=False):
        if is_alias:
            query = dict(name__iexact=name, boundary__level=level)
            query["__".join(["boundary"] + ["parent"] * level)] = self.country
        else:
            query = dict(name__iexact=name, level=level)
            query["__".join(["parent"] * level)] = self.country

        return query

    def find_boundary_by_name(self, name, level, parent):
        """
        Finds the boundary with the passed in name or alias on this organization at the stated level.

        @returns Iterable of matching boundaries
        """
        # first check if we have a direct name match
        if parent:
            boundary = parent.children.filter(name__iexact=name, level=level)
        else:
            query = self.generate_location_query(name, level)
            boundary = AdminBoundary.objects.filter(**query)

        # not found by name, try looking up by alias
        if not boundary:
            if parent:
                alias = BoundaryAlias.objects.filter(
                    name__iexact=name, boundary__level=level, boundary__parent=parent
                ).first()
            else:
                query = self.generate_location_query(name, level, True)
                alias = BoundaryAlias.objects.filter(**query).first()

            if alias:
                boundary = [alias.boundary]

        return boundary

    def parse_location_path(self, location_string):
        """
        Parses a location path into a single location, returning None if not found
        """
        # while technically we could resolve a full boundary path without a country, our policy is that
        # if you don't have a country set then you don't have locations
        return (
            AdminBoundary.objects.filter(path__iexact=location_string.strip()).first()
            if self.country_id and isinstance(location_string, str)
            else None
        )

    def parse_location(self, location_string, level, parent=None):
        """
        Attempts to parse the passed in location string at the passed in level. This does various tokenizing
        of the string to try to find the best possible match.

        @returns Iterable of matching boundaries
        """
        # no country? bail
        if not self.country_id or not isinstance(location_string, str):
            return []

        boundary = None

        # try it as a path first if it looks possible
        if level == AdminBoundary.LEVEL_COUNTRY or AdminBoundary.PATH_SEPARATOR in location_string:
            boundary = self.parse_location_path(location_string)
            if boundary:
                boundary = [boundary]

        # try to look up it by full name
        if not boundary:
            boundary = self.find_boundary_by_name(location_string, level, parent)

        # try removing punctuation and try that
        if not boundary:
            bare_name = regex.sub(r"\W+", " ", location_string, flags=regex.UNICODE | regex.V0).strip()
            boundary = self.find_boundary_by_name(bare_name, level, parent)

        # if we didn't find it, tokenize it
        if not boundary:
            words = regex.split(r"\W+", location_string.lower(), flags=regex.UNICODE | regex.V0)
            if len(words) > 1:
                for word in words:
                    boundary = self.find_boundary_by_name(word, level, parent)
                    if boundary:
                        break

                if not boundary:
                    # still no boundary? try n-gram of 2
                    for i in range(0, len(words) - 1):
                        bigram = " ".join(words[i : i + 2])
                        boundary = self.find_boundary_by_name(bigram, level, parent)
                        if boundary:  # pragma: needs cover
                            break

        return boundary

    def get_org_admins(self):
        return self.administrators.all()

    def get_org_editors(self):
        return self.editors.all()

    def get_org_viewers(self):
        return self.viewers.all()

    def get_org_surveyors(self):
        return self.surveyors.all()

    def get_org_users(self):
        org_users = self.get_org_admins() | self.get_org_editors() | self.get_org_viewers() | self.get_org_surveyors()
        return org_users.distinct().order_by("email")

    def latest_admin(self):
        admin = self.get_org_admins().last()

        # no admins? try editors
        if not admin:  # pragma: needs cover
            admin = self.get_org_editors().last()

        # no editors? try viewers
        if not admin:  # pragma: needs cover
            admin = self.get_org_viewers().last()

        return admin

    def get_user_org_group(self, user):
        if user in self.get_org_admins():
            user._org_group = Group.objects.get(name="Administrators")
        elif user in self.get_org_editors():
            user._org_group = Group.objects.get(name="Editors")
        elif user in self.get_org_viewers():
            user._org_group = Group.objects.get(name="Viewers")
        elif user in self.get_org_surveyors():
            user._org_group = Group.objects.get(name="Surveyors")
        elif user.is_staff:
            user._org_group = Group.objects.get(name="Administrators")
        else:
            user._org_group = None

        return getattr(user, "_org_group", None)

    def has_twilio_number(self):  # pragma: needs cover
        return self.channels.filter(channel_type="T")

    def has_nexmo_number(self):  # pragma: needs cover
        return self.channels.filter(channel_type="NX")

    def create_welcome_topup(self, topup_size=None):
        if topup_size:
            return TopUp.create(self.created_by, price=0, credits=topup_size, org=self)
        return None

    def create_sample_flows(self, api_url):
        # get our sample dir
        filename = os.path.join(settings.STATICFILES_DIRS[0], "examples", "sample_flows.json")

        # for each of our samples
        with open(filename, "r") as example_file:
            samples = example_file.read()

        user = self.get_user()
        if user:
            # some some substitutions
            samples = samples.replace("{{EMAIL}}", user.username).replace("{{API_URL}}", api_url)

            try:
                self.import_app(json.loads(samples), user)
            except Exception as e:  # pragma: needs cover
                logger.error(
                    f"Failed creating sample flows: {str(e)}",
                    exc_info=True,
                    extra=dict(definition=json.loads(samples)),
                )

    def get_user(self):
        return self.administrators.filter(is_active=True).first()

    def has_low_credits(self):
        return self.get_credits_remaining() <= self.get_low_credits_threshold()

    def get_low_credits_threshold(self):
        """
        Get the credits number to consider as low threshold to this org
        """
        return get_cacheable_result(
            ORG_LOW_CREDIT_THRESHOLD_CACHE_KEY % self.pk, self._calculate_low_credits_threshold
        )

    def _calculate_low_credits_threshold(self):
        now = timezone.now()
        unexpired_topups = self.topups.filter(is_active=True, expires_on__gte=now)

        active_topup_credits = [topup.credits for topup in unexpired_topups if topup.get_remaining() > 0]
        last_topup_credits = sum(active_topup_credits)

        return int(last_topup_credits * 0.15), self.get_credit_ttl()

    def get_credits_total(self, force_dirty=False):
        """
        Gets the total number of credits purchased or assigned to this org
        """
        return get_cacheable_result(
            ORG_CREDITS_TOTAL_CACHE_KEY % self.pk, self._calculate_credits_total, force_dirty=force_dirty
        )

    def get_purchased_credits(self):
        """
        Returns the total number of credits purchased
        :return:
        """
        return get_cacheable_result(ORG_CREDITS_PURCHASED_CACHE_KEY % self.pk, self._calculate_purchased_credits)

    def _calculate_purchased_credits(self):
        purchased_credits = (
            self.topups.filter(is_active=True, price__gt=0).aggregate(Sum("credits")).get("credits__sum")
        )
        return purchased_credits if purchased_credits else 0, self.get_credit_ttl()

    def _calculate_credits_total(self):
        active_credits = (
            self.topups.filter(is_active=True, expires_on__gte=timezone.now())
            .aggregate(Sum("credits"))
            .get("credits__sum")
        )
        active_credits = active_credits if active_credits else 0

        # these are the credits that have been used in expired topups
        expired_credits = (
            TopUpCredits.objects.filter(topup__org=self, topup__is_active=True, topup__expires_on__lte=timezone.now())
            .aggregate(Sum("used"))
            .get("used__sum")
        )

        expired_credits = expired_credits if expired_credits else 0

        return active_credits + expired_credits, self.get_credit_ttl()

    def get_credits_used(self):
        """
        Gets the number of credits used by this org
        """
        return get_cacheable_result(ORG_CREDITS_USED_CACHE_KEY % self.pk, self._calculate_credits_used)

    def _calculate_credits_used(self):
        used_credits_sum = TopUpCredits.objects.filter(topup__org=self, topup__is_active=True)
        used_credits_sum = used_credits_sum.aggregate(Sum("used")).get("used__sum")
        used_credits_sum = used_credits_sum if used_credits_sum else 0

        # if we don't have an active topup, add up pending messages too
        if not self.get_active_topup_id():
            used_credits_sum += self.msgs.filter(topup=None).count()

            # we don't cache in this case
            return used_credits_sum, 0

        return used_credits_sum, self.get_credit_ttl()

    def get_credits_remaining(self):
        """
        Gets the number of credits remaining for this org
        """
        return self.get_credits_total() - self.get_credits_used()

    def select_most_recent_topup(self, amount):
        """
        Determines the active topup with latest expiry date and returns that
        along with how many credits we will be able to decrement from it. Amount
        decremented is not guaranteed to be the full amount requested.
        """
        # if we have an active topup cache, we need to decrement the amount remaining
        non_expired_topups = self.topups.filter(is_active=True, expires_on__gte=timezone.now()).order_by(
            "-expires_on", "id"
        )
        active_topups = (
            non_expired_topups.annotate(used_credits=Sum("topupcredits__used"))
            .filter(credits__gt=0)
            .filter(Q(used_credits__lt=F("credits")) | Q(used_credits=None))
        )
        active_topup = active_topups.first()

        if active_topup:
            available_credits = active_topup.get_remaining()

            if amount > available_credits:
                # use only what is available
                return active_topup.id, available_credits
            else:
                # use the full amount
                return active_topup.id, amount
        else:  # pragma: no cover
            return None, 0

    def allocate_credits(self, user, org, amount):
        """
        Allocates credits to a sub org of the current org, but only if it
        belongs to us and we have enough credits to do so.
        """
        if org.parent == self or self.parent == org.parent or self.parent == org:
            if self.get_credits_remaining() >= amount:

                with self.lock_on(OrgLock.credits):

                    # now debit our account
                    debited = None
                    while amount or debited == 0:

                        # remove the credits from ourselves
                        (topup_id, debited) = self.select_most_recent_topup(amount)

                        if topup_id:
                            topup = TopUp.objects.get(id=topup_id)

                            # create the topup for our child, expiring on the same date
                            new_topup = TopUp.create(
                                user, credits=debited, org=org, expires_on=topup.expires_on, price=None
                            )

                            # create a debit for transaction history
                            Debit.objects.create(
                                topup_id=topup_id,
                                amount=debited,
                                beneficiary=new_topup,
                                debit_type=Debit.TYPE_ALLOCATION,
                                created_by=user,
                            )

                            # decrease the amount of credits we need
                            amount -= debited

                        else:  # pragma: needs cover
                            break

                    # apply topups to messages missing them
                    from .tasks import apply_topups_task

                    apply_topups_task.delay(org.id)

                    # the credit cache for our org should be invalidated too
                    self.clear_credit_cache()

                return True

        # couldn't allocate credits
        return False

    def decrement_credit(self):
        """
        Decrements this orgs credit by amount.

        Determines the active topup and returns that along with how many credits we were able
        to decrement it by. Amount decremented is not guaranteed to be the full amount requested.
        """
        # amount is hardcoded to `1` in database triggers that handle TopUpCredits relation when sending messages
        AMOUNT = 1

        r = get_redis_connection()

        # we always consider this a credit 'used' since un-applied msgs are pending
        # credit expenses for the next purchased topup
        incrby_existing(ORG_CREDITS_USED_CACHE_KEY % self.id, AMOUNT)

        # if we have an active topup cache, we need to decrement the amount remaining
        active_topup_id = self.get_active_topup_id()
        if active_topup_id:
            remaining = r.decr(ORG_ACTIVE_TOPUP_REMAINING % (self.id, active_topup_id), AMOUNT)

            # near the edge, clear out our cache and calculate from the db
            if not remaining or int(remaining) < 100:
                active_topup_id = None
                self.clear_credit_cache()

        # calculate our active topup if we need to
        if not active_topup_id:
            active_topup = self.get_active_topup(force_dirty=True)
            if active_topup:
                active_topup_id = active_topup.id
                r.decr(ORG_ACTIVE_TOPUP_REMAINING % (self.id, active_topup.id), AMOUNT)

        if active_topup_id:
            return (active_topup_id, AMOUNT)

        return None, 0

    def get_active_topup(self, force_dirty=False):
        topup_id = self.get_active_topup_id(force_dirty=force_dirty)
        if topup_id:
            return TopUp.objects.get(id=topup_id)
        return None

    def get_active_topup_id(self, force_dirty=False):
        return get_cacheable_result(
            ORG_ACTIVE_TOPUP_KEY % self.pk, self._calculate_active_topup, force_dirty=force_dirty
        )

    def get_credit_ttl(self):
        """
        Credit TTL should be smallest of active topup expiration and ORG_CREDITS_CACHE_TTL
        :return:
        """
        return self.get_topup_ttl(self.get_active_topup())

    def get_topup_ttl(self, topup):
        """
        Gets how long metrics based on the given topup should live. Returns the shorter ttl of
        either ORG_CREDITS_CACHE_TTL or time remaining on the expiration
        """
        if not topup:
            return 10

        return max(10, min((ORG_CREDITS_CACHE_TTL, int((topup.expires_on - timezone.now()).total_seconds()))))

    def _calculate_active_topup(self):
        """
        Calculates the oldest non-expired topup that still has credits
        """
        non_expired_topups = self.topups.filter(is_active=True, expires_on__gte=timezone.now()).order_by(
            "expires_on", "id"
        )
        active_topups = (
            non_expired_topups.annotate(used_credits=Sum("topupcredits__used"))
            .filter(credits__gt=0)
            .filter(Q(used_credits__lt=F("credits")) | Q(used_credits=None))
        )

        topup = active_topups.first()
        if topup:
            # initialize our active topup metrics
            r = get_redis_connection()
            ttl = self.get_topup_ttl(topup)
            r.set(ORG_ACTIVE_TOPUP_REMAINING % (self.id, topup.id), topup.get_remaining(), ttl)
            return topup.id, ttl

        return 0, 0

    def apply_topups(self):
        """
        We allow users to receive messages even if they're out of credit. Once they re-add credit, this function
        retro-actively applies topups to any messages or IVR actions that don't have a topup
        """
        from temba.msgs.models import Msg

        with self.lock_on(OrgLock.credits):
            # get all items that haven't been credited
            msg_uncredited = self.msgs.filter(topup=None).order_by("created_on")
            all_uncredited = list(msg_uncredited)

            # get all topups that haven't expired
            unexpired_topups = list(
                self.topups.filter(is_active=True, expires_on__gte=timezone.now()).order_by("-expires_on")
            )

            # dict of topups to lists of their newly assigned items
            new_topup_items = {topup: [] for topup in unexpired_topups}

            # assign topup with credits to items...
            current_topup = None
            current_topup_remaining = 0

            for item in all_uncredited:
                # find a topup with remaining credit
                while current_topup_remaining <= 0:
                    if not unexpired_topups:
                        break

                    current_topup = unexpired_topups.pop()
                    current_topup_remaining = current_topup.credits - current_topup.get_used()

                if current_topup_remaining:
                    # if we found some credit, assign the item to the current topup
                    new_topup_items[current_topup].append(item)
                    current_topup_remaining -= 1
                else:
                    # if not, then stop processing items
                    break

            # update items in the database with their new topups
            for topup, items in new_topup_items.items():
                msg_ids = [item.id for item in items if isinstance(item, Msg)]
                Msg.objects.filter(id__in=msg_ids).update(topup=topup)

        # deactive all our credit alerts
        CreditAlert.reset_for_org(self)

        # any time we've reapplied topups, lets invalidate our credit cache too
        self.clear_credit_cache()

        # update our capabilities based on topups
        self.update_capabilities()

    def reset_capabilities(self):
        """
        Resets our capabilities based on the current tiers, mostly used in unit tests
        """
        self.is_multi_user = False
        self.is_multi_org = False
        self.update_capabilities()

    def update_capabilities(self):
        """
        Using our topups and brand settings, figures out whether this org should be multi-user and multi-org. We never
        disable one of these capabilities, but will turn it on for those that qualify via credits
        """
        if self.get_purchased_credits() >= self.get_branding().get("tiers", {}).get("multi_org", 0):
            self.is_multi_org = True

        if self.get_purchased_credits() >= self.get_branding().get("tiers", {}).get("multi_user", 0):
            self.is_multi_user = True

        self.save(update_fields=("is_multi_user", "is_multi_org"))

    def get_stripe_customer(self):  # pragma: no cover
        # We can't test stripe in unit tests since it requires javascript tokens to be generated
        if not self.stripe_customer:
            return None

        try:
            stripe.api_key = get_stripe_credentials()[1]
            customer = stripe.Customer.retrieve(self.stripe_customer)
            return customer
        except Exception as e:
            logger.error(f"Could not get Stripe customer: {str(e)}", exc_info=True)
            return None

    def get_bundles(self):
        return get_brand_bundles(self.get_branding())

    def add_credits(self, bundle, token, user):
        # look up our bundle
        bundle_map = get_bundle_map(self.get_bundles())
        if bundle not in bundle_map:
            raise ValidationError(_("Invalid bundle: %s, cannot upgrade.") % bundle)
        bundle = bundle_map[bundle]

        # adds credits to this org
        stripe.api_key = get_stripe_credentials()[1]

        # our actual customer object
        customer = self.get_stripe_customer()

        # 3 possible cases
        # 1. we already have a stripe customer and the token matches it
        # 2. we already have a stripe customer, but they have just added a new card, we need to use that one
        # 3. we don't have a customer, so we need to create a new customer and use that card

        validation_error = None

        # for our purposes, #1 and #2 are treated the same, we just always update the default card
        try:

            if not customer or customer.email != user.email:
                # then go create a customer object for this user
                customer = stripe.Customer.create(card=token, email=user.email, description="{ org: %d }" % self.pk)

                stripe_customer = customer.id
                self.stripe_customer = stripe_customer
                self.save()

            # update the stripe card to the one they just entered
            else:
                # remove existing cards
                # TODO: this is all a bit wonky because we are using the Stripe JS widget..
                # if we instead used on our mechanism to display / edit cards we could be a bit smarter
                existing_cards = [c for c in customer.cards.list().data]
                for card in existing_cards:
                    card.delete()

                card = customer.cards.create(card=token)

                customer.default_card = card.id
                customer.save()

                stripe_customer = customer.id

            charge = stripe.Charge.create(
                amount=bundle["cents"], currency="usd", customer=stripe_customer, description=bundle["description"]
            )

            remaining = self.get_credits_remaining()

            # create our top up
            topup = TopUp.create(
                user, price=bundle["cents"], credits=bundle["credits"], stripe_charge=charge.id, org=self
            )

            context = dict(
                description=bundle["description"],
                charge_id=charge.id,
                charge_date=timezone.now().strftime("%b %e, %Y"),
                amount=bundle["dollars"],
                credits=bundle["credits"],
                remaining=remaining,
                org=self.name,
            )

            # card
            if getattr(charge, "card", None):
                context["cc_last4"] = charge.card.last4
                context["cc_type"] = charge.card.type
                context["cc_name"] = charge.card.name

            # bitcoin
            else:
                context["cc_type"] = "bitcoin"
                context["cc_name"] = charge.source.bitcoin.address

            branding = self.get_branding()

            subject = _("%(name)s Receipt") % branding
            template = "orgs/email/receipt_email"
            to_email = user.email

            context["customer"] = user
            context["branding"] = branding
            context["subject"] = subject

            if settings.SEND_RECEIPTS:
                send_template_email(to_email, subject, template, context, branding)

            # apply our new topups
            from .tasks import apply_topups_task

            apply_topups_task.delay(self.id)

            return topup

        except stripe.error.CardError as e:
            logger.warning(f"Error adding credits to org: {str(e)}", exc_info=True)
            validation_error = _("Sorry, your card was declined, please contact your provider or try another card.")

        except Exception as e:
            logger.error(f"Error adding credits to org: {str(e)}", exc_info=True)

            validation_error = _(
                "Sorry, we were unable to process your payment, please try again later or contact us."
            )

        if validation_error is not None:
            raise ValidationError(validation_error)

    def account_value(self):
        """
        How much has this org paid to date in dollars?
        """
        paid = TopUp.objects.filter(org=self).aggregate(paid=Sum("price"))["paid"]
        if not paid:
            paid = 0
        return paid / 100

    def generate_dependency_graph(self, include_campaigns=True, include_triggers=False, include_archived=False):
        """
        Generates a dict of all exportable flows and campaigns for this org with each object's immediate dependencies
        """
        from temba.campaigns.models import Campaign, CampaignEvent
        from temba.contacts.models import ContactGroup, ContactField
        from temba.flows.models import Flow

        flow_prefetches = ("action_sets", "rule_sets")
        campaign_prefetches = (
            Prefetch(
                "events",
                queryset=CampaignEvent.objects.filter(is_active=True).exclude(flow__is_system=True),
                to_attr="flow_events",
            ),
            "flow_events__flow",
        )

        all_flows = self.flows.filter(is_active=True).exclude(is_system=True).prefetch_related(*flow_prefetches)

        if include_campaigns:
            all_campaigns = (
                self.campaigns.filter(is_active=True).select_related("group").prefetch_related(*campaign_prefetches)
            )
        else:
            all_campaigns = Campaign.objects.none()

        if not include_archived:
            all_flows = all_flows.filter(is_archived=False)
            all_campaigns = all_campaigns.filter(is_archived=False)

        # build dependency graph for all flows and campaigns
        dependencies = defaultdict(set)
        for flow in all_flows:
            dependencies[flow] = flow.get_export_dependencies()
        for campaign in all_campaigns:
            dependencies[campaign] = set([e.flow for e in campaign.flow_events])

        # replace any dependency on a group with that group's associated campaigns - we're not actually interested
        # in flow-group-flow relationships - only relationships that go through a campaign
        campaigns_by_group = defaultdict(list)
        if include_campaigns:
            for campaign in self.campaigns.filter(is_active=True).select_related("group"):
                campaigns_by_group[campaign.group].append(campaign)

        for c, deps in dependencies.items():
            if isinstance(c, Flow):
                for d in list(deps):
                    # not interested in groups or fields for now
                    if isinstance(d, ContactField):
                        deps.remove(d)
                    if isinstance(d, ContactGroup):
                        deps.remove(d)
                        deps.update(campaigns_by_group[d])

        if include_triggers:
            all_triggers = self.trigger_set.filter(is_archived=False, is_active=True).select_related("flow")
            for trigger in all_triggers:
                dependencies[trigger] = {trigger.flow}

        # make dependencies symmetric, i.e. if A depends on B, B depends on A
        for c, deps in dependencies.copy().items():
            for d in deps:
                dependencies[d].add(c)

        return dependencies

    def resolve_dependencies(
        self, flows, campaigns, include_campaigns=True, include_triggers=False, include_archived=False
    ):
        """
        Given a set of flows and and a set of campaigns, returns a new set including all dependencies
        """
        dependencies = self.generate_dependency_graph(
            include_campaigns=include_campaigns, include_triggers=include_triggers, include_archived=include_archived
        )

        primary_components = set(itertools.chain(flows, campaigns))
        all_components = set()

        def add_component(c):
            if c in all_components:
                return

            all_components.add(c)
            if c in primary_components:
                primary_components.remove(c)

            for d in dependencies[c]:
                add_component(d)

        while primary_components:
            component = next(iter(primary_components))
            add_component(component)

        return all_components

    def initialize(self, branding=None, topup_size=None):
        """
        Initializes an organization, creating all the dependent objects we need for it to work properly.
        """
        from temba.middleware import BrandingMiddleware
        from temba.contacts.models import ContactField, ContactGroup

        with transaction.atomic():
            if not branding:
                branding = BrandingMiddleware.get_branding_for_host("")

            ContactGroup.create_system_groups(self)
            ContactField.create_system_fields(self)
            self.create_welcome_topup(topup_size)
            self.update_capabilities()

        # outside of the transaction as it's going to call out to mailroom for flow validation
        self.create_sample_flows(branding.get("api_link", ""))

    def download_and_save_media(self, request, extension=None):  # pragma: needs cover
        """
        Given an HTTP Request object, downloads the file then saves it as media for the current org. If no extension
        is passed it we attempt to extract it from the filename
        """
        s = Session()
        prepped = s.prepare_request(request)
        response = s.send(prepped)

        if response.status_code == 200:
            # download the content to a temp file
            temp = NamedTemporaryFile(delete=True)
            temp.write(response.content)
            temp.flush()

            # try to derive our extension from the filename if it wasn't passed in
            if not extension:
                url_parts = urlparse(request.url)
                if url_parts.path:
                    path_pieces = url_parts.path.rsplit(".")
                    if len(path_pieces) > 1:
                        extension = path_pieces[-1]

        else:
            raise Exception(
                "Received non-200 response (%s) for request: %s" % (response.status_code, response.content)
            )

        return self.save_media(File(temp), extension)

    def get_delete_date(self, *, archive_type=Archive.TYPE_MSG):
        """
        Gets the most recent date for which data hasn't been deleted yet or None if no deletion has been done
        :return:
        """
        archive = self.archives.filter(needs_deletion=False, archive_type=archive_type).order_by("-start_date").first()
        if archive:
            return archive.get_end_date()

    def save_media(self, file, extension):
        """
        Saves the given file data with the extension and returns an absolute url to the result
        """
        random_file = str(uuid4())
        random_dir = random_file[0:4]

        filename = "%s/%s" % (random_dir, random_file)
        if extension:
            filename = "%s.%s" % (filename, extension)

        path = "%s/%d/media/%s" % (settings.STORAGE_ROOT_DIR, self.pk, filename)
        location = public_file_storage.save(path, file)

        return f"{settings.STORAGE_URL}/{location}"

    def release(self, *, release_users=True, immediately=False):

        # free our children
        Org.objects.filter(parent=self).update(parent=None)

        # deactivate ourselves
        self.is_active = False
        self.save(update_fields=("is_active", "modified_on"))

        # clear all our channel dependencies on our flows
        for flow in self.flows.all():
            flow.channel_dependencies.clear()

        # and immediately release our channels
        from temba.channels.models import Channel

        for channel in Channel.objects.filter(org=self, is_active=True):
            channel.release()

        # release any user that belongs only to us
        if release_users:
            for user in self.get_org_users():
                # check if this user is a member of any org on any brand
                other_orgs = user.get_user_orgs().exclude(id=self.id)
                if not other_orgs:
                    user.release(self.brand)

        # clear out all of our users
        self.administrators.clear()
        self.editors.clear()
        self.viewers.clear()
        self.surveyors.clear()

        if immediately:
            self._full_release()

    def _full_release(self):
        """
        Do the dirty work of deleting this org
        """

        # delete exports
        self.exportcontactstasks.all().delete()
        self.exportmessagestasks.all().delete()
        self.exportflowresultstasks.all().delete()

        for label in self.msgs_labels(manager="all_objects").all():
            label.release(self.modified_by)
            label.delete()

        msg_ids = self.msgs.all().values_list("id", flat=True)

        # might be a lot of messages, batch this
        for id_batch in chunk_list(msg_ids, 1000):
            for msg in self.msgs.filter(id__in=id_batch):
                msg.release()

        # our system label counts
        self.system_labels.all().delete()

        # delete our flow labels
        self.flow_labels.all().delete()

        # delete all our campaigns and associated events
        for c in self.campaigns.all():
            c._full_release()

        # delete everything associated with our flows
        for flow in self.flows.all():
            # we want to manually release runs so we don't fire a task to do it
            flow.release()
            flow.release_runs()

            for rev in flow.revisions.all():
                rev.release()

            flow.rule_sets.all().delete()
            flow.action_sets.all().delete()

            flow.category_counts.all().delete()
            flow.path_counts.all().delete()
            flow.node_counts.all().delete()
            flow.exit_counts.all().delete()

            flow.delete()

        # delete contact-related data
        self.sessions.all().delete()
        self.tickets.all().delete()
        self.airtime_transfers.all().delete()

        # delete our contacts
        for contact in self.contacts.all():
            contact.release(contact.modified_by, full=True, immediately=True)
            contact.delete()

        # delete all our URNs
        self.urns.all().delete()

        # delete our fields
        for contactfield in self.contactfields(manager="all_fields").all():
            contactfield.release(contactfield.modified_by)
            contactfield.delete()

        # delete our groups
        for group in self.all_groups.all():
            group.release()
            group.delete()

        # delete our channels
        for channel in self.channels.all():
            channel.release()

            channel.counts.all().delete()
            channel.logs.all().delete()

            channel.delete()

        for log in self.http_logs.all():
            log.release()

        for g in self.globals.all():
            g.release()

        # delete our classifiers
        for classifier in self.classifiers.all():
            classifier.release()
            classifier.delete()

        # delete our ticketers
        for ticketer in self.ticketers.all():
            ticketer.release()
            ticketer.delete()

        # release all archives objects and files for this org
        Archive.release_org_archives(self)

        # return any unused credits to our parent
        if self.parent:
            self.allocate_credits(self.modified_by, self.parent, self.get_credits_remaining())

        for topup in self.topups.all():
            topup.release()

        for result in self.webhook_results.all():
            result.release()

        for event in self.webhookevent_set.all():
            event.release()

        for resthook in self.resthooks.all():
            resthook.release(self.modified_by)
            for sub in resthook.subscribers.all():
                sub.delete()
            resthook.delete()

        # delete org languages
        Org.objects.filter(id=self.id).update(primary_language=None)
        self.languages.all().delete()

        # delete other related objects
        self.api_tokens.all().delete()
        self.invitations.all().delete()
        self.credit_alerts.all().delete()
        self.broadcast_set.all().delete()
        self.schedules.all().delete()
        self.boundaryalias_set.all().delete()

        # needs to come after deletion of msgs and broadcasts as those insert new counts
        self.system_labels.all().delete()

        # now what we've all been waiting for
        self.delete()

    @classmethod
    def create_user(cls, email, password):
        user = User.objects.create_user(username=email, email=email, password=password)
        return user

    @classmethod
    def get_org(cls, user):
        if not user:  # pragma: needs cover
            return None

        if not hasattr(user, "_org"):
            org = Org.objects.filter(administrators=user, is_active=True).first()
            if org:
                user._org = org

        return getattr(user, "_org", None)

    def as_environment_def(self):
        """
        Returns this org as an environment definition as used by the flow engine
        """

        return {
            "date_format": "DD-MM-YYYY" if self.date_format == Org.DATE_FORMAT_DAY_FIRST else "MM-DD-YYYY",
            "time_format": "tt:mm",
            "timezone": str(self.timezone),
            "default_language": self.primary_language.iso_code if self.primary_language else None,
            "allowed_languages": list(self.get_language_codes()),
            "default_country": self.get_country_code(),
            "redaction_policy": "urns" if self.is_anon else "none",
        }

    def __str__(self):
        return self.name


# ===================== monkey patch User class with a few extra functions ========================


def release(user, brand):

    # if our user exists across brands don't muck with the user
    if user.get_user_orgs().order_by("brand").distinct("brand").count() < 2:
        user_uuid = str(uuid4())
        user.first_name = ""
        user.last_name = ""
        user.email = f"{user_uuid}@rapidpro.io"
        user.username = f"{user_uuid}@rapidpro.io"
        user.password = ""
        user.is_active = False
        user.save()

    # release any orgs we own on this brand
    for org in user.get_owned_orgs([brand]):
        org.release(release_users=False)

    # remove us as a user on any org for our brand
    for org in user.get_user_orgs([brand]):
        org.administrators.remove(user)
        org.editors.remove(user)
        org.viewers.remove(user)
        org.surveyors.remove(user)


def get_user_orgs(user, brands=None):
    if user.is_superuser:
        return Org.objects.all()

    user_orgs = user.org_admins.all() | user.org_editors.all() | user.org_viewers.all() | user.org_surveyors.all()

    if brands:
        user_orgs = user_orgs.filter(brand__in=brands)

    return user_orgs.filter(is_active=True).distinct().order_by("name")


def get_owned_orgs(user, brands=None):
    """
    Gets all the orgs where this is the only user for the current brand
    """
    owned_orgs = []
    for org in user.get_user_orgs(brands=brands):
        if not org.get_org_users().exclude(id=user.id).exists():
            owned_orgs.append(org)
    return owned_orgs


def get_org(obj):
    return getattr(obj, "_org", None)


def is_alpha_user(user):  # pragma: needs cover
    return user.groups.filter(name="Alpha").exists()


def is_beta_user(user):  # pragma: needs cover
    return user.groups.filter(name="Beta").exists()


def is_support_user(user):
    return user.groups.filter(name="Customer Support").exists()


def get_settings(user):
    if not user:  # pragma: needs cover
        return None

    settings = UserSettings.objects.filter(user=user).first()

    if not settings:
        settings = UserSettings.objects.create(user=user)

    return settings


def set_org(obj, org):
    obj._org = org


def get_org_group(obj):
    org_group = None
    org = obj.get_org()
    if org:
        org_group = org.get_user_org_group(obj)
    return org_group


def _user_has_org_perm(user, org, permission):
    """
    Determines if a user has the given permission in this org
    """
    if user.is_superuser:  # pragma: needs cover
        return True

    if user.is_anonymous:  # pragma: needs cover
        return False

    # has it innately? (customer support)
    if user.has_perm(permission):  # pragma: needs cover
        return True

    org_group = org.get_user_org_group(user)

    if not org_group:  # pragma: needs cover
        return False

    (app_label, codename) = permission.split(".")

    return org_group.permissions.filter(content_type__app_label=app_label, codename=codename).exists()


User.release = release
User.get_org = get_org
User.set_org = set_org
User.is_alpha = is_alpha_user
User.is_beta = is_beta_user
User.is_support = is_support_user
User.get_settings = get_settings
User.get_user_orgs = get_user_orgs
User.get_org_group = get_org_group
User.get_owned_orgs = get_owned_orgs
User.has_org_perm = _user_has_org_perm

USER_GROUPS = (("A", _("Administrator")), ("E", _("Editor")), ("V", _("Viewer")), ("S", _("Surveyor")))


def get_stripe_credentials():
    public_key = os.environ.get(
        "STRIPE_PUBLIC_KEY", getattr(settings, "STRIPE_PUBLIC_KEY", "MISSING_STRIPE_PUBLIC_KEY")
    )
    private_key = os.environ.get(
        "STRIPE_PRIVATE_KEY", getattr(settings, "STRIPE_PRIVATE_KEY", "MISSING_STRIPE_PRIVATE_KEY")
    )
    return (public_key, private_key)


class Language(SmartModel):
    """
    A Language that has been added to the org. In the end and language is just an iso_code and name
    and it is not really restricted to real-world languages at this level. Instead we restrict the
    language selection options to real-world languages.
    """

    name = models.CharField(max_length=128)

    iso_code = models.CharField(max_length=4)

    org = models.ForeignKey(Org, on_delete=models.PROTECT, verbose_name=_("Org"), related_name="languages")

    @classmethod
    def create(cls, org, user, name, iso_code):
        return cls.objects.create(org=org, name=name, iso_code=iso_code, created_by=user, modified_by=user)

    def as_json(self):  # pragma: needs cover
        return dict(name=self.name, iso_code=self.iso_code)

    @classmethod
    def get_localized_text(cls, text_translations, preferred_languages, default_text=""):
        """
        Returns the appropriate translation to use.
        :param text_translations: A dictionary (or plain text) which contains our message indexed by language iso code
        :param preferred_languages: The prioritized list of language preferences (list of iso codes)
        :param default_text: default text to use if no match is found
        """
        # No translations, return our default text
        if not text_translations:
            return default_text

        # If we are handed raw text without translations, just return that
        if not isinstance(text_translations, dict):  # pragma: no cover
            return text_translations

        # otherwise, find the first preferred language
        for lang in preferred_languages:
            localized = text_translations.get(lang)
            if localized is not None:
                return localized

        return default_text

    def __str__(self):  # pragma: needs cover
        return "%s" % self.name


class Invitation(SmartModel):
    """
    An Invitation to an e-mail address to join an Org with specific roles.
    """

    org = models.ForeignKey(
        Org,
        on_delete=models.PROTECT,
        verbose_name=_("Org"),
        related_name="invitations",
        help_text=_("The organization to which the account is invited to view"),
    )

    email = models.EmailField(
        verbose_name=_("Email"), help_text=_("The email to which we send the invitation of the viewer")
    )

    secret = models.CharField(
        verbose_name=_("Secret"),
        max_length=64,
        unique=True,
        help_text=_("a unique code associated with this invitation"),
    )

    user_group = models.CharField(max_length=1, choices=USER_GROUPS, default="V", verbose_name=_("User Role"))

    @classmethod
    def create(cls, org, user, email, user_group):
        return cls.objects.create(org=org, email=email, user_group=user_group, created_by=user, modified_by=user)

    def save(self, *args, **kwargs):
        if not self.secret:
            secret = random_string(64)

            while Invitation.objects.filter(secret=secret):  # pragma: needs cover
                secret = random_string(64)

            self.secret = secret

        return super().save(*args, **kwargs)

    def send_invitation(self):
        from .tasks import send_invitation_email_task

        send_invitation_email_task(self.id)

    def send_email(self):
        # no=op if we do not know the email
        if not self.email:  # pragma: needs cover
            return

        branding = self.org.get_branding()
        subject = _("%(name)s Invitation") % branding
        template = "orgs/email/invitation_email"
        to_email = self.email

        context = dict(org=self.org, now=timezone.now(), branding=branding, invitation=self)
        context["subject"] = subject

        send_template_email(to_email, subject, template, context, branding)


class UserSettings(models.Model):
    """
    User specific configuration
    """

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="settings")
    language = models.CharField(
        max_length=8, choices=settings.LANGUAGES, default="en-us", help_text=_("Your preferred language")
    )
    tel = models.CharField(
        verbose_name=_("Phone Number"),
        max_length=16,
        null=True,
        blank=True,
        help_text=_("Phone number for testing and recording voice flows"),
    )
    otp_secret = models.CharField(verbose_name=_("OTP Secret"), max_length=18, null=True, blank=True)
    two_factor_enabled = models.BooleanField(verbose_name=_("Two Factor Enabled"), default=False)

    def get_tel_formatted(self):
        if self.tel:
            import phonenumbers

            normalized = phonenumbers.parse(self.tel, None)
            return phonenumbers.format_number(normalized, phonenumbers.PhoneNumberFormat.INTERNATIONAL)


class TopUp(SmartModel):
    """
    TopUps are used to track usage across the platform. Each TopUp represents a certain number of
    credits that can be consumed by messages.
    """

    org = models.ForeignKey(
        Org, on_delete=models.PROTECT, related_name="topups", help_text="The organization that was toppped up"
    )
    price = models.IntegerField(
        null=True,
        blank=True,
        verbose_name=_("Price Paid"),
        help_text=_("The price paid for the messages in this top up (in cents)"),
    )
    credits = models.IntegerField(
        verbose_name=_("Number of Credits"), help_text=_("The number of credits bought in this top up")
    )
    expires_on = models.DateTimeField(
        verbose_name=_("Expiration Date"), help_text=_("The date that this top up will expire")
    )
    stripe_charge = models.CharField(
        verbose_name=_("Stripe Charge Id"),
        max_length=32,
        null=True,
        blank=True,
        help_text=_("The Stripe charge id for this charge"),
    )
    comment = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Any comment associated with this topup, used when we credit accounts",
    )

    @classmethod
    def create(cls, user, price, credits, stripe_charge=None, org=None, expires_on=None):
        """
        Creates a new topup
        """
        if not org:
            org = user.get_org()

        if not expires_on:
            expires_on = timezone.now() + timedelta(days=365)  # credits last 1 year

        topup = TopUp.objects.create(
            org=org,
            price=price,
            credits=credits,
            expires_on=expires_on,
            stripe_charge=stripe_charge,
            created_by=user,
            modified_by=user,
        )

        org.clear_credit_cache()
        return topup

    def release(self):

        # clear us off any debits we are connected to
        Debit.objects.filter(topup=self).update(topup=None)

        # any debits benefitting us are deleted
        Debit.objects.filter(beneficiary=self).delete()

        # remove any credits associated with us
        TopUpCredits.objects.filter(topup=self)

        for used in TopUpCredits.objects.filter(topup=self):
            used.release()

        self.delete()

    def get_ledger(self):  # pragma: needs cover
        debits = self.debits.filter(debit_type=Debit.TYPE_ALLOCATION).order_by("-created_by")
        balance = self.credits
        ledger = []

        active = self.get_remaining() < balance

        if active:
            transfer = self.allocations.all().first()

            if transfer:
                comment = _("Transfer from %s" % transfer.topup.org.name)
            else:
                price = -1 if self.price is None else self.price

                if price > 0:
                    comment = _("Purchased Credits")
                elif price == 0:
                    comment = _("Complimentary Credits")
                else:
                    comment = _("Credits")

            ledger.append(dict(date=self.created_on, comment=comment, amount=self.credits, balance=self.credits))

        for debit in debits:  # pragma: needs cover
            balance -= debit.amount
            ledger.append(
                dict(
                    date=debit.created_on,
                    comment=_("Transfer to %(org)s") % dict(org=debit.beneficiary.org.name),
                    amount=-debit.amount,
                    balance=balance,
                )
            )

        now = timezone.now()
        expired = self.expires_on < now

        # add a line for used message credits
        if active:
            ledger.append(
                dict(
                    date=self.expires_on if expired else now,
                    comment=_("Messaging credits used"),
                    amount=self.get_remaining() - balance,
                    balance=self.get_remaining(),
                )
            )

        # add a line for expired credits
        if expired and self.get_remaining() > 0:
            ledger.append(
                dict(date=self.expires_on, comment=_("Expired credits"), amount=-self.get_remaining(), balance=0)
            )
        return ledger

    def get_price_display(self):
        if self.price is None:
            return ""
        elif self.price == 0:
            return _("Free")

        return "$%.2f" % self.dollars()

    def dollars(self):
        if self.price == 0:  # pragma: needs cover
            return 0
        else:
            return Decimal(self.price) / Decimal(100)

    def revert_topup(self):  # pragma: needs cover
        # unwind any items that were assigned to this topup
        self.msgs.update(topup=None)

        # mark this topup as inactive
        self.is_active = False
        self.save()

    def get_stripe_charge(self):  # pragma: needs cover
        try:
            stripe.api_key = get_stripe_credentials()[1]
            return stripe.Charge.retrieve(self.stripe_charge)
        except Exception as e:
            logger.error(f"Could not get Stripe charge: {str(e)}", exc_info=True)
            return None

    def get_used(self):
        """
        Calculates how many topups have actually been used
        """
        used = TopUpCredits.objects.filter(topup=self).aggregate(used=Sum("used"))
        return 0 if not used["used"] else used["used"]

    def get_remaining(self):
        """
        Returns how many credits remain on this topup
        """
        return self.credits - self.get_used()

    def __str__(self):  # pragma: needs cover
        return f"{self.credits} Credits"


class Debit(models.Model):
    """
    Transactional history of credits allocated to other topups or chunks of archived messages
    """

    TYPE_ALLOCATION = "A"

    DEBIT_TYPES = ((TYPE_ALLOCATION, "Allocation"),)

    id = models.BigAutoField(auto_created=True, primary_key=True, verbose_name="ID")

    topup = models.ForeignKey(
        TopUp,
        on_delete=models.PROTECT,
        null=True,
        related_name="debits",
        help_text=_("The topup these credits are applied against"),
    )

    amount = models.IntegerField(help_text=_("How many credits were debited"))

    beneficiary = models.ForeignKey(
        TopUp,
        on_delete=models.PROTECT,
        null=True,
        related_name="allocations",
        help_text=_("Optional topup that was allocated with these credits"),
    )

    debit_type = models.CharField(max_length=1, choices=DEBIT_TYPES, null=False, help_text=_("What caused this debit"))

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        related_name="debits_created",
        help_text="The user which originally created this item",
    )
    created_on = models.DateTimeField(default=timezone.now, help_text="When this item was originally created")


class TopUpCredits(SquashableModel):
    """
    Used to track number of credits used on a topup, mostly maintained by triggers on Msg insertion.
    """

    SQUASH_OVER = ("topup_id",)

    topup = models.ForeignKey(
        TopUp, on_delete=models.PROTECT, help_text=_("The topup these credits are being used against")
    )
    used = models.IntegerField(help_text=_("How many credits were used, can be negative"))

    def release(self):
        self.delete()

    def __str__(self):  # pragma: no cover
        return f"{self.topup} (Used: {self.used})"

    @classmethod
    def get_squash_query(cls, distinct_set):
        sql = """
        WITH deleted as (
            DELETE FROM %(table)s WHERE "topup_id" = %%s RETURNING "used"
        )
        INSERT INTO %(table)s("topup_id", "used", "is_squashed")
        VALUES (%%s, GREATEST(0, (SELECT SUM("used") FROM deleted)), TRUE);
        """ % {
            "table": cls._meta.db_table
        }

        return sql, (distinct_set.topup_id,) * 2


class CreditAlert(SmartModel):
    """
    Tracks when we have sent alerts to organization admins about low credits.
    """

    TYPE_OVER = "O"
    TYPE_LOW = "L"
    TYPE_EXPIRING = "E"
    TYPES = ((TYPE_OVER, _("Credits Over")), (TYPE_LOW, _("Low Credits")), (TYPE_EXPIRING, _("Credits expiring soon")))

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="credit_alerts")

    alert_type = models.CharField(max_length=1, choices=TYPES)

    @classmethod
    def trigger_credit_alert(cls, org, alert_type):
        # don't create a new alert if there is already an alert of this type for the org
        if org.credit_alerts.filter(is_active=True, alert_type=alert_type).exists():
            return

        logging.info(f"triggering {alert_type} credits alert type for {org.name}")

        admin = org.get_org_admins().first()

        if admin:
            # Otherwise, create our alert objects and trigger our event
            alert = CreditAlert.objects.create(org=org, alert_type=alert_type, created_by=admin, modified_by=admin)

            alert.send_alert()

    def send_alert(self):
        from .tasks import send_alert_email_task

        send_alert_email_task(self.id)

    def send_email(self):
        admin_emails = [admin.email for admin in self.org.get_org_admins().order_by("email")]

        if len(admin_emails) == 0:
            return

        branding = self.org.get_branding()
        subject = _("%(name)s Credits Alert") % branding
        template = "orgs/email/alert_email"
        to_email = admin_emails

        context = dict(org=self.org, now=timezone.now(), branding=branding, alert=self, customer=self.created_by)
        context["subject"] = subject

        send_template_email(to_email, subject, template, context, branding)

    @classmethod
    def reset_for_org(cls, org):
        org.credit_alerts.filter(is_active=True).update(is_active=False)

    @classmethod
    def check_org_credits(cls):
        from temba.msgs.models import Msg

        # all active orgs in the last hour
        active_orgs = Msg.objects.filter(created_on__gte=timezone.now() - timedelta(hours=1))
        active_orgs = active_orgs.order_by("org").distinct("org")

        for msg in active_orgs:
            org = msg.org

            # does this org have less than 0 messages?
            org_remaining_credits = org.get_credits_remaining()
            org_low_credits = org.has_low_credits()

            if org_remaining_credits <= 0:
                CreditAlert.trigger_credit_alert(org, CreditAlert.TYPE_OVER)
            elif org_low_credits:  # pragma: needs cover
                CreditAlert.trigger_credit_alert(org, CreditAlert.TYPE_LOW)

    @classmethod
    def check_topup_expiration(cls):
        """
        Triggers an expiring credit alert for any org that has its last
        active topup expiring in the next 30 days and still has available credits
        """

        # get the ids of the last to expire topup, with credits, for each org
        final_topups = (
            TopUp.objects.filter(is_active=True, org__is_active=True, credits__gt=0)
            .order_by("org_id", "-expires_on")
            .distinct("org_id")
            .values_list("id", flat=True)
        )

        # figure out which of those have credits remaining, and will expire in next 30 days
        expiring_final_topups = (
            TopUp.objects.filter(id__in=final_topups)
            .annotate(used_credits=Sum("topupcredits__used"))
            .filter(expires_on__gt=timezone.now(), expires_on__lte=(timezone.now() + timedelta(days=30)))
            .filter(Q(used_credits__lt=F("credits")) | Q(used_credits=None))
            .select_related("org")
        )

        for topup in expiring_final_topups:
            CreditAlert.trigger_credit_alert(topup.org, CreditAlert.TYPE_EXPIRING)


class BackupToken(SmartModel):
    settings = models.ForeignKey(
        UserSettings, verbose_name=_("Settings"), related_name="backups", on_delete=models.CASCADE
    )
    token = models.CharField(verbose_name=_("Token"), max_length=18, unique=True, default=generate_token)
    used = models.BooleanField(verbose_name=_("Used"), default=False)

    def __str__(self):  # pragma: no cover
        return f"{self.token}"


class OrgActivity(models.Model):
    """
    Tracks various metrics for an organization on a daily basis:
       * total # of contacts
       * total # of active contacts (that sent or received a message)
       * total # of messages sent
       * total # of message received
    """

    # the org this contact activity is being tracked for
    org = models.ForeignKey("orgs.Org", related_name="contact_activity", on_delete=models.CASCADE)

    # the day this activity was tracked for
    day = models.DateField()

    # the total number of contacts on this day
    contact_count = models.IntegerField(default=0)

    # the number of active contacts on this day
    active_contact_count = models.IntegerField(default=0)

    # the number of messages sent on this day
    outgoing_count = models.IntegerField(default=0)

    # the number of messages received on this day
    incoming_count = models.IntegerField(default=0)

    @classmethod
    def update_day(cls, now):
        """
        Updates our org activity for the passed in day.
        """
        # truncate to midnight the same day in UTC
        end = pytz.utc.normalize(now.astimezone(pytz.utc)).replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=1)

        # first get all our contact counts
        contact_counts = Org.objects.filter(
            is_active=True, contacts__is_active=True, contacts__created_on__lt=end
        ).annotate(contact_count=Count("contacts"))

        # then get active contacts
        active_counts = Org.objects.filter(
            is_active=True, msgs__created_on__gte=start, msgs__created_on__lt=end
        ).annotate(contact_count=Count("msgs__contact_id", distinct=True))
        active_counts = {o.id: o.contact_count for o in active_counts}

        # number of received msgs
        incoming_count = Org.objects.filter(
            is_active=True, msgs__created_on__gte=start, msgs__created_on__lt=end, msgs__direction="I"
        ).annotate(msg_count=Count("id"))
        incoming_count = {o.id: o.msg_count for o in incoming_count}

        # number of sent messages
        outgoing_count = Org.objects.filter(
            is_active=True, msgs__created_on__gte=start, msgs__created_on__lt=end, msgs__direction="O"
        ).annotate(msg_count=Count("id"))
        outgoing_count = {o.id: o.msg_count for o in outgoing_count}

        for org in contact_counts:
            OrgActivity.objects.update_or_create(
                org=org,
                day=start,
                contact_count=org.contact_count,
                active_contact_count=active_counts.get(org.id, 0),
                incoming_count=incoming_count.get(org.id, 0),
                outgoing_count=outgoing_count.get(org.id, 0),
            )

    class Meta:
        unique_together = ("org", "day")
