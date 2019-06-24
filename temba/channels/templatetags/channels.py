from django import template

register = template.Library()


@register.filter
def channel_icon(channel):
    return channel.get_type().icon


@register.simple_tag(takes_context=True)
def anonymize_channellog_url(context, log, *args, **kwargs):
    channel_type = log.channel.get_type_from_code(log.channel.channel_type)

    user = context["user"]

    if user.has_org_perm(user.get_org(), "contacts.contact_break_anon"):
        return channel_type.format_channellog_url(log)
    elif log.channel.org.is_anon:
        return channel_type.anonymize_channellog_url(log)
    else:
        return channel_type.format_channellog_url(log)


@register.simple_tag(takes_context=True)
def anonymize_channellog_request(context, log, *args, **kwargs):
    channel_type = log.channel.get_type_from_code(log.channel.channel_type)

    user = context["user"]

    if user.has_org_perm(user.get_org(), "contacts.contact_break_anon"):
        return channel_type.format_channellog_request(log)
    elif log.channel.org.is_anon:
        return channel_type.anonymize_channellog_request(log)
    else:
        return channel_type.format_channellog_request(log)


@register.simple_tag(takes_context=True)
def anonymize_channellog_response(context, log, *args, **kwargs):

    channel_type = log.channel.get_type_from_code(log.channel.channel_type)

    user = context["user"]

    if user.has_org_perm(user.get_org(), "contacts.contact_break_anon"):
        return channel_type.format_channellog_response(log)
    elif log.channel.org.is_anon:
        return channel_type.anonymize_channellog_response(log)
    else:
        return channel_type.format_channellog_response(log)
