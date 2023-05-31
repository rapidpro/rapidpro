from datetime import datetime

import pytz
from pyfcm import FCMNotification

from django.conf import settings
from django.utils import timezone

from temba import mailroom
from temba.contacts.models import Contact
from temba.msgs.models import Msg
from temba.utils import on_transaction_commit
from temba.utils.text import clean_string

from ..models import Channel, ChannelEvent


def get_sync_commands(msgs):
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


def get_channel_commands(channel, commands, sync_event=None):
    """
    Generates sync commands for all queued messages on the given channel
    """

    msgs = Msg.objects.filter(status__in=Msg.STATUS_QUEUED, channel=channel, direction=Msg.DIRECTION_OUT)

    if sync_event:
        pending_msgs = sync_event.get_pending_messages()
        retry_msgs = sync_event.get_retry_messages()
        msgs = msgs.exclude(id__in=pending_msgs).exclude(id__in=retry_msgs)

    commands += get_sync_commands(msgs=msgs)

    return commands


def sync_channel_fcm(registration_id, channel=None):  # pragma: no cover
    push_service = FCMNotification(api_key=settings.FCM_API_KEY)
    fcm_failed = False
    try:
        result = push_service.notify_single_device(registration_id=registration_id, data_message=dict(msg="sync"))
        if not result.get("success", 0):
            fcm_failed = True
    except Exception:
        fcm_failed = True

    if fcm_failed:
        valid_registration_ids = push_service.clean_registration_ids([registration_id])
        if registration_id not in valid_registration_ids:
            # this fcm id is invalid now, clear it out
            channel.config.pop(Channel.CONFIG_FCM_ID, None)
            channel.save(update_fields=["config"])


def create_incoming(org, channel, urn, text, received_on, attachments=None):
    contact, contact_urn = Contact.resolve(channel, urn)

    # we limit our text message length and remove any invalid chars
    if text:
        text = clean_string(text[: Msg.MAX_TEXT_LEN])

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
        direction=Msg.DIRECTION_IN,
        attachments=attachments,
        status=Msg.STATUS_PENDING,
        msg_type=Msg.TYPE_TEXT,
    )

    # pass off handling of the message after we commit
    on_transaction_commit(lambda: msg.handle())

    return msg


def create_event(channel, urn, event_type, occurred_on, extra=None):
    contact, contact_urn = Contact.resolve(channel, urn)

    event = ChannelEvent.objects.create(
        org=channel.org,
        channel=channel,
        contact=contact,
        contact_urn=contact_urn,
        occurred_on=occurred_on,
        event_type=event_type,
        extra=extra,
    )

    if event_type == ChannelEvent.TYPE_CALL_IN_MISSED:
        # pass off handling of the message to mailroom after we commit
        on_transaction_commit(lambda: mailroom.queue_mo_miss_event(event))

    return event


def update_message(msg, cmd):
    """
    Updates a message according to the provided client command
    """

    date = datetime.fromtimestamp(int(cmd["ts"]) // 1000).replace(tzinfo=pytz.utc)
    keyword = cmd["cmd"]
    handled = False

    if keyword == "mt_error":
        msg.status = Msg.STATUS_ERRORED
        handled = True

    elif keyword == "mt_fail":
        msg.status = Msg.STATUS_FAILED
        handled = True

    elif keyword == "mt_sent":
        msg.status = Msg.STATUS_SENT
        msg.sent_on = date
        handled = True

    elif keyword == "mt_dlvd":
        msg.status = Msg.STATUS_DELIVERED
        msg.sent_on = msg.sent_on or date
        handled = True

    msg.save(update_fields=("status", "sent_on"))
    return handled
