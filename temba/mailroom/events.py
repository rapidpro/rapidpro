from collections import defaultdict
from datetime import datetime

import iso8601

from django.contrib.auth.models import User
from django.urls import reverse

from temba.airtime.models import AirtimeTransfer
from temba.campaigns.models import EventFire
from temba.channels.models import ChannelEvent
from temba.flows.models import FlowExit, FlowRun
from temba.ivr.models import IVRCall
from temba.msgs.models import Msg
from temba.orgs.models import Org
from temba.tickets.models import Ticket, TicketEvent, Topic


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
    TYPE_TICKET_ASSIGNED = "ticket_assigned"
    TYPE_TICKET_CLOSED = "ticket_closed"
    TYPE_TICKET_NOTE_ADDED = "ticket_note_added"
    TYPE_TICKET_TOPIC_CHANGED = "ticket_topic_changed"
    TYPE_TICKET_OPENED = "ticket_opened"
    TYPE_TICKET_REOPENED = "ticket_reopened"
    TYPE_WEBHOOK_CALLED = "webhook_called"

    # additional events
    TYPE_CALL_STARTED = "call_started"
    TYPE_CAMPAIGN_FIRED = "campaign_fired"
    TYPE_CHANNEL_EVENT = "channel_event"
    TYPE_FLOW_EXITED = "flow_exited"

    ticket_event_types = {
        TicketEvent.TYPE_OPENED: TYPE_TICKET_OPENED,
        TicketEvent.TYPE_ASSIGNED: TYPE_TICKET_ASSIGNED,
        TicketEvent.TYPE_NOTE_ADDED: TYPE_TICKET_NOTE_ADDED,
        TicketEvent.TYPE_TOPIC_CHANGED: TYPE_TICKET_TOPIC_CHANGED,
        TicketEvent.TYPE_CLOSED: TYPE_TICKET_CLOSED,
        TicketEvent.TYPE_REOPENED: TYPE_TICKET_REOPENED,
    }

    @classmethod
    def from_history_item(cls, org: Org, user: User, item) -> dict:
        if isinstance(item, dict):  # already an event
            return item

        renderer = event_renderers.get(type(item))
        assert renderer is not None, f"unsupported history item of type {type(item)}"

        return renderer(org, user, item)

    @classmethod
    def from_msg(cls, org: Org, user: User, obj: Msg) -> dict:
        """
        Reconstructs an engine event from a msg instance. Properties which aren't part of regular events are prefixed
        with an underscore.
        """

        channel_log = obj.get_last_log()
        logs_url = _url_for_user(org, user, "channels.channellog_read", args=[channel_log.id]) if channel_log else None

        if obj.direction == Msg.DIRECTION_IN:
            return {
                "type": cls.TYPE_MSG_RECEIVED,
                "created_on": get_event_time(obj).isoformat(),
                "msg": _msg_in(obj),
                # additional properties
                "msg_type": obj.msg_type,
                "logs_url": logs_url,
            }
        elif obj.broadcast and obj.broadcast.get_message_count() > 1:
            return {
                "type": cls.TYPE_BROADCAST_CREATED,
                "created_on": get_event_time(obj).isoformat(),
                "translations": obj.broadcast.text,
                "base_language": obj.broadcast.base_language,
                # additional properties
                "msg": _msg_out(obj),
                "status": obj.status,
                "recipient_count": obj.broadcast.get_message_count(),
                "logs_url": logs_url,
            }
        else:
            msg_event = {
                "type": cls.TYPE_IVR_CREATED if obj.msg_type == Msg.TYPE_IVR else cls.TYPE_MSG_CREATED,
                "created_on": get_event_time(obj).isoformat(),
                "msg": _msg_out(obj),
                # additional properties
                "status": obj.status,
                "logs_url": logs_url,
            }

            if obj.broadcast and obj.broadcast.created_by:
                user = obj.broadcast.created_by
                msg_event["msg"]["created_by"] = {
                    "id": user.id,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "email": user.email,
                }

            return msg_event

    @classmethod
    def from_flow_run(cls, org: Org, user: User, obj: FlowRun) -> dict:
        session = obj.session
        logs_url = _url_for_user(org, user, "flows.flowsession_json", args=[session.uuid]) if session else None

        return {
            "type": cls.TYPE_FLOW_ENTERED,
            "created_on": get_event_time(obj).isoformat(),
            "flow": {"uuid": str(obj.flow.uuid), "name": obj.flow.name},
            "logs_url": logs_url,
        }

    @classmethod
    def from_flow_exit(cls, org: Org, user: User, obj: FlowExit) -> dict:
        return {
            "type": cls.TYPE_FLOW_EXITED,
            "created_on": get_event_time(obj).isoformat(),
            "flow": {"uuid": str(obj.run.flow.uuid), "name": obj.run.flow.name},
            # additional properties
            "status": obj.run.status,
        }

    @classmethod
    def from_ivr_call(cls, org: Org, user: User, obj: IVRCall) -> dict:
        logs_url = (
            _url_for_user(org, user, "channels.channellog_connection", args=[obj.id]) if obj.has_logs() else None
        )

        return {
            "type": cls.TYPE_CALL_STARTED,
            "created_on": get_event_time(obj).isoformat(),
            "status": obj.status,
            "status_display": obj.status_display,
            "logs_url": logs_url,
        }

    @classmethod
    def from_airtime_transfer(cls, org: Org, user: User, obj: AirtimeTransfer) -> dict:
        logs_url = _url_for_user(org, user, "airtime.airtimetransfer_read", args=[obj.id])

        return {
            "type": cls.TYPE_AIRTIME_TRANSFERRED,
            "created_on": get_event_time(obj).isoformat(),
            "sender": obj.sender,
            "recipient": obj.recipient,
            "currency": obj.currency,
            "desired_amount": obj.desired_amount,
            "actual_amount": obj.actual_amount,
            # additional properties
            "logs_url": logs_url,
        }

    @classmethod
    def from_ticket_event(cls, org: Org, user: User, obj: TicketEvent) -> dict:
        ticket = obj.ticket
        return {
            "type": cls.ticket_event_types[obj.event_type],
            "note": obj.note,
            "topic": _topic(obj.topic) if obj.topic else None,
            "assignee": _user(obj.assignee) if obj.assignee else None,
            "ticket": {
                "uuid": str(ticket.uuid),
                "opened_on": ticket.opened_on.isoformat(),
                "closed_on": ticket.closed_on.isoformat() if ticket.closed_on else None,
                "topic": _topic(ticket.topic) if ticket.topic else None,
                "status": ticket.status,
                "body": ticket.body,
                "ticketer": {"uuid": str(ticket.ticketer.uuid), "name": ticket.ticketer.name},
            },
            "created_on": get_event_time(obj).isoformat(),
            "created_by": _user(obj.created_by) if obj.created_by else None,
        }

    @classmethod
    def from_event_fire(cls, org: Org, user: User, obj: EventFire) -> dict:
        return {
            "type": cls.TYPE_CAMPAIGN_FIRED,
            "created_on": get_event_time(obj).isoformat(),
            "campaign": {"id": obj.event.campaign.id, "name": obj.event.campaign.name},
            "campaign_event": {
                "id": obj.event.id,
                "offset_display": obj.event.offset_display,
                "relative_to": {"key": obj.event.relative_to.key, "name": obj.event.relative_to.label},
            },
            "fired_result": obj.fired_result,
        }

    @classmethod
    def from_channel_event(cls, org: Org, user: User, obj: ChannelEvent) -> dict:
        extra = obj.extra or {}
        return {
            "type": cls.TYPE_CHANNEL_EVENT,
            "created_on": get_event_time(obj).isoformat(),
            "channel_event_type": obj.event_type,
            "duration": extra.get("duration"),
        }


def _url_for_user(org: Org, user: User, view_name: str, args: list) -> str:
    return reverse(view_name, args=args) if user.has_org_perm(org, view_name) else None


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


def _user(user: User) -> dict:
    return {
        "id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
    }


def _topic(topic: Topic) -> dict:
    return {"uuid": str(topic.uuid), "name": topic.name}


# map of history item types to methods to render them as events
event_renderers = {
    AirtimeTransfer: Event.from_airtime_transfer,
    ChannelEvent: Event.from_channel_event,
    EventFire: Event.from_event_fire,
    FlowExit: Event.from_flow_exit,
    FlowRun: Event.from_flow_run,
    IVRCall: Event.from_ivr_call,
    Msg: Event.from_msg,
    TicketEvent: Event.from_ticket_event,
}

# map of history item types to a callable which can extract the event time from that type
event_time = defaultdict(lambda: lambda i: i.created_on)
event_time.update(
    {
        dict: lambda e: iso8601.parse_date(e["created_on"]),
        EventFire: lambda e: e.fired,
        FlowExit: lambda e: e.run.exited_on,
        Ticket: lambda e: e.closed_on,
    },
)


def get_event_time(item) -> datetime:
    """
    Extracts the event time from a history item
    """
    return event_time[type(item)](item)
