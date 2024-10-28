import json
from datetime import timezone as tzone

from django.template.defaultfilters import register
from django.urls import reverse
from django.utils.html import escapejs
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from temba.campaigns.models import Campaign, CampaignEvent
from temba.contacts.models import ContactGroup
from temba.flows.models import Flow
from temba.triggers.models import Trigger
from temba.utils import analytics
from temba.utils.dates import datetime_to_str
from temba.utils.text import unsnakify

OBJECT_URLS = {
    Flow: lambda o: reverse("flows.flow_editor", args=[o.uuid]),
    Campaign: lambda o: reverse("campaigns.campaign_read", args=[o.uuid]),
    CampaignEvent: lambda o: reverse("campaigns.campaign_read", args=[o.uuid]),
    ContactGroup: lambda o: reverse("contacts.contact_group", args=[o.uuid]),
    Trigger: lambda o: reverse("triggers.trigger_list"),
}


@register.filter
def object_class_name(obj):
    return obj.__class__.__name__


@register.filter
def oxford(forloop, conjunction=_("and")):
    """
    Filter for use in a forloop to join items using oxford commas and a conjunction.
    """
    # there are only two items
    if forloop["counter"] == 1 and forloop["revcounter"] == 2:
        return f" {conjunction} "

    # we are the last in a list of 3 or more
    if forloop["revcounter"] == 2:
        return f", {conjunction} "

    if not forloop["last"]:
        return ", "

    return ""


@register.filter
def unsnake(str):
    return unsnakify(str)


@register.filter
def object_url(o):
    assert type(o) in OBJECT_URLS

    return OBJECT_URLS[type(o)](o)


@register.filter
def verbose_name_plural(object):
    return object._meta.verbose_name_plural


@register.simple_tag()
def annotated_field(field, label, help_text):
    attrs = field.field.widget.attrs
    attrs["label"] = label
    attrs["help_text"] = help_text
    attrs["errors"] = json.dumps([str(error) for error in field.errors])
    return field.as_widget(attrs=attrs)


@register.filter
def js_bool(value):
    return "true" if value else "false"


@register.filter
def to_json(value):
    """
    To use a python variable in JS, we call json.dumps to serialize as JSON server-side and reconstruct using
    JSON.parse. The serialized string must be escaped appropriately before dumping into the client-side code.

    https://stackoverflow.com/a/14290542
    """
    if not isinstance(value, str):
        raise ValueError(f"Expected str got {type(value)} for to_json")

    escaped_output = escapejs(value)

    return mark_safe(f'JSON.parse("{escaped_output}")')


@register.filter
def day(date):
    return _date_component(date, "date")


@register.filter
def duration(date):
    return _date_component(date, "duration")


@register.filter
def datetime(date):
    return _date_component(date, "datetime")


@register.filter
def timedate(date):
    return _date_component(date, "timedate")


def _date_component(date, display: str):
    value = date if isinstance(date, str) else date.isoformat()
    return mark_safe(f'<temba-date value="{value}" display="{display}"></temba-date>')


@register.simple_tag(takes_context=True)
def format_datetime(context, dt, seconds: bool = False):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tzone.utc)

    tz = tzone.utc
    org = context.get("user_org")
    if org:
        tz = org.timezone
    dt = dt.astimezone(tz)

    if org:
        return org.format_datetime(dt, seconds=seconds)

    fmt = "%d-%m-%Y %H:%M:%S" if seconds else "%d-%m-%Y %H:%M"
    return datetime_to_str(dt, fmt, tz)


@register.simple_tag(takes_context=True)
def analytics_hook(context, name: str):
    return mark_safe(analytics.get_hook_html(name, context))
