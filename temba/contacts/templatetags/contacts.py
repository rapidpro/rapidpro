from django import template
from django.utils.safestring import mark_safe

from temba.campaigns.models import EventFire
from temba.channels.models import ChannelEvent
from temba.contacts.models import URN, ContactField, ContactURN
from temba.flows.models import FlowRun
from temba.ivr.models import IVRCall
from temba.mailroom.events import Event
from temba.msgs.models import Msg

register = template.Library()

URN_SCHEME_ICONS = {
    URN.TEL_SCHEME: "icon-phone",
    URN.TWITTER_SCHEME: "icon-twitter",
    URN.TWITTERID_SCHEME: "icon-twitter",
    URN.TWILIO_SCHEME: "icon-twilio_original",
    URN.EMAIL_SCHEME: "icon-envelop",
    URN.FACEBOOK_SCHEME: "icon-facebook",
    URN.TELEGRAM_SCHEME: "icon-telegram",
    URN.LINE_SCHEME: "icon-line",
    URN.EXTERNAL_SCHEME: "icon-channel-external",
    URN.FCM_SCHEME: "icon-fcm",
    URN.FRESHCHAT_SCHEME: "icon-freshchat",
    URN.WHATSAPP_SCHEME: "icon-whatsapp",
}

ACTIVITY_ICONS = {
    Event.TYPE_AIRTIME_TRANSFERRED: "icon-cash",
    Event.TYPE_BROADCAST_CREATED: "icon-bullhorn",
    Event.TYPE_CALL_STARTED: "icon-phone",
    Event.TYPE_CAMPAIGN_FIRED: "icon-clock",
    Event.TYPE_CHANNEL_EVENT: "icon-power",
    Event.TYPE_CHANNEL_EVENT + ":missed_incoming": "icon-call-incoming",
    Event.TYPE_CHANNEL_EVENT + ":missed_outgoing": "icon-call-outgoing",
    Event.TYPE_CONTACT_FIELD_CHANGED: "icon-pencil",
    Event.TYPE_CONTACT_GROUPS_CHANGED: "icon-users",
    Event.TYPE_CONTACT_LANGUAGE_CHANGED: "icon-language",
    Event.TYPE_CONTACT_NAME_CHANGED: "icon-contact",
    Event.TYPE_CONTACT_URNS_CHANGED: "icon-address-book",
    Event.TYPE_EMAIL_SENT: "icon-envelop",
    Event.TYPE_ERROR: "icon-warning",
    Event.TYPE_FAILURE: "icon-warning",
    Event.TYPE_FLOW_ENTERED: "icon-flow",
    Event.TYPE_FLOW_EXITED + ":expired": "icon-clock",
    Event.TYPE_FLOW_EXITED + ":interrupted": "icon-cancel-circle",
    Event.TYPE_FLOW_EXITED + ":completed": "icon-checkmark",
    Event.TYPE_INPUT_LABELS_ADDED: "icon-tags",
    Event.TYPE_IVR_CREATED: "icon-call-outgoing",
    Event.TYPE_MSG_CREATED: "icon-bubble-right",
    Event.TYPE_MSG_CREATED + ":failed": "icon-bubble-notification",
    Event.TYPE_MSG_CREATED + ":delivered": "icon-bubble-check",
    Event.TYPE_MSG_RECEIVED: "icon-bubble-user",
    Event.TYPE_MSG_RECEIVED + ":voice": "icon-call-incoming",
    Event.TYPE_RUN_RESULT_CHANGED: "icon-bars",
    Event.TYPE_TICKET_ASSIGNED: "icon-ticket",
    Event.TYPE_TICKET_REOPENED: "icon-ticket",
    Event.TYPE_TICKET_OPENED: "icon-ticket",
    Event.TYPE_TICKET_CLOSED: "icon-ticket",
    Event.TYPE_TICKET_NOTE_ADDED: "icon-pencil",
    Event.TYPE_WEBHOOK_CALLED: "icon-cloud-upload",
}

MSG_EVENTS = {Event.TYPE_MSG_CREATED, Event.TYPE_MSG_RECEIVED, Event.TYPE_IVR_CREATED, Event.TYPE_BROADCAST_CREATED}

# events that are included in the summary view
SUMMARY_EVENTS = {
    Event.TYPE_CALL_STARTED,
    Event.TYPE_CAMPAIGN_FIRED,
    Event.TYPE_FLOW_ENTERED,
    Event.TYPE_FLOW_EXITED,
    Event.TYPE_BROADCAST_CREATED,
    Event.TYPE_IVR_CREATED,
    Event.TYPE_MSG_CREATED,
    Event.TYPE_MSG_RECEIVED,
}

MISSING_VALUE = "--"


@register.filter
def contact_field(contact, arg):
    field = ContactField.get_by_key(contact.org, arg.lower())
    if field is None:
        return MISSING_VALUE

    value = contact.get_field_display(field)
    return value or MISSING_VALUE


@register.filter
def name_or_urn(contact, org):
    return contact.get_display(org)


@register.filter
def name(contact, org):
    if contact.name:
        return contact.name
    elif org.is_anon:
        return contact.anon_identifier
    else:
        return MISSING_VALUE


@register.filter
def format_urn(urn, org):
    if org and org.is_anon:
        return ContactURN.ANON_MASK_HTML

    if isinstance(urn, ContactURN):
        return urn.get_display(org=org, international=True)
    else:
        return URN.format(urn, international=True)


@register.filter
def urn(contact, org):
    contact_urn = contact.get_urn()
    if contact_urn:
        return format_urn(contact_urn, org)
    else:
        return MISSING_VALUE


@register.filter
def format_contact(contact, org):  # pragma: needs cover
    return contact.get_display(org=org)


@register.filter
def urn_icon(urn):
    return URN_SCHEME_ICONS.get(urn.scheme, "")


@register.filter
def history_icon(event: dict) -> str:
    event_type = event["type"]
    variant = None

    if event_type == Event.TYPE_MSG_CREATED:
        if event["status"] in (Msg.STATUS_ERRORED, Msg.STATUS_FAILED):
            variant = "failed"
        elif event["status"] == Msg.STATUS_DELIVERED:
            variant = "delivered"

    elif event_type == Event.TYPE_MSG_RECEIVED:
        if event["msg_type"] == Msg.TYPE_IVR:
            variant = "voice"

    elif event_type == Event.TYPE_FLOW_EXITED:
        if event["status"] == FlowRun.STATUS_INTERRUPTED:
            variant = "interrupted"
        elif event["status"] == FlowRun.STATUS_EXPIRED:
            variant = "expired"
        else:
            variant = "completed"

    elif event_type == Event.TYPE_CHANNEL_EVENT:
        if event["channel_event_type"] == ChannelEvent.TYPE_CALL_IN_MISSED:
            variant = "missed_incoming"
        elif event["channel_event_type"] == ChannelEvent.TYPE_CALL_OUT_MISSED:
            variant = "missed_outgoing"

    if variant:
        glyph_name = ACTIVITY_ICONS.get(event_type + ":" + variant)
    else:
        glyph_name = ACTIVITY_ICONS.get(event_type)

    return mark_safe(f'<span class="glyph {glyph_name}"></span>')


@register.filter
def history_class(event: dict) -> str:
    event_type = event["type"]
    classes = []

    if event_type in MSG_EVENTS:
        classes.append("msg")

        if event.get("status") in (Msg.STATUS_ERRORED, Msg.STATUS_FAILED):
            classes.append("warning")
    else:
        classes.append("non-msg")

        if event_type == Event.TYPE_ERROR or event_type == "failure":
            classes.append("warning")
        elif event_type == Event.TYPE_WEBHOOK_CALLED and event["status"] != "success":
            classes.append("warning")
        elif event_type == Event.TYPE_CALL_STARTED and event["status"] == IVRCall.STATUS_FAILED:
            classes.append("warning")
        elif event_type == Event.TYPE_CAMPAIGN_FIRED and event["fired_result"] == EventFire.RESULT_SKIPPED:
            classes.append("skipped")

    if event_type not in SUMMARY_EVENTS:
        classes.append("detail-event")

    return " ".join(classes)


@register.filter
def inactive_count(objs) -> int:
    """
    Returns the number of items in a queryset or list where is_active=False
    """
    return len([o for o in list(objs) if not o.is_active])
