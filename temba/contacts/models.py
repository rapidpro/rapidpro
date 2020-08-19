import datetime
import logging
import os
import time
import uuid
from decimal import Decimal
from itertools import chain
from typing import Dict, List

import iso8601
import phonenumbers
import pytz
import regex
from smartmin.csv_imports.models import ImportTask
from smartmin.models import SmartImportRowError, SmartModel

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, connection, models, transaction
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba import mailroom
from temba.assets.models import register_asset_store
from temba.channels.models import Channel, ChannelEvent
from temba.locations.models import AdminBoundary
from temba.mailroom import modifiers, queue_populate_dynamic_group
from temba.orgs.models import Org, OrgLock
from temba.utils import analytics, chunk_list, es, format_number, get_anonymous_user, json, on_transaction_commit
from temba.utils.export import BaseExportAssetStore, BaseExportTask, TableExporter
from temba.utils.languages import _get_language_name_iso6393
from temba.utils.models import JSONField, RequireUpdateFieldsMixin, SquashableModel, TembaModel
from temba.utils.text import truncate, unsnakify
from temba.utils.urns import ParsedURN, parse_urn
from temba.utils.uuid import uuid4
from temba.values.constants import Value

logger = logging.getLogger(__name__)

# phone number for every org's test contact
OLD_TEST_CONTACT_TEL = "12065551212"

# how many sequential contacts on import triggers suspension
SEQUENTIAL_CONTACTS_THRESHOLD = 250

DELETED_SCHEME = "deleted"
EMAIL_SCHEME = "mailto"
EXTERNAL_SCHEME = "ext"
FACEBOOK_SCHEME = "facebook"
JIOCHAT_SCHEME = "jiochat"
LINE_SCHEME = "line"
TEL_SCHEME = "tel"
TELEGRAM_SCHEME = "telegram"
TWILIO_SCHEME = "twilio"
TWITTER_SCHEME = "twitter"
TWITTERID_SCHEME = "twitterid"
VIBER_SCHEME = "viber"
VK_SCHEME = "vk"
FCM_SCHEME = "fcm"
WHATSAPP_SCHEME = "whatsapp"
WECHAT_SCHEME = "wechat"
FRESHCHAT_SCHEME = "freshchat"

FACEBOOK_PATH_REF_PREFIX = "ref:"

# Scheme, Label, Export/Import Header, Context Key
URN_SCHEME_CONFIG = (
    (TEL_SCHEME, _("Phone number"), "tel_e164"),
    (FACEBOOK_SCHEME, _("Facebook identifier"), FACEBOOK_SCHEME),
    (TWITTER_SCHEME, _("Twitter handle"), TWITTER_SCHEME),
    (TWITTERID_SCHEME, _("Twitter ID"), TWITTERID_SCHEME),
    (VIBER_SCHEME, _("Viber identifier"), VIBER_SCHEME),
    (LINE_SCHEME, _("LINE identifier"), LINE_SCHEME),
    (TELEGRAM_SCHEME, _("Telegram identifier"), TELEGRAM_SCHEME),
    (EMAIL_SCHEME, _("Email address"), EMAIL_SCHEME),
    (EXTERNAL_SCHEME, _("External identifier"), EXTERNAL_SCHEME),
    (JIOCHAT_SCHEME, _("JioChat identifier"), JIOCHAT_SCHEME),
    (WECHAT_SCHEME, _("WeChat identifier"), WECHAT_SCHEME),
    (FCM_SCHEME, _("Firebase Cloud Messaging identifier"), FCM_SCHEME),
    (WHATSAPP_SCHEME, _("WhatsApp identifier"), WHATSAPP_SCHEME),
    (FRESHCHAT_SCHEME, _("Freshchat identifier"), FRESHCHAT_SCHEME),
    (VK_SCHEME, _("VK identifier"), VK_SCHEME),
)


IMPORT_HEADERS = tuple((f"URN:{c[0]}", c[0]) for c in URN_SCHEME_CONFIG)

# events from sessions to include in contact history
HISTORY_INCLUDE_EVENTS = {
    "contact_language_changed",
    "contact_field_changed",
    "contact_groups_changed",
    "contact_name_changed",
    "contact_urns_changed",
    "email_created",  # no longer generated but exists in old sessions
    "email_sent",
    "error",
    "failure",
    "input_labels_added",
    "run_result_changed",
    "ticket_opened",
}


class URN(object):
    """
    Support class for URN strings. We differ from the strict definition of a URN (https://tools.ietf.org/html/rfc2141)
    in that:
        * We only supports URNs with scheme and path parts (no netloc, query, params or fragment)
        * Path component can be any non-blank unicode string
        * No hex escaping in URN path
    """

    VALID_SCHEMES = {s[0] for s in URN_SCHEME_CONFIG}
    IMPORT_HEADERS = {f"URN:{s[0]}" for s in URN_SCHEME_CONFIG}

    def __init__(self):  # pragma: no cover
        raise ValueError("Class shouldn't be instantiated")

    @classmethod
    def from_parts(cls, scheme, path, query=None, display=None):
        """
        Formats a URN scheme and path as single URN string, e.g. tel:+250783835665
        """
        if not scheme or (scheme not in cls.VALID_SCHEMES and scheme != DELETED_SCHEME):
            raise ValueError("Invalid scheme component: '%s'" % scheme)

        if not path:
            raise ValueError("Invalid path component: '%s'" % path)

        return str(ParsedURN(scheme, path, query=query, fragment=display))

    @classmethod
    def to_parts(cls, urn):
        """
        Parses a URN string (e.g. tel:+250783835665) into a tuple of scheme and path
        """
        try:
            parsed = parse_urn(urn)
        except ValueError:
            raise ValueError("URN strings must contain scheme and path components")

        if parsed.scheme not in cls.VALID_SCHEMES and parsed.scheme != DELETED_SCHEME:
            raise ValueError("URN contains an invalid scheme component: '%s'" % parsed.scheme)

        return parsed.scheme, parsed.path, parsed.query or None, parsed.fragment or None

    @classmethod
    def format(cls, urn, international=False, formatted=True):
        """
        formats this URN as a human friendly string
        """
        scheme, path, query, display = cls.to_parts(urn)

        if scheme in [TEL_SCHEME, WHATSAPP_SCHEME] and formatted:
            try:
                # whatsapp scheme is E164 without a leading +, add it so parsing works
                if scheme == WHATSAPP_SCHEME:
                    path = "+" + path

                if path and path[0] == "+":
                    phone_format = phonenumbers.PhoneNumberFormat.NATIONAL
                    if international:
                        phone_format = phonenumbers.PhoneNumberFormat.INTERNATIONAL
                    return phonenumbers.format_number(phonenumbers.parse(path, None), phone_format)
            except phonenumbers.NumberParseException:  # pragma: no cover
                pass

        if display:
            return display

        return path

    @classmethod
    def validate(cls, urn, country_code=None):
        """
        Validates a normalized URN
        """
        try:
            scheme, path, query, display = cls.to_parts(urn)
        except ValueError:
            return False

        if scheme == TEL_SCHEME:
            try:
                parsed = phonenumbers.parse(path, country_code)
                return phonenumbers.is_possible_number(parsed)
            except Exception:
                return False

        # validate twitter URNs look like handles
        elif scheme == TWITTER_SCHEME:
            return regex.match(r"^[a-zA-Z0-9_]{1,15}$", path, regex.V0)

        # validate path is a number and display is a handle if present
        elif scheme == TWITTERID_SCHEME:
            valid = path.isdigit()
            if valid and display:
                valid = regex.match(r"^[a-zA-Z0-9_]{1,15}$", display, regex.V0)

            return valid

        elif scheme == EMAIL_SCHEME:
            try:
                validate_email(path)
                return True
            except ValidationError:
                return False

        # facebook uses integer ids or temp ref ids
        elif scheme == FACEBOOK_SCHEME:
            # we don't validate facebook refs since they come from the outside
            if URN.is_path_fb_ref(path):
                return True

            # otherwise, this should be an int
            else:
                try:
                    int(path)
                    return True
                except ValueError:
                    return False

        # telegram and whatsapp use integer ids
        elif scheme in [TELEGRAM_SCHEME, WHATSAPP_SCHEME]:
            return regex.match(r"^[0-9]+$", path, regex.V0)

        # validate Viber URNS look right (this is a guess)
        elif scheme == VIBER_SCHEME:  # pragma: needs cover
            return regex.match(r"^[a-zA-Z0-9_=]{1,24}$", path, regex.V0)

        # validate Freshchat URNS look right (this is a guess)
        elif scheme == FRESHCHAT_SCHEME:  # pragma: needs cover
            return regex.match(
                r"^[0-9a-fA-F]{8}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{12}/[0-9a-fA-F]{8}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{12}$",
                path,
                regex.V0,
            )

        # anything goes for external schemes
        return True

    @classmethod
    def normalize(cls, urn, country_code=None):
        """
        Normalizes the path of a URN string. Should be called anytime looking for a URN match.
        """
        scheme, path, query, display = cls.to_parts(urn)

        norm_path = str(path).strip()

        if scheme == TEL_SCHEME:
            norm_path, valid = cls.normalize_number(norm_path, country_code)
        elif scheme == TWITTER_SCHEME:
            norm_path = norm_path.lower()
            if norm_path[0:1] == "@":  # strip @ prefix if provided
                norm_path = norm_path[1:]
            norm_path = norm_path.lower()  # Twitter handles are case-insensitive, so we always store as lowercase

        elif scheme == TWITTERID_SCHEME:
            if display:
                display = str(display).strip().lower()
                if display and display[0] == "@":
                    display = display[1:]

        elif scheme == EMAIL_SCHEME:
            norm_path = norm_path.lower()

        return cls.from_parts(scheme, norm_path, query, display)

    @classmethod
    def normalize_number(cls, number, country_code):
        """
        Normalizes the passed in number, they should be only digits, some backends prepend + and
        maybe crazy users put in dashes or parentheses in the console.

        Returns a tuple of the normalized number and whether it looks like a possible full international
        number.
        """
        # if the number ends with e11, then that is Excel corrupting it, remove it
        if number.lower().endswith("e+11") or number.lower().endswith("e+12"):
            number = number[0:-4].replace(".", "")

        # remove other characters
        number = regex.sub(r"[^0-9a-z\+]", "", number.lower(), regex.V0)

        # add on a plus if it looks like it could be a fully qualified number
        if len(number) >= 11 and number[0] not in ["+", "0"]:
            number = "+" + number

        normalized = None
        try:
            normalized = phonenumbers.parse(number, str(country_code) if country_code else None)
        except Exception:
            pass

        # now does it look plausible?
        try:
            if phonenumbers.is_possible_number(normalized):
                return (phonenumbers.format_number(normalized, phonenumbers.PhoneNumberFormat.E164), True)
        except Exception:
            pass

        # this must be a local number of some kind, just lowercase and save
        return regex.sub("[^0-9a-z]", "", number.lower(), regex.V0), False

    @classmethod
    def identity(cls, urn):
        scheme, path, query, display = URN.to_parts(urn)
        return URN.from_parts(scheme, path)

    @classmethod
    def fb_ref_from_path(cls, path):
        return path[len(FACEBOOK_PATH_REF_PREFIX) :]

    @classmethod
    def path_from_fb_ref(cls, ref):
        return FACEBOOK_PATH_REF_PREFIX + ref

    @classmethod
    def is_path_fb_ref(cls, path):
        return path.startswith(FACEBOOK_PATH_REF_PREFIX)

    # ==================== shortcut constructors ===========================

    @classmethod
    def from_tel(cls, path):
        return cls.from_parts(TEL_SCHEME, path)

    @classmethod
    def from_twitter(cls, path):
        return cls.from_parts(TWITTER_SCHEME, path)

    @classmethod
    def from_twitterid(cls, id, screen_name=None):
        return cls.from_parts(TWITTERID_SCHEME, id, display=screen_name)

    @classmethod
    def from_email(cls, path):
        return cls.from_parts(EMAIL_SCHEME, path)

    @classmethod
    def from_facebook(cls, path):
        return cls.from_parts(FACEBOOK_SCHEME, path)

    @classmethod
    def from_vk(cls, path):
        return cls.from_parts(VK_SCHEME, path)

    @classmethod
    def from_line(cls, path):
        return cls.from_parts(LINE_SCHEME, path)

    @classmethod
    def from_telegram(cls, path):
        return cls.from_parts(TELEGRAM_SCHEME, path)

    @classmethod
    def from_external(cls, path):
        return cls.from_parts(EXTERNAL_SCHEME, path)

    @classmethod
    def from_viber(cls, path):
        return cls.from_parts(VIBER_SCHEME, path)

    @classmethod
    def from_whatsapp(cls, path):
        return cls.from_parts(WHATSAPP_SCHEME, path)

    @classmethod
    def from_fcm(cls, path):
        return cls.from_parts(FCM_SCHEME, path)

    @classmethod
    def from_freshchat(cls, path):
        return cls.from_parts(FRESHCHAT_SCHEME, path)

    @classmethod
    def from_jiochat(cls, path):
        return cls.from_parts(JIOCHAT_SCHEME, path)

    @classmethod
    def from_wechat(cls, path):
        return cls.from_parts(WECHAT_SCHEME, path)


class UserContactFieldsQuerySet(models.QuerySet):
    def collect_usage(self):
        return (
            self.annotate(
                flow_count=Count("dependent_flows", distinct=True, filter=Q(dependent_flows__is_active=True))
            )
            .annotate(
                campaign_count=Count("campaign_events", distinct=True, filter=Q(campaign_events__is_active=True))
            )
            .annotate(contactgroup_count=Count("contactgroup", distinct=True, filter=Q(contactgroup__is_active=True)))
        )

    def active_for_org(self, org):
        return self.filter(is_active=True, org=org)


class UserContactFieldsManager(models.Manager):
    def get_queryset(self):
        return UserContactFieldsQuerySet(self.model, using=self._db).filter(field_type=ContactField.FIELD_TYPE_USER)

    def create(self, **kwargs):
        kwargs["field_type"] = ContactField.FIELD_TYPE_USER

        return super().create(**kwargs)

    def count_active_for_org(self, org):
        return self.get_queryset().active_for_org(org=org).count()

    def collect_usage(self):
        return self.get_queryset().collect_usage()

    def active_for_org(self, org):
        return self.get_queryset().active_for_org(org=org)


class SystemContactFieldsManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(field_type=ContactField.FIELD_TYPE_SYSTEM)

    def create(self, **kwargs):
        kwargs["field_type"] = ContactField.FIELD_TYPE_SYSTEM

        return super().create(**kwargs)


class ContactField(SmartModel):
    """
    Represents a type of field that can be put on Contacts.
    """

    MAX_KEY_LEN = 36
    MAX_LABEL_LEN = 36

    FIELD_TYPE_SYSTEM = "S"
    FIELD_TYPE_USER = "U"
    FIELD_TYPE_CHOICES = ((FIELD_TYPE_SYSTEM, "System"), (FIELD_TYPE_USER, "User"))

    KEY_ID = "id"
    KEY_NAME = "name"
    KEY_CREATED_ON = "created_on"
    KEY_LANGUAGE = "language"
    KEY_LAST_SEEN_ON = "last_seen_on"

    # fields that cannot be updated by user
    IMMUTABLE_FIELDS = (KEY_ID, KEY_CREATED_ON, KEY_LAST_SEEN_ON)

    SYSTEM_FIELDS = {
        KEY_ID: dict(label="ID", value_type=Value.TYPE_NUMBER),
        KEY_NAME: dict(label="Name", value_type=Value.TYPE_TEXT),
        KEY_CREATED_ON: dict(label="Created On", value_type=Value.TYPE_DATETIME),
        KEY_LANGUAGE: dict(label="Language", value_type=Value.TYPE_TEXT),
        KEY_LAST_SEEN_ON: dict(label="Last Seen On", value_type=Value.TYPE_DATETIME),
    }

    EXPORT_KEY = "key"
    EXPORT_NAME = "name"
    EXPORT_TYPE = "type"

    GOFLOW_TYPES = {
        Value.TYPE_TEXT: "text",
        Value.TYPE_NUMBER: "number",
        Value.TYPE_DATETIME: "datetime",
        Value.TYPE_STATE: "state",
        Value.TYPE_DISTRICT: "district",
        Value.TYPE_WARD: "ward",
    }

    uuid = models.UUIDField(unique=True, default=uuid.uuid4)

    org = models.ForeignKey(Org, on_delete=models.PROTECT, verbose_name=_("Org"), related_name="contactfields")

    label = models.CharField(verbose_name=_("Label"), max_length=MAX_LABEL_LEN)

    key = models.CharField(verbose_name=_("Key"), max_length=MAX_KEY_LEN)

    value_type = models.CharField(
        choices=Value.TYPE_CHOICES, max_length=1, default=Value.TYPE_TEXT, verbose_name="Field Type"
    )
    show_in_table = models.BooleanField(
        verbose_name=_("Shown in Tables"), default=False, help_text=_("Featured field")
    )

    priority = models.PositiveIntegerField(default=0)

    field_type = models.CharField(max_length=1, choices=FIELD_TYPE_CHOICES, default=FIELD_TYPE_USER)

    # Model managers
    all_fields = models.Manager()  # this is the default manager
    user_fields = UserContactFieldsManager()
    system_fields = SystemContactFieldsManager()

    @classmethod
    def create_system_fields(cls, org):
        for key, spec in ContactField.SYSTEM_FIELDS.items():
            org.contactfields.create(
                field_type=ContactField.FIELD_TYPE_SYSTEM,
                key=key,
                label=spec["label"],
                value_type=spec["value_type"],
                show_in_table=False,
                created_by=org.created_by,
                modified_by=org.modified_by,
            )

    @classmethod
    def make_key(cls, label):
        """
        Generates a key from a label. There is no guarantee that the key is valid so should be checked with is_valid_key
        """
        key = regex.sub(r"([^a-z0-9]+)", " ", label.lower(), regex.V0)
        return regex.sub(r"([^a-z0-9]+)", "_", key.strip(), regex.V0)

    @classmethod
    def is_valid_key(cls, key):
        if not regex.match(r"^[a-z][a-z0-9_]*$", key, regex.V0):
            return False
        if key in Contact.RESERVED_FIELD_KEYS or len(key) > cls.MAX_KEY_LEN:
            return False
        return True

    @classmethod
    def is_valid_label(cls, label):
        label = label.strip()
        return regex.match(r"^[A-Za-z0-9\- ]+$", label, regex.V0) and len(label) <= cls.MAX_LABEL_LEN

    @classmethod
    def hide_field(cls, org, user, key):
        existing = ContactField.user_fields.collect_usage().active_for_org(org=org).filter(key=key).first()

        if existing:

            if any([existing.flow_count, existing.campaign_count, existing.contactgroup_count]):
                formatted_field_use = (
                    f"F: {existing.flow_count} C: {existing.campaign_count} G: {existing.contactgroup_count}"
                )
                raise ValueError(f"Cannot delete field '{key}', it's used by: {formatted_field_use}")

            existing.is_active = False
            existing.show_in_table = False
            existing.modified_by = user
            existing.save(update_fields=("is_active", "show_in_table", "modified_by", "modified_on"))

    @classmethod
    def get_or_create(cls, org, user, key, label=None, show_in_table=None, value_type=None, priority=None):
        """
        Gets the existing contact field or creates a new field if it doesn't exist

        This method only applies to ContactField.user_fields
        """
        if label:
            label = label.strip()

        with org.lock_on(OrgLock.field, key):
            field = ContactField.user_fields.active_for_org(org=org).filter(key__iexact=key).first()

            if not field and not key and label:
                # try to lookup the existing field by label
                field = ContactField.get_by_label(org, label)

            # we have a field with a invalid key we should ignore it
            if field and not ContactField.is_valid_key(field.key):
                field = None

            if field:
                changed = False

                # update whether we show in tables if passed in
                if show_in_table is not None and show_in_table != field.show_in_table:
                    field.show_in_table = show_in_table
                    changed = True

                # update our label if we were given one
                if label and field.label != label:
                    field.label = label
                    changed = True

                # update our type if we were given one
                if value_type and field.value_type != value_type:
                    # no changing away from datetime if we have campaign events
                    if (
                        field.value_type == Value.TYPE_DATETIME
                        and field.campaign_events.filter(is_active=True).exists()
                    ):
                        raise ValueError("Cannot change field type for '%s' while it is used in campaigns." % key)

                    field.value_type = value_type
                    changed = True

                if priority is not None and field.priority != priority:
                    field.priority = priority
                    changed = True

                if changed:
                    field.modified_by = user
                    field.save()

            else:
                # generate a label if we don't have one
                if not label:
                    label = unsnakify(key)

                label = cls.get_unique_label(org, label)

                if not value_type:
                    value_type = Value.TYPE_TEXT

                if show_in_table is None:
                    show_in_table = False

                if priority is None:
                    priority = 0

                if not ContactField.is_valid_key(key):
                    raise ValueError("Field key %s has invalid characters or is a reserved field name" % key)

                field = ContactField.user_fields.create(
                    org=org,
                    key=key,
                    label=label,
                    show_in_table=show_in_table,
                    value_type=value_type,
                    created_by=user,
                    modified_by=user,
                    priority=priority,
                )

            return field

    @classmethod
    def get_unique_label(cls, org, base_label, ignore=None):
        """
        Generates a unique field label based on the given base label
        """
        label = base_label[:64].strip()

        count = 2
        while True:
            if not ContactField.user_fields.filter(org=org, label=label, is_active=True).exists():
                break

            label = "%s %d" % (base_label[:59].strip(), count)
            count += 1

        return label

    @classmethod
    def get_by_label(cls, org, label):
        return cls.user_fields.active_for_org(org=org).filter(label__iexact=label).first()

    @classmethod
    def get_by_key(cls, org, key):
        return cls.user_fields.active_for_org(org=org).filter(key=key).first()

    @classmethod
    def get_location_field(cls, org, value_type):
        return cls.user_fields.active_for_org(org=org).filter(value_type=value_type).first()

    @classmethod
    def import_fields(cls, org, user, field_defs):
        """
        Import fields from a list of exported fields
        """

        db_types = {value: key for key, value in ContactField.GOFLOW_TYPES.items()}

        for field_def in field_defs:
            field_key = field_def.get(ContactField.EXPORT_KEY)
            field_name = field_def.get(ContactField.EXPORT_NAME)
            field_type = field_def.get(ContactField.EXPORT_TYPE)
            ContactField.get_or_create(org, user, key=field_key, label=field_name, value_type=db_types[field_type])

    def as_export_def(self):
        return {
            ContactField.EXPORT_KEY: self.key,
            ContactField.EXPORT_NAME: self.label,
            ContactField.EXPORT_TYPE: ContactField.GOFLOW_TYPES[self.value_type],
        }

    def release(self, user):
        self.is_active = False
        self.modified_by = user
        self.save(update_fields=("is_active", "modified_on", "modified_by"))

    def __str__(self):
        return "%s" % self.label


MAX_HISTORY = 50


class Contact(RequireUpdateFieldsMixin, TembaModel):
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="contacts")

    name = models.CharField(
        verbose_name=_("Name"), max_length=128, blank=True, null=True, help_text=_("The name of this contact")
    )

    language = models.CharField(
        max_length=3,
        verbose_name=_("Language"),
        null=True,
        blank=True,
        help_text=_("The preferred language for this contact"),
    )

    # whether contact has been blocked by a user
    is_blocked = models.BooleanField(default=False)

    # whether contact has opted out of receiving messages
    is_stopped = models.BooleanField(default=False)

    # custom field values for this contact, keyed by field UUID
    fields = JSONField(null=True)

    # user that last modified this contact
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_modifications",
    )

    # user that created this contact
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="%(app_label)s_%(class)s_creations", null=True
    )

    last_seen_on = models.DateTimeField(null=True)

    NAME = "name"
    FIRST_NAME = "first_name"
    LANGUAGE = "language"
    CREATED_ON = "created_on"
    PHONE = "phone"
    UUID = "uuid"
    CONTACT_UUID = "contact uuid"
    GROUPS = "groups"
    ID = "id"
    CREATED_ON_TITLE = "created on"

    RESERVED_ATTRIBUTES = {
        ID,
        NAME,
        FIRST_NAME,
        PHONE,
        LANGUAGE,
        GROUPS,
        UUID,
        CONTACT_UUID,
        CREATED_ON,
        CREATED_ON_TITLE,
        "created_by",
        "modified_by",
        "org",
        "is",
        "has",
        "tel_e164",
    }

    SUPPORTED_IMPORT_ATTRIBUTE_HEADERS = {ID, NAME, LANGUAGE, UUID, CONTACT_UUID}

    # can't create custom contact fields with these keys
    RESERVED_FIELD_KEYS = RESERVED_ATTRIBUTES.union(URN.VALID_SCHEMES)

    # the import headers which map to contact attributes or URNs rather than custom fields
    ATTRIBUTE_AND_URN_IMPORT_HEADERS = RESERVED_ATTRIBUTES.union(URN.IMPORT_HEADERS)

    STATUS_ACTIVE = "active"
    STATUS_BLOCKED = "blocked"
    STATUS_STOPPED = "stopped"

    @property
    def anon_identifier(self):
        """
        The displayable identifier used in place of URNs for anonymous orgs
        """
        return "%010d" % self.id

    @property
    def user_groups(self):
        """
        Define Contact.user_groups to only refer to user groups
        """
        return self.all_groups.filter(group_type=ContactGroup.TYPE_USER_DEFINED)

    def save(self, *args, handle_update=None, **kwargs):
        super().save(*args, **kwargs)

        # `handle_update` must be explicity set to execute handle_update when saving contact
        if self.id and "update_fields" in kwargs:
            if handle_update is None:
                raise ValueError("When saving contacts we need to specify value for `handle_update`.")

            if handle_update is True:
                self.handle_update(fields=kwargs["update_fields"])

    def as_json(self):
        obj = dict(id=self.pk, name=str(self), uuid=self.uuid)

        if not self.org.is_anon:
            urns = []
            for urn in self.urns.all():
                urns.append(dict(scheme=urn.scheme, path=urn.path, priority=urn.priority))
            obj["urns"] = urns

        return obj

    def as_search_json(self):
        def urn_as_json(urn):
            if self.org.is_anon:
                return {"scheme": urn.scheme, "path": ContactURN.ANON_MASK}
            return {"scheme": urn.scheme, "path": urn.path}

        return {
            "id": self.id,
            "uuid": self.uuid,
            "name": self.name,
            "language": self.language,
            "urns": [urn_as_json(u) for u in self.urns.all()],
            "fields": self.fields if self.fields else {},
            "created_on": self.created_on.isoformat(),
        }

    @classmethod
    def query_elasticsearch_for_ids(cls, org, query, group=None):
        from temba.contacts import search

        try:
            group_uuid = group.uuid if group else ""
            parsed = search.parse_query(org.id, query, group_uuid=group_uuid)
            results = (
                es.ModelESSearch(model=Contact, index="contacts")
                .source(include=["id"])
                .params(routing=org.id)
                .using(es.ES)
                .query(parsed.elastic_query)
            )
            matches = []
            for r in results.scan():
                matches.append(int(r.id))

            return matches
        except search.SearchException:
            logger.error("Error evaluating query", exc_info=True)
            raise  # reraise the exception

    def get_scheduled_messages(self):
        from temba.msgs.models import SystemLabel

        contact_urns = self.get_urns()
        contact_groups = self.user_groups.all()
        now = timezone.now()

        scheduled_broadcasts = SystemLabel.get_queryset(self.org, SystemLabel.TYPE_SCHEDULED)
        scheduled_broadcasts = scheduled_broadcasts.exclude(schedule__next_fire=None)
        scheduled_broadcasts = scheduled_broadcasts.filter(schedule__next_fire__gte=now)
        scheduled_broadcasts = scheduled_broadcasts.filter(
            Q(contacts__in=[self]) | Q(urns__in=contact_urns) | Q(groups__in=contact_groups)
        )

        return scheduled_broadcasts.order_by("schedule__next_fire")

    def get_history(self, after, before):
        """
        Gets this contact's history of messages, calls, runs etc in the given time window
        """
        from temba.ivr.models import IVRCall
        from temba.msgs.models import Msg, INCOMING, OUTGOING

        msgs = list(
            self.msgs.filter(created_on__gte=after, created_on__lt=before)
            .exclude(visibility=Msg.VISIBILITY_DELETED)
            .order_by("-created_on")
            .select_related("channel")
            .prefetch_related("channel_logs")[:MAX_HISTORY]
        )
        msgs_in = filter(lambda m: m.direction == INCOMING, msgs)
        msgs_out = filter(lambda m: m.direction == OUTGOING, msgs)

        # and all of this contact's runs, channel events such as missed calls, scheduled events
        started_runs = (
            self.runs.filter(created_on__gte=after, created_on__lt=before)
            .exclude(flow__is_system=True)
            .order_by("-created_on")
            .select_related("flow")[:MAX_HISTORY]
        )

        exited_runs = (
            self.runs.filter(exited_on__gte=after, exited_on__lt=before)
            .exclude(flow__is_system=True)
            .exclude(exit_type=None)
            .order_by("-created_on")
            .select_related("flow")[:MAX_HISTORY]
        )

        channel_events = (
            self.channel_events.filter(created_on__gte=after, created_on__lt=before)
            .order_by("-created_on")
            .select_related("channel")[:MAX_HISTORY]
        )

        campaign_events = (
            self.campaign_fires.filter(fired__gte=after, fired__lt=before)
            .exclude(fired=None)
            .order_by("-fired")
            .select_related("event__campaign")[:MAX_HISTORY]
        )

        webhook_results = self.webhook_results.filter(created_on__gte=after, created_on__lt=before).order_by(
            "-created_on"
        )[:MAX_HISTORY]

        calls = (
            IVRCall.objects.filter(contact=self, created_on__gte=after, created_on__lt=before)
            .filter(status__in=[IVRCall.BUSY, IVRCall.FAILED, IVRCall.NO_ANSWER, IVRCall.CANCELED, IVRCall.COMPLETED])
            .order_by("-created_on")
            .select_related("channel")[:MAX_HISTORY]
        )

        transfers = self.airtime_transfers.filter(created_on__gte=after, created_on__lt=before).order_by(
            "-created_on"
        )[:MAX_HISTORY]

        session_events = self.get_session_events(after, before, HISTORY_INCLUDE_EVENTS)

        # wrap items, chain and sort by time
        events = chain(
            [{"type": "msg_created", "created_on": m.created_on, "obj": m} for m in msgs_out],
            [{"type": "msg_received", "created_on": m.created_on, "obj": m} for m in msgs_in],
            [{"type": "flow_entered", "created_on": r.created_on, "obj": r} for r in started_runs],
            [{"type": "flow_exited", "created_on": r.exited_on, "obj": r} for r in exited_runs],
            [{"type": "channel_event", "created_on": e.created_on, "obj": e} for e in channel_events],
            [{"type": "campaign_fired", "created_on": f.fired, "obj": f} for f in campaign_events],
            [{"type": "webhook_called", "created_on": r.created_on, "obj": r} for r in webhook_results],
            [{"type": "call_started", "created_on": c.created_on, "obj": c} for c in calls],
            [{"type": "airtime_transferred", "created_on": t.created_on, "obj": t} for t in transfers],
            session_events,
        )

        return sorted(events, key=lambda i: i["created_on"], reverse=True)[:MAX_HISTORY]

    def get_session_events(self, after, before, types):
        """
        Extracts events from this contacts sessions that overlap with the given time window
        """
        sessions = self.sessions.filter(
            Q(created_on__gte=after, created_on__lt=before) | Q(ended_on__gte=after, ended_on__lt=before)
        )
        events = []
        for session in sessions:
            for run in session.output.get("runs", []):
                for event in run.get("events", []):
                    event["session_uuid"] = str(session.uuid)
                    event["created_on"] = iso8601.parse_date(event["created_on"])

                    if event["type"] in types and after <= event["created_on"] < before:
                        events.append(event)

        return events

    def get_field_json(self, field):
        """
        Returns the JSON (as a dict) value for this field, or None if there is no value
        """
        assert field.field_type == ContactField.FIELD_TYPE_USER, f"not supported for system field {field.key}"

        return self.fields.get(str(field.uuid)) if self.fields else None

    def get_field_serialized(self, field):
        """
        Given the passed in contact field object, returns the value (as a string) for this contact or None.
        """
        json_value = self.get_field_json(field)
        if not json_value:
            return

        if field.value_type == Value.TYPE_TEXT:
            return json_value.get(Value.KEY_TEXT)
        elif field.value_type == Value.TYPE_DATETIME:
            return json_value.get(Value.KEY_DATETIME)
        elif field.value_type == Value.TYPE_NUMBER:
            dec_value = json_value.get(Value.KEY_NUMBER, json_value.get("decimal"))
            return format_number(Decimal(dec_value)) if dec_value is not None else None
        elif field.value_type == Value.TYPE_STATE:
            return json_value.get(Value.KEY_STATE)
        elif field.value_type == Value.TYPE_DISTRICT:
            return json_value.get(Value.KEY_DISTRICT)
        elif field.value_type == Value.TYPE_WARD:
            return json_value.get(Value.KEY_WARD)

        raise ValueError("unknown contact field value type: %s", field.value_type)

    def get_field_value(self, field):
        """
        Given the passed in contact field object, returns the value (as a string, decimal, datetime, AdminBoundary)
        for this contact or None.
        """
        if field.field_type == ContactField.FIELD_TYPE_USER:
            string_value = self.get_field_serialized(field)
            if string_value is None:
                return None

            if field.value_type == Value.TYPE_TEXT:
                return string_value
            elif field.value_type == Value.TYPE_DATETIME:
                return iso8601.parse_date(string_value)
            elif field.value_type == Value.TYPE_NUMBER:
                return Decimal(string_value)
            elif field.value_type in [Value.TYPE_STATE, Value.TYPE_DISTRICT, Value.TYPE_WARD]:
                return AdminBoundary.get_by_path(self.org, string_value)

        elif field.field_type == ContactField.FIELD_TYPE_SYSTEM:
            if field.key == "created_on":
                return self.created_on
            if field.key == "last_seen_on":
                return self.last_seen_on
            elif field.key == "language":
                return self.language
            elif field.key == "name":
                return self.name
            else:
                raise ValueError(f"System contact field '{field.key}' is not supported")

        else:  # pragma: no cover
            raise ValueError(f"Unhandled ContactField type '{field.field_type}'.")

    def get_field_display(self, field):
        """
        Returns the display value for the passed in field, or empty string if None
        """
        value = self.get_field_value(field)
        if value is None:
            return ""

        if field.value_type == Value.TYPE_DATETIME:
            return self.org.format_datetime(value)
        elif field.value_type == Value.TYPE_NUMBER:
            return format_number(value)
        elif field.value_type in [Value.TYPE_STATE, Value.TYPE_DISTRICT, Value.TYPE_WARD] and value:
            return value.name
        else:
            return str(value)

    def serialize_field(self, field, value):
        # parse as all value data types
        str_value = str(value)[: Value.MAX_VALUE_LEN]
        dt_value = self.org.parse_datetime(value)
        num_value = self.org.parse_number(value)
        loc_value = None

        # for locations, if it has a '>' then it is explicit, look it up that way
        if AdminBoundary.PATH_SEPARATOR in str_value:
            loc_value = self.org.parse_location_path(str_value)

        # otherwise, try to parse it as a name at the appropriate level
        else:
            if field.value_type == Value.TYPE_WARD:
                district_field = ContactField.get_location_field(self.org, Value.TYPE_DISTRICT)
                district_value = self.get_field_value(district_field)
                if district_value:
                    loc_value = self.org.parse_location(str_value, AdminBoundary.LEVEL_WARD, district_value)

            elif field.value_type == Value.TYPE_DISTRICT:
                state_field = ContactField.get_location_field(self.org, Value.TYPE_STATE)
                if state_field:
                    state_value = self.get_field_value(state_field)
                    if state_value:
                        loc_value = self.org.parse_location(str_value, AdminBoundary.LEVEL_DISTRICT, state_value)

            elif field.value_type == Value.TYPE_STATE:
                loc_value = self.org.parse_location(str_value, AdminBoundary.LEVEL_STATE)

            if loc_value is not None and len(loc_value) > 0:
                loc_value = loc_value[0]
            else:
                loc_value = None

        # all fields have a text value
        field_dict = {Value.KEY_TEXT: str_value}

        # set all the other fields that have a non-zero value
        if dt_value is not None:
            field_dict[Value.KEY_DATETIME] = timezone.localtime(dt_value, self.org.timezone).isoformat()

        if num_value is not None:
            field_dict[Value.KEY_NUMBER] = format_number(num_value)

        if loc_value:
            if loc_value.level == AdminBoundary.LEVEL_STATE:
                field_dict[Value.KEY_STATE] = loc_value.path
            elif loc_value.level == AdminBoundary.LEVEL_DISTRICT:
                field_dict[Value.KEY_DISTRICT] = loc_value.path
                field_dict[Value.KEY_STATE] = AdminBoundary.strip_last_path(loc_value.path)
            elif loc_value.level == AdminBoundary.LEVEL_WARD:
                field_dict[Value.KEY_WARD] = loc_value.path
                field_dict[Value.KEY_DISTRICT] = AdminBoundary.strip_last_path(loc_value.path)
                field_dict[Value.KEY_STATE] = AdminBoundary.strip_last_path(field_dict[Value.KEY_DISTRICT])

        return field_dict

    def set_fields(self, user, fields):
        """
        Sets multiple field values on a contact - used by imports
        """
        if self.fields is None:
            self.fields = {}

        fields_for_delete = set()
        fields_for_update = set()
        changed_field_keys = set()
        all_fields = {}

        for key, value in fields.items():
            field = ContactField.get_or_create(self.org, user, key)

            field_uuid = str(field.uuid)

            # parse into the appropriate value types
            if value is None or value == "":
                # value being cleared, remove our key
                if field_uuid in self.fields:  # pragma: no cover
                    fields_for_delete.add(field_uuid)

                    changed_field_keys.add(key)

            else:
                field_dict = self.serialize_field(field, value)

                # update our field if it is different
                if self.fields.get(field_uuid) != field_dict:
                    fields_for_update.add(field_uuid)
                    all_fields.update({field_uuid: field_dict})

                    changed_field_keys.add(key)

        modified_on = timezone.now()

        # if there was a change, update our JSONB on our contact
        if fields_for_delete:  # pragma: no cover
            with connection.cursor() as cursor:
                # prepare expression for multiple field delete
                remove_fields = " - ".join(f"%s" for _ in range(len(fields_for_delete)))
                cursor.execute(
                    f"UPDATE contacts_contact SET fields = fields - {remove_fields}, modified_on = %s WHERE id = %s",
                    [*fields_for_delete, modified_on, self.id],
                )

        if fields_for_update:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE contacts_contact SET fields = COALESCE(fields,'{}'::jsonb) || %s::jsonb, modified_on = %s WHERE id = %s",
                    [json.dumps(all_fields), modified_on, self.id],
                )

        # update local contact cache
        self.fields.update(all_fields)

        # remove deleted fields
        for field_uuid in fields_for_delete:  # pragma: no cover
            self.fields.pop(field_uuid, None)

        self.modified_on = modified_on

        if changed_field_keys:
            self.handle_update(fields=list(fields.keys()))

    def handle_update(self, urns=(), fields=None, group=None, is_new=False):
        """
        Handles an update to a contact which can be one of
          1. A change to one or more attributes
          2. A change to the specified contact field
          3. A manual change to a group membership
        """
        changed_groups = set([group]) if group else set()
        if fields or urns or is_new:
            # ensure dynamic groups are up to date
            changed_groups.update(self.reevaluate_dynamic_groups(for_fields=fields, urns=urns))

        # ensure our campaigns are up to date
        from temba.campaigns.models import EventFire

        if fields:
            EventFire.update_events_for_contact_fields(contact=self, keys=fields)

        if changed_groups:
            # ensure our campaigns are up to date
            EventFire.update_events_for_contact_groups(self, changed_groups)

    def update(self, name: str, language: str) -> List[modifiers.Modifier]:
        """
        Updates attributes of this contact
        """
        mods = []
        if (self.name or "") != (name or ""):
            mods.append(modifiers.Name(name or ""))

        if (self.language or "") != (language or ""):
            mods.append(modifiers.Language(language or ""))

        return mods

    def update_fields(self, values: Dict[ContactField, str]) -> List[modifiers.Modifier]:
        """
        Updates custom field values of this contact
        """
        mods = []

        for field, value in values.items():
            field_ref = modifiers.FieldRef(key=field.key, name=field.label)
            mods.append(modifiers.Field(field=field_ref, value=value))

        return mods

    def update_static_groups(self, groups) -> List[modifiers.Modifier]:
        """
        Updates the static groups for this contact to match the provided list
        """
        assert not [g for g in groups if g.is_dynamic], "can't update membership of a dynamic group"

        current = self.user_groups.filter(query=None)

        # figure out our diffs, what groups need to be added or removed
        to_remove = [g for g in current if g not in groups]
        to_add = [g for g in groups if g not in current]

        def refs(gs):
            return [modifiers.GroupRef(uuid=str(g.uuid), name=g.name) for g in gs]

        mods = []

        if to_remove:
            mods.append(modifiers.Groups(groups=refs(to_remove), modification="remove"))
        if to_add:
            mods.append(modifiers.Groups(groups=refs(to_add), modification="add"))

        return mods

    def update_urns(self, urns: List[str]) -> List[modifiers.Modifier]:
        return [modifiers.URNs(urns=urns, modification="set")]

    def modify(self, user, mods: List[modifiers.Modifier], refresh=True):
        self.bulk_modify(user, [self], mods)
        if refresh:
            self.refresh_from_db()

    @classmethod
    def bulk_modify(cls, user, contacts, mods: List[modifiers.Modifier]):
        if not contacts:
            return

        org = contacts[0].org
        client = mailroom.get_client()
        try:
            response = client.contact_modify(org.id, user.id, [c.id for c in contacts], mods)
        except mailroom.MailroomException as e:
            logger.error(f"Contact update failed: {str(e)}", exc_info=True)
            raise e

        def modified(contact):
            return len(response.get(contact.id, {}).get("events", [])) > 0

        return [c.id for c in contacts if modified(c)]

    @classmethod
    def from_urn(cls, org, urn_as_string, country=None):
        """
        Looks up a contact by a URN string (which will be normalized)
        """
        try:
            urn_obj = ContactURN.lookup(org, urn_as_string, country)
        except ValueError:
            return None

        if urn_obj and urn_obj.contact and urn_obj.contact.is_active:
            return urn_obj.contact
        else:
            return None

    @classmethod
    def get_or_create(cls, org, urn, channel=None, name=None, auth=None, user=None, init_new=True):
        """
        Gets or creates a contact with the given URN
        """

        # if we don't have an org blow up, this is required
        if not org:
            raise ValueError("Attempt to create contact without org")

        # get country from channel or org
        if channel:
            country = channel.country.code
        else:
            country = org.get_country_code()

        # limit our contact name to 128 chars
        if name:
            name = name[:128]

        normalized = URN.normalize(urn, country)
        existing_urn = ContactURN.lookup(org, normalized, normalize=False, country_code=country)

        if existing_urn and existing_urn.contact:
            contact = existing_urn.contact
            ContactURN.update_auth(existing_urn, auth)
            return contact, existing_urn
        else:
            kwargs = dict(org=org, name=name, created_by=user)
            contact = Contact.objects.create(**kwargs)
            contact.is_new = True
            updated_attrs = list(kwargs.keys())

            if existing_urn:
                ContactURN.objects.filter(pk=existing_urn.pk).update(contact=contact)
                urn_obj = existing_urn
            else:
                urn_obj = ContactURN.get_or_create(org, contact, normalized, channel=channel, auth=auth)

            updated_urns = [urn]

            # record contact creation in analytics
            analytics.gauge("temba.contact_created")

            # handle group and campaign updates
            if init_new:
                contact.handle_update(fields=updated_attrs, urns=updated_urns, is_new=True)

            return contact, urn_obj

    @classmethod
    def get_or_create_by_urns(
        cls, org, user, name=None, urns=None, channel=None, uuid=None, language=None, force_urn_update=False, auth=None
    ):
        """
        Gets or creates a contact with the given URNs
        """
        # if we don't have an org or user, blow up, this is required
        if not org or not user:
            raise ValueError("Attempt to create contact without org or user")

        # if channel is specified then urns should contain the single URN that communicated with the channel
        if channel and (not urns or len(urns) > 1):
            raise ValueError("Only one URN may be specified when calling from channel event")

        # deal with None being passed into urns
        if urns is None:
            urns = ()

        # get country from channel or org
        if channel:
            country = channel.country.code
        else:
            country = org.get_country_code()

        contact = None

        # limit our contact name to 128 chars
        if name:
            name = name[:128]

        # optimize the single URN contact lookup case with an existing contact, this doesn't need a lock as
        # it is read only from a contacts perspective, but it is by far the most common case
        if not uuid and not name and urns and len(urns) == 1:
            existing_urn = ContactURN.lookup(org, urns[0], country)

            if existing_urn and existing_urn.contact:
                contact = existing_urn.contact
                ContactURN.update_auth(existing_urn, auth)
                return contact

        # if we were passed in a UUID, look it up by that
        if uuid:
            contact = Contact.objects.filter(org=org, is_active=True, uuid=uuid).first()

            # if contact already exists try to figured if it has all the urn to skip the lock
            if contact:
                contact_has_all_urns = True
                contact_urns = set(contact.get_urns().values_list("identity", flat=True))
                if len(urns) <= len(contact_urns):
                    for urn in urns:
                        normalized = URN.normalize(urn, country)
                        identity = URN.identity(normalized)
                        if identity not in contact_urns:
                            contact_has_all_urns = False

                        existing_urn = ContactURN.lookup(org, normalized, country_code=country, normalize=False)
                        if existing_urn and auth:
                            ContactURN.update_auth(existing_urn, auth)

                    if contact_has_all_urns:
                        # update contact name if provided
                        updated_attrs = []
                        if name:
                            contact.name = name
                            updated_attrs.append(ContactField.KEY_NAME)
                        if language:  # pragma: needs cover
                            contact.language = language
                            updated_attrs.append(ContactField.KEY_LANGUAGE)

                        if updated_attrs:
                            contact.save(update_fields=updated_attrs + ["modified_on"], handle_update=False)
                        # handle group and campaign updates
                        contact.handle_update(fields=updated_attrs)
                        return contact

        # perform everything in a org-level lock to prevent duplication by different instances
        with org.lock_on(OrgLock.contacts):
            # figure out which URNs already exist and who they belong to
            existing_owned_urns = dict()
            existing_orphan_urns = dict()
            urns_to_create = dict()
            for urn in urns:
                normalized = URN.normalize(urn, country)
                existing_urn = ContactURN.lookup(org, normalized, normalize=False, country_code=country)

                if existing_urn:
                    if existing_urn.contact and not force_urn_update:
                        existing_owned_urns[urn] = existing_urn
                        if contact and contact != existing_urn.contact:
                            raise ValueError(_("Provided URNs belong to different existing contacts"))
                        else:
                            contact = existing_urn.contact
                    else:
                        existing_orphan_urns[urn] = existing_urn
                        if not contact and existing_urn.contact:
                            contact = existing_urn.contact

                    ContactURN.update_auth(existing_urn, auth)

                else:
                    urns_to_create[urn] = normalized

            # URNs correspond to one contact so update and return that
            if contact:
                contact.is_new = False
                # update contact name if provided
                updated_attrs = []
                if name:
                    contact.name = name
                    updated_attrs.append(ContactField.KEY_NAME)
                if language:
                    contact.language = language
                    updated_attrs.append(ContactField.KEY_LANGUAGE)

                if updated_attrs:
                    contact.save(update_fields=updated_attrs + ["modified_on"], handle_update=False)

            # otherwise create new contact with all URNs
            else:
                kwargs = dict(org=org, name=name, language=language, created_by=user)
                contact = Contact.objects.create(**kwargs)
                updated_attrs = ["name", "language", "created_on"]

                # add attribute which allows import process to track new vs existing
                contact.is_new = True

            # attach all orphaned URNs
            ContactURN.objects.filter(pk__in=[urn.id for urn in existing_orphan_urns.values()]).update(contact=contact)

            # create dict of all requested URNs and actual URN objects
            urn_objects = existing_orphan_urns.copy()

            # add all new URNs
            for raw, normalized in urns_to_create.items():
                urn = ContactURN.get_or_create(org, contact, normalized, channel=channel, auth=auth)
                urn_objects[raw] = urn

            # save which urns were updated
            updated_urns = list(urn_objects.keys())

        # record contact creation in analytics
        if getattr(contact, "is_new", False):
            analytics.gauge("temba.contact_created")

        # handle group and campaign updates
        contact.handle_update(fields=updated_attrs, urns=updated_urns, is_new=contact.is_new)
        return contact

    @classmethod
    def create_instance(cls, field_dict):
        """
        Creates or updates a contact from the given field values during an import
        """
        if "org" not in field_dict or "created_by" not in field_dict:
            raise ValueError("Import fields dictionary must include org and created_by")

        org = field_dict.pop("org")
        user = field_dict.pop("created_by")
        is_admin = org.administrators.filter(id=user.id).exists()
        uuid = field_dict.pop("contact uuid", None)

        # for backward compatibility
        if uuid is None:
            uuid = field_dict.pop("uuid", None)

        country = org.get_country_code()
        urns = []

        possible_urn_headers = [scheme[0] for scheme in IMPORT_HEADERS]
        possible_urn_headers_case_insensitive = [scheme.lower() for scheme in possible_urn_headers]

        # prevent urns update on anon org
        if uuid and org.is_anon and not is_admin:
            possible_urn_headers_case_insensitive = []

        for urn_header in possible_urn_headers_case_insensitive:
            value = None
            if urn_header in field_dict:
                value = field_dict[urn_header]
                del field_dict[urn_header]

            if not value:
                continue

            value = str(value)

            urn_scheme = ContactURN.IMPORT_HEADER_TO_SCHEME[urn_header]

            if urn_scheme == TEL_SCHEME:

                value = regex.sub(r"[ \-()]+", "", value, regex.V0)

                # at this point the number might be a decimal, something that looks like '18094911278.0' due to
                # excel formatting that field as numeric.. try to parse it into an int instead
                try:
                    value = str(int(float(value)))
                except Exception:  # pragma: no cover
                    # oh well, neither of those, stick to the plan, maybe we can make sense of it below
                    pass

                # only allow valid numbers
                (normalized, is_valid) = URN.normalize_number(value, country)

                if not is_valid:
                    error_msg = f"Invalid Phone number {value}"
                    if not country:
                        error_msg = f"Invalid Phone number or no country code specified for {value}"

                    raise SmartImportRowError(error_msg)

                # in the past, test contacts have ended up in exports. Don't re-import them
                if value == OLD_TEST_CONTACT_TEL:
                    raise SmartImportRowError("Ignored test contact")

            urn = URN.normalize(URN.from_parts(urn_scheme, value), country)
            if not URN.validate(urn):
                raise SmartImportRowError(f"Invalid URN: {value}")

            search_contact = Contact.from_urn(org, urn, country)

            # if this is an anonymous org, don't allow updating
            if org.is_anon and search_contact and not is_admin:
                raise SmartImportRowError("Other existing contact in anonymous workspace")

            urns.append(urn)

        if not urns and not (org.is_anon or uuid):
            urn_headers = ", ".join(possible_urn_headers)
            raise SmartImportRowError(
                f"Missing any valid URNs; at least one among {urn_headers} should be provided or a Contact UUID"
            )

        # title case our name
        name = field_dict.get(ContactField.KEY_NAME, None)
        if name:
            name = " ".join([_.capitalize() for _ in name.split()])

        language = field_dict.get(ContactField.KEY_LANGUAGE)
        if language is not None and len(language) != 3:
            language = None
        if language is not None and _get_language_name_iso6393(language) is None:
            raise SmartImportRowError(f"Language: '{language}' is not a valid ISO639-3 code")

        # if this is just a UUID import, look up the contact directly
        if uuid and not urns and not language and not name:
            contact = org.contacts.filter(uuid=uuid).first()
            if not contact:
                raise SmartImportRowError(f"No contact found with uuid: {uuid}")

        else:
            # create new contact or fetch existing one
            contact = Contact.get_or_create_by_urns(
                org, user, name, uuid=uuid, urns=urns, language=language, force_urn_update=True
            )

        # if they exist and are blocked, reactivate them
        if contact.is_blocked:
            contact.reactivate(user)

        # ignore any reserved fields or URN schemes
        valid_keys = (
            key
            for key in field_dict.keys()
            if not (key in Contact.ATTRIBUTE_AND_URN_IMPORT_HEADERS or key.startswith("urn:"))
        )

        valid_field_dict = {}
        for key in valid_keys:
            value = field_dict[key]

            # date values need converted to localized strings
            if isinstance(value, datetime.date):
                # make naive datetime timezone-aware, ignoring date
                if getattr(value, "tzinfo", "ignore") is None:
                    value = org.timezone.localize(value) if org.timezone else pytz.utc.localize(value)

                value = org.format_datetime(value, True)

            valid_field_dict.update({key: value})

        contact.set_fields(user, valid_field_dict)

        return contact

    @classmethod
    def prepare_fields(cls, field_dict, import_params=None, user=None):
        if not import_params or "org_id" not in import_params or "extra_fields" not in import_params:
            raise ValueError("Import params must include org_id and extra_fields")

        field_dict["created_by"] = user
        field_dict["org"] = Org.objects.get(pk=import_params["org_id"])

        extra_fields = []

        # include extra fields specified in the params
        for field in import_params["extra_fields"]:
            key = field["key"]
            label = field["label"]
            if key not in Contact.ATTRIBUTE_AND_URN_IMPORT_HEADERS:
                # column values are mapped to lower-cased column header names but we need them by contact field key
                value = field_dict[field["header"]]
                del field_dict[field["header"]]
                field_dict[key] = value

                # create the contact field if it doesn't exist
                ContactField.get_or_create(field_dict["org"], user, key, label, value_type=field["type"])

                extra_fields.append(key)
            else:
                raise ValueError("Extra field %s is a reserved field name" % key)

        active_scheme_headers = [h[0].lower() for h in IMPORT_HEADERS]

        # remove any field that's not a reserved field or an explicitly included extra field
        return {
            key: value
            for key, value in field_dict.items()
            if not (
                (key not in Contact.ATTRIBUTE_AND_URN_IMPORT_HEADERS)
                and key not in extra_fields
                and key not in active_scheme_headers
            )
        }

    @classmethod
    def get_org_import_file_headers(cls, csv_file, org):
        csv_file.open()

        # this file isn't good enough, lets write it to local disk
        from django.conf import settings

        # make sure our tmp directory is present (throws if already present)
        try:
            os.makedirs(os.path.join(settings.MEDIA_ROOT, "tmp"))
        except Exception:
            pass

        # write our file out
        tmp_file = os.path.join(settings.MEDIA_ROOT, "tmp/%s" % str(uuid.uuid4()))

        out_file = open(tmp_file, "wb")
        out_file.write(csv_file.read())
        out_file.close()

        try:
            headers = SmartModel.get_import_file_headers(open(tmp_file))
        finally:
            os.remove(tmp_file)

        Contact.validate_org_import_header(headers, org)

        # return the column headers which can become contact fields
        possible_fields = []
        for header in headers:
            header = header.strip().lower()
            if not header.startswith("field:"):
                continue

            if header and header not in Contact.ATTRIBUTE_AND_URN_IMPORT_HEADERS:
                possible_fields.append(header)

        return possible_fields

    @classmethod
    def validate_org_import_header(cls, headers, org):
        possible_headers = [h[0] for h in IMPORT_HEADERS]
        possible_headers_case_insensitive = [h.lower() for h in possible_headers]

        found_headers = []
        unsupported_headers = []

        for h in headers:
            h_lower_stripped = h.strip().lower()

            if h_lower_stripped in possible_headers_case_insensitive:
                found_headers.append(h_lower_stripped)

            if (
                h_lower_stripped
                and not h_lower_stripped.startswith("urn:")
                and not h_lower_stripped.startswith("field:")
                and not h_lower_stripped.startswith("group:")
                and h_lower_stripped not in Contact.SUPPORTED_IMPORT_ATTRIBUTE_HEADERS
                and h_lower_stripped != Contact.CREATED_ON_TITLE
            ):
                unsupported_headers.append(h_lower_stripped)

        joined_possible_headers = '", "'.join([h for h in possible_headers])
        joined_unsupported_headers = '", "'.join([h for h in unsupported_headers])

        if unsupported_headers:
            raise Exception(
                _(
                    f'The provided file has unrecognized headers. Columns "{joined_unsupported_headers}" should be removed or prepended with the prefix "Field:".'
                )
            )

        if "uuid" in headers or "contact uuid" in headers:
            return

        if not found_headers:
            raise Exception(
                _(
                    f'The file you provided is missing a required header. At least one of "{joined_possible_headers}" or "Contact UUID" should be included.'
                )
            )

        if "name" not in headers:
            raise Exception(_('The file you provided is missing a required header called "Name".'))

    @classmethod
    def normalize_value(cls, val):
        if isinstance(val, str):
            return SmartModel.normalize_value(val)
        return val

    @classmethod
    def import_excel(cls, filename, user, import_params, task, log=None, import_results=None):

        import pyexcel

        sheet_data = pyexcel.get_array(file_name=filename.name)

        line_number = 0

        header = sheet_data[line_number]
        line_number += 1
        while header is not None and len(header[0]) > 1 and header[0][0] == "#":  # pragma: needs cover
            header = sheet_data[line_number]
            line_number += 1

        # do some sanity checking to make sure they uploaded the right kind of file
        if len(header) < 1:  # pragma: needs cover
            raise Exception("Invalid header for import file")

        # normalize our header names, removing quotes and spaces
        header = [cls.normalize_value(str(cell_value)).lower() for cell_value in header]

        cls.validate_import_header(header)

        records = []
        num_errors = 0
        error_messages = []
        row_processed = 0

        sheet_data_records = sheet_data[line_number:]

        for row in sheet_data_records:
            row_processed += 1

            if row_processed % 100 == 0:  # pragma: no cover
                task.modified_on = timezone.now()
                task.save(update_fields=["modified_on"])

            # trim all our values
            row_data = []
            for cell in row:
                cell_value = cls.normalize_value(cell)
                if not isinstance(cell_value, datetime.date) and not isinstance(cell_value, datetime.datetime):
                    cell_value = str(cell_value)
                row_data.append(cell_value)

            line_number += 1

            # make sure there are same number of fields
            if len(row_data) != len(header):  # pragma: needs cover
                raise Exception(
                    "Line %d: The number of fields for this row is incorrect. Expected %d but found %d."
                    % (line_number, len(header), len(row_data))
                )

            field_values = dict(zip(header, row_data))
            log_field_values = field_values.copy()
            field_values["created_by"] = user
            try:

                field_values = cls.prepare_fields(field_values, import_params, user)
                record = cls.create_instance(field_values)
                if record:
                    records.append(record)
                else:  # pragma: needs cover
                    num_errors += 1

            except SmartImportRowError as e:
                error_messages.append(dict(line=line_number, error=str(e)))

            except Exception as e:  # pragma: needs cover
                if log:
                    import traceback

                    traceback.print_exc(limit=100, file=log)
                raise Exception("Line %d: %s\n\n%s" % (line_number, str(e), str(log_field_values)))

        if import_results is not None:
            import_results["records"] = len(records)
            import_results["errors"] = num_errors + len(error_messages)
            import_results["error_messages"] = error_messages

        return records

    @classmethod
    def finalize_import(cls, task, records):
        for chunk in chunk_list(records, 1000):
            Contact.objects.filter(id__in=[c.id for c in chunk]).update(modified_on=timezone.now())

    @classmethod
    def import_csv(cls, task, log=None):
        import pyexcel

        filename = task.csv_file.file
        user = task.created_by

        # additional parameters are optional
        import_params = None
        if task.import_params:
            try:
                import_params = json.loads(task.import_params)
            except Exception:  # pragma: needs cover
                logger.error("Failed to parse JSON for contact import #d" % task.pk, exc_info=True)

        # this file isn't good enough, lets write it to local disk
        # make sure our tmp directory is present (throws if already present)
        try:
            os.makedirs(os.path.join(settings.MEDIA_ROOT, "tmp"))
        except Exception:
            pass

        # rewrite our file to local disk
        extension = filename.name.rpartition(".")[2]
        tmp_file = os.path.join(settings.MEDIA_ROOT, "tmp/%s.%s" % (str(uuid4()), extension.lower()))
        filename.open()

        out_file = open(tmp_file, "wb")
        out_file.write(filename.read())
        out_file.close()

        # convert the file to CSV
        csv_tmp_file = os.path.join(settings.MEDIA_ROOT, "tmp/%s.csv" % str(uuid4()))

        pyexcel.save_as(file_name=out_file.name, dest_file_name=csv_tmp_file)

        import_results = dict()

        try:
            contacts = cls.import_excel(open(tmp_file), user, import_params, task, log, import_results)
        finally:
            os.remove(tmp_file)
            os.remove(csv_tmp_file)

        # save the import results even if no record was created
        task.import_results = json.dumps(import_results)

        # don't create a group if there are no contacts
        if not contacts:
            return contacts

        # we always create a group after a successful import (strip off 8 character uniquifier by django)
        group_name = os.path.splitext(os.path.split(import_params.get("original_filename"))[-1])[0]
        group_name = group_name.replace("_", " ").replace("-", " ").title()

        if len(group_name) >= ContactGroup.MAX_NAME_LEN - 10:
            group_name = group_name[: ContactGroup.MAX_NAME_LEN - 10]

        # group org is same as org of any contact in that group
        group_org = contacts[0].org
        group = ContactGroup.create_static(
            group_org, user, group_name, status=ContactGroup.STATUS_INITIALIZING, task=task
        )

        num_creates = 0
        for contact in contacts:
            # if contact has is_new attribute, then we have created a new contact rather than updated an existing one
            if getattr(contact, "is_new", False):
                num_creates += 1

            # do not add blocked or stopped contacts
            if not contact.is_stopped and not contact.is_blocked:
                group.contacts.add(contact)

        # group is now ready to be used in a flow starts etc
        group.status = ContactGroup.STATUS_READY
        group.save(update_fields=("status",))

        # if we aren't verified, check for sequential phone numbers
        if not group_org.is_verified():
            try:
                # get all of our phone numbers for the imported contacts
                paths = [
                    int(u.path)
                    for u in ContactURN.objects.filter(scheme=TEL_SCHEME, contact__in=[c.pk for c in contacts])
                ]
                paths = sorted(paths)

                last_path = None
                sequential = 0
                for path in paths:
                    if last_path:
                        if path - last_path == 1:
                            sequential += 1
                    last_path = path

                    if sequential > SEQUENTIAL_CONTACTS_THRESHOLD:
                        group_org.flag()
                        break

            except Exception:  # pragma: no cover
                # if we fail to parse phone numbers for any reason just punt
                pass

        # overwrite the import results for adding the counts
        import_results["creates"] = num_creates
        import_results["updates"] = len(contacts) - num_creates
        task.import_results = json.dumps(import_results)

        return contacts

    @classmethod
    def bulk_change_status(cls, user, contacts, status):
        cls.bulk_modify(user, contacts, [modifiers.Status(status=status)])

    @classmethod
    def bulk_change_group(cls, user, contacts, group, add: bool):
        mod = modifiers.Groups(
            groups=[modifiers.GroupRef(uuid=str(group.uuid), name=group.name)], modification="add" if add else "remove"
        )
        cls.bulk_modify(user, contacts, mods=[mod])

    @classmethod
    def apply_action_block(cls, user, contacts):
        cls.bulk_change_status(user, contacts, Contact.STATUS_BLOCKED)

    @classmethod
    def apply_action_unblock(cls, user, contacts):
        cls.bulk_change_status(user, contacts, Contact.STATUS_ACTIVE)

    @classmethod
    def apply_action_unstop(cls, user, contacts):
        cls.bulk_change_status(user, contacts, Contact.STATUS_ACTIVE)

    @classmethod
    def apply_action_label(cls, user, contacts, group):
        cls.bulk_change_group(user, contacts, group, add=True)

    @classmethod
    def apply_action_unlabel(cls, user, contacts, group):
        cls.bulk_change_group(user, contacts, group, add=False)

    @classmethod
    def apply_action_delete(cls, user, contacts):
        for contact in contacts:
            contact.release(user)

    def block(self, user):
        """
        Blocks this contact removing it from all non-dynamic groups
        """

        Contact.bulk_change_status(user, [self], Contact.STATUS_BLOCKED)
        self.refresh_from_db()

    def stop(self, user):
        """
        Marks this contact has stopped, removing them from all groups.
        """

        Contact.bulk_change_status(user, [self], Contact.STATUS_STOPPED)
        self.refresh_from_db()

    def reactivate(self, user):
        """
        Reactivates a stopped or blocked contact, re-adding them to any dynamic groups they belong to
        """

        Contact.bulk_change_status(user, [self], Contact.STATUS_ACTIVE)
        self.refresh_from_db()

    def release(self, user, *, full=True, immediately=False):
        """
        Marks this contact for deletion
        """
        with transaction.atomic():
            # prep our urns for deletion so our old path creates a new urn
            for urn in self.urns.all():
                path = str(uuid.uuid4())
                urn.identity = f"{DELETED_SCHEME}:{path}"
                urn.path = path
                urn.scheme = DELETED_SCHEME
                urn.channel = None
                urn.save(update_fields=("identity", "path", "scheme", "channel"))

            # no group for you!
            self.clear_all_groups(user)

            # now deactivate the contact itself
            self.is_active = False
            self.name = None
            self.fields = None
            self.save(update_fields=("name", "is_active", "fields", "modified_on"), handle_update=False)

        # if we are removing everything do so
        if full:
            if immediately:
                self._full_release()
            else:
                from temba.contacts.tasks import full_release_contact

                full_release_contact.delay(self.id)

    def _full_release(self):
        with transaction.atomic():

            # release our messages
            for msg in self.msgs.all():
                msg.release()

            # any urns currently owned by us
            for urn in self.urns.all():

                # release any messages attached with each urn,
                # these could include messages that began life
                # on a different contact
                for msg in urn.msgs.all():
                    msg.release()

                # same thing goes for connections
                for conn in urn.connections.all():
                    conn.release()

                urn.release()

            # release our channel events
            for event in self.channel_events.all():  # pragma: needs cover
                event.release()

            # release our runs too
            for run in self.runs.all():
                run.release()

            for session in self.sessions.all():
                session.release()

            for conn in self.connections.all():  # pragma: needs cover
                conn.release()

            # and any event fire history
            self.campaign_fires.all().delete()

            # take us out of broadcast addressed contacts
            for broadcast in self.addressed_broadcasts.all():
                broadcast.contacts.remove(self)

    @classmethod
    def bulk_cache_initialize(cls, org, contacts):
        """
        Performs optimizations on our contacts to prepare them to send. This includes loading all our contact fields for
        variable substitution.
        """
        if not contacts:
            return

        contact_map = dict()
        for contact in contacts:
            contact_map[contact.id] = contact
            # initialize URN list cache
            setattr(contact, "_urns_cache", list())

        # cache all URN values (a priority ordered list on each contact)
        urns = ContactURN.objects.filter(contact__in=contact_map.keys()).order_by("contact", "-priority", "pk")
        for urn in urns:
            contact = contact_map[urn.contact_id]
            getattr(contact, "_urns_cache").append(urn)

        # set the cache initialize as correct
        for contact in contacts:
            contact.org = org
            setattr(contact, "__cache_initialized", True)

    def get_urns(self):
        """
        Gets all URNs ordered by priority
        """
        cache_attr = "_urns_cache"
        if hasattr(self, cache_attr):
            return getattr(self, cache_attr)

        urns = self.urns.order_by("-priority", "pk")
        setattr(self, cache_attr, urns)
        return urns

    def get_urn(self, schemes=None):
        """
        Gets the highest priority matching URN for this contact. Schemes may be a single scheme or a set/list/tuple
        """
        if isinstance(schemes, str):
            schemes = (schemes,)

        urns = self.get_urns()

        if schemes is not None:
            for urn in urns:
                if urn.scheme in schemes:
                    return urn
            return None
        else:
            # otherwise return highest priority of any scheme
            return urns[0] if urns else None

    def reevaluate_dynamic_groups(self, for_fields=None, urns=()):
        """
        Re-evaluates this contacts membership of dynamic groups. If field is specified then re-evaluation is only
        performed for those groups which reference that field.
        :returns: the set of groups that were affected
        """
        from .search import evaluate_query

        # blocked, stopped or test contacts can't be in dynamic groups
        if self.is_blocked or self.is_stopped:
            return set()

        # cache contact search json
        contact_search_json = self.as_search_json()
        user = get_anonymous_user()

        affected_dynamic_groups = ContactGroup.get_user_groups(self.org, dynamic=True, ready_only=False)

        # if we have fields and no urn changes, filter to just the groups that may have changed
        if not urns and for_fields:
            affected_dynamic_groups = affected_dynamic_groups.filter(query_fields__key__in=for_fields)

        changed_set = set()

        for dynamic_group in affected_dynamic_groups:
            dynamic_group.org = self.org

            try:
                should_add = evaluate_query(self.org, dynamic_group.query, contact_json=contact_search_json)
            except Exception as e:  # pragma: no cover
                should_add = False
                logger.error(f"Error evaluating query: {str(e)}", exc_info=True)

            changed_set.update(dynamic_group._update_contacts(user, [self], add=should_add))

        return changed_set

    def clear_all_groups(self, user):
        """
        Removes this contact from all groups - static and dynamic.
        """
        for group in self.user_groups.all():
            group.remove_contacts(user, [self])

    def get_display(self, org=None, formatted=True, short=False, for_expressions=False):
        """
        Gets a displayable name or URN for the contact. If available, org can be provided to avoid having to fetch it
        again based on the contact.
        """
        if not org:
            org = self.org

        if self.name:
            res = self.name
        elif org.is_anon:
            res = self.id if for_expressions else self.anon_identifier
        else:
            res = self.get_urn_display(org=org, formatted=formatted)

        return truncate(res, 20) if short else res

    def get_urn_display(self, org=None, scheme=None, formatted=True, international=False):
        """
        Gets a displayable URN for the contact. If available, org can be provided to avoid having to fetch it again
        based on the contact.
        """
        if not org:
            org = self.org

        urn = self.get_urn(scheme)

        if not urn:
            return ""

        if org.is_anon:
            return ContactURN.ANON_MASK

        return urn.get_display(org=org, formatted=formatted, international=international) if urn else ""

    def __str__(self):
        return self.get_display()


class ContactURN(models.Model):
    """
    A Universal Resource Name used to uniquely identify contacts, e.g. tel:+1234567890 or twitter:example
    """

    # schemes that we actually support
    SCHEMES = [s[0] for s in URN_SCHEME_CONFIG]
    SCHEME_CHOICES = tuple((c[0], c[1]) for c in URN_SCHEME_CONFIG)
    CONTEXT_KEYS_TO_SCHEME = {c[2]: c[0] for c in URN_SCHEME_CONFIG}
    CONTEXT_KEYS_TO_LABEL = {c[2]: c[1] for c in URN_SCHEME_CONFIG}
    IMPORT_HEADER_TO_SCHEME = {s[0].lower(): s[1] for s in IMPORT_HEADERS}

    # schemes that support "new conversation" triggers
    SCHEMES_SUPPORTING_NEW_CONVERSATION = {FACEBOOK_SCHEME, VIBER_SCHEME, TELEGRAM_SCHEME}
    SCHEMES_SUPPORTING_REFERRALS = {FACEBOOK_SCHEME}  # schemes that support "referral" triggers

    EXPORT_SCHEME_HEADERS = tuple((c[0], c[1]) for c in URN_SCHEME_CONFIG)

    PRIORITY_LOWEST = 1
    PRIORITY_STANDARD = 50
    PRIORITY_HIGHEST = 99

    PRIORITY_DEFAULTS = {
        TEL_SCHEME: PRIORITY_STANDARD,
        TWITTER_SCHEME: 90,
        TWITTERID_SCHEME: 90,
        FACEBOOK_SCHEME: 90,
        TELEGRAM_SCHEME: 90,
        VIBER_SCHEME: 90,
        FCM_SCHEME: 90,
        FRESHCHAT_SCHEME: 90,
    }

    ANON_MASK = "*" * 8  # Returned instead of URN values for anon orgs
    ANON_MASK_HTML = "•" * 8  # Pretty HTML version of anon mask

    contact = models.ForeignKey(
        Contact,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="urns",
        help_text="The contact that this URN is for, can be null",
    )

    identity = models.CharField(
        max_length=255,
        help_text="The Universal Resource Name as a string, excluding display if present. ex: tel:+250788383383",
    )

    path = models.CharField(max_length=255, help_text="The path component of our URN. ex: +250788383383")

    display = models.CharField(max_length=255, null=True, help_text="The display component for this URN, if any")

    scheme = models.CharField(
        max_length=128, help_text="The scheme for this URN, broken out for optimization reasons, ex: tel"
    )

    org = models.ForeignKey(
        Org, related_name="urns", on_delete=models.PROTECT, help_text="The organization for this URN, can be null"
    )

    priority = models.IntegerField(
        default=PRIORITY_STANDARD, help_text="The priority of this URN for the contact it is associated with"
    )

    channel = models.ForeignKey(
        Channel,
        related_name="urns",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="The preferred channel for this URN",
    )

    auth = models.TextField(null=True, help_text=_("Any authentication information needed by this URN"))

    @classmethod
    def get_or_create(cls, org, contact, urn_as_string, channel=None, auth=None):
        urn = cls.lookup(org, urn_as_string)

        # not found? create it
        if not urn:
            try:
                with transaction.atomic():
                    urn = cls.create(org, contact, urn_as_string, channel=channel, auth=auth)
            except IntegrityError:
                urn = cls.lookup(org, urn_as_string)

        return urn

    @classmethod
    def create(cls, org, contact, urn_as_string, channel=None, priority=None, auth=None):
        scheme, path, query, display = URN.to_parts(urn_as_string)
        urn_as_string = URN.from_parts(scheme, path)

        if not priority:
            priority = cls.PRIORITY_DEFAULTS.get(scheme, cls.PRIORITY_STANDARD)

        return cls.objects.create(
            org=org,
            contact=contact,
            priority=priority,
            channel=channel,
            auth=auth,
            scheme=scheme,
            path=path,
            identity=urn_as_string,
            display=display,
        )

    @classmethod
    def lookup(cls, org, urn_as_string, country_code=None, normalize=True):
        """
        Looks up an existing URN by a formatted URN string, e.g. "tel:+250234562222"
        """
        if normalize:
            urn_as_string = URN.normalize(urn_as_string, country_code)

        identity = URN.identity(urn_as_string)
        (scheme, path, query, display) = URN.to_parts(urn_as_string)

        existing = cls.objects.filter(org=org, identity=identity).select_related("contact").first()

        # is this a TWITTER scheme? check TWITTERID scheme by looking up by display
        if scheme == TWITTER_SCHEME:
            twitterid_urn = (
                cls.objects.filter(org=org, scheme=TWITTERID_SCHEME, display=path).select_related("contact").first()
            )
            if twitterid_urn:
                return twitterid_urn

        return existing

    def release(self):
        for event in ChannelEvent.objects.filter(contact_urn=self):
            event.release()
        self.delete()

    def update_auth(self, auth):
        if auth and auth != self.auth:
            self.auth = auth
            self.save(update_fields=["auth"])

    def ensure_number_normalization(self, country_code):
        """
        Tries to normalize our phone number from a possible 10 digit (0788 383 383) to a 12 digit number
        with country code (+250788383383) using the country we now know about the channel.
        """
        number = self.path

        if number and not number[0] == "+" and country_code:
            (norm_number, valid) = URN.normalize_number(number, country_code)

            # don't trounce existing contacts with that country code already
            norm_urn = URN.from_tel(norm_number)
            if not ContactURN.objects.filter(identity=norm_urn, org_id=self.org_id).exclude(id=self.id):
                self.identity = norm_urn
                self.path = norm_number
                self.save(update_fields=["identity", "path"])

        return self

    @classmethod
    def derive_country_from_tel(cls, phone, country=None):
        """
        Given a phone number in E164 returns the two letter country code for it.  ex: +250788383383 -> RW
        """
        try:
            parsed = phonenumbers.parse(phone, country)
            return phonenumbers.region_code_for_number(parsed)
        except Exception:
            return None

    def get_display(self, org=None, international=False, formatted=True):
        """
        Gets a representation of the URN for display
        """
        if not org:
            org = self.org

        if org.is_anon:
            return self.ANON_MASK

        return URN.format(self.urn, international=international, formatted=formatted)

    @property
    def urn(self):
        """
        Returns a full representation of this contact URN as a string
        """
        return URN.from_parts(self.scheme, self.path, display=self.display)

    def __str__(self):  # pragma: no cover
        return self.urn

    class Meta:
        unique_together = ("identity", "org")
        ordering = ("-priority", "id")


class SystemContactGroupManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().exclude(group_type=ContactGroup.TYPE_USER_DEFINED)


class UserContactGroupManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(group_type=ContactGroup.TYPE_USER_DEFINED, is_active=True)


class ContactGroup(TembaModel):
    """
    A static or dynamic group of contacts
    """

    MAX_NAME_LEN = 64
    MAX_ORG_CONTACTGROUPS = 250

    TYPE_ALL = "A"
    TYPE_BLOCKED = "B"
    TYPE_STOPPED = "S"
    TYPE_USER_DEFINED = "U"

    TYPE_CHOICES = (
        (TYPE_ALL, "All Contacts"),
        (TYPE_BLOCKED, "Blocked Contacts"),
        (TYPE_STOPPED, "Stopped Contacts"),
        (TYPE_USER_DEFINED, "User Defined Groups"),
    )

    STATUS_INITIALIZING = "I"  # group has been created but not yet (re)evaluated
    STATUS_EVALUATING = "V"  # a task is currently (re)evaluating this group
    STATUS_READY = "R"  # group is ready for use

    # single char flag, human readable name, API readable name
    STATUS_CONFIG = (
        (STATUS_INITIALIZING, _("Initializing"), "initializing"),
        (STATUS_EVALUATING, _("Evaluating"), "evaluating"),
        (STATUS_READY, _("Ready"), "ready"),
    )

    STATUS_CHOICES = [(s[0], s[1]) for s in STATUS_CONFIG]

    REEVALUATE_LOCK_KEY = "contactgroup_reevaluating_%d"

    EXPORT_UUID = "uuid"
    EXPORT_NAME = "name"
    EXPORT_QUERY = "query"

    name = models.CharField(
        verbose_name=_("Name"), max_length=MAX_NAME_LEN, help_text=_("The name of this contact group")
    )

    group_type = models.CharField(
        max_length=1,
        choices=TYPE_CHOICES,
        default=TYPE_USER_DEFINED,
        help_text=_("What type of group it is, either user defined or one of our system groups"),
    )

    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=STATUS_INITIALIZING)

    contacts = models.ManyToManyField(Contact, verbose_name=_("Contacts"), related_name="all_groups")

    org = models.ForeignKey(
        Org,
        on_delete=models.PROTECT,
        related_name="all_groups",
        verbose_name=_("Org"),
        help_text=_("The organization this group is part of"),
    )

    import_task = models.ForeignKey(ImportTask, on_delete=models.PROTECT, null=True, blank=True)

    query = models.TextField(null=True, help_text=_("The membership query for this group"))

    query_fields = models.ManyToManyField(ContactField, verbose_name=_("Query Fields"))

    # define some custom managers to do the filtering of user / system groups for us
    all_groups = models.Manager()
    system_groups = SystemContactGroupManager()
    user_groups = UserContactGroupManager()

    @classmethod
    def create_system_groups(cls, org):
        """
        Creates our system groups for the given organization so that we can keep track of counts etc..
        """
        org.all_groups.create(
            name="All Contacts",
            group_type=ContactGroup.TYPE_ALL,
            created_by=org.created_by,
            modified_by=org.modified_by,
        )
        org.all_groups.create(
            name="Blocked Contacts",
            group_type=ContactGroup.TYPE_BLOCKED,
            created_by=org.created_by,
            modified_by=org.modified_by,
        )
        org.all_groups.create(
            name="Stopped Contacts",
            group_type=ContactGroup.TYPE_STOPPED,
            created_by=org.created_by,
            modified_by=org.modified_by,
        )

    @classmethod
    def get_user_group_by_name(cls, org, name):
        """
        Returns the user group with the passed in name
        """
        return cls.user_groups.filter(name__iexact=cls.clean_name(name), org=org, is_active=True).first()

    @classmethod
    def get_user_groups(cls, org, dynamic=None, ready_only=True):
        """
        Gets all user groups for the given org - optionally filtering by dynamic vs static
        """
        groups = cls.user_groups.filter(org=org, is_active=True)
        if dynamic is not None:
            groups = groups.filter(query=None) if dynamic is False else groups.exclude(query=None)
        if ready_only:
            groups = groups.filter(status=ContactGroup.STATUS_READY)

        return groups

    @classmethod
    def get_or_create(cls, org, user, name, query=None, uuid=None, parsed_query=None):
        existing = None

        if uuid:
            existing = ContactGroup.user_groups.filter(uuid=uuid, org=org, is_active=True).first()

        if not existing and name:
            existing = ContactGroup.get_user_group_by_name(org, name)

        if existing:
            return existing

        assert name, "can't create group without a name"

        if query:
            return cls.create_dynamic(org, user, name, query, parsed_query=parsed_query)
        else:
            return cls.create_static(org, user, name)

    @classmethod
    def create_static(cls, org, user, name, *, status=STATUS_READY, task=None):
        """
        Creates a static group whose members will be manually added and removed
        """
        return cls._create(org, user, name, status=status, task=task)

    @classmethod
    def create_dynamic(cls, org, user, name, query, evaluate=True, parsed_query=None):
        """
        Creates a dynamic group with the given query, e.g. gender=M
        """
        if not query:
            raise ValueError("Query cannot be empty for a dynamic group")

        group = cls._create(org, user, name, ContactGroup.STATUS_INITIALIZING, query=query)
        group.update_query(query=query, reevaluate=evaluate, parsed=parsed_query)
        return group

    @classmethod
    def _create(cls, org, user, name, status, task=None, query=None):
        full_group_name = cls.clean_name(name)

        if not cls.is_valid_name(full_group_name):
            raise ValueError("Invalid group name: %s" % name)

        # look for name collision and append count if necessary
        existing = cls.get_user_group_by_name(org, full_group_name)

        count = 2
        while existing:
            full_group_name = "%s %d" % (name, count)
            existing = cls.get_user_group_by_name(org, full_group_name)
            count += 1

        return cls.user_groups.create(
            org=org,
            name=full_group_name,
            query=query,
            status=status,
            import_task=task,
            created_by=user,
            modified_by=user,
        )

    @classmethod
    def clean_name(cls, name):
        """
        Returns a normalized name for the passed in group name
        """
        return None if name is None else name.strip()[: cls.MAX_NAME_LEN]

    @classmethod
    def is_valid_name(cls, name):
        # don't allow empty strings, blanks, initial or trailing whitespace
        if not name or name.strip() != name:
            return False

        if len(name) > cls.MAX_NAME_LEN:
            return False

        # first character must be a word char
        return regex.match(r"\w", name[0], flags=regex.UNICODE)

    def remove_contacts(self, user, contacts):
        """
        Forces removal of contacts from this group regardless of whether it is static or dynamic
        """
        if self.group_type != self.TYPE_USER_DEFINED:  # pragma: no cover
            raise ValueError("Can't remove contacts from system groups")

        return self._update_contacts(user, contacts, add=False)

    def _update_contacts(self, user, contacts, add):
        """
        Adds or removes contacts from this group - used for both non-dynamic and dynamic groups
        """
        changed = set()
        group_contacts = self.contacts.all()

        for contact in contacts:
            if add and (contact.is_blocked or contact.is_stopped or not contact.is_active):  # pragma: no cover
                raise ValueError("Blocked, stopped and deleted contacts can't be added to groups")

            contact_changed = False

            # if we are adding the contact to the group, and this contact is not in this group
            if add:
                if not group_contacts.filter(id=contact.id):
                    self.contacts.add(contact)
                    contact_changed = True
            else:
                if group_contacts.filter(id=contact.id):
                    self.contacts.remove(contact)
                    contact_changed = True

            if contact_changed:
                changed.add(contact.pk)
                contact.handle_update(group=self)

        if changed:
            # update modified on in small batches to avoid long table lock, and having too many non-unique values for
            # modified_on which is the primary ordering for the API
            for batch in chunk_list(changed, 100):
                Contact.objects.filter(org=self.org, pk__in=batch).update(modified_on=timezone.now())

        return changed

    def update_query(self, query, reevaluate=True, parsed=None):
        """
        Updates the query for a dynamic group
        """
        from temba.contacts.search import parse_query, SearchException

        if not self.is_dynamic:
            raise ValueError("Cannot update query on a non-dynamic group")
        if self.status == ContactGroup.STATUS_EVALUATING:
            raise ValueError("Cannot update query on a group which is currently re-evaluating")

        try:
            if not parsed:
                parsed = parse_query(self.org_id, query)

            if not parsed.metadata.allow_as_group:
                raise ValueError(f"Cannot use query '{query}' as a dynamic group")

            self.query = parsed.query
            self.status = ContactGroup.STATUS_INITIALIZING
            self.save(update_fields=("query", "status"))

            self.query_fields.clear()

            # build our list of the fields we are dependent on
            field_keys = [f["key"] for f in parsed.metadata.fields]
            field_ids = []
            for c in ContactField.all_fields.filter(org=self.org, is_active=True, key__in=field_keys).only("id"):
                field_ids.append(c.id)

            # and add them as dependencies
            self.query_fields.add(*field_ids)

        except SearchException as e:
            raise ValueError(str(e))

        # start background task to re-evaluate who belongs in this group
        if reevaluate:
            on_transaction_commit(lambda: queue_populate_dynamic_group(self))

    @classmethod
    def get_system_group_counts(cls, org, group_types=None):
        """
        Gets all system label counts by type for the given org
        """
        groups = cls.system_groups.filter(org=org)
        if group_types:
            groups = groups.filter(group_type__in=group_types)

        return {g.group_type: g.get_member_count() for g in groups}

    def get_member_count(self):
        """
        Returns the number of active and non-test contacts in the group
        """
        return ContactGroupCount.get_totals([self])[self]

    def release(self):
        """
        Releases (i.e. deletes) this group, removing all contacts and marking as inactive
        """

        # if group is still active, deactivate it
        if self.is_active is True:
            self.is_active = False
            self.save(update_fields=("is_active",))

        # delete all counts for this group
        self.counts.all().delete()

        # get the automatically generated M2M model
        ContactGroupContacts = self.contacts.through

        # grab the ids of all our m2m related rows
        contactgroup_contact_ids = ContactGroupContacts.objects.filter(contactgroup_id=self.id).values_list(
            "id", flat=True
        )

        for id_batch in chunk_list(contactgroup_contact_ids, 1000):
            ContactGroupContacts.objects.filter(id__in=id_batch).delete()

        # delete any event fires related to our group
        from temba.campaigns.models import EventFire

        eventfire_ids = EventFire.objects.filter(event__campaign__group=self, fired=None).values_list("id", flat=True)

        for id_batch in chunk_list(eventfire_ids, 1000):
            EventFire.objects.filter(id__in=id_batch).delete()

        # mark any triggers that operate only on this group as inactive
        from temba.triggers.models import Trigger

        Trigger.objects.filter(is_active=True, groups=self).update(is_active=False, is_archived=True)

        # deactivate any campaigns that are related to this group
        from temba.campaigns.models import Campaign

        Campaign.objects.filter(is_active=True, group=self).update(is_active=False, is_archived=True)

    @property
    def is_dynamic(self):
        return self.query is not None

    @classmethod
    def import_groups(cls, org, user, group_defs, dependency_mapping):
        """
        Import groups from a list of exported groups
        """

        for group_def in group_defs:
            group_uuid = group_def.get(ContactGroup.EXPORT_UUID)
            group_name = group_def.get(ContactGroup.EXPORT_NAME)
            group_query = group_def.get(ContactGroup.EXPORT_QUERY)

            parsed_query = None
            if group_query:
                from .search import parse_query

                parsed_query = parse_query(org.id, group_query)
                for field_ref in parsed_query.metadata.fields:
                    ContactField.get_or_create(org, user, key=field_ref["key"])

            group = ContactGroup.get_or_create(
                org, user, group_name, group_query, uuid=group_uuid, parsed_query=parsed_query
            )

            dependency_mapping[group_uuid] = str(group.uuid)

    def as_export_ref(self):
        return {ContactGroup.EXPORT_UUID: str(self.uuid), ContactGroup.EXPORT_NAME: self.name}

    def as_export_def(self):
        return {
            ContactGroup.EXPORT_UUID: str(self.uuid),
            ContactGroup.EXPORT_NAME: self.name,
            ContactGroup.EXPORT_QUERY: self.query,
        }

    def __str__(self):
        return self.name


class ContactGroupCount(SquashableModel):
    """
    Maintains counts of contact groups. These are calculated via triggers on the database and squashed
    by a recurring task.
    """

    SQUASH_OVER = ("group_id",)

    group = models.ForeignKey(ContactGroup, on_delete=models.PROTECT, related_name="counts", db_index=True)
    count = models.IntegerField(default=0)

    @classmethod
    def get_squash_query(cls, distinct_set):
        sql = """
        WITH deleted as (
            DELETE FROM %(table)s WHERE "group_id" = %%s RETURNING "count"
        )
        INSERT INTO %(table)s("group_id", "count", "is_squashed")
        VALUES (%%s, GREATEST(0, (SELECT SUM("count") FROM deleted)), TRUE);
        """ % {
            "table": cls._meta.db_table
        }

        return sql, (distinct_set.group_id,) * 2

    @classmethod
    def get_totals(cls, groups):
        """
        Gets total counts for all the given groups
        """
        counts = cls.objects.filter(group__in=groups)
        counts = counts.values("group").order_by("group").annotate(count_sum=Sum("count"))
        counts_by_group_id = {c["group"]: c["count_sum"] for c in counts}
        return {g: counts_by_group_id.get(g.id, 0) for g in groups}

    @classmethod
    def populate_for_group(cls, group):
        # remove old ones
        ContactGroupCount.objects.filter(group=group).delete()

        # calculate our count for the group
        count = group.contacts.all().count()

        # insert updated count, returning it
        return ContactGroupCount.objects.create(group=group, count=count)

    def __str__(self):  # pragma: needs cover
        return "ContactGroupCount[%d:%d]" % (self.group_id, self.count)


class ExportContactsTask(BaseExportTask):
    analytics_key = "contact_export"
    email_subject = "Your contacts export from %s is ready"
    email_template = "contacts/email/contacts_export_download"

    group = models.ForeignKey(
        ContactGroup,
        on_delete=models.PROTECT,
        null=True,
        related_name="exports",
        help_text=_("The unique group to export"),
    )

    group_memberships = models.ManyToManyField(ContactGroup)

    search = models.TextField(null=True, blank=True, help_text=_("The search query"))

    @classmethod
    def create(cls, org, user, group=None, search=None, group_memberships=()):
        export = cls.objects.create(org=org, group=group, search=search, created_by=user, modified_by=user)
        export.group_memberships.add(*group_memberships)
        return export

    def get_export_fields_and_schemes(self):
        fields = [
            dict(label="Contact UUID", key=Contact.UUID, id=0, field=None, urn_scheme=None),
            dict(label="Name", key=ContactField.KEY_NAME, id=0, field=None, urn_scheme=None),
            dict(label="Language", key=ContactField.KEY_LANGUAGE, id=0, field=None, urn_scheme=None),
            dict(label="Created On", key=ContactField.KEY_CREATED_ON, id=0, field=None, urn_scheme=None),
        ]

        # anon orgs also get an ID column that is just the PK
        if self.org.is_anon:
            fields = [dict(label="ID", key=ContactField.KEY_ID, id=0, field=None, urn_scheme=None)] + fields

        scheme_counts = dict()
        if not self.org.is_anon:
            active_urn_schemes = [c[0] for c in ContactURN.SCHEME_CHOICES]

            scheme_counts = {
                scheme: ContactURN.objects.filter(org=self.org, scheme=scheme)
                .exclude(contact=None)
                .values("contact")
                .annotate(count=Count("contact"))
                .aggregate(Max("count"))["count__max"]
                for scheme in active_urn_schemes
            }

            schemes = list(scheme_counts.keys())
            schemes.sort()

            for scheme in schemes:
                count = scheme_counts[scheme]
                if count is not None:
                    for i in range(count):
                        field_dict = dict(
                            label=f"URN:{scheme.capitalize()}", key=None, id=0, field=None, urn_scheme=scheme
                        )
                        field_dict["position"] = i
                        fields.append(field_dict)

        contact_fields_list = (
            ContactField.user_fields.active_for_org(org=self.org).select_related("org").order_by("-priority", "pk")
        )
        for contact_field in contact_fields_list:
            fields.append(
                dict(
                    field=contact_field,
                    label="Field:%s" % contact_field.label,
                    key=contact_field.key,
                    id=contact_field.id,
                    urn_scheme=None,
                )
            )

        group_fields = []
        for group in self.group_memberships.all():
            group_fields.append(dict(label="Group:%s" % group.name, key=None, group_id=group.id, group=group))

        return fields, scheme_counts, group_fields

    def write_export(self):
        fields, scheme_counts, group_fields = self.get_export_fields_and_schemes()

        group = self.group or ContactGroup.all_groups.get(org=self.org, group_type=ContactGroup.TYPE_ALL)

        include_group_memberships = bool(self.group_memberships.exists())

        if self.search:
            contact_ids = Contact.query_elasticsearch_for_ids(self.org, self.search, group)
        else:
            contact_ids = group.contacts.order_by("name", "id").values_list("id", flat=True)

        # create our exporter
        exporter = TableExporter(self, "Contact", [f["label"] for f in fields] + [g["label"] for g in group_fields])

        total_exported_contacts = 0
        start = time.time()

        # write out contacts in batches to limit memory usage
        for batch_ids in chunk_list(contact_ids, 1000):
            # fetch all the contacts for our batch
            batch_contacts = (
                Contact.objects.filter(id__in=batch_ids).prefetch_related("all_groups").select_related("org")
            )

            # to maintain our sort, we need to lookup by id, create a map of our id->contact to aid in that
            contact_by_id = {c.id: c for c in batch_contacts}

            # bulk initialize them
            Contact.bulk_cache_initialize(self.org, batch_contacts)

            for contact_id in batch_ids:
                contact = contact_by_id[contact_id]

                values = []
                group_values = []
                for col in range(len(fields)):
                    field = fields[col]

                    if field["key"] == ContactField.KEY_NAME:
                        field_value = contact.name
                    elif field["key"] == Contact.UUID:
                        field_value = contact.uuid
                    elif field["key"] == ContactField.KEY_LANGUAGE:
                        field_value = contact.language
                    elif field["key"] == ContactField.KEY_CREATED_ON:
                        field_value = contact.created_on
                    elif field["key"] == ContactField.KEY_ID:
                        field_value = str(contact.id)
                    elif field["urn_scheme"] is not None:
                        contact_urns = contact.get_urns()
                        scheme_urns = []
                        for urn in contact_urns:
                            if urn.scheme == field["urn_scheme"]:
                                scheme_urns.append(urn)
                        position = field["position"]
                        if len(scheme_urns) > position:
                            urn_obj = scheme_urns[position]
                            field_value = urn_obj.get_display(org=self.org, formatted=False) if urn_obj else ""
                        else:
                            field_value = ""
                    else:
                        field_value = contact.get_field_display(field["field"])

                    if field_value is None:
                        field_value = ""

                    if field_value:
                        field_value = self.prepare_value(field_value)

                    values.append(field_value)

                if include_group_memberships:
                    contact_groups_ids = [g.id for g in contact.all_groups.all()]
                    for col in range(len(group_fields)):
                        field = group_fields[col]
                        group_values.append(field["group_id"] in contact_groups_ids)

                # write this contact's values
                exporter.write_row(values + group_values)
                total_exported_contacts += 1

                # output some status information every 10,000 contacts
                if total_exported_contacts % ExportContactsTask.LOG_PROGRESS_PER_ROWS == 0:
                    elapsed = time.time() - start
                    predicted = elapsed // (total_exported_contacts / len(contact_ids))

                    logger.info(
                        "Export of %s contacts - %d%% (%s/%s) complete in %0.2fs (predicted %0.0fs)"
                        % (
                            self.org.name,
                            total_exported_contacts * 100 // len(contact_ids),
                            "{:,}".format(total_exported_contacts),
                            "{:,}".format(len(contact_ids)),
                            time.time() - start,
                            predicted,
                        )
                    )

                    self.modified_on = timezone.now()
                    self.save(update_fields=["modified_on"])

        return exporter.save_file()


@register_asset_store
class ContactExportAssetStore(BaseExportAssetStore):
    model = ExportContactsTask
    key = "contact_export"
    directory = "contact_exports"
    permission = "contacts.contact_export"
    extensions = ("xlsx", "csv")
