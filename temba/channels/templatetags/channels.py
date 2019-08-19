from django import template

register = template.Library()


@register.filter
def channel_icon(channel):
    return channel.get_type().icon


@register.simple_tag(takes_context=True)
def channellog_url(context, log, *args, **kwargs):
    return log.get_url(context["user"])


@register.simple_tag(takes_context=True)
def channellog_request(context, log, *args, **kwargs):
    return log.get_request(context["user"])


@register.simple_tag(takes_context=True)
def channellog_response(context, log, *args, **kwargs):
    if not log.response:
        return log.description

    return log.get_response(context["user"])
