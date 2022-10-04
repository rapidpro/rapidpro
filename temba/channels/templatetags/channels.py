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


@register.inclusion_tag("channels/tags/channel_log_link.haml")
def channel_log_link(obj):
    obj_age = timezone.now() - obj.created_on
    has_logs = obj_age < (settings.RETENTION_PERIODS["channellog"] - timedelta(hours=4))

    if has_logs and isinstance(obj, Call):
        logs_url = reverse("channels.channellog_call", args=[obj.channel.uuid, obj.id])
    elif has_logs and isinstance(obj, Msg):
        logs_url = reverse("channels.channellog_msg", args=[obj.channel.uuid, obj.id])
    else:
        logs_url = None

    return {"logs_url": logs_url}
