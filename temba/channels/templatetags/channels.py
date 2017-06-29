from __future__ import unicode_literals

from django import template

register = template.Library()


@register.filter
def channel_icon(channel):
    return channel.get_type().icon
