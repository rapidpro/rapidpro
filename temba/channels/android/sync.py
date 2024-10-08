from datetime import datetime, timezone as tzone

from temba.msgs.models import Msg


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


def update_message(msg, cmd):
    """
    Updates a message according to the provided client command
    """

    date = datetime.fromtimestamp(int(cmd["ts"]) // 1000).replace(tzinfo=tzone.utc)
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
