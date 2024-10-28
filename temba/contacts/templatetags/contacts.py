from django import template
from django.utils.safestring import mark_safe

from temba.contacts.models import URN, ContactField, ContactURN
from temba.msgs.models import Msg

register = template.Library()

URN_SCHEME_ICONS = {
    URN.TEL_SCHEME: "icon-phone",
    URN.TWITTER_SCHEME: "icon-twitter",
    URN.TWITTERID_SCHEME: "icon-twitter",
    URN.EMAIL_SCHEME: "icon-envelop",
    URN.FACEBOOK_SCHEME: "icon-facebook",
    URN.TELEGRAM_SCHEME: "icon-telegram",
    URN.LINE_SCHEME: "icon-line",
    URN.EXTERNAL_SCHEME: "icon-channel-external",
    URN.FCM_SCHEME: "icon-fcm",
    URN.FRESHCHAT_SCHEME: "icon-freshchat",
    URN.WHATSAPP_SCHEME: "icon-whatsapp",
}

MISSING_VALUE = "--"


@register.simple_tag()
def contact_field(contact, field):
    value = contact.get_field_display(field)

    if value and field.value_type == ContactField.TYPE_DATETIME:
        value = contact.get_field_value(field)
        if value:
            display = "timedate" if field.is_proxy else "date"
            return mark_safe(f"<temba-date value='{value.isoformat()}' display='{display}'></temba-date>")

    return value or MISSING_VALUE


@register.filter
def name_or_urn(contact, org):
    return contact.get_display(org)


@register.filter
def urn_or_anon(contact, org):
    """
    Renders the contact has their primary URN or anon id if org is anon
    """
    if not org.is_anon:
        contact_urn = contact.get_urn()
        if contact_urn:
            return format_urn(contact_urn, org)
        else:
            return MISSING_VALUE
    else:
        return contact.anon_display


@register.filter
def format_urn(urn, org):
    if org and org.is_anon:
        return ContactURN.ANON_MASK_HTML

    return urn.get_display(org=org, international=True)


@register.filter
def urn_icon(urn):
    return URN_SCHEME_ICONS.get(urn.scheme, "")


@register.filter
def msg_status_badge(msg) -> str:
    display = {}

    if msg.status == Msg.STATUS_DELIVERED:
        display = {"background": "#efffe0", "icon": "check", "icon_color": "rgb(var(--success-rgb))"}

    if msg.direction == Msg.DIRECTION_IN or msg.status == Msg.STATUS_WIRED:
        display = {"background": "#f9f9f9", "icon": "check", "icon_color": "var(--color-primary-dark)"}

    if msg.status == Msg.STATUS_ERRORED or msg.status == Msg.STATUS_FAILED:
        display = {"background": "#fff4f4", "icon": "x", "icon_color": "var(--color-error)"}

        # we are still working on errored messages, slightly different icon
        if msg.status == Msg.STATUS_ERRORED:
            display["icon"] = "retry"

    if len(display) >= 3:
        return mark_safe(
            """
            <div class="flex items-center flex-row p-1 rounded-lg" style="background:%(background)s">
                <temba-icon name="%(icon)s" style="--icon-color:%(icon_color)s"></temba-icon>
            </div>
        """
            % display
        )
    return ""


@register.filter
def inactive_count(objs) -> int:
    """
    Returns the number of items in a queryset or list where is_active=False
    """
    return len([o for o in list(objs) if not o.is_active])
