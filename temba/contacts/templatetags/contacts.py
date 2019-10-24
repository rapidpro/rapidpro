from django import template
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _

from temba.campaigns.models import EventFire
from temba.contacts.models import (
    EMAIL_SCHEME,
    EXTERNAL_SCHEME,
    FACEBOOK_SCHEME,
    FCM_SCHEME,
    LINE_SCHEME,
    TEL_SCHEME,
    TELEGRAM_SCHEME,
    TWILIO_SCHEME,
    TWITTER_SCHEME,
    TWITTERID_SCHEME,
    URN,
    WHATSAPP_SCHEME,
    ContactField,
    ContactURN,
)
from temba.ivr.models import IVRCall
from temba.msgs.models import ERRORED, FAILED

register = template.Library()

URN_SCHEME_ICONS = {
    TEL_SCHEME: "icon-mobile-2",
    TWITTER_SCHEME: "icon-twitter",
    TWITTERID_SCHEME: "icon-twitter",
    TWILIO_SCHEME: "icon-twilio_original",
    EMAIL_SCHEME: "icon-envelop",
    FACEBOOK_SCHEME: "icon-facebook",
    TELEGRAM_SCHEME: "icon-telegram",
    LINE_SCHEME: "icon-line",
    EXTERNAL_SCHEME: "icon-channel-external",
    FCM_SCHEME: "icon-fcm",
    WHATSAPP_SCHEME: "icon-whatsapp",
}

ACTIVITY_ICONS = {
    "airtime_transferred": "icon-cash",
    "call_started": "icon-phone",
    "campaign_fired": "icon-clock",
    "channel_event": "icon-power",
    "channel_event:missed_incoming": "icon-call-incoming",
    "channel_event:missed_outgoing": "icon-call-outgoing",
    "contact_field_changed": "icon-pencil",
    "contact_groups_changed": "icon-users",
    "contact_language_changed": "icon-language",
    "contact_name_changed": "icon-vcard",
    "contact_urns_changed": "icon-address-book",
    "email_created": "icon-envelop",
    "flow_entered": "icon-tree-2",
    "flow_exited:expired": "icon-clock",
    "flow_exited:interrupted": "icon-warning",
    "flow_exited:completed": "icon-checkmark",
    "input_labels_added": "icon-tags",
    "msg_created": "icon-bubble-right",
    "msg_created:broadcast": "icon-bullhorn",
    "msg_created:failed": "icon-bubble-notification",
    "msg_created:delivered": "icon-bubble-check",
    "msg_created:voice": "icon-call-outgoing",
    "msg_received": "icon-bubble-user",
    "msg_received:voice": "icon-call-incoming",
    "run_result_changed": "icon-bars",
    "session_started": "icon-new",
    "webhook_called": "icon-cloud-upload",
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
def format_contact(contact, org):
    return contact.get_display(org=org)


@register.filter
def urn_icon(urn):
    return URN_SCHEME_ICONS.get(urn.scheme, "")


@register.filter
def history_icon(item):
    event_type = item["type"]
    obj = item.get("obj")
    variant = None

    if event_type == "msg_created":
        if obj.broadcast and obj.broadcast.recipient_count and obj.broadcast.recipient_count > 1:
            variant = "failed" if obj.status in ("E", "F") else "broadcast"
        elif obj.msg_type == "V":
            variant = "voice"
        else:
            if obj.status in ("F", "E"):
                variant = "failed"
            elif obj.status == "D":
                variant = "delivered"

    elif event_type == "msg_received":
        if obj.msg_type == "V":
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

    if item["type"] in ("msg_created", "msg_received"):
        classes.append("msg")
        if obj.status in (ERRORED, FAILED):
            classes.append("warning")
    else:
        classes.append("non-msg")

        if item["type"] == "webhook_called" and not obj.is_success:
            classes.append("warning")
        elif item["type"] == "call_started" and obj.status == IVRCall.FAILED:
            classes.append("warning")
        elif item["type"] == "campaign_fired" and obj.fired_result == EventFire.RESULT_SKIPPED:
            classes.append("skipped")

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
