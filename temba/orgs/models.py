import itertools
import logging
import os
from abc import ABCMeta
from collections import defaultdict
from enum import Enum
from urllib.parse import quote, urlencode, urlparse

import pycountry
import pyotp
import pytz
from packaging.version import Version
from smartmin.models import SmartModel
from timezone_field import TimeZoneField
from twilio.rest import Client as TwilioClient

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User as AuthUser
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.validators import ArrayMinLengthValidator
from django.db import models, transaction
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from temba import mailroom
from temba.archives.models import Archive
from temba.locations.models import AdminBoundary
from temba.utils import brands, chunk_list, json, languages
from temba.utils.dates import datetime_to_str
from temba.utils.email import send_template_email
from temba.utils.models import JSONAsTextField, JSONField
from temba.utils.text import generate_token, random_string
from temba.utils.timezones import timezone_to_country_code
from temba.utils.uuid import uuid4

logger = logging.getLogger(__name__)


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


class User(AuthUser):
    """
    There's still no easy way to migrate an existing project to a custom user model, so this is a proxy which provides
    extra functionality based on the same underlying auth.User model, and for additional fields we use the UserSettings
    related model.
    """

    @classmethod
    def create(cls, email: str, first_name: str, last_name: str, password: str, language: str = None):
        obj = cls.objects.create_user(
            username=email, email=email, first_name=first_name, last_name=last_name, password=password
        )
        if language:
            obj.settings.language = language
            obj.settings.save(update_fields=("language",))
        return obj

    @classmethod
    def get_or_create(cls, email: str, first_name: str, last_name: str, password: str, language: str = None):
        obj = cls.objects.filter(username__iexact=email).first()
        if obj:
            obj.first_name = first_name
            obj.last_name = last_name
            obj.save(update_fields=("first_name", "last_name"))
            return obj

        return cls.create(email, first_name, last_name, password=password, language=language)

    @property
    def name(self) -> str:
        return self.get_full_name()

    def get_orgs(self, *, brand: str = None, roles=None):
        """
        Gets the orgs in the given brands that this user has access to (i.e. a role in).
        """
        orgs = self.orgs.filter(is_active=True).order_by("name")
        if brand:
            orgs = orgs.filter(brand=brand)
        if roles is not None:
            orgs = orgs.filter(orgmembership__user=self, orgmembership__role_code__in=[r.code for r in roles])

        return orgs

    def get_owned_orgs(self, *, brand=None):
        """
        Gets the orgs in the given brands where this user is the only user.
        """
        owned_orgs = []
        for org in self.get_orgs(brand=brand):
            if not org.users.exclude(id=self.id).exists():
                owned_orgs.append(org)
        return owned_orgs

    def set_team(self, team):
        """
        Sets the ticketing team for this user
        """
        self.settings.team = team
        self.settings.save(update_fields=("team",))

    def record_auth(self):
        """
        Records that this user authenticated
        """
        self.settings.last_auth_on = timezone.now()
        self.settings.save(update_fields=("last_auth_on",))

    def enable_2fa(self):
        """
        Enables 2FA for this user
        """
        self.settings.two_factor_enabled = True
        self.settings.save(update_fields=("two_factor_enabled",))

        BackupToken.generate_for_user(self)

    def disable_2fa(self):
        """
        Disables 2FA for this user
        """
        self.settings.two_factor_enabled = False
        self.settings.save(update_fields=("two_factor_enabled",))

        self.backup_tokens.all().delete()

    def verify_2fa(self, *, otp: str = None, backup_token: str = None) -> bool:
        """
        Verifies a user using a 2FA mechanism (OTP or backup token)
        """
        if otp:
            secret = self.settings.otp_secret
            return pyotp.TOTP(secret).verify(otp, valid_window=2)
        elif backup_token:
            token = self.backup_tokens.filter(token=backup_token, is_used=False).first()
            if token:
                token.is_used = True
                token.save(update_fields=("is_used",))
                return True

        return False

    @cached_property
    def is_alpha(self) -> bool:
        return self.groups.filter(name="Alpha").exists()

    @cached_property
    def is_beta(self) -> bool:
        return self.groups.filter(name="Beta").exists()

    def has_org_perm(self, org, permission: str) -> bool:
        """
        Determines if a user has the given permission in the given org.
        """
        if self.is_staff:
            return True

        if self.is_anonymous:  # pragma: needs cover
            return False

        # has it innately? e.g. Granter group
        if self.has_perm(permission):
            return True

        role = org.get_user_role(self)
        if not role:
            return False

        return role.has_perm(permission)

    @cached_property
    def settings(self):
        assert self.is_authenticated, "can't fetch user settings for anonymous users"

        return UserSettings.objects.get_or_create(user=self)[0]

    def get_api_token(self, org) -> str:
        from temba.api.models import get_or_create_api_token

        return get_or_create_api_token(org, self)

    def as_engine_ref(self) -> dict:
        return {"email": self.email, "name": self.name}

    def release(self, user, *, brand):
        """
        Releases this user, and any orgs of which they are the sole owner.
        """

        # if our user exists across brands don't muck with the user
        if self.get_orgs().order_by("brand").distinct("brand").count() < 2:
            user_uuid = str(uuid4())
            self.first_name = ""
            self.last_name = ""
            self.email = f"{user_uuid}@rapidpro.io"
            self.username = f"{user_uuid}@rapidpro.io"
            self.password = ""
            self.is_active = False
            self.save()

        # release any orgs we own on this brand
        for org in self.get_owned_orgs(brand=brand):
            org.release(user, release_users=False)

        # remove user from all roles on any org for our brand
        for org in self.get_orgs(brand=brand):
            org.remove_user(self)

    def __str__(self):
        return self.name or self.username

    class Meta:
        proxy = True


class UserSettings(models.Model):
    """
    Custom fields for users
    """

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="usersettings")
    language = models.CharField(max_length=8, choices=settings.LANGUAGES, default=settings.DEFAULT_LANGUAGE)
    team = models.ForeignKey("tickets.Team", on_delete=models.PROTECT, null=True)
    otp_secret = models.CharField(max_length=16, default=pyotp.random_base32)
    two_factor_enabled = models.BooleanField(default=False)
    last_auth_on = models.DateTimeField(null=True)
    external_id = models.CharField(max_length=128, null=True)
    verification_token = models.CharField(max_length=64, null=True)


class OrgRole(Enum):
    ADMINISTRATOR = ("A", _("Administrator"), _("Administrators"), "Administrators")
    EDITOR = ("E", _("Editor"), _("Editors"), "Editors")
    VIEWER = ("V", _("Viewer"), _("Viewers"), "Viewers")
    AGENT = ("T", _("Agent"), _("Agents"), "Agents")
    SURVEYOR = ("S", _("Surveyor"), _("Surveyors"), "Surveyors")

    def __init__(self, code: str, display: str, display_plural: str, group_name: str):
        self.code = code
        self.display = display
        self.display_plural = display_plural
        self.group_name = group_name

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
        The auth group which defines the permissions for this role
        """
        return Group.objects.get(name=self.group_name)

    @cached_property
    def permissions(self) -> set:
        perms = self.group.permissions.select_related("content_type")
        return {f"{p.content_type.app_label}.{p.codename}" for p in perms}

    def has_perm(self, permission: str) -> bool:
        """
        Returns whether this role has the given permission
        """
        return permission in self.permissions


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

    FEATURE_USERS = "users"  # can invite users to this org
    FEATURE_NEW_ORGS = "new_orgs"  # can create new workspace with same login
    FEATURE_CHILD_ORGS = "child_orgs"  # can create child workspaces of this org
    FEATURES_CHOICES = (
        (FEATURE_USERS, _("Users")),
        (FEATURE_NEW_ORGS, _("New Orgs")),
        (FEATURE_CHILD_ORGS, _("Child Orgs")),
    )

    LIMIT_CHANNELS = "channels"
    LIMIT_FIELDS = "fields"
    LIMIT_GLOBALS = "globals"
    LIMIT_GROUPS = "groups"
    LIMIT_LABELS = "labels"
    LIMIT_TOPICS = "topics"
    LIMIT_TEAMS = "teams"

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
    brand = models.CharField(max_length=128, default="rapidpro", verbose_name=_("Brand"))
    plan = models.CharField(verbose_name=_("Plan"), max_length=16, null=True, blank=True)
    plan_start = models.DateTimeField(null=True)
    plan_end = models.DateTimeField(null=True)

    stripe_customer = models.CharField(
        verbose_name=_("Stripe Customer"),
        max_length=32,
        null=True,
        blank=True,
        help_text=_("Our Stripe customer id for your organization"),
    )

    users = models.ManyToManyField(User, through="OrgMembership", related_name="orgs")

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

    features = ArrayField(models.CharField(max_length=32), default=list)
    limits = JSONField(default=dict)
    api_rates = JSONField(default=dict)

    is_anon = models.BooleanField(
        default=False, help_text=_("Whether this organization anonymizes the phone numbers of contacts within it")
    )

    is_flagged = models.BooleanField(default=False, help_text=_("Whether this organization is currently flagged."))
    is_suspended = models.BooleanField(default=False, help_text=_("Whether this organization is currently suspended."))

    flow_languages = ArrayField(models.CharField(max_length=3), default=list, validators=[ArrayMinLengthValidator(1)])

    surveyor_password = models.CharField(
        null=True, max_length=128, default=None, help_text=_("A password that allows users to register as surveyors")
    )

    parent = models.ForeignKey("orgs.Org", on_delete=models.PROTECT, null=True, related_name="children")

    # when this org was released and when it was actually deleted
    released_on = models.DateTimeField(null=True)
    deleted_on = models.DateTimeField(null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._user_role_cache = {}

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

    @classmethod
    def create(cls, user, branding, name: str, tz):
        """
        Creates a new workspace.
        """

        mdy_tzs = pytz.country_timezones("US")
        date_format = Org.DATE_FORMAT_MONTH_FIRST if str(tz) in mdy_tzs else cls.DATE_FORMAT_DAY_FIRST

        # use default user language as default flow language too
        default_flow_language = languages.alpha2_to_alpha3(settings.DEFAULT_LANGUAGE)
        flow_languages = [default_flow_language] if default_flow_language else ["eng"]

        org = Org.objects.create(
            name=name,
            timezone=tz,
            date_format=date_format,
            language=settings.DEFAULT_LANGUAGE,
            flow_languages=flow_languages,
            brand=branding["slug"],
            slug=cls.get_unique_slug(name),
            created_by=user,
            modified_by=user,
        )

        org.add_user(user, OrgRole.ADMINISTRATOR)
        org.initialize()
        return org

    def create_new(self, user, name: str, tz, *, as_child: bool):
        """
        Creates a new workspace copying settings from this workspace.
        """

        if as_child:
            assert Org.FEATURE_CHILD_ORGS in self.features, "only orgs with this feature enabled can create child orgs"
            assert not self.parent_id, "child orgs can't create children"
        else:
            assert Org.FEATURE_NEW_ORGS in self.features, "only orgs with this feature enabled can create new orgs"

        org = Org.objects.create(
            name=name,
            timezone=tz,
            date_format=self.date_format,
            language=self.language,
            flow_languages=self.flow_languages,
            brand=self.brand,
            parent=self if as_child else None,
            slug=self.get_unique_slug(name),
            created_by=user,
            modified_by=user,
        )

        org.add_user(user, OrgRole.ADMINISTRATOR)
        org.initialize()
        return org

    @cached_property
    def branding(self):
        return brands.get_by_slug(self.brand)

    def get_brand_domain(self):
        return self.branding["domain"]

    def get_integrations(self, category: IntegrationType.Category) -> list:
        """
        Returns the connected integrations on this org of the given category
        """

        return [t for t in IntegrationType.get_all(category) if t.is_connected(self)]

    def get_limit(self, limit_type):
        return int(self.limits.get(limit_type, settings.ORG_LIMIT_DEFAULTS.get(limit_type)))

    def flag(self):
        """
        Flags this org for suspicious activity
        """
        from temba.notifications.incidents.builtin import OrgFlaggedIncidentType

        self.is_flagged = True
        self.save(update_fields=("is_flagged", "modified_on"))

        OrgFlaggedIncidentType.get_or_create(self)  # create incident which will notify admins

    def unflag(self):
        """
        Unflags this org if they previously were flagged
        """

        from temba.notifications.incidents.builtin import OrgFlaggedIncidentType

        if self.is_flagged:
            self.is_flagged = False
            self.save(update_fields=("is_flagged", "modified_on"))

            OrgFlaggedIncidentType.get_or_create(self).end()

    def verify(self):
        """
        Unflags org and marks as verified so it won't be flagged automatically in future
        """
        self.unflag()
        self.config[Org.CONFIG_VERIFIED] = True
        self.save(update_fields=("config", "modified_on"))

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

        self.clean_import(export_json)

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

    def clean_import(self, import_def):
        from temba.triggers.models import Trigger

        cleaned_triggers = []

        for trigger_def in import_def.get("triggers", []):
            trigger_type = trigger_def.get("trigger_type", "")
            channel_uuid = trigger_def.get("channel")

            # TODO need better way to report import results back to users
            # ignore scheduled triggers and new conversation triggers without channels
            if trigger_type == "S" or (trigger_type == "N" and not channel_uuid):
                continue

            Trigger.validate_import_def(trigger_def)
            cleaned_triggers.append(trigger_def)

        import_def["triggers"] = cleaned_triggers

    @classmethod
    def export_definitions(cls, site_link, components, include_fields=True, include_groups=True):
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
                        if not event.relative_to.is_system:
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
        from temba.channels.models import Channel
        from temba.contacts.models import URN

        return self.get_channel(Channel.ROLE_CALL, scheme=URN.TEL_SCHEME)

    def get_answer_channel(self):
        from temba.channels.models import Channel
        from temba.contacts.models import URN

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

        return self.groups.get(group_type=ContactGroup.TYPE_DB_ACTIVE)

    def get_contact_count(self) -> int:
        from temba.contacts.models import Contact

        return sum(Contact.get_status_counts(self).values())

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

    def set_flow_languages(self, user, codes: list):
        """
        Sets languages used in flows for this org, creating and deleting language objects as necessary
        """

        assert len(codes), "must specify at least one language"
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

    def get_users(self, *, roles: list = None, with_perm: str = None):
        """
        Gets users in this org, filtered by role or permission.
        """
        qs = self.users.filter(is_active=True)

        if with_perm:
            app_label, codename = with_perm.split(".")
            permission = Permission.objects.get(content_type__app_label=app_label, codename=codename)
            groups = Group.objects.filter(permissions=permission)
            roles = [OrgRole.from_group(g) for g in groups]

        if roles is not None:
            qs = qs.filter(orgmembership__org=self, orgmembership__role_code__in=[r.code for r in roles])

        return qs

    def get_admins(self):
        """
        Convenience method for getting all org administrators
        """
        return self.get_users(roles=[OrgRole.ADMINISTRATOR])

    def has_user(self, user: User) -> bool:
        """
        Returns whether the given user has a role in this org (only explicit roles, so doesn't include staff)
        """
        return self.users.filter(id=user.id).exists()

    def add_user(self, user: User, role: OrgRole):
        """
        Adds the given user to this org with the given role
        """
        if self.has_user(user):  # remove user from any existing roles
            self.remove_user(user)

        self.users.add(user, through_defaults={"role_code": role.code})
        self._user_role_cache[user] = role

    def remove_user(self, user: User):
        """
        Removes the given user from this org by removing them from any roles
        """
        self.users.remove(user)
        if user in self._user_role_cache:
            del self._user_role_cache[user]

    def get_owner(self) -> User:
        # look thru roles in order for the first added user
        for role in OrgRole:
            user = self.users.filter(orgmembership__role_code=role.code).order_by("id").first()
            if user:
                return user

        # default to user that created this org (converting to our User proxy model)
        return User.objects.get(id=self.created_by_id)

    def get_user_role(self, user: User):
        """
        Gets the role of the given user in this org if any.
        """

        def get_role():
            if user.is_staff:
                return OrgRole.ADMINISTRATOR

            membership = OrgMembership.objects.filter(org=self, user=user).first()
            return membership.role if membership else None

        if user not in self._user_role_cache:
            self._user_role_cache[user] = get_role()
        return self._user_role_cache[user]

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

    def generate_dependency_graph(self, include_campaigns=True, include_triggers=False, include_archived=False):
        """
        Generates a dict of all exportable flows and campaigns for this org with each object's immediate dependencies
        """
        from temba.campaigns.models import Campaign, CampaignEvent
        from temba.contacts.models import ContactField, ContactGroup
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

    def initialize(self, sample_flows=True):
        """
        Initializes an organization, creating all the dependent objects we need for it to work properly.
        """
        from temba.contacts.models import ContactField, ContactGroup
        from temba.tickets.models import Ticketer, Topic

        with transaction.atomic():
            ContactGroup.create_system_groups(self)
            ContactField.create_system_fields(self)
            Ticketer.create_internal_ticketer(self, self.branding)
            Topic.create_default_topic(self)

        # outside of the transaction as it's going to call out to mailroom for flow validation
        if sample_flows:
            self.create_sample_flows(self.branding.get("link", ""))

    def get_delete_date(self, *, archive_type=Archive.TYPE_MSG):
        """
        Gets the most recent date for which data hasn't been deleted yet or None if no deletion has been done
        :return:
        """
        archive = self.archives.filter(needs_deletion=False, archive_type=archive_type).order_by("-start_date").first()
        if archive:
            return archive.get_end_date()

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
            for org_user in self.users.all():
                # check if this user is a member of any org on any brand
                other_orgs = org_user.get_orgs().exclude(id=self.id)
                if not other_orgs:
                    org_user.release(user, brand=self.brand)

        # remove all the org users
        for org_user in self.users.all():
            self.remove_user(org_user)

    def delete(self):
        """
        Does an actual delete of this org
        """

        assert not self.is_active and self.released_on, "can't delete an org which hasn't been released"
        assert not self.deleted_on, "can't delete an org twice"

        user = self.modified_by

        # delete notifications and exports
        self.incidents.all().delete()
        self.notifications.all().delete()
        self.exportcontactstasks.all().delete()
        self.exportmessagestasks.all().delete()
        self.exportflowresultstasks.all().delete()
        self.exportticketstasks.all().delete()

        for label in self.msgs_labels.all():
            label.release(user)
            label.delete()

        msg_ids = self.msgs.all().values_list("id", flat=True)

        # might be a lot of messages, batch this
        for id_batch in chunk_list(msg_ids, 1000):
            for msg in self.msgs.filter(id__in=id_batch):
                msg.delete()

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
            contact.release(user, immediately=True)
            contact.delete()

        # delete all our URNs
        self.urns.all().delete()

        # delete our fields
        for contactfield in self.fields.all():
            contactfield.release(user)
            contactfield.delete()

        # delete our groups
        for group in self.groups.all():
            group.release(user, immediate=True)
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

        self.webhookevent_set.all().delete()

        for resthook in self.resthooks.all():
            resthook.release(user)
            for sub in resthook.subscribers.all():
                sub.delete()
            resthook.delete()

        # release our broadcasts
        for bcast in self.broadcast_set.filter(parent=None):
            bcast.delete(user, soft=False)

        # delete other related objects
        self.api_tokens.all().delete()
        self.invitations.all().delete()
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

    def as_environment_def(self):
        """
        Returns this org as an environment definition as used by the flow engine
        """

        return {
            "date_format": Org.DATE_FORMATS_ENGINE.get(self.date_format),
            "time_format": "tt:mm",
            "timezone": str(self.timezone),
            "allowed_languages": self.flow_languages,
            "default_country": self.default_country_code,
            "redaction_policy": "urns" if self.is_anon else "none",
        }

    def __str__(self):
        return self.name


class OrgMembership(models.Model):
    org = models.ForeignKey(Org, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    role_code = models.CharField(max_length=1)

    @property
    def role(self):
        return OrgRole.from_code(self.role_code)

    class Meta:
        unique_together = (("org", "user"),)


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

        subject = _("%(name)s Invitation") % self.org.branding
        template = "orgs/email/invitation_email"
        to_email = self.email

        context = dict(org=self.org, now=timezone.now(), branding=self.org.branding, invitation=self)
        context["subject"] = subject

        send_template_email(to_email, subject, template, context, self.org.branding)


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
