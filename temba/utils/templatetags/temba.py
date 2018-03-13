# -*- coding: utf-8 -*-
from django import template
from django.template import TemplateSyntaxError
from django.template.defaultfilters import register
from django.utils.translation import ugettext_lazy as _
from django.conf import settings
from django.core.urlresolvers import reverse
from django.utils.translation import ugettext, ungettext_lazy
from ...campaigns.models import Campaign
from ...flows.models import Flow
from ...triggers.models import Trigger

TIME_SINCE_CHUNKS = (
    (60 * 60 * 24 * 365, ungettext_lazy('%d year', '%d years')),
    (60 * 60 * 24 * 30, ungettext_lazy('%d month', '%d months')),
    (60 * 60 * 24 * 7, ungettext_lazy('%d week', '%d weeks')),
    (60 * 60 * 24, ungettext_lazy('%d day', '%d days')),
    (60 * 60, ungettext_lazy('%d hour', '%d hours')),
    (60, ungettext_lazy('%d minute', '%d minutes')),
    (1, ungettext_lazy('%d second', '%d seconds'))
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

    if not forloop['last']:
        return ", "
    return punctuation


@register.filter
def icon(o):

    if isinstance(o, Campaign):
        return "icon-instant"

    if isinstance(o, Trigger):
        return "icon-feed"

    if isinstance(o, Flow):
        return "icon-tree"

    return ""


@register.filter
def verbose_name_plural(object):
    return object._meta.verbose_name_plural


@register.filter
def format_seconds(seconds):
    if not seconds:
        return None

    if seconds < 60:
        return '%s sec' % seconds
    minutes = seconds // 60
    seconds %= 60
    if seconds >= 30:
        minutes += 1
    return '%s min' % minutes


@register.simple_tag(takes_context=True)
def ssl_brand_url(context, url_name, args=None):
    hostname = settings.HOSTNAME
    if 'brand' in context:
        hostname = context['brand'].get('domain', settings.HOSTNAME)

    path = reverse(url_name, args)
    if getattr(settings, 'SESSION_COOKIE_SECURE', False):  # pragma: needs cover
        return "https://%s%s" % (hostname, path)
    else:
        return path


@register.simple_tag(takes_context=True)
def non_ssl_brand_url(context, url_name, args=None):
    hostname = settings.HOSTNAME
    if 'brand' in context:
        hostname = context['brand'].get('domain', settings.HOSTNAME)

    path = reverse(url_name, args)
    if settings.HOSTNAME != "localhost":
        return "http://%s%s" % (hostname, path)
    return path  # pragma: needs cover


@register.filter("delta", is_safe=False)
def delta_filter(delta):
    """Humanizes a timedelta object on template (i.e. "2 months, 2 weeks")."""
    if not delta:
        return ''
    try:
        # ignore microseconds
        since = delta.days * 24 * 60 * 60 + delta.seconds
        if since <= 0:
            # d is in the future compared to now, stop processing.
            return ugettext('0 seconds')
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
                result += ugettext(', ') + name2 % count2
        return result

    except Exception:
        return ''


def lessblock(parser, token):
    args = token.split_contents()
    if len(args) != 1:  # pragma: no cover
        raise TemplateSyntaxError("lessblock tag takes no arguments, got: [%s]" % ",".join(args))

    nodelist = parser.parse(('endlessblock',))
    parser.delete_first_token()
    return LessBlockNode(nodelist)


class LessBlockNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        output = self.nodelist.render(context)
        includes = '@import (reference) "variables.less";\n'
        includes += '@import (reference, optional) "../brands/%s/less/variables.less";\n' % context['brand']['slug']
        includes += '@import (reference) "mixins.less";\n'
        style_output = '<style type="text/less" media="all">\n%s\n%s</style>' % (includes, output)
        return style_output


# register our tag
lessblock = register.tag(lessblock)
