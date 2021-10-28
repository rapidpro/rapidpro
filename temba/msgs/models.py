import logging
import time
from array import array
from datetime import datetime, timedelta
from typing import Dict, List

import iso8601
import pytz
import regex
from xlsxlite.writer import XLSXBook

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.postgres.fields import ArrayField
from django.core.files.temp import NamedTemporaryFile
from django.db import models, transaction
from django.db.models import Prefetch, Q, Sum
from django.db.models.functions import Upper
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba import mailroom
from temba.assets.models import register_asset_store
from temba.channels.courier import push_courier_msgs
from temba.channels.models import Channel, ChannelEvent, ChannelLog
from temba.contacts.models import URN, Contact, ContactGroup, ContactURN
from temba.orgs.models import DependencyMixin, Org, TopUp
from temba.schedules.models import Schedule
from temba.utils import chunk_list, on_transaction_commit
from temba.utils.export import BaseExportAssetStore, BaseExportTask
from temba.utils.models import JSONAsTextField, SquashableModel, TembaModel, TranslatableField
from temba.utils.text import clean_string
from temba.utils.uuid import uuid4

logger = logging.getLogger(__name__)


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

    STATUS_INITIALIZING = "I"
    STATUS_QUEUED = "Q"
    STATUS_SENT = "S"
    STATUS_FAILED = "F"
    STATUS_CHOICES = (
        (STATUS_INITIALIZING, "Initializing"),
        (STATUS_QUEUED, "Queued"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
    )

    MAX_TEXT_LEN = settings.MSG_FIELD_SIZE

    TEMPLATE_STATE_LEGACY = "legacy"
    TEMPLATE_STATE_EVALUATED = "evaluated"
    TEMPLATE_STATE_UNEVALUATED = "unevaluated"
    TEMPLATE_STATE_CHOICES = (TEMPLATE_STATE_LEGACY, TEMPLATE_STATE_EVALUATED, TEMPLATE_STATE_UNEVALUATED)

    METADATA_QUICK_REPLIES = "quick_replies"
    METADATA_TEMPLATE_STATE = "template_state"

    org = models.ForeignKey(Org, on_delete=models.PROTECT)

    # recipients of this broadcast
    groups = models.ManyToManyField(ContactGroup, related_name="addressed_broadcasts")
    contacts = models.ManyToManyField(Contact, related_name="addressed_broadcasts")
    urns = models.ManyToManyField(ContactURN, related_name="addressed_broadcasts")

    # URN strings that mailroom will turn into contacts and URN objects
    raw_urns = ArrayField(models.TextField(), null=True)

    # message content
    base_language = models.CharField(max_length=4)
    text = TranslatableField(max_length=MAX_TEXT_LEN)
    media = TranslatableField(max_length=2048, null=True)

    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, null=True)
    ticket = models.ForeignKey("tickets.Ticket", on_delete=models.PROTECT, null=True, related_name="broadcasts")

    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=STATUS_INITIALIZING)

    schedule = models.OneToOneField(Schedule, on_delete=models.PROTECT, null=True, related_name="broadcast")

    # used for repeating scheduled broadcasts
    parent = models.ForeignKey("Broadcast", on_delete=models.PROTECT, null=True, related_name="children")

    created_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="broadcast_creations")
    created_on = models.DateTimeField(default=timezone.now, db_index=True)  # TODO remove index
    modified_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="broadcast_modifications")
    modified_on = models.DateTimeField(default=timezone.now)

    # whether this broadcast should send to all URNs for each contact
    send_all = models.BooleanField(default=False)

    metadata = JSONAsTextField(null=True, default=dict)

    @classmethod
    def create(
        cls,
        org,
        user,
        text,
        *,
        groups=None,
        contacts=None,
        urns: List[str] = None,
        contact_ids: List[int] = None,
        base_language: str = None,
        channel: Channel = None,
        ticket=None,
        media: Dict = None,
        send_all: bool = False,
        quick_replies: List[Dict] = None,
        template_state: str = TEMPLATE_STATE_LEGACY,
        status: str = STATUS_INITIALIZING,
        **kwargs,
    ):
        # for convenience broadcasts can still be created with single translation and no base_language
        if isinstance(text, str):
            base_language = org.flow_languages[0] if org.flow_languages else "base"
            text = {base_language: text}

        assert groups or contacts or contact_ids or urns, "can't create broadcast without recipients"
        assert base_language in text, "base_language doesn't exist in text translations"
        assert not media or base_language in media, "base_language doesn't exist in media translations"

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
            ticket=ticket,
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

    def send_async(self):
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

    def get_text(self, contact=None):
        """
        Gets the text that will be sent. If contact is provided and their language is a valid flow language and there's
        a translation for it then that will be used (used when rendering upcoming scheduled broadcasts).
        """

        if contact and contact.language and contact.language in self.org.flow_languages:  # try contact language
            if contact.language in self.text:
                return self.text[contact.language]

        if self.org.flow_languages and self.org.flow_languages[0] in self.text:  # try org primary language
            return self.text[self.org.flow_languages[0]]

        return self.text[self.base_language]  # should always be a base language translation

    def release(self):
        for child in self.children.all():
            child.release()

        for msg in self.msgs.all():
            msg.release()

        BroadcastMsgCount.objects.filter(broadcast=self).delete()

        self.delete()

        if self.schedule:
            self.schedule.delete()

    def update_recipients(self, *, groups=None, contacts=None, urns: List[str] = None):
        """
        Only used to update recipients for scheduled / repeating broadcasts
        """
        # clear our current recipients
        self.groups.clear()
        self.contacts.clear()

        self._set_recipients(groups=groups, contacts=contacts, urns=urns)

    def _set_recipients(self, *, groups=None, contacts=None, urns: List[str] = None, contact_ids=None):
        """
        Sets the recipients which may be contact groups, contacts or contact URNs.
        """
        if groups:
            self.groups.add(*groups)

        if contacts:
            self.contacts.add(*contacts)

        if urns:
            self.raw_urns = urns
            self.save(update_fields=("raw_urns",))

        if contact_ids:
            RelatedModel = self.contacts.through
            for chunk in chunk_list(contact_ids, 1000):
                bulk_contacts = [RelatedModel(contact_id=id, broadcast_id=self.id) for id in chunk]
                RelatedModel.objects.bulk_create(bulk_contacts)

    def get_template_state(self):
        metadata = self.metadata or {}
        return metadata.get(Broadcast.METADATA_TEMPLATE_STATE, Broadcast.TEMPLATE_STATE_LEGACY)

    def __str__(self):  # pragma: no cover
        return f"Broadcast[id={self.id}, text={self.text}]"

    class Meta:
        indexes = [
            # used by the broadcasts API endpoint
            models.Index(name="msgs_broadcasts_org_created_id", fields=["org", "-created_on", "-id"]),
        ]


class Attachment:
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

    STATUS_INITIALIZING = "I"  # used to hold off sending the message until the flow is ready to receive a response
    STATUS_PENDING = "P"  # initial state for all messages
    STATUS_QUEUED = "Q"
    STATUS_WIRED = "W"  # message was handed off to the provider and credits were deducted for it
    STATUS_SENT = "S"  # we have confirmation that a message was sent
    STATUS_DELIVERED = "D"  # we have confirmation that a message was delivered
    STATUS_HANDLED = "H"
    STATUS_ERRORED = "E"  # there was an error during delivery
    STATUS_FAILED = "F"  # we gave up on sending this message
    STATUS_RESENT = "R"  # we retried this message (no longer used)
    STATUS_CHOICES = (
        (STATUS_INITIALIZING, _("Initializing")),
        (STATUS_PENDING, _("Pending")),
        (STATUS_QUEUED, _("Queued")),
        (STATUS_WIRED, _("Wired")),
        (STATUS_SENT, _("Sent")),
        (STATUS_DELIVERED, _("Delivered")),
        (STATUS_HANDLED, _("Handled")),
        (STATUS_ERRORED, _("Error")),
        (STATUS_FAILED, _("Failed")),
        (STATUS_RESENT, _("Resent")),
    )

    VISIBILITY_VISIBLE = "V"
    VISIBILITY_ARCHIVED = "A"
    VISIBILITY_DELETED = "D"
    VISIBILITY_CHOICES = (
        (VISIBILITY_VISIBLE, "Visible"),
        (VISIBILITY_ARCHIVED, "Archived"),
        (VISIBILITY_DELETED, "Deleted"),
    )

    DIRECTION_IN = "I"
    DIRECTION_OUT = "O"
    DIRECTION_CHOICES = ((DIRECTION_IN, "Incoming"), (DIRECTION_OUT, "Outgoing"))

    TYPE_INBOX = "I"
    TYPE_FLOW = "F"
    TYPE_IVR = "V"
    TYPE_USSD = "U"
    TYPE_CHOICES = (
        (TYPE_INBOX, "Inbox Message"),
        (TYPE_FLOW, "Flow Message"),
        (TYPE_IVR, "IVR Message"),
        (TYPE_USSD, "USSD Message"),
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

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(null=True, default=uuid4)
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="msgs")
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, null=True, related_name="msgs")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="msgs", db_index=False)
    contact_urn = models.ForeignKey(ContactURN, on_delete=models.PROTECT, null=True, related_name="msgs")
    broadcast = models.ForeignKey(Broadcast, on_delete=models.PROTECT, null=True, blank=True, related_name="msgs")

    text = models.TextField()
    attachments = ArrayField(models.URLField(max_length=2048), null=True)

    high_priority = models.BooleanField(null=True)

    created_on = models.DateTimeField(db_index=True)
    modified_on = models.DateTimeField(null=True, blank=True, auto_now=True)
    sent_on = models.DateTimeField(null=True)
    queued_on = models.DateTimeField(null=True)

    msg_type = models.CharField(max_length=1, choices=TYPE_CHOICES, null=True)
    direction = models.CharField(max_length=1, choices=DIRECTION_CHOICES)
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    visibility = models.CharField(max_length=1, choices=VISIBILITY_CHOICES, default=VISIBILITY_VISIBLE)

    response_to = models.ForeignKey(
        "Msg", on_delete=models.PROTECT, null=True, blank=True, related_name="responses", db_index=False
    )

    labels = models.ManyToManyField("Label", related_name="msgs")

    # the number of actual messages the channel sent this as (outgoing only)
    msg_count = models.IntegerField(default=1)

    # the number of times this message has errored (outgoing only)
    error_count = models.IntegerField(default=0)

    # when we'll next try to send this message (only set for retries after an error)
    next_attempt = models.DateTimeField(null=True)

    # the id of this message on the other side of its channel
    external_id = models.CharField(max_length=255, null=True, blank=True)

    topup = models.ForeignKey(TopUp, null=True, blank=True, related_name="msgs", on_delete=models.PROTECT)

    # used for IVR sessions on channels
    connection = models.ForeignKey(
        "channels.ChannelConnection", on_delete=models.PROTECT, related_name="msgs", null=True
    )

    metadata = JSONAsTextField(null=True, default=dict)

    # why this message is being deleted - determines what happens with counts
    delete_reason = models.CharField(null=True, max_length=1, choices=DELETE_CHOICES)

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
                    .exclude(msg_type=cls.TYPE_IVR)
                    .exclude(status=cls.STATUS_FAILED)
                )
                send_messages.update(status=cls.STATUS_QUEUED, queued_on=queued_on, modified_on=queued_on)

                # now push each onto our queue
                for msg in msgs:

                    # in development mode, don't actual send any messages
                    if not settings.SEND_MESSAGES:
                        msg.status = cls.STATUS_WIRED
                        msg.sent_on = timezone.now()
                        msg.save(update_fields=("status", "sent_on"))
                        logger.debug(f"FAKED SEND for [{msg.id}]")
                        continue

                    if (
                        (msg.msg_type != cls.TYPE_IVR and msg.channel and not msg.channel.is_android())
                        and msg.status != cls.STATUS_FAILED
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
        messages = cls.objects.filter(org=org)

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
        statuses = (cls.STATUS_QUEUED, cls.STATUS_PENDING, cls.STATUS_ERRORED)
        failed_messages = Msg.objects.filter(
            created_on__lte=one_week_ago, direction=Msg.DIRECTION_OUT, status__in=statuses
        )

        # fail our messages
        failed_messages.update(status=cls.STATUS_FAILED, modified_on=timezone.now())

    def as_archive_json(self):
        """
        Returns this message in the same format as archived by rp-archiver which is based on the API format
        """
        from temba.api.v2.serializers import MsgReadSerializer

        return {
            "id": self.id,
            "contact": {"uuid": str(self.contact.uuid), "name": self.contact.name},
            "channel": {"uuid": str(self.channel.uuid), "name": self.channel.name} if self.channel else None,
            "urn": self.contact_urn.identity if self.contact_urn else None,
            "direction": "in" if self.direction == Msg.DIRECTION_IN else "out",
            "type": MsgReadSerializer.TYPES.get(self.msg_type),
            "status": MsgReadSerializer.STATUSES.get(self.status),
            "visibility": MsgReadSerializer.VISIBILITIES.get(self.visibility),
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
            self.status = self.STATUS_ERRORED
            handled = True

        elif keyword == "mt_fail":
            self.status = self.STATUS_FAILED
            handled = True

        elif keyword == "mt_sent":
            self.status = self.STATUS_SENT
            self.sent_on = date
            handled = True

        elif keyword == "mt_dlvd":
            self.status = self.STATUS_DELIVERED
            self.sent_on = self.sent_on or date
            handled = True

        self.save(update_fields=("status", "sent_on"))
        return handled

    def handle(self):
        """
        Queues this message to be handled
        """

        mailroom.queue_msg_handling(self)

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
        contact, contact_urn = Contact.resolve(channel, urn)

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
            direction=cls.DIRECTION_IN,
            attachments=attachments,
            status=cls.STATUS_PENDING,
        )

        # pass off handling of the message after we commit
        on_transaction_commit(lambda: msg.handle())

        return msg

    def archive(self):
        """
        Archives this message
        """
        if self.direction != self.DIRECTION_IN:
            raise ValueError("Can only archive incoming non-test messages")

        self.visibility = self.VISIBILITY_ARCHIVED
        self.save(update_fields=("visibility", "modified_on"))

    @classmethod
    def archive_all_for_contacts(cls, contacts):
        """
        Archives all incoming messages for the given contacts
        """
        msgs = Msg.objects.filter(direction=cls.DIRECTION_IN, visibility=cls.VISIBILITY_VISIBLE, contact__in=contacts)
        msg_ids = list(msgs.values_list("pk", flat=True))

        # update modified on in small batches to avoid long table lock, and having too many non-unique values for
        # modified_on which is the primary ordering for the API
        for batch in chunk_list(msg_ids, 100):
            Msg.objects.filter(pk__in=batch).update(visibility=cls.VISIBILITY_ARCHIVED, modified_on=timezone.now())

    def restore(self):
        """
        Restores (i.e. un-archives) this message
        """
        if self.direction != self.DIRECTION_IN:  # pragma: needs cover
            raise ValueError("Can only restore incoming non-test messages")

        self.visibility = self.VISIBILITY_VISIBLE
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
        if msgs:
            mailroom.get_client().msg_resend(msgs[0].org.id, [m.id for m in msgs])

    class Meta:
        indexes = [
            # used for finding errored messages to retry
            models.Index(
                name="msgs_next_attempt_out_errored",
                fields=["next_attempt", "created_on", "id"],
                condition=Q(direction="O", status="E", next_attempt__isnull=False),
            ),
            # used for view of sent messages
            models.Index(
                name="msgs_outgoing_visible_sent",
                fields=["org", "-sent_on", "-id"],
                condition=Q(direction="O", visibility="V", status__in=("W", "S", "D")),
            ),
        ]
        constraints = [
            models.CheckConstraint(
                name="no_sent_status_without_sent_on",
                check=(~Q(status__in=("W", "S", "D"), sent_on__isnull=True)),
            ),
        ]


class BroadcastMsgCount(SquashableModel):
    """
    Maintains count of how many msgs are tied to a broadcast
    """

    squash_over = ("broadcast_id",)

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
        return cls.sum(broadcast.counts.all())

    def __str__(self):  # pragma: needs cover
        return f"BroadcastMsgCount[{self.broadcast_id}:{self.count}]"


class SystemLabel:
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
            qs = Msg.objects.filter(
                direction=Msg.DIRECTION_IN, visibility=Msg.VISIBILITY_VISIBLE, msg_type=Msg.TYPE_INBOX
            )
        elif label_type == cls.TYPE_FLOWS:
            qs = Msg.objects.filter(
                direction=Msg.DIRECTION_IN, visibility=Msg.VISIBILITY_VISIBLE, msg_type=Msg.TYPE_FLOW
            )
        elif label_type == cls.TYPE_ARCHIVED:
            qs = Msg.objects.filter(direction=Msg.DIRECTION_IN, visibility=Msg.VISIBILITY_ARCHIVED)
        elif label_type == cls.TYPE_OUTBOX:
            qs = Msg.objects.filter(
                direction=Msg.DIRECTION_OUT,
                visibility=Msg.VISIBILITY_VISIBLE,
                status__in=(Msg.STATUS_PENDING, Msg.STATUS_QUEUED),
            )
        elif label_type == cls.TYPE_SENT:
            qs = Msg.objects.filter(
                direction=Msg.DIRECTION_OUT,
                visibility=Msg.VISIBILITY_VISIBLE,
                status__in=(Msg.STATUS_WIRED, Msg.STATUS_SENT, Msg.STATUS_DELIVERED),
            )
        elif label_type == cls.TYPE_FAILED:
            qs = Msg.objects.filter(
                direction=Msg.DIRECTION_OUT, visibility=Msg.VISIBILITY_VISIBLE, status=Msg.STATUS_FAILED
            )
        elif label_type == cls.TYPE_SCHEDULED:
            qs = Broadcast.objects.exclude(schedule=None).prefetch_related("groups", "contacts", "urns")
        elif label_type == cls.TYPE_CALLS:
            qs = ChannelEvent.objects.filter(event_type__in=ChannelEvent.CALL_TYPES)
        else:  # pragma: needs cover
            raise ValueError("Invalid label type: %s" % label_type)

        return qs.filter(org=org)

    @classmethod
    def get_archive_attributes(cls, label_type: str) -> tuple:
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

    squash_over = ("org_id", "label_type", "is_archived")

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="system_labels")
    label_type = models.CharField(max_length=1, choices=SystemLabel.TYPE_CHOICES)
    is_archived = models.BooleanField(default=False)
    count = models.IntegerField(default=0)

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


class Label(TembaModel, DependencyMixin):
    """
    Labels represent both user defined labels and folders of labels. User defined labels that can be applied to messages
    much the same way labels or tags apply to messages in web-based email services.
    """

    MAX_NAME_LEN = 64
    MAX_ORG_FOLDERS = 250

    TYPE_FOLDER = "F"
    TYPE_LABEL = "L"

    TYPE_CHOICES = ((TYPE_FOLDER, "Folder of labels"), (TYPE_LABEL, "Regular label"))

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="msgs_labels")
    name = models.CharField(max_length=MAX_NAME_LEN)
    folder = models.ForeignKey("Label", on_delete=models.PROTECT, null=True, related_name="children")
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
            if msg.direction != Msg.DIRECTION_IN:
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

    def has_child_labels(self):
        return self.children.filter(is_active=True).exists()

    def is_folder(self):
        return self.label_type == Label.TYPE_FOLDER

    def release(self, user):
        assert not self.has_child_labels(), "can't release non-empty label folder"

        if not self.is_folder():
            super().release(user)  # releases flow dependencies

            # delete labellings of messages with this label (not the actual messages)
            Msg.labels.through.objects.filter(label=self).delete()

        self.counts.all().delete()

        self.is_active = False
        self.modified_by = user
        self.save(update_fields=("is_active", "modified_by", "modified_on"))

    def __str__(self):
        if self.folder:
            return "%s > %s" % (str(self.folder), self.name)
        return self.name


class LabelCount(SquashableModel):
    """
    Counts of user labels maintained by database level triggers
    """

    squash_over = ("label_id", "is_archived")

    label = models.ForeignKey(Label, on_delete=models.PROTECT, related_name="counts")
    is_archived = models.BooleanField(default=False)
    count = models.IntegerField(default=0)

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
    notification_export_type = "message"

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

        where = {"visibility": "visible"}
        if system_label:
            visibility, direction, msg_type, statuses = SystemLabel.get_archive_attributes(system_label)
            where["direction"] = direction
            if msg_type:
                where["type"] = msg_type
            if statuses:
                where["status__in"] = statuses
        elif label:
            where["__raw__"] = f"'{label.uuid}' IN s.labels[*].uuid[*]"

        records = Archive.iter_all_records(self.org, Archive.TYPE_MSG, start_date, end_date, where=where)
        last_created_on = None

        for record_batch in chunk_list(records, 1000):
            matching = []
            for record in record_batch:
                created_on = iso8601.parse_date(record["created_on"])
                if last_created_on is None or last_created_on < created_on:
                    last_created_on = created_on

                if group_contacts and record["contact"]["uuid"] not in group_contacts:
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

        messages = messages.order_by("created_on").using("readonly")
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
