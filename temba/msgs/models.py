from __future__ import unicode_literals

import json
import logging
import pytz
import regex
import time
import traceback

from datetime import datetime, timedelta
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files import File
from django.core.files.temp import NamedTemporaryFile
from django.db import models, transaction, connection
from django.db.models import Q, Count, Prefetch, Sum
from django.utils import timezone
from django.utils.html import escape
from django.utils.translation import ugettext, ugettext_lazy as _
from temba_expressions.evaluator import EvaluationContext, DateStyle
from redis_cache import get_redis_connection
from smartmin.models import SmartModel
from temba.contacts.models import Contact, ContactGroup, ContactURN, URN, TEL_SCHEME
from temba.channels.models import Channel, ChannelEvent
from temba.orgs.models import Org, TopUp, Language, UNREAD_INBOX_MSGS
from temba.schedules.models import Schedule
from temba.utils import get_datetime_format, datetime_to_str, analytics, chunk_list
from temba.utils.cache import get_cacheable_attr
from temba.utils.email import send_template_email
from temba.utils.expressions import evaluate_template
from temba.utils.models import TembaModel
from temba.utils.queues import DEFAULT_PRIORITY, push_task, LOW_PRIORITY, HIGH_PRIORITY
from uuid import uuid4
from .handler import MessageHandler

logger = logging.getLogger(__name__)
__message_handlers = None

MSG_QUEUE = 'msgs'
SEND_MSG_TASK = 'send_msg_task'

HANDLER_QUEUE = 'handler'
HANDLE_EVENT_TASK = 'handle_event_task'
MSG_EVENT = 'msg'
FIRE_EVENT = 'fire'
TIMEOUT_EVENT = 'timeout'

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
INTERRUPTED = 'X'

INCOMING = 'I'
OUTGOING = 'O'

INBOX = 'I'
FLOW = 'F'
IVR = 'V'

MSG_SENT_KEY = 'msgs_sent_%y_%m_%d'

# status codes used for both messages and broadcasts (single char constant, human readable, API readable)
STATUS_CONFIG = (
    # special state for flows used to hold off sending the message until the flow is ready to receive a response
    (INITIALIZING, _("Initializing"), 'initializing'),

    (PENDING, _("Pending"), 'pending'),        # initial state for all messages

    # valid only for outgoing messages
    (QUEUED, _("Queued"), 'queued'),
    (WIRED, _("Wired"), 'wired'),              # message was handed off to the provider and credits were deducted for it
    (SENT, _("Sent"), 'sent'),                 # we have confirmation that a message was sent
    (DELIVERED, _("Delivered"), 'delivered'),

    # valid only for incoming messages
    (HANDLED, _("Handled"), 'handled'),

    (ERRORED, _("Error Sending"), 'errored'),  # there was an error during delivery
    (FAILED, _("Failed Sending"), 'failed'),   # we gave up on sending this message
    (RESENT, _("Resent message"), 'resent'),   # we retried this message

    (INTERRUPTED, _("Interrupt message"), 'interrupted'),   # we were interrupted, ie, ussd termination
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
    STATUS_CHOICES = [(s[0], s[1]) for s in STATUS_CONFIG]

    BULK_THRESHOLD = 50  # use bulk priority for messages if number of recipients greater than this

    org = models.ForeignKey(Org, verbose_name=_("Org"),
                            help_text=_("The org this broadcast is connected to"))

    groups = models.ManyToManyField(ContactGroup, verbose_name=_("Groups"), related_name='addressed_broadcasts',
                                    help_text=_("The groups to send the message to"))

    contacts = models.ManyToManyField(Contact, verbose_name=_("Contacts"), related_name='addressed_broadcasts',
                                      help_text=_("Individual contacts included in this message"))

    urns = models.ManyToManyField(ContactURN, verbose_name=_("URNs"), related_name='addressed_broadcasts',
                                  help_text=_("Individual URNs included in this message"))

    recipients = models.ManyToManyField(Contact, verbose_name=_("Recipients"), related_name='broadcasts',
                                        help_text=_("The contacts which received this message"))

    recipient_count = models.IntegerField(verbose_name=_("Number of recipients"), null=True,
                                          help_text=_("Number of urns which received this broadcast"))

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

    purged = models.NullBooleanField(default=False,
                                     help_text="If the messages for this broadcast have been purged")

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

        # cache on object for use in subsequent send(..) calls
        delattr(self, '_recipient_cache')

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

    def get_preferred_languages(self, contact, base_language=None, org=None):
        """
        Gets the ordered list of language preferences for the given contact
        """
        org = org or self.org  # org object can be provided to allow caching of org languages
        preferred_languages = []

        if org.primary_language:
            preferred_languages.append(org.primary_language.iso_code)

        if base_language:
            preferred_languages.append(base_language)

        # if contact has a language and it's a valid org language, it has priority
        if contact.language and contact.language in org.get_language_codes():
            preferred_languages = [contact.language] + preferred_languages

        return preferred_languages

    def get_translations(self):
        if not self.language_dict:
            return []
        return get_cacheable_attr(self, '_translations', lambda: json.loads(self.language_dict))

    def get_translated_text(self, contact, base_language=None, org=None):
        """
        Gets the appropriate translation for the given contact. base_language may be provided
        """
        translations = self.get_translations()
        preferred_languages = self.get_preferred_languages(contact, base_language, org)
        return Language.get_localized_text(translations, preferred_languages, self.text)

    def send(self, trigger_send=True, message_context=None, response_to=None, status=PENDING, msg_type=INBOX,
             created_on=None, base_language=None, partial_recipients=None, run_map=None):
        """
        Sends this broadcast by creating outgoing messages for each recipient.
        """
        # ignore mock messages
        if response_to and not response_to.id:
            response_to = None

        # cannot ask for sending by us AND specify a created on, blow up in that case
        if trigger_send and created_on:  # pragma: no cover
            raise ValueError("Cannot trigger send and specify a created_on, breaks creating batches")

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

        RelatedRecipient = Broadcast.recipients.through

        # we batch up our SQL calls to speed up the creation of our SMS objects
        batch = []
        recipient_batch = []

        # our priority is based on the number of recipients
        priority = Msg.PRIORITY_NORMAL
        if len(recipients) == 1:
            priority = Msg.PRIORITY_HIGH
        elif len(recipients) >= self.BULK_THRESHOLD:
            priority = Msg.PRIORITY_BULK

        # if they didn't pass in a created on, create one ourselves
        if not created_on:
            created_on = timezone.now()

        # pre-fetch channels to reduce database hits
        org = Org.objects.filter(pk=self.org.id).prefetch_related('channels').first()

        for recipient in recipients:
            contact = recipient if isinstance(recipient, Contact) else recipient.contact

            # get the appropriate translation for this contact
            text = self.get_translated_text(contact, base_language)

            # add in our parent context if the message references @parent
            if run_map:
                run = run_map.get(recipient.pk, None)
                if run and run.flow:
                    # a bit kludgy here, but should avoid most unnecessary context creations.
                    # since this path is an optimization for flow starts, we don't need to
                    # worry about the @child context.
                    if 'parent' in text:
                        if run.parent:
                            from temba.flows.models import Flow
                            message_context = message_context.copy()
                            message_context.update(dict(parent=Flow.build_flow_context(run.parent.flow, run.parent.contact)))

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
                # keep track of this URN as a recipient
                recipient_batch.append(RelatedRecipient(contact_id=msg.contact_id, broadcast_id=self.id))

            # we commit our messages in batches
            if len(batch) >= BATCH_SIZE:
                Msg.objects.bulk_create(batch)
                RelatedRecipient.objects.bulk_create(recipient_batch)

                # send any messages
                if trigger_send:
                    self.org.trigger_send(Msg.objects.filter(broadcast=self, created_on=created_on).select_related('contact', 'contact_urn', 'channel'))

                    # increment our created on so we can load our next batch
                    created_on = created_on + timedelta(seconds=1)

                batch = []
                recipient_batch = []

        # commit any remaining objects
        if batch:
            Msg.objects.bulk_create(batch)
            RelatedRecipient.objects.bulk_create(recipient_batch)

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
    STATUS_CHOICES = [(s[0], s[1]) for s in STATUS_CONFIG]

    VISIBILITY_VISIBLE = 'V'
    VISIBILITY_ARCHIVED = 'A'
    VISIBILITY_DELETED = 'D'

    # single char flag, human readable name, API readable name
    VISIBILITY_CONFIG = ((VISIBILITY_VISIBLE, _("Visible"), 'visible'),
                         (VISIBILITY_ARCHIVED, _("Archived"), 'archived'),
                         (VISIBILITY_DELETED, _("Deleted"), 'deleted'))

    VISIBILITY_CHOICES = [(s[0], s[1]) for s in VISIBILITY_CONFIG]

    DIRECTION_CHOICES = ((INCOMING, _("Incoming")),
                         (OUTGOING, _("Outgoing")))

    MSG_TYPES = ((INBOX, _("Inbox Message")),
                 (FLOW, _("Flow Message")),
                 (IVR, _("IVR Message")))

    MEDIA_GPS = 'geo'
    MEDIA_IMAGE = 'image'
    MEDIA_VIDEO = 'video'
    MEDIA_AUDIO = 'audio'

    MEDIA_TYPES = [MEDIA_AUDIO, MEDIA_GPS, MEDIA_IMAGE, MEDIA_VIDEO]

    PRIORITY_HIGH = 1000
    PRIORITY_NORMAL = 500
    PRIORITY_BULK = 100

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

    priority = models.IntegerField(default=PRIORITY_NORMAL,
                                   help_text=_("The priority for this message to be sent, higher is higher priority"))

    created_on = models.DateTimeField(verbose_name=_("Created On"), db_index=True,
                                      help_text=_("When this message was created"))

    modified_on = models.DateTimeField(null=True, blank=True, verbose_name=_("Modified On"), auto_now=True,
                                       help_text=_("When this message was last modified"))

    sent_on = models.DateTimeField(null=True, blank=True, verbose_name=_("Sent On"),
                                   help_text=_("When this message was sent to the endpoint"))

    queued_on = models.DateTimeField(null=True, blank=True, verbose_name=_("Queued On"),
                                     help_text=_("When this message was queued to be sent or handled."))

    direction = models.CharField(max_length=1, choices=DIRECTION_CHOICES, verbose_name=_("Direction"),
                                 help_text=_("The direction for this message, either incoming or outgoing"))

    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default='P', verbose_name=_("Status"), db_index=True,
                              help_text=_("The current status for this message"))

    response_to = models.ForeignKey('Msg', null=True, blank=True, related_name='responses',
                                    verbose_name=_("Response To"), db_index=False,
                                    help_text=_("The message that this message is in reply to"))

    labels = models.ManyToManyField('Label', related_name='msgs', verbose_name=_("Labels"),
                                    help_text=_("Any labels on this message"))

    visibility = models.CharField(max_length=1, choices=VISIBILITY_CHOICES, default=VISIBILITY_VISIBLE, db_index=True,
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

    media = models.URLField(null=True, blank=True, max_length=255,
                            help_text=_("The media associated with this message if any"))

    @classmethod
    def send_messages(cls, all_msgs):
        """
        Adds the passed in messages to our sending queue, this will also update the status of the message to
        queued.
        :return:
        """
        task_msgs = []
        task_priority = None
        last_contact = None

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
                                           .exclude(channel__channel_type=Channel.TYPE_ANDROID)\
                                           .exclude(msg_type=IVR)\
                                           .exclude(topup=None)\
                                           .exclude(contact__is_test=True)
                send_messages.update(status=QUEUED, queued_on=queued_on, modified_on=queued_on)

                # now push each onto our queue
                for msg in msgs:
                    if (msg.msg_type != IVR and msg.channel and msg.channel.channel_type != Channel.TYPE_ANDROID) and \
                            msg.topup and not msg.contact.is_test:

                        # if this is a different contact than our last, and we have msgs for that last contact, queue the task
                        if task_msgs and last_contact != msg.contact_id:
                            # if no priority was set, default to DEFAULT
                            if task_priority is None:
                                task_priority = DEFAULT_PRIORITY

                            push_task(task_msgs[0]['org'], MSG_QUEUE, SEND_MSG_TASK, task_msgs, priority=task_priority)
                            task_msgs = []
                            task_priority = None

                        # serialize the model to a dictionary
                        msg.queued_on = queued_on
                        task = msg.as_task_json()

                        # only be low priority if no priority has been set for this task group
                        if msg.priority == Msg.PRIORITY_BULK and task_priority is None:
                            task_priority = LOW_PRIORITY
                        elif msg.priority == Msg.PRIORITY_HIGH:
                            task_priority = HIGH_PRIORITY

                        task_msgs.append(task)
                        last_contact = msg.contact_id

        # send our last msgs
        if task_msgs:
            if task_priority is None:
                task_priority = DEFAULT_PRIORITY
            push_task(task_msgs[0]['org'], MSG_QUEUE, SEND_MSG_TASK, task_msgs, priority=task_priority)

    @classmethod
    def process_message(cls, msg):
        """
        Processes a message, running it through all our handlers
        """
        handlers = get_message_handlers()

        if msg.contact.is_blocked and not msg.status == INTERRUPTED:
            msg.visibility = Msg.VISIBILITY_ARCHIVED
            msg.modified_on = timezone.now()
            msg.save(update_fields=['visibility', 'modified_on'])
        else:
            for handler in handlers:
                try:
                    start = None
                    if settings.DEBUG:  # pragma: no cover
                        start = time.time()

                    handled = handler.handle(msg)

                    if start:  # pragma: no cover
                        print "[%0.2f] %s for %d" % (time.time() - start, handler.name, msg.pk or 0)

                    if handled:
                        break
                except Exception as e:  # pragma: no cover
                    import traceback
                    traceback.print_exc(e)
                    logger.exception("Error in message handling: %s" % e)

        if not msg.status == INTERRUPTED:
            cls.mark_handled(msg)

        # if this is an inbox message, increment our unread inbox count
        if msg.msg_type == INBOX:
            msg.org.increment_unread_msg_count(UNREAD_INBOX_MSGS)

        # record our handling latency for this object
        if msg.queued_on:
            analytics.gauge('temba.handling_latency', (msg.modified_on - msg.queued_on).total_seconds())

        # this is the latency from when the message was received at the channel, which may be different than
        # above if people above us are queueing (or just because clocks are out of sync)
        analytics.gauge('temba.channel_handling_latency', (msg.modified_on - msg.created_on).total_seconds())

    @classmethod
    def get_messages(cls, org, is_archived=False, direction=None, msg_type=None):
        messages = Msg.objects.filter(org=org)

        if is_archived:
            messages = messages.filter(visibility=Msg.VISIBILITY_ARCHIVED)
        else:
            messages = messages.filter(visibility=Msg.VISIBILITY_VISIBLE)

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
        failed_messages.update(status='F', modified_on=timezone.now())

        # and update all related broadcast statuses
        for broadcast in Broadcast.objects.filter(id__in=[b['broadcast'] for b in failed_broadcasts]):
            broadcast.update()

    @classmethod
    def get_unread_msg_count(cls, user):
        org = user.get_org()

        key = 'org_unread_msg_count_%d' % org.pk
        unread_count = cache.get(key, None)

        if unread_count is None:
            unread_count = Msg.objects.filter(org=org, visibility=Msg.VISIBILITY_VISIBLE, direction=INCOMING,
                                              msg_type=INBOX, contact__is_test=False,
                                              created_on__gt=org.msg_last_viewed, labels=None).count()
            cache.set(key, unread_count, 900)

        return unread_count

    @classmethod
    def mark_handled(cls, msg):
        """
        Marks an incoming message as HANDLED
        """
        update_fields = ['status', 'modified_on']

        # if flows or IVR haven't claimed this message, then it's going to the inbox
        if not msg.msg_type:
            msg.msg_type = INBOX
            update_fields.append('msg_type')

        msg.status = HANDLED
        msg.modified_on = timezone.now()

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
                analytics.gauge('temba.msg_failed_%s' % channel.channel_type.lower())
        else:
            msg.status = ERRORED
            msg.modified_on = timezone.now()
            msg.next_attempt = timezone.now() + timedelta(minutes=5 * msg.error_count)

            if isinstance(msg, Msg):
                msg.save(update_fields=('status', 'modified_on', 'next_attempt', 'error_count'))
            else:
                Msg.objects.filter(id=msg.id).update(status=msg.status, next_attempt=msg.next_attempt,
                                                     error_count=msg.error_count, modified_on=msg.modified_on)

            # clear that we tried to send this message (otherwise we'll ignore it when we retry)
            pipe = r.pipeline()
            pipe.srem(timezone.now().strftime(MSG_SENT_KEY), str(msg.id))
            pipe.srem((timezone.now() - timedelta(days=1)).strftime(MSG_SENT_KEY), str(msg.id))
            pipe.execute()

            if channel:
                analytics.gauge('temba.msg_errored_%s' % channel.channel_type.lower())

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
            analytics.gauge('temba.sending_latency', (msg.sent_on - msg.queued_on).total_seconds())
        else:
            analytics.gauge('temba.sending_latency', (msg.sent_on - msg.created_on).total_seconds())

        # logs that a message was sent for this channel type if our latency is known
        if latency > 0:
            analytics.gauge('temba.msg_sent_%s' % channel.channel_type.lower(), latency)

    def as_json(self):
        return dict(direction=self.direction,
                    text=self.text,
                    id=self.id,
                    media=self.media,
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
                    return text, None

                else:
                    # search for a space to split on, up to 140 characters in
                    index = max_length
                    while index > max_length - 20:
                        if text[index] == ' ':
                            break
                        index -= 1

                    # couldn't find a good split, oh well, 160 it is
                    if index == max_length - 20:
                        return text[:max_length], text[max_length:]
                    else:
                        return text[:index], text[index + 1:]

            parts = []
            rest = text
            while rest:
                (part, rest) = next_part(rest)
                parts.append(part)

            return parts

    @classmethod
    def get_sync_commands(self, channel, msgs):
        """
        Returns the minimal # of broadcast commands for the given Android channel to uniquely represent all the
        messages which are being sent to tel URNs. This will return an array of dicts that look like:
             dict(cmd="mt_bcast", to=[dict(phone=msg.contact.tel, id=msg.pk) for msg in msgs], msg=broadcast.text))
        """
        commands = []
        current_msg = None
        contact_id_pairs = []

        ordered_msgs = Msg.objects.filter(id__in=[m.id for m in msgs]).order_by('created_on')

        for msg in ordered_msgs:
            if msg.text != current_msg and contact_id_pairs:
                commands.append(dict(cmd='mt_bcast', to=contact_id_pairs, msg=current_msg))
                contact_id_pairs = []

            current_msg = msg.text
            contact_id_pairs.append(dict(phone=msg.contact_urn.path, id=msg.pk))

        if contact_id_pairs:
            commands.append(dict(cmd='mt_bcast', to=contact_id_pairs, msg=current_msg))

        return commands

    def get_last_log(self):
        """
        Gets the last channel log for this message. Performs sorting in Python to ease pre-fetching.
        """
        sorted_logs = sorted(self.channel_logs.all(), key=lambda l: l.created_on, reverse=True)
        return sorted_logs[0] if sorted_logs else None

    def get_media_path(self):

        if self.media:
            # TODO: remove after migration msgs.0053
            if self.media.startswith('http'):
                return self.media

            if ':' in self.media:
                return self.media.split(':', 1)[1]

    def get_media_type(self):

        if self.media:
            # TODO: remove after migration msgs.0053
            if self.media.startswith('http'):
                return 'audio'

        if self.media and ':' in self.media:
            type = self.media.split(':', 1)[0]
            if type == 'application/octet-stream':
                return 'audio'
            return type.split('/', 1)[0]

    def is_media_type_audio(self):
        return Msg.MEDIA_AUDIO == self.get_media_type()

    def is_media_type_video(self):
        return Msg.MEDIA_VIDEO == self.get_media_type()

    def is_media_type_image(self):
        return Msg.MEDIA_IMAGE == self.get_media_type()

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
            self.modified_on = timezone.now()
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
        if not self.channel or self.channel.channel_type == Channel.TYPE_ANDROID or self.contact.is_test:
            Msg.process_message(self)

        # others do in celery
        else:
            push_task(self.org, HANDLER_QUEUE, HANDLE_EVENT_TASK,
                      dict(type=MSG_EVENT, id=self.id, from_mage=False, new_contact=False))

    def build_message_context(self):
        date_format = get_datetime_format(self.org.get_dayfirst())[1]
        tz = pytz.timezone(self.org.timezone)

        return {
            '__default__': self.text,
            'value': self.text,
            'contact': self.contact.build_message_context(),
            'time': datetime_to_str(self.created_on, format=date_format, tz=tz)
        }

    def resend(self):
        """
        Resends this message by creating a clone and triggering a send of that clone
        """
        now = timezone.now()
        (topup_id, amount) = self.org.decrement_credit()  # costs 1 credit to resend message

        # see if we should use a new channel
        channel = self.org.get_send_channel(contact_urn=self.contact_urn)

        cloned = Msg.objects.create(org=self.org,
                                    channel=channel,
                                    contact=self.contact,
                                    contact_urn=self.contact_urn,
                                    created_on=now,
                                    modified_on=now,
                                    text=self.text,
                                    response_to=self.response_to,
                                    direction=self.direction,
                                    topup_id=topup_id,
                                    status=PENDING,
                                    broadcast=self.broadcast)

        # mark ourselves as resent
        self.status = RESENT
        self.modified_on = now
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

    def get_flow(self):
        step = self.get_flow_step()
        return step.run.flow if step else None

    def as_task_json(self):
        """
        Used internally to serialize to JSON when queueing messages in Redis
        """
        return dict(id=self.id, org=self.org_id, channel=self.channel_id, broadcast=self.broadcast_id,
                    text=self.text, urn_path=self.contact_urn.path,
                    contact=self.contact_id, contact_urn=self.contact_urn_id,
                    priority=self.priority, error_count=self.error_count, next_attempt=self.next_attempt,
                    status=self.status, direction=self.direction,
                    external_id=self.external_id, response_to_id=self.response_to_id,
                    sent_on=self.sent_on, queued_on=self.queued_on,
                    created_on=self.created_on, modified_on=self.modified_on)

    def __unicode__(self):
        return self.text

    @classmethod
    def create_incoming(cls, channel, urn, text, user=None, date=None, org=None, contact=None,
                        status=PENDING, media=None, msg_type=None, topup=None):

        from temba.api.models import WebHookEvent, SMS_RECEIVED
        if not org and channel:
            org = channel.org

        if not org:
            raise Exception(_("Can't create an incoming message without an org"))

        if not user:
            user = User.objects.get(username=settings.ANONYMOUS_USER_NAME)

        if not date:
            date = timezone.now()  # no date?  set it to now

        contact_urn = None
        if not contact:
            contact = Contact.get_or_create(org, user, name=None, urns=[urn], channel=channel)
            contact_urn = contact.urn_objects[urn]
        elif urn:
            contact_urn = ContactURN.get_or_create(org, contact, urn, channel=channel)

        # set the preferred channel for this contact
        contact.set_preferred_channel(channel)

        # and update this URN to make sure it is associated with this channel
        if contact_urn:
            contact_urn.update_affinity(channel)

        existing = Msg.objects.filter(text=text, created_on=date, contact=contact, direction='I').first()
        if existing:
            return existing

        # costs 1 credit to receive a message
        topup_id = None
        if topup:
            topup_id = topup.pk
        elif not contact.is_test:
            (topup_id, amount) = org.decrement_credit()

        # we limit text messages to 640 characters
        if text:
            text = text[:640]

        msg_args = dict(contact=contact,
                        contact_urn=contact_urn,
                        org=org,
                        channel=channel,
                        text=text,
                        created_on=date,
                        modified_on=timezone.now(),
                        queued_on=timezone.now(),
                        direction=INCOMING,
                        msg_type=msg_type,
                        media=media,
                        status=status)

        if topup_id is not None:
            msg_args['topup_id'] = topup_id

        # fake interrupt message to handle the flow properly
        if status == INTERRUPTED:
            msg = Msg(**msg_args)
        else:
            msg = Msg.objects.create(**msg_args)

        # if this contact is currently stopped, unstop them
        if contact.is_stopped:
            contact.unstop(user)

        if channel:
            analytics.gauge('temba.msg_incoming_%s' % channel.channel_type.lower())

        # ivr messages are handled in handle_call
        if status in (PENDING, INTERRUPTED) and msg_type != IVR:
            msg.handle()

            # fire an event off for this message
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, msg, date)

        return msg

    @classmethod
    def substitute_variables(cls, text, contact, message_context,
                             org=None, url_encode=False, partial_vars=False):
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
    def create_outgoing(cls, org, user, recipient, text, broadcast=None, channel=None, priority=PRIORITY_NORMAL,
                        created_on=None, response_to=None, message_context=None, status=PENDING, insert_object=True,
                        media=None, topup_id=None, msg_type=INBOX):

        if not org or not user:  # pragma: no cover
            raise ValueError("Trying to create outgoing message with no org or user")

        # for IVR messages we need a channel that can call
        role = Channel.ROLE_CALL if msg_type == IVR else Channel.ROLE_SEND

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

        # make sure 'channel' is populated if we have a channel
        if channel:
            message_context['channel'] = channel.build_message_context()

        (text, errors) = Msg.substitute_variables(text, contact, message_context, org=org)

        # if we are doing a single message, check whether this might be a loop of some kind
        if insert_object:
            # prevent the loop of message while the sending phone is the channel
            # get all messages with same text going to same number
            same_msgs = Msg.objects.filter(contact_urn=contact_urn,
                                           contact__is_test=False,
                                           channel=channel,
                                           media=media,
                                           text=text,
                                           direction=OUTGOING,
                                           created_on__gte=created_on - timedelta(minutes=10))

            # we aren't considered with robo detection on calls
            same_msg_count = same_msgs.exclude(msg_type=IVR).count()

            if same_msg_count >= 10:
                analytics.gauge('temba.msg_loop_caught')
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
                    analytics.gauge('temba.msg_shortcode_loop_caught')
                    return None

        # costs 1 credit to send a message
        if not topup_id and not contact.is_test:
            (topup_id, amount) = org.decrement_credit()

        if response_to:
            msg_type = response_to.msg_type

        text = text.strip()

        # track this if we have a channel
        if channel:
            analytics.gauge('temba.msg_outgoing_%s' % channel.channel_type.lower())

        msg_args = dict(contact=contact,
                        contact_urn=contact_urn,
                        org=org,
                        channel=channel,
                        text=text,
                        created_on=created_on,
                        modified_on=created_on,
                        direction=OUTGOING,
                        status=status,
                        broadcast=broadcast,
                        response_to=response_to,
                        msg_type=msg_type,
                        priority=priority,
                        media=media,
                        has_template_error=len(errors) > 0)

        if topup_id is not None:
            msg_args['topup_id'] = topup_id

        return Msg.objects.create(**msg_args) if insert_object else Msg(**msg_args)

    @staticmethod
    def resolve_recipient(org, user, recipient, channel, role=Channel.ROLE_SEND):
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
        elif isinstance(recipient, basestring):
            scheme, path = URN.to_parts(recipient)
            if scheme in resolved_schemes:
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
        self.modified_on = timezone.now()
        self.save(update_fields=('status', 'modified_on'))

        Channel.track_status(self.channel, "Failed")

    def status_sent(self):
        """
        Update the message status to SENT
        """
        now = timezone.now()
        self.status = SENT
        self.sent_on = now
        self.modified_on = now
        self.save(update_fields=('status', 'sent_on', 'modified_on'))

        Channel.track_status(self.channel, "Sent")

    def status_delivered(self):
        """
        Update the message status to DELIVERED
        """
        self.status = DELIVERED
        self.modified_on = timezone.now()
        if not self.sent_on:
            self.sent_on = timezone.now()
        self.save(update_fields=('status', 'modified_on', 'sent_on'))

        Channel.track_status(self.channel, "Delivered")

    def archive(self):
        """
        Archives this message
        """
        if self.direction != INCOMING or self.contact.is_test:
            raise ValueError("Can only archive incoming non-test messages")

        self.visibility = Msg.VISIBILITY_ARCHIVED
        self.modified_on = timezone.now()
        self.save(update_fields=('visibility', 'modified_on'))

    @classmethod
    def archive_all_for_contacts(cls, contacts):
        """
        Archives all incoming messages for the given contacts
        """
        msgs = Msg.objects.filter(direction=INCOMING, visibility=Msg.VISIBILITY_VISIBLE, contact__in=contacts)
        msg_ids = list(msgs.values_list('pk', flat=True))

        # update modified on in small batches to avoid long table lock, and having too many non-unique values for
        # modified_on which is the primary ordering for the API
        for batch in chunk_list(msg_ids, 100):
            Msg.objects.filter(pk__in=batch).update(visibility=Msg.VISIBILITY_ARCHIVED, modified_on=timezone.now())

    def restore(self):
        """
        Restores (i.e. un-archives) this message
        """
        if self.direction != INCOMING or self.contact.is_test:
            raise ValueError("Can only restore incoming non-test messages")

        self.visibility = Msg.VISIBILITY_VISIBLE
        self.modified_on = timezone.now()

        self.save(update_fields=('visibility', 'modified_on'))

    def release(self):
        """
        Releases (i.e. deletes) this message
        """
        self.visibility = Msg.VISIBILITY_DELETED
        self.text = ""
        self.modified_on = timezone.now()

        self.save(update_fields=('visibility', 'text', 'modified_on'))

        # remove labels
        self.labels.clear()

    @classmethod
    def apply_action_label(cls, user, msgs, label, add):
        return label.toggle_label(msgs, add)

    @classmethod
    def apply_action_archive(cls, user, msgs):
        changed = []

        for msg in msgs:
            msg.archive()
            changed.append(msg.pk)

        return changed

    @classmethod
    def apply_action_restore(cls, user, msgs):
        changed = []

        for msg in msgs:
            msg.restore()
            changed.append(msg.pk)

        return changed

    @classmethod
    def apply_action_delete(cls, user, msgs):
        changed = []

        for msg in msgs:
            msg.release()
            changed.append(msg.pk)

        return changed

    @classmethod
    def apply_action_resend(cls, user, msgs):
        changed = []

        for msg in msgs:
            msg.resend()
            changed.append(msg.pk)
        return changed

    class Meta:
        ordering = ['-created_on', '-pk']


STOP_WORDS = 'a,able,about,across,after,all,almost,also,am,among,an,and,any,are,as,at,be,because,been,but,by,can,' \
             'cannot,could,dear,did,do,does,either,else,ever,every,for,from,get,got,had,has,have,he,her,hers,him,his,' \
             'how,however,i,if,in,into,is,it,its,just,least,let,like,likely,may,me,might,most,must,my,neither,no,nor,' \
             'not,of,off,often,on,only,or,other,our,own,rather,said,say,says,she,should,since,so,some,than,that,the,' \
             'their,them,then,there,these,they,this,tis,to,too,twas,us,wants,was,we,were,what,when,where,which,while,' \
             'who,whom,why,will,with,would,yet,you,your'.split(',')


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

    LAST_SQUASH_KEY = 'last_systemlabel_squash'

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
    def squash_counts(cls):
        # get the id of the last count we squashed
        r = get_redis_connection()
        last_squash = r.get(SystemLabel.LAST_SQUASH_KEY)
        if not last_squash:
            last_squash = 0

        # get the unique systemlabel ids for all new ones
        start = time.time()
        squash_count = 0
        for count in SystemLabel.objects.filter(id__gt=last_squash).order_by('org_id', 'label_type').distinct('org_id', 'label_type'):
            # perform our atomic squash in SQL by calling our squash method
            with connection.cursor() as c:
                c.execute("SELECT temba_squash_systemlabel(%s, %s);", (count.org_id, count.label_type))

            squash_count += 1

        # insert our new top squashed id
        max_id = SystemLabel.objects.all().order_by('-id').first()
        if max_id:
            r.set(SystemLabel.LAST_SQUASH_KEY, max_id.id)

        print "Squashed system label counts for %d pairs in %0.3fs" % (squash_count, time.time() - start)

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
    def get_queryset(cls, org, label_type, exclude_test_contacts=True):
        """
        Gets the queryset for the given system label. Any change here needs to be reflected in a change to the db
        trigger used to maintain the label counts.
        """
        # TODO: (Indexing) Sent and Failed require full message history
        if label_type == cls.TYPE_INBOX:
            qs = Msg.objects.filter(direction=INCOMING, visibility=Msg.VISIBILITY_VISIBLE, msg_type=INBOX)
        elif label_type == cls.TYPE_FLOWS:
            qs = Msg.objects.filter(direction=INCOMING, visibility=Msg.VISIBILITY_VISIBLE, msg_type=FLOW)
        elif label_type == cls.TYPE_ARCHIVED:
            qs = Msg.objects.filter(direction=INCOMING, visibility=Msg.VISIBILITY_ARCHIVED)
        elif label_type == cls.TYPE_OUTBOX:
            qs = Msg.objects.filter(direction=OUTGOING, visibility=Msg.VISIBILITY_VISIBLE, status__in=(PENDING, QUEUED))
        elif label_type == cls.TYPE_SENT:
            qs = Msg.objects.filter(direction=OUTGOING, visibility=Msg.VISIBILITY_VISIBLE, status__in=(WIRED, SENT, DELIVERED))
        elif label_type == cls.TYPE_FAILED:
            qs = Msg.objects.filter(direction=OUTGOING, visibility=Msg.VISIBILITY_VISIBLE, status=FAILED)
        elif label_type == cls.TYPE_SCHEDULED:
            qs = Broadcast.objects.exclude(schedule=None)
        elif label_type == cls.TYPE_CALLS:
            qs = ChannelEvent.objects.filter(is_active=True, event_type__in=ChannelEvent.CALL_TYPES)
        else:
            raise ValueError("Invalid label type: %s" % label_type)

        qs = qs.filter(org=org)

        if exclude_test_contacts:
            if label_type == cls.TYPE_SCHEDULED:
                qs = qs.exclude(contacts__is_test=True)
            else:
                qs = qs.exclude(contact__is_test=True)

        return qs

    @classmethod
    def recalculate_counts(cls, org, label_types=None):
        """
        Recalculates the system label counts for the passed in org, updating them in our database
        """
        if label_types is None:
            label_types = [cls.TYPE_INBOX, cls.TYPE_FLOWS, cls.TYPE_ARCHIVED, cls.TYPE_OUTBOX, cls.TYPE_SENT,
                           cls.TYPE_FAILED, cls.TYPE_SCHEDULED, cls.TYPE_CALLS]

        counts_by_type = {}

        # for each type
        for label_type in label_types:
            count = cls.get_queryset(org, label_type).count()
            counts_by_type[label_type] = count

            # delete existing counts
            cls.objects.filter(org=org, label_type=label_type).delete()

            # and create our new count
            cls.objects.create(org=org, label_type=label_type, count=count)

        return counts_by_type

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


class Label(TembaModel):
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
        # TODO: consider purpose built indexes
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

        # update modified on all our changed msgs
        Msg.objects.filter(id__in=changed).update(modified_on=timezone.now())

        return changed

    def is_folder(self):
        return self.label_type == Label.TYPE_FOLDER

    def release(self):
        self.delete()

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
            chunk_queryset = Msg.objects.filter(id__in=self._ids[i:i + self.max_obj_num])

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

        fields = ['Date', 'Contact', 'Contact Type', 'Name', 'Contact UUID', 'Direction', 'Text', 'Labels', "Status"]

        all_messages = Msg.get_messages(self.org).order_by('-created_on')

        tz = self.org.get_tzinfo()

        if self.start_date:
            start_date = tz.localize(datetime.combine(self.start_date, datetime.min.time()))
            all_messages = all_messages.filter(created_on__gte=start_date)

        if self.end_date:
            end_date = tz.localize(datetime.combine(self.end_date, datetime.max.time()))
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
            contact_uuid = msg.contact.uuid
            created_on = msg.created_on.astimezone(pytz.utc).replace(tzinfo=None)
            msg_labels = ", ".join(msg_label.name for msg_label in msg.labels.all())

            # only show URN path if org isn't anon and there is a URN
            if self.org.is_anon:
                urn_path = msg.contact.anon_identifier
            elif msg.contact_urn:
                urn_path = msg.contact_urn.get_display(org=self.org, formatted=False)
            else:
                urn_path = ''

            urn_scheme = msg.contact_urn.scheme if msg.contact_urn else ''

            current_messages_sheet.write(row, 0, created_on, date_style)
            current_messages_sheet.write(row, 1, urn_path)
            current_messages_sheet.write(row, 2, urn_scheme)
            current_messages_sheet.write(row, 3, contact_name)
            current_messages_sheet.write(row, 4, contact_uuid)
            current_messages_sheet.write(row, 5, msg.get_direction_display())
            current_messages_sheet.write(row, 6, msg.text)
            current_messages_sheet.write(row, 7, msg_labels)
            current_messages_sheet.write(row, 8, msg.get_status_display())
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

        branding = self.org.get_branding()

        subject = "Your messages export is ready"
        template = 'msgs/email/msg_export_download'
        download_url = branding['link'] + get_asset_url(AssetType.message_export, self.pk)

        # force a gc
        import gc
        gc.collect()

        send_template_email(self.created_by.username, subject, template, dict(link=download_url), branding)
