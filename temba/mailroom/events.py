from collections import defaultdict
from datetime import datetime

import iso8601

from django.conf import settings
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from temba.airtime.models import AirtimeTransfer
from temba.campaigns.models import EventFire
from temba.channels.models import Channel, ChannelEvent
from temba.flows.models import FlowExit, FlowRun
from temba.ivr.models import Call
from temba.msgs.models import Msg, OptIn
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
    TYPE_OPTIN_REQUESTED = "optin_requested"
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

        obj_age = timezone.now() - obj.created_on

        logs_url = None
        if obj.channel and obj_age < settings.RETENTION_PERIODS["channellog"]:
            logs_url = _url_for_user(
                org, user, "channels.channellog_msg", args=[obj.channel.uuid, obj.id], perm="channels.channellog_read"
            )

        if obj.direction == Msg.DIRECTION_IN:
            return {
                "type": cls.TYPE_MSG_RECEIVED,
                "created_on": get_event_time(obj).isoformat(),
                "msg": _msg_in(obj),
                # additional properties
                "msg_type": Msg.TYPE_VOICE if obj.msg_type == Msg.TYPE_VOICE else Msg.TYPE_TEXT,
                "visibility": obj.visibility,
                "logs_url": logs_url,
            }
        elif obj.broadcast and obj.broadcast.get_message_count() > 1:
            return {
                "type": cls.TYPE_BROADCAST_CREATED,
                "created_on": get_event_time(obj).isoformat(),
                "translations": obj.broadcast.translations,
                "base_language": obj.broadcast.base_language,
                # additional properties
                "created_by": _user(obj.broadcast.created_by) if obj.broadcast.created_by else None,
                "msg": _msg_out(obj),
                "optin": _optin(obj.optin) if obj.optin else None,
                "status": obj.status,
                "recipient_count": obj.broadcast.get_message_count(),
                "logs_url": logs_url,
            }
        else:
            created_by = obj.broadcast.created_by if obj.broadcast else obj.created_by

            if obj.msg_type == Msg.TYPE_VOICE:
                msg_event = {
                    "type": cls.TYPE_IVR_CREATED,
                    "created_on": get_event_time(obj).isoformat(),
                    "msg": _msg_out(obj),
                }
            elif obj.msg_type == Msg.TYPE_OPTIN and obj.optin:
                msg_event = {
                    "type": cls.TYPE_OPTIN_REQUESTED,
                    "created_on": get_event_time(obj).isoformat(),
                    "optin": _optin(obj.optin),
                    "channel": _channel(obj.channel),
                    "urn": str(obj.contact_urn),
                }
            else:
                msg_event = {
                    "type": cls.TYPE_MSG_CREATED,
                    "created_on": get_event_time(obj).isoformat(),
                    "msg": _msg_out(obj),
                    "optin": _optin(obj.optin) if obj.optin else None,
                }

            # add additional properties
            msg_event["created_by"] = _user(created_by) if created_by else None
            msg_event["status"] = obj.status
            msg_event["logs_url"] = logs_url

            if obj.status == Msg.STATUS_FAILED:
                msg_event["failed_reason"] = obj.failed_reason
                msg_event["failed_reason_display"] = obj.get_failed_reason_display()

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
    def from_ivr_call(cls, org: Org, user: User, obj: Call) -> dict:
        obj_age = timezone.now() - obj.created_on

        logs_url = None
        if obj_age < settings.RETENTION_PERIODS["channellog"]:
            logs_url = _url_for_user(
                org, user, "channels.channellog_call", args=[obj.channel.uuid, obj.id], perm="channels.channellog_read"
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
            },
            "created_on": get_event_time(obj).isoformat(),
            "created_by": _user(obj.created_by) if obj.created_by else None,
        }

    @classmethod
    def from_event_fire(cls, org: Org, user: User, obj: EventFire) -> dict:
        return {
            "type": cls.TYPE_CAMPAIGN_FIRED,
            "created_on": get_event_time(obj).isoformat(),
            "campaign": {
                "uuid": obj.event.campaign.uuid,
                "id": obj.event.campaign.id,
                "name": obj.event.campaign.name,
            },
            "campaign_event": {
                "id": obj.event.id,
                "offset_display": obj.event.offset_display,
                "relative_to": {"key": obj.event.relative_to.key, "name": obj.event.relative_to.name},
            },
            "fired_result": obj.fired_result,
        }

    @classmethod
    def from_channel_event(cls, org: Org, user: User, obj: ChannelEvent) -> dict:
        extra = obj.extra or {}
        ch_event = {"type": obj.event_type, "channel": _channel(obj.channel)}

        if obj.event_type in ChannelEvent.CALL_TYPES:
            ch_event["duration"] = extra.get("duration")
        elif obj.event_type in (ChannelEvent.TYPE_OPTIN, ChannelEvent.TYPE_OPTOUT):
            ch_event["optin"] = _optin(obj.optin) if obj.optin else None

        return {
            "type": cls.TYPE_CHANNEL_EVENT,
            "created_on": get_event_time(obj).isoformat(),
            "event": ch_event,
            "channel_event_type": obj.event_type,  # deprecated
            "duration": extra.get("duration"),  # deprecated
        }


def _url_for_user(org: Org, user: User, view_name: str, args: list, perm: str = None) -> str:
    return reverse(view_name, args=args) if user.has_org_perm(org, perm or view_name) else None


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
    redact = obj.visibility in (Msg.VISIBILITY_DELETED_BY_USER, Msg.VISIBILITY_DELETED_BY_SENDER)
    d = {
        "uuid": str(obj.uuid),
        "id": obj.id,
        "urn": str(obj.contact_urn) if obj.contact_urn else None,
        "channel": _channel(obj.channel) if obj.channel else None,
        "text": obj.text if not redact else "",
    }
    if obj.attachments:
        d["attachments"] = obj.attachments if not redact else []

    return d


def _user(user: User) -> dict:
    return {"id": user.id, "first_name": user.first_name, "last_name": user.last_name, "email": user.email}


def _channel(channel: Channel) -> dict:
    return {"uuid": str(channel.uuid), "name": channel.name}


def _topic(topic: Topic) -> dict:
    return {"uuid": str(topic.uuid), "name": topic.name}


def _optin(optin: OptIn) -> dict:
    return {"uuid": str(optin.uuid), "name": optin.name}


# map of history item types to methods to render them as events
event_renderers = {
    AirtimeTransfer: Event.from_airtime_transfer,
    ChannelEvent: Event.from_channel_event,
    EventFire: Event.from_event_fire,
    FlowExit: Event.from_flow_exit,
    FlowRun: Event.from_flow_run,
    Call: Event.from_ivr_call,
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
