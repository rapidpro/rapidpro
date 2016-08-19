from django import template
from django.template import TemplateSyntaxError
from django.template.defaultfilters import register
from django.conf import settings
from django.core.urlresolvers import reverse
from ...campaigns.models import Campaign
from ...flows.models import Flow
from ...triggers.models import Trigger


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
    minutes = seconds / 60
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
    if getattr(settings, 'SESSION_COOKIE_SECURE', False):
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
    return path


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
        style_output = '<style type="text/less" media="all">@import "variables.less";%s</style>' % output
        return style_output

# register our tag
lessblock = register.tag(lessblock)
