from django import template

register = template.Library()


@register.filter
def channel_icon(channel):
    return channel.get_type().icon


@register.simple_tag(takes_context=True)
def anonymize_channellog_url(context, log, *args, **kwargs):
    channel_type = log.channel.get_type_from_code(log.channel.channel_type)
    user = context["user"]

    if log.channel.org.is_anon and not user.has_org_perm(user.get_org(), "contacts.contact_break_anon"):
        return channel_type.anonymize_channellog_url(log)

    return log.url


@register.simple_tag(takes_context=True)
def anonymize_channellog_request(context, log, *args, **kwargs):
    channel_type = log.channel.get_type_from_code(log.channel.channel_type)
    user = context["user"]

    if log.channel.org.is_anon and not user.has_org_perm(user.get_org(), "contacts.contact_break_anon"):
        return channel_type.anonymize_channellog_request(log)

    if not log.request:
        return f"{log.method} {log.url}"
    return log.request


@register.simple_tag(takes_context=True)
def anonymize_channellog_response(context, log, *args, **kwargs):
    channel_type = log.channel.get_type_from_code(log.channel.channel_type)
    user = context["user"]

    if log.channel.org.is_anon and not user.has_org_perm(user.get_org(), "contacts.contact_break_anon"):
        return channel_type.anonymize_channellog_response(log)

    if not log.response:
        return log.description
    else:
        return log.response
