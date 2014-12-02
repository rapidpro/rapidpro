from __future__ import unicode_literals

from django import template
from temba.channels.views import get_channel_icon

register = template.Library()


@register.filter
def channel_icon(channel):
    return get_channel_icon(channel.channel_type)
