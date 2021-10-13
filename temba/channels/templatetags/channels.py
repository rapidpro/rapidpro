from django import template
from django.conf import settings

from temba.contacts.models import ContactURN

register = template.Library()


@register.filter
def channel_icon(channel):
    return channel.get_type().icon


@register.simple_tag(takes_context=True)
def channellog_url(context, log, *args, **kwargs):
    return log.get_url_display(context["user"], ContactURN.ANON_MASK)


@register.simple_tag(takes_context=True)
def channellog_request(context, log, *args, **kwargs):

    request = []
    for header in log.request.split('\r\n'):
        can_add = True
        for excluded in settings.EXCLUDED_HTTP_HEADERS:
            if excluded.lower() in header.lower():
                can_add = False

        if can_add:
            request.append(header)


    log.request = '\r\n'.join(request)

    return log.get_request_display(context["user"], ContactURN.ANON_MASK)


@register.simple_tag(takes_context=True)
def channellog_response(context, log, *args, **kwargs):
    if not log.response:
        return log.description

    return log.get_response_display(context["user"], ContactURN.ANON_MASK)
