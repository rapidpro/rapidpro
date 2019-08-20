from django import template

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
    return log.get_request_display(context["user"], ContactURN.ANON_MASK)


@register.simple_tag(takes_context=True)
def channellog_response(context, log, *args, **kwargs):
    if not log.response:
        return log.description

    return log.get_response_display(context["user"], ContactURN.ANON_MASK)
