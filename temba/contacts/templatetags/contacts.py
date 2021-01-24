from django import template
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _

from temba.campaigns.models import EventFire
from temba.contacts.models import URN, ContactField, ContactURN
from temba.ivr.models import IVRCall
from temba.mailroom.events import Event
from temba.msgs.models import ERRORED, FAILED

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
    "call_started": "icon-phone",
    "campaign_fired": "icon-clock",
    "channel_event": "icon-power",
    "channel_event:missed_incoming": "icon-call-incoming",
    "channel_event:missed_outgoing": "icon-call-outgoing",
    Event.TYPE_CONTACT_FIELD_CHANGED: "icon-pencil",
    Event.TYPE_CONTACT_GROUPS_CHANGED: "icon-users",
    Event.TYPE_CONTACT_LANGUAGE_CHANGED: "icon-language",
    Event.TYPE_CONTACT_NAME_CHANGED: "icon-contact",
    Event.TYPE_CONTACT_URNS_CHANGED: "icon-address-book",
    Event.TYPE_EMAIL_SENT: "icon-envelop",
    Event.TYPE_ERROR: "icon-warning",
    Event.TYPE_FAILURE: "icon-warning",
    Event.TYPE_FLOW_ENTERED: "icon-flow",
    "flow_exited:expired": "icon-clock",
    "flow_exited:interrupted": "icon-cancel-circle",
    "flow_exited:completed": "icon-checkmark",
    Event.TYPE_INPUT_LABELS_ADDED: "icon-tags",
    Event.TYPE_IVR_CREATED: "icon-call-outgoing",
    Event.TYPE_MSG_CREATED: "icon-bubble-right",
    Event.TYPE_MSG_CREATED + ":failed": "icon-bubble-notification",
    Event.TYPE_MSG_CREATED + ":delivered": "icon-bubble-check",
    Event.TYPE_MSG_RECEIVED: "icon-bubble-user",
    Event.TYPE_MSG_RECEIVED + ":voice": "icon-call-incoming",
    Event.TYPE_RUN_RESULT_CHANGED: "icon-bars",
    Event.TYPE_TICKET_OPENED: "icon-ticket",
    Event.TYPE_WEBHOOK_CALLED: "icon-cloud-upload",
}

MSG_EVENTS = {Event.TYPE_MSG_CREATED, Event.TYPE_MSG_RECEIVED, Event.TYPE_IVR_CREATED, Event.TYPE_BROADCAST_CREATED}

MISSING_VALUE = "--"


@register.filter
def contact_field(contact, arg):
    field = ContactField.get_by_key(contact.org, arg.lower())
    if field is None:
        return MISSING_VALUE

    value = contact.get_field_display(field)
    return value or MISSING_VALUE


@register.filter
def short_name(contact, org):
    return contact.get_display(org, short=True)


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
def history_icon(item):
    event_type = item["type"]
    obj = item.get("obj")
    variant = None

    if event_type == Event.TYPE_MSG_CREATED:
        if item["status"] in ("F", "E"):
            variant = "failed"
        elif item["status"] == "D":
            variant = "delivered"

    elif event_type == Event.TYPE_MSG_RECEIVED:
        if item["msg_type"] == "V":
            variant = "voice"

    elif event_type == "flow_exited":
        if obj.exit_type == "C":
            variant = "completed"
        elif obj.exit_type == "I":
            variant = "interrupted"
        else:
            variant = "expired"

    elif event_type == "channel_event":
        if obj.event_type == "mo_miss":
            variant = "missed_incoming"
        elif obj.event_type == "mt_miss":
            variant = "missed_outgoing"

    if variant:
        glyph_name = ACTIVITY_ICONS[event_type + ":" + variant]
    else:
        glyph_name = ACTIVITY_ICONS[event_type]

    return mark_safe(f'<span class="glyph {glyph_name}"></span>')


@register.filter
def history_class(item):
    obj = item.get("obj")
    classes = []

    if item["type"] in MSG_EVENTS:
        classes.append("msg")

        if item.get("status") in (ERRORED, FAILED):
            classes.append("warning")
    else:
        classes.append("non-msg")

        if item["type"] == "error" or item["type"] == "failure":
            classes.append("warning")
        elif item["type"] == "webhook_called" and not obj.is_success:
            classes.append("warning")
        elif item["type"] == "call_started" and obj.status == IVRCall.FAILED:
            classes.append("warning")
        elif item["type"] == "campaign_fired" and obj.fired_result == EventFire.RESULT_SKIPPED:
            classes.append("skipped")

    if item["type"] not in (
        "call_started",
        "campaign_fired",
        "flow_entered",
        "flow_exited",
        Event.TYPE_BROADCAST_CREATED,
        Event.TYPE_IVR_CREATED,
        Event.TYPE_MSG_CREATED,
        Event.TYPE_MSG_RECEIVED,
    ):
        classes.append("detail-event")

    return " ".join(classes)


@register.filter
def campaign_event_time(event):

    unit = event.unit
    if abs(event.offset) == 1:
        if event.unit == "D":
            unit = _("day")
        elif event.unit == "M":
            unit = _("minute")
        elif event.unit == "H":
            unit = _("hour")
    else:
        if event.unit == "D":
            unit = _("days")
        elif event.unit == "M":
            unit = _("minutes")
        elif event.unit == "H":
            unit = _("hours")

    direction = "after"
    if event.offset < 0:
        direction = "before"

    return "%d %s %s %s" % (abs(event.offset), unit, direction, event.relative_to.label)
