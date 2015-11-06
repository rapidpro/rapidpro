from __future__ import unicode_literals

import json
import logging
import time
import traceback
from datetime import datetime, timedelta
from uuid import uuid4

import pytz
import regex
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files import File
from django.core.files.temp import NamedTemporaryFile
from django.db import models, transaction
from django.db.models import Q, Count, Prefetch, Sum
from django.utils import timezone
from django.utils.html import escape
from django.utils.translation import ugettext, ugettext_lazy as _
from temba_expressions.evaluator import EvaluationContext, DateStyle
from smartmin.models import SmartModel
from temba.contacts.models import Contact, ContactGroup, ContactURN, TEL_SCHEME
from temba.channels.models import Channel, ANDROID, SEND, CALL
from temba.orgs.models import Org, TopUp, Language, UNREAD_INBOX_MSGS
from temba.schedules.models import Schedule
from temba.utils.email import send_template_email
from temba.utils import get_datetime_format, datetime_to_str, analytics, chunk_list
from temba.utils.expressions import evaluate_template
from temba.utils.models import TembaModel
from temba.utils.queues import DEFAULT_PRIORITY, push_task, LOW_PRIORITY, HIGH_PRIORITY
from .handler import MessageHandler

logger = logging.getLogger(__name__)
__message_handlers = None

MSG_QUEUE = 'msgs'
SEND_MSG_TASK = 'send_msg_task'

HANDLER_QUEUE = 'handler'
HANDLE_EVENT_TASK = 'handle_event_task'
MSG_EVENT = 'msg'
FIRE_EVENT = 'fire'

BATCH_SIZE = 500

INITIALIZING = 'I'
PENDING = 'P'
QUEUED = 'Q'
WIRED = 'W'
SENT = 'S'
DELIVERED = 'D'
HANDLED = 'H'
ERRORED = 'E'
FAILED = 'F'
RESENT = 'R'

INCOMING = 'I'
OUTGOING = 'O'

VISIBLE = 'V'
ARCHIVED = 'A'
DELETED = 'D'

INBOX = 'I'
FLOW = 'F'
IVR = 'V'

SMS_HIGH_PRIORITY = 1000
SMS_NORMAL_PRIORITY = 500
SMS_BULK_PRIORITY = 100

BULK_THRESHOLD = 50

MSG_SENT_KEY = 'msgs_sent_%y_%m_%d'

STATUS_CHOICES = (
    # special state for flows that is used to hold off sending the message until the flow is ready to receive a response
    (INITIALIZING, _("Initializing")),

    # initial state for all messages
    (PENDING, _("Pending")),

    # valid only for outgoing messages
    (QUEUED, _("Queued")),
    (WIRED, _("Wired")),  # means the message was handed off to the provider and credits were deducted for it
    (SENT, _("Sent")),  # means we have confirmation that a message was sent
    (DELIVERED, _("Delivered")),

    # valid only for incoming messages
    (HANDLED, _("Handled")),

    # there was an error during delivery
    (ERRORED, _("Error Sending")),

    # we gave up on sending this message
    (FAILED, _("Failed Sending")),

    # we retried this message
    (RESENT, _("Resent message")),
)


def get_message_handlers():
    """
    Initializes all our message handlers
    """
    global __message_handlers
    if not __message_handlers:
        handlers = []
        for handler_class in settings.MESSAGE_HANDLERS:
            try:
                cls = MessageHandler.find(handler_class)
                handlers.append(cls())
            except Exception as ee:  # pragma: no cover
                traceback.print_exc(ee)

        __message_handlers = handlers

    return __message_handlers


def get_unique_recipients(urns, contacts, groups):
    """
    Builds a list of the unique contacts and URNs by merging urns, contacts and groups
    """
    unique_urns = set()
    unique_contacts = set()
    included_by_urn = set()  # contact ids of contacts included by URN

    for urn in urns:
        unique_urns.add(urn)
        included_by_urn.add(urn.contact_id)

    for group in groups:
        for contact in group.contacts.all():
            if contact.id not in included_by_urn:
                unique_contacts.add(contact)

    for contact in contacts:
        if contact.id not in included_by_urn:
            unique_contacts.add(contact)

    return unique_urns, unique_contacts


class UnreachableException(Exception):
    """
    Exception thrown when a message is being sent to a contact that we don't have a sendable URN for
    """
    pass


class Broadcast(models.Model):
    """
    A broadcast is a message that is sent out to more than one recipient, such
    as a ContactGroup or a list of Contacts. It's nothing more than a way to tie
    messages sent from the same bundle together
    """
    org = models.ForeignKey(Org, verbose_name=_("Org"),
                            help_text=_("The org this broadcast is connected to"))

    groups = models.ManyToManyField(ContactGroup, verbose_name=_("Groups"),
                                    help_text=_("The groups to send the message to"))

    contacts = models.ManyToManyField(Contact, verbose_name=_("Contacts"),
                                      help_text=_("Individual contacts included in this message"))

    urns = models.ManyToManyField(ContactURN, verbose_name=_("URNs"),
                                  help_text=_("Individual URNs included in this message"))

    recipient_count = models.IntegerField(verbose_name=_("Number of recipients"), null=True,
                                          help_text=_("Number of contacts to receive this broadcast"))

    text = models.TextField(max_length=640, verbose_name=_("Text"),
                            help_text=_("The message to send out"))

    channel = models.ForeignKey(Channel, null=True, verbose_name=_("Channel"),
                                help_text=_("Channel to use for message sending"))

    status = models.CharField(max_length=1, verbose_name=_("Status"), choices=STATUS_CHOICES, default=INITIALIZING,
                              help_text=_("The current status for this broadcast"))

    schedule = models.OneToOneField(Schedule, verbose_name=_("Schedule"), null=True,
                                    help_text=_("Our recurring schedule if we have one"), related_name="broadcast")

    parent = models.ForeignKey('Broadcast', verbose_name=_("Parent"), null=True, related_name='children')

    language_dict = models.TextField(verbose_name=_("Translations"),
                                     help_text=_("The localized versions of the broadcast"), null=True)

    is_active = models.BooleanField(default=True, help_text="Whether this broadcast is active")

    created_by = models.ForeignKey(User, related_name="%(app_label)s_%(class)s_creations",
                                   help_text="The user which originally created this item")

    created_on = models.DateTimeField(auto_now_add=True, db_index=True,
                                      help_text=_("When this broadcast was created"))

    modified_by = models.ForeignKey(User, related_name="%(app_label)s_%(class)s_modifications",
                                    help_text="The user which last modified this item")

    modified_on = models.DateTimeField(auto_now=True,
                                       help_text="When this item was last modified")

    @classmethod
    def create(cls, org, user, text, recipients, channel=None, **kwargs):
        create_args = dict(org=org, text=text, channel=channel, created_by=user, modified_by=user)
        create_args.update(kwargs)
        broadcast = Broadcast.objects.create(**create_args)
        broadcast.update_recipients(recipients)
        return broadcast

    def update_contacts(self, contact_ids):
        """
        Optimization for broadcasts that only contain contacts. Updates our contacts according to the passed in
        queryset or array.
        """
        self.urns.clear()
        self.groups.clear()
        self.contacts.clear()

        # get our through model
        RelatedModel = self.contacts.through

        # clear called automatically by django
        for chunk in chunk_list(contact_ids, 1000):
            bulk_contacts = [RelatedModel(contact_id=id, broadcast_id=self.id) for id in chunk]
            RelatedModel.objects.bulk_create(bulk_contacts)

        self.recipient_count = len(contact_ids)
        self.save(update_fields=('recipient_count',))

    def update_recipients(self, recipients):
        """
        Updates the recipients which may be contact groups, contacts or contact URNs. Normally you can't update a
        broadcast after it has been created - the exception is scheduled broadcasts which are never really sent (clones
        of them are sent).
        """
        urns = []
        contacts = []
        groups = []

        for recipient in recipients:
            if isinstance(recipient, ContactURN):
                urns.append(recipient)
            elif isinstance(recipient, Contact):
                contacts.append(recipient)
            elif isinstance(recipient, ContactGroup):
                groups.append(recipient)
            else:
                raise ValueError("Recipient item is not a Contact, ContactURN or ContactGroup")

        self.urns.clear()
        self.urns.add(*urns)
        self.contacts.clear()
        self.contacts.add(*contacts)
        self.groups.clear()
        self.groups.add(*groups)

        urns, contacts = get_unique_recipients(urns, contacts, groups)

        # update the recipient count - the number of messages we intend to send
        self.recipient_count = len(urns) + len(contacts)
        self.save(update_fields=('recipient_count',))

        # cache on object for use in subsequent send(..) calls
        setattr(self, '_recipient_cache', (urns, contacts))

        return urns, contacts

    def has_pending_fire(self):
        return self.schedule and self.schedule.has_pending_fire()

    def fire(self):
        recipients = list(self.urns.all()) + list(self.contacts.all()) + list(self.groups.all())
        broadcast = Broadcast.create(self.org, self.created_by, self.text, recipients,
                                     parent=self, modified_by=self.modified_by)

        broadcast.send(trigger_send=True)

        return broadcast

    @classmethod
    def get_broadcasts(cls, org, scheduled=False):
        qs = Broadcast.objects.filter(org=org).exclude(contacts__is_test=True)
        return qs.exclude(schedule=None) if scheduled else qs.filter(schedule=None)

    def get_messages(self):
        return self.msgs.exclude(status=RESENT)

    def get_messages_by_status(self):
        return self.get_messages().order_by('delivered_on', 'sent_on', '-status')

    def get_messages_substitution_complete(self):
        return self.get_messages().filter(has_template_error=False)

    def get_messages_substitution_incomplete(self):
        return self.get_messages().filter(has_template_error=True)

    def get_message_count(self):
        return self.get_messages().count()

    def get_message_sending_count(self):
        return self.get_messages().filter(status__in=[PENDING, QUEUED]).count()

    def get_message_sent_count(self):
        return self.get_messages().filter(status__in=[SENT, DELIVERED, WIRED]).count()

    def get_message_delivered_count(self):
        return self.get_messages().filter(status=DELIVERED).count()

    def get_message_failed_count(self):
        return self.get_messages().filter(status__in=[FAILED, RESENT]).count()

    def get_first_message(self):
        return self.get_messages().first()

    def get_sync_commands(self, channel):
        """
        Returns the minimal # of broadcast commands for the given Android channel to uniquely represent all the
        messages which are being sent to tel URNs. This will return an array of dicts that look like:
             dict(cmd="mt_bcast", to=[dict(phone=msg.contact.tel, id=msg.pk) for msg in msgs], msg=broadcast.text))
        """
        commands = []
        current_msg = None
        contact_id_pairs = []

        pending = self.get_messages().filter(status__in=[PENDING, QUEUED, WIRED], channel=channel,
                                             contact_urn__scheme=TEL_SCHEME).select_related('contact_urn').order_by('text', 'pk')

        for msg in pending:
            if msg.text != current_msg and contact_id_pairs:
                commands.append(dict(cmd='mt_bcast', to=contact_id_pairs, msg=current_msg))
                contact_id_pairs = []

            current_msg = msg.text
            contact_id_pairs.append(dict(phone=msg.contact_urn.path, id=msg.pk))

        if contact_id_pairs:
            commands.append(dict(cmd='mt_bcast', to=contact_id_pairs, msg=current_msg))

        return commands

    def send(self, trigger_send=True, message_context=None, response_to=None, status=PENDING, msg_type=INBOX,
             created_on=None, base_language=None, partial_recipients=None):
        """
        Sends this broadcast by creating outgoing messages for each recipient.
        """
        # ignore mock messages
        if response_to and not response_to.id:
            response_to = None

        # cannot ask for sending by us AND specify a created on, blow up in that case
        if trigger_send and created_on:
            raise Exception("Cannot trigger send and specify a created_on, breaks creating batches")

        if partial_recipients:
            # if flow is being started, it'll provide a batch of unique contacts itself
            urns, contacts = partial_recipients
        elif hasattr(self, '_recipient_cache'):
            # look to see if previous call to update_recipients left a cached value
            urns, contacts = self._recipient_cache
        else:
            # otherwise fetch everything and calculate
            urns, contacts = get_unique_recipients(self.urns.all(), self.contacts.all(), self.groups.all())

        Contact.bulk_cache_initialize(self.org, contacts)
        recipients = list(urns) + list(contacts)

        # we batch up our SQL calls to speed up the creation of our SMS objects
        batch = []

        # our priority is based on the number of recipients
        priority = SMS_NORMAL_PRIORITY
        if len(recipients) == 1:
            priority = SMS_HIGH_PRIORITY
        elif len(recipients) >= BULK_THRESHOLD:
            priority = SMS_BULK_PRIORITY

        # determine our preferred languages
        org_languages = {l.iso_code for l in self.org.languages.all()}
        other_preferred_languages = []

        if self.org.primary_language:
            other_preferred_languages.append(self.org.primary_language.iso_code)

        if base_language:
            other_preferred_languages.append(base_language)

        # if they didn't pass in a created on, create one ourselves
        if not created_on:
            created_on = timezone.now()

        # pre-fetch channels to reduce database hits
        org = Org.objects.filter(pk=self.org.id).prefetch_related('channels').first()

        # build our text translations
        text_translations = None
        if self.language_dict:
            text_translations = json.loads(self.language_dict)

        for recipient in recipients:
            contact = recipient if isinstance(recipient, Contact) else recipient.contact

            # if contact has a language and it's a valid org language, it has priority
            if contact.language and contact.language in org_languages:
                preferred_languages = [contact.language] + other_preferred_languages
            else:
                preferred_languages = other_preferred_languages

            # find the right text to send
            text = Language.get_localized_text(text_translations, preferred_languages, self.text)

            try:
                msg = Msg.create_outgoing(org,
                                          self.created_by,
                                          recipient,
                                          text,
                                          broadcast=self,
                                          channel=self.channel,
                                          response_to=response_to,
                                          message_context=message_context,
                                          status=status,
                                          msg_type=msg_type,
                                          insert_object=False,
                                          priority=priority,
                                          created_on=created_on)

            except UnreachableException:
                # there was no way to reach this contact, do not create a message
                msg = None

            # only add it to our batch if it was legit
            if msg:
                batch.append(msg)

            # we commit our messages in batches
            if len(batch) >= BATCH_SIZE:
                Msg.objects.bulk_create(batch)

                # send any messages
                if trigger_send:
                    self.org.trigger_send(Msg.objects.filter(broadcast=self, created_on=created_on).select_related('contact', 'contact_urn', 'channel'))

                    # increment our created on so we can load our next batch
                    created_on = created_on + timedelta(seconds=1)

                batch = []

        # commit any remaining objects
        if batch:
            Msg.objects.bulk_create(batch)

            if trigger_send:
                self.org.trigger_send(Msg.objects.filter(broadcast=self, created_on=created_on).select_related('contact', 'contact_urn', 'channel'))

        # for large batches, status is handled externally
        # we do this as with the high concurrency of sending we can run into postgresl deadlocks
        # (this could be our fault, or could be: http://www.postgresql.org/message-id/20140731233051.GN17765@andrew-ThinkPad-X230)
        if not partial_recipients:
            self.status = QUEUED if len(recipients) > 0 else SENT
            self.save(update_fields=('status',))

    def update(self):
        """
        Check the status of our messages and update ours accordingly
        """
        # build a map from status to the count for that status
        statuses = self.get_messages().values('status').order_by('status').annotate(count=Count('status'))
        total = 0
        status_map = dict()
        for status in statuses:
            status_map[status['status']] = status['count']
            total += status['count']

        # if errored msgs are greater than the half of all msgs
        if status_map.get(ERRORED, 0) > total / 2:
            self.status = ERRORED

        # if there are more than half failed, show failed
        elif status_map.get(FAILED, 0) > total / 2:
            self.status = FAILED

        # if there are any in Q, we are Q
        elif status_map.get(QUEUED, 0) or status_map.get(PENDING, 0):
            self.status = QUEUED

        # at this point we are either sent or delivered

        # if there are any messages that are only in a sent state
        elif status_map.get(SENT, 0) or status_map.get(WIRED, 0):
            self.status = SENT

        # otherwise, all messages delivered
        elif status_map.get(DELIVERED, 0) == total:
            self.status = DELIVERED

        self.save(update_fields=['status'])

    def __unicode__(self):
        return "%s (%s)" % (self.org.name, self.pk)


class Msg(models.Model):
    """
    Messages are the main building blocks of a RapidPro application. Channels send and receive
    these, Triggers and Flows handle them when appropriate.

    Messages are either inbound or outbound and can have varying states depending on their
    direction. Generally an outbound message will go through the following states:

      INITIALIZING > QUEUED > WIRED > SENT > DELIVERED

    If things go wrong, they can be put into an ERRORED state where they can be retried. Once
    we've given up then they can be put in the FAILED state.

    Inbound messages are much simpler. They start as PENDING and the can be picked up by Triggers
    or Flows where they would get set to the HANDLED state once they've been dealt with.
    """

    VISIBILITY_CHOICES = ((VISIBLE, _("Visible")),
                          (ARCHIVED, _("Archived")),
                          (DELETED, _("Deleted")))

    DIRECTION_CHOICES = ((INCOMING, _("Incoming")),
                         (OUTGOING, _("Outgoing")))

    MSG_TYPES = ((INBOX, _("Inbox Message")),
                 (FLOW, _("Flow Message")),
                 (IVR, _("IVR Message")))

    org = models.ForeignKey(Org, related_name='msgs', verbose_name=_("Org"),
                            help_text=_("The org this message is connected to"))

    channel = models.ForeignKey(Channel, null=True,
                                related_name='msgs', verbose_name=_("Channel"),
                                help_text=_("The channel object that this message is associated with"))

    contact = models.ForeignKey(Contact,
                                related_name='msgs', verbose_name=_("Contact"),
                                help_text=_("The contact this message is communicating with"))

    contact_urn = models.ForeignKey(ContactURN, null=True,
                                    related_name='msgs', verbose_name=_("Contact URN"),
                                    help_text=_("The URN this message is communicating with"))

    broadcast = models.ForeignKey(Broadcast, null=True, blank=True,
                                  related_name='msgs', verbose_name=_("Broadcast"),
                                  help_text=_("If this message was sent to more than one recipient"))

    text = models.TextField(max_length=640, verbose_name=_("Text"),
                            help_text=_("The actual message content that was sent"))

    priority = models.IntegerField(default=SMS_NORMAL_PRIORITY,
                                   help_text=_("The priority for this message to be sent, higher is higher priority"))

    created_on = models.DateTimeField(verbose_name=_("Created On"), db_index=True,
                                      help_text=_("When this message was created"))

    sent_on = models.DateTimeField(null=True, blank=True, verbose_name=_("Sent On"),
                                   help_text=_("When this message was sent to the endpoint"))

    delivered_on = models.DateTimeField(null=True, blank=True, verbose_name=_("Delivered On"),
                                        help_text=_("When this message was delivered to the final recipient (for incoming messages, when the message was handled)"))

    queued_on = models.DateTimeField(null=True, blank=True, verbose_name=_("Queued On"),
                                     help_text=_("When this message was queued to be sent or handled."))

    direction = models.CharField(max_length=1, choices=DIRECTION_CHOICES, verbose_name=_("Direction"),
                                 help_text=_("The direction for this message, either incoming or outgoing"))

    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default='P', verbose_name=_("Status"), db_index=True,
                              help_text=_("The current status for this message"))

    response_to = models.ForeignKey('Msg', null=True, blank=True, related_name='responses',
                                    verbose_name=_("Response To"),
                                    help_text=_("The message that this message is in reply to"))

    labels = models.ManyToManyField('Label', related_name='msgs', verbose_name=_("Labels"),
                                    help_text=_("Any labels on this message"))

    visibility = models.CharField(max_length=1, choices=VISIBILITY_CHOICES, default=VISIBLE, db_index=True,
                                  verbose_name=_("Visibility"),
                                  help_text=_("The current visibility of this message, either visible, archived or deleted"))

    has_template_error = models.BooleanField(default=False, verbose_name=_("Has Template Error"),
                                             help_text=_("Whether data for variable substitution are missing"))

    msg_type = models.CharField(max_length=1, choices=MSG_TYPES, null=True, verbose_name=_("Message Type"),
                                help_text=_('The type of this message'))

    msg_count = models.IntegerField(default=1, verbose_name=_("Message Count"),
                                    help_text=_("The number of messages that were used to send this message, calculated on Twilio channels"))

    error_count = models.IntegerField(default=0, verbose_name=_("Error Count"),
                                      help_text=_("The number of times this message has errored"))

    next_attempt = models.DateTimeField(auto_now_add=True, verbose_name=_("Next Attempt"),
                                        help_text=_("When we should next attempt to deliver this message"))

    external_id = models.CharField(max_length=255, null=True, blank=True, db_index=True, verbose_name=_("External ID"),
                                   help_text=_("External id used for integrating with callbacks from other APIs"))

    topup = models.ForeignKey(TopUp, null=True, blank=True, related_name='msgs', on_delete=models.SET_NULL,
                              help_text="The topup that this message was deducted from")

    recording_url = models.URLField(null=True, blank=True, max_length=255,
                                    help_text=_("The url for any recording associated with this message"))

    @classmethod
    def send_messages(cls, all_msgs):
        """
        Adds the passed in messages to our sending queue, this will also update the status of the message to
        queued.
        :return:
        """
        # we send in chunks of 1,000 to help with contention
        for msg_chunk in chunk_list(all_msgs, 1000):
            # create a temporary list of our chunk so we can iterate more than once
            msgs = [msg for msg in msg_chunk]

            # build our id list
            msg_ids = set([m.id for m in msgs])

            with transaction.atomic():
                queued_on = timezone.now()

                # update them to queued
                send_messages = Msg.objects.filter(id__in=msg_ids)\
                                           .exclude(channel__channel_type=ANDROID)\
                                           .exclude(msg_type=IVR)\
                                           .exclude(topup=None)\
                                           .exclude(contact__is_test=True)
                send_messages.update(status=QUEUED, queued_on=queued_on)

                # now push each onto our queue
                for msg in msgs:
                    if (msg.msg_type != IVR and msg.channel and msg.channel.channel_type != ANDROID) and \
                            msg.topup and not msg.contact.is_test:
                        # serialize the model to a dictionary
                        msg.queued_on = queued_on
                        task = msg.as_task_json()

                        task_priority = DEFAULT_PRIORITY
                        if msg.priority == SMS_BULK_PRIORITY:
                            task_priority = LOW_PRIORITY
                        elif msg.priority == SMS_HIGH_PRIORITY:
                            task_priority = HIGH_PRIORITY

                        push_task(msg.org, MSG_QUEUE, SEND_MSG_TASK, task, priority=task_priority)

    @classmethod
    def process_message(cls, msg):
        """
        Processes a message, running it through all our handlers
        """
        handlers = get_message_handlers()

        if msg.contact.is_blocked:
            msg.visibility = ARCHIVED
            msg.save(update_fields=['visibility'])
        else:
            for handler in handlers:
                try:
                    start = None
                    if settings.DEBUG:
                        start = time.time()

                    handled = handler.handle(msg)

                    if start:
                        print "[%0.2f] %s for %d" % (time.time() - start, handler.name, msg.pk)

                    if handled:
                        break
                except Exception as e:  # pragma: no cover
                    import traceback
                    traceback.print_exc(e)
                    logger.exception("Error in message handling: %s" % e)

        cls.mark_handled(msg)

        # if this is an inbox message, increment our unread inbox count
        if msg.msg_type == INBOX:
            msg.org.increment_unread_msg_count(UNREAD_INBOX_MSGS)

        # record our handling latency for this object
        if msg.queued_on:
            analytics.track("System", "temba.handling_latency", properties=dict(value=(msg.delivered_on - msg.queued_on).total_seconds()))

        # this is the latency from when the message was received at the channel, which may be different than
        # above if people above us are queueing (or just because clocks are out of sync)
        analytics.track("System", "temba.channel_handling_latency", properties=dict(value=(msg.delivered_on - msg.created_on).total_seconds()))

    @classmethod
    def get_messages(cls, org, is_archived=False, direction=None, msg_type=None):
        messages = Msg.objects.filter(org=org)

        if is_archived:
            messages = messages.filter(visibility=ARCHIVED)
        else:
            messages = messages.filter(visibility=VISIBLE)

        if direction:
            messages = messages.filter(direction=direction)

        if msg_type:
            messages = messages.filter(msg_type=msg_type)

        return messages.filter(contact__is_test=False)

    @classmethod
    def fail_old_messages(cls):
        """
        Looks for any errored or queued messages more than a week old and fails them. Messages that old would
        probably be confusing to go out.
        """
        one_week_ago = timezone.now() - timedelta(days=7)
        failed_messages = Msg.objects.filter(created_on__lte=one_week_ago, direction=OUTGOING,
                                             status__in=[QUEUED, PENDING, ERRORED])

        failed_broadcasts = list(failed_messages.order_by('broadcast').values('broadcast').distinct())

        # fail our messages
        failed_messages.update(status='F')

        # and update all related broadcast statuses
        for broadcast in Broadcast.objects.filter(id__in=[b['broadcast'] for b in failed_broadcasts]):
            broadcast.update()

    @classmethod
    def get_unread_msg_count(cls, user):
        org = user.get_org()

        key = 'org_unread_msg_count_%d' % org.pk
        unread_count = cache.get(key, None)

        if unread_count is None:
            unread_count = Msg.objects.filter(org=org, visibility=VISIBLE, direction=INCOMING, msg_type=INBOX,
                                              contact__is_test=False, created_on__gt=org.msg_last_viewed, labels=None).count()

            cache.set(key, unread_count, 900)

        return unread_count

    @classmethod
    def mark_handled(cls, msg):
        """
        Marks an incoming message as HANDLED
        """
        update_fields = ['status', 'delivered_on']

        # if flows or IVR haven't claimed this message, then it's going to the inbox
        if not msg.msg_type:
            msg.msg_type = INBOX
            update_fields.append('msg_type')

        msg.status = HANDLED
        msg.delivered_on = timezone.now()  # current time as delivery date so we can track created->delivered latency

        # make sure we don't overwrite any async message changes by only saving specific fields
        msg.save(update_fields=update_fields)

    @classmethod
    def mark_error(cls, r, channel, msg, fatal=False):
        """
        Marks an outgoing message as FAILED or ERRORED
        :param msg: a JSON representation of the message or a Msg object
        """
        msg.error_count += 1
        if msg.error_count >= 3 or fatal:
            if isinstance(msg, Msg):
                msg.fail()
            else:
                Msg.objects.select_related('org').get(pk=msg.id).fail()

            if channel:
                analytics.track("System", "temba.msg_failed_%s" % channel.channel_type.lower())
        else:
            msg.status = ERRORED
            msg.next_attempt = timezone.now() + timedelta(minutes=5*msg.error_count)

            if isinstance(msg, Msg):
                msg.save(update_fields=('status', 'next_attempt', 'error_count'))
            else:
                Msg.objects.filter(id=msg.id).update(status=msg.status, next_attempt=msg.next_attempt, error_count=msg.error_count)

            # clear that we tried to send this message (otherwise we'll ignore it when we retry)
            pipe = r.pipeline()
            pipe.srem(timezone.now().strftime(MSG_SENT_KEY), str(msg.id))
            pipe.srem((timezone.now()-timedelta(days=1)).strftime(MSG_SENT_KEY), str(msg.id))
            pipe.execute()

            if channel:
                analytics.track("System", "temba.msg_errored_%s" % channel.channel_type.lower())

    @classmethod
    def mark_sent(cls, r, channel, msg, status, latency, external_id=None):
        """
        Marks an outgoing message as WIRED or SENT
        :param msg: a JSON representation of the message
        """
        msg.status = status
        msg.sent_on = timezone.now()
        if external_id:
            msg.external_id = external_id

        # use redis to mark this message sent
        pipe = r.pipeline()
        sent_key = timezone.now().strftime(MSG_SENT_KEY)
        pipe.sadd(sent_key, str(msg.id))
        pipe.expire(sent_key, 86400)
        pipe.execute()

        if external_id:
            Msg.objects.filter(id=msg.id).update(status=status, sent_on=msg.sent_on, external_id=external_id)
        else:
            Msg.objects.filter(id=msg.id).update(status=status, sent_on=msg.sent_on)

        # record our latency between the message being created and it being sent
        # (this will have some db latency but will still be a good measure in the second-range)

        # hasattr needed here as queued_on being included is new, so some messages may not have the attribute after push
        if getattr(msg, 'queued_on', None):
            analytics.track("System", "temba.sending_latency", properties=dict(value=(msg.sent_on - msg.queued_on).total_seconds()))
        else:
            analytics.track("System", "temba.sending_latency", properties=dict(value=(msg.sent_on - msg.created_on).total_seconds()))

        # logs that a message was sent for this channel type if our latency is known
        if latency > 0:
            analytics.track("System", "temba.msg_sent_%s" % channel.channel_type.lower(), properties=dict(value=latency))

    def as_json(self):
        return dict(direction=self.direction,
                    text=self.text,
                    id=self.id,
                    created_on=self.created_on.strftime('%x %X'),
                    model="msg")

    def simulator_json(self):
        msg_json = self.as_json()
        msg_json['text'] = escape(self.text).replace('\n', "<br/>")
        return msg_json

    @classmethod
    def get_text_parts(cls, text, max_length=160):
        """
        Breaks our message into 160 character parts
        """
        if len(text) < max_length or max_length <= 0:
            return [text]

        else:
            def next_part(text):
                if len(text) <= max_length:
                    return (text, None)

                else:
                    # search for a space to split on, up to 140 characters in
                    index = max_length
                    while index > max_length-20:
                        if text[index] == ' ':
                            break
                        index = index - 1

                    # couldn't find a good split, oh well, 160 it is
                    if index == max_length-20:
                        return (text[:max_length], text[max_length:])
                    else:
                        return (text[:index], text[index+1:])

            parts = []
            rest = text
            while rest:
                (part, rest) = next_part(rest)
                parts.append(part)

            return parts

    def reply(self, text, user, trigger_send=False, message_context=None):
        return self.contact.send(text, user, trigger_send=trigger_send, message_context=message_context,
                                 response_to=self if self.id else None)

    def update(self, cmd):
        """
        Updates our message according to the provided client command
        """
        from temba.api.models import WebHookEvent, SMS_DELIVERED, SMS_SENT, SMS_FAIL
        date = datetime.fromtimestamp(int(cmd['ts']) / 1000).replace(tzinfo=pytz.utc)

        keyword = cmd['cmd']
        handled = False

        if keyword == 'mt_error':
            self.status = ERRORED
            handled = True

        elif keyword == 'mt_fail':
            self.status = FAILED
            handled = True
            WebHookEvent.trigger_sms_event(SMS_FAIL, self, date)

        elif keyword == 'mt_sent':
            self.status = SENT
            self.sent_on = date
            handled = True
            WebHookEvent.trigger_sms_event(SMS_SENT, self, date)

        elif keyword == 'mt_dlvd':
            self.status = DELIVERED
            self.delivered_on = date
            handled = True
            WebHookEvent.trigger_sms_event(SMS_DELIVERED, self, date)

        self.save()  # first save message status before updating the broadcast status

        # update our broadcast if we have one
        if self.broadcast:
            self.broadcast.update()

        return handled

    def handle(self):
        if self.direction == OUTGOING:
            raise ValueError(ugettext("Cannot process an outgoing message."))

        # process Android and test contact messages inline
        if not self.channel or self.channel.channel_type == ANDROID or self.contact.is_test:
            Msg.process_message(self)

        # others do in celery
        else:
            push_task(self.org, HANDLER_QUEUE, HANDLE_EVENT_TASK,
                      dict(type=MSG_EVENT, id=self.id, from_mage=False, new_contact=False))

    def build_message_context(self):
        message_context = dict()
        message_context['__default__'] = self.text

        message_context['contact'] = self.contact.build_message_context()

        message_context['value'] = self.text
        message_context['time'] = self.created_on

        return message_context

    def resend(self):
        """
        Resends this message by creating a clone and triggering a send of that clone
        """
        topup_id = self.org.decrement_credit()  # costs 1 credit to resend message

        # see if we should use a new channel
        channel = self.org.get_send_channel(contact_urn=self.contact_urn)

        cloned = Msg.objects.create(org=self.org,
                                    channel=channel,
                                    contact=self.contact,
                                    contact_urn=self.contact_urn,
                                    created_on=timezone.now(),
                                    text=self.text,
                                    response_to=self.response_to,
                                    direction=self.direction,
                                    topup_id=topup_id,
                                    status=PENDING,
                                    broadcast=self.broadcast)

        # mark ourselves as resent
        self.status = RESENT
        self.topup = None
        self.save()

        # update our broadcast
        if cloned.broadcast:
            cloned.broadcast.update()

        # send our message
        self.org.trigger_send([cloned])

    def get_flow_step(self):
        if self.msg_type not in (FLOW, IVR):
            return None

        steps = list(self.steps.all())  # steps may have been pre-fetched
        return steps[0] if steps else None

    def get_flow_id(self):
        step = self.get_flow_step()
        return step.run.flow_id if step else None

    def get_flow_name(self):
        step = self.get_flow_step()
        return step.run.flow.name if step else ""

    def as_task_json(self):
        """
        Used internally to serialize to JSON when queueing messages in Redis
        """
        return dict(id=self.id, org=self.org_id, channel=self.channel_id, broadcast=self.broadcast_id,
                    text=self.text, urn_path=self.contact_urn.path,
                    contact=self.contact_id, contact_urn=self.contact_urn_id,
                    priority=self.priority, error_count=self.error_count, next_attempt=self.next_attempt,
                    status=self.status, direction=self.direction,
                    external_id=self.external_id,
                    sent_on=self.sent_on, queued_on=self.queued_on,
                    created_on=self.created_on, delivered_on=self.delivered_on)

    def __unicode__(self):
        return self.text

    @classmethod
    def create_incoming(cls, channel, urn, text, user=None, date=None, org=None, contact=None,
                        status=PENDING, recording_url=None, msg_type=None, topup=None):

        from temba.api.models import WebHookEvent, SMS_RECEIVED
        if not org and channel:
            org = channel.org

        if not org:
            raise Exception(_("Can't create an incoming message without an org"))

        if not user:
            user = User.objects.get(pk=settings.ANONYMOUS_USER_ID)

        if not date:
            date = timezone.now()  # no date?  set it to now

        if not contact:
            contact = Contact.get_or_create(org, user, name=None, urns=[urn], incoming_channel=channel)
            contact_urn = contact.urn_objects[urn]
        else:
            contact_urn = None

        existing = Msg.objects.filter(text=text, created_on=date, contact=contact, direction='I').first()
        if existing:
            return existing

        # costs 1 credit to receive a message
        topup_id = None
        if topup:
            topup_id = topup.pk
        elif not contact.is_test:
            topup_id = org.decrement_credit()

        # we limit text messages to 640 characters
        text = text[:640]

        msg_args = dict(contact=contact,
                        contact_urn=contact_urn,
                        org=org,
                        channel=channel,
                        text=text,
                        created_on=date,
                        queued_on=timezone.now(),
                        direction=INCOMING,
                        msg_type=msg_type,
                        recording_url=recording_url,
                        status=status)

        if topup_id is not None:
            msg_args['topup_id'] = topup_id

        msg = Msg.objects.create(**msg_args)

        if channel:
            analytics.track('System', 'temba.msg_incoming_%s' % channel.channel_type.lower())

        if status == PENDING:
            msg.handle()

            # fire an event off for this message
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, msg, date)

        return msg

    @classmethod
    def substitute_variables(cls, text, contact, message_context, org=None, url_encode=False, partial_vars=False):
        """
        Given input ```text```, tries to find variables in the format @foo.bar and replace them according to
        the passed in context, contact and org. If some variables are not resolved to values, then the variable
        name will remain (ie, @foo.bar).

        Returns a tuple of the substituted text and whether there were are substitution failures.
        """
        # shortcut for cases where there is no way we would substitute anything as there are no variables
        if not text or text.find('@') < 0:
            return text, []

        if contact:
            message_context['contact'] = contact.build_message_context()

        # add 'step.contact' if it isn't already populated (like in flow batch starts)
        if 'step' not in message_context or 'contact' not in message_context['step']:
            message_context['step'] = dict(contact=message_context['contact'])

        if not org:
            dayfirst = True
            tz = timezone.get_current_timezone()
        else:
            dayfirst = org.get_dayfirst()
            tz = org.get_tzinfo()

        (format_date, format_time) = get_datetime_format(dayfirst)

        date_context = dict()
        date_context['__default__'] = datetime_to_str(timezone.now(), format=format_time, tz=tz)
        date_context['now'] = datetime_to_str(timezone.now(), format=format_time, tz=tz)
        date_context['today'] = datetime_to_str(timezone.now(), format=format_date, tz=tz)
        date_context['tomorrow'] = datetime_to_str(timezone.now() + timedelta(days=1), format=format_date, tz=tz)
        date_context['yesterday'] = datetime_to_str(timezone.now() - timedelta(days=1), format=format_date, tz=tz)

        message_context['date'] = date_context

        date_style = DateStyle.DAY_FIRST if dayfirst else DateStyle.MONTH_FIRST
        context = EvaluationContext(message_context, tz, date_style)

        # returns tuple of output and errors
        return evaluate_template(text, context, url_encode, partial_vars)

    @classmethod
    def create_outgoing(cls, org, user, recipient, text, broadcast=None, channel=None, priority=SMS_NORMAL_PRIORITY,
                        created_on=None, response_to=None, message_context=None, status=PENDING, insert_object=True,
                        recording_url=None, topup_id=None, msg_type=INBOX):

        if not org or not user:  # pragma: no cover
            raise ValueError("Trying to create outgoing message with no org or user")

        # for IVR messages we need a channel that can call
        role = CALL if msg_type == IVR else SEND

        if status != SENT:
            # if message will be sent, resolve the recipient to a contact and URN
            contact, contact_urn = cls.resolve_recipient(org, user, recipient, channel, role=role)

            if not contact_urn:
                raise UnreachableException("No suitable URN found for contact")

            if not channel:
                if msg_type == IVR:
                    channel = org.get_call_channel()
                else:
                    channel = org.get_send_channel(contact_urn=contact_urn)

                if not channel and not contact.is_test:
                    raise ValueError("No suitable channel available for this org")
        else:
            # if message has already been sent, recipient must be a tuple of contact and URN
            contact, contact_urn = recipient

        # no creation date?  set it to now
        if not created_on:
            created_on = timezone.now()

        # substitute variables in the text messages
        if not message_context:
            message_context = dict()

        (text, errors) = Msg.substitute_variables(text, contact, message_context, org=org)

        # if we are doing a single message, check whether this might be a loop of some kind
        if insert_object:
            # prevent the loop of message while the sending phone is the channel
            # get all messages with same text going to same number
            same_msgs = Msg.objects.filter(contact_urn=contact_urn,
                                           contact__is_test=False,
                                           channel=channel,
                                           recording_url=recording_url,
                                           text=text,
                                           direction=OUTGOING,
                                           created_on__gte=created_on - timedelta(minutes=10))

            # we aren't considered with robo detection on calls
            same_msg_count = same_msgs.exclude(msg_type=IVR).count()

            channel_id = channel.pk if channel else None

            if same_msg_count >= 10:
                analytics.track('System', "temba.msg_loop_caught", dict(org=org.pk, channel=channel_id))
                return None

            # be more aggressive about short codes for duplicate messages
            # we don't want machines talking to each other
            tel = contact.raw_tel()
            if tel and len(tel) < 6:
                same_msg_count = Msg.objects.filter(contact_urn=contact_urn,
                                                    contact__is_test=False,
                                                    channel=channel,
                                                    text=text,
                                                    direction=OUTGOING,
                                                    created_on__gte=created_on - timedelta(hours=24)).count()
                if same_msg_count >= 10:
                    analytics.track('System', "temba.msg_shortcode_loop_caught", dict(org=org.pk, channel=channel_id))
                    return None

        # costs 1 credit to send a message
        if not topup_id and not contact.is_test:
            topup_id = org.decrement_credit()

        if response_to:
            msg_type = response_to.msg_type

        text = text.strip()

        # track this if we have a channel
        if channel:
            analytics.track('System', 'temba.msg_outgoing_%s' % channel.channel_type.lower())

        msg_args = dict(contact=contact,
                        contact_urn=contact_urn,
                        org=org,
                        channel=channel,
                        text=text,
                        created_on=created_on,
                        direction=OUTGOING,
                        status=status,
                        broadcast=broadcast,
                        response_to=response_to,
                        msg_type=msg_type,
                        priority=priority,
                        recording_url=recording_url,
                        has_template_error=len(errors) > 0)

        if topup_id is not None:
            msg_args['topup_id'] = topup_id

        return Msg.objects.create(**msg_args) if insert_object else Msg(**msg_args)

    @staticmethod
    def resolve_recipient(org, user, recipient, channel, role=SEND):
        """
        Recipient can be a contact, a URN object, or a URN tuple, e.g. ('tel', '123'). Here we resolve the contact and
        contact URN to use for an outgoing message.
        """
        contact = None
        contact_urn = None

        resolved_schemes = {channel.scheme} if channel else org.get_schemes(role)

        if isinstance(recipient, Contact):
            if recipient.is_test:
                contact = recipient
                contact_urn = contact.urns.all().first()
            else:
                contact = recipient
                contact_urn = contact.get_urn(schemes=resolved_schemes)  # use highest priority URN we can send to
        elif isinstance(recipient, ContactURN):
            if recipient.scheme in resolved_schemes:
                contact = recipient.contact
                contact_urn = recipient
        elif isinstance(recipient, tuple) and len(recipient) == 2:
            if recipient[0] in resolved_schemes:
                contact = Contact.get_or_create(org, user, urns=[recipient])
                contact_urn = contact.urn_objects[recipient]
        else:  # pragma: no cover
            raise ValueError("Message recipient must be a Contact, ContactURN or URN tuple")

        return contact, contact_urn

    def fail(self):
        """
        Fails this message, provided it is currently not failed
        """
        self.status = FAILED
        self.save(update_fields=('status',))

        Channel.track_status(self.channel, "Failed")

    def status_sent(self):
        """
        Update the message status to SENT
        """
        self.status = SENT
        self.sent_on = timezone.now()
        self.save(update_fields=('status', 'sent_on'))
        Channel.track_status(self.channel, "Sent")

    def status_delivered(self):
        """
        Update the message status to DELIVERED
        """
        self.status = DELIVERED
        self.delivered_on = timezone.now()
        if not self.sent_on:
            self.sent_on = timezone.now()
        self.save(update_fields=('status', 'delivered_on', 'sent_on'))
        Channel.track_status(self.channel, "Delivered")

    def archive(self):
        """
        Archives this message
        """
        if self.direction != INCOMING or self.contact.is_test:
            raise ValueError("Can only archive incoming non-test messages")

        self.visibility = ARCHIVED
        self.save(update_fields=('visibility',))

    def restore(self):
        """
        Restores (i.e. un-archives) this message
        """
        if self.direction != INCOMING or self.contact.is_test:
            raise ValueError("Can only restore incoming non-test messages")

        self.visibility = VISIBLE
        self.save(update_fields=('visibility',))

    def release(self):
        """
        Releases (i.e. deletes) this message
        """
        self.visibility = DELETED
        self.text = ""
        self.save(update_fields=('visibility', 'text'))

        # remove labels
        self.labels.clear()

    @classmethod
    def apply_action_label(cls, msgs, label, add):
        return label.toggle_label(msgs, add)

    @classmethod
    def apply_action_archive(cls, msgs):
        changed = []

        for msg in msgs:
            msg.archive()
            changed.append(msg.pk)
        return changed

    @classmethod
    def apply_action_restore(cls, msgs):
        changed = []

        for msg in msgs:
            msg.restore()
            changed.append(msg.pk)
        return changed

    @classmethod
    def apply_action_delete(cls, msgs):
        changed = []

        for msg in msgs:
            msg.release()
            changed.append(msg.pk)
        return changed

    @classmethod
    def apply_action_resend(cls, msgs):
        changed = []

        for msg in msgs:
            msg.resend()
            changed.append(msg.pk)
        return changed

    class Meta:
        ordering = ['-created_on', '-pk']

class Call(SmartModel):
    """
    Call represents a inbound, outobound, or missed call on an Android Channel. When such an event occurs
    on an Android Phone with the Channel application installed, the calls are relayed to the server much
    the same way incoming messages are.

    Note: These are not related to calls made for voice-based flows.
    """
    TYPE_UNKNOWN = 'unk'
    TYPE_OUT = 'mt_call'
    TYPE_OUT_MISSED = 'mt_miss'
    TYPE_IN = 'mo_call'
    TYPE_IN_MISSED = 'mo_miss'

    CALL_TYPES = ((TYPE_UNKNOWN, _("Unknown Call Type")),
                  (TYPE_IN, _("Incoming Call")),
                  (TYPE_IN_MISSED, _("Missed Incoming Call")),
                  (TYPE_OUT, _("Outgoing Call")),
                  (TYPE_OUT_MISSED, _("Missed Outgoing Call")))

    org = models.ForeignKey(Org, verbose_name=_("Org"), help_text=_("The org this call is connected to"))

    channel = models.ForeignKey(Channel,
                                null=True, verbose_name=_("Channel"),
                                help_text=_("The channel where this call took place"))
    contact = models.ForeignKey(Contact, verbose_name=_("Contact"), related_name='calls',
                                help_text=_("The phone number for this call"))
    time = models.DateTimeField(verbose_name=_("Time"), help_text=_("When this call took place"))
    duration = models.IntegerField(default=0, verbose_name=_("Duration"),
                                   help_text=_("The duration of this call in seconds, if appropriate"))
    call_type = models.CharField(max_length=16, choices=CALL_TYPES,
                                 verbose_name=_("Call Type"), help_text=_("The type of call"))

    @classmethod
    def create_call(cls, channel, phone, date, duration, call_type, user=None):
        from temba.api.models import WebHookEvent
        from temba.triggers.models import Trigger

        if not user:
            user = User.objects.get(pk=settings.ANONYMOUS_USER_ID)

        contact = Contact.get_or_create(channel.org, user, name=None, urns=[(TEL_SCHEME, phone)],
                                        incoming_channel=channel)

        call = Call.objects.create(channel=channel,
                                   org=channel.org,
                                   contact=contact,
                                   time=date,
                                   duration=duration,
                                   call_type=call_type,
                                   created_by=user,
                                   modified_by=user)

        analytics.track('System', 'temba.call_%s' % call.get_call_type_display().lower(), dict(channel_type=channel.get_channel_type_display()))

        WebHookEvent.trigger_call_event(call)

        if call_type == Call.TYPE_IN_MISSED:
            Trigger.catch_triggers(call, Trigger.TYPE_MISSED_CALL, channel)

        return call

    @classmethod
    def get_calls(cls, org):
        return Call.objects.filter(org=org)

    def release(self):
        self.is_active = False
        self.save(update_fields=('is_active',))


STOP_WORDS = 'a,able,about,across,after,all,almost,also,am,among,an,and,any,are,as,at,be,because,been,but,by,can,cannot,could,dear,did,do,does,either,else,ever,every,for,from,get,got,had,has,have,he,her,hers,him,his,how,however,i,if,in,into,is,it,its,just,least,let,like,likely,may,me,might,most,must,my,neither,no,nor,not,of,off,often,on,only,or,other,our,own,rather,said,say,says,she,should,since,so,some,than,that,the,their,them,then,there,these,they,this,tis,to,too,twas,us,wants,was,we,were,what,when,where,which,while,who,whom,why,will,with,would,yet,you,your'.split(',')


class SystemLabel(models.Model):
    """
    Counts of messages/broadcasts/calls maintained by database level triggers
    """
    TYPE_INBOX = 'I'
    TYPE_FLOWS = 'W'
    TYPE_ARCHIVED = 'A'
    TYPE_OUTBOX = 'O'
    TYPE_SENT = 'S'
    TYPE_FAILED = 'X'
    TYPE_SCHEDULED = 'E'
    TYPE_CALLS = 'C'

    TYPE_CHOICES = ((TYPE_INBOX, "Inbox"),
                    (TYPE_FLOWS, "Flows"),
                    (TYPE_ARCHIVED, "Archived"),
                    (TYPE_OUTBOX, "Outbox"),
                    (TYPE_SENT, "Sent"),
                    (TYPE_FAILED, "Failed"),
                    (TYPE_SCHEDULED, "Scheduled"),
                    (TYPE_CALLS, "Calls"))

    org = models.ForeignKey(Org, related_name='system_labels')

    label_type = models.CharField(max_length=1, choices=TYPE_CHOICES)

    count = models.IntegerField(default=0, help_text=_("Number of items with this system label"))

    @classmethod
    def create_all(cls, org):
        """
        Creates all system labels for the given org
        """
        labels = []
        for label_type, _name in cls.TYPE_CHOICES:
            labels.append(cls.objects.create(org=org, label_type=label_type))
        return labels

    @classmethod
    def get_queryset(cls, org, label_type):
        """
        Gets the queryset for the given system label. Any change here needs to be reflected in a change to the db
        trigger used to maintain the label counts.
        """
        if label_type == cls.TYPE_INBOX:
            qs = Msg.objects.filter(direction=INCOMING, visibility=VISIBLE, msg_type=INBOX)
        elif label_type == cls.TYPE_FLOWS:
            qs = Msg.objects.filter(direction=INCOMING, visibility=VISIBLE, msg_type=FLOW)
        elif label_type == cls.TYPE_ARCHIVED:
            qs = Msg.objects.filter(direction=INCOMING, visibility=ARCHIVED)
        elif label_type == cls.TYPE_OUTBOX:
            qs = Msg.objects.filter(direction=OUTGOING, visibility=VISIBLE, status__in=(PENDING, QUEUED))
        elif label_type == cls.TYPE_SENT:
            qs = Msg.objects.filter(direction=OUTGOING, visibility=VISIBLE, status__in=(WIRED, SENT, DELIVERED))
        elif label_type == cls.TYPE_FAILED:
            qs = Msg.objects.filter(direction=OUTGOING, visibility=VISIBLE, status=FAILED)
        elif label_type == cls.TYPE_SCHEDULED:
            qs = Broadcast.objects.exclude(schedule=None)
        elif label_type == cls.TYPE_CALLS:
            qs = Call.objects.filter(is_active=True)
        else:
            raise ValueError("Invalid label type: %s" % label_type)

        qs = qs.filter(org=org)

        if label_type == cls.TYPE_SCHEDULED:
            qs = qs.exclude(contacts__is_test=True)
        else:
            qs = qs.exclude(contact__is_test=True)

        return qs

    @classmethod
    def get_counts(cls, org, label_types=None):
        """
        Gets all system label counts by type for the given org
        """
        labels = cls.objects.filter(org=org)
        if label_types:
            labels = labels.filter(label_type__in=label_types)
        label_counts = labels.values('label_type').order_by('label_type').annotate(count_sum=Sum('count'))

        return {l['label_type']: l['count_sum'] for l in label_counts}

    class Meta:
        index_together = ('org', 'label_type')


class UserFolderManager(models.Manager):
    def get_queryset(self):
        return super(UserFolderManager, self).get_queryset().filter(label_type=Label.TYPE_FOLDER)


class UserLabelManager(models.Manager):
    def get_queryset(self):
        return super(UserLabelManager, self).get_queryset().filter(label_type=Label.TYPE_LABEL)


class Label(TembaModel, SmartModel):
    """
    Labels represent both user defined labels and folders of labels. User defined labels that can be applied to messages
    much the same way labels or tags apply to messages in web-based email services.
    """
    MAX_NAME_LEN = 64

    TYPE_FOLDER = 'F'
    TYPE_LABEL = 'L'

    TYPE_CHOICES = ((TYPE_FOLDER, "Folder of labels"),
                    (TYPE_LABEL, "Regular label"))

    org = models.ForeignKey(Org)

    name = models.CharField(max_length=MAX_NAME_LEN, verbose_name=_("Name"), help_text=_("The name of this label"))

    folder = models.ForeignKey('Label', verbose_name=_("Folder"), null=True, related_name="children")

    label_type = models.CharField(max_length=1, choices=TYPE_CHOICES, default=TYPE_LABEL, help_text=_("Label type"))

    visible_count = models.PositiveIntegerField(default=0,
                                                help_text=_("Number of non-archived messages with this label"))

    # define some custom managers to do the filtering of label types for us
    all_objects = models.Manager()
    folder_objects = UserFolderManager()
    label_objects = UserLabelManager()

    @classmethod
    def get_or_create(cls, org, user, name, folder=None):
        name = name.strip()

        if not cls.is_valid_name(name):
            raise ValueError("Invalid label name: %s" % name)

        if folder and not folder.is_folder():
            raise ValueError("%s is not a label folder" % unicode(folder))

        label = cls.label_objects.filter(org=org, name__iexact=name).first()
        if label:
            return label

        return cls.label_objects.create(org=org, name=name, folder=folder, created_by=user, modified_by=user)

    @classmethod
    def get_or_create_folder(cls, org, user, name):
        name = name.strip()

        if not cls.is_valid_name(name):
            raise ValueError("Invalid folder name: %s" % name)

        folder = cls.folder_objects.filter(org=org, name__iexact=name).first()
        if folder:
            return folder

        return cls.folder_objects.create(org=org, name=name, label_type=Label.TYPE_FOLDER,
                                       created_by=user, modified_by=user)

    @classmethod
    def get_hierarchy(cls, org):
        """
        Gets top-level user labels and folders, with children pre-fetched and ordered by name
        """
        qs = Label.all_objects.filter(org=org).order_by('name')
        qs = qs.filter(Q(label_type=cls.TYPE_LABEL, folder=None) | Q(label_type=cls.TYPE_FOLDER))

        children_prefetch = Prefetch('children', queryset=Label.all_objects.order_by('name'))

        return qs.select_related('folder').prefetch_related(children_prefetch)

    @classmethod
    def is_valid_name(cls, name):
        # don't allow empty strings, blanks, initial or trailing whitespace
        if not name or name.strip() != name:
            return False

        if len(name) > cls.MAX_NAME_LEN:
            return False

        # first character must be a word char
        return regex.match('\w', name[0], flags=regex.UNICODE)

    def filter_messages(self, queryset):
        if self.is_folder():
            return queryset.filter(labels__in=self.children.all()).distinct()

        return queryset.filter(labels=self)

    def get_messages(self):
        return self.filter_messages(Msg.objects.all())

    def get_visible_count(self):
        """
        Returns the count of visible, non-test message tagged with this label
        """
        if self.is_folder():
            raise ValueError("Message counts are not tracked for user folders")

        return self.visible_count

    def toggle_label(self, msgs, add):
        """
        Adds or removes this label from the given messages
        """
        if self.is_folder():
            raise ValueError("Can only assign messages to user labels")

        changed = set()

        for msg in msgs:
            if msg.direction != INCOMING:
                raise ValueError("Can only apply labels to incoming messages")

            if msg.contact.is_test:
                raise ValueError("Cannot apply labels to test messages")

            # if we are adding the label and this message doesnt have it, add it
            if add:
                if not msg.labels.filter(pk=self.pk):
                    msg.labels.add(self)
                    changed.add(msg.pk)

            # otherwise, remove it if not already present
            else:
                if msg.labels.filter(pk=self.pk):
                    msg.labels.remove(self)
                    changed.add(msg.pk)

        return changed

    def is_folder(self):
        return self.label_type == Label.TYPE_FOLDER

    def __unicode__(self):
        if self.folder:
            return "%s > %s" % (unicode(self.folder), self.name)
        return self.name

    class Meta:
        unique_together = ('org', 'name')


class MsgIterator(object):
    """
    Queryset wrapper to chunk queries and reduce in-memory footprint
    """
    def __init__(self, ids, order_by=None, select_related=None, prefetch_related=None, max_obj_num=1000):
        self._ids = ids
        self._order_by = order_by
        self._select_related = select_related
        self._prefetch_related = prefetch_related
        self._generator = self._setup()
        self.max_obj_num = max_obj_num

    def _setup(self):
        for i in xrange(0, len(self._ids), self.max_obj_num):
            chunk_queryset = Msg.objects.filter(id__in=self._ids[i:i+self.max_obj_num])

            if self._order_by:
                chunk_queryset = chunk_queryset.order_by(*self._order_by)

            if self._select_related:
                chunk_queryset = chunk_queryset.select_related(*self._select_related)

            if self._prefetch_related:
                chunk_queryset = chunk_queryset.prefetch_related(*self._prefetch_related)

            for obj in chunk_queryset:
                yield obj

    def __iter__(self):
        return self

    def next(self):
        return self._generator.next()

class ExportMessagesTask(SmartModel):
    """
    Wrapper for handling exports of raw messages. This will export all selected messages in
    an Excel spreadsheet, adding sheets as necessary to fall within the guidelines of Excel 97
    (the library we depend on requires this) which has column and row size limits.

    When the export is done, we store the file on the server and send an e-mail notice with a
    link to download the results.
    """
    org = models.ForeignKey(Org, help_text=_("The organization of the user."))

    groups = models.ManyToManyField(ContactGroup)

    label = models.ForeignKey(Label, null=True)

    start_date = models.DateField(null=True, blank=True, help_text=_("The date for the oldest message to export"))

    end_date = models.DateField(null=True, blank=True, help_text=_("The date for the newest message to export"))

    host = models.CharField(max_length=32, help_text=_("The host this export task was created on"))

    task_id = models.CharField(null=True, max_length=64)

    is_finished = models.BooleanField(default=False, help_text=_("Whether this export is finished running"))

    uuid = models.CharField(max_length=36, null=True, help_text=_("The uuid used to name the resulting export file"))

    def start_export(self):
        """
        Starts our export, wrapping it in a try block to make sure we mark it as finished when complete.
        """
        try:
            start = time.time()
            self.do_export()
        finally:
            elapsed = time.time() - start
            analytics.track(self.created_by.username, 'temba.msg_export_latency', properties=dict(value=elapsed))

            self.is_finished = True
            self.save(update_fields=['is_finished'])

    def do_export(self):
        from xlwt import Workbook, XFStyle
        book = Workbook()

        date_style = XFStyle()
        date_style.num_format_str = 'DD-MM-YYYY HH:MM:SS'

        fields = ['Date', 'Contact', 'Contact Type', 'Name', 'Direction', 'Text', 'Labels']

        all_messages = Msg.get_messages(self.org).order_by('-created_on')

        if self.start_date:
            start_date = datetime.combine(self.start_date, datetime.min.time()).replace(tzinfo=self.org.get_tzinfo())
            all_messages = all_messages.filter(created_on__gte=start_date)

        if self.end_date:
            end_date = datetime.combine(self.end_date, datetime.max.time()).replace(tzinfo=self.org.get_tzinfo())
            all_messages = all_messages.filter(created_on__lte=end_date)

        if self.groups.all():
            all_messages = all_messages.filter(contact__all_groups__in=self.groups.all())

        if self.label:
            all_messages = all_messages.filter(labels=self.label)

        all_message_ids = [m['id'] for m in all_messages.values('id')]

        messages_sheet_number = 1

        current_messages_sheet = book.add_sheet(unicode(_("Messages %d" % messages_sheet_number)))
        for col in range(len(fields)):
            field = fields[col]
            current_messages_sheet.write(0, col, unicode(field))

        row = 1
        processed = 0
        start = time.time()

        prefetch = Prefetch('labels', queryset=Label.label_objects.order_by('name'))
        for msg in MsgIterator(all_message_ids,
                               order_by=[''
                                         '-created_on'],
                               select_related=['contact', 'contact_urn'],
                               prefetch_related=[prefetch]):

            if row >= 65535:
                messages_sheet_number += 1
                current_messages_sheet = book.add_sheet(unicode(_("Messages %d" % messages_sheet_number)))
                for col in range(len(fields)):
                    field = fields[col]
                    current_messages_sheet.write(0, col, unicode(field))
                row = 1

            contact_name = msg.contact.name if msg.contact.name else ''
            created_on = msg.created_on.astimezone(pytz.utc).replace(tzinfo=None)
            msg_labels = ", ".join(msg_label.name for msg_label in msg.labels.all())

            # only show URN path if org isn't anon and there is a URN
            if self.org.is_anon:
                urn_path = msg.contact.anon_identifier
            elif msg.contact_urn:
                urn_path = msg.contact_urn.get_display(org=self.org, full=True)
            else:
                urn_path = ''

            urn_scheme = msg.contact_urn.scheme if msg.contact_urn else ''

            current_messages_sheet.write(row, 0, created_on, date_style)
            current_messages_sheet.write(row, 1, urn_path)
            current_messages_sheet.write(row, 2, urn_scheme)
            current_messages_sheet.write(row, 3, contact_name)
            current_messages_sheet.write(row, 4, msg.get_direction_display())
            current_messages_sheet.write(row, 5, msg.text)
            current_messages_sheet.write(row, 6, msg_labels)
            row += 1
            processed += 1

            if processed % 10000 == 0:
                current_messages_sheet.flush_row_data()
                print "Export of %d msgs for %s - %d%% complete in %0.2fs" % \
                      (len(all_message_ids), self.org.name, processed * 100 / len(all_message_ids), time.time() - start)

        temp = NamedTemporaryFile(delete=True)
        book.save(temp)
        temp.flush()

        self.uuid = str(uuid4())
        self.save(update_fields=['uuid'])

        # save as file asset associated with this task
        from temba.assets.models import AssetType
        from temba.assets.views import get_asset_url

        store = AssetType.message_export.store
        store.save(self.pk, File(temp), 'xls')

        from temba.middleware import BrandingMiddleware
        branding = BrandingMiddleware.get_branding_for_host(self.host)

        subject = "Your messages export is ready"
        template = 'msgs/email/msg_export_download'
        download_url = branding['link'] + get_asset_url(AssetType.message_export, self.pk)

        # force a gc
        import gc
        gc.collect()

        send_template_email(self.created_by.username, subject, template, dict(link=download_url), branding)
