import json
from datetime import timedelta

import iso8601
import pytz

from django.template.defaultfilters import register
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escapejs
from django.utils.safestring import mark_safe
from django.utils.translation import gettext, gettext_lazy as _, ngettext_lazy

from temba.campaigns.models import Campaign, CampaignEvent
from temba.contacts.models import ContactGroup
from temba.flows.models import Flow
from temba.triggers.models import Trigger
from temba.utils import analytics
from temba.utils.dates import datetime_to_str
from temba.utils.text import unsnakify

TIME_SINCE_CHUNKS = (
    (60 * 60 * 24 * 365, ngettext_lazy("%d year", "%d years")),
    (60 * 60 * 24 * 30, ngettext_lazy("%d month", "%d months")),
    (60 * 60 * 24 * 7, ngettext_lazy("%d week", "%d weeks")),
    (60 * 60 * 24, ngettext_lazy("%d day", "%d days")),
    (60 * 60, ngettext_lazy("%d hour", "%d hours")),
    (60, ngettext_lazy("%d minute", "%d minutes")),
    (1, ngettext_lazy("%d second", "%d seconds")),
)


OBJECT_URLS = {
    Flow: lambda o: reverse("flows.flow_editor", args=[o.uuid]),
    Campaign: lambda o: reverse("campaigns.campaign_read", args=[o.uuid]),
    CampaignEvent: lambda o: reverse("campaigns.campaign_read", args=[o.uuid]),
    ContactGroup: lambda o: reverse("contacts.contact_filter", args=[o.uuid]),
    Trigger: lambda o: reverse("triggers.trigger_type", args=[o.type.slug]),
}


@register.filter
def object_class_name(obj):
    return obj.__class__.__name__


@register.filter
def oxford(forloop, punctuation=""):
    """
    Filter that looks at the current step in a forloop and adds commas or and
    """
    # there are only two items
    if forloop["counter"] == 1 and forloop["revcounter"] == 2:
        return f' {_("and")} '

    # we are the last in a list of 3 or more
    if forloop["revcounter"] == 2:
        return f', {_("and")} '

    if not forloop["last"]:
        return ", "
    return punctuation


@register.filter
def icon(o):
    if isinstance(o, Campaign):
        return "icon-campaign"

    if isinstance(o, Trigger):
        return "icon-feed"

    if isinstance(o, Flow):
        return "icon-flow"

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


@register.filter("delta", is_safe=False)
def delta_filter(delta):
    """Humanizes a timedelta object on template (i.e. "2 months, 2 weeks")."""
    if not delta:
        return ""
    try:
        # ignore microseconds
        since = delta.days * 24 * 60 * 60 + delta.seconds
        if since <= 0:
            # d is in the future compared to now, stop processing.
            return gettext("0 seconds")
        for i, (seconds, name) in enumerate(TIME_SINCE_CHUNKS):
            count = since // seconds
            if count != 0:
                break
        result = name % count
        if i + 1 < len(TIME_SINCE_CHUNKS):
            # Now get the second item
            seconds2, name2 = TIME_SINCE_CHUNKS[i + 1]
            count2 = (since - (seconds * count)) // seconds2
            if count2 != 0:
                result += ", " + name2 % count2
        return result

    except Exception:
        return ""


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
    if type(value) != str:
        raise ValueError(f"Expected str got {type(value)} for to_json")

    escaped_output = escapejs(value)

    return mark_safe(f'JSON.parse("{escaped_output}")')


@register.filter
def duration(date):
    return mark_safe(f"<temba-date value='{date.isoformat()}' display='duration'></temba-date>")


@register.filter
def datetime(date):
    return mark_safe(f"<temba-date value='{date.isoformat()}' display='datetime'></temba-date>")


@register.filter
def day(date):
    return mark_safe(f"<temba-date value='{date.isoformat()}' display='date'></temba-date>")


@register.simple_tag(takes_context=True)
def short_datetime(context, dtime):
    if dtime.tzinfo is None:
        dtime = dtime.replace(tzinfo=pytz.utc)

    org_format = "D"
    tz = pytz.UTC
    org = context["user_org"]
    if org:
        org_format = org.date_format
        tz = org.timezone

    dtime = dtime.astimezone(tz)

    now = timezone.now()
    twelve_hours_ago = now - timedelta(hours=12)

    if org_format == "D":
        if dtime > twelve_hours_ago:
            return f"{dtime.strftime('%H')}:{dtime.strftime('%M')}"
        elif now.year == dtime.year:
            return f"{int(dtime.strftime('%d'))} {dtime.strftime('%b')}"
        else:
            return f"{int(dtime.strftime('%d'))}/{int(dtime.strftime('%m'))}/{dtime.strftime('%y')}"
    elif org_format == "Y":
        if dtime > twelve_hours_ago:
            return f"{dtime.strftime('%H')}:{dtime.strftime('%M')}"
        elif now.year == dtime.year:
            return f"{dtime.strftime('%b')} {int(dtime.strftime('%d'))}"
        else:
            return f"{dtime.strftime('%Y')}/{int(dtime.strftime('%m'))}/{int(dtime.strftime('%d'))}"

    else:
        if dtime > twelve_hours_ago:
            return f"{int(dtime.strftime('%I'))}:{dtime.strftime('%M')} {dtime.strftime('%p').lower()}"
        elif now.year == dtime.year:
            return f"{dtime.strftime('%b')} {int(dtime.strftime('%d'))}"
        else:
            return f"{int(dtime.strftime('%m'))}/{int(dtime.strftime('%d'))}/{dtime.strftime('%y')}"


@register.simple_tag(takes_context=True)
def format_datetime(context, dt, seconds: bool = False):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=pytz.utc)

    tz = pytz.UTC
    org = context.get("user_org")
    if org:
        tz = org.timezone
    dt = dt.astimezone(tz)

    if org:
        return org.format_datetime(dt, seconds=seconds)

    fmt = "%d-%m-%Y %H:%M:%S" if seconds else "%d-%m-%Y %H:%M"
    return datetime_to_str(dt, fmt, tz)


@register.filter
def parse_isodate(value):
    return iso8601.parse_date(value)


@register.filter
def first_word(value):
    return str(value).split(" ", maxsplit=1)[0]


@register.simple_tag(takes_context=True)
def analytics_hook(context, name: str):
    return mark_safe(analytics.get_hook_html(name, context))
