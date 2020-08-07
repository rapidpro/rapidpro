import logging
import time
from array import array
from datetime import datetime, timedelta

import iso8601
import pytz
import regex
from xlsxlite.writer import XLSXBook

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.postgres.fields import ArrayField
from django.core.files.temp import NamedTemporaryFile
from django.db import models, transaction
from django.db.models import Prefetch, Sum
from django.db.models.functions import Upper
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba import mailroom
from temba.assets.models import register_asset_store
from temba.channels.courier import push_courier_msgs
from temba.channels.models import Channel, ChannelEvent, ChannelLog
from temba.contacts.models import URN, Contact, ContactGroup, ContactURN
from temba.orgs.models import Language, Org, TopUp
from temba.schedules.models import Schedule
from temba.utils import chunk_list, extract_constants, on_transaction_commit
from temba.utils.export import BaseExportAssetStore, BaseExportTask
from temba.utils.models import JSONAsTextField, SquashableModel, TembaModel, TranslatableField
from temba.utils.text import clean_string
from temba.utils.uuid import uuid4

logger = logging.getLogger(__name__)

INITIALIZING = "I"
PENDING = "P"
QUEUED = "Q"
WIRED = "W"
SENT = "S"
DELIVERED = "D"
HANDLED = "H"
ERRORED = "E"
FAILED = "F"
RESENT = "R"

INCOMING = "I"
OUTGOING = "O"

INBOX = "I"
FLOW = "F"
IVR = "V"
USSD = "U"

# status codes used for both messages and broadcasts (single char constant, human readable, API readable)
STATUS_CONFIG = (
    # special state for flows used to hold off sending the message until the flow is ready to receive a response
    (INITIALIZING, _("Initializing"), "initializing"),
    (PENDING, _("Pending"), "pending"),  # initial state for all messages
    # valid only for outgoing messages
    (QUEUED, _("Queued"), "queued"),
    (WIRED, _("Wired"), "wired"),  # message was handed off to the provider and credits were deducted for it
    (SENT, _("Sent"), "sent"),  # we have confirmation that a message was sent
    (DELIVERED, _("Delivered"), "delivered"),
    # valid only for incoming messages
    (HANDLED, _("Handled"), "handled"),
    (ERRORED, _("Error Sending"), "errored"),  # there was an error during delivery
    (FAILED, _("Failed Sending"), "failed"),  # we gave up on sending this message
    (RESENT, _("Resent message"), "resent"),  # we retried this message
)


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

    MAX_TEXT_LEN = settings.MSG_FIELD_SIZE

    TEMPLATE_STATE_LEGACY = "legacy"
    TEMPLATE_STATE_EVALUATED = "evaluated"
    TEMPLATE_STATE_UNEVALUATED = "unevaluated"
    TEMPLATE_STATE_CHOICES = (TEMPLATE_STATE_LEGACY, TEMPLATE_STATE_EVALUATED, TEMPLATE_STATE_UNEVALUATED)

    METADATA_QUICK_REPLIES = "quick_replies"
    METADATA_TEMPLATE_STATE = "template_state"

    org = models.ForeignKey(
        Org, on_delete=models.PROTECT, verbose_name=_("Org"), help_text=_("The org this broadcast is connected to")
    )

    groups = models.ManyToManyField(
        ContactGroup,
        verbose_name=_("Groups"),
        related_name="addressed_broadcasts",
        help_text=_("The groups to send the message to"),
    )

    contacts = models.ManyToManyField(
        Contact,
        verbose_name=_("Contacts"),
        related_name="addressed_broadcasts",
        help_text=_("Individual contacts included in this message"),
    )

    urns = models.ManyToManyField(
        ContactURN,
        verbose_name=_("URNs"),
        related_name="addressed_broadcasts",
        help_text=_("Individual URNs included in this message"),
    )

    channel = models.ForeignKey(
        Channel,
        on_delete=models.PROTECT,
        null=True,
        verbose_name=_("Channel"),
        help_text=_("Channel to use for message sending"),
    )

    status = models.CharField(
        max_length=1,
        verbose_name=_("Status"),
        choices=STATUS_CHOICES,
        default=INITIALIZING,
        help_text=_("The current status for this broadcast"),
    )

    schedule = models.OneToOneField(
        Schedule,
        on_delete=models.PROTECT,
        verbose_name=_("Schedule"),
        null=True,
        help_text=_("Our recurring schedule if we have one"),
        related_name="broadcast",
    )

    parent = models.ForeignKey(
        "Broadcast", on_delete=models.PROTECT, verbose_name=_("Parent"), null=True, related_name="children"
    )

    text = TranslatableField(
        verbose_name=_("Translations"),
        max_length=MAX_TEXT_LEN,
        help_text=_("The localized versions of the message text"),
    )

    base_language = models.CharField(
        max_length=4, help_text=_("The language used to send this to contacts without a language")
    )

    is_active = models.BooleanField(default=True, help_text="Whether this broadcast is active")

    created_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_creations",
        help_text="The user which originally created this item",
    )

    created_on = models.DateTimeField(
        default=timezone.now, blank=True, editable=False, db_index=True, help_text=_("When this broadcast was created")
    )

    modified_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_modifications",
        help_text="The user which last modified this item",
    )

    modified_on = models.DateTimeField(auto_now=True, help_text="When this item was last modified")

    media = TranslatableField(
        verbose_name=_("Media"), max_length=2048, help_text=_("The localized versions of the media"), null=True
    )

    send_all = models.BooleanField(
        default=False, help_text="Whether this broadcast should send to all URNs for each contact"
    )

    metadata = JSONAsTextField(null=True, help_text=_("The metadata for messages of this broadcast"), default=dict)

    @classmethod
    def create(
        cls,
        org,
        user,
        text,
        *,
        groups=None,
        contacts=None,
        urns=None,
        contact_ids=None,
        base_language=None,
        channel=None,
        media=None,
        send_all=False,
        quick_replies=None,
        template_state=TEMPLATE_STATE_LEGACY,
        status=INITIALIZING,
        **kwargs,
    ):
        # for convenience broadcasts can still be created with single translation and no base_language
        if isinstance(text, str):
            base_language = org.primary_language.iso_code if org.primary_language else "base"
            text = {base_language: text}

        # check we have at least one recipient type
        if groups is None and contacts is None and contact_ids is None and urns is None:
            raise ValueError("Must specify at least one recipient kind in broadcast creation")

        if base_language not in text:  # pragma: no cover
            raise ValueError("Base language '%s' doesn't exist in the provided translations dict" % base_language)

        if media and base_language not in media:  # pragma: no cover
            raise ValueError("Base language '%s' doesn't exist in the provided media dict" % base_language)

        if quick_replies:
            for quick_reply in quick_replies:
                if base_language not in quick_reply:
                    raise ValueError(
                        "Base language '%s' doesn't exist for one or more of the provided quick replies"
                        % base_language
                    )

        metadata = {Broadcast.METADATA_TEMPLATE_STATE: template_state}
        if quick_replies:
            metadata[Broadcast.METADATA_QUICK_REPLIES] = quick_replies

        broadcast = cls.objects.create(
            org=org,
            channel=channel,
            send_all=send_all,
            base_language=base_language,
            text=text,
            media=media,
            created_by=user,
            modified_by=user,
            metadata=metadata,
            status=status,
            **kwargs,
        )

        # set our recipients
        broadcast._set_recipients(groups=groups, contacts=contacts, urns=urns, contact_ids=contact_ids)

        return broadcast

    def send(self):
        """
        Queues this broadcast for sending by mailroom
        """
        mailroom.queue_broadcast(self)

    def has_pending_fire(self):  # pragma: needs cover
        return self.schedule and self.schedule.next_fire is not None

    def get_messages(self):
        return self.msgs.all()

    def get_message_count(self):
        return BroadcastMsgCount.get_count(self)

    def get_recipient_counts(self):
        if self.status in (WIRED, SENT, DELIVERED):
            return {"recipients": self.get_message_count(), "groups": 0, "contacts": 0, "urns": 0}

        group_count = self.groups.count()
        contact_count = self.contacts.count()
        urn_count = self.urns.count()

        if group_count == 1 and contact_count == 0 and urn_count == 0:
            return {"recipients": self.groups.first().get_member_count(), "groups": 0, "contacts": 0, "urns": 0}
        if group_count == 0 and urn_count == 0:
            return {"recipients": contact_count, "groups": 0, "contacts": 0, "urns": 0}
        if group_count == 0 and contact_count == 0:
            return {"recipients": urn_count, "groups": 0, "contacts": 0, "urns": 0}

        return {"recipients": 0, "groups": group_count, "contacts": contact_count, "urns": urn_count}

    def get_default_text(self):
        """
        Gets the appropriate display text for the broadcast without a contact
        """
        return self.text[self.base_language]

    def get_translated_text(self, contact, org=None):
        """
        Gets the appropriate translation for the given contact
        """
        preferred_languages = self._get_preferred_languages(contact, org)
        return Language.get_localized_text(self.text, preferred_languages)

    def release(self):
        for msg in self.msgs.all():
            msg.release()

        BroadcastMsgCount.objects.filter(broadcast=self).delete()

        self.delete()

        if self.schedule:
            self.schedule.delete()

    def update_recipients(self, *, groups=None, contacts=None, urns=None):
        """
        Only used to update recipients for scheduled / repeating broadcasts
        """
        # clear our current recipients
        self.groups.clear()
        self.contacts.clear()
        self.urns.clear()

        self._set_recipients(groups=groups, contacts=contacts, urns=urns)

    def _set_recipients(self, *, groups=None, contacts=None, urns=None, contact_ids=None):
        """
        Sets the recipients which may be contact groups, contacts or contact URNs.
        """
        if groups:
            self.groups.add(*groups)

        if contacts:
            self.contacts.add(*contacts)

        if urns:
            self.urns.add(*urns)

        if contact_ids:
            RelatedModel = self.contacts.through
            for chunk in chunk_list(contact_ids, 1000):
                bulk_contacts = [RelatedModel(contact_id=id, broadcast_id=self.id) for id in chunk]
                RelatedModel.objects.bulk_create(bulk_contacts)

    def _get_preferred_languages(self, contact=None, org=None):
        """
        Gets the ordered list of language preferences for the given contact
        """
        org = org or self.org  # org object can be provided to allow caching of org languages
        preferred_languages = []

        # if contact has a language and it's a valid org language, it has priority
        if contact is not None and contact.language and contact.language in org.get_language_codes():
            preferred_languages.append(contact.language)

        if org.primary_language:
            preferred_languages.append(org.primary_language.iso_code)

        preferred_languages.append(self.base_language)

        return preferred_languages

    def get_template_state(self):
        metadata = self.metadata or {}
        return metadata.get(Broadcast.METADATA_TEMPLATE_STATE, Broadcast.TEMPLATE_STATE_LEGACY)

    def __str__(self):  # pragma: no cover
        return f"Broadcast[id={self.id}, text={self.text}]"


class Attachment(object):
    """
    Represents a message attachment stored as type:url
    """

    def __init__(self, content_type, url):
        self.content_type = content_type
        self.url = url

    @classmethod
    def parse(cls, s):
        return cls(*s.split(":", 1))

    @classmethod
    def parse_all(cls, attachments):
        return [cls.parse(s) for s in attachments] if attachments else []

    def as_json(self):
        return {"content_type": self.content_type, "url": self.url}

    def __eq__(self, other):
        return self.content_type == other.content_type and self.url == other.url


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

    VISIBILITY_VISIBLE = "V"
    VISIBILITY_ARCHIVED = "A"
    VISIBILITY_DELETED = "D"

    # single char flag, human readable name, API readable name
    VISIBILITY_CONFIG = (
        (VISIBILITY_VISIBLE, _("Visible"), "visible"),
        (VISIBILITY_ARCHIVED, _("Archived"), "archived"),
        (VISIBILITY_DELETED, _("Deleted"), "deleted"),
    )

    VISIBILITY_CHOICES = [(s[0], s[1]) for s in VISIBILITY_CONFIG]

    DIRECTION_CHOICES = ((INCOMING, _("Incoming")), (OUTGOING, _("Outgoing")))

    MSG_TYPES_CHOICES = (
        (INBOX, _("Inbox Message")),
        (FLOW, _("Flow Message")),
        (IVR, _("IVR Message")),
        (USSD, _("USSD Message")),
    )

    DELETE_FOR_ARCHIVE = "A"
    DELETE_FOR_USER = "U"

    DELETE_CHOICES = ((DELETE_FOR_ARCHIVE, _("Archive delete")), (DELETE_FOR_USER, _("User delete")))

    MEDIA_GPS = "geo"
    MEDIA_IMAGE = "image"
    MEDIA_VIDEO = "video"
    MEDIA_AUDIO = "audio"

    MEDIA_TYPES = [MEDIA_AUDIO, MEDIA_GPS, MEDIA_IMAGE, MEDIA_VIDEO]

    MAX_TEXT_LEN = settings.MSG_FIELD_SIZE

    STATUSES = extract_constants(STATUS_CONFIG)
    VISIBILITIES = extract_constants(VISIBILITY_CONFIG)
    DIRECTIONS = {INCOMING: "in", OUTGOING: "out"}
    MSG_TYPES = {INBOX: "inbox", FLOW: "flow", IVR: "ivr"}

    uuid = models.UUIDField(null=True, default=uuid4, help_text=_("The UUID for this message"))

    org = models.ForeignKey(
        Org,
        on_delete=models.PROTECT,
        related_name="msgs",
        verbose_name=_("Org"),
        help_text=_("The org this message is connected to"),
    )

    channel = models.ForeignKey(
        Channel,
        on_delete=models.PROTECT,
        null=True,
        related_name="msgs",
        verbose_name=_("Channel"),
        help_text=_("The channel object that this message is associated with"),
    )

    contact = models.ForeignKey(
        Contact,
        on_delete=models.PROTECT,
        related_name="msgs",
        verbose_name=_("Contact"),
        help_text=_("The contact this message is communicating with"),
        db_index=False,
    )

    contact_urn = models.ForeignKey(
        ContactURN,
        on_delete=models.PROTECT,
        null=True,
        related_name="msgs",
        verbose_name=_("Contact URN"),
        help_text=_("The URN this message is communicating with"),
    )

    broadcast = models.ForeignKey(
        Broadcast,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="msgs",
        verbose_name=_("Broadcast"),
        help_text=_("If this message was sent to more than one recipient"),
    )

    text = models.TextField(verbose_name=_("Text"), help_text=_("The actual message content that was sent"))

    high_priority = models.BooleanField(
        null=True, help_text=_("Give this message higher priority than other messages")
    )

    created_on = models.DateTimeField(
        verbose_name=_("Created On"), db_index=True, help_text=_("When this message was created")
    )

    modified_on = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Modified On"),
        auto_now=True,
        help_text=_("When this message was last modified"),
    )

    sent_on = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Sent On"), help_text=_("When this message was sent to the endpoint")
    )

    queued_on = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Queued On"),
        help_text=_("When this message was queued to be sent or handled."),
    )

    direction = models.CharField(
        max_length=1,
        choices=DIRECTION_CHOICES,
        verbose_name=_("Direction"),
        help_text=_("The direction for this message, either incoming or outgoing"),
    )

    status = models.CharField(
        max_length=1,
        choices=STATUS_CHOICES,
        default="P",
        verbose_name=_("Status"),
        db_index=True,
        help_text=_("The current status for this message"),
    )

    response_to = models.ForeignKey(
        "Msg",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="responses",
        verbose_name=_("Response To"),
        db_index=False,
        help_text=_("The message that this message is in reply to"),
    )

    labels = models.ManyToManyField(
        "Label", related_name="msgs", verbose_name=_("Labels"), help_text=_("Any labels on this message")
    )

    visibility = models.CharField(
        max_length=1,
        choices=VISIBILITY_CHOICES,
        default=VISIBILITY_VISIBLE,
        verbose_name=_("Visibility"),
        help_text=_("The current visibility of this message, either visible, archived or deleted"),
    )

    msg_type = models.CharField(
        max_length=1,
        choices=MSG_TYPES_CHOICES,
        null=True,
        verbose_name=_("Message Type"),
        help_text=_("The type of this message"),
    )

    msg_count = models.IntegerField(
        default=1,
        verbose_name=_("Message Count"),
        help_text=_("The number of messages that were used to send this message, calculated on Twilio channels"),
    )

    error_count = models.IntegerField(
        default=0, verbose_name=_("Error Count"), help_text=_("The number of times this message has errored")
    )

    next_attempt = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Next Attempt"),
        help_text=_("When we should next attempt to deliver this message"),
    )

    external_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name=_("External ID"),
        help_text=_("External id used for integrating with callbacks from other APIs"),
    )

    topup = models.ForeignKey(
        TopUp,
        null=True,
        blank=True,
        related_name="msgs",
        on_delete=models.PROTECT,
        help_text="The topup that this message was deducted from",
    )

    attachments = ArrayField(
        models.URLField(max_length=2048), null=True, help_text=_("The media attachments on this message if any")
    )

    connection = models.ForeignKey(
        "channels.ChannelConnection",
        on_delete=models.PROTECT,
        related_name="msgs",
        null=True,
        help_text=_("The session this message was a part of if any"),
    )

    metadata = JSONAsTextField(null=True, help_text=_("The metadata for this msg"), default=dict)

    delete_reason = models.CharField(
        null=True, max_length=1, choices=DELETE_CHOICES, help_text=_("Why the message is being deleted")
    )

    @classmethod
    def send_messages(cls, all_msgs):
        """
        Adds the passed in messages to our sending queue, this will also update the status of the message to
        queued.
        :return:
        """

        from temba.channels.types.android import AndroidType

        courier_batches = []

        # we send in chunks of 1,000 to help with contention
        for msgs in chunk_list(all_msgs, 1000):
            # build our id list
            msg_ids = set([m.id for m in msgs])

            with transaction.atomic():
                queued_on = timezone.now()
                courier_msgs = []

                # update them to queued
                send_messages = (
                    Msg.objects.filter(id__in=msg_ids)
                    .exclude(channel__channel_type=AndroidType.code)
                    .exclude(msg_type=IVR)
                    .exclude(topup=None)
                )
                send_messages.update(status=QUEUED, queued_on=queued_on, modified_on=queued_on)

                # now push each onto our queue
                for msg in msgs:

                    # in development mode, don't actual send any messages
                    if not settings.SEND_MESSAGES:
                        msg.status = WIRED
                        msg.sent_on = timezone.now()
                        msg.save(update_fields=("status", "sent_on"))
                        logger.debug(f"FAKED SEND for [{msg.id}]")
                        continue

                    if (
                        (msg.msg_type != IVR and msg.channel and not msg.channel.is_android())
                        and msg.topup
                        and msg.uuid
                    ):
                        courier_msgs.append(msg)
                        continue

                # ok, now batch up our courier msgs
                last_contact = None
                last_channel = None
                task_msgs = []
                for msg in courier_msgs:
                    if task_msgs and (last_contact != msg.contact_id or last_channel != msg.channel_id):
                        courier_batches.append(
                            dict(
                                channel=task_msgs[0].channel, msgs=task_msgs, high_priority=task_msgs[0].high_priority
                            )
                        )
                        task_msgs = []

                    last_contact = msg.contact_id
                    last_channel = msg.channel_id
                    task_msgs.append(msg)

                # push any remaining courier msgs
                if task_msgs:
                    courier_batches.append(
                        dict(channel=task_msgs[0].channel, msgs=task_msgs, high_priority=task_msgs[0].high_priority)
                    )

        # send our batches
        on_transaction_commit(lambda: cls._send_courier_msg_batches(courier_batches))

    @classmethod
    def _send_courier_msg_batches(cls, batches):
        for batch in batches:
            push_courier_msgs(batch["channel"], batch["msgs"], batch["high_priority"])

    @classmethod
    def get_messages(cls, org, is_archived=False, direction=None, msg_type=None):
        messages = Msg.objects.filter(org=org)

        if is_archived:  # pragma: needs cover
            messages = messages.filter(visibility=Msg.VISIBILITY_ARCHIVED)
        else:
            messages = messages.filter(visibility=Msg.VISIBILITY_VISIBLE)

        if direction:  # pragma: needs cover
            messages = messages.filter(direction=direction)

        if msg_type:  # pragma: needs cover
            messages = messages.filter(msg_type=msg_type)

        return messages

    @classmethod
    def fail_old_messages(cls):  # pragma: needs cover
        """
        Looks for any errored or queued messages more than a week old and fails them. Messages that old would
        probably be confusing to go out.
        """
        one_week_ago = timezone.now() - timedelta(days=7)
        failed_messages = Msg.objects.filter(
            created_on__lte=one_week_ago, direction=OUTGOING, status__in=[QUEUED, PENDING, ERRORED]
        )

        # fail our messages
        failed_messages.update(status="F", modified_on=timezone.now())

    def as_archive_json(self):
        return {
            "id": self.id,
            "contact": {"uuid": str(self.contact.uuid), "name": self.contact.name},
            "channel": {"uuid": str(self.channel.uuid), "name": self.channel.name} if self.channel else None,
            "urn": self.contact_urn.identity if self.contact_urn else None,
            "direction": Msg.DIRECTIONS.get(self.direction),
            "type": Msg.MSG_TYPES.get(self.msg_type),
            "status": Msg.STATUSES.get(self.status),
            "visibility": Msg.VISIBILITIES.get(self.visibility),
            "text": self.text,
            "attachments": [attachment.as_json() for attachment in Attachment.parse_all(self.attachments)],
            "labels": [{"uuid": l.uuid, "name": l.name} for l in self.labels.all()],
            "created_on": self.created_on.isoformat(),
            "sent_on": self.sent_on.isoformat() if self.sent_on else None,
        }

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
                        if text[index] == " ":
                            break
                        index -= 1

                    # couldn't find a good split, oh well, 160 it is
                    if index == max_length - 20:
                        return text[:max_length], text[max_length:]
                    else:
                        return text[:index], text[index + 1 :]

            parts = []
            rest = text
            while rest:
                (part, rest) = next_part(rest)
                parts.append(part)

            return parts

    @classmethod
    def get_sync_commands(cls, msgs):
        """
        Returns the minimal # of broadcast commands for the given Android channel to uniquely represent all the
        messages which are being sent to tel URNs. This will return an array of dicts that look like:
             dict(cmd="mt_bcast", to=[dict(phone=msg.contact.tel, id=msg.pk) for msg in msgs], msg=broadcast.text))
        """
        commands = []
        current_text = None
        contact_id_pairs = []

        for m in msgs.values("id", "text", "contact_urn__path").order_by("created_on"):
            if m["text"] != current_text and contact_id_pairs:
                commands.append(dict(cmd="mt_bcast", to=contact_id_pairs, msg=current_text))
                contact_id_pairs = []

            current_text = m["text"]
            contact_id_pairs.append(dict(phone=m["contact_urn__path"], id=m["id"]))

        if contact_id_pairs:
            commands.append(dict(cmd="mt_bcast", to=contact_id_pairs, msg=current_text))

        return commands

    def get_attachments(self):
        """
        Gets this message's attachments parsed into actual attachment objects
        """
        return Attachment.parse_all(self.attachments)

    def get_last_log(self):
        """
        Gets the last channel log for this message. Performs sorting in Python to ease pre-fetching.
        """
        sorted_logs = None
        if self.channel and self.channel.is_active:
            sorted_logs = sorted(self.channel_logs.all(), key=lambda l: l.created_on, reverse=True)
        return sorted_logs[0] if sorted_logs else None

    def update(self, cmd):
        """
        Updates our message according to the provided client command
        """

        date = datetime.fromtimestamp(int(cmd["ts"]) // 1000).replace(tzinfo=pytz.utc)

        keyword = cmd["cmd"]
        handled = False

        if keyword == "mt_error":
            self.status = ERRORED
            handled = True

        elif keyword == "mt_fail":
            self.status = FAILED
            handled = True

        elif keyword == "mt_sent":
            self.status = SENT
            self.sent_on = date
            handled = True

        elif keyword == "mt_dlvd":
            self.status = DELIVERED
            handled = True

        self.save(
            update_fields=["status", "sent_on"]
        )  # first save message status before updating the broadcast status

        return handled

    def handle(self):
        """
        Queues this message to be handled
        """

        mailroom.queue_msg_handling(self)

    def resend(self):
        """
        Resends this message by creating a clone and triggering a send of that clone
        """
        now = timezone.now()
        (topup_id, amount) = self.org.decrement_credit()  # costs 1 credit to resend message

        # see if we should use a new channel
        channel = self.org.get_send_channel(contact_urn=self.contact_urn)

        cloned = Msg.objects.create(
            org=self.org,
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
            broadcast=self.broadcast,
            metadata=self.metadata,
        )

        # mark ourselves as resent
        self.status = RESENT
        self.topup = None
        self.save()

        # send our message
        self.org.trigger_send([cloned])
        return cloned

    def as_task_json(self):
        """
        Used internally to serialize to JSON when queueing messages in Redis
        """
        data = dict(
            id=self.id,
            org=self.org_id,
            channel=self.channel_id,
            broadcast=self.broadcast_id,
            text=self.text,
            urn_path=self.contact_urn.path,
            urn=str(self.contact_urn),
            contact=self.contact_id,
            contact_urn=self.contact_urn_id,
            error_count=self.error_count,
            next_attempt=self.next_attempt,
            status=self.status,
            direction=self.direction,
            attachments=self.attachments,
            external_id=self.external_id,
            response_to_id=self.response_to_id,
            sent_on=self.sent_on,
            queued_on=self.queued_on,
            created_on=self.created_on,
            modified_on=self.modified_on,
            high_priority=self.high_priority,
            metadata=self.metadata,
            connection_id=self.connection_id,
        )

        if self.contact_urn.auth:  # pragma: no cover
            data.update(dict(auth=self.contact_urn.auth))

        return data

    def __str__(self):  # pragma: needs cover
        return self.text

    @classmethod
    def create_relayer_incoming(cls, org, channel, urn, text, received_on, attachments=None):
        # get / create our contact and URN
        contact, contact_urn = Contact.get_or_create(org, urn, channel, init_new=False)

        # we limit our text message length and remove any invalid chars
        if text:
            text = clean_string(text[: cls.MAX_TEXT_LEN])

        now = timezone.now()

        # don't create duplicate messages
        existing = Msg.objects.filter(text=text, sent_on=received_on, contact=contact, direction="I").first()
        if existing:
            return existing

        msg = Msg.objects.create(
            org=org,
            channel=channel,
            contact=contact,
            contact_urn=contact_urn,
            text=text,
            sent_on=received_on,
            created_on=now,
            modified_on=now,
            queued_on=now,
            direction=INCOMING,
            attachments=attachments,
            status=PENDING,
        )

        # pass off handling of the message after we commit
        on_transaction_commit(lambda: msg.handle())

        return msg

    def archive(self):
        """
        Archives this message
        """
        if self.direction != INCOMING:
            raise ValueError("Can only archive incoming non-test messages")

        self.visibility = Msg.VISIBILITY_ARCHIVED
        self.save(update_fields=("visibility", "modified_on"))

    @classmethod
    def archive_all_for_contacts(cls, contacts):
        """
        Archives all incoming messages for the given contacts
        """
        msgs = Msg.objects.filter(direction=INCOMING, visibility=Msg.VISIBILITY_VISIBLE, contact__in=contacts)
        msg_ids = list(msgs.values_list("pk", flat=True))

        # update modified on in small batches to avoid long table lock, and having too many non-unique values for
        # modified_on which is the primary ordering for the API
        for batch in chunk_list(msg_ids, 100):
            Msg.objects.filter(pk__in=batch).update(visibility=Msg.VISIBILITY_ARCHIVED, modified_on=timezone.now())

    def restore(self):
        """
        Restores (i.e. un-archives) this message
        """
        if self.direction != INCOMING:  # pragma: needs cover
            raise ValueError("Can only restore incoming non-test messages")

        self.visibility = Msg.VISIBILITY_VISIBLE
        self.save(update_fields=("visibility", "modified_on"))

    def release(self, delete_reason=DELETE_FOR_USER):
        """
        Releases (i.e. deletes) this message
        """
        Msg.objects.filter(response_to=self).update(response_to=None)

        for log in ChannelLog.objects.filter(msg=self):
            log.release()

        if delete_reason:
            self.delete_reason = delete_reason
            self.save(update_fields=["delete_reason"])

        # delete this object
        self.delete()

    @classmethod
    def apply_action_label(cls, user, msgs, label):
        label.toggle_label(msgs, add=True)

    @classmethod
    def apply_action_unlabel(cls, user, msgs, label):
        label.toggle_label(msgs, add=False)

    @classmethod
    def apply_action_archive(cls, user, msgs):
        for msg in msgs:
            msg.archive()

    @classmethod
    def apply_action_restore(cls, user, msgs):
        for msg in msgs:
            msg.restore()

    @classmethod
    def apply_action_delete(cls, user, msgs):
        for msg in msgs:
            msg.release()

    @classmethod
    def apply_action_resend(cls, user, msgs):
        for msg in msgs:
            msg.resend()


class BroadcastMsgCount(SquashableModel):
    """
    Maintains count of how many msgs are tied to a broadcast
    """

    SQUASH_OVER = ("broadcast_id",)

    broadcast = models.ForeignKey(Broadcast, on_delete=models.PROTECT, related_name="counts", db_index=True)
    count = models.IntegerField(default=0)

    @classmethod
    def get_squash_query(cls, distinct_set):
        sql = """
        WITH deleted as (
            DELETE FROM %(table)s WHERE "broadcast_id" = %%s RETURNING "count"
        )
        INSERT INTO %(table)s("broadcast_id", "count", "is_squashed")
        VALUES (%%s, GREATEST(0, (SELECT SUM("count") FROM deleted)), TRUE);
        """ % {
            "table": cls._meta.db_table
        }

        return sql, (distinct_set.broadcast_id,) * 2

    @classmethod
    def get_count(cls, broadcast):
        count = BroadcastMsgCount.objects.filter(broadcast=broadcast).aggregate(count_sum=Sum("count"))["count_sum"]
        return count if count else 0

    def __str__(self):  # pragma: needs cover
        return f"BroadcastMsgCount[{self.broadcast_id}:{self.count}]"


STOP_WORDS = (
    "a,able,about,across,after,all,almost,also,am,among,an,and,any,are,as,at,be,because,been,but,by,can,"
    "cannot,could,dear,did,do,does,either,else,ever,every,for,from,get,got,had,has,have,he,her,hers,him,his,"
    "how,however,i,if,in,into,is,it,its,just,least,let,like,likely,may,me,might,most,must,my,neither,no,nor,"
    "not,of,off,often,on,only,or,other,our,own,rather,said,say,says,she,should,since,so,some,than,that,the,"
    "their,them,then,there,these,they,this,tis,to,too,twas,us,wants,was,we,were,what,when,where,which,while,"
    "who,whom,why,will,with,would,yet,you,your".split(",")
)


class SystemLabel(object):
    TYPE_INBOX = "I"
    TYPE_FLOWS = "W"
    TYPE_ARCHIVED = "A"
    TYPE_OUTBOX = "O"
    TYPE_SENT = "S"
    TYPE_FAILED = "X"
    TYPE_SCHEDULED = "E"
    TYPE_CALLS = "C"

    TYPE_CHOICES = (
        (TYPE_INBOX, "Inbox"),
        (TYPE_FLOWS, "Flows"),
        (TYPE_ARCHIVED, "Archived"),
        (TYPE_OUTBOX, "Outbox"),
        (TYPE_SENT, "Sent"),
        (TYPE_FAILED, "Failed"),
        (TYPE_SCHEDULED, "Scheduled"),
        (TYPE_CALLS, "Calls"),
    )

    @classmethod
    def get_counts(cls, org):
        return SystemLabelCount.get_totals(org)

    @classmethod
    def get_queryset(cls, org, label_type):
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
            qs = Msg.objects.filter(
                direction=OUTGOING, visibility=Msg.VISIBILITY_VISIBLE, status__in=(PENDING, QUEUED)
            )
        elif label_type == cls.TYPE_SENT:
            qs = Msg.objects.filter(
                direction=OUTGOING, visibility=Msg.VISIBILITY_VISIBLE, status__in=(WIRED, SENT, DELIVERED)
            )
        elif label_type == cls.TYPE_FAILED:
            qs = Msg.objects.filter(direction=OUTGOING, visibility=Msg.VISIBILITY_VISIBLE, status=FAILED)
        elif label_type == cls.TYPE_SCHEDULED:
            qs = Broadcast.objects.exclude(schedule=None).prefetch_related("groups", "contacts", "urns")
        elif label_type == cls.TYPE_CALLS:
            qs = ChannelEvent.objects.filter(event_type__in=ChannelEvent.CALL_TYPES)
        else:  # pragma: needs cover
            raise ValueError("Invalid label type: %s" % label_type)

        return qs.filter(org=org)

    @classmethod
    def get_archive_attributes(cls, label_type):
        visibility = "visible"
        msg_type = None
        direction = "in"
        statuses = None

        if label_type == cls.TYPE_INBOX:
            msg_type = "inbox"
        elif label_type == cls.TYPE_FLOWS:
            msg_type = "flow"
        elif label_type == cls.TYPE_ARCHIVED:
            visibility = "archived"
        elif label_type == cls.TYPE_OUTBOX:
            direction = "out"
            statuses = ["pending", "queued"]
        elif label_type == cls.TYPE_SENT:
            direction = "out"
            statuses = ["wired", "sent", "delivered"]
        elif label_type == cls.TYPE_FAILED:
            direction = "out"
            statuses = ["failed"]

        return (visibility, direction, msg_type, statuses)


class SystemLabelCount(SquashableModel):
    """
    Counts of messages/broadcasts/calls maintained by database level triggers
    """

    SQUASH_OVER = ("org_id", "label_type", "is_archived")

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="system_labels")

    label_type = models.CharField(max_length=1, choices=SystemLabel.TYPE_CHOICES)

    is_archived = models.BooleanField(default=False, help_text=_("Whether this count is for archived messages"))

    count = models.IntegerField(default=0, help_text=_("Number of items with this system label"))

    @classmethod
    def get_squash_query(cls, distinct_set):
        sql = """
        WITH deleted as (
            DELETE FROM %(table)s WHERE "org_id" = %%s AND "label_type" = %%s and "is_archived" = %%s RETURNING "count"
        )
        INSERT INTO %(table)s("org_id", "label_type", "is_archived", "count", "is_squashed")
        VALUES (%%s, %%s, %%s, GREATEST(0, (SELECT SUM("count") FROM deleted)), TRUE);
        """ % {
            "table": cls._meta.db_table
        }

        return sql, (distinct_set.org_id, distinct_set.label_type, distinct_set.is_archived) * 2

    @classmethod
    def get_totals(cls, org, is_archived=False):
        """
        Gets all system label counts by type for the given org
        """
        counts = cls.objects.filter(org=org, is_archived=is_archived)
        counts = counts.values_list("label_type").annotate(count_sum=Sum("count"))
        counts_by_type = {c[0]: c[1] for c in counts}

        # for convenience, include all label types
        return {l: counts_by_type.get(l, 0) for l, n in SystemLabel.TYPE_CHOICES}

    class Meta:
        index_together = ("org", "label_type")


class UserFolderManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(label_type=Label.TYPE_FOLDER)


class UserLabelManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(label_type=Label.TYPE_LABEL)


class Label(TembaModel):
    """
    Labels represent both user defined labels and folders of labels. User defined labels that can be applied to messages
    much the same way labels or tags apply to messages in web-based email services.
    """

    MAX_NAME_LEN = 64
    MAX_ORG_LABELS = 250
    MAX_ORG_FOLDERS = 250

    TYPE_FOLDER = "F"
    TYPE_LABEL = "L"

    TYPE_CHOICES = ((TYPE_FOLDER, "Folder of labels"), (TYPE_LABEL, "Regular label"))

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="msgs_labels")

    name = models.CharField(max_length=MAX_NAME_LEN, verbose_name=_("Name"), help_text=_("The name of this label"))

    folder = models.ForeignKey(
        "Label", on_delete=models.PROTECT, verbose_name=_("Folder"), null=True, related_name="children"
    )

    label_type = models.CharField(max_length=1, choices=TYPE_CHOICES, default=TYPE_LABEL)

    # define some custom managers to do the filtering of label types for us
    all_objects = models.Manager()
    folder_objects = UserFolderManager()
    label_objects = UserLabelManager()

    @classmethod
    def get_or_create(cls, org, user, name, folder=None):
        assert not folder or folder.is_folder(), "folder must be a folder if provided"

        name = name.strip()

        if not cls.is_valid_name(name):
            raise ValueError("Invalid label name: %s" % name)

        label = cls.label_objects.filter(org=org, name__iexact=name, is_active=True).first()
        if label:
            return label

        return cls.label_objects.create(org=org, name=name, folder=folder, created_by=user, modified_by=user)

    @classmethod
    def get_or_create_folder(cls, org, user, name):
        name = name.strip()

        if not cls.is_valid_name(name):
            raise ValueError("Invalid folder name: %s" % name)

        folder = cls.folder_objects.filter(org=org, name__iexact=name, is_active=True).first()
        if folder:
            return folder

        return cls.folder_objects.create(
            org=org, name=name, label_type=Label.TYPE_FOLDER, created_by=user, modified_by=user
        )

    @classmethod
    def get_hierarchy(cls, org):
        """
        Gets labels and folders organized into their hierarchy and with their message counts
        """

        labels_and_folders = list(Label.all_objects.filter(org=org, is_active=True).order_by(Upper("name")))
        label_counts = LabelCount.get_totals([l for l in labels_and_folders if not l.is_folder()])

        folder_nodes = {}
        all_nodes = []
        for obj in labels_and_folders:
            node = {"obj": obj, "count": label_counts.get(obj), "children": []}
            all_nodes.append(node)

            if obj.is_folder():
                folder_nodes[obj.id] = node

        top_nodes = []
        for node in all_nodes:
            if node["obj"].folder_id is None:
                top_nodes.append(node)
            else:
                folder_nodes[node["obj"].folder_id]["children"].append(node)

        return top_nodes

    @classmethod
    def is_valid_name(cls, name):
        # don't allow empty strings, blanks, initial or trailing whitespace
        if not name or name.strip() != name:
            return False

        if len(name) > cls.MAX_NAME_LEN:
            return False

        # first character must be a word char
        return regex.match(r"\w", name[0], flags=regex.UNICODE)

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

        return LabelCount.get_totals([self])[self]

    def toggle_label(self, msgs, add):
        """
        Adds or removes this label from the given messages
        """

        assert not self.is_folder(), "can't assign messages to label folders"

        changed = set()

        for msg in msgs:
            if msg.direction != INCOMING:
                raise ValueError("Can only apply labels to incoming messages")

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

    def release(self, user):

        dependent_flows_count = self.dependent_flows.count()
        if dependent_flows_count > 0:
            raise ValueError(f"Cannot delete Label: {self.name}, used by {dependent_flows_count} flows")

        # release our children if we are a folder
        if self.is_folder():
            for label in self.children.all():
                label.release(user)
        else:
            Msg.labels.through.objects.filter(label=self).delete()

        self.counts.all().delete()

        self.is_active = False
        self.modified_by = user
        self.modified_on = timezone.now()
        self.save(update_fields=("is_active", "modified_by", "modified_on"))

    def __str__(self):
        if self.folder:
            return "%s > %s" % (str(self.folder), self.name)
        return self.name


class LabelCount(SquashableModel):
    """
    Counts of user labels maintained by database level triggers
    """

    SQUASH_OVER = ("label_id", "is_archived")

    label = models.ForeignKey(Label, on_delete=models.PROTECT, related_name="counts")

    is_archived = models.BooleanField(default=False, help_text=_("Whether this count is for archived messages"))

    count = models.IntegerField(default=0, help_text=_("Number of items with this system label"))

    @classmethod
    def get_squash_query(cls, distinct_set):
        sql = """
            WITH deleted as (
                DELETE FROM %(table)s WHERE "label_id" = %%s AND "is_archived" = %%s RETURNING "count"
            )
            INSERT INTO %(table)s("label_id", "is_archived", "count", "is_squashed")
            VALUES (%%s, %%s, GREATEST(0, (SELECT SUM("count") FROM deleted)), TRUE);
            """ % {
            "table": cls._meta.db_table
        }

        return sql, (distinct_set.label_id, distinct_set.is_archived) * 2

    @classmethod
    def get_totals(cls, labels, is_archived=False):
        """
        Gets total counts for all the given labels
        """
        counts = (
            cls.objects.filter(label__in=labels, is_archived=is_archived)
            .values_list("label_id")
            .annotate(count_sum=Sum("count"))
        )
        counts_by_label_id = {c[0]: c[1] for c in counts}
        return {l: counts_by_label_id.get(l.id, 0) for l in labels}


class MsgIterator:
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
        for i in range(0, len(self._ids), self.max_obj_num):
            chunk_queryset = Msg.objects.filter(id__in=self._ids[i : i + self.max_obj_num])

            if self._order_by:
                chunk_queryset = chunk_queryset.order_by(*self._order_by)

            if self._select_related:
                chunk_queryset = chunk_queryset.select_related(*self._select_related)

            if self._prefetch_related:
                chunk_queryset = chunk_queryset.prefetch_related(*self._prefetch_related)

            yield chunk_queryset

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._generator)


class ExportMessagesTask(BaseExportTask):
    """
    Wrapper for handling exports of raw messages. This will export all selected messages in
    an Excel spreadsheet, adding sheets as necessary to fall within the guidelines of Excel 97
    (the library we depend on requires this) which has column and row size limits.

    When the export is done, we store the file on the server and send an e-mail notice with a
    link to download the results.
    """

    analytics_key = "msg_export"
    email_subject = "Your messages export from %s is ready"
    email_template = "msgs/email/msg_export_download"

    groups = models.ManyToManyField(ContactGroup)

    label = models.ForeignKey(Label, on_delete=models.PROTECT, null=True)

    system_label = models.CharField(null=True, max_length=1)

    start_date = models.DateField(null=True, blank=True, help_text=_("The date for the oldest message to export"))

    end_date = models.DateField(null=True, blank=True, help_text=_("The date for the newest message to export"))

    @classmethod
    def create(cls, org, user, system_label=None, label=None, groups=(), start_date=None, end_date=None):
        if label and system_label:  # pragma: no cover
            raise ValueError("Can't specify both label and system label")

        export = cls.objects.create(
            org=org,
            system_label=system_label,
            label=label,
            start_date=start_date,
            end_date=end_date,
            created_by=user,
            modified_by=user,
        )
        export.groups.add(*groups)
        return export

    def _add_msgs_sheet(self, book):
        name = "Messages (%d)" % (book.num_msgs_sheets + 1) if book.num_msgs_sheets > 0 else "Messages"
        sheet = book.add_sheet(name, book.num_msgs_sheets)
        book.num_msgs_sheets += 1

        self.append_row(sheet, book.headers)
        return sheet

    def write_export(self):
        book = XLSXBook()
        book.num_msgs_sheets = 0

        book.headers = [
            "Date",
            "Contact UUID",
            "Name",
            "ID" if self.org.is_anon else "URN",
            "URN Type",
            "Direction",
            "Text",
            "Attachments",
            "Status",
            "Channel",
            "Labels",
        ]

        book.current_msgs_sheet = self._add_msgs_sheet(book)

        total_msgs_exported = 0
        temp_msgs_exported = 0

        start = time.time()

        contact_uuids = set()
        for group in self.groups.all():
            contact_uuids = contact_uuids.union(set(group.contacts.only("uuid").values_list("uuid", flat=True)))

        tz = self.org.timezone

        start_date = self.org.created_on
        if self.start_date:
            start_date = tz.localize(datetime.combine(self.start_date, datetime.min.time()))

        end_date = timezone.now()
        if self.end_date:
            end_date = tz.localize(datetime.combine(self.end_date, datetime.max.time()))

        for batch in self._get_msg_batches(self.system_label, self.label, start_date, end_date, contact_uuids):
            self._write_msgs(book, batch)

            total_msgs_exported += len(batch)

            # start logging
            if (total_msgs_exported - temp_msgs_exported) > ExportMessagesTask.LOG_PROGRESS_PER_ROWS:
                mins = (time.time() - start) / 60
                logger.info(
                    f"Msgs export #{self.id} for org #{self.org.id}: exported {total_msgs_exported} in {mins:.1f} mins"
                )
                temp_msgs_exported = total_msgs_exported

                self.modified_on = timezone.now()
                self.save(update_fields=["modified_on"])

        temp = NamedTemporaryFile(delete=True, suffix=".xlsx", mode="wb+")
        book.finalize(to_file=temp)
        temp.flush()
        return temp, "xlsx"

    def _get_msg_batches(self, system_label, label, start_date, end_date, group_contacts):
        logger.info(f"Msgs export #{self.id} for org #{self.org.id}: fetching msgs from archives to export...")

        # firstly get msgs from archives
        from temba.archives.models import Archive

        records = Archive.iter_all_records(self.org, Archive.TYPE_MSG, start_date, end_date)
        last_created_on = None

        for record_batch in chunk_list(records, 1000):
            matching = []
            for record in record_batch:
                created_on = iso8601.parse_date(record["created_on"])
                if last_created_on is None or last_created_on < created_on:
                    last_created_on = created_on

                if group_contacts and record["contact"]["uuid"] not in group_contacts:
                    continue

                visibility = "visible"
                if system_label:
                    visibility, direction, msg_type, statuses = SystemLabel.get_archive_attributes(system_label)

                    if record["direction"] != direction:
                        continue

                    if msg_type and record["type"] != msg_type:
                        continue

                    if statuses and record["status"] not in statuses:
                        continue

                elif label:
                    record_labels = [l["uuid"] for l in record["labels"]]
                    if label and label.uuid not in record_labels:
                        continue

                if record["visibility"] != visibility:
                    continue

                matching.append(record)
            yield matching

        if system_label:
            messages = SystemLabel.get_queryset(self.org, system_label)
        elif label:
            messages = label.get_messages()
        else:
            messages = Msg.get_messages(self.org)

        if self.start_date:
            messages = messages.filter(created_on__gte=start_date)

        if self.end_date:
            messages = messages.filter(created_on__lte=end_date)

        if self.groups.all():
            messages = messages.filter(contact__all_groups__in=self.groups.all())

        messages = messages.order_by("created_on")
        if last_created_on:
            messages = messages.filter(created_on__gt=last_created_on)

        all_message_ids = array(str("l"), messages.values_list("id", flat=True))

        logger.info(
            f"Msgs export #{self.id} for org #{self.org.id}: found {len(all_message_ids)} msgs in database to export"
        )

        prefetch = Prefetch("labels", queryset=Label.label_objects.order_by("name"))
        for msg_batch in MsgIterator(
            all_message_ids,
            order_by=["" "created_on"],
            select_related=["contact", "contact_urn", "channel"],
            prefetch_related=[prefetch],
        ):
            # convert this batch of msgs to same format as records in our archives
            yield [msg.as_archive_json() for msg in msg_batch]

    def _write_msgs(self, book, msgs):
        # get all the contacts referenced in this batch
        contact_uuids = {m["contact"]["uuid"] for m in msgs}
        contacts = Contact.objects.filter(org=self.org, uuid__in=contact_uuids)
        contacts_by_uuid = {str(c.uuid): c for c in contacts}

        for msg in msgs:
            contact = contacts_by_uuid.get(msg["contact"]["uuid"])

            urn_scheme = URN.to_parts(msg["urn"])[0] if msg["urn"] else ""

            # only show URN path if org isn't anon and there is a URN
            if self.org.is_anon:  # pragma: needs cover
                urn_path = f"{contact.id:010d}"
                urn_scheme = ""
            elif msg["urn"]:
                urn_path = URN.format(msg["urn"], international=False, formatted=False)
            else:
                urn_path = ""

            if book.current_msgs_sheet.num_rows >= self.MAX_EXCEL_ROWS:  # pragma: no cover
                book.current_msgs_sheet = self._add_msgs_sheet(book)

            self.append_row(
                book.current_msgs_sheet,
                [
                    iso8601.parse_date(msg["created_on"]),
                    msg["contact"]["uuid"],
                    msg["contact"].get("name", ""),
                    urn_path,
                    urn_scheme,
                    msg["direction"].upper() if msg["direction"] else None,
                    msg["text"],
                    ", ".join(attachment["url"] for attachment in msg["attachments"]),
                    msg["status"],
                    msg["channel"]["name"] if msg["channel"] else "",
                    ", ".join(msg_label["name"] for msg_label in msg["labels"]),
                ],
            )


@register_asset_store
class MessageExportAssetStore(BaseExportAssetStore):
    model = ExportMessagesTask
    key = "message_export"
    directory = "message_exports"
    permission = "msgs.msg_export"
    extensions = ("xlsx",)
