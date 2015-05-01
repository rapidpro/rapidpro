from __future__ import unicode_literals

import calendar
import json
import logging
import os
import pytz
import random
import re
import stripe
import traceback

from dateutil.relativedelta import relativedelta
from datetime import datetime, timedelta
from decimal import Decimal
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models import Sum, Count, F
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _
from django.utils.text import slugify
from django.contrib.auth.models import User, Group
from enum import Enum
from redis_cache import get_redis_connection
from smartmin.models import SmartModel
from temba.locations.models import AdminBoundary, BoundaryAlias
from temba.nexmo import NexmoClient
from temba.temba_email import send_temba_email
from temba.utils import analytics, str_to_datetime, get_datetime_format, datetime_to_str, datetime_to_ms, random_string
from temba.utils import timezone_to_country_code
from temba.utils.cache import get_cacheable_result, incrby_existing
from twilio.rest import TwilioRestClient
from uuid import uuid4
from .bundles import BUNDLE_MAP, WELCOME_TOPUP_SIZE

CURRENT_EXPORT_VERSION = 4
EARLIEST_IMPORT_VERSION = 3

MT_SMS_EVENTS = 1 << 0
MO_SMS_EVENTS = 1 << 1
MT_CALL_EVENTS = 1 << 2
MO_CALL_EVENTS = 1 << 3
ALARM_EVENTS = 1 << 4

ALL_EVENTS = MT_SMS_EVENTS | MO_SMS_EVENTS | MT_CALL_EVENTS | MO_CALL_EVENTS | ALARM_EVENTS

# number of credits before they get special features
# such as adding extra users
PRO_CREDITS_THRESHOLD = 100000

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

ORG_LOW_CREDIT_THRESHOLD = 500

# cache keys and TTLs
ORG_LOCK_KEY = 'org:%d:lock:%s'
ORG_FOLDER_COUNT_CACHE_KEY = 'org:%d:cache:folder_count:%s'
ORG_CREDITS_TOTAL_CACHE_KEY = 'org:%d:cache:credits_total'
ORG_CREDITS_USED_CACHE_KEY = 'org:%d:cache:credits_used'

ORG_LOCK_TTL = 60  # 1 minute
ORG_CREDITS_CACHE_TTL = 7 * 24 * 60 * 60  # 1 week
ORG_DISPLAY_CACHE_TTL = 7 * 24 * 60 * 60  # 1 week


class OrgFolder(Enum):
    # used on the contacts page
    contacts_all = 1
    contacts_failed = 2
    contacts_blocked = 3

    # used on the messages page
    msgs_inbox = 4
    msgs_archived = 5
    msgs_outbox = 6
    broadcasts_outbox = 7
    calls_all = 8
    msgs_flows = 9
    broadcasts_scheduled = 10
    msgs_failed = 11


class OrgEvent(Enum):
    """
    Represents an internal org event
    """
    contact_new = 1
    contact_blocked = 2
    contact_unblocked = 3
    contact_failed = 4
    contact_unfailed = 5
    contact_deleted = 6
    broadcast_new = 7
    msg_new_incoming = 8
    msg_new_outgoing = 9
    msg_handled = 10
    msg_failed = 11
    msg_archived = 12
    msg_restored = 13
    msg_deleted = 14
    call_new = 15
    topup_new = 16
    topup_updated = 17


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


class OrgModelMixin(object):
    """
    Mixin for objects like contacts, messages which are owned by orgs and affect org caches
    """
    def _update_state(self, required_state, new_state, event):
        """
        Updates the state of this org-owned asset and triggers an org event, if it is currently in another state
        """
        qs = type(self).objects.filter(pk=self.pk)

        # required state is either provided explicitly, or is inverse of new_state
        if required_state:
            qs = qs.filter(**required_state)
        else:
            qs = qs.exclude(**new_state)

        # tells us if object state was actually changed at a db-level
        rows_updated = qs.update(**new_state)
        if rows_updated:
            # update current object to new state
            for attr_name, value in new_state.iteritems():
                setattr(self, attr_name, value)

            self.org.update_caches(event, self)

        return bool(rows_updated)


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
    language = models.CharField(verbose_name=_("Language"), max_length=64, null=True, blank=True,
                                choices=settings.LANGUAGES, help_text=_("The main language used by this organization"))
    timezone = models.CharField(verbose_name=_("Timezone"), max_length=64)
    date_format = models.CharField(verbose_name=_("Date Format"), max_length=1, choices=DATE_PARSING, default=DAYFIRST,
                                   help_text=_("Whether day comes first or month comes first in dates"))

    webhook = models.CharField(verbose_name=_("Webhook"), max_length=255, blank=True, null=True)
    webhook_events = models.IntegerField(default=0, verbose_name=_("Webhook Events"),
                                         help_text=_("Which type of actions will trigger webhook events."))

    country = models.ForeignKey('locations.AdminBoundary', null=True, blank=True, on_delete=models.SET_NULL,
                                help_text="The country this organization should map results for.")

    msg_last_viewed = models.DateTimeField(verbose_name=_("Message Last Viewed"), auto_now_add=True)

    flows_last_viewed = models.DateTimeField(verbose_name=_("Flows Last Viewed"), auto_now_add=True)

    config = models.TextField(null=True, verbose_name=_("Configuration"),
                              help_text=_("More Organization specific configuration"))

    slug = models.SlugField(verbose_name=_("Slug"), max_length=255, null=True, blank=True, unique=True, error_messages=dict(unique=_("This slug is not available")))

    is_anon = models.BooleanField(default=False,
                                  help_text=_("Whether this organization anonymizes the phone numbers of contacts within it"))

    primary_language = models.ForeignKey('orgs.Language', null=True, blank=True, related_name='orgs',
                                         help_text=_('The primary language will be used for contacts with no language preference.'), on_delete=models.SET_NULL)

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

    def lock_on(self, lock, qualifier=None):
        """
        Creates the requested type of org-level lock
        """
        r = get_redis_connection()
        lock_key = ORG_LOCK_KEY % (self.pk, lock.name)
        if qualifier:
            lock_key += (":%s" % qualifier)

        return r.lock(lock_key, ORG_LOCK_TTL)

    def get_folder_queryset(self, folder):
        """
        Gets the queryset for the given contact or message folder for this org
        """
        from temba.contacts.models import Contact
        from temba.msgs.models import Broadcast, Call, Msg, INCOMING, INBOX, OUTGOING, PENDING, FLOW, FAILED as M_FAILED
        from temba.contacts.models import ALL_CONTACTS_GROUP, BLOCKED_CONTACTS_GROUP, FAILED_CONTACTS_GROUP

        if folder == OrgFolder.contacts_all:
            return self.all_groups.get(group_type=ALL_CONTACTS_GROUP).contacts.all()
        elif folder == OrgFolder.contacts_failed:
            return self.all_groups.get(group_type=FAILED_CONTACTS_GROUP).contacts.all()
        elif folder == OrgFolder.contacts_blocked:
            return self.all_groups.get(group_type=BLOCKED_CONTACTS_GROUP).contacts.all()
        elif folder == OrgFolder.msgs_inbox:
            return Msg.get_messages(self, direction=INCOMING, is_archived=False, msg_type=INBOX).exclude(status=PENDING)
        elif folder == OrgFolder.msgs_archived:
            return Msg.get_messages(self, is_archived=True)
        elif folder == OrgFolder.msgs_outbox:
            return Msg.get_messages(self, direction=OUTGOING, is_archived=False)
        elif folder == OrgFolder.broadcasts_outbox:
            return Broadcast.get_broadcasts(self, scheduled=False)
        elif folder == OrgFolder.calls_all:
            return Call.get_calls(self)
        elif folder == OrgFolder.msgs_flows:
            return Msg.get_messages(self, direction=INCOMING, is_archived=False, msg_type=FLOW)
        elif folder == OrgFolder.broadcasts_scheduled:
            return Broadcast.get_broadcasts(self, scheduled=True)
        elif folder == OrgFolder.msgs_failed:
            return Msg.get_messages(self, direction=OUTGOING, is_archived=False).filter(status=M_FAILED)

    def get_folder_count(self, folder):
        """
        Gets the (cached) count for the given contact folder
        """
        from temba.contacts.models import ALL_CONTACTS_GROUP, BLOCKED_CONTACTS_GROUP, FAILED_CONTACTS_GROUP

        if folder == OrgFolder.contacts_all:
            return self.all_groups.get(group_type=ALL_CONTACTS_GROUP).count
        elif folder == OrgFolder.contacts_blocked:
            return self.all_groups.get(group_type=BLOCKED_CONTACTS_GROUP).count
        elif folder == OrgFolder.contacts_failed:
            return self.all_groups.get(group_type=FAILED_CONTACTS_GROUP).count
        else:
            def calculate(_folder):
                return self.get_folder_queryset(_folder).count()

            cache_key = self._get_folder_count_cache_key(folder)
            return get_cacheable_result(cache_key, ORG_DISPLAY_CACHE_TTL, lambda: calculate(folder))

    def _get_folder_count_cache_key(self, folder):
        return ORG_FOLDER_COUNT_CACHE_KEY % (self.pk, folder.name)

    def patch_folder_queryset(self, queryset, folder, request):
        """
        Patches the given queryset so that it's count function fetches from our folder count cache
        """
        def patched_count():
            cached_count = self.get_folder_count(folder)
            # if our cache calculations are wrong we might have a negative value that will crash a paginator
            if cached_count >= 0:
                return cached_count
            else:
                logger = logging.getLogger(__name__)
                msg = 'Cached count for folder %s in org #%d is negative (%d)' % (folder.name, self.id, cached_count)
                logger.error(msg, exc_info=True, extra=dict(request=request))
                return queryset._real_count()  # defer to the real count function

        queryset._real_count = queryset.count  # backup the real count function
        queryset.count = patched_count

    def has_contacts(self):
        """
        Gets whether this org has any contacts
        """
        return (self.get_folder_count(OrgFolder.contacts_all) + self.get_folder_count(OrgFolder.contacts_blocked)) > 0

    def has_messages(self):
        """
        Gets whether this org has any messages (or calls)
        """
        return (self.get_folder_count(OrgFolder.msgs_inbox)
                + self.get_folder_count(OrgFolder.msgs_outbox)
                + self.get_folder_count(OrgFolder.calls_all)) > 0

    def update_caches(self, event, entity):
        """
        Update org-level caches in response to an event
        """
        from temba.msgs.models import INCOMING, INBOX, FAILED as M_FAILED

        #print "ORG EVENT: %s for %s #%d" % (event.name, type(entity).__name__, entity.pk)

        r = get_redis_connection()

        def update_folder_count(folder, delta):
            #print " > %d to folder %s" % (delta, folder.name)

            cache_key = self._get_folder_count_cache_key(folder)
            incrby_existing(cache_key, delta, r)

        # helper methods for modifying all the keys
        clear_value = lambda key: r.delete(key)
        increment_count = lambda folder: update_folder_count(folder, 1)
        decrement_count = lambda folder: update_folder_count(folder, -1)

        if event == OrgEvent.broadcast_new:
            if entity.schedule:
                increment_count(OrgFolder.broadcasts_scheduled)
            else:
                increment_count(OrgFolder.broadcasts_outbox)

        elif event == OrgEvent.msg_new_incoming:
            pass  # message will be pending and won't appear in the inbox

        elif event == OrgEvent.msg_new_outgoing:
            increment_count(OrgFolder.msgs_outbox)

        elif event == OrgEvent.msg_handled:
            increment_count(OrgFolder.msgs_inbox if entity.msg_type == INBOX else OrgFolder.msgs_flows)

        elif event == OrgEvent.msg_failed:
            increment_count(OrgFolder.msgs_failed)

        elif event == OrgEvent.msg_archived:
            if entity.direction == INCOMING:
                decrement_count(OrgFolder.msgs_inbox if entity.msg_type == INBOX else OrgFolder.msgs_flows)
            else:
                decrement_count(OrgFolder.msgs_outbox)
            increment_count(OrgFolder.msgs_archived)
            if entity.status == M_FAILED:
                decrement_count(OrgFolder.msgs_failed)

        elif event == OrgEvent.msg_restored:
            increment_count(OrgFolder.msgs_inbox if entity.direction == INCOMING else OrgFolder.msgs_outbox)
            decrement_count(OrgFolder.msgs_archived)
            if entity.status == M_FAILED:
                increment_count(OrgFolder.msgs_failed)

        elif event == OrgEvent.msg_deleted:
            decrement_count(OrgFolder.msgs_archived)

        elif event == OrgEvent.call_new:
            increment_count(OrgFolder.calls_all)

        elif event == OrgEvent.topup_new:
            incrby_existing(ORG_CREDITS_TOTAL_CACHE_KEY % self.pk, entity.credits, r)

        elif event == OrgEvent.topup_updated:
            clear_value(ORG_CREDITS_TOTAL_CACHE_KEY % self.pk)

    def clear_caches(self, caches):
        """
        Clears the given cache types (display, credits) for this org. Returns number of keys actually deleted
        """
        keys = []
        if OrgCache.display in caches:
            for folder in OrgFolder.__members__.values():
                keys.append(self._get_folder_count_cache_key(folder))
            for label in self.label_set.all():
                keys.append(label.get_message_count_cache_key())

        if OrgCache.credits in caches:
            keys.append(ORG_CREDITS_TOTAL_CACHE_KEY % self.pk)
            keys.append(ORG_CREDITS_USED_CACHE_KEY % self.pk)

        r = get_redis_connection()
        return r.delete(*keys)

    def import_app(self, data, user, site=None):
        from temba.flows.models import Flow
        from temba.campaigns.models import Campaign
        from temba.triggers.models import Trigger

        # we need to import flows first, they will resolve to
        # the appropriate ids and update our definition accordingly
        Flow.import_flows(data, self, user, site)
        Campaign.import_campaigns(data, self, user, site)
        Trigger.import_triggers(data, self, user, site)

    def config_json(self):
        if self.config:
            return json.loads(self.config)
        else:
            return dict()

    def can_add_sender(self):
        """
        If an org's telephone send channel is an Android device, let them add a bulk sender
        """
        from temba.contacts.models import TEL_SCHEME
        from temba.channels.models import ANDROID

        send_channel = self.get_send_channel(TEL_SCHEME)
        return send_channel and send_channel.channel_type == ANDROID

    def can_add_caller(self):
        return not self.supports_ivr() and self.is_connected_to_twilio()

    def supports_ivr(self):
        return self.get_call_channel() or self.get_answer_channel()

    def get_channel(self, scheme, role):
        """
        Gets a channel for this org which supports the given scheme and role
        """
        from temba.channels.models import Channel, SEND, CALL

        types = Channel.types_for_scheme(scheme)
        channel = self.channels.filter(is_active=True, channel_type__in=types,
                                       role__contains=role).order_by('-pk').first()

        if channel and (role == SEND or role == CALL):
            return channel.get_delegate(role)
        else:
            return channel

    def get_send_channel(self, scheme=None, contact_urn=None):
        from temba.contacts.models import TEL_SCHEME
        from temba.channels.models import SEND

        if not scheme and not contact_urn:
            raise ValueError("Must specify scheme or contact URN")

        if contact_urn:
            scheme = contact_urn.scheme

            # if URN has a previously used channel that is still active, use that
            if contact_urn.channel and contact_urn.channel.is_active:
                previous_sender = self.get_channel_delegate(contact_urn.channel, SEND)
                if previous_sender:
                    return previous_sender

            if contact_urn.scheme == TEL_SCHEME:
                # we don't have a channel for this contact yet, let's try to pick one from the same carrier
                # we need at least one digit to overlap to infer a channel
                contact_number = contact_urn.path.strip('+')
                prefix = 1
                channel = None

                # filter the (possibly) pre-fetched channels in Python to reduce database hits as this method is called
                # for every message in a broadcast
                senders = [r for r in self.channels.all() if SEND in r.role and not r.parent_id and r.is_active and r.address]
                senders.sort(key=lambda r: r.id)

                for r in senders:
                    channel_number = r.address.strip('+')

                    for idx in range(prefix, len(channel_number)):
                        if idx >= prefix and channel_number[0:idx] == contact_number[0:idx]:
                            prefix = idx
                            channel = r
                        else:
                            break

                if channel:
                    return self.get_channel_delegate(channel, SEND)

        return self.get_channel(scheme, SEND)

    def get_receive_channel(self, scheme):
        from temba.channels.models import RECEIVE
        return self.get_channel(scheme, RECEIVE)

    def get_call_channel(self):
        from temba.contacts.models import TEL_SCHEME
        from temba.channels.models import CALL
        return self.get_channel(TEL_SCHEME, CALL)

    def get_answer_channel(self):
        from temba.contacts.models import TEL_SCHEME
        from temba.channels.models import ANSWER
        return self.get_channel(TEL_SCHEME, ANSWER)

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
            schemes.add(channel.get_scheme())

        setattr(self, cache_attr, schemes)
        return schemes

    @classmethod
    def get_possible_countries(cls):
        return AdminBoundary.objects.filter(level=0).order_by('name')

    def trigger_send(self, msgs=None):
        """
        Triggers either our Android channels to sync, or for all our pending messages to be queued
        to send.
        """
        from temba.msgs.models import Msg
        from temba.channels.models import Channel, ANDROID

        # if we have msgs, then send just those
        if msgs:
            ids = [m.id for m in msgs]

            # trigger syncs for our android channels
            for channel in self.channels.filter(is_active=True, channel_type=ANDROID, msgs__id__in=ids):
                channel.trigger_sync()

            # and send those messages
            Msg.send_messages(msgs)

        # otherwise, sync all pending messages and channels
        else:
            for channel in self.channels.filter(is_active=True, channel_type=ANDROID):
                channel.trigger_sync()

            # otherwise, send any pending messages on our channels
            r = get_redis_connection()

            with r.lock('trigger_send_%d' % self.pk, timeout=60):
                pending = Channel.get_pending_messages(self)
                Msg.send_messages(pending)

    def connect_nexmo(self, api_key, api_secret):
        nexmo_uuid = str(uuid4())
        nexmo_config = {NEXMO_KEY: api_key.strip(), NEXMO_SECRET: api_secret.strip(), NEXMO_UUID: nexmo_uuid}

        config = self.config_json()
        config.update(nexmo_config)
        self.config = json.dumps(config)

        # update the mo and dl URL for our account
        client = NexmoClient(api_key, api_secret)

        mo_path = reverse('api.nexmo_handler', args=['receive', nexmo_uuid])
        dl_path = reverse('api.nexmo_handler', args=['status', nexmo_uuid])

        from temba.settings import TEMBA_HOST
        client.update_account('http://%s%s' % (TEMBA_HOST, mo_path), 'http://%s%s' % (TEMBA_HOST, dl_path))

        # clear all our channel configurations
        self.save(update_fields=['config'])
        self.clear_channel_caches()

    def nexmo_uuid(self):
        config = self.config_json()
        return config.get(NEXMO_UUID, None)

    def connect_twilio(self, account_sid, account_token):
        client = TwilioRestClient(account_sid, account_token)
        app_name = "%s/%d" % (settings.TEMBA_HOST.lower(), self.pk)
        apps = client.applications.list(friendly_name=app_name)
        if apps:
            temba_app = apps[0]
        else:
            app_url = "https://" + settings.TEMBA_HOST + "%s" % reverse('api.twilio_handler')

            # the the twiml to run when the voice app fails
            fallback_url = "https://" + settings.AWS_STORAGE_BUCKET_NAME + "/voice_unavailable.xml"

            temba_app = client.applications.create(friendly_name=app_name,
                                                   voice_url=app_url,
                                                   voice_fallback_url=fallback_url,
                                                   voice_fallback_method='GET',
                                                   sms_url=app_url,
                                                   sms_method="POST")

        application_sid = temba_app.sid
        twilio_config = {ACCOUNT_SID: account_sid, ACCOUNT_TOKEN: account_token, APPLICATION_SID: application_sid}

        config = self.config_json()
        config.update(twilio_config)
        self.config = json.dumps(config)

        # clear all our channel configurations
        self.save(update_fields=['config'])
        self.clear_channel_caches()

    def is_connected_to_nexmo(self):
        if self.config:
            config = self.config_json()
            nexmo_key = config.get(NEXMO_KEY, None)
            nexmo_secret = config.get(NEXMO_SECRET, None)
            nexmo_uuid = config.get(NEXMO_UUID, None)

            return nexmo_key and nexmo_secret and nexmo_uuid
        else:
            return False

    def is_connected_to_twilio(self):
        if self.config:
            config = self.config_json()
            account_sid = config.get(ACCOUNT_SID, None)
            account_token = config.get(ACCOUNT_TOKEN, None)
            application_sid = config.get(APPLICATION_SID, None)
            if account_sid and account_token and application_sid:
                return True
        return False

    def remove_nexmo_account(self):
        if self.config:
            config = self.config_json()
            config[NEXMO_KEY] = ''
            config[NEXMO_SECRET] = ''
            self.config = json.dumps(config)
            self.save()

            # release any nexmo channels
            from temba.channels.models import NEXMO
            channels = self.channels.filter(is_active=True, channel_type=NEXMO)
            for channel in channels:
                channel.release()

            # clear all our channel configurations
            self.clear_channel_caches()

    def remove_twilio_account(self):
        if self.config:
            config = self.config_json()
            config[ACCOUNT_SID] = ''
            config[ACCOUNT_TOKEN] = ''
            config[APPLICATION_SID] = ''
            self.config = json.dumps(config)
            self.save()

            # release any twilio channels
            from temba.channels.models import TWILIO
            channels = self.channels.filter(is_active=True, channel_type=TWILIO)
            for channel in channels:
                channel.release()

            # clear all our channel configurations
            self.clear_channel_caches()

    def get_verboice_client(self):
        from temba.ivr.clients import VerboiceClient
        channel = self.get_call_channel()
        from temba.channels.models import VERBOICE
        if channel.channel_type == VERBOICE:
            return VerboiceClient(channel)
        return None

    def get_twilio_client(self):
        config = self.config_json()
        from temba.ivr.clients import TwilioClient

        if config:
            account_sid = config.get(ACCOUNT_SID, None)
            auth_token = config.get(ACCOUNT_TOKEN, None)
            if account_sid and auth_token:
                return TwilioClient(account_sid, auth_token)
        return None

    def get_nexmo_client(self):
        config = self.config_json()
        if config:
            api_key = config.get(NEXMO_KEY, None)
            api_secret = config.get(NEXMO_SECRET, None)
            if api_key and api_secret:
                return NexmoClient(api_key, api_secret)

        return None

    def clear_channel_caches(self):
        """
        Clears any cached configurations we have for any of our channels.
        """
        from temba.channels.models import Channel
        for channel in self.channels.exclude(channel_type='A'):
            Channel.clear_cached_channel(channel.pk)

    def get_dayfirst(self):
        return self.date_format == DAYFIRST

    def get_tzinfo(self):
        # we have to build the timezone based on an actual date
        # see: https://bugs.launchpad.net/pytz/+bug/1319939
        return timezone.now().astimezone(pytz.timezone(self.timezone)).tzinfo

    def format_date(self, datetime, show_time=True):
        """
        Formats a datetime with or without time using this org's date format
        """
        formats = get_datetime_format(self.get_dayfirst())
        format = formats[1] if show_time else formats[0]
        return datetime_to_str(datetime, format, False, self.get_tzinfo())

    def parse_date(self, date_string):
        if isinstance(date_string, datetime):
            return date_string

        return str_to_datetime(date_string, self.get_tzinfo(), self.get_dayfirst())

    def parse_decimal(self, decimal_string):
        try:
            return Decimal(decimal_string)
        except:
            return None

    def find_boundary_by_name(self, name, level, parent):
        # first check if we have a direct name match
        if parent:
            boundary = parent.children.filter(name__iexact=name, level=level).first()
        elif level == 1:
            boundary = AdminBoundary.objects.filter(parent=self.country, name__iexact=name, level=level).first()
        elif level == 2:
            boundary = AdminBoundary.objects.filter(parent__parent=self.country, name__iexact=name, level=level).first()

        # not found by name, try looking up by alias
        if not boundary:
            if parent:
                alias = BoundaryAlias.objects.filter(name__iexact=name, boundary__level=level,
                                                     boundary__parent=parent).first()
            elif level == 1:
                alias = BoundaryAlias.objects.filter(name__iexact=name, boundary__level=level,
                                                     boundary__parent=self.country).first()
            elif level == 2:
                alias = BoundaryAlias.objects.filter(name__iexact=name, boundary__level=level,
                                                     boundary__parent__parent=self.country).first()

            if alias:
                boundary = alias.boundary

        return boundary

    def parse_location(self, location_string, level, parent=None):
        # no country? bail
        if not self.country or not isinstance(location_string, basestring):
            return None

        # now look up the boundary by full name
        boundary = self.find_boundary_by_name(location_string, level, parent)

        if not boundary:
            # try removing punctuation and try that
            bare_name = re.sub(r"\W+", " ", location_string, flags=re.UNICODE).strip()
            boundary = self.find_boundary_by_name(bare_name, level, parent)

        # if we didn't find it, tokenize it
        if not boundary:
            words = re.split(r"\W+", location_string.lower(), flags=re.UNICODE)
            if len(words) > 1:
                for word in words:
                    boundary = self.find_boundary_by_name(word, level, parent)
                    if boundary:
                        break

                if not boundary:
                    # still no boundary? try n-gram of 2
                    for i in range(0, len(words)-1):
                        bigram = " ".join(words[i:i+2])
                        boundary = self.find_boundary_by_name(bigram, level, parent)
                        if boundary:
                            break

        return boundary

    def get_org_admins(self):
        return self.administrators.all()

    def get_org_editors(self):
        return self.editors.all()

    def get_org_viewers(self):
        return self.viewers.all()

    def get_org_users(self):
        org_users = self.get_org_admins() | self.get_org_editors() | self.get_org_viewers()
        return org_users.distinct()

    def latest_admin(self):
        admin = self.get_org_admins().last()

        # no admins? try editors
        if not admin:
            admin = self.get_org_editors().last()

        # no editors? try viewers
        if not admin:
            admin = self.get_org_viewers().last()

        return admin

    def is_free_plan(self):
        return self.plan == FREE_PLAN or self.plan == TRIAL_PLAN

    def is_pro(self):
        return self.get_credits_total() >= PRO_CREDITS_THRESHOLD

    def has_added_credits(self):
        return self.get_credits_total() > WELCOME_TOPUP_SIZE

    def get_credits_until_pro(self):
        return max(PRO_CREDITS_THRESHOLD - self.get_credits_total(), 0)

    def get_user_org_group(self, user):
        if user in self.get_org_admins():
            user._org_group = Group.objects.get(name="Administrators")
        elif user in self.get_org_editors():
            user._org_group = Group.objects.get(name="Editors")
        elif user in self.get_org_viewers():
            user._org_group = Group.objects.get(name="Viewers")
        else:
            user._org_group = None

        return getattr(user, '_org_group', None)

    def has_twilio_number(self):
        from temba.channels.models import TWILIO
        return self.channels.filter(channel_type=TWILIO)

    def has_nexmo_number(self):
        from temba.channels.models import NEXMO
        return self.channels.filter(channel_type=NEXMO)

    def create_welcome_topup(self, topup_size=WELCOME_TOPUP_SIZE):
        return TopUp.create(self.created_by, price=0, credits=topup_size, org=self)

    def create_system_groups(self):
        """
        Initializes our system groups for this organization so that we can keep track of counts etc..
        """
        from temba.contacts.models import ALL_CONTACTS_GROUP, BLOCKED_CONTACTS_GROUP, FAILED_CONTACTS_GROUP

        self.all_groups.create(name='All Contacts', group_type=ALL_CONTACTS_GROUP,
                               created_by=self.created_by, modified_by=self.modified_by)
        self.all_groups.create(name='Blocked Contacts', group_type=BLOCKED_CONTACTS_GROUP,
                               created_by=self.created_by, modified_by=self.modified_by)
        self.all_groups.create(name='Failed Contacts', group_type=FAILED_CONTACTS_GROUP,
                               created_by=self.created_by, modified_by=self.modified_by)

    def create_sample_flows(self):
        from temba.flows.models import Flow
        from temba.settings import API_URL
        import json

        # get our sample dir
        examples_dir = os.path.join(settings.STATICFILES_DIRS[0], 'examples', 'flows')

        # for each of our samples
        for filename in sorted(os.listdir(examples_dir), reverse=True):
            if filename.endswith(".json") and filename.startswith("0"):
                with open(os.path.join(examples_dir, filename), 'r') as example_file:
                    example = example_file.read()

                # transform our filename into our flow name
                flow_name = " ".join(f.capitalize() for f in filename[2:-5].split('_'))
                flow_name = "%s - %s" % (_("Sample Flow"), flow_name)

                user = self.get_user()
                if user:
                    # some some substitutions
                    org_example = example.replace("{{EMAIL}}", user.username)
                    org_example = org_example.replace("{{API_URL}}", API_URL)

                    if not Flow.objects.filter(name=flow_name, org=self):
                        try:
                            flow = Flow.create(self, user, flow_name)
                            flow.import_definition(json.loads(org_example))
                            flow.save()

                        except Exception as e:
                            import traceback
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
        user = self.administrators.filter(is_active=True).first()
        if user:
            org_user = user
            org_user.set_org(self)
            return org_user
        else:
            return None

    def has_low_credits(self):
        return self.get_credits_remaining() <= ORG_LOW_CREDIT_THRESHOLD

    def get_credits_total(self):
        """
        Gets the total number of credits purchased or assigned to this org
        """
        return get_cacheable_result(ORG_CREDITS_TOTAL_CACHE_KEY % self.pk, ORG_CREDITS_CACHE_TTL,
                                    self._calculate_credits_total)

    def _calculate_credits_total(self):
        # these are the credits that are still active
        active_credits = self.topups.filter(is_active=True, expires_on__gte=timezone.now()).aggregate(Sum('credits')).get('credits__sum')
        active_credits = active_credits if active_credits else 0

        # these are the credits that have been used in expired topups
        expired_credits = self.topups.filter(is_active=True, expires_on__lte=timezone.now()).aggregate(Sum('used')).get('used__sum')
        expired_credits = expired_credits if expired_credits else 0

        return active_credits + expired_credits

    def get_credits_used(self):
        """
        Gets the number of credits used by this org
        """
        return get_cacheable_result(ORG_CREDITS_USED_CACHE_KEY % self.pk, ORG_CREDITS_CACHE_TTL,
                                    self._calculate_credits_used)

    def _calculate_credits_used(self):
        used_credits_sum = self.topups.filter(is_active=True).aggregate(Sum('used')).get('used__sum')
        used_credits_sum = used_credits_sum if used_credits_sum else 0

        unassigned_sum = self.msgs.filter(contact__is_test=False, topup=None).count()

        return used_credits_sum + unassigned_sum

    def _calculate_credit_caches(self):
        """
        Calculates both our total as well as our active topup
        """
        get_cacheable_result(ORG_CREDITS_TOTAL_CACHE_KEY % self.pk, ORG_CREDITS_CACHE_TTL,
                             self._calculate_credits_total, force_dirty=True)
        get_cacheable_result(ORG_CREDITS_USED_CACHE_KEY % self.pk, ORG_CREDITS_CACHE_TTL,
                             self._calculate_credits_used, force_dirty=True)

    def get_credits_remaining(self):
        """
        Gets the number of credits remaining for this org
        """
        return self.get_credits_total() - self.get_credits_used()

    def decrement_credit(self):
        """
        Decrements this orgs credit by 1. Returns the id of the active topup which can then be assigned to the message
        or IVR action which is being paid for with this credit
        """
        total_used_key = ORG_CREDITS_USED_CACHE_KEY % self.pk
        incrby_existing(total_used_key, 1)

        active_topup = self._calculate_active_topup()
        return active_topup.pk if active_topup else None

    def _calculate_active_topup(self):
        """
        Calculates the oldest non-expired topup that still has credits
        """
        non_expired_topups = self.topups.filter(is_active=True, expires_on__gte=timezone.now())
        active_topups = non_expired_topups.filter(credits__gt=F('used')).order_by('expires_on')
        return active_topups.first()

    def apply_topups(self):
        """
        We allow users to receive messages even if they're out of credit. Once they re-add credit, this function
        retro-actively applies topups to any messages or IVR actions that don't have a topup
        """
        from temba.msgs.models import Msg

        with self.lock_on(OrgLock.credits):
            # get all items that haven't been credited
            msg_uncredited = self.msgs.filter(topup=None, contact__is_test=False).order_by('created_on')
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
                    current_topup_remaining = current_topup.credits - current_topup.used

                if current_topup_remaining:
                    # if we found some credit, assign the item to the current topup
                    new_topup_items[current_topup].append(item)
                    current_topup_remaining -= 1
                else:
                    # if not, then stop processing items
                    break

            # update items in the database with their new topups
            for topup, items in new_topup_items.iteritems():
                Msg.objects.filter(id__in=[item.pk for item in items if isinstance(item, Msg)]).update(topup=topup)

        # deactive all our credit alerts
        CreditAlert.reset_for_org(self)

    def current_plan_start(self):
        today = timezone.now().date()

        # move it to the same day our plan started (taking into account short months)
        plan_start = today.replace(day=min(self.plan_start.day, calendar.monthrange(today.year, today.month)[1]))

        if plan_start > today:
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
        except Exception as e:
            traceback.print_exc()
            return None

    def add_credits(self, bundle, token, user): # pragma: no cover
        # look up our bundle
        if not bundle in BUNDLE_MAP:
            raise ValidationError(_("Invalid bundle: %s, cannot upgrade.") % bundle)

        bundle = BUNDLE_MAP[bundle]

        # adds credits to this org
        stripe.api_key = get_stripe_credentials()[1]

        # our stripe customer and the card to use
        stripe_customer = None
        stripe_card = None

        # our actual customer object
        customer = self.get_stripe_customer()

        # 3 possible cases
        # 1. we already have a stripe customer and the token matches it
        # 2. we already have a stripe customer, but they have just added a new card, we need to use that one
        # 3. we don't have a customer, so we need to create a new customer and use that card

        # for our purposes, #1 and #2 are treated the same, we just always update the default card

        try:
            if not customer:
                # then go create a customer object for this user
                customer = stripe.Customer.create(card=token, email=user,
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

                card = customer.cards.create(card=token)

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
                           org=self.name,
                           cc_last4=charge.card.last4,
                           cc_type=charge.card.type,
                           cc_name=charge.card.name)

            print context

            analytics.track(user.email, "temba.topup_purchased", context)

            # apply our new topups
            self.apply_topups()

            return topup

        except Exception as e:
            traceback.print_exc(e)
            raise ValidationError(_("Sorry, we were unable to charge your card, please try again later or contact us."))

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
                except Exception as e:
                    traceback.print_exc(e)
                    raise ValidationError(_("Sorry, we are unable to cancel your plan at this time.  Please contact us."))
            else:
                raise ValidationError(_("Sorry, we are unable to cancel your plan at this time.  Please contact us."))

        else:
            # we have a customer, try to upgrade them
            if customer:
                try:
                    subscription = customer.update_subscription(plan=new_plan)

                    analytics.track(user.username, 'temba.plan_upgraded', dict(previousPlan=self.plan, plan=new_plan))

                except Exception as e:
                    # can't load it, oh well, we'll try to create one dynamically below
                    traceback.print_exc(e)
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

                except Exception as e:
                    traceback.print_exc(e)
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

    def get_export_flows(self, include_archived=False):
        from temba.flows.models import Flow
        flows = self.flows.all().exclude(flow_type=Flow.MESSAGE).order_by('-modified_on')
        if not include_archived:
            flows = flows.filter(is_archived=False)
        return flows

    def get_recommended_channel(self):
        from temba.channels.views import TWILIO_SEARCH_COUNTRIES
        NEXMO_RECOMMEND_COUNTRIES = ['US', 'CA', 'GB', 'AU', 'AT', 'FI', 'DE', 'HK', 'HU',
                                     'LT', 'NL', 'NO', 'PL', 'SE', 'CH', 'BE', 'ES', 'ZA']

        countrycode = timezone_to_country_code(self.timezone)

        recommended = 'android'
        if countrycode in NEXMO_RECOMMEND_COUNTRIES:
            recommended = 'nexmo'
        if countrycode in [country[0] for country in TWILIO_SEARCH_COUNTRIES]:
            recommended = 'twilio'
        if countrycode == 'KE':
            recommended = 'africastalking'
        if countrycode == 'ID':
            recommended = 'hub9'
        if countrycode == 'SO':
            recommended = 'shaqodoon'
        if countrycode == 'NP':
            recommended = 'blackmyna'

        return recommended


    def initialize(self, topup_size=WELCOME_TOPUP_SIZE):
        """
        Initializes an organization, creating all the dependent objects we need for it to work properly.
        """
        self.create_system_groups()
        self.create_sample_flows()
        self.create_welcome_topup(topup_size)

    @classmethod
    def create_user(cls, email, password):
        user = User.objects.create_user(username=email, email=email, password=password)
        return user

    @classmethod
    def get_org(cls, user):
        if not user:
            return None

        if not hasattr(user, '_org'):
            org = Org.objects.filter(administrators=user, is_active=True).first()
            if org:
                user._org = org

        return getattr(user, '_org', None)

    def __unicode__(self):
        return self.name


############ monkey patch User class with a few extra functions ##############

def get_user_orgs(user):
    if user.is_superuser:
        return Org.objects.all()
    user_orgs = user.org_admins.all() | user.org_editors.all() | user.org_viewers.all()
    return user_orgs.distinct().order_by('name')


def get_org(obj):
    return getattr(obj, '_org', None)


def is_alpha_user(user):
    return user.groups.filter(name='Alpha')


def is_beta_user(user):
    return user.groups.filter(name='Beta')


def get_settings(user):
    if not user:
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
    if user.is_superuser:
        return True

    if user.is_anonymous():
        return False

    org_group = org.get_user_org_group(user)

    if not org_group:
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
               ('V', _("Viewer")))


def get_stripe_credentials():
    public_key = os.environ.get('STRIPE_PUBLIC_KEY', getattr(settings, 'STRIPE_PUBLIC_KEY', 'MISSING_STRIPE_PUBLIC_KEY'))
    private_key = os.environ.get('STRIPE_PRIVATE_KEY', getattr(settings, 'STRIPE_PRIVATE_KEY', 'MISSING_STRIPE_PRIVATE_KEY'))
    return (public_key, private_key)


class Language(SmartModel):
    """
    A Language that has been added to the org. In the end and language is just an iso_code and name
    and it is not really restricted to real-world languages at this level. Instead we restrict the
    language selection options to real-world languages.
    """
    name = models.CharField(max_length=128)
    iso_code = models.CharField(max_length=4)
    org = models.ForeignKey(Org, verbose_name=_("Org"), related_name="languages")

    def as_json(self):
        return dict(name=self.name, iso_code=self.iso_code)

    def __unicode__(self):
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

    host = models.CharField(max_length=32, help_text=_("The host this invitation was created on"))

    user_group = models.CharField(max_length=1, choices=USER_GROUPS, default='V', verbose_name=_("User Role"))

    def save(self, *args, **kwargs):
        if not self.secret:
            secret = random_string(64)

            while Invitation.objects.filter(secret=secret):
                secret = random_string(64)

            self.secret = secret

        return super(Invitation, self).save(*args, **kwargs)

    @classmethod
    def generate_random_string(cls, length):
        """
        Generates a [length] characters alpha numeric secret
        """
        letters="23456789ABCDEFGHJKLMNPQRSTUVWXYZ" # avoid things that could be mistaken ex: 'I' and '1'
        return ''.join([random.choice(letters) for _ in range(length)])

    def send_invitation(self):
        from .tasks import send_invitation_email_task
        send_invitation_email_task(self.id)

    def send_email(self):
        # no=op if we do not know the email
        if not self.email:
            return

        from temba.middleware import BrandingMiddleware
        branding = BrandingMiddleware.get_branding_for_host(self.host)

        subject = _("%(name)s Invitation") % branding
        template = "orgs/email/invitation_email"
        to_email = self.email

        context = dict(org=self.org, now=timezone.now(), branding=branding, invitation=self)
        context['subject'] = subject

        send_temba_email(to_email, subject, template, context, branding)


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


class TopUp(SmartModel):
    """
    TopUps are used to track usage across the platform. Each TopUp represents a certain number of
    credits that can be consumed by messages.
    """
    org = models.ForeignKey(Org, related_name='topups',
                            help_text="The organization that was toppped up")
    price = models.IntegerField(verbose_name=_("Price Paid"),
                                help_text=_("The price paid for the messages in this top up (in cents)"))
    credits = models.IntegerField(verbose_name=_("Number of Credits"),
                                  help_text=_("The number of credits bought in this top up"))
    used = models.IntegerField(verbose_name=_("Number of Credits used"), default=0,
                               help_text=_("The number of credits used in this top up"))
    expires_on = models.DateTimeField(verbose_name=("Expiration Date"),
                                      help_text=_("The date that this top up will expire"))
    stripe_charge = models.CharField(verbose_name=_("Stripe Charge Id"), max_length=32, null=True, blank=True,
                                     help_text=_("The Stripe charge id for this charge"))
    comment = models.CharField(max_length=255, null=True, blank=True,
                               help_text="Any comment associated with this topup, used when we credit accounts")

    @classmethod
    def create(cls, user, price, credits, stripe_charge=None, org=None):
        """
        Creates a new topup
        """
        if not org:
            org = user.get_org()

        expires_on = timezone.now() + timedelta(days=365)  # credits last 1 year

        topup = TopUp.objects.create(org=org, price=price, credits=credits, expires_on=expires_on,
                                     stripe_charge=stripe_charge, created_by=user, modified_by=user)

        org.update_caches(OrgEvent.topup_new, topup)
        return topup

    def dollars(self):
        if self.price == 0:
            return 0
        else:
            return Decimal(self.price) / Decimal(100)

    def revert_topup(self):
        # unwind any items that were assigned to this topup
        self.msgs.update(topup=None)

        # mark this topup as inactive
        self.is_active = False
        self.save()

    def get_stripe_charge(self):
        try:
            stripe.api_key = get_stripe_credentials()[1]
            return stripe.Charge.retrieve(self.stripe_charge)
        except Exception as e:
            traceback.print_exc()
            return None

    def __unicode__(self):
        return "%s Credits" % self.credits


class CreditAlert(SmartModel):
    """
    Tracks when we have sent alerts to organization admins about low credits.
    """
    org = models.ForeignKey(Org, help_text="The organization this alert was triggered for")
    threshold = models.IntegerField(help_text="The threshold this alert was sent for")

    @classmethod
    def trigger_credit_alert(cls, org, threshold):
        # is there already an active alert at this threshold? if so, exit
        if CreditAlert.objects.filter(is_active=True, org=org, threshold__lte=threshold):
            return None

        print "triggering alert at %d for %s" % (threshold, org.name)

        admin = org.get_org_admins().first()

        if admin:
            # Otherwise, create our alert objects and trigger our event
            CreditAlert.objects.create(org=org, threshold=threshold,
                                       created_by=admin, modified_by=admin)

            properties = dict(remaining=org.get_credits_remaining(),
                              threshold=threshold,
                              org=org.name)

            if threshold == 0:
                analytics.track(admin.username, 'temba.credits_over', properties)
            else:
                analytics.track(admin.username, 'temba.credits_low', properties)

    @classmethod
    def reset_for_org(cls, org):
        CreditAlert.objects.filter(org=org).update(is_active=False)

    @classmethod
    def check_org_credits(cls):
        from temba.msgs.models import Msg

        # all active orgs in the last hour
        active_orgs = Msg.objects.filter(created_on__gte=timezone.now()-timedelta(hours=1)).order_by('org').distinct('org')

        for msg in active_orgs:
            org = msg.org

            # does this org have less than 0 messages?
            remaining = org.get_credits_remaining()
            if remaining <= 0:
                CreditAlert.trigger_credit_alert(org, 0)
            elif remaining <= 500:
                CreditAlert.trigger_credit_alert(org, 500)
