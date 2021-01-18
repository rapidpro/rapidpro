class Event:
    """
    Utility class for working with engine events.
    """

    TYPE_CONTACT_FIELD_CHANGED = "contact_field_changed"
    TYPE_CONTACT_GROUPS_CHANGED = "contact_groups_changed"
    TYPE_CONTACT_LANGUAGE_CHANGED = "contact_language_changed"
    TYPE_CONTACT_NAME_CHANGED = "contact_name_changed"
    TYPE_CONTACT_URNS_CHANGED = "contact_urns_changed"
    TYPE_EMAIL_SENT = "email_sent"
    TYPE_ERROR = "error"
    TYPE_FAILURE = "failure"
    TYPE_INPUT_LABELS_ADDED = "input_labels_added"
    TYPE_MSG_CREATED = "msg_created"
    TYPE_MSG_RECEIVED = "msg_received"
    TYPE_RUN_RESULT_CHANGED = "run_result_changed"
    TYPE_TICKET_OPENED = "ticket_opened"

    @classmethod
    def from_msg_in(cls, obj) -> dict:
        return {
            "type": cls.TYPE_MSG_RECEIVED,
            "created_on": obj.created_on,
            "msg": _msg_in(obj),
        }

    @classmethod
    def from_msg_out(cls, obj) -> dict:
        return {
            "type": "msg_created",
            "created_on": obj.created_on,
            "msg": _msg_out(obj),
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
        d["channel"] = {"uuid": str(obj.uuid), "name": obj.name}
    if obj.attachments:
        d["attachments"] = obj.attachments

    return d
