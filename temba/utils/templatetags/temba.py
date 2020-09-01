import json
from datetime import timedelta

import pytz

from django import template
from django.conf import settings
from django.template import TemplateSyntaxError
from django.template.defaultfilters import register
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escapejs
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext, ugettext_lazy as _, ungettext_lazy

from temba.utils.dates import datetime_to_str

from ...campaigns.models import Campaign
from ...flows.models import Flow
from ...triggers.models import Trigger

TIME_SINCE_CHUNKS = (
    (60 * 60 * 24 * 365, ungettext_lazy("%d year", "%d years")),
    (60 * 60 * 24 * 30, ungettext_lazy("%d month", "%d months")),
    (60 * 60 * 24 * 7, ungettext_lazy("%d week", "%d weeks")),
    (60 * 60 * 24, ungettext_lazy("%d day", "%d days")),
    (60 * 60, ungettext_lazy("%d hour", "%d hours")),
    (60, ungettext_lazy("%d minute", "%d minutes")),
    (1, ungettext_lazy("%d second", "%d seconds")),
)


@register.filter
def oxford(forloop, punctuation=""):
    """
    Filter that looks at the current step in a forloop and adds commas or and
    """
    # there are only two items
    if forloop["counter"] == 1 and forloop["revcounter"] == 2:
        return _(" and ")

    # we are the last in a list of 3 or more
    if forloop["revcounter"] == 2:
        return _(", and ")

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
def verbose_name_plural(object):
    return object._meta.verbose_name_plural


@register.filter
def format_seconds(seconds):
    if not seconds:
        return None

    if seconds < 60:
        return "%s sec" % seconds
    minutes = seconds // 60
    seconds %= 60
    if seconds >= 30:
        minutes += 1
    return "%s min" % minutes


@register.simple_tag()
def annotated_field(field, label, help_text):
    attrs = field.field.widget.attrs
    attrs["label"] = label
    attrs["help_text"] = help_text
    attrs["errors"] = json.dumps([str(error) for error in field.errors])
    return field.as_widget(attrs=attrs)


@register.simple_tag(takes_context=True)
def ssl_brand_url(context, url_name, args=None):
    hostname = settings.HOSTNAME
    if "brand" in context:
        hostname = context["brand"].get("domain", settings.HOSTNAME)

    path = reverse(url_name, args)
    if getattr(settings, "SESSION_COOKIE_SECURE", False):  # pragma: needs cover
        return "https://%s%s" % (hostname, path)
    else:
        return path


@register.simple_tag(takes_context=True)
def non_ssl_brand_url(context, url_name, args=None):
    hostname = settings.HOSTNAME
    if "brand" in context:
        hostname = context["brand"].get("domain", settings.HOSTNAME)

    path = reverse(url_name, args)
    if settings.HOSTNAME != "localhost":  # pragma: needs cover
        return "http://%s%s" % (hostname, path)
    return path


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
            return ugettext("0 seconds")
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
                result += ugettext(", ") + name2 % count2
        return result

    except Exception:
        return ""


def lessblock(parser, token):
    args = token.split_contents()
    if len(args) != 1:  # pragma: no cover
        raise TemplateSyntaxError("lessblock tag takes no arguments, got: [%s]" % ",".join(args))

    nodelist = parser.parse(("endlessblock",))
    parser.delete_first_token()
    return LessBlockNode(nodelist)


class LessBlockNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        output = self.nodelist.render(context)
        includes = '@import (reference) "variables.less";\n'
        includes += '@import (reference, optional) "../brands/%s/less/variables.less";\n' % context["brand"]["slug"]
        includes += '@import (reference) "mixins.less";\n'
        style_output = '<style type="text/less" media="all">\n%s\n%s</style>' % (includes, output)
        return style_output


# register our tag
lessblock = register.tag(lessblock)


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
            return "%s:%s" % (dtime.strftime("%H"), dtime.strftime("%M"))
        elif now.year == dtime.year:
            return "%d %s" % (int(dtime.strftime("%d")), dtime.strftime("%b"))
        else:
            return "%d/%d/%s" % (int(dtime.strftime("%d")), int(dtime.strftime("%m")), dtime.strftime("%y"))
    else:
        if dtime > twelve_hours_ago:
            return "%d:%s %s" % (int(dtime.strftime("%I")), dtime.strftime("%M"), dtime.strftime("%p").lower())
        elif now.year == dtime.year:
            return "%s %d" % (dtime.strftime("%b"), int(dtime.strftime("%d")))
        else:
            return "%d/%d/%s" % (int(dtime.strftime("%m")), int(dtime.strftime("%d")), dtime.strftime("%y"))


@register.simple_tag(takes_context=True)
def format_datetime(context, dtime):
    if dtime.tzinfo is None:
        dtime = dtime.replace(tzinfo=pytz.utc)

    tz = pytz.UTC
    org = context.get("user_org")
    if org:
        tz = org.timezone
    dtime = dtime.astimezone(tz)
    if org:
        return org.format_datetime(dtime)
    return datetime_to_str(dtime, "%d-%m-%Y %H:%M", tz)
