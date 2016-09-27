from __future__ import unicode_literals

import datetime
import json
import logging
import os
import phonenumbers
import regex
import time

from collections import defaultdict
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.validators import validate_email
from django.db import models, connection
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone
from django.utils.translation import ugettext, ugettext_lazy as _
from guardian.utils import get_anonymous_user
from itertools import chain
from redis_cache import get_redis_connection
from smartmin.models import SmartModel, SmartImportRowError
from smartmin.csv_imports.models import ImportTask
from temba.channels.models import Channel
from temba.orgs.models import Org, OrgLock
from temba.utils.email import send_template_email
from temba.utils import analytics, format_decimal, truncate, datetime_to_str, chunk_list
from temba.utils.models import TembaModel
from temba.utils.exporter import TableExporter
from temba.utils.profiler import SegmentProfiler
from temba.values.models import Value
from temba.locations.models import STATE_LEVEL, DISTRICT_LEVEL, WARD_LEVEL
from uuid import uuid4


logger = logging.getLogger(__name__)

# phone number for every org's test contact
OLD_TEST_CONTACT_TEL = '12065551212'
START_TEST_CONTACT_PATH = 12065550100
END_TEST_CONTACT_PATH = 12065550199

# how many sequential contacts on import triggers suspension
SEQUENTIAL_CONTACTS_THRESHOLD = 250

TEL_SCHEME = 'tel'
TWITTER_SCHEME = 'twitter'
TWILIO_SCHEME = 'twilio'
FACEBOOK_SCHEME = 'facebook'
TELEGRAM_SCHEME = 'telegram'
EMAIL_SCHEME = 'mailto'
EXTERNAL_SCHEME = 'ext'

# Scheme, Label, Export/Import Header, Context Key
URN_SCHEME_CONFIG = ((TEL_SCHEME, _("Phone number"), 'phone', 'tel_e164'),
                     (TWITTER_SCHEME, _("Twitter handle"), 'twitter', TWITTER_SCHEME),
                     (TELEGRAM_SCHEME, _("Telegram identifier"), 'telegram', TELEGRAM_SCHEME),
                     (EMAIL_SCHEME, _("Email address"), 'email', EMAIL_SCHEME),
                     (FACEBOOK_SCHEME, _("Facebook identifier"), 'facebook', FACEBOOK_SCHEME),
                     (EXTERNAL_SCHEME, _("External identifier"), 'external', EXTERNAL_SCHEME))

IMPORT_HEADERS = tuple((c[2], c[0]) for c in URN_SCHEME_CONFIG)


class URN(object):
    """
    Support class for URN strings. We differ from the strict definition of a URN (https://tools.ietf.org/html/rfc2141)
    in that:
        * We only supports URNs with scheme and path parts (no netloc, query, params or fragment)
        * Path component can be any non-blank unicode string
        * No hex escaping in URN path
    """
    VALID_SCHEMES = {s[0] for s in URN_SCHEME_CONFIG}

    def __init__(self):  # pragma: no cover
        raise ValueError("Class shouldn't be instantiated")

    @classmethod
    def from_parts(cls, scheme, path):
        """
        Formats a URN scheme and path as single URN string, e.g. tel:+250783835665
        """
        if not scheme or scheme not in cls.VALID_SCHEMES:
            raise ValueError("Invalid scheme component: '%s'" % scheme)

        if not path:
            raise ValueError("Invalid path component: '%s'" % path)

        return '%s:%s' % (scheme, path)

    @classmethod
    def to_parts(cls, urn):
        """
        Parses a URN string (e.g. tel:+250783835665) into a tuple of scheme and path
        """
        try:
            scheme, path = urn.split(':', 1)
        except:
            raise ValueError("URN strings must contain scheme and path components")

        if not scheme or scheme not in cls.VALID_SCHEMES:
            raise ValueError("URN contains an invalid scheme component: '%s'" % scheme)

        if not path:
            raise ValueError("URN contains an invalid path component: '%s'" % path)

        return scheme, path

    @classmethod
    def validate(cls, urn, country_code=None):
        """
        Validates a normalized URN
        """
        try:
            scheme, path = cls.to_parts(urn)
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
            return regex.match(r'^[a-zA-Z0-9_]{1,15}$', path, regex.V0)

        elif scheme == EMAIL_SCHEME:
            try:
                validate_email(path)
                return True
            except ValidationError:
                return False

        # telegram and facebook uses integer ids
        elif scheme in (TELEGRAM_SCHEME, FACEBOOK_SCHEME):
            try:
                int(path)
                return True
            except ValueError:
                return False

        # anything goes for external schemes
        return True

    @classmethod
    def normalize(cls, urn, country_code=None):
        """
        Normalizes the path of a URN string. Should be called anytime looking for a URN match.
        """
        scheme, path = cls.to_parts(urn)

        norm_path = unicode(path).strip()

        if scheme == TEL_SCHEME:
            norm_path, valid = cls.normalize_number(norm_path, country_code)
        elif scheme == TWITTER_SCHEME:
            norm_path = norm_path.lower()
            if norm_path[0:1] == '@':  # strip @ prefix if provided
                norm_path = norm_path[1:]
            norm_path = norm_path.lower()  # Twitter handles are case-insensitive, so we always store as lowercase
        elif scheme == EMAIL_SCHEME:
            norm_path = norm_path.lower()

        return cls.from_parts(scheme, norm_path)

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
            number = number[0:-4].replace('.', '')

        # remove other characters
        number = regex.sub('[^0-9a-z\+]', '', number.lower(), regex.V0)

        # add on a plus if it looks like it could be a fully qualified number
        if len(number) >= 11 and number[0] not in ['+', '0']:
            number = '+' + number

        normalized = None
        try:
            normalized = phonenumbers.parse(number, str(country_code) if country_code else None)
        except Exception:
            pass

        # now does it look plausible?
        try:
            if phonenumbers.is_possible_number(normalized):
                return phonenumbers.format_number(normalized, phonenumbers.PhoneNumberFormat.E164), True
        except Exception:
            pass

        # this must be a local number of some kind, just lowercase and save
        return regex.sub('[^0-9a-z]', '', number.lower(), regex.V0), False

    # ==================== shortcut constructors ===========================

    @classmethod
    def from_tel(cls, path):
        return cls.from_parts(TEL_SCHEME, path)

    @classmethod
    def from_twitter(cls, path):
        return cls.from_parts(TWITTER_SCHEME, path)

    @classmethod
    def from_email(cls, path):
        return cls.from_parts(EMAIL_SCHEME, path)

    @classmethod
    def from_facebook(cls, path):
        return cls.from_parts(FACEBOOK_SCHEME, path)

    @classmethod
    def from_telegram(cls, path):
        return cls.from_parts(TELEGRAM_SCHEME, path)

    @classmethod
    def from_external(cls, path):
        return cls.from_parts(EXTERNAL_SCHEME, path)


class ContactField(SmartModel):
    """
    Represents a type of field that can be put on Contacts.
    """
    org = models.ForeignKey(Org, verbose_name=_("Org"), related_name="contactfields")

    label = models.CharField(verbose_name=_("Label"), max_length=36)

    key = models.CharField(verbose_name=_("Key"), max_length=36)

    value_type = models.CharField(choices=Value.TYPE_CHOICES, max_length=1, default=Value.TYPE_TEXT,
                                  verbose_name="Field Type")
    show_in_table = models.BooleanField(verbose_name=_("Shown in Tables"), default=False)

    @classmethod
    def make_key(cls, label):
        """
        Generates a key from a label. There is no guarantee that the key is valid so should be checked with is_valid_key
        """
        key = regex.sub(r'([^a-z0-9]+)', ' ', label.lower(), regex.V0)
        return regex.sub(r'([^a-z0-9]+)', '_', key.strip(), regex.V0)

    @classmethod
    def is_valid_key(cls, key):
        return regex.match(r'^[a-z][a-z0-9_]*$', key, regex.V0) and key not in Contact.RESERVED_FIELDS

    @classmethod
    def is_valid_label(cls, label):
        label = label.strip()
        return regex.match(r'^[A-Za-z0-9\- ]+$', label, regex.V0)

    @classmethod
    def hide_field(cls, org, user, key):
        existing = ContactField.objects.filter(org=org, key=key).first()
        if existing:
            existing.is_active = False
            existing.show_in_table = False
            existing.modified_by = user
            existing.modified_on = timezone.now()
            existing.save()

            # cancel any events on this
            from temba.campaigns.models import EventFire
            EventFire.update_field_events(existing)

    @classmethod
    def get_or_create(cls, org, user, key, label=None, show_in_table=None, value_type=None):
        """
        Gets the existing contact field or creates a new field if it doesn't exist
        """
        if label:
            label = label.strip()

        with org.lock_on(OrgLock.field, key):
            field = ContactField.objects.filter(org=org, key__iexact=key).first()

            if not field:
                # try to lookup the existing field by label
                field = ContactField.get_by_label(org, label)

            # we have a field with a invalid key we should ignore it
            if field and not ContactField.is_valid_key(field.key):
                field = None

            if field:
                update_events = False
                changed = False

                # make this as active
                if not field.is_active:
                    field.is_active = True
                    update_events = True
                    changed = True

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
                    field.value_type = value_type
                    changed = True

                if changed:
                    field.modified_by = user
                    field.modified_on = timezone.now()
                    field.save()

                    if update_events:
                        from temba.campaigns.models import EventFire
                        EventFire.update_field_events(field)

            else:
                # we need to create a new contact field, use our key with invalid chars removed
                if not label:
                    label = regex.sub(r'([^A-Za-z0-9\- ]+)', ' ', key, regex.V0).title()

                if not value_type:
                    value_type = Value.TYPE_TEXT

                if show_in_table is None:
                    show_in_table = False

                if not ContactField.is_valid_key(key):
                    raise ValueError('Field key %s has invalid characters or is a reserved field name' % key)

                field = ContactField.objects.create(org=org, key=key, label=label,
                                                    show_in_table=show_in_table, value_type=value_type,
                                                    created_by=user, modified_by=user)

            return field

    @classmethod
    def get_by_label(cls, org, label):
        return cls.objects.filter(org=org, is_active=True, label__iexact=label).first()

    @classmethod
    def get_location_field(cls, org, type):
        return cls.objects.filter(is_active=True, org=org, value_type=type).first()

    def __unicode__(self):
        return "%s" % self.label


NEW_CONTACT_VARIABLE = "@new_contact"


class Contact(TembaModel):
    name = models.CharField(verbose_name=_("Name"), max_length=128, blank=True, null=True,
                            help_text=_("The name of this contact"))

    org = models.ForeignKey(Org, verbose_name=_("Org"), related_name="org_contacts",
                            help_text=_("The organization that this contact belongs to"))

    is_blocked = models.BooleanField(verbose_name=_("Is Blocked"), default=False,
                                     help_text=_("Whether this contact has been blocked"))

    is_test = models.BooleanField(verbose_name=_("Is Test"), default=False,
                                  help_text=_("Whether this contact is for simulation"))

    is_stopped = models.BooleanField(verbose_name=_("Is Stopped"), default=False,
                                     help_text=_("Whether this contact has opted out of receiving messages"))

    language = models.CharField(max_length=3, verbose_name=_("Language"), null=True, blank=True,
                                help_text=_("The preferred language for this contact"))

    simulation = False

    NAME = 'name'
    FIRST_NAME = 'first_name'
    LANGUAGE = 'language'
    PHONE = 'phone'
    UUID = 'uuid'
    GROUPS = 'groups'

    # reserved contact fields
    RESERVED_FIELDS = [
        NAME, FIRST_NAME, PHONE, LANGUAGE, GROUPS, UUID, 'created_by', 'modified_by', 'org', 'is', 'has'
    ] + [c[0] for c in IMPORT_HEADERS]

    @classmethod
    def get_contacts(cls, org, blocked=False, stopped=False):
        return Contact.objects.filter(org=org, is_active=True, is_test=False, is_blocked=blocked, is_stopped=stopped)

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

    def as_json(self):
        obj = dict(id=self.pk, name=unicode(self), uuid=self.uuid)

        if not self.org.is_anon:
            urns = []
            for urn in self.urns.all():
                urns.append(dict(scheme=urn.scheme, path=urn.path, priority=urn.priority))
            obj['urns'] = urns

        return obj

    def groups_as_text(self):
        groups = self.user_groups.all().order_by('name')
        groups_name_list = [group.name for group in groups]
        return ", ".join(groups_name_list)

    @classmethod
    def set_simulation(cls, simulation):
        cls.simulation = simulation

    @classmethod
    def get_simulation(cls):
        return cls.simulation

    @classmethod
    def all(cls):
        simulation = cls.get_simulation()
        return cls.objects.filter(is_test=simulation)

    def get_scheduled_messages(self):
        from temba.msgs.models import SystemLabel

        contact_urns = self.get_urns()
        contact_groups = self.user_groups.all()
        now = timezone.now()

        scheduled_broadcasts = SystemLabel.get_queryset(self.org, SystemLabel.TYPE_SCHEDULED, exclude_test_contacts=False)
        scheduled_broadcasts = scheduled_broadcasts.exclude(schedule__next_fire=None)
        scheduled_broadcasts = scheduled_broadcasts.filter(schedule__next_fire__gte=now)
        scheduled_broadcasts = scheduled_broadcasts.filter(
            Q(contacts__in=[self]) | Q(urns__in=contact_urns) | Q(groups__in=contact_groups))

        return scheduled_broadcasts.order_by('schedule__next_fire')

    def get_activity(self, after, before):
        """
        Gets this contact's activity of messages, calls, runs etc in the given time window
        """
        from temba.flows.models import Flow
        from temba.ivr.models import BUSY, FAILED, NO_ANSWER, CANCELED
        from temba.msgs.models import Msg

        msgs = Msg.objects.filter(contact=self, created_on__gte=after, created_on__lt=before)
        msgs = msgs.exclude(visibility=Msg.VISIBILITY_DELETED).select_related('channel').prefetch_related('channel_logs')

        # we also include in the timeline purged broadcasts with a best guess at the translation used
        broadcasts = self.broadcasts.filter(purged=True).filter(created_on__gte=after, created_on__lt=before)
        broadcasts = broadcasts.prefetch_related('steps__run__flow')
        for broadcast in broadcasts:
            steps = list(broadcast.steps.all())
            flow = steps[0].run.flow if steps else None
            flow_language = flow.base_language if flow else None
            broadcast.translated_text = broadcast.get_translated_text(contact=self,
                                                                      base_language=flow_language,
                                                                      org=self.org)

        # and all of this contact's runs, channel events such as missed calls, scheduled events
        runs = self.runs.filter(created_on__gte=after, created_on__lt=before).exclude(flow__flow_type=Flow.MESSAGE)
        runs = runs.select_related('flow')

        channel_events = self.channel_events.filter(created_on__gte=after, created_on__lt=before)
        channel_events = channel_events.select_related('channel')

        event_fires = self.fire_events.filter(fired__gte=after, fired__lt=before).exclude(fired=None)
        event_fires = event_fires.select_related('event__campaign')

        # for easier comparison and display - give event fires same time attribute as other activity items
        for event_fire in event_fires:
            event_fire.created_on = event_fire.fired

        # and the contact's failed IVR calls
        error_calls = self.calls.filter(created_on__gte=after, created_on__lt=before, status__in=[BUSY, FAILED, NO_ANSWER, CANCELED])
        error_calls = error_calls.select_related('channel')

        # chain them all together in the same list and sort by time
        activity = chain(msgs, broadcasts, runs, event_fires, channel_events, error_calls)
        return sorted(activity, key=lambda i: i.created_on, reverse=True)

    def get_field(self, key):
        """
        Gets the (possibly cached) value of a contact field
        """
        key = key.lower()
        cache_attr = '__field__%s' % key
        if hasattr(self, cache_attr):
            return getattr(self, cache_attr)

        value = Value.objects.filter(contact=self, contact_field__key__exact=key).first()
        self.set_cached_field_value(key, value)
        return value

    def get_field_raw(self, key):
        """
        Gets the string value (i.e. raw user input) of a contact field
        """
        value = self.get_field(key)
        return value.string_value if value else None

    def get_field_display(self, key):
        """
        Gets either the field category if set, or the formatted field value
        """
        value = self.get_field(key)
        if value:
            field = value.contact_field
            return Contact.get_field_display_for_value(field, value)
        else:
            return None

    @classmethod
    def get_field_display_for_value(cls, field, value):
        """
        Utility method to determine best display value for the passed in field, value pair.
        """
        if value is None:
            return None

        if field.value_type == Value.TYPE_DATETIME:
            return field.org.format_date(value.datetime_value)
        elif field.value_type == Value.TYPE_DECIMAL:
            return format_decimal(value.decimal_value)
        elif field.value_type in [Value.TYPE_STATE, Value.TYPE_DISTRICT, Value.TYPE_WARD] and value.location_value:
            return value.location_value.name
        elif value.category:
            return value.category
        else:
            return value.string_value

    @classmethod
    def serialize_field_value(cls, field, value):
        """
        Utility method to give the serialized value for the passed in field, value pair.
        """
        if value is None:
            return None

        if field.value_type == Value.TYPE_DATETIME:
            return datetime_to_str(value.datetime_value)
        elif field.value_type == Value.TYPE_DECIMAL:
            return format_decimal(value.decimal_value)
        elif field.value_type in [Value.TYPE_STATE, Value.TYPE_DISTRICT, Value.TYPE_WARD] and value.location_value:
            return value.location_value.name
        elif value.category:
            return value.category
        else:
            return value.string_value

    def set_field(self, user, key, value, label=None):
        from temba.values.models import Value

        # make sure this field exists
        field = ContactField.get_or_create(self.org, user, key, label)

        existing = None
        if value is None or value == '':
            Value.objects.filter(contact=self, contact_field__pk=field.id).delete()
        else:
            # parse as all value data types
            str_value = unicode(value)
            dt_value = self.org.parse_date(value)
            dec_value = self.org.parse_decimal(value)
            loc_value = None

            if field.value_type == Value.TYPE_WARD:
                district_field = ContactField.get_location_field(self.org, Value.TYPE_DISTRICT)
                district_value = self.get_field(district_field.key)
                if district_value:
                    loc_value = self.org.parse_location(value, WARD_LEVEL, district_value.location_value)

            elif field.value_type == Value.TYPE_DISTRICT:
                state_field = ContactField.get_location_field(self.org, Value.TYPE_STATE)
                if state_field:
                    state_value = self.get_field(state_field.key)
                    if state_value:
                        loc_value = self.org.parse_location(value, DISTRICT_LEVEL, state_value.location_value)
            else:
                loc_value = self.org.parse_location(value, STATE_LEVEL)

            if loc_value is not None and len(loc_value) > 0:
                loc_value = loc_value[0]
            else:
                loc_value = None

            # find the existing value
            existing = Value.objects.filter(contact=self, contact_field__pk=field.id).first()

            # update it if it exists
            if existing:
                existing.string_value = str_value
                existing.decimal_value = dec_value
                existing.datetime_value = dt_value
                existing.location_value = loc_value

                if loc_value:
                    existing.category = loc_value.name
                else:
                    existing.category = None

                existing.save(update_fields=['string_value', 'decimal_value', 'datetime_value',
                                             'location_value', 'category', 'modified_on'])

                # remove any others on the same field that may exist
                Value.objects.filter(contact=self, contact_field__pk=field.id).exclude(id=existing.id).delete()

            # otherwise, create a new value for it
            else:
                category = loc_value.name if loc_value else None
                existing = Value.objects.create(contact=self, contact_field=field, org=self.org,
                                                string_value=str_value, decimal_value=dec_value, datetime_value=dt_value,
                                                location_value=loc_value, category=category)

        # cache
        self.set_cached_field_value(key, existing)

        self.modified_by = user
        self.modified_on = timezone.now()
        self.save(update_fields=('modified_by', 'modified_on'))

        # update any groups or campaigns for this contact
        self.handle_update(field=field)

        # invalidate our value cache for this contact field
        Value.invalidate_cache(contact_field=field)

    def set_cached_field_value(self, key, value):
        setattr(self, '__field__%s' % key, value)

    def handle_update(self, attrs=(), urns=(), field=None, group=None):
        """
        Handles an update to a contact which can be one of
          1. A change to one or more attributes
          2. A change to the specified contact field
          3. A manual change to a group membership
        """
        dynamic_group_change = False

        if Contact.NAME in attrs or field or urns:
            # ensure dynamic groups are up to date
            dynamic_group_change = self.reevaluate_dynamic_groups(field)

        # ensure our campaigns are up to date
        from temba.campaigns.models import EventFire
        if field:
            EventFire.update_events_for_contact_field(self, field.key)

        if group or dynamic_group_change:
            # ensure our campaigns are up to date
            EventFire.update_events_for_contact(self)

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
    def get_or_create(cls, org, user, name=None, urns=None, channel=None, uuid=None, language=None, is_test=False, force_urn_update=False):
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

                # return our contact, mapping our existing urn appropriately
                contact.urn_objects = {urns[0]: existing_urn}
                return contact

        # if we were passed in a UUID, look it up by that
        if uuid:
            contact = Contact.objects.filter(org=org, is_active=True, uuid=uuid).first()

            # if contact already exists try to figured if it has all the urn to skip the lock
            if contact:
                contact_has_all_urns = True
                contact_urns = set(contact.get_urns().values_list('urn', flat=True))
                if len(urns) <= len(contact_urns):
                    for urn in urns:
                        normalized = URN.normalize(urn, country)
                        if normalized not in contact_urns:
                            contact_has_all_urns = False

                    if contact_has_all_urns:
                        # update contact name if provided
                        updated_attrs = []
                        if name:
                            contact.name = name
                            updated_attrs.append(Contact.NAME)
                        if language:
                            contact.language = language
                            updated_attrs.append(Contact.LANGUAGE)

                        if updated_attrs:
                            contact.modified_on = timezone.now()
                            contact.modified_by = user
                            contact.save(update_fields=updated_attrs + ['modified_on', 'modified_by'])

                        contact.urn_objects = contact_urns

                        # handle group and campaign updates
                        contact.handle_update(attrs=updated_attrs)
                        return contact

        # perform everything in a org-level lock to prevent duplication by different instances
        with org.lock_on(OrgLock.contacts):

            # figure out which URNs already exist and who they belong to
            existing_owned_urns = dict()
            existing_orphan_urns = dict()
            urns_to_create = dict()
            for urn in urns:
                normalized = URN.normalize(urn, country)
                existing_urn = ContactURN.lookup(org, normalized, normalize=False)

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
                else:
                    urns_to_create[urn] = normalized

            # URNs correspond to one contact so update and return that
            if contact:
                # update contact name if provided
                updated_attrs = []
                if name:
                    contact.name = name
                    updated_attrs.append(Contact.NAME)
                if language:
                    contact.language = language
                    updated_attrs.append(Contact.LANGUAGE)

                if updated_attrs:
                    contact.modified_by = user
                    contact.modified_on = timezone.now()
                    contact.save(update_fields=updated_attrs + ['modified_by', 'modified_on'])

            # otherwise create new contact with all URNs
            else:
                kwargs = dict(org=org, name=name, language=language, is_test=is_test,
                              created_by=user, modified_by=user)
                contact = Contact.objects.create(**kwargs)
                updated_attrs = kwargs.keys()

                # add attribute which allows import process to track new vs existing
                contact.is_new = True

            # attach all orphaned URNs
            ContactURN.objects.filter(pk__in=[urn.id for urn in existing_orphan_urns.values()]).update(contact=contact)

            # create dict of all requested URNs and actual URN objects
            urn_objects = existing_orphan_urns.copy()

            # add all new URNs
            for raw, normalized in urns_to_create.iteritems():
                urn = ContactURN.get_or_create(org, contact, normalized, channel=channel)
                urn_objects[raw] = urn

            # save which urns were updated
            updated_urns = urn_objects.keys()

            # add remaining already owned URNs and attach to contact object so that calling code can easily fetch the
            # actual URN object for each URN tuple it requested
            urn_objects.update(existing_owned_urns)
            contact.urn_objects = urn_objects

        # record contact creation in analytics
        if getattr(contact, 'is_new', False):
            params = dict(name=name)

            # properties passed to track must be flat so since we may have multiple URNs for the same scheme, we
            # assign them property names with added count
            urns_for_scheme_counts = defaultdict(int)
            for urn in urn_objects.keys():
                scheme, path = URN.to_parts(urn)
                urns_for_scheme_counts[scheme] += 1
                params["%s%d" % (scheme, urns_for_scheme_counts[scheme])] = path

            analytics.gauge('temba.contact_created')

        # handle group and campaign updates
        contact.handle_update(attrs=updated_attrs, urns=updated_urns)
        return contact

    @classmethod
    def get_test_contact(cls, user):
        """
        Gets or creates the test contact for the given user
        """
        org = user.get_org()
        test_contacts = Contact.objects.filter(is_test=True, org=org, created_by=user, is_active=True)
        test_contact = test_contacts.order_by('-created_on').first()

        # double check that our test contact has a valid URN, it may have been reassigned
        if test_contact:
            test_urn = test_contact.get_urn(TEL_SCHEME)

            # no URN, let's start over
            if not test_urn:
                test_contact.release(user)
                test_contact = None

        if not test_contact:
            # creates a full URN string from a phone number stored as an integer
            def make_urn(tel_as_int):
                return URN.from_tel('+%s' % tel_as_int)

            # generate sequential test contact URNs until we find an available one
            test_urn_path = START_TEST_CONTACT_PATH
            existing_urn = ContactURN.lookup(org, make_urn(test_urn_path), normalize=False)
            while existing_urn and test_urn_path < END_TEST_CONTACT_PATH:
                test_urn_path += 1
                existing_urn = ContactURN.lookup(org, make_urn(test_urn_path), normalize=False)

            test_contact = Contact.get_or_create(org, user, "Test Contact", [make_urn(test_urn_path)], is_test=True)
        return test_contact

    @classmethod
    def search(cls, org, query, base_queryset=None):
        """
        Performs a search of contacts based on a query. Returns a tuple of the queryset and a bool for whether
        or not the query was a valid complex query, e.g. name = "Bob" AND age = 21
        """
        from temba.contacts import search

        if not base_queryset:
            base_queryset = Contact.objects.filter(org=org, is_active=True, is_test=False, is_blocked=False, is_stopped=False)

        return search.contact_search(org, query, base_queryset)

    @classmethod
    def create_instance(cls, field_dict):
        """
        Creates or updates a contact from the given field values during an import
        """
        if 'org' not in field_dict or 'created_by' not in field_dict:
            raise ValueError("Import fields dictionary must include org and created_by")

        org = field_dict.pop('org')
        user = field_dict.pop('created_by')
        is_admin = org.administrators.filter(id=user.id).exists()
        uuid = field_dict.pop('uuid', None)

        country = org.get_country_code()
        urns = []

        possible_urn_headers = [scheme[0] for scheme in IMPORT_HEADERS]

        # prevent urns update on anon org
        if uuid and org.is_anon and not is_admin:
            possible_urn_headers = []

        for urn_header in possible_urn_headers:
            value = None
            if urn_header in field_dict:
                value = field_dict[urn_header]
                del field_dict[urn_header]

            if not value:
                continue

            urn_scheme = ContactURN.IMPORT_HEADER_TO_SCHEME[urn_header]

            if urn_scheme == TEL_SCHEME:

                value = regex.sub(r'[ \-()]+', '', value, regex.V0)

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
                    error_msg = "Invalid Phone number %s" % value
                    if not country:
                        error_msg = "Invalid Phone number or no country code specified for %s" % value

                    raise SmartImportRowError(error_msg)

                # in the past, test contacts have ended up in exports. Don't re-import them
                if value == OLD_TEST_CONTACT_TEL:
                    raise SmartImportRowError("Ignored test contact")

            urn = URN.from_parts(urn_scheme, value)
            search_contact = Contact.from_urn(org, urn, country)

            # if this is an anonymous org, don't allow updating
            if org.is_anon and search_contact and not is_admin:
                raise SmartImportRowError("Other existing contact on anonymous organization")

            urns.append(urn)

        if not urns and not (org.is_anon or uuid):
            error_str = "Missing any valid URNs"
            error_str += "; at least one among %s should be provided" % ", ".join(possible_urn_headers)

            raise SmartImportRowError(error_str)

        # title case our name
        name = field_dict.get(Contact.NAME, None)
        if name:
            name = " ".join([_.capitalize() for _ in name.split()])

        language = field_dict.get(Contact.LANGUAGE)
        if language is not None and len(language) != 3:
            language = None  # ignore anything that's not a 3-letter code

        # create new contact or fetch existing one
        contact = Contact.get_or_create(org, user, name, uuid=uuid, urns=urns, language=language, force_urn_update=True)

        # if they exist and are blocked, unblock them
        if contact.is_blocked:
            contact.unblock(user)

        for key in field_dict.keys():
            # ignore any reserved fields
            if key in Contact.RESERVED_FIELDS:
                continue

            value = field_dict[key]

            # date values need converted to localized strings
            if isinstance(value, datetime.date):
                value = org.format_date(value, True)

            contact.set_field(user, key, value)

        return contact

    @classmethod
    def prepare_fields(cls, field_dict, import_params=None, user=None):
        if not import_params or 'org_id' not in import_params or 'extra_fields' not in import_params:
            raise Exception('Import params must include org_id and extra_fields')

        field_dict['created_by'] = user
        field_dict['org'] = Org.objects.get(pk=import_params['org_id'])

        extra_fields = []

        # include extra fields specified in the params
        for field in import_params['extra_fields']:
            key = field['key']
            label = field['label']
            if key not in Contact.RESERVED_FIELDS:
                # column values are mapped to lower-cased column header names but we need them by contact field key
                value = field_dict[field['header']]
                del field_dict[field['header']]
                field_dict[key] = value

                # create the contact field if it doesn't exist
                ContactField.get_or_create(field_dict['org'], user, key, label, False, field['type'])
                extra_fields.append(key)
            else:
                raise Exception('Extra field %s is a reserved field name' % key)

        active_scheme = [scheme[0] for scheme in ContactURN.SCHEME_CHOICES if scheme[0] != TEL_SCHEME]

        # remove any field that's not a reserved field or an explicitly included extra field
        for key in field_dict.keys():
            if key not in Contact.RESERVED_FIELDS and key not in extra_fields and key not in active_scheme:
                del field_dict[key]

        return field_dict

    @classmethod
    def get_org_import_file_headers(cls, csv_file, org):
        csv_file.open()

        # this file isn't good enough, lets write it to local disk
        from django.conf import settings
        from uuid import uuid4

        # make sure our tmp directory is present (throws if already present)
        try:
            os.makedirs(os.path.join(settings.MEDIA_ROOT, 'tmp'))
        except Exception:
            pass

        # write our file out
        tmp_file = os.path.join(settings.MEDIA_ROOT, 'tmp/%s' % str(uuid4()))

        out_file = open(tmp_file, 'w')
        out_file.write(csv_file.read())
        out_file.close()

        try:
            headers = SmartModel.get_import_file_headers(open(tmp_file))
        finally:
            os.remove(tmp_file)

        Contact.validate_org_import_header(headers, org)

        # return the column headers which can become contact fields
        return [header for header in headers if header.strip().lower() and header.strip().lower() not in Contact.RESERVED_FIELDS]

    @classmethod
    def validate_org_import_header(cls, headers, org):
        possible_headers = [h[0] for h in IMPORT_HEADERS]
        found_headers = [h for h in headers if h in possible_headers]

        capitalized_possible_headers = '", "'.join([h.capitalize() for h in possible_headers])

        if 'uuid' in headers:
            return

        if not found_headers:
            raise Exception(ugettext('The file you provided is missing a required header. At least one of "%s" '
                                     'should be included.' % capitalized_possible_headers))

        if 'name' not in headers:
            raise Exception(ugettext('The file you provided is missing a required header called "Name".'))

    @classmethod
    def import_csv(cls, task, log=None):
        from xlrd import XLRDError

        filename = task.csv_file.file
        user = task.created_by

        # additional parameters are optional
        import_params = None
        if task.import_params:
            try:
                import_params = json.loads(task.import_params)
            except Exception:
                logger.error("Failed to parse JSON for contact import #d" % task.pk, exc_info=True)

        # this file isn't good enough, lets write it to local disk
        from django.conf import settings
        from uuid import uuid4

        # make sure our tmp directory is present (throws if already present)
        try:
            os.makedirs(os.path.join(settings.MEDIA_ROOT, 'tmp'))
        except Exception:
            pass

        # rewrite our file to local disk
        tmp_file = os.path.join(settings.MEDIA_ROOT, 'tmp/%s' % str(uuid4()))
        filename.open()

        out_file = open(tmp_file, 'w')
        out_file.write(filename.read())
        out_file.close()

        import_results = dict()

        try:
            contacts = cls.import_xls(open(tmp_file), user, import_params, log, import_results)
        except XLRDError:
            contacts = cls.import_raw_csv(open(tmp_file), user, import_params, log, import_results)
        finally:
            os.remove(tmp_file)

        # save the import results even if no record was created
        task.import_results = json.dumps(import_results)

        # don't create a group if there are no contacts
        if not contacts:
            return contacts

        # we always create a group after a successful import (strip off 8 character uniquifier by django)
        group_name = os.path.splitext(os.path.split(import_params.get('original_filename'))[-1])[0]
        group_name = group_name.replace('_', ' ').replace('-', ' ').title()

        # group org is same as org of any contact in that group
        group_org = contacts[0].org
        group = ContactGroup.create_static(group_org, user, group_name, task)

        num_creates = 0
        for contact in contacts:
            # if contact has is_new attribute, then we have created a new contact rather than updated an existing one
            if getattr(contact, 'is_new', False):
                num_creates += 1

            group.contacts.add(contact)

        # if we aren't whitelisted, check for sequential phone numbers
        if not group_org.is_whitelisted():
            try:
                # get all of our phone numbers for the imported contacts
                paths = [int(u.path) for u in ContactURN.objects.filter(scheme=TEL_SCHEME, contact__in=[c.pk for c in contacts])]
                paths = sorted(paths)

                last_path = None
                sequential = 0
                for path in paths:
                    if last_path:
                        if path - last_path == 1:
                            sequential += 1
                    last_path = path

                    if sequential > SEQUENTIAL_CONTACTS_THRESHOLD:
                        group_org.set_suspended()
                        break

            except Exception:  # pragma: no-cover
                # if we fail to parse phone numbers for any reason just punt
                pass

        # overwrite the import results for adding the counts
        import_results['creates'] = num_creates
        import_results['updates'] = len(contacts) - num_creates
        task.import_results = json.dumps(import_results)

        return contacts

    @classmethod
    def apply_action_label(cls, user, contacts, group, add):
        return group.update_contacts(user, contacts, add)

    @classmethod
    def apply_action_block(cls, user, contacts):
        changed = []

        for contact in contacts:
            contact.block(user)
            changed.append(contact.pk)
        return changed

    @classmethod
    def apply_action_unblock(cls, user, contacts):
        changed = []

        for contact in contacts:
            contact.unblock(user)
            changed.append(contact.pk)
        return changed

    @classmethod
    def apply_action_delete(cls, user, contacts):
        changed = []

        for contact in contacts:
            contact.release(user)
            changed.append(contact.pk)
        return changed

    @classmethod
    def apply_action_unstop(cls, user, contacts):
        changed = []

        for contact in contacts:
            contact.unstop(user)
            changed.append(contact.pk)
        return changed

    def block(self, user):
        """
        Blocks this contact removing it from all non-dynamic groups
        """
        if self.is_test:
            raise ValueError("Can't block a test contact")

        self.clear_all_groups(user)

        self.is_blocked = True
        self.modified_by = user
        self.save(update_fields=('is_blocked', 'modified_on', 'modified_by'))

    def unblock(self, user):
        """
        Unlocks this contact and marking it as not archived
        """
        self.is_blocked = False
        self.modified_by = user
        self.save(update_fields=('is_blocked', 'modified_on', 'modified_by'))

        self.reevaluate_dynamic_groups()

    def stop(self, user):
        """
        Marks this contact has stopped, removing them from all groups.
        """
        if self.is_test:
            raise ValueError("Can't stop a test contact")

        self.is_stopped = True
        self.modified_by = user
        self.save(update_fields=['is_stopped', 'modified_on', 'modified_by'])

        self.clear_all_groups(get_anonymous_user())

    def unstop(self, user):
        """
        Unstops this contact, re-adding them to any dynamic groups they belong to
        """
        self.is_stopped = False
        self.modified_by = user
        self.save(update_fields=['is_stopped', 'modified_on', 'modified_by'])

        # re-add them to any dynamic groups they would belong to
        self.reevaluate_dynamic_groups()

    def release(self, user):
        """
        Releases (i.e. deletes) this contact, provided it is currently not deleted
        """
        # detach all contact's URNs
        self.update_urns(user, [])

        # remove from all groups
        self.clear_all_groups(user)

        # release all messages with this contact
        for msg in self.msgs.all():
            msg.release()

        # release all channel events with this contact
        for event in self.channel_events.all():
            event.release()

        # remove all flow runs and steps
        for run in self.runs.all():
            run.release()

        self.is_active = False
        self.modified_by = user
        self.save(update_fields=('is_active', 'modified_on', 'modified_by'))

    @classmethod
    def bulk_cache_initialize(cls, org, contacts, for_show_only=False):
        """
        Performs optimizations on our contacts to prepare them to send. This includes loading all our contact fields for
        variable substitution.
        """
        from temba.values.models import Value

        if not contacts:
            return

        # get our contact fields
        fields = ContactField.objects.filter(org=org)
        if for_show_only:
            fields = fields.filter(show_in_table=True)

        # build id maps to avoid re-fetching contact objects
        key_map = {f.id: f.key for f in fields}

        contact_map = dict()
        for contact in contacts:
            contact_map[contact.id] = contact
            setattr(contact, '__urns', list())  # initialize URN list cache (setattr avoids name mangling or __urns)

        # cache all field values
        values = Value.objects.filter(contact_id__in=contact_map.keys(),
                                      contact_field_id__in=key_map.keys()).select_related('contact_field', 'location_value')
        for value in values:
            contact = contact_map[value.contact_id]
            field_key = key_map[value.contact_field_id]
            cache_attr = '__field__%s' % field_key
            setattr(contact, cache_attr, value)

        # set missing fields as None attributes to avoid cache fetches later
        for contact in contacts:
            for field in fields:
                cache_attr = '__field__%s' % field.key
                if not hasattr(contact, cache_attr):
                    setattr(contact, cache_attr, None)

        # cache all URN values (a priority ordered list on each contact)
        urns = ContactURN.objects.filter(contact__in=contact_map.keys()).order_by('contact', '-priority', 'pk')
        for urn in urns:
            contact = contact_map[urn.contact_id]
            getattr(contact, '__urns').append(urn)

    def build_message_context(self):
        """
        Builds a dictionary suitable for use in variable substitution in messages.
        """
        org = self.org
        contact_dict = dict(__default__=self.get_display(org=org))
        contact_dict[Contact.NAME] = self.name if self.name else ''
        contact_dict[Contact.FIRST_NAME] = self.first_name(org)
        contact_dict['tel_e164'] = self.get_urn_display(scheme=TEL_SCHEME, org=org, formatted=False)
        contact_dict['groups'] = ",".join([_.name for _ in self.user_groups.all()])
        contact_dict['uuid'] = self.uuid
        contact_dict[Contact.LANGUAGE] = self.language

        # add all URNs
        for scheme, label in ContactURN.SCHEME_CHOICES:
            urn_value = self.get_urn_display(scheme=scheme, org=org)
            contact_dict[scheme] = urn_value if urn_value is not None else ''

        field_values = Value.objects.filter(contact=self).exclude(contact_field=None)\
                                                         .exclude(contact_field__is_active=False)\
                                                         .select_related('contact_field')

        # get all the values for this contact
        contact_values = {v.contact_field.key: v for v in field_values}

        # add all active fields to our context
        for field in ContactField.objects.filter(org_id=self.org_id, is_active=True).select_related('org'):
            field_value = Contact.get_field_display_for_value(field, contact_values.get(field.key, None))
            contact_dict[field.key] = field_value if field_value is not None else ''

        return contact_dict

    def first_name(self, org):
        if not self.name:
            return self.get_urn_display(org)
        else:
            names = self.name.split()
            if len(names) > 1:
                return names[0]
            else:
                return self.name

    def set_first_name(self, first_name):
        if not self.name:
            self.name = first_name
        else:
            names = self.name.split()
            names = [first_name] + names[1:]
            self.name = " ".join(names)

    def set_preferred_channel(self, channel):
        """
        Sets the preferred channel for communicating with this Contact
        """
        if channel is None:
            return

        # don't set preferred channels for test contacts
        if self.is_test:
            return

        urns = self.get_urns()

        # make sure all urns of the same scheme use this channel (only do this for TEL, others are channel specific)
        if channel.scheme == TEL_SCHEME:
            for urn in urns:
                if urn.scheme == channel.scheme and urn.channel_id != channel.id:
                    urn.channel = channel
                    urn.save(update_fields=['channel'])

        # if our scheme isn't the highest priority
        if urns and urns[0].scheme != channel.scheme:
            # update the highest URN of the right scheme to be highest
            for urn in urns[1:]:
                if urn.scheme == channel.scheme:
                    urn.priority = urns[0].priority + 1
                    urn.save(update_fields=['priority'])

                    # clear our URN cache, order is different now
                    self.clear_urn_cache()
                    break

    def get_urns_for_scheme(self, scheme):
        """
        Returns all the URNs for the passed in scheme
        """
        return self.urns.filter(scheme=scheme).order_by('-priority', 'pk')

    def clear_urn_cache(self):
        if hasattr(self, '__urns'):
            delattr(self, '__urns')

    def get_urns(self):
        """
        Gets all URNs ordered by priority
        """
        cache_attr = '__urns'
        if hasattr(self, cache_attr):
            return getattr(self, cache_attr)

        urns = self.urns.order_by('-priority', 'pk')
        setattr(self, cache_attr, urns)
        return urns

    def get_urn(self, schemes=None):
        """
        Gets the highest priority matching URN for this contact. Schemes may be a single scheme or a set/list/tuple
        """
        if isinstance(schemes, basestring):
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

    def update_urns(self, user, urns):
        """
        Updates the URNs on this contact to match the provided list, i.e. detaches any existing not included.
        The URNs are supplied in order of priority, most preferred URN first.
        """
        country = self.org.get_country_code()

        urns_created = []  # new URNs created
        urns_attached = []  # existing orphan URNs attached
        urns_retained = []  # existing URNs retained

        # perform everything in a org-level lock to prevent duplication by different instances. Org-level is required
        # to prevent conflicts with get_or_create which uses an org-level lock.

        with self.org.lock_on(OrgLock.contacts):

            # urns are submitted in order of priority
            priority = ContactURN.PRIORITY_HIGHEST

            for urn_as_string in urns:
                normalized = URN.normalize(urn_as_string, country)
                urn = ContactURN.objects.filter(org=self.org, urn=normalized).first()
                if not urn:
                    urn = ContactURN.create(self.org, self, normalized, priority=priority)
                    urns_created.append(urn)

                # unassigned URN or assigned to someone else
                elif not urn.contact or urn.contact != self:
                    urn.contact = self
                    urn.priority = priority
                    urn.save()
                    urns_attached.append(urn)

                else:
                    if urn.priority != priority:
                        urn.priority = priority
                        urn.save()
                    urns_retained.append(urn)

                # step down our priority
                priority -= 1

        # detach any existing URNs that weren't included
        urn_ids = [u.pk for u in (urns_created + urns_attached + urns_retained)]
        urns_detached_qs = ContactURN.objects.filter(contact=self).exclude(pk__in=urn_ids)
        urns_detached_qs.update(contact=None)
        urns_detached = list(urns_detached_qs)

        self.modified_by = user
        self.save(update_fields=('modified_on', 'modified_by'))

        # trigger updates based all urns created or detached
        self.handle_update(urns=[u.urn for u in (urns_created + urns_attached + urns_detached)])

        # clear URN cache
        if hasattr(self, '__urns'):
            delattr(self, '__urns')

    def update_static_groups(self, user, groups):
        """
        Updates the static groups for this contact to match the provided list, i.e. leaves any existing not included
        """
        current_static_groups = self.user_groups.filter(query=None)

        # figure out our diffs, what groups need to be added or removed
        remove_groups = [g for g in current_static_groups if g not in groups]
        add_groups = [g for g in groups if g not in current_static_groups]

        for group in remove_groups:
            group.update_contacts(user, [self], add=False)

        for group in add_groups:
            group.update_contacts(user, [self], add=True)

    def reevaluate_dynamic_groups(self, for_field=None):
        """
        Re-evaluates this contacts membership of dynamic groups. If field is specified then re-evaluation is only
        performed for those groups which reference that field.
        """
        affected_dynamic_groups = ContactGroup.get_user_groups(self.org, dynamic=True)

        if for_field:
            affected_dynamic_groups = affected_dynamic_groups.filter(query_fields=for_field)

        group_change = False
        for group in affected_dynamic_groups:
            changed = group.reevaluate_contacts([self])
            if changed:
                group_change = True

        return group_change

    def clear_all_groups(self, user):
        """
        Removes this contact from all groups - static and dynamic.
        """
        for group in self.user_groups.all():
            group.remove_contacts(user, [self])

    def get_display(self, org=None, formatted=True, short=False):
        """
        Gets a displayable name or URN for the contact. If available, org can be provided to avoid having to fetch it
        again based on the contact.
        """
        if not org:
            org = self.org

        if self.name:
            res = self.name
        elif org.is_anon:
            res = self.anon_identifier
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

        if org.is_anon:
            return self.anon_identifier

        urn = self.get_urn(scheme)
        return urn.get_display(org=org, formatted=formatted, international=international) if urn else ''

    def raw_tel(self):
        tel = self.get_urn(TEL_SCHEME)
        if tel:
            return tel.path

    def send(self, text, user, trigger_send=True, response_to=None, message_context=None):
        from temba.msgs.models import Msg

        msg = Msg.create_outgoing(self.org, user, self, text, priority=Msg.PRIORITY_HIGH,
                                  response_to=response_to, message_context=message_context)
        if trigger_send:
            self.org.trigger_send([msg])

        return msg

    def __unicode__(self):
        return self.get_display()


class ContactURN(models.Model):
    """
    A Universal Resource Name used to uniquely identify contacts, e.g. tel:+1234567890 or twitter:example
    """
    # schemes that we actually support
    SCHEME_CHOICES = tuple((c[0], c[1]) for c in URN_SCHEME_CONFIG)
    CONTEXT_KEYS_TO_SCHEME = {c[3]: c[0] for c in URN_SCHEME_CONFIG}
    CONTEXT_KEYS_TO_LABEL = {c[3]: c[1] for c in URN_SCHEME_CONFIG}
    IMPORT_HEADER_TO_SCHEME = {s[0]: s[1] for s in IMPORT_HEADERS}

    SCHEMES_SUPPORTING_FOLLOW = {TWITTER_SCHEME}  # schemes that support "follow" triggers
    SCHEMES_SUPPORTING_NEW_CONVERSATION = {FACEBOOK_SCHEME}  # schemes that support "new conversation" triggers

    EXPORT_FIELDS = {
        TEL_SCHEME: dict(label="Phone", key=Contact.PHONE, id=0, field=None, urn_scheme=TEL_SCHEME),
        TWITTER_SCHEME: dict(label="Twitter", key=None, id=0, field=None, urn_scheme=TWITTER_SCHEME),
        EXTERNAL_SCHEME: dict(label="External", key=None, id=0, field=None, urn_scheme=EXTERNAL_SCHEME),
        EMAIL_SCHEME: dict(label="Email", key=None, id=0, field=None, urn_scheme=EMAIL_SCHEME),
        TELEGRAM_SCHEME: dict(label="Telegram", key=None, id=0, field=None, urn_scheme=TELEGRAM_SCHEME),
        FACEBOOK_SCHEME: dict(label="Facebook", key=None, id=0, field=None, urn_scheme=FACEBOOK_SCHEME),
    }

    PRIORITY_LOWEST = 1
    PRIORITY_STANDARD = 50
    PRIORITY_HIGHEST = 99

    PRIORITY_DEFAULTS = {TEL_SCHEME: PRIORITY_STANDARD, TWITTER_SCHEME: 90, FACEBOOK_SCHEME: 90, TELEGRAM_SCHEME: 90}

    ANON_MASK = '*' * 8  # returned instead of URN values for anon orgs

    contact = models.ForeignKey(Contact, null=True, blank=True, related_name='urns',
                                help_text="The contact that this URN is for, can be null")

    urn = models.CharField(max_length=255, choices=SCHEME_CHOICES,
                           help_text="The Universal Resource Name as a string. ex: tel:+250788383383")

    path = models.CharField(max_length=255,
                            help_text="The path component of our URN. ex: +250788383383")

    scheme = models.CharField(max_length=128,
                              help_text="The scheme for this URN, broken out for optimization reasons, ex: tel")

    org = models.ForeignKey(Org,
                            help_text="The organization for this URN, can be null")

    priority = models.IntegerField(default=PRIORITY_STANDARD,
                                   help_text="The priority of this URN for the contact it is associated with")

    channel = models.ForeignKey(Channel, null=True, blank=True,
                                help_text="The preferred channel for this URN")

    @classmethod
    def get_or_create(cls, org, contact, urn_as_string, channel=None):
        urn = cls.lookup(org, urn_as_string)

        # not found? create it
        if not urn:
            urn = cls.create(org, contact, urn_as_string, channel=channel)

        return urn

    @classmethod
    def create(cls, org, contact, urn_as_string, channel=None, priority=None):
        scheme, path = URN.to_parts(urn_as_string)

        if not priority:
            priority = cls.PRIORITY_DEFAULTS.get(scheme, cls.PRIORITY_STANDARD)

        return cls.objects.create(org=org, contact=contact, priority=priority, channel=channel,
                                  scheme=scheme, path=path, urn=urn_as_string)

    @classmethod
    def lookup(cls, org, urn_as_string, country_code=None, normalize=True):
        """
        Looks up an existing URN by a formatted URN string, e.g. "tel:+250234562222"
        """
        if normalize:
            urn_as_string = URN.normalize(urn_as_string, country_code)

        return cls.objects.filter(org=org, urn=urn_as_string).select_related('contact').first()

    def update_affinity(self, channel):
        """
        Checks and optionally updates the affinity for this contact URN
        """
        if channel and self.channel != channel:
            self.channel = channel
            self.save(update_fields=['channel'])

    def ensure_number_normalization(self, country_code):
        """
        Tries to normalize our phone number from a possible 10 digit (0788 383 383) to a 12 digit number
        with country code (+250788383383) using the country we now know about the channel.
        """
        number = self.path

        if number and not number[0] == '+' and country_code:
            (norm_number, valid) = URN.normalize_number(number, country_code)

            # don't trounce existing contacts with that country code already
            norm_urn = URN.from_tel(norm_number)
            if not ContactURN.objects.filter(urn=norm_urn, org_id=self.org_id).exclude(id=self.id):
                self.urn = norm_urn
                self.path = norm_number
                self.save()

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

        if self.scheme == TEL_SCHEME and formatted:
            # if we don't want a full tell, see if we can show the national format instead
            try:
                if self.path and self.path[0] == '+':
                    phone_format = phonenumbers.PhoneNumberFormat.NATIONAL
                    if international:
                        phone_format = phonenumbers.PhoneNumberFormat.INTERNATIONAL

                    return phonenumbers.format_number(phonenumbers.parse(self.path, None), phone_format)

            except Exception:  # pragma: no cover
                pass

        return self.path

    def __unicode__(self):  # pragma: no cover
        return self.urn

    class Meta:
        unique_together = ('urn', 'org')
        ordering = ('-priority', 'id')


class SystemContactGroupManager(models.Manager):
    def get_queryset(self):
        return super(SystemContactGroupManager, self).get_queryset().exclude(group_type=ContactGroup.TYPE_USER_DEFINED)


class UserContactGroupManager(models.Manager):
    def get_queryset(self):
        return super(UserContactGroupManager, self).get_queryset().filter(group_type=ContactGroup.TYPE_USER_DEFINED,
                                                                          is_active=True)


class ContactGroup(TembaModel):
    MAX_NAME_LEN = 64

    TYPE_ALL = 'A'
    TYPE_BLOCKED = 'B'
    TYPE_STOPPED = 'S'
    TYPE_USER_DEFINED = 'U'

    TYPE_CHOICES = ((TYPE_ALL, "All Contacts"),
                    (TYPE_BLOCKED, "Blocked Contacts"),
                    (TYPE_STOPPED, "Stopped Contacts"),
                    (TYPE_USER_DEFINED, "User Defined Groups"))

    name = models.CharField(verbose_name=_("Name"), max_length=MAX_NAME_LEN,
                            help_text=_("The name of this contact group"))

    group_type = models.CharField(max_length=1, choices=TYPE_CHOICES, default=TYPE_USER_DEFINED,
                                  help_text=_("What type of group it is, either user defined or one of our system groups"))

    contacts = models.ManyToManyField(Contact, verbose_name=_("Contacts"), related_name='all_groups')

    count = models.IntegerField(default=0,
                                verbose_name=_("Count"), help_text=_("The number of contacts in this group"))

    org = models.ForeignKey(Org, related_name='all_groups',
                            verbose_name=_("Org"), help_text=_("The organization this group is part of"))

    import_task = models.ForeignKey(ImportTask, null=True, blank=True)

    query = models.TextField(null=True, help_text=_("The membership query for this group"))

    query_fields = models.ManyToManyField(ContactField, verbose_name=_("Query Fields"))

    # define some custom managers to do the filtering of user / system groups for us
    all_groups = models.Manager()
    system_groups = SystemContactGroupManager()
    user_groups = UserContactGroupManager()

    @classmethod
    def get_user_group(cls, org, name):
        """
        Returns the user group with the passed in name
        """
        return cls.user_groups.filter(name__iexact=cls.clean_name(name), org=org).first()

    @classmethod
    def get_user_groups(cls, org, dynamic=None):
        """
        Gets all user groups for the given org - optionally filtering by dynamic vs static
        """
        groups = cls.user_groups.filter(org=org, is_active=True)
        if dynamic is not None:
            groups = groups.filter(query=None) if dynamic is False else groups.exclude(query=None)

        return groups

    @classmethod
    def get_or_create(cls, org, user, name, group_uuid=None):
        existing = None

        if group_uuid is not None:
            existing = ContactGroup.user_groups.filter(org=org, uuid=group_uuid).first()

        if not existing:
            existing = ContactGroup.get_user_group(org, name)

        if existing:
            return existing

        return cls.create_static(org, user, name)

    @classmethod
    def create_static(cls, org, user, name, task=None):
        """
        Creates a static group whose members will be manually added and removed
        """
        return cls._create(org, user, name, task=task)

    @classmethod
    def create_dynamic(cls, org, user, name, query):
        """
        Creates a dynamic group with the given query, e.g. gender=M
        """
        if not query:
            raise ValueError("Query cannot be empty for a dynamic group")

        group = cls._create(org, user, name, query=query)
        group.update_query(query)
        return group

    @classmethod
    def _create(cls, org, user, name, task=None, query=None):
        full_group_name = cls.clean_name(name)

        if not cls.is_valid_name(full_group_name):
            raise ValueError("Invalid group name: %s" % name)

        # look for name collision and append count if necessary
        existing = cls.get_user_group(org, full_group_name)

        count = 2
        while existing:
            full_group_name = "%s %d" % (name, count)
            existing = cls.get_user_group(org, full_group_name)
            count += 1

        return cls.user_groups.create(org=org, name=full_group_name, query=query,
                                      import_task=task, created_by=user, modified_by=user)

    @classmethod
    def clean_name(cls, name):
        """
        Returns a normalized name for the passed in group name
        """
        return None if name is None else name.strip()[:cls.MAX_NAME_LEN]

    @classmethod
    def is_valid_name(cls, name):
        # don't allow empty strings, blanks, initial or trailing whitespace
        if not name or name.strip() != name:
            return False

        if len(name) > cls.MAX_NAME_LEN:
            return False

        # first character must be a word char
        return regex.match('\w', name[0], flags=regex.UNICODE)

    def update_contacts(self, user, contacts, add):
        """
        Manually adds or removes contacts from a static group. Returns contact ids of contacts whose membership changed.
        """
        if self.group_type != self.TYPE_USER_DEFINED or self.is_dynamic:  # pragma: no cover
            raise ValueError("Can't add or remove contacts from system or dynamic groups")

        return self._update_contacts(user, contacts, add)

    def reevaluate_contacts(self, contacts):
        """
        Re-evaluates whether contacts belong in a dynamic group. Returns contacts whose membership changed.
        """
        if self.group_type != self.TYPE_USER_DEFINED or not self.is_dynamic:  # pragma: no cover
            raise ValueError("Can't re-evaluate contacts against system or static groups")

        user = get_anonymous_user()
        changed = set()
        for contact in contacts:
            qualifies = self._check_dynamic_membership(contact)
            changed = self._update_contacts(user, [contact], qualifies)
            if changed:
                changed.add(contact)
        return changed

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

        # invalidate our result cache for anybody depending on this group if it changed
        if changed:
            Value.invalidate_cache(group=self)

            # update modified on in small batches to avoid long table lock, and having too many non-unique values for
            # modified_on which is the primary ordering for the API
            for batch in chunk_list(changed, 100):
                Contact.objects.filter(org=self.org, pk__in=batch).update(modified_by=user, modified_on=timezone.now())

        return changed

    def update_query(self, query):
        """
        Updates the query for a dynamic group
        """
        if not self.is_dynamic:
            raise ValueError("Can only update query for a dynamic group")

        self.query = query
        self.save(update_fields=('query',))

        self.query_fields.clear()

        for match in regex.finditer(r'\w+', self.query, regex.V0):
            field = ContactField.objects.filter(key=match.group(), org=self.org, is_active=True).first()
            if field:
                self.query_fields.add(field)

        members = list(self._get_dynamic_members())
        self.contacts.clear()
        self.contacts.add(*members)

    def _get_dynamic_members(self):
        """
        For dynamic groups, this returns the set of contacts who belong in this group
        """
        if not self.is_dynamic:  # pragma: no cover
            raise ValueError("Can only be called on dynamic groups")

        members, is_complex = Contact.search(self.org, self.query)
        return members

    def _check_dynamic_membership(self, contact):
        """
        For dynamic groups, determines whether the given contact belongs in the group
        """
        return self._get_dynamic_members().filter(pk=contact.pk).count() == 1

    @classmethod
    def get_system_group_queryset(cls, org, group_type):
        if group_type == cls.TYPE_USER_DEFINED:  # pragma: no cover
            raise ValueError("Can only get system group querysets")

        return cls.all_groups.get(org=org, group_type=group_type).contacts.all()

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
        return ContactGroupCount.contact_count(self)

    def release(self):
        """
        Releases (i.e. deletes) this group, removing all contacts and marking as inactive
        """
        self.is_active = False
        self.save()
        self.contacts.clear()

        # delete any event fires related to our group
        from temba.campaigns.models import EventFire
        EventFire.objects.filter(event__campaign__group=self, fired=None).delete()

        # mark any triggers that operate only on this group as inactive
        from temba.triggers.models import Trigger
        Trigger.objects.filter(is_active=True, groups=self).update(is_active=False, is_archived=True)

        Value.invalidate_cache(group=self)

    @property
    def is_dynamic(self):
        return self.query is not None

    def analytics_json(self):
        if self.get_member_count() > 0:
            return dict(name=self.name, id=self.pk, count=self.get_member_count())

    def __unicode__(self):
        return self.name


class ContactGroupCount(models.Model):
    """
    Maintains counts of contact groups. These are calculated via triggers on the database and squashed
    by a reocurring task.
    """
    group = models.ForeignKey(ContactGroup, related_name='counts', db_index=True)
    count = models.IntegerField(default=0)

    LAST_SQUASH_KEY = 'last_contactgroupcount_squash'

    @classmethod
    def squash_counts(cls):
        # get the id of the last count we squashed
        r = get_redis_connection()
        last_squash = r.get(ContactGroupCount.LAST_SQUASH_KEY)
        if not last_squash:
            last_squash = 0

        # get the unique group ids for all new ones
        start = time.time()
        squash_count = 0
        for count in ContactGroupCount.objects.filter(id__gt=last_squash).order_by('group_id').distinct('group_id'):
            # perform our atomic squash in SQL by calling our squash method
            with connection.cursor() as c:
                c.execute("SELECT temba_squash_contactgroupcounts(%s);", (count.group_id,))

            squash_count += 1

        # insert our new top squashed id
        max_id = ContactGroupCount.objects.all().order_by('-id').first()
        if max_id:
            r.set(ContactGroupCount.LAST_SQUASH_KEY, max_id.id)

        print "Squashed group counts for %d groups in %0.3fs" % (squash_count, time.time() - start)

    @classmethod
    def contact_count(cls, group):
        count = ContactGroupCount.objects.filter(group=group)
        count = count.aggregate(Sum('count')).get('count__sum', 0)
        return 0 if count is None else count

    @classmethod
    def populate_for_group(cls, group):
        # remove old ones
        ContactGroupCount.objects.filter(group=group).delete()

        # get test contacts on this org
        test_contacts = Contact.objects.filter(org=group.org, is_test=True).values('id')

        # calculate our count for the group
        count = group.contacts.all().exclude(id__in=test_contacts).count()

        # insert updated count, returning it
        return ContactGroupCount.objects.create(group=group, count=count)

    def __unicode__(self):
        return "ContactGroupCount[%d:%d]" % (self.group_id, self.count)


class ExportContactsTask(SmartModel):

    org = models.ForeignKey(Org, related_name='contacts_exports', help_text=_("The Organization of the user."))
    group = models.ForeignKey(ContactGroup, null=True, related_name='exports', help_text=_("The unique group to export"))
    task_id = models.CharField(null=True, max_length=64)
    is_finished = models.BooleanField(default=False,
                                      help_text=_("Whether this export has completed"))
    uuid = models.CharField(max_length=36, null=True,
                            help_text=_("The uuid used to name the resulting export file"))

    def start_export(self):
        """
        Starts our export, this just wraps our do-export in a try/finally so we can track
        when the export is complete.
        """
        try:
            start = time.time()
            self.do_export()
        finally:
            elapsed = time.time() - start
            analytics.track(self.created_by.username, 'temba.contact_export_latency', properties=dict(value=elapsed))

            self.is_finished = True
            self.save(update_fields=['is_finished'])

    def get_export_fields_and_schemes(self):

        fields = [dict(label='UUID', key=Contact.UUID, id=0, field=None, urn_scheme=None),
                  dict(label='Name', key=Contact.NAME, id=0, field=None, urn_scheme=None)]

        scheme_counts = dict()
        if not self.org.is_anon:
            active_urn_schemes = [c[0] for c in ContactURN.SCHEME_CHOICES]

            scheme_counts = {scheme: ContactURN.objects.filter(org=self.org, scheme=scheme).exclude(contact=None).values('contact').annotate(count=Count('contact')).aggregate(Max('count'))['count__max'] for scheme in active_urn_schemes}

            schemes = scheme_counts.keys()
            schemes.sort()

            for scheme in schemes:
                count = scheme_counts[scheme]
                if count is not None:
                    for i in range(count):
                        field_dict = ContactURN.EXPORT_FIELDS[scheme].copy()
                        field_dict['position'] = i
                        fields.append(field_dict)

        with SegmentProfiler("building up contact fields"):
            contact_fields_list = ContactField.objects.filter(org=self.org, is_active=True).select_related('org')
            for contact_field in contact_fields_list:
                fields.append(dict(field=contact_field,
                                   label=contact_field.label,
                                   key=contact_field.key,
                                   id=contact_field.id,
                                   urn_scheme=None))

        return fields, scheme_counts

    def do_export(self):
        fields, scheme_counts = self.get_export_fields_and_schemes()

        with SegmentProfiler("build up contact ids"):
            all_contacts = Contact.get_contacts(self.org)
            if self.group:
                all_contacts = all_contacts.filter(all_groups=self.group)

            contact_ids = [c['id'] for c in all_contacts.order_by('name', 'id').values('id')]

        # create our exporter
        exporter = TableExporter("Contact", [c['label'] for c in fields])

        current_contact = 0
        start = time.time()

        # in batches of 500 contacts
        for batch_ids in chunk_list(contact_ids, 500):
            with SegmentProfiler("output 500 contacts"):
                batch_ids = list(batch_ids)

                # fetch all the contacts for our batch
                batch_contacts = Contact.objects.filter(id__in=batch_ids).select_related('org')

                # to maintain our sort, we need to lookup by id, create a map of our id->contact to aid in that
                contact_by_id = {c.id: c for c in batch_contacts}

                # bulk initialize them
                Contact.bulk_cache_initialize(self.org, batch_contacts)

                for contact_id in batch_ids:
                    contact = contact_by_id[contact_id]

                    values = []
                    for col in range(len(fields)):
                        field = fields[col]

                        if field['key'] == Contact.NAME:
                            field_value = contact.name
                        elif field['key'] == Contact.UUID:
                            field_value = contact.uuid
                        elif field['urn_scheme'] is not None:
                            contact_urns = contact.get_urns()
                            scheme_urns = []
                            for urn in contact_urns:
                                if urn.scheme == field['urn_scheme']:
                                    scheme_urns.append(urn)
                            position = field['position']
                            if len(scheme_urns) > position:
                                urn_obj = scheme_urns[position]
                                field_value = urn_obj.get_display(org=self.org, formatted=False) if urn_obj else ''
                            else:
                                field_value = ''
                        else:
                            value = contact.get_field(field['key'])
                            field_value = Contact.get_field_display_for_value(field['field'], value)

                        if field_value is None:
                            field_value = ''

                        if field_value:
                            field_value = unicode(field_value)

                        values.append(field_value)

                    # write this contact's values
                    exporter.write_row(values)
                    current_contact += 1

                    # output some status information every 10,000 contacts
                    if current_contact % 10000 == 0:  # pragma: no cover
                        elapsed = time.time() - start
                        predicted = int(elapsed / (current_contact / (len(contact_ids) * 1.0)))

                        print "Export of %s contacts - %d%% (%s/%s) complete in %0.2fs (predicted %0.0fs)" % \
                            (self.org.name, current_contact * 100 / len(contact_ids),
                             "{:,}".format(current_contact), "{:,}".format(len(contact_ids)),
                             time.time() - start, predicted)

        # save as file asset associated with this task
        from temba.assets.models import AssetType
        from temba.assets.views import get_asset_url

        # get our table file
        table_file = exporter.save_file()

        self.uuid = str(uuid4())
        self.save(update_fields=['uuid'])

        store = AssetType.contact_export.store
        store.save(self.pk, File(table_file), 'csv' if exporter.is_csv else 'xls')

        branding = self.org.get_branding()
        download_url = branding['link'] + get_asset_url(AssetType.contact_export, self.pk)

        subject = "Your contacts export is ready"
        template = 'contacts/email/contacts_export_download'

        # force a gc
        import gc
        gc.collect()

        send_template_email(self.created_by.username, subject, template, dict(link=download_url), branding)
