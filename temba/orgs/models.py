# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import calendar
import itertools
import logging
import mimetypes
import os
import pycountry
import random
import re
import regex
import six
import stripe
import traceback

from collections import defaultdict
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.models import User, Group
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.urlresolvers import reverse
from django.core.files import File
from django.core.files.temp import NamedTemporaryFile
from django.db import models, transaction
from django.db.models import Sum, F, Q, Prefetch
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import ugettext_lazy as _
from django.utils.text import slugify
from django_redis import get_redis_connection
from enum import Enum
from requests import Session
from smartmin.models import SmartModel
from temba.bundles import get_brand_bundles, get_bundle_map
from temba.locations.models import AdminBoundary, BoundaryAlias
from temba.utils import analytics, languages
from temba.utils.cache import get_cacheable_result, get_cacheable_attr, incrby_existing
from temba.utils.currencies import currency_for_country
from temba.utils.dates import str_to_datetime, get_datetime_format, datetime_to_str
from temba.utils.email import send_template_email, send_simple_email, send_custom_smtp_email
from temba.utils.models import SquashableModel, JSONAsTextField
from temba.utils.text import random_string
from timezone_field import TimeZoneField
from six.moves.urllib.parse import urlparse
from uuid import uuid4


EARLIEST_IMPORT_VERSION = "3"


# making this a function allows it to be used as a default for Django fields
def get_current_export_version():
    from temba.flows.models import Flow
    return Flow.VERSIONS[-1]


MT_SMS_EVENTS = 1 << 0
MO_SMS_EVENTS = 1 << 1
MT_CALL_EVENTS = 1 << 2
MO_CALL_EVENTS = 1 << 3
ALARM_EVENTS = 1 << 4

ALL_EVENTS = MT_SMS_EVENTS | MO_SMS_EVENTS | MT_CALL_EVENTS | MO_CALL_EVENTS | ALARM_EVENTS

FREE_PLAN = 'FREE'
TRIAL_PLAN = 'TRIAL'
TIER1_PLAN = 'TIER1'
TIER2_PLAN = 'TIER2'
TIER3_PLAN = 'TIER3'

TIER_39_PLAN = 'TIER_39'
TIER_249_PLAN = 'TIER_249'
TIER_449_PLAN = 'TIER_449'

DAYFIRST = 'D'
MONTHFIRST = 'M'

PLANS = ((FREE_PLAN, _("Free Plan")),
         (TRIAL_PLAN, _("Trial")),
         (TIER_39_PLAN, _("Bronze")),
         (TIER1_PLAN, _("Silver")),
         (TIER2_PLAN, _("Gold (Legacy)")),
         (TIER3_PLAN, _("Platinum (Legacy)")),
         (TIER_249_PLAN, _("Gold")),
         (TIER_449_PLAN, _("Platinum")))

DATE_PARSING = ((DAYFIRST, "DD-MM-YYYY"),
                (MONTHFIRST, "MM-DD-YYYY"))

APPLICATION_SID = 'APPLICATION_SID'
ACCOUNT_SID = 'ACCOUNT_SID'
ACCOUNT_TOKEN = 'ACCOUNT_TOKEN'

NEXMO_KEY = 'NEXMO_KEY'
NEXMO_SECRET = 'NEXMO_SECRET'
NEXMO_UUID = 'NEXMO_UUID'
NEXMO_APP_ID = 'NEXMO_APP_ID'
NEXMO_APP_PRIVATE_KEY = 'NEXMO_APP_PRIVATE_KEY'

TRANSFERTO_ACCOUNT_LOGIN = 'TRANSFERTO_ACCOUNT_LOGIN'
TRANSFERTO_AIRTIME_API_TOKEN = 'TRANSFERTO_AIRTIME_API_TOKEN'
TRANSFERTO_ACCOUNT_CURRENCY = 'TRANSFERTO_ACCOUNT_CURRENCY'

SMTP_FROM_EMAIL = 'SMTP_FROM_EMAIL'
SMTP_HOST = 'SMTP_HOST'
SMTP_USERNAME = 'SMTP_USERNAME'
SMTP_PASSWORD = 'SMTP_PASSWORD'
SMTP_PORT = 'SMTP_PORT'
SMTP_ENCRYPTION = 'SMTP_ENCRYPTION'

CHATBASE_AGENT_NAME = 'CHATBASE_AGENT_NAME'
CHATBASE_API_KEY = 'CHATBASE_API_KEY'
CHATBASE_TYPE_AGENT = 'agent'
CHATBASE_TYPE_USER = 'user'
CHATBASE_FEEDBACK = 'CHATBASE_FEEDBACK'
CHATBASE_VERSION = 'CHATBASE_VERSION'

ORG_STATUS = 'STATUS'
SUSPENDED = 'suspended'
RESTORED = 'restored'
WHITELISTED = 'whitelisted'

ORG_LOW_CREDIT_THRESHOLD = 500

ORG_CREDIT_OVER = 'O'
ORG_CREDIT_LOW = 'L'
ORG_CREDIT_EXPIRING = 'E'

# cache keys and TTLs
ORG_LOCK_KEY = 'org:%d:lock:%s'
ORG_CREDITS_TOTAL_CACHE_KEY = 'org:%d:cache:credits_total'
ORG_CREDITS_PURCHASED_CACHE_KEY = 'org:%d:cache:credits_purchased'
ORG_CREDITS_USED_CACHE_KEY = 'org:%d:cache:credits_used'
ORG_ACTIVE_TOPUP_KEY = 'org:%d:cache:active_topup'
ORG_ACTIVE_TOPUP_REMAINING = 'org:%d:cache:credits_remaining:%d'
ORG_CREDIT_EXPIRING_CACHE_KEY = 'org:%d:cache:credits_expiring_soon'
ORG_LOW_CREDIT_THRESHOLD_CACHE_KEY = 'org:%d:cache:low_credits_threshold'

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


@six.python_2_unicode_compatible
class Org(SmartModel):
    """
    An Org can have several users and is the main component that holds all Flows, Messages, Contacts, etc. Orgs
    know their country so they can deal with locally formatted numbers (numbers provided without a country code). As such,
    each org can only add phone channels from one country.

    Users will create new Org for Flows that should be kept separate (say for distinct projects), or for
    each country where they are deploying messaging applications.
    """
    name = models.CharField(verbose_name=_("Name"), max_length=128)
    plan = models.CharField(verbose_name=_("Plan"), max_length=16, choices=PLANS, default=FREE_PLAN,
                            help_text=_("What plan your organization is on"))
    plan_start = models.DateTimeField(verbose_name=_("Plan Start"), auto_now_add=True,
                                      help_text=_("When the user switched to this plan"))

    stripe_customer = models.CharField(verbose_name=_("Stripe Customer"), max_length=32, null=True, blank=True,
                                       help_text=_("Our Stripe customer id for your organization"))

    administrators = models.ManyToManyField(User, verbose_name=_("Administrators"), related_name="org_admins",
                                            help_text=_("The administrators in your organization"))

    viewers = models.ManyToManyField(User, verbose_name=_("Viewers"), related_name="org_viewers",
                                     help_text=_("The viewers in your organization"))

    editors = models.ManyToManyField(User, verbose_name=_("Editors"), related_name="org_editors",
                                     help_text=_("The editors in your organization"))

    surveyors = models.ManyToManyField(User, verbose_name=_("Surveyors"), related_name="org_surveyors",
                                       help_text=_("The users can login via Android for your organization"))

    language = models.CharField(verbose_name=_("Language"), max_length=64, null=True, blank=True,
                                choices=settings.LANGUAGES, help_text=_("The main language used by this organization"))

    timezone = TimeZoneField(verbose_name=_("Timezone"))

    date_format = models.CharField(verbose_name=_("Date Format"), max_length=1, choices=DATE_PARSING, default=DAYFIRST,
                                   help_text=_("Whether day comes first or month comes first in dates"))

    webhook = JSONAsTextField(null=True, verbose_name=_("Webhook"), default=dict,
                              help_text=_("Webhook endpoint and configuration"))

    webhook_events = models.IntegerField(default=0, verbose_name=_("Webhook Events"),
                                         help_text=_("Which type of actions will trigger webhook events."))

    country = models.ForeignKey('locations.AdminBoundary', null=True, blank=True, on_delete=models.SET_NULL,
                                help_text="The country this organization should map results for.")

    config = JSONAsTextField(null=True, default=dict, verbose_name=_("Configuration"),
                             help_text=_("More Organization specific configuration"))

    slug = models.SlugField(verbose_name=_("Slug"), max_length=255, null=True, blank=True, unique=True,
                            error_messages=dict(unique=_("This slug is not available")))

    is_anon = models.BooleanField(default=False,
                                  help_text=_("Whether this organization anonymizes the phone numbers of contacts within it"))

    is_purgeable = models.BooleanField(default=False,
                                       help_text=_("Whether this org's outgoing messages should be purged"))

    primary_language = models.ForeignKey('orgs.Language', null=True, blank=True, related_name='orgs',
                                         help_text=_('The primary language will be used for contacts with no language preference.'),
                                         on_delete=models.SET_NULL)

    brand = models.CharField(max_length=128, default=settings.DEFAULT_BRAND, verbose_name=_("Brand"),
                             help_text=_("The brand used in emails"))

    surveyor_password = models.CharField(null=True, max_length=128, default=None,
                                         help_text=_('A password that allows users to register as surveyors'))

    parent = models.ForeignKey('orgs.Org', null=True, blank=True, help_text=_('The parent org that manages this org'))

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

        if self.is_multi_org_tier() and not self.parent:

            if not timezone:
                timezone = self.timezone

            if not created_by:
                created_by = self.created_by

            # generate a unique slug
            slug = Org.get_unique_slug(name)

            org = Org.objects.create(name=name, timezone=timezone, brand=self.brand, parent=self, slug=slug,
                                     created_by=created_by, modified_by=created_by)

            org.administrators.add(created_by)

            # initialize our org, but without any credits
            org.initialize(branding=org.get_branding(), topup_size=0)

            return org

    def get_branding(self):
        from temba.middleware import BrandingMiddleware
        return BrandingMiddleware.get_branding_for_host(self.brand)

    def get_brand_domain(self):
        return self.get_branding()['domain']

    def lock_on(self, lock, qualifier=None):
        """
        Creates the requested type of org-level lock
        """
        r = get_redis_connection()
        lock_key = ORG_LOCK_KEY % (self.pk, lock.name)
        if qualifier:
            lock_key += (":%s" % qualifier)

        return r.lock(lock_key, ORG_LOCK_TTL)

    def has_contacts(self):
        """
        Gets whether this org has any contacts
        """
        from temba.contacts.models import ContactGroup

        counts = ContactGroup.get_system_group_counts(self, (ContactGroup.TYPE_ALL, ContactGroup.TYPE_BLOCKED))
        return (counts[ContactGroup.TYPE_ALL] + counts[ContactGroup.TYPE_BLOCKED]) > 0

    def clear_credit_cache(self):
        """
        Clears the given cache types (currently just credits) for this org. Returns number of keys actually deleted
        """
        r = get_redis_connection()
        active_topup_keys = [ORG_ACTIVE_TOPUP_REMAINING % (self.pk, topup.pk) for topup in self.topups.all()]
        return r.delete(ORG_CREDITS_TOTAL_CACHE_KEY % self.pk,
                        ORG_CREDIT_EXPIRING_CACHE_KEY % self.pk,
                        ORG_CREDITS_USED_CACHE_KEY % self.pk,
                        ORG_CREDITS_PURCHASED_CACHE_KEY % self.pk,
                        ORG_LOW_CREDIT_THRESHOLD_CACHE_KEY % self.pk,
                        ORG_ACTIVE_TOPUP_KEY % self.pk,
                        *active_topup_keys)

    def set_status(self, status):
        config = self.config
        config[ORG_STATUS] = status
        self.config = config
        self.save(update_fields=['config'])

    def set_suspended(self):
        self.set_status(SUSPENDED)

    def set_whitelisted(self):
        self.set_status(WHITELISTED)

    def set_restored(self):
        self.set_status(RESTORED)

    def is_suspended(self):
        return self.config.get(ORG_STATUS, None) == SUSPENDED

    def is_whitelisted(self):
        return self.config.get(ORG_STATUS, None) == WHITELISTED

    @transaction.atomic
    def import_app(self, data, user, site=None):
        from temba.flows.models import Flow
        from temba.campaigns.models import Campaign
        from temba.triggers.models import Trigger

        # determine if this app is being imported from the same site
        data_site = data.get('site', None)
        same_site = False

        # compare the hosts of the sites to see if they are the same
        if data_site and site:
            same_site = urlparse(data_site).netloc == urlparse(site).netloc

        # see if our export needs to be updated
        export_version = data.get('version', 0)
        if Flow.is_before_version(export_version, EARLIEST_IMPORT_VERSION):  # pragma: needs cover
            raise ValueError(_("Unknown version (%s)" % data.get('version', 0)))

        if Flow.is_before_version(export_version, get_current_export_version()):
            from temba.flows.models import FlowRevision
            data = FlowRevision.migrate_export(self, data, same_site, export_version)

        # we need to import flows first, they will resolve to
        # the appropriate ids and update our definition accordingly
        Flow.import_flows(data, self, user, same_site)
        Campaign.import_campaigns(data, self, user, same_site)
        Trigger.import_triggers(data, self, user, same_site)

    @classmethod
    def export_definitions(cls, site_link, components):
        from temba.campaigns.models import Campaign
        from temba.flows.models import Flow
        from temba.triggers.models import Trigger

        exported_flows = []
        exported_campaigns = []
        exported_triggers = []

        for component in components:
            if isinstance(component, Flow):
                component.ensure_current_version()  # only export current versions
                exported_flows.append(component.as_json(expand_contacts=True))
            elif isinstance(component, Campaign):
                exported_campaigns.append(component.as_json())
            elif isinstance(component, Trigger):
                exported_triggers.append(component.as_json())

        return dict(version=get_current_export_version(),
                    site=site_link,
                    flows=exported_flows,
                    campaigns=exported_campaigns,
                    triggers=exported_triggers)

    def can_add_sender(self):  # pragma: needs cover
        """
        If an org's telephone send channel is an Android device, let them add a bulk sender
        """
        from temba.contacts.models import TEL_SCHEME
        from temba.channels.models import Channel

        send_channel = self.get_send_channel(TEL_SCHEME)
        return send_channel and send_channel.channel_type == Channel.TYPE_ANDROID

    def can_add_caller(self):  # pragma: needs cover
        return not self.supports_ivr() and self.is_connected_to_twilio()

    def supports_ivr(self):
        return self.get_call_channel() or self.get_answer_channel()

    def get_channel(self, scheme, country_code, role):
        """
        Gets a channel for this org which supports the given scheme and role
        """
        from temba.channels.models import Channel

        channels = self.channels.filter(is_active=True, role__contains=role).order_by('-pk')

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

    @cached_property
    def cached_channels(self):
        channels = [c for c in self.channels.filter(is_active=True)]
        for ch in channels:
            ch.org = self

        return channels

    def clear_cached_channels(self):
        if 'cached_channels' in self.__dict__:
            del self.__dict__['cached_channels']
        self.clear_cached_schemes()

    def get_channel_for_role(self, role, scheme=None, contact_urn=None, country_code=None):
        from temba.contacts.models import TEL_SCHEME
        from temba.channels.models import Channel
        from temba.contacts.models import ContactURN

        if contact_urn:
            if contact_urn:
                scheme = contact_urn.scheme

                # if URN has a previously used channel that is still active, use that
                if contact_urn.channel and contact_urn.channel.is_active:
                    previous_sender = self.get_channel_delegate(contact_urn.channel, role)
                    if previous_sender:
                        return previous_sender

            if scheme == TEL_SCHEME:
                path = contact_urn.path

                # we don't have a channel for this contact yet, let's try to pick one from the same carrier
                # we need at least one digit to overlap to infer a channel
                contact_number = path.strip('+')
                prefix = 1
                channel = None

                # try to use only a channel in the same country
                if not country_code:
                    country_code = ContactURN.derive_country_from_tel(path)

                channels = []
                if country_code:
                    for c in self.cached_channels:
                        if c.country == country_code:
                            channels.append(c)

                # no country specific channel, try to find any channel at all
                if not channels:
                    channels = [c for c in self.cached_channels if TEL_SCHEME in c.schemes]

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
                            channel_prefixes = [sender.address.strip('+')]

                        for chan_prefix in channel_prefixes:
                            for idx in range(prefix, len(chan_prefix)):
                                if idx >= prefix and chan_prefix[0:idx] == contact_number[0:idx]:
                                    prefix = idx
                                    channel = sender
                                else:
                                    break
                elif senders:
                    channel = senders[0]

                if channel:
                    if role == Channel.ROLE_SEND:
                        return self.get_channel_delegate(channel, Channel.ROLE_SEND)
                    else:
                        return channel

        # get any send channel without any country or URN hints
        return self.get_channel(scheme, country_code, role)

    def get_send_channel(self, scheme=None, contact_urn=None, country_code=None):
        from temba.channels.models import Channel
        return self.get_channel_for_role(Channel.ROLE_SEND, scheme=scheme, contact_urn=contact_urn, country_code=country_code)

    def get_ussd_channel(self, contact_urn=None, country_code=None):
        from temba.contacts.models import TEL_SCHEME
        from temba.channels.models import Channel
        return self.get_channel_for_role(Channel.ROLE_USSD, scheme=TEL_SCHEME, contact_urn=contact_urn, country_code=country_code)

    def get_receive_channel(self, scheme, contact_urn=None, country_code=None):
        from temba.channels.models import Channel
        return self.get_channel_for_role(Channel.ROLE_RECEIVE, scheme=scheme, contact_urn=contact_urn, country_code=country_code)

    def get_call_channel(self, contact_urn=None, country_code=None):
        from temba.contacts.models import TEL_SCHEME
        from temba.channels.models import Channel
        return self.get_channel_for_role(Channel.ROLE_CALL, scheme=TEL_SCHEME, contact_urn=contact_urn, country_code=country_code)

    def get_answer_channel(self, contact_urn=None, country_code=None):
        from temba.contacts.models import TEL_SCHEME
        from temba.channels.models import Channel
        return self.get_channel_for_role(Channel.ROLE_ANSWER, scheme=TEL_SCHEME, contact_urn=contact_urn, country_code=country_code)

    def get_ussd_channels(self):
        from temba.channels.models import ChannelType, Channel
        return Channel.get_by_category(self, ChannelType.Category.USSD)

    def get_channel_delegate(self, channel, role):
        """
        Gets a channel's delegate for the given role with caching on the org object
        """
        cache_attr = '__%d__delegate_%s' % (channel.id, role)
        if hasattr(self, cache_attr):
            return getattr(self, cache_attr)

        delegate = channel.get_delegate(role)
        setattr(self, cache_attr, delegate)
        return delegate

    def get_schemes(self, role):
        """
        Gets all URN schemes which this org has org has channels configured for
        """
        cache_attr = '__schemes__%s' % role
        if hasattr(self, cache_attr):
            return getattr(self, cache_attr)

        schemes = set()
        for channel in self.channels.filter(is_active=True, role__contains=role):
            for scheme in channel.schemes:
                schemes.add(scheme)

        setattr(self, cache_attr, schemes)
        return schemes

    def clear_cached_schemes(self):
        from temba.channels.models import Channel
        for role in [Channel.ROLE_SEND, Channel.ROLE_RECEIVE, Channel.ROLE_ANSWER, Channel.ROLE_CALL, Channel.ROLE_USSD]:
            cache_attr = '__schemes__%s' % role
            if hasattr(self, cache_attr):
                delattr(self, cache_attr)

    def normalize_contact_tels(self):
        """
        Attempts to normalize any contacts which don't have full e164 phone numbers
        """
        from temba.contacts.models import ContactURN, TEL_SCHEME

        # do we have an org-level country code? if so, try to normalize any numbers not starting with +
        country_code = self.get_country_code()
        if country_code:
            urns = ContactURN.objects.filter(org=self, scheme=TEL_SCHEME).exclude(path__startswith="+")
            for urn in urns:
                urn.ensure_number_normalization(country_code)

    def get_resthooks(self):
        """
        Returns the resthooks configured on this Org
        """
        return self.resthooks.filter(is_active=True).order_by('slug')

    def get_webhook_url(self):
        """
        Returns a string with webhook url.
        """
        return self.webhook.get('url') if self.webhook else None

    def get_webhook_headers(self):
        """
        Returns a dictionary of any webhook headers, e.g.:
        {'Authorization': 'Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ==',
         'X-My-Special-Header': 'woo'}
        """
        return self.webhook.get('headers', {})

    def get_channel_countries(self):
        channel_countries = []

        if not self.is_connected_to_transferto():
            return channel_countries

        channel_country_codes = self.channels.filter(is_active=True).exclude(country=None)
        channel_country_codes = set(channel_country_codes.values_list('country', flat=True))

        for country_code in channel_country_codes:
            country_obj = pycountry.countries.get(alpha_2=country_code)
            country_name = country_obj.name
            currency = currency_for_country(country_code)
            channel_countries.append(dict(code=country_code, name=country_name, currency_code=currency.alpha_3,
                                          currency_name=currency.name))

        return sorted(channel_countries, key=lambda k: k['name'])

    @classmethod
    def get_possible_countries(cls):
        return AdminBoundary.objects.filter(level=0).order_by('name')

    def trigger_send(self, msgs=None):
        """
        Triggers either our Android channels to sync, or for all our pending messages to be queued
        to send.
        """
        from temba.msgs.models import Msg
        from temba.channels.models import Channel

        # if we have msgs, then send just those
        if msgs is not None:
            ids = [m.id for m in msgs]

            # trigger syncs for our android channels
            for channel in self.channels.filter(is_active=True, channel_type=Channel.TYPE_ANDROID, msgs__id__in=ids):
                channel.trigger_sync()

            # and send those messages
            Msg.send_messages(msgs)

        # otherwise, sync all pending messages and channels
        else:
            for channel in self.channels.filter(is_active=True, channel_type=Channel.TYPE_ANDROID):  # pragma: needs cover
                channel.trigger_sync()

            # otherwise, send any pending messages on our channels
            r = get_redis_connection()

            key = 'trigger_send_%d' % self.pk

            # only try to send all pending messages if nobody is doing so already
            if not r.get(key):
                with r.lock(key, timeout=900):
                    pending = Channel.get_pending_messages(self)
                    Msg.send_messages(pending)

    def add_smtp_config(self, from_email, host, username, password, port, encryption, user):
        smtp_config = {SMTP_FROM_EMAIL: from_email.strip(),
                       SMTP_HOST: host, SMTP_USERNAME: username, SMTP_PASSWORD: password,
                       SMTP_PORT: port, SMTP_ENCRYPTION: encryption}

        config = self.config
        config.update(smtp_config)
        self.config = config
        self.modified_by = user
        self.save()

    def remove_smtp_config(self, user):
        if self.config:

            self.config.pop(SMTP_FROM_EMAIL)
            self.config.pop(SMTP_HOST)
            self.config.pop(SMTP_USERNAME)
            self.config.pop(SMTP_PASSWORD)
            self.config.pop(SMTP_PORT)
            self.config.pop(SMTP_ENCRYPTION)
            self.modified_by = user
            self.save()

    def has_smtp_config(self):
        if self.config:
            smtp_from_email = self.config.get(SMTP_FROM_EMAIL, None)
            smtp_host = self.config.get(SMTP_HOST, None)
            smtp_username = self.config.get(SMTP_USERNAME, None)
            smtp_password = self.config.get(SMTP_PASSWORD, None)
            smtp_port = self.config.get(SMTP_PORT, None)

            return smtp_from_email and smtp_host and smtp_username and smtp_password and smtp_port
        else:
            return False

    def email_action_send(self, recipients, subject, body):
        if self.has_smtp_config():
            smtp_from_email = self.config.get(SMTP_FROM_EMAIL, None)
            smtp_host = self.config.get(SMTP_HOST, None)
            smtp_port = self.config.get(SMTP_PORT, None)
            smtp_username = self.config.get(SMTP_USERNAME, None)
            smtp_password = self.config.get(SMTP_PASSWORD, None)
            use_tls = self.config.get(SMTP_ENCRYPTION, None) == 'T' or None

            send_custom_smtp_email(recipients, subject, body, smtp_from_email,
                                   smtp_host, smtp_port, smtp_username, smtp_password,
                                   use_tls)
        else:
            from_email = self.get_branding().get('flow_email', settings.FLOW_FROM_EMAIL)
            send_simple_email(recipients, subject, body, from_email=from_email)

    def has_airtime_transfers(self):
        from temba.airtime.models import AirtimeTransfer
        return AirtimeTransfer.objects.filter(org=self).exists()

    def connect_transferto(self, account_login, airtime_api_token, user):
        transferto_config = {TRANSFERTO_ACCOUNT_LOGIN: account_login.strip(),
                             TRANSFERTO_AIRTIME_API_TOKEN: airtime_api_token.strip()}

        config = self.config
        config.update(transferto_config)
        self.config = config
        self.modified_by = user
        self.save()

    def refresh_transferto_account_currency(self):
        config = self.config
        account_login = config.get(TRANSFERTO_ACCOUNT_LOGIN, None)
        airtime_api_token = config.get(TRANSFERTO_AIRTIME_API_TOKEN, None)

        from temba.airtime.models import AirtimeTransfer
        response = AirtimeTransfer.post_transferto_api_response(account_login, airtime_api_token,
                                                                action='check_wallet')
        parsed_response = AirtimeTransfer.parse_transferto_response(response.text)
        account_currency = parsed_response.get('currency', '')
        config.update({TRANSFERTO_ACCOUNT_CURRENCY: account_currency})
        self.config = config
        self.save()

    def is_connected_to_transferto(self):
        if self.config:
            transferto_account_login = self.config.get(TRANSFERTO_ACCOUNT_LOGIN, None)
            transferto_airtime_api_token = self.config.get(TRANSFERTO_AIRTIME_API_TOKEN, None)

            return transferto_account_login and transferto_airtime_api_token
        else:
            return False

    def remove_transferto_account(self, user):
        if self.config:
            self.config[TRANSFERTO_ACCOUNT_LOGIN] = ''
            self.config[TRANSFERTO_AIRTIME_API_TOKEN] = ''
            self.config[TRANSFERTO_ACCOUNT_CURRENCY] = ''
            self.modified_by = user
            self.save()

    def connect_nexmo(self, api_key, api_secret, user):
        from nexmo import Client as NexmoClient

        nexmo_uuid = str(uuid4())
        nexmo_config = {NEXMO_KEY: api_key.strip(), NEXMO_SECRET: api_secret.strip(), NEXMO_UUID: nexmo_uuid}
        client = NexmoClient(key=nexmo_config[NEXMO_KEY], secret=nexmo_config[NEXMO_SECRET])
        domain = self.get_brand_domain()

        app_name = "%s/%s" % (domain, nexmo_uuid)

        answer_url = "https://%s%s" % (domain, reverse('handlers.nexmo_call_handler', args=['answer', nexmo_uuid]))

        event_url = "https://%s%s" % (domain, reverse('handlers.nexmo_call_handler', args=['event', nexmo_uuid]))

        params = dict(name=app_name, type='voice', answer_url=answer_url, answer_method='POST',
                      event_url=event_url, event_method='POST')

        response = client.create_application(params=params)
        app_id = response.get('id', None)
        private_key = response.get("keys", dict()).get("private_key", None)

        nexmo_config[NEXMO_APP_ID] = app_id
        nexmo_config[NEXMO_APP_PRIVATE_KEY] = private_key

        config = self.config
        config.update(nexmo_config)
        self.config = config
        self.modified_by = user
        self.save()

        # clear all our channel configurations
        self.clear_channel_caches()

    def nexmo_uuid(self):
        config = self.config
        return config.get(NEXMO_UUID, None)

    def connect_twilio(self, account_sid, account_token, user):
        twilio_config = {ACCOUNT_SID: account_sid, ACCOUNT_TOKEN: account_token}

        config = self.config
        config.update(twilio_config)
        self.config = config
        self.modified_by = user
        self.save()

        # clear all our channel configurations
        self.clear_channel_caches()

    def is_connected_to_nexmo(self):
        if self.config:
            nexmo_key = self.config.get(NEXMO_KEY, None)
            nexmo_secret = self.config.get(NEXMO_SECRET, None)
            nexmo_uuid = self.config.get(NEXMO_UUID, None)

            return nexmo_key and nexmo_secret and nexmo_uuid
        else:
            return False

    def is_connected_to_twilio(self):
        if self.config:
            account_sid = self.config.get(ACCOUNT_SID, None)
            account_token = self.config.get(ACCOUNT_TOKEN, None)
            if account_sid and account_token:
                return True
        return False

    def remove_nexmo_account(self, user):
        if self.config:
            # release any nexmo channels
            for channel in self.channels.filter(is_active=True, channel_type='NX'):  # pragma: needs cover
                channel.release()

            self.config[NEXMO_KEY] = ''
            self.config[NEXMO_SECRET] = ''
            self.modified_by = user
            self.save()

            # clear all our channel configurations
            self.clear_channel_caches()

    def remove_twilio_account(self, user):
        if self.config:
            # release any twilio and twilio messaging sevice channels
            for channel in self.channels.filter(is_active=True, channel_type__in=['T', 'TMS']):
                channel.release()

            self.config[ACCOUNT_SID] = ''
            self.config[ACCOUNT_TOKEN] = ''
            self.config[APPLICATION_SID] = ''
            self.modified_by = user
            self.save()

            # clear all our channel configurations
            self.clear_channel_caches()

    def connect_chatbase(self, agent_name, api_key, version, user):
        chatbase_config = {
            CHATBASE_AGENT_NAME: agent_name,
            CHATBASE_API_KEY: api_key,
            CHATBASE_VERSION: version
        }

        config = self.config
        config.update(chatbase_config)
        self.config = config
        self.modified_by = user
        self.save()

    def remove_chatbase_account(self, user):
        config = self.config

        if CHATBASE_AGENT_NAME in config:
            del config[CHATBASE_AGENT_NAME]

        if CHATBASE_API_KEY in config:
            del config[CHATBASE_API_KEY]

        if CHATBASE_VERSION in config:
            del config[CHATBASE_VERSION]

        self.config = config
        self.modified_by = user
        self.save()

    def get_chatbase_credentials(self):
        if self.config:
            chatbase_api_key = self.config.get(CHATBASE_API_KEY, None)
            chatbase_version = self.config.get(CHATBASE_VERSION, None)
            return chatbase_api_key, chatbase_version
        else:
            return None, None

    def get_verboice_client(self):  # pragma: needs cover
        from temba.ivr.clients import VerboiceClient
        channel = self.get_call_channel()
        if channel.channel_type == 'VB':
            return VerboiceClient(channel)
        return None

    def get_twilio_client(self):
        from temba.ivr.clients import TwilioClient

        if self.config:
            account_sid = self.config.get(ACCOUNT_SID, None)
            auth_token = self.config.get(ACCOUNT_TOKEN, None)
            if account_sid and auth_token:
                return TwilioClient(account_sid, auth_token, org=self)
        return None

    def get_nexmo_client(self):
        from temba.ivr.clients import NexmoClient

        if self.config:
            api_key = self.config.get(NEXMO_KEY, None)
            api_secret = self.config.get(NEXMO_SECRET, None)
            app_id = self.config.get(NEXMO_APP_ID, None)
            app_private_key = self.config.get(NEXMO_APP_PRIVATE_KEY, None)
            if api_key and api_secret:
                return NexmoClient(api_key, api_secret, app_id, app_private_key, org=self)

        return None

    def clear_channel_caches(self):
        """
        Clears any cached configurations we have for any of our channels.
        """
        from temba.channels.models import Channel
        for channel in self.channels.exclude(channel_type='A'):
            Channel.clear_cached_channel(channel.pk)

    def get_country_code(self):
        """
        Gets the 2-digit country code, e.g. RW, US
        """
        return get_cacheable_attr(self, '_country_code', lambda: self.calculate_country_code())

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
        countries = self.channels.filter(is_active=True).exclude(country=None).order_by('country')
        countries = countries.distinct('country').values_list('country', flat=True)
        if len(countries) == 1:
            return countries[0]

        return None

    def get_language_codes(self):
        return get_cacheable_attr(self, '_language_codes', lambda: {l.iso_code for l in self.languages.all()})

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
                self.save(update_fields=('primary_language',))

        # unset the primary language if not in the new list of codes
        if self.primary_language and self.primary_language.iso_code not in iso_codes:
            self.primary_language = None
            self.save(update_fields=('primary_language',))

        # remove any languages that are not in the new list
        self.languages.exclude(iso_code__in=iso_codes).delete()

        if hasattr(self, '_language_codes'):  # invalidate language cache if set
            delattr(self, '_language_codes')

    def get_dayfirst(self):
        return self.date_format == DAYFIRST

    def format_date(self, datetime, show_time=True):
        """
        Formats a datetime with or without time using this org's date format
        """
        formats = get_datetime_format(self.get_dayfirst())
        format = formats[1] if show_time else formats[0]
        return datetime_to_str(datetime, format, False, self.timezone)

    def parse_date(self, date_string):
        if isinstance(date_string, datetime):
            return date_string

        return str_to_datetime(date_string, self.timezone, self.get_dayfirst())

    def parse_decimal(self, decimal_string):
        parsed = None

        try:
            parsed = Decimal(decimal_string)
            if not parsed.is_finite() or parsed > Decimal('999999999999999999999999'):
                parsed = None
        except Exception:
            pass

        return parsed

    def generate_location_query(self, name, level, is_alias=False):
        if is_alias:
            query = dict(name__iexact=name, boundary__level=level)
            query['__'.join(['boundary'] + ['parent'] * level)] = self.country
        else:
            query = dict(name__iexact=name, level=level)
            query['__'.join(['parent'] * level)] = self.country

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
                alias = BoundaryAlias.objects.filter(name__iexact=name, boundary__level=level,
                                                     boundary__parent=parent).first()
            else:
                query = self.generate_location_query(name, level, True)
                alias = BoundaryAlias.objects.filter(**query).first()

            if alias:
                boundary = [alias.boundary]

        return boundary

    def parse_location(self, location_string, level, parent=None):
        """
        Attempts to parse the passed in location string at the passed in level. This does various tokenizing
        of the string to try to find the best possible match.

        @returns Iterable of matching boundaries
        """
        # no country? bail
        if not self.country_id or not isinstance(location_string, six.string_types):
            return []

        # now look up the boundary by full name
        boundary = self.find_boundary_by_name(location_string, level, parent)

        if not boundary:
            # try removing punctuation and try that
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
                        bigram = " ".join(words[i:i + 2])
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
        return org_users.distinct().order_by('email')

    def latest_admin(self):
        admin = self.get_org_admins().last()

        # no admins? try editors
        if not admin:  # pragma: needs cover
            admin = self.get_org_editors().last()

        # no editors? try viewers
        if not admin:  # pragma: needs cover
            admin = self.get_org_viewers().last()

        return admin

    def is_free_plan(self):  # pragma: needs cover
        return self.plan == FREE_PLAN or self.plan == TRIAL_PLAN

    def is_import_flows_tier(self):
        return self.get_purchased_credits() >= self.get_branding().get('tiers', {}).get('import_flows', 0)

    def is_multi_user_tier(self):
        return self.get_purchased_credits() >= self.get_branding().get('tiers', {}).get('multi_user', 0)

    def is_multi_org_tier(self):
        return not self.parent and self.get_purchased_credits() >= self.get_branding().get('tiers', {}).get('multi_org', 0)

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

        return getattr(user, '_org_group', None)

    def has_twilio_number(self):  # pragma: needs cover
        return self.channels.filter(channel_type='T')

    def has_nexmo_number(self):  # pragma: needs cover
        return self.channels.filter(channel_type='NX')

    def create_welcome_topup(self, topup_size=None):
        if topup_size:
            return TopUp.create(self.created_by, price=0, credits=topup_size, org=self)
        return None

    def create_system_groups(self):
        """
        Creates our system groups for this organization so that we can keep track of counts etc..
        """
        from temba.contacts.models import ContactGroup

        self.all_groups.create(name='All Contacts', group_type=ContactGroup.TYPE_ALL,
                               created_by=self.created_by, modified_by=self.modified_by)
        self.all_groups.create(name='Blocked Contacts', group_type=ContactGroup.TYPE_BLOCKED,
                               created_by=self.created_by, modified_by=self.modified_by)
        self.all_groups.create(name='Stopped Contacts', group_type=ContactGroup.TYPE_STOPPED,
                               created_by=self.created_by, modified_by=self.modified_by)

    def create_sample_flows(self, api_url):
        import json

        # get our sample dir
        filename = os.path.join(settings.STATICFILES_DIRS[0], 'examples', 'sample_flows.json')

        # for each of our samples
        with open(filename, 'r') as example_file:
            example = example_file.read()

        user = self.get_user()
        if user:
            # some some substitutions
            org_example = example.replace("{{EMAIL}}", user.username)
            org_example = org_example.replace("{{API_URL}}", api_url)

            try:
                self.import_app(json.loads(org_example), user)
            except Exception:  # pragma: needs cover
                import traceback
                logger = logging.getLogger(__name__)
                msg = 'Failed creating sample flows'
                logger.error(msg, exc_info=True, extra=dict(definition=json.loads(org_example)))
                traceback.print_exc()

    def is_notified_of_mt_sms(self):
        return self.webhook_events & MT_SMS_EVENTS > 0

    def is_notified_of_mo_sms(self):
        return self.webhook_events & MO_SMS_EVENTS > 0

    def is_notified_of_mt_call(self):
        return self.webhook_events & MT_CALL_EVENTS > 0

    def is_notified_of_mo_call(self):
        return self.webhook_events & MO_CALL_EVENTS > 0

    def is_notified_of_alarms(self):
        return self.webhook_events & ALARM_EVENTS > 0

    def get_user(self):
        return self.administrators.filter(is_active=True).first()

    def is_nearing_expiration(self):
        """
        Determines if the org is nearing expiration
        """
        newest_topup = TopUp.objects.filter(org=self, is_active=True).order_by('-created_on').first()
        if newest_topup:
            if timezone.now() + timedelta(days=30) > newest_topup.expires_on:
                return newest_topup.get_remaining() > 0
        return False

    def has_low_credits(self):
        return self.get_credits_remaining() <= self.get_low_credits_threshold()

    def get_low_credits_threshold(self):
        """
        Get the credits number to consider as low threshold to this org
        """
        return get_cacheable_result(ORG_LOW_CREDIT_THRESHOLD_CACHE_KEY % self.pk,
                                    self._calculate_low_credits_threshold)

    def _calculate_low_credits_threshold(self):
        now = timezone.now()
        last_topup_credits = self.topups.filter(is_active=True, expires_on__gte=now).aggregate(Sum('credits')).get('credits__sum')
        return int(last_topup_credits * 0.15) if last_topup_credits else 0, self.get_credit_ttl()

    def get_credits_total(self, force_dirty=False):
        """
        Gets the total number of credits purchased or assigned to this org
        """
        return get_cacheable_result(ORG_CREDITS_TOTAL_CACHE_KEY % self.pk,
                                    self._calculate_credits_total, force_dirty=force_dirty)

    def get_purchased_credits(self):
        """
        Returns the total number of credits purchased
        :return:
        """
        return get_cacheable_result(ORG_CREDITS_PURCHASED_CACHE_KEY % self.pk, self._calculate_purchased_credits)

    def _calculate_purchased_credits(self):
        purchased_credits = self.topups.filter(is_active=True, price__gt=0).aggregate(Sum('credits')).get('credits__sum')
        return purchased_credits if purchased_credits else 0, self.get_credit_ttl()

    def _calculate_credits_total(self):
        active_credits = self.topups.filter(is_active=True, expires_on__gte=timezone.now()).aggregate(Sum('credits')).get('credits__sum')
        active_credits = active_credits if active_credits else 0

        # these are the credits that have been used in expired topups
        expired_credits = TopUpCredits.objects.filter(
            topup__org=self, topup__is_active=True, topup__expires_on__lte=timezone.now()
        ).aggregate(Sum('used')).get('used__sum')

        expired_credits = expired_credits if expired_credits else 0

        return active_credits + expired_credits, self.get_credit_ttl()

    def get_credits_used(self):
        """
        Gets the number of credits used by this org
        """
        return get_cacheable_result(ORG_CREDITS_USED_CACHE_KEY % self.pk, self._calculate_credits_used)

    def _calculate_credits_used(self):
        used_credits_sum = TopUpCredits.objects.filter(topup__org=self, topup__is_active=True)
        used_credits_sum = used_credits_sum.aggregate(Sum('used')).get('used__sum')
        used_credits_sum = used_credits_sum if used_credits_sum else 0

        # if we don't have an active topup, add up pending messages too
        if not self.get_active_topup_id():
            test_contacts = self.org_contacts.filter(is_test=True).values_list('id', flat=True)
            used_credits_sum += self.msgs.filter(topup=None).exclude(contact_id__in=test_contacts).count()

            # we don't cache in this case
            return used_credits_sum, 0

        return used_credits_sum, self.get_credit_ttl()

    def get_credits_remaining(self):
        """
        Gets the number of credits remaining for this org
        """
        return self.get_credits_total() - self.get_credits_used()

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
                        (topup_id, debited) = self.decrement_credit(amount)

                        if topup_id:
                            topup = TopUp.objects.get(id=topup_id)

                            # create the topup for our child, expiring on the same date
                            new_topup = TopUp.create(user, credits=debited, org=org, expires_on=topup.expires_on, price=None)

                            # create a debit for transaction history
                            Debit.objects.create(topup_id=topup_id, amount=debited, beneficiary=new_topup,
                                                 debit_type=Debit.TYPE_ALLOCATION, created_by=user)

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

    def decrement_credit(self, amount=1):
        """
        Decrements this orgs credit by amount.

        Determines the active topup and returns that along with how many credits we were able
        to decrement it by. Amount decremented is not guaranteed to be the full amount requested.
        """
        r = get_redis_connection()

        # we always consider this a credit 'used' since un-applied msgs are pending
        # credit expenses for the next purchased topup
        incrby_existing(ORG_CREDITS_USED_CACHE_KEY % self.id, amount)

        # if we have an active topup cache, we need to decrement the amount remaining
        active_topup_id = self.get_active_topup_id()
        if active_topup_id:

            remaining = r.decr(ORG_ACTIVE_TOPUP_REMAINING % (self.id, active_topup_id), amount)

            # near the edge, clear out our cache and calculate from the db
            if not remaining or int(remaining) < 100:
                active_topup_id = None
                self.clear_credit_cache()

        # calculate our active topup if we need to
        if not active_topup_id:
            active_topup = self.get_active_topup(force_dirty=True)
            if active_topup:
                active_topup_id = active_topup.id
                remaining = active_topup.get_remaining()
                if amount > remaining:
                    amount = remaining
                r.decr(ORG_ACTIVE_TOPUP_REMAINING % (self.id, active_topup.id), amount)

        if active_topup_id:
            return (active_topup_id, amount)

        return None, 0

    def get_active_topup(self, force_dirty=False):
        topup_id = self.get_active_topup_id(force_dirty=force_dirty)
        if topup_id:
            return TopUp.objects.get(id=topup_id)
        return None

    def get_active_topup_id(self, force_dirty=False):
        return get_cacheable_result(ORG_ACTIVE_TOPUP_KEY % self.pk, self._calculate_active_topup, force_dirty=force_dirty)

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
        non_expired_topups = self.topups.filter(is_active=True, expires_on__gte=timezone.now()).order_by('expires_on', 'id')
        active_topups = non_expired_topups.annotate(used_credits=Sum('topupcredits__used'))\
                                          .filter(credits__gt=0)\
                                          .filter(Q(used_credits__lt=F('credits')) | Q(used_credits=None))

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
            test_contacts = self.org_contacts.filter(is_test=True).values_list('id', flat=True)
            msg_uncredited = self.msgs.filter(topup=None).exclude(contact_id__in=test_contacts).order_by('created_on')
            all_uncredited = list(msg_uncredited)

            # get all topups that haven't expired
            unexpired_topups = list(self.topups.filter(is_active=True, expires_on__gte=timezone.now()).order_by('-expires_on'))

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
            for topup, items in six.iteritems(new_topup_items):
                msg_ids = [item.id for item in items if isinstance(item, Msg)]
                Msg.objects.filter(id__in=msg_ids).update(topup=topup)

        # deactive all our credit alerts
        CreditAlert.reset_for_org(self)

        # any time we've reapplied topups, lets invalidate our credit cache too
        self.clear_credit_cache()

    def current_plan_start(self):
        today = timezone.now().date()

        # move it to the same day our plan started (taking into account short months)
        plan_start = today.replace(day=min(self.plan_start.day, calendar.monthrange(today.year, today.month)[1]))

        if plan_start > today:  # pragma: needs cover
            plan_start -= relativedelta(months=1)

        return plan_start

    def current_plan_end(self):
        plan_start = self.current_plan_start()
        plan_end = plan_start + relativedelta(months=1)
        return plan_end

    def get_stripe_customer(self):  # pragma: no cover
        # We can't test stripe in unit tests since it requires javascript tokens to be generated
        if not self.stripe_customer:
            return None

        try:
            stripe.api_key = get_stripe_credentials()[1]
            customer = stripe.Customer.retrieve(self.stripe_customer)
            return customer
        except Exception:
            traceback.print_exc()
            return None

    def get_bundles(self):
        return get_brand_bundles(self.get_branding())

    @cached_property
    def cached_contact_fields(self):
        from temba.contacts.models import ContactField
        fields = ContactField.objects.filter(org=self, is_active=True)
        for field in fields:
            field.org = self
        return fields

    def clear_cached_groups(self):
        if '__cached_groups' in self.__dict__:
            del self.__dict__['__cached_groups']

    def get_group(self, uuid):
        cached_groups = self.__dict__.get('__cached_groups', {})
        existing = cached_groups.get(uuid, None)

        if existing:
            return existing

        from temba.contacts.models import ContactGroup
        existing = ContactGroup.user_groups.filter(org=self, uuid=uuid).first()
        if existing:
            cached_groups[uuid] = existing
            self.__dict__['__cached_groups'] = cached_groups
        return existing

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

        # for our purposes, #1 and #2 are treated the same, we just always update the default card

        try:
            if not customer or customer.email != user.email:
                # then go create a customer object for this user
                customer = stripe.Customer.create(card=token, email=user.email,
                                                  description="{ org: %d }" % self.pk)

                stripe_customer = customer.id
                self.stripe_customer = stripe_customer
                self.save()

            # update the stripe card to the one they just entered
            else:
                # remove existing cards
                # TODO: this is all a bit wonky because we are using the Stripe JS widget..
                # if we instead used on our mechanism to display / edit cards we could be a bit smarter
                existing_cards = [c for c in customer.cards.all().data]
                for card in existing_cards:
                    card.delete()

                try:
                    card = customer.cards.create(card=token)
                except stripe.CardError:
                    raise ValidationError(_("Sorry, your card was declined, please contact your provider or try another card."))

                customer.default_card = card.id
                customer.save()

                stripe_customer = customer.id

            charge = stripe.Charge.create(amount=bundle['cents'],
                                          currency='usd',
                                          customer=stripe_customer,
                                          description=bundle['description'])

            remaining = self.get_credits_remaining()

            # create our top up
            topup = TopUp.create(user, price=bundle['cents'], credits=bundle['credits'],
                                 stripe_charge=charge.id, org=self)

            context = dict(description=bundle['description'],
                           charge_id=charge.id,
                           charge_date=timezone.now().strftime("%b %e, %Y"),
                           amount=bundle['dollars'],
                           credits=bundle['credits'],
                           remaining=remaining,
                           org=self.name)

            # card
            if getattr(charge, 'card', None):
                context['cc_last4'] = charge.card.last4
                context['cc_type'] = charge.card.type
                context['cc_name'] = charge.card.name

            # bitcoin
            else:
                context['cc_type'] = 'bitcoin'
                context['cc_name'] = charge.source.bitcoin.address

            branding = self.get_branding()

            subject = _("%(name)s Receipt") % branding
            template = "orgs/email/receipt_email"
            to_email = user.email

            context['customer'] = user
            context['branding'] = branding
            context['subject'] = subject

            send_template_email(to_email, subject, template, context, branding)

            # apply our new topups
            from .tasks import apply_topups_task
            apply_topups_task.delay(self.id)

            return topup

        except ValidationError as e:
            raise e

        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error("Error adding credits to org", exc_info=True)
            raise ValidationError(_("Sorry, we were unable to process your payment, please try again later or contact us."))

    def account_value(self):
        """
        How much has this org paid to date in dollars?
        """
        paid = TopUp.objects.filter(org=self).aggregate(paid=Sum('price'))['paid']
        if not paid:
            paid = 0
        return paid / 100

    def update_plan(self, new_plan, token, user):  # pragma: no cover
        # We can't test stripe in unit tests since it requires javascript tokens to be generated
        stripe.api_key = get_stripe_credentials()[1]

        # no plan change?  do nothing
        if new_plan == self.plan:
            return None

        # this is our stripe customer id
        stripe_customer = None

        # our actual customer object
        customer = self.get_stripe_customer()
        if customer:
            stripe_customer = customer.id

        # cancel our plan on our stripe customer
        if new_plan == FREE_PLAN:
            if customer:
                analytics.track(user.username, 'temba.plan_cancelled', dict(cancelledPlan=self.plan))

                try:
                    subscription = customer.cancel_subscription(at_period_end=True)
                except Exception:
                    traceback.print_exc()
                    raise ValidationError(_("Sorry, we are unable to cancel your plan at this time.  Please contact us."))
            else:
                raise ValidationError(_("Sorry, we are unable to cancel your plan at this time.  Please contact us."))

        else:
            # we have a customer, try to upgrade them
            if customer:
                try:
                    subscription = customer.update_subscription(plan=new_plan)

                    analytics.track(user.username, 'temba.plan_upgraded', dict(previousPlan=self.plan, plan=new_plan))

                except Exception:
                    # can't load it, oh well, we'll try to create one dynamically below
                    traceback.print_exc()
                    customer = None

            # if we don't have a customer, go create one
            if not customer:
                try:
                    # then go create a customer object for this user
                    customer = stripe.Customer.create(card=token, plan=new_plan, email=user,
                                                      description="{ org: %d }" % self.pk)

                    stripe_customer = customer.id
                    subscription = customer['subscription']

                    analytics.track(user.username, 'temba.plan_upgraded', dict(previousPlan=self.plan, plan=new_plan))

                except Exception:
                    traceback.print_exc()
                    raise ValidationError(_("Sorry, we were unable to charge your card, please try again later or contact us."))

        # update our org
        self.stripe_customer = stripe_customer

        if subscription['status'] != 'active':
            self.plan = FREE_PLAN
        else:
            self.plan = new_plan

        self.plan_start = datetime.fromtimestamp(subscription['start'])
        self.save()

        return subscription

    def generate_dependency_graph(self, include_campaigns=True, include_triggers=False, include_archived=False):
        """
        Generates a dict of all exportable flows and campaigns for this org with each object's immediate dependencies
        """
        from temba.campaigns.models import Campaign, CampaignEvent
        from temba.contacts.models import ContactGroup
        from temba.flows.models import Flow

        flow_prefetches = ('action_sets', 'rule_sets')
        campaign_prefetches = (
            Prefetch('events', queryset=CampaignEvent.objects.filter(is_active=True).exclude(flow__flow_type=Flow.MESSAGE), to_attr='flow_events'),
            'flow_events__flow'
        )

        all_flows = self.flows.filter(is_active=True).exclude(flow_type=Flow.MESSAGE).prefetch_related(*flow_prefetches)
        all_flow_map = {f.uuid: f for f in all_flows}

        if include_campaigns:
            all_campaigns = self.campaign_set.filter(is_active=True).select_related('group').prefetch_related(*campaign_prefetches)
        else:
            all_campaigns = Campaign.objects.none()

        if not include_archived:
            all_flows = all_flows.filter(is_archived=False)
            all_campaigns = all_campaigns.filter(is_archived=False)

        # build dependency graph for all flows and campaigns
        dependencies = defaultdict(set)
        for flow in all_flows:
            dependencies[flow] = flow.get_dependencies(all_flow_map)
        for campaign in all_campaigns:
            dependencies[campaign] = set([e.flow for e in campaign.flow_events])

        # replace any dependency on a group with that group's associated campaigns - we're not actually interested
        # in flow-group-flow relationships - only relationships that go through a campaign
        campaigns_by_group = defaultdict(list)
        if include_campaigns:
            for campaign in self.campaign_set.filter(is_active=True).select_related('group'):
                campaigns_by_group[campaign.group].append(campaign)

        for c, deps in six.iteritems(dependencies):
            if isinstance(c, Flow):
                for d in list(deps):
                    if isinstance(d, ContactGroup):
                        deps.remove(d)
                        deps.update(campaigns_by_group[d])

        if include_triggers:
            all_triggers = self.trigger_set.filter(is_archived=False, is_active=True).select_related('flow')
            for trigger in all_triggers:
                dependencies[trigger] = {trigger.flow}

        # make dependencies symmetric, i.e. if A depends on B, B depends on A
        for c, deps in six.iteritems(dependencies.copy()):
            for d in deps:
                dependencies[d].add(c)

        return dependencies

    def resolve_dependencies(self, flows, campaigns, include_campaigns=True, include_triggers=False, include_archived=False):
        """
        Given a set of flows and and a set of campaigns, returns a new set including all dependencies
        """
        dependencies = self.generate_dependency_graph(include_campaigns=include_campaigns,
                                                      include_triggers=include_triggers,
                                                      include_archived=include_archived)

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

        if not branding:
            branding = BrandingMiddleware.get_branding_for_host('')

        self.create_system_groups()
        self.create_sample_flows(branding.get('api_link', ""))
        self.create_welcome_topup(topup_size)

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
                    path_pieces = url_parts.path.rsplit('.')
                    if len(path_pieces) > 1:
                        extension = path_pieces[-1]

        else:
            raise Exception("Received non-200 response (%s) for request: %s" % (response.status_code, response.content))

        return self.save_media(File(temp), extension)

    def save_response_media(self, response):
        disposition = response.headers.get('Content-Disposition', None)
        content_type = response.headers.get('Content-Type', None)

        downloaded = None

        if content_type:
            extension = None
            if disposition == 'inline':
                extension = mimetypes.guess_extension(content_type)
                extension = extension.strip('.')
            elif disposition:
                filename = re.findall("filename=\"(.+)\"", disposition)[0]
                extension = filename.rpartition('.')[2]
            elif content_type == 'audio/x-wav':
                extension = 'wav'

            temp = NamedTemporaryFile(delete=True)
            temp.write(response.content)
            temp.flush()

            # save our file off
            downloaded = self.save_media(File(temp), extension)

        return content_type, downloaded

    def save_media(self, file, extension):
        """
        Saves the given file data with the extension and returns an absolute url to the result
        """
        random_file = str(uuid4())
        random_dir = random_file[0:4]

        filename = '%s/%s' % (random_dir, random_file)
        if extension:
            filename = '%s.%s' % (filename, extension)

        path = '%s/%d/media/%s' % (settings.STORAGE_ROOT_DIR, self.pk, filename)
        location = default_storage.save(path, file)

        # force http for localhost
        scheme = 'https'
        if 'localhost' in settings.AWS_BUCKET_DOMAIN:  # pragma: no cover
            scheme = 'http'

        return "%s://%s/%s" % (scheme, settings.AWS_BUCKET_DOMAIN, location)

    @classmethod
    def create_user(cls, email, password):
        user = User.objects.create_user(username=email, email=email, password=password)
        return user

    @classmethod
    def get_org(cls, user):
        if not user:  # pragma: needs cover
            return None

        if not hasattr(user, '_org'):
            org = Org.objects.filter(administrators=user, is_active=True).first()
            if org:
                user._org = org

        return getattr(user, '_org', None)

    def __str__(self):
        return self.name


# ===================== monkey patch User class with a few extra functions ========================

def get_user_orgs(user, brand=None):
    if not brand:
        org = Org.get_org(user)
        brand = org.brand if org else settings.DEFAULT_BRAND

    if user.is_superuser:
        return Org.objects.all()

    user_orgs = user.org_admins.all() | user.org_editors.all() | user.org_viewers.all() | user.org_surveyors.all()
    return user_orgs.filter(brand=brand, is_active=True).distinct().order_by('name')


def get_org(obj):
    return getattr(obj, '_org', None)


def is_alpha_user(user):  # pragma: needs cover
    return user.groups.filter(name='Alpha')


def is_beta_user(user):  # pragma: needs cover
    return user.groups.filter(name='Beta')


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

    if user.is_anonymous():  # pragma: needs cover
        return False

    # has it innately? (customer support)
    if user.has_perm(permission):  # pragma: needs cover
        return True

    org_group = org.get_user_org_group(user)

    if not org_group:  # pragma: needs cover
        return False

    (app_label, codename) = permission.split(".")

    return org_group.permissions.filter(content_type__app_label=app_label, codename=codename).exists()


User.get_org = get_org
User.set_org = set_org
User.is_alpha = is_alpha_user
User.is_beta = is_beta_user
User.get_settings = get_settings
User.get_user_orgs = get_user_orgs
User.get_org_group = get_org_group
User.has_org_perm = _user_has_org_perm


USER_GROUPS = (('A', _("Administrator")),
               ('E', _("Editor")),
               ('V', _("Viewer")),
               ('S', _("Surveyor")))


def get_stripe_credentials():
    public_key = os.environ.get('STRIPE_PUBLIC_KEY', getattr(settings, 'STRIPE_PUBLIC_KEY', 'MISSING_STRIPE_PUBLIC_KEY'))
    private_key = os.environ.get('STRIPE_PRIVATE_KEY', getattr(settings, 'STRIPE_PRIVATE_KEY', 'MISSING_STRIPE_PRIVATE_KEY'))
    return (public_key, private_key)


@six.python_2_unicode_compatible
class Language(SmartModel):
    """
    A Language that has been added to the org. In the end and language is just an iso_code and name
    and it is not really restricted to real-world languages at this level. Instead we restrict the
    language selection options to real-world languages.
    """
    name = models.CharField(max_length=128)

    iso_code = models.CharField(max_length=4)

    org = models.ForeignKey(Org, verbose_name=_("Org"), related_name="languages")

    @classmethod
    def create(cls, org, user, name, iso_code):
        return cls.objects.create(org=org, name=name, iso_code=iso_code, created_by=user, modified_by=user)

    def as_json(self):  # pragma: needs cover
        return dict(name=self.name, iso_code=self.iso_code)

    @classmethod
    def get_localized_text(cls, text_translations, preferred_languages, default_text=None):
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
        if not isinstance(text_translations, dict):
            return text_translations

        # otherwise, find the first preferred language
        for lang in preferred_languages:
            localized = text_translations.get(lang)
            if localized is not None:
                return localized

        return default_text

    def __str__(self):  # pragma: needs cover
        return '%s' % self.name


class Invitation(SmartModel):
    """
    An Invitation to an e-mail address to join an Org with specific roles.
    """
    org = models.ForeignKey(Org, verbose_name=_("Org"), related_name="invitations",
                            help_text=_("The organization to which the account is invited to view"))

    email = models.EmailField(verbose_name=_("Email"), help_text=_("The email to which we send the invitation of the viewer"))

    secret = models.CharField(verbose_name=_("Secret"), max_length=64, unique=True,
                              help_text=_("a unique code associated with this invitation"))

    user_group = models.CharField(max_length=1, choices=USER_GROUPS, default='V', verbose_name=_("User Role"))

    @classmethod
    def create(cls, org, user, email, user_group):
        return cls.objects.create(org=org, email=email, user_group=user_group,
                                  created_by=user, modified_by=user)

    def save(self, *args, **kwargs):
        if not self.secret:
            secret = random_string(64)

            while Invitation.objects.filter(secret=secret):  # pragma: needs cover
                secret = random_string(64)

            self.secret = secret

        return super(Invitation, self).save(*args, **kwargs)

    @classmethod
    def generate_random_string(cls, length):  # pragma: needs cover
        """
        Generates a [length] characters alpha numeric secret
        """
        letters = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # avoid things that could be mistaken ex: 'I' and '1'
        return ''.join([random.choice(letters) for _ in range(length)])

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
        context['subject'] = subject

        send_template_email(to_email, subject, template, context, branding)


class UserSettings(models.Model):
    """
    User specific configuration
    """
    user = models.ForeignKey(User, related_name='settings')
    language = models.CharField(max_length=8, choices=settings.LANGUAGES, default="en-us",
                                help_text=_('Your preferred language'))
    tel = models.CharField(verbose_name=_("Phone Number"), max_length=16, null=True, blank=True,
                           help_text=_("Phone number for testing and recording voice flows"))

    def get_tel_formatted(self):
        if self.tel:
            import phonenumbers
            normalized = phonenumbers.parse(self.tel, None)
            return phonenumbers.format_number(normalized, phonenumbers.PhoneNumberFormat.INTERNATIONAL)


@six.python_2_unicode_compatible
class TopUp(SmartModel):
    """
    TopUps are used to track usage across the platform. Each TopUp represents a certain number of
    credits that can be consumed by messages.
    """
    org = models.ForeignKey(Org, related_name='topups',
                            help_text="The organization that was toppped up")
    price = models.IntegerField(null=True, blank=True, verbose_name=_("Price Paid"),
                                help_text=_("The price paid for the messages in this top up (in cents)"))
    credits = models.IntegerField(verbose_name=_("Number of Credits"),
                                  help_text=_("The number of credits bought in this top up"))
    expires_on = models.DateTimeField(verbose_name=_("Expiration Date"),
                                      help_text=_("The date that this top up will expire"))
    stripe_charge = models.CharField(verbose_name=_("Stripe Charge Id"), max_length=32, null=True, blank=True,
                                     help_text=_("The Stripe charge id for this charge"))
    comment = models.CharField(max_length=255, null=True, blank=True,
                               help_text="Any comment associated with this topup, used when we credit accounts")

    @classmethod
    def create(cls, user, price, credits, stripe_charge=None, org=None, expires_on=None):
        """
        Creates a new topup
        """
        if not org:
            org = user.get_org()

        if not expires_on:
            expires_on = timezone.now() + timedelta(days=365)  # credits last 1 year

        topup = TopUp.objects.create(org=org, price=price, credits=credits, expires_on=expires_on,
                                     stripe_charge=stripe_charge, created_by=user, modified_by=user)

        org.clear_credit_cache()
        return topup

    def get_ledger(self):  # pragma: needs cover
        debits = self.debits.filter(debit_type=Debit.TYPE_ALLOCATION).order_by('-created_by')
        balance = self.credits
        ledger = []

        active = self.get_remaining() < balance

        if active:
            transfer = self.allocations.all().first()

            if transfer:
                comment = _('Transfer from %s' % transfer.topup.org.name)
            else:
                if self.price > 0:
                    comment = _('Purchased Credits')
                elif self.price == 0:
                    comment = _('Complimentary Credits')
                else:
                    comment = _('Credits')

            ledger.append(dict(date=self.created_on,
                               comment=comment,
                               amount=self.credits,
                               balance=self.credits))

        for debit in debits:  # pragma: needs cover
            balance -= debit.amount
            ledger.append(dict(date=debit.created_on,
                          comment=_('Transfer to %(org)s') % dict(org=debit.beneficiary.org.name),
                          amount=-debit.amount,
                          balance=balance))

        now = timezone.now()
        expired = self.expires_on < now

        # add a line for used message credits
        if active:
            ledger.append(dict(date=self.expires_on if expired else now,
                               comment=_('Messaging credits used'),
                               amount=self.get_remaining() - balance,
                               balance=self.get_remaining()))

        # add a line for expired credits
        if expired and self.get_remaining() > 0:
            ledger.append(dict(date=self.expires_on,
                               comment=_('Expired credits'),
                               amount=-self.get_remaining(),
                               balance=0))
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
        except Exception:
            traceback.print_exc()
            return None

    def get_used(self):
        """
        Calculates how many topups have actually been used
        """
        used = TopUpCredits.objects.filter(topup=self).aggregate(used=Sum('used'))
        return 0 if not used['used'] else used['used']

    def get_remaining(self):
        """
        Returns how many credits remain on this topup
        """
        return self.credits - self.get_used()

    def __str__(self):  # pragma: needs cover
        return "%s Credits" % self.credits


class Debit(SquashableModel):
    """
    Transactional history of credits allocated to other topups or chunks of archived messages
    """
    SQUASH_OVER = ('topup_id',)

    TYPE_ALLOCATION = 'A'
    TYPE_PURGE = 'P'

    DEBIT_TYPES = ((TYPE_ALLOCATION, 'Allocation'),
                   (TYPE_PURGE, 'Purge'))

    topup = models.ForeignKey(TopUp, related_name="debits", help_text=_("The topup these credits are applied against"))

    amount = models.IntegerField(help_text=_('How many credits were debited'))

    beneficiary = models.ForeignKey(TopUp, null=True,
                                    related_name="allocations",
                                    help_text=_('Optional topup that was allocated with these credits'))

    debit_type = models.CharField(max_length=1, choices=DEBIT_TYPES, null=False, help_text=_('What caused this debit'))

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   related_name="debits_created",
                                   help_text="The user which originally created this item")
    created_on = models.DateTimeField(default=timezone.now,
                                      help_text="When this item was originally created")

    @classmethod
    def get_unsquashed(cls):
        return super(Debit, cls).get_unsquashed().filter(debit_type=cls.TYPE_PURGE)

    @classmethod
    def get_squash_query(cls, distinct_set):
        sql = """
            WITH removed as (
                DELETE FROM %(table)s WHERE "topup_id" = %%s AND debit_type = 'P' RETURNING "amount"
            )
            INSERT INTO %(table)s("topup_id", "amount", "debit_type", "created_on", "is_squashed")
            VALUES (%%s, GREATEST(0, (SELECT SUM("amount") FROM removed)), 'P', %%s, TRUE);
        """ % {'table': cls._meta.db_table}

        return sql, (distinct_set.topup_id, distinct_set.topup_id, timezone.now())


class TopUpCredits(SquashableModel):
    """
    Used to track number of credits used on a topup, mostly maintained by triggers on Msg insertion.
    """
    SQUASH_OVER = ('topup_id',)

    topup = models.ForeignKey(TopUp,
                              help_text=_("The topup these credits are being used against"))
    used = models.IntegerField(help_text=_("How many credits were used, can be negative"))

    @classmethod
    def get_squash_query(cls, distinct_set):
        sql = """
        WITH deleted as (
            DELETE FROM %(table)s WHERE "topup_id" = %%s RETURNING "used"
        )
        INSERT INTO %(table)s("topup_id", "used", "is_squashed")
        VALUES (%%s, GREATEST(0, (SELECT SUM("used") FROM deleted)), TRUE);
        """ % {'table': cls._meta.db_table}

        return sql, (distinct_set.topup_id,) * 2


class CreditAlert(SmartModel):
    """
    Tracks when we have sent alerts to organization admins about low credits.
    """

    ALERT_TYPES_CHOICES = ((ORG_CREDIT_OVER, _("Credits Over")),
                           (ORG_CREDIT_LOW, _("Low Credits")),
                           (ORG_CREDIT_EXPIRING, _("Credits expiring soon")))

    org = models.ForeignKey(Org, help_text="The organization this alert was triggered for")
    alert_type = models.CharField(max_length=1, choices=ALERT_TYPES_CHOICES,
                                  help_text="The type of this alert")

    @classmethod
    def trigger_credit_alert(cls, org, alert_type):
        # is there already an active alert at this threshold? if so, exit
        if CreditAlert.objects.filter(is_active=True, org=org, alert_type=alert_type):  # pragma: needs cover
            return None

        print("triggering %s credits alert type for %s" % (alert_type, org.name))

        admin = org.get_org_admins().first()

        if admin:
            # Otherwise, create our alert objects and trigger our event
            alert = CreditAlert.objects.create(org=org, alert_type=alert_type,
                                               created_by=admin, modified_by=admin)

            alert.send_alert()

    def send_alert(self):
        from .tasks import send_alert_email_task
        send_alert_email_task(self.id)

    def send_email(self):
        email = self.created_by.email
        if not email:  # pragma: needs cover
            return

        branding = self.org.get_branding()
        subject = _("%(name)s Credits Alert") % branding
        template = "orgs/email/alert_email"
        to_email = email

        context = dict(org=self.org, now=timezone.now(), branding=branding, alert=self, customer=self.created_by)
        context['subject'] = subject

        send_template_email(to_email, subject, template, context, branding)

    @classmethod
    def reset_for_org(cls, org):
        CreditAlert.objects.filter(org=org).update(is_active=False)

    @classmethod
    def check_org_credits(cls):
        from temba.msgs.models import Msg

        # all active orgs in the last hour
        active_orgs = Msg.objects.filter(created_on__gte=timezone.now() - timedelta(hours=1))
        active_orgs = active_orgs.order_by('org').distinct('org')

        for msg in active_orgs:
            org = msg.org

            # does this org have less than 0 messages?
            org_remaining_credits = org.get_credits_remaining()
            org_low_credits = org.has_low_credits()

            if org_remaining_credits <= 0:
                CreditAlert.trigger_credit_alert(org, ORG_CREDIT_OVER)
            elif org_low_credits:  # pragma: needs cover
                CreditAlert.trigger_credit_alert(org, ORG_CREDIT_LOW)
            elif org.is_nearing_expiration():  # pragma: needs cover
                CreditAlert.trigger_credit_alert(org, ORG_CREDIT_EXPIRING)
