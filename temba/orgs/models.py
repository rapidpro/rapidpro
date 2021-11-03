import functools
import itertools
import logging
import operator
import os
from abc import ABCMeta
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from urllib.parse import quote, urlencode, urlparse

import pycountry
import pyotp
import pytz
import stripe
import stripe.error
from django_redis import get_redis_connection
from packaging.version import Version
from requests import Session
from smartmin.models import SmartModel
from timezone_field import TimeZoneField
from twilio.rest import Client as TwilioClient

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.contrib.postgres.fields import ArrayField
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
from temba.locations.models import AdminBoundary
from temba.utils import chunk_list, json, languages
from temba.utils.cache import get_cacheable_result
from temba.utils.dates import datetime_to_str
from temba.utils.email import send_template_email
from temba.utils.models import JSONAsTextField, JSONField, SquashableModel
from temba.utils.s3 import public_file_storage
from temba.utils.text import generate_token, random_string
from temba.utils.timezones import timezone_to_country_code
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


class DependencyMixin:
    """
    Utility mixin for models which can be flow dependencies
    """

    soft_dependent_types = {"flow"}

    def get_dependents(self):
        return {"flow": self.dependent_flows.filter(is_active=True)}

    def release(self, user):
        """
        Mark this dependency's flows as having issues, and then remove the dependencies
        """

        for dep_type, deps in self.get_dependents().items():
            if dep_type not in self.soft_dependent_types and deps.exists():
                raise AssertionError(f"can't delete {self} that still has {dep_type} dependents")

        self.dependent_flows.update(has_issues=True)
        self.dependent_flows.clear()


class IntegrationType(metaclass=ABCMeta):
    """
    IntegrationType is our abstract base type for third party integrations.
    """

    class Category(Enum):
        CHANNELS = 1
        EMAIL = 2
        AIRTIME = 3
        MONITORING = 4

    # the verbose name for this type
    name = None

    # the short code for this type (< 16 chars, lowercase)
    slug = None

    # the icon to show for this type
    icon = "icon-plug"

    # the category of features this integration provides
    category = None

    def is_available_to(self, user) -> bool:
        """
        Determines whether this integration type is available to the given user
        """
        return True

    def is_connected(self, org) -> bool:
        """
        Returns whether the given org is connected to this integration
        """

    def disconnect(self, org, user):
        """
        Disconnects this integration on the given org
        """

    def management_ui(self, org, formax):
        """
        Adds formax sections to provide a UI to manage this integration
        """

    def get_urls(self) -> list:
        """
        Returns the urls and views for this integration
        """

    @classmethod
    def get_all(cls, category: Category = None) -> list:
        """
        Returns all possible types with the given category
        """
        from .integrations import TYPES

        return [t for t in TYPES.values() if not category or t.category == category]


class OrgRole(Enum):
    ADMINISTRATOR = ("A", _("Administrator"), _("Administrators"), "Administrators", "administrators", "org_admins")
    EDITOR = ("E", _("Editor"), _("Editors"), "Editors", "editors", "org_editors")
    VIEWER = ("V", _("Viewer"), _("Viewers"), "Viewers", "viewers", "org_viewers")
    AGENT = ("T", _("Agent"), _("Agents"), "Agents", "agents", "org_agents")
    SURVEYOR = ("S", _("Surveyor"), _("Surveyors"), "Surveyors", "surveyors", "org_surveyors")

    def __init__(self, code: str, display: str, display_plural: str, group_name: str, m2m_name: str, rel_name: str):
        self.code = code
        self.display = display
        self.display_plural = display_plural
        self.group_name = group_name
        self.m2m_name = m2m_name
        self.rel_name = rel_name

    @classmethod
    def from_code(cls, code: str):
        for role in cls:
            if role.code == code:
                return role
        return None

    @classmethod
    def from_group(cls, group: Group):
        for role in cls:
            if role.group == group:
                return role
        return None

    @cached_property
    def group(self):
        """
        Gets the auth group which defines the permissions for this role
        """
        return Group.objects.get(name=self.group_name)

    def get_users(self, org):
        """
        The users with this role in the given org
        """
        return getattr(org, self.m2m_name).all()

    def get_orgs(self, user):
        """
        The orgs which the given user belongs to with this role
        """
        return getattr(user, self.rel_name).all()


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
    DATE_FORMAT_YEAR_FIRST = "Y"
    DATE_FORMAT_CHOICES = (
        (DATE_FORMAT_DAY_FIRST, "DD-MM-YYYY"),
        (DATE_FORMAT_MONTH_FIRST, "MM-DD-YYYY"),
        (DATE_FORMAT_YEAR_FIRST, "YYYY-MM-DD"),
    )

    DATE_FORMATS_PYTHON = {
        DATE_FORMAT_DAY_FIRST: "%d-%m-%Y",
        DATE_FORMAT_MONTH_FIRST: "%m-%d-%Y",
        DATE_FORMAT_YEAR_FIRST: "%Y-%m-%d",
    }

    DATE_FORMATS_ENGINE = {
        DATE_FORMAT_DAY_FIRST: "DD-MM-YYYY",
        DATE_FORMAT_MONTH_FIRST: "MM-DD-YYYY",
        DATE_FORMAT_YEAR_FIRST: "YYYY-MM-DD",
    }

    CONFIG_VERIFIED = "verified"
    CONFIG_SMTP_SERVER = "smtp_server"
    CONFIG_TWILIO_SID = "ACCOUNT_SID"
    CONFIG_TWILIO_TOKEN = "ACCOUNT_TOKEN"
    CONFIG_VONAGE_KEY = "NEXMO_KEY"
    CONFIG_VONAGE_SECRET = "NEXMO_SECRET"

    EARLIEST_IMPORT_VERSION = "3"
    CURRENT_EXPORT_VERSION = "13"

    LIMIT_FIELDS = "fields"
    LIMIT_GLOBALS = "globals"
    LIMIT_GROUPS = "groups"
    LIMIT_LABELS = "labels"
    LIMIT_TOPICS = "topics"

    DELETE_DELAY_DAYS = 7  # how many days after releasing that an org is deleted

    BLOCKER_SUSPENDED = _(
        "Sorry, your workspace is currently suspended. To re-enable starting flows and sending messages, please "
        "contact support."
    )
    BLOCKER_FLAGGED = _(
        "Sorry, your workspace is currently flagged. To re-enable starting flows and sending messages, please "
        "contact support."
    )

    uuid = models.UUIDField(unique=True, default=uuid4)

    name = models.CharField(verbose_name=_("Name"), max_length=128)
    plan = models.CharField(
        verbose_name=_("Plan"),
        max_length=16,
        default=settings.DEFAULT_PLAN,
        help_text=_("What plan your organization is on"),
    )
    plan_start = models.DateTimeField(null=True)
    plan_end = models.DateTimeField(null=True)

    stripe_customer = models.CharField(
        verbose_name=_("Stripe Customer"),
        max_length=32,
        null=True,
        blank=True,
        help_text=_("Our Stripe customer id for your organization"),
    )

    # user role m2ms
    administrators = models.ManyToManyField(User, related_name=OrgRole.ADMINISTRATOR.rel_name)
    editors = models.ManyToManyField(User, related_name=OrgRole.EDITOR.rel_name)
    viewers = models.ManyToManyField(User, related_name=OrgRole.VIEWER.rel_name)
    agents = models.ManyToManyField(User, related_name=OrgRole.AGENT.rel_name)
    surveyors = models.ManyToManyField(User, related_name=OrgRole.SURVEYOR.rel_name)

    language = models.CharField(
        verbose_name=_("Default Language"),
        max_length=64,
        null=True,
        choices=settings.LANGUAGES,
        default=settings.DEFAULT_LANGUAGE,
        help_text=_("The default website language for new users."),
    )

    timezone = TimeZoneField(verbose_name=_("Timezone"))

    date_format = models.CharField(
        verbose_name=_("Date Format"),
        max_length=1,
        choices=DATE_FORMAT_CHOICES,
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

    limits = JSONField(default=dict)

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

    flow_languages = ArrayField(models.CharField(max_length=3), default=list)

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

    # when this org was released and when it was actually deleted
    released_on = models.DateTimeField(null=True)
    deleted_on = models.DateTimeField(null=True)

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
            plan = brand.get("default_plan", settings.DEFAULT_PLAN)

            # if parent are on topups keep using those
            if self.plan == settings.TOPUP_PLAN:
                plan = settings.TOPUP_PLAN

            org = Org.objects.create(
                name=name,
                timezone=timezone,
                language=self.language,
                flow_languages=self.flow_languages,
                brand=self.brand,
                parent=self,
                slug=slug,
                created_by=created_by,
                modified_by=created_by,
                plan=plan,
                is_multi_user=self.is_multi_user,
                is_multi_org=self.is_multi_org,
            )

            org.add_user(created_by, OrgRole.ADMINISTRATOR)

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

        counts = ContactGroup.get_system_group_counts(self, (ContactGroup.TYPE_ACTIVE, ContactGroup.TYPE_BLOCKED))
        return (counts[ContactGroup.TYPE_ACTIVE] + counts[ContactGroup.TYPE_BLOCKED]) > 0

    def get_integrations(self, category: IntegrationType.Category) -> list:
        """
        Returns the connected integrations on this org of the given category
        """

        return [t for t in IntegrationType.get_all(category) if t.is_connected(self)]

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

    def get_limit(self, limit_type):
        return int(self.limits.get(limit_type, settings.ORG_LIMIT_DEFAULTS.get(limit_type)))

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

    def import_app(self, export_json, user, site=None):
        """
        Imports previously exported JSON
        """

        from temba.campaigns.models import Campaign
        from temba.contacts.models import ContactField, ContactGroup
        from temba.flows.models import Flow
        from temba.triggers.models import Trigger

        # only required field is version
        if "version" not in export_json:
            raise ValueError("Export missing version field")

        export_version = Version(str(export_json["version"]))
        export_site = export_json.get("site")

        # determine if this app is being imported from the same site
        same_site = False
        if export_site and site:
            same_site = urlparse(export_site).netloc == urlparse(site).netloc

        # do we have a supported export version?
        if not (Version(Org.EARLIEST_IMPORT_VERSION) <= export_version <= Version(Org.CURRENT_EXPORT_VERSION)):
            raise ValueError(f"Unsupported export version {export_version}")

        # do we need to migrate the export forward?
        if export_version < Version(Flow.CURRENT_SPEC_VERSION):
            export_json = Flow.migrate_export(self, export_json, same_site, export_version)

        self.validate_import(export_json)

        export_fields = export_json.get("fields", [])
        export_groups = export_json.get("groups", [])
        export_campaigns = export_json.get("campaigns", [])
        export_triggers = export_json.get("triggers", [])

        dependency_mapping = {}  # dependency UUIDs in import => new UUIDs

        with transaction.atomic():
            ContactField.import_fields(self, user, export_fields)
            ContactGroup.import_groups(self, user, export_groups, dependency_mapping)

            new_flows = Flow.import_flows(self, user, export_json, dependency_mapping, same_site)

            # these depend on flows so are imported last
            new_campaigns = Campaign.import_campaigns(self, user, export_campaigns, same_site)
            Trigger.import_triggers(self, user, export_triggers, same_site)

        # queue mailroom tasks to schedule campaign events
        for campaign in new_campaigns:
            campaign.schedule_events_async()

        # with all the flows and dependencies committed, we can now have mailroom do full validation
        for flow in new_flows:
            flow_info = mailroom.get_client().flow_inspect(self.id, flow.get_definition())
            flow.has_issues = len(flow_info[Flow.INSPECT_ISSUES]) > 0
            flow.save(update_fields=("has_issues",))

    def validate_import(self, import_def):
        from temba.triggers.models import Trigger

        for trigger_def in import_def.get("triggers", []):
            Trigger.validate_import_def(trigger_def)

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
                exported_flows.append(component.get_definition())

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
                if component.type.exportable:
                    exported_triggers.append(component.as_export_def())
                    if include_groups:
                        groups.update(component.groups.all())

        return {
            "version": Org.CURRENT_EXPORT_VERSION,
            "site": site_link,
            "flows": exported_flows,
            "campaigns": exported_campaigns,
            "triggers": exported_triggers,
            "fields": [f.as_export_def() for f in sorted(fields, key=lambda f: f.key)],
            "groups": [g.as_export_def() for g in sorted(groups, key=lambda g: g.name)],
        }

    def can_add_sender(self):  # pragma: needs cover
        """
        If an org's telephone send channel is an Android device, let them add a bulk sender
        """
        from temba.contacts.models import URN

        send_channel = self.get_send_channel(URN.TEL_SCHEME)
        return send_channel and send_channel.is_android()

    def can_add_caller(self):  # pragma: needs cover
        return not self.supports_ivr() and self.is_connected_to_twilio()

    def supports_ivr(self):
        return self.get_call_channel() or self.get_answer_channel()

    def get_channel(self, role: str, scheme: str):
        """
        Gets a channel for this org which supports the given role and scheme
        """
        from temba.channels.models import Channel

        channels = self.channels.filter(is_active=True, role__contains=role).order_by("-id")

        if scheme is not None:
            channels = channels.filter(schemes__contains=[scheme])

        channel = channels.first()

        if channel and (role == Channel.ROLE_SEND or role == Channel.ROLE_CALL):
            return channel.get_delegate(role)
        else:
            return channel

    def get_send_channel(self, scheme=None):
        from temba.channels.models import Channel

        return self.get_channel(Channel.ROLE_SEND, scheme=scheme)

    def get_receive_channel(self, scheme=None):
        from temba.channels.models import Channel

        return self.get_channel(Channel.ROLE_RECEIVE, scheme=scheme)

    def get_call_channel(self):
        from temba.contacts.models import URN
        from temba.channels.models import Channel

        return self.get_channel(Channel.ROLE_CALL, scheme=URN.TEL_SCHEME)

    def get_answer_channel(self):
        from temba.contacts.models import URN
        from temba.channels.models import Channel

        return self.get_channel(Channel.ROLE_ANSWER, scheme=URN.TEL_SCHEME)

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

    @cached_property
    def active_contacts_group(self):
        from temba.contacts.models import ContactGroup

        return self.all_groups(manager="system_groups").get(group_type=ContactGroup.TYPE_ACTIVE)

    @cached_property
    def default_ticket_topic(self):
        return self.topics.get(is_default=True)

    def get_resthooks(self):
        """
        Returns the resthooks configured on this Org
        """
        return self.resthooks.filter(is_active=True).order_by("slug")

    @classmethod
    def get_possible_countries(cls):
        return AdminBoundary.objects.filter(level=0).order_by("name")

    def trigger_send(self):
        """
        Triggers either our Android channels to sync, or for all our pending messages to be queued
        to send.
        """

        from temba.channels.models import Channel
        from temba.channels.types.android import AndroidType
        from temba.msgs.models import Msg

        # sync all pending channels
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

    def connect_vonage(self, api_key, api_secret, user):
        self.config.update({Org.CONFIG_VONAGE_KEY: api_key.strip(), Org.CONFIG_VONAGE_SECRET: api_secret.strip()})
        self.modified_by = user
        self.save(update_fields=("config", "modified_by", "modified_on"))

    def connect_twilio(self, account_sid, account_token, user):
        self.config.update({Org.CONFIG_TWILIO_SID: account_sid, Org.CONFIG_TWILIO_TOKEN: account_token})
        self.modified_by = user
        self.save(update_fields=("config", "modified_by", "modified_on"))

    def is_connected_to_vonage(self):
        if self.config:
            return self.config.get(Org.CONFIG_VONAGE_KEY) and self.config.get(Org.CONFIG_VONAGE_SECRET)
        return False

    def is_connected_to_twilio(self):
        if self.config:
            return self.config.get(Org.CONFIG_TWILIO_SID) and self.config.get(Org.CONFIG_TWILIO_TOKEN)
        return False

    def remove_vonage_account(self, user):
        if self.config:
            # release any vonage channels
            for channel in self.channels.filter(is_active=True, channel_type="NX"):  # pragma: needs cover
                channel.release(user)

            self.config.pop(Org.CONFIG_VONAGE_KEY, None)
            self.config.pop(Org.CONFIG_VONAGE_SECRET, None)
            self.modified_by = user
            self.save(update_fields=("config", "modified_by", "modified_on"))

    def remove_twilio_account(self, user):
        if self.config:
            # release any Twilio and Twilio Messaging Service channels
            for channel in self.channels.filter(is_active=True, channel_type__in=["T", "TMS"]):
                channel.release(user)

            self.config.pop(Org.CONFIG_TWILIO_SID, None)
            self.config.pop(Org.CONFIG_TWILIO_TOKEN, None)
            self.modified_by = user
            self.save(update_fields=("config", "modified_by", "modified_on"))

    def get_twilio_client(self):
        account_sid = self.config.get(Org.CONFIG_TWILIO_SID)
        auth_token = self.config.get(Org.CONFIG_TWILIO_TOKEN)
        if account_sid and auth_token:
            return TwilioClient(account_sid, auth_token)
        return None

    def get_vonage_client(self):
        from temba.channels.types.vonage.client import VonageClient

        api_key = self.config.get(Org.CONFIG_VONAGE_KEY)
        api_secret = self.config.get(Org.CONFIG_VONAGE_SECRET)
        if api_key and api_secret:
            return VonageClient(api_key, api_secret)
        return None

    @property
    def default_country_code(self) -> str:
        """
        Gets the default country as a 2-digit country code, e.g. RW, US
        """

        return self.default_country.alpha_2 if self.default_country else ""

    @cached_property
    def default_country(self):
        """
        Gets the default country as a pycountry country for this org
        """

        # first try the country boundary field
        if self.country:
            country = pycountry.countries.get(name=self.country.name)
            if country:
                return country

        # next up try timezone
        code = timezone_to_country_code(self.timezone)
        if code:
            country = pycountry.countries.get(alpha_2=code)
            if country:
                return country

        # if that didn't work (not all timezones have a country) look for channels with countries
        codes = (
            self.channels.filter(is_active=True)
            .exclude(country=None)
            .order_by("country")
            .distinct("country")
            .values_list("country", flat=True)
        )
        if len(codes) == 1:
            country = pycountry.countries.get(alpha_2=codes[0])
            if country:
                return country

        return None

    def set_flow_languages(self, user, codes):
        """
        Sets languages used in flows for this org, creating and deleting language objects as necessary
        """

        assert all([languages.get_name(c) for c in codes]), "not a valid or allowed language"
        assert len(set(codes)) == len(codes), "language code list contains duplicates"

        self.flow_languages = codes
        self.modified_by = user
        self.save(update_fields=("flow_languages", "modified_by", "modified_on"))

    def get_datetime_formats(self, *, seconds=False):
        date_format = Org.DATE_FORMATS_PYTHON.get(self.date_format)
        time_format = "%H:%M:%S" if seconds else "%H:%M"
        datetime_format = f"{date_format} {time_format}"
        return date_format, datetime_format

    def format_datetime(self, d, *, show_time=True, seconds=False):
        """
        Formats a datetime with or without time using this org's date format
        """
        formats = self.get_datetime_formats(seconds=seconds)
        format = formats[1] if show_time else formats[0]
        return datetime_to_str(d, format, self.timezone)

    def get_users_with_role(self, role: OrgRole):
        """
        Gets the users who have the given role in this org
        """
        return role.get_users(self)

    def get_admins(self):
        """
        Convenience method for getting all org administrators
        """
        return self.get_users_with_role(OrgRole.ADMINISTRATOR)

    def get_users(self, *, roles=None):
        """
        Gets all of the users across all roles for this org
        """
        user_sets = [role.get_users(self) for role in roles or OrgRole]
        all_users = functools.reduce(operator.or_, user_sets)
        return all_users.distinct()

    def get_users_with_perm(self, perm: str):
        """
        Gets all of the users with the given permission for this org
        """

        app_label, codename = perm.split(".")
        permission = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        groups = Group.objects.filter(permissions=permission)

        return self.get_users(roles=[OrgRole.from_group(g) for g in groups])

    def has_user(self, user: User) -> bool:
        """
        Returns whether the given user has a role in this org (only explicit roles, so doesn't include customer support)
        """
        return self.get_users().filter(id=user.id).exists()

    def add_user(self, user: User, role: OrgRole):
        """
        Adds the given user to this org with the given role
        """

        # remove user from any existing roles
        if self.has_user(user):
            self.remove_user(user)

        getattr(self, role.m2m_name).add(user)

    def remove_user(self, user: User):
        """
        Removes the given user from this org by removing them from any roles
        """
        for role in OrgRole:
            getattr(self, role.m2m_name).remove(user)

    def get_owner(self) -> User:
        # look thru roles in order for the first added user
        for role in OrgRole:
            user = self.get_users_with_role(role).order_by("id").first()
            if user:
                return user

        # default to user that created this org
        return self.created_by

    def get_user_role(self, user: User):
        if user.is_staff:
            return OrgRole.ADMINISTRATOR

        for role in OrgRole:
            if self.get_users_with_role(role).filter(id=user.id).exists():
                return role

        return None

    def get_user_org_group(self, user: User):
        role = self.get_user_role(user)

        user._org_group = role.group if role else None

        return user._org_group

    def has_twilio_number(self):  # pragma: needs cover
        return self.channels.filter(channel_type="T")

    def has_vonage_number(self):  # pragma: needs cover
        return self.channels.filter(channel_type="NX")

    def init_topups(self, topup_size=None):
        if topup_size:
            return TopUp.create(self.created_by, price=0, credits=topup_size, org=self)

        # set whether we use topups based on our plan
        self.uses_topups = self.plan == settings.TOPUP_PLAN
        self.save(update_fields=["uses_topups"])

        return None

    def create_sample_flows(self, api_url):
        # get our sample dir
        filename = os.path.join(settings.STATICFILES_DIRS[0], "examples", "sample_flows.json")

        # for each of our samples
        with open(filename, "r") as example_file:
            samples = example_file.read()

        user = self.get_admins().first()
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

        # if we our suspended and have credits now, unsuspend ourselves
        if self.is_suspended and self.get_credits_remaining() > 0:
            self.is_suspended = False
            self.save(update_fields=["is_suspended"])

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

        campaign_prefetches = (
            Prefetch(
                "events",
                queryset=CampaignEvent.objects.filter(is_active=True).exclude(flow__is_system=True),
                to_attr="flow_events",
            ),
            "flow_events__flow",
        )

        all_flows = self.flows.filter(is_active=True).exclude(is_system=True)

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
            all_triggers = self.triggers.filter(is_archived=False, is_active=True).select_related("flow")
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

    def initialize(self, branding=None, topup_size=None, sample_flows=True):
        """
        Initializes an organization, creating all the dependent objects we need for it to work properly.
        """
        from temba.middleware import BrandingMiddleware
        from temba.contacts.models import ContactField, ContactGroup
        from temba.tickets.models import Ticketer, Topic

        with transaction.atomic():
            if not branding:
                branding = BrandingMiddleware.get_branding_for_host("")

            ContactGroup.create_system_groups(self)
            ContactField.create_system_fields(self)
            Ticketer.create_internal_ticketer(self, branding)
            Topic.create_default_topic(self)

            self.init_topups(topup_size)
            self.update_capabilities()

        # outside of the transaction as it's going to call out to mailroom for flow validation
        if sample_flows:
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

    def release(self, user, *, release_users=True):
        """
        Releases this org, marking it as inactive. Actual deletion of org data won't happen until after 7 days unless
        delete is True.
        """

        # free our children
        Org.objects.filter(parent=self).update(parent=None)

        # deactivate ourselves
        self.is_active = False
        self.modified_by = user
        self.released_on = timezone.now()
        self.save(update_fields=("is_active", "released_on", "modified_by", "modified_on"))

        # and immediately release our channels to halt messaging
        for channel in self.channels.filter(is_active=True):
            channel.release(user)

        # release any user that belongs only to us
        if release_users:
            for org_user in self.get_users():
                # check if this user is a member of any org on any brand
                other_orgs = org_user.get_user_orgs().exclude(id=self.id)
                if not other_orgs:
                    org_user.release(user, brand=self.brand)

        # remove all the org users
        for org_user in self.get_users():
            self.remove_user(org_user)

    def delete(self):
        """
        Does an actual delete of this org
        """

        assert not self.is_active and self.released_on, "can't delete an org which hasn't been released"
        assert not self.deleted_on, "can't delete an org twice"

        user = self.modified_by

        # delete notifications and exports
        self.notifications.all().delete()
        self.exportcontactstasks.all().delete()
        self.exportmessagestasks.all().delete()
        self.exportflowresultstasks.all().delete()

        for label in self.msgs_labels(manager="all_objects").all():
            label.release(user)
            label.delete()

        msg_ids = self.msgs.all().values_list("id", flat=True)

        # might be a lot of messages, batch this
        for id_batch in chunk_list(msg_ids, 1000):
            for msg in self.msgs.filter(id__in=id_batch):
                msg.release()

        # our system label counts
        self.system_labels.all().delete()

        # delete all our campaigns and associated events
        for c in self.campaigns.all():
            c.delete()

        # delete everything associated with our flows
        for flow in self.flows.all():
            # we want to manually release runs so we don't fire a mailroom task to do it
            flow.release(user, interrupt_sessions=False)
            flow.delete()

        # delete our flow labels (deleting a label deletes its children)
        for flow_label in self.flow_labels.filter(parent=None):
            flow_label.delete()

        # delete contact-related data
        self.http_logs.all().delete()
        self.sessions.all().delete()
        self.ticket_events.all().delete()
        self.tickets.all().delete()
        self.topics.all().delete()
        self.airtime_transfers.all().delete()

        # delete our contacts
        for contact in self.contacts.all():
            contact.release(user, full=True, immediately=True)
            contact.delete()

        # delete all our URNs
        self.urns.all().delete()

        # delete our fields
        for contactfield in self.contactfields(manager="all_fields").all():
            contactfield.release(user)
            contactfield.delete()

        # delete our groups
        for group in self.all_groups.all():
            group.release(user)
            group.delete()

        # delete our channels
        for channel in self.channels.all():
            channel.counts.all().delete()
            channel.logs.all().delete()
            channel.template_translations.all().delete()

            channel.delete()

        for g in self.globals.all():
            g.release(user)

        # delete our classifiers
        for classifier in self.classifiers.all():
            classifier.release(user)
            classifier.delete()

        # delete our ticketers
        for ticketer in self.ticketers.all():
            ticketer.release(user)
            ticketer.delete()

        # release all archives objects and files for this org
        Archive.release_org_archives(self)

        # return any unused credits to our parent
        if self.parent:
            self.allocate_credits(user, self.parent, self.get_credits_remaining())

        for topup in self.topups.all():
            topup.release()

        self.webhookevent_set.all().delete()

        for resthook in self.resthooks.all():
            resthook.release(user)
            for sub in resthook.subscribers.all():
                sub.delete()
            resthook.delete()

        # release our broadcasts
        for bcast in self.broadcast_set.filter(parent=None):
            bcast.release()

        # delete other related objects
        self.api_tokens.all().delete()
        self.invitations.all().delete()
        self.credit_alerts.all().delete()
        self.schedules.all().delete()
        self.boundaryalias_set.all().delete()
        self.templates.all().delete()

        # needs to come after deletion of msgs and broadcasts as those insert new counts
        self.system_labels.all().delete()

        # save when we were actually deleted
        self.modified_on = timezone.now()
        self.deleted_on = timezone.now()
        self.config = {}
        self.surveyor_password = None
        self.save()

    @classmethod
    def create_user(cls, email: str, password: str, language: str = None) -> User:
        user = User.objects.create_user(username=email, email=email, password=password)
        if language:
            user_settings = user.get_settings()
            user_settings.language = language
            user_settings.save(update_fields=("language",))
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
            "date_format": Org.DATE_FORMATS_ENGINE.get(self.date_format),
            "time_format": "tt:mm",
            "timezone": str(self.timezone),
            "default_language": self.flow_languages[0] if self.flow_languages else None,
            "allowed_languages": self.flow_languages,
            "default_country": self.default_country_code,
            "redaction_policy": "urns" if self.is_anon else "none",
        }

    def __str__(self):
        return self.name


# ===================== monkey patch User class with a few extra functions ========================


def release(user, releasing_user, *, brand):
    """
    Releases this user, and any orgs of which they are the sole owner
    """

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
        org.release(releasing_user, release_users=False)

    # remove user from all roles on any org for our brand
    for org in user.get_user_orgs([brand]):
        org.remove_user(user)


def get_user_orgs(user, brands=None):
    if user.is_superuser:
        return Org.objects.all()

    org_sets = [role.get_orgs(user) for role in OrgRole]
    user_orgs = functools.reduce(operator.or_, org_sets)

    if brands:
        user_orgs = user_orgs.filter(brand__in=brands)

    return user_orgs.filter(is_active=True).distinct().order_by("name")


def get_owned_orgs(user, brands=None):
    """
    Gets all the orgs where this is the only user for the current brand
    """
    owned_orgs = []
    for org in user.get_user_orgs(brands=brands):
        if not org.get_users().exclude(id=user.id).exists():
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


def _user_get_settings(user):
    """
    Gets or creates user settings for this user
    """
    assert user and user.is_authenticated, "can't fetch user settings for anonymous users"

    return UserSettings.get_or_create(user)


def _user_record_auth(user):
    user_settings = user.get_settings()
    user_settings.last_auth_on = timezone.now()
    user_settings.save(update_fields=("last_auth_on",))


def _user_enable_2fa(user):
    """
    Enables 2FA for this user
    """
    user_settings = user.get_settings()
    user_settings.two_factor_enabled = True
    user_settings.save(update_fields=("two_factor_enabled",))

    BackupToken.generate_for_user(user)


def _user_disable_2fa(user):
    """
    Disables 2FA for this user
    """
    user_settings = user.get_settings()
    user_settings.two_factor_enabled = False
    user_settings.save(update_fields=("two_factor_enabled",))

    user.backup_tokens.all().delete()


def _user_verify_2fa(user, *, otp: str = None, backup_token: str = None) -> bool:
    """
    Verifies a user using a 2FA mechanism (OTP or backup token)
    """
    if otp:
        secret = user.get_settings().otp_secret
        return pyotp.TOTP(secret).verify(otp, valid_window=2)
    elif backup_token:
        token = user.backup_tokens.filter(token=backup_token, is_used=False).first()
        if token:
            token.is_used = True
            token.save(update_fields=("is_used",))
            return True

    return False


def _user_name(user: User) -> str:
    return user.get_full_name()


def _user_as_engine_ref(user: User) -> dict:
    return {"email": user.email, "name": user.name}


def _user_str(user):
    as_str = _user_name(user)
    if not as_str:
        as_str = user.username
    return as_str


User.release = release
User.get_org = get_org
User.set_org = set_org
User.is_alpha = is_alpha_user
User.is_beta = is_beta_user
User.is_support = is_support_user
User.get_user_orgs = get_user_orgs
User.get_org_group = get_org_group
User.get_owned_orgs = get_owned_orgs
User.has_org_perm = _user_has_org_perm
User.get_settings = _user_get_settings
User.record_auth = _user_record_auth
User.enable_2fa = _user_enable_2fa
User.disable_2fa = _user_disable_2fa
User.verify_2fa = _user_verify_2fa
User.name = property(_user_name)
User.as_engine_ref = _user_as_engine_ref
User.__str__ = _user_str


def get_stripe_credentials():
    public_key = os.environ.get(
        "STRIPE_PUBLIC_KEY", getattr(settings, "STRIPE_PUBLIC_KEY", "MISSING_STRIPE_PUBLIC_KEY")
    )
    private_key = os.environ.get(
        "STRIPE_PRIVATE_KEY", getattr(settings, "STRIPE_PRIVATE_KEY", "MISSING_STRIPE_PRIVATE_KEY")
    )
    return (public_key, private_key)


class Invitation(SmartModel):
    """
    An Invitation to an e-mail address to join an Org with specific roles.
    """

    ROLE_CHOICES = [(r.code, r.display) for r in OrgRole]

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="invitations")

    email = models.EmailField()

    secret = models.CharField(max_length=64, unique=True)

    user_group = models.CharField(max_length=1, choices=ROLE_CHOICES, default=OrgRole.VIEWER.code)

    @classmethod
    def create(cls, org, user, email, role: OrgRole):
        return cls.objects.create(org=org, email=email, user_group=role.code, created_by=user, modified_by=user)

    @classmethod
    def bulk_create_or_update(cls, org, user, emails: list, role: OrgRole):
        for email in emails:
            invitation = cls.create(org, user, email, role)
            invitation.send()

    def save(self, *args, **kwargs):
        if not self.secret:
            secret = random_string(64)

            while Invitation.objects.filter(secret=secret):  # pragma: needs cover
                secret = random_string(64)

            self.secret = secret

        return super().save(*args, **kwargs)

    @property
    def role(self):
        return OrgRole.from_code(self.user_group)

    def send(self):
        """
        Sends this invitation as an email to the user
        """
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
    language = models.CharField(max_length=8, choices=settings.LANGUAGES, default=settings.DEFAULT_LANGUAGE)
    otp_secret = models.CharField(max_length=16, default=pyotp.random_base32)
    two_factor_enabled = models.BooleanField(default=False)
    last_auth_on = models.DateTimeField(null=True)

    @classmethod
    def get_or_create(cls, user):
        existing = UserSettings.objects.filter(user=user).first()
        if existing:
            return existing

        return cls.objects.create(user=user)


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

    squash_over = ("topup_id",)

    topup = models.ForeignKey(TopUp, on_delete=models.PROTECT)
    used = models.IntegerField()  # how many credits were used, can be negative

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

        admin = org.get_admins().first()

        if admin:
            # Otherwise, create our alert objects and trigger our event
            alert = CreditAlert.objects.create(org=org, alert_type=alert_type, created_by=admin, modified_by=admin)

            alert.send_alert()

    def send_alert(self):
        from .tasks import send_alert_email_task

        send_alert_email_task(self.id)

    def send_email(self):
        admin_emails = [admin.email for admin in self.org.get_admins().order_by("email")]

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
        active_orgs = Msg.objects.filter(created_on__gte=timezone.now() - timedelta(hours=1), org__uses_topups=True)
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
            TopUp.objects.filter(is_active=True, org__is_active=True, org__uses_topups=True, credits__gt=0)
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


class BackupToken(models.Model):
    """
    A 2FA backup token for a user
    """

    user = models.ForeignKey(User, related_name="backup_tokens", on_delete=models.PROTECT)
    token = models.CharField(max_length=18, unique=True, default=generate_token)
    is_used = models.BooleanField(default=False)
    created_on = models.DateTimeField(default=timezone.now)

    @classmethod
    def generate_for_user(cls, user, count: int = 10):
        # delete any existing tokens for this user
        user.backup_tokens.all().delete()

        return [cls.objects.create(user=user) for i in range(count)]

    def __str__(self):
        return self.token


class OrgActivity(models.Model):
    """
    Tracks various metrics for an organization on a daily basis:
       * total # of contacts
       * total # of active contacts (that sent or received a message)
       * total # of messages sent
       * total # of message received
       * total # of active contacts in plan period up to that date (if there is one)
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

    # the number of active contacts in the plan period (if they are on a plan)
    plan_active_contact_count = models.IntegerField(null=True)

    @classmethod
    def update_day(cls, now):
        """
        Updates our org activity for the passed in day.
        """
        from temba.msgs.models import Msg

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

        # calculate active count in plan period for orgs with an active plan
        plan_active_contact_counts = dict()
        for org in (
            Org.objects.exclude(plan_end=None)
            .exclude(plan_start=None)
            .exclude(plan_end__lt=start)
            .only("plan_start", "plan_end")
        ):
            plan_end = org.plan_end if org.plan_end < end else end
            count = (
                Msg.objects.filter(org=org, created_on__gt=org.plan_start, created_on__lt=plan_end)
                .only("contact")
                .distinct("contact")
                .count()
            )
            plan_active_contact_counts[org.id] = count

        for org in contact_counts:
            OrgActivity.objects.update_or_create(
                org=org,
                day=start,
                contact_count=org.contact_count,
                active_contact_count=active_counts.get(org.id, 0),
                incoming_count=incoming_count.get(org.id, 0),
                outgoing_count=outgoing_count.get(org.id, 0),
                plan_active_contact_count=plan_active_contact_counts.get(org.id),
            )

    class Meta:
        unique_together = ("org", "day")
