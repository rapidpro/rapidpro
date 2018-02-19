# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django import template

register = template.Library()


@register.filter
def channel_icon(channel):
    return channel.get_type().icon
