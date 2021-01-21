class Event:
    """
    Utility class for working with engine events.
    """

    TYPE_AIRTIME_TRANSFERRED = "airtime_transferred"
    TYPE_BROADCAST_CREATED = "broadcast_created"
    TYPE_CONTACT_FIELD_CHANGED = "contact_field_changed"
    TYPE_CONTACT_GROUPS_CHANGED = "contact_groups_changed"
    TYPE_CONTACT_LANGUAGE_CHANGED = "contact_language_changed"
    TYPE_CONTACT_NAME_CHANGED = "contact_name_changed"
    TYPE_CONTACT_URNS_CHANGED = "contact_urns_changed"
    TYPE_EMAIL_SENT = "email_sent"
    TYPE_ERROR = "error"
    TYPE_FAILURE = "failure"
    TYPE_FLOW_ENTERED = "flow_entered"
    TYPE_INPUT_LABELS_ADDED = "input_labels_added"
    TYPE_IVR_CREATED = "ivr_created"
    TYPE_MSG_CREATED = "msg_created"
    TYPE_MSG_RECEIVED = "msg_received"
    TYPE_RUN_RESULT_CHANGED = "run_result_changed"
    TYPE_TICKET_OPENED = "ticket_opened"
    TYPE_WEBHOOK_CALLED = "webhook_called"

    @classmethod
    def from_msg(cls, obj) -> dict:
        """
        Reconstructs an engine event from a msg instance. Properties which aren't part of regular events are prefixed
        with an underscore.
        """
        from temba.msgs.models import INCOMING, IVR

        channel_log = obj.get_last_log()

        if obj.direction == INCOMING:
            return {
                "type": cls.TYPE_MSG_RECEIVED,
                "created_on": obj.created_on,
                "msg": _msg_in(obj),
                # additional properties
                "msg_type": obj.msg_type,
                "channel_log_id": channel_log.id if channel_log else None,
            }
        elif obj.broadcast and obj.broadcast.get_message_count() > 1:
            return {
                "type": cls.TYPE_BROADCAST_CREATED,
                "created_on": obj.created_on,
                "translations": obj.broadcast.text,
                "base_language": obj.broadcast.base_language,
                # additional properties
                "msg": _msg_out(obj),
                "recipient_count": obj.broadcast.get_message_count(),
                "channel_log_id": channel_log.id if channel_log else None,
            }
        elif obj.msg_type == IVR:
            return {
                "type": cls.TYPE_IVR_CREATED,
                "created_on": obj.created_on,
                "msg": _msg_out(obj),
                # additional properties
                "status": obj.status,
                "channel_log_id": channel_log.id if channel_log else None,
            }
        else:
            return {
                "type": cls.TYPE_MSG_CREATED,
                "created_on": obj.created_on,
                "msg": _msg_out(obj),
                # additional properties
                "status": obj.status,
                "channel_log_id": channel_log.id if channel_log else None,
            }


def _msg_in(obj) -> dict:
    d = _base_msg(obj)

    if obj.external_id:
        d["external_id"] = obj.external_id

    return d


def _msg_out(obj) -> dict:
    metadata = obj.metadata or {}
    quick_replies = metadata.get("quick_replies", [])
    d = _base_msg(obj)

    if quick_replies:
        d["quick_replies"] = quick_replies

    return d


def _base_msg(obj) -> dict:
    d = {
        "uuid": str(obj.uuid),
        "id": obj.id,
        "urn": str(obj.contact_urn) if obj.contact_urn else None,
        "text": obj.text,
    }
    if obj.channel:
        d["channel"] = {"uuid": str(obj.channel.uuid), "name": obj.channel.name}
    if obj.attachments:
        d["attachments"] = obj.attachments

    return d
