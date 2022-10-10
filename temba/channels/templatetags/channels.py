from datetime import timedelta

from django import template
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from temba.ivr.models import Call
from temba.msgs.models import Msg

register = template.Library()


@register.filter
def channel_icon(channel):
    return channel.type.icon


@register.inclusion_tag("channels/tags/channel_log_link.haml", takes_context=True)
def channel_log_link(context, obj):
    assert isinstance(obj, (Msg, Call)), "tag only supports Msg or Call instances"

    user = context["user"]
    org = context["user_org"]
    logs_url = None

    if user.has_org_perm(org, "channels.channellog_read"):
        has_channel = obj.channel and obj.channel.is_active

        obj_age = timezone.now() - obj.created_on
        has_logs = obj_age < (settings.RETENTION_PERIODS["channellog"] - timedelta(hours=4))

        if has_channel and has_logs:
            if isinstance(obj, Call):
                logs_url = reverse("channels.channellog_call", args=[obj.channel.uuid, obj.id])
            if isinstance(obj, Msg):
                logs_url = reverse("channels.channellog_msg", args=[obj.channel.uuid, obj.id])

    return {"logs_url": logs_url}
