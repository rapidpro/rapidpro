import iso8601

from django.urls import reverse


class Event:
    """
    Utility class for working with engine events.
    """

    # engine events
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

    # additional events
    TYPE_CALL_STARTED = "call_started"
    TYPE_CAMPAIGN_FIRED = "campaign_fired"
    TYPE_CHANNEL_EVENT = "channel_event"
    TYPE_FLOW_EXITED = "flow_exited"

    @classmethod
    def from_history_item(cls, item) -> dict:
        if isinstance(item, dict):  # already an event
            item["created_on"] = iso8601.parse_date(item["created_on"])
            return item

        from temba.airtime.models import AirtimeTransfer
        from temba.api.models import WebHookResult
        from temba.campaigns.models import EventFire
        from temba.channels.models import ChannelEvent
        from temba.flows.models import FlowRun, FlowExit
        from temba.ivr.models import IVRCall
        from temba.msgs.models import Msg

        renderers = {
            AirtimeTransfer: cls.from_airtime_transfer,
            ChannelEvent: cls.from_channel_event,
            EventFire: cls.from_event_fire,
            FlowExit: cls.from_flow_exit,
            FlowRun: cls.from_flow_run,
            IVRCall: cls.from_ivr_call,
            Msg: cls.from_msg,
            WebHookResult: cls.from_webhook_result,
        }

        renderer = renderers.get(type(item))
        assert renderer is not None, f"unsupported history item of type {type(item)}"

        return renderer(item)

    @classmethod
    def from_msg(cls, obj) -> dict:
        """
        Reconstructs an engine event from a msg instance. Properties which aren't part of regular events are prefixed
        with an underscore.
        """
        from temba.msgs.models import INCOMING, IVR

        channel_log = obj.get_last_log()
        logs_url = reverse("channels.channellog_read", args=[channel_log.id]) if channel_log else None

        if obj.direction == INCOMING:
            return {
                "type": cls.TYPE_MSG_RECEIVED,
                "created_on": obj.created_on,
                "msg": _msg_in(obj),
                # additional properties
                "msg_type": obj.msg_type,
                "logs_url": logs_url,
            }
        elif obj.broadcast and obj.broadcast.get_message_count() > 1:
            return {
                "type": cls.TYPE_BROADCAST_CREATED,
                "created_on": obj.created_on,
                "translations": obj.broadcast.text,
                "base_language": obj.broadcast.base_language,
                # additional properties
                "msg": _msg_out(obj),
                "status": obj.status,
                "recipient_count": obj.broadcast.get_message_count(),
                "logs_url": logs_url,
            }
        elif obj.msg_type == IVR:
            return {
                "type": cls.TYPE_IVR_CREATED,
                "created_on": obj.created_on,
                "msg": _msg_out(obj),
                # additional properties
                "status": obj.status,
                "logs_url": logs_url,
            }
        else:
            return {
                "type": cls.TYPE_MSG_CREATED,
                "created_on": obj.created_on,
                "msg": _msg_out(obj),
                # additional properties
                "status": obj.status,
                "logs_url": logs_url,
            }

    @classmethod
    def from_flow_run(cls, obj) -> dict:
        return {
            "type": cls.TYPE_FLOW_ENTERED,
            "created_on": obj.created_on,
            "flow": {"uuid": str(obj.flow.uuid), "name": obj.flow.name},
            "logs_url": reverse("flows.flowsession_json", args=[obj.session.uuid]) if obj.session else None,
        }

    @classmethod
    def from_flow_exit(cls, obj) -> dict:
        return {
            "type": cls.TYPE_FLOW_EXITED,
            "created_on": obj.run.exited_on,
            "flow": {"uuid": str(obj.run.flow.uuid), "name": obj.run.flow.name},
            # additional properties
            "status": obj.run.status,
        }

    @classmethod
    def from_ivr_call(cls, obj) -> dict:
        return {
            "type": cls.TYPE_CALL_STARTED,
            "created_on": obj.created_on,
            "status": obj.status,
            "status_display": obj.get_status_display(),
            "logs_url": reverse("channels.channellog_connection", args=[obj.id]) if obj.has_logs() else None,
        }

    @classmethod
    def from_airtime_transfer(cls, obj) -> dict:
        return {
            "type": cls.TYPE_AIRTIME_TRANSFERRED,
            "created_on": obj.created_on,
            "sender": obj.sender,
            "recipient": obj.recipient,
            "currency": obj.currency,
            "desired_amount": obj.desired_amount,
            "actual_amount": obj.actual_amount,
            # additional properties
            "logs_url": reverse("airtime.airtimetransfer_read", args=[obj.id]),
        }

    @classmethod
    def from_webhook_result(cls, obj) -> dict:
        return {
            "type": cls.TYPE_WEBHOOK_CALLED,
            "created_on": obj.created_on,
            "url": obj.url,
            "status": "success" if obj.is_success else "response_error",
            "status_code": obj.status_code,
            "elapsed_ms": obj.request_time,
            # additional properties
            "logs_url": reverse("api.webhookresult_read", args=[obj.id]),
        }

    @classmethod
    def from_event_fire(cls, obj) -> dict:
        return {
            "type": cls.TYPE_CAMPAIGN_FIRED,
            "created_on": obj.fired,
            "campaign": {"id": obj.event.campaign.id, "name": obj.event.campaign.name},
            "campaign_event": {
                "id": obj.event.id,
                "offset_display": obj.event.offset_display,
                "relative_to": {"key": obj.event.relative_to.key, "name": obj.event.relative_to.label},
            },
            "fired_result": obj.fired_result,
        }

    @classmethod
    def from_channel_event(cls, obj) -> dict:
        extra = obj.extra or {}
        return {
            "type": cls.TYPE_CHANNEL_EVENT,
            "created_on": obj.created_on,
            "channel_event_type": obj.event_type,
            "duration": extra.get("duration"),
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
