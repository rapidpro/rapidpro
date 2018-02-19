# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django import template
from django.utils.safestring import mark_safe
from temba.channels.models import ChannelEvent

register = template.Library()

PLAYABLE_CONTENT_TYPES = {
    'audio/wav',
    'audio/x-wav',
    'audio/vnd.wav',
    'audio/ogg',
    'audio/mp3',
    'audio/m4a',
    'video/mp4',
    'video/webm'
}


@register.filter
def as_icon(contact_event):
    icon = 'icon-bubble-dots-2 green'
    direction = getattr(contact_event, 'direction', 'O')
    msg_type = getattr(contact_event, 'msg_type', 'I')

    if hasattr(contact_event, 'status'):
        status = contact_event.status
    elif isinstance(contact_event, ChannelEvent):
        status = contact_event.event_type
    else:
        status = None

    if msg_type == 'V':
        icon = 'icon-phone'
    elif direction == 'I':
        icon = 'icon-bubble-user primary'
    elif status in ['P', 'Q']:
        icon = 'icon-bubble-dots-2 green'
    elif status == 'D':
        icon = 'icon-bubble-check green'
    elif status in ['W', 'S']:
        icon = 'icon-bubble-right green'
    elif status in ['E', 'F']:
        icon = 'icon-bubble-notification red'
    elif status == ChannelEvent.TYPE_CALL_IN:
        icon = 'icon-call-incoming green'
    elif status == ChannelEvent.TYPE_CALL_IN_MISSED:
        icon = 'icon-call-incoming red'
    elif status == ChannelEvent.TYPE_CALL_OUT:
        icon = 'icon-call-outgoing green'
    elif status == ChannelEvent.TYPE_CALL_OUT_MISSED:
        icon = 'icon-call-outgoing red'

    return mark_safe('<span class="glyph %s"></span>' % icon)


@register.tag(name='render')
def render(parser, token):
    """
    A block tag that renders its contents to a context variable.

    Here is an example of using it with a ``blocktrans`` tag::

        {% render as name %}
            <a href="{{ profile.get_absolute_url }}">{{ profile }}</a>
        {% endrender %}
        {% blocktrans %}Logged in as {{ name }}{% endblocktrans %}

    Here is an example of a simple base template that leverages this tag to
    avoid duplication of a page title::

        {% render as title %}
            {% block title %}The page title{% endblock %}
        {% endrender %}

        <html>
        <head><title>{{ title }}</title></head>
        <body>
            <h1>{{ title }}</h1>
            {% block body %}{% endblock %}
        </body>
    """

    class RenderNode(template.Node):
        def __init__(self, nodelist, as_var):
            self.nodelist = nodelist
            self.as_var = as_var

        def render(self, context):
            output = self.nodelist.render(context)
            context[self.as_var] = mark_safe(output.strip())
            return ''

    bits = token.split_contents()
    if len(bits) != 3 or bits[1] != 'as':
        raise ValueError("render tag should be followed by keyword as and the name of a context variable")
    as_var = bits[2]

    nodes = parser.parse(('endrender',))
    parser.delete_first_token()
    return RenderNode(nodes, as_var)


@register.inclusion_tag('msgs/tags/attachment.haml')
def attachment_button(attachment):
    content_type, delim, url = attachment.partition(":")

    # some OGG/OGA attachments may have wrong content type
    if content_type == 'application/octet-stream' and (url.endswith('.ogg') or url.endswith('.oga')):  # pragma: no cover
        content_type = 'audio/ogg'

    category = content_type.split('/')[0] if '/' in content_type else content_type

    if category == 'geo':
        preview = url

        (lat, lng) = url.split(',')
        url = 'http://www.openstreetmap.org/?mlat=%(lat)s&mlon=%(lng)s#map=18/%(lat)s/%(lng)s' % {"lat": lat, "lng": lng}
    else:
        preview = url.rpartition('.')[2].upper()  # preview is the file extension in uppercase

    return {
        'content_type': content_type,
        'category': category,
        'preview': preview,
        'url': url,
        'is_playable': content_type in PLAYABLE_CONTENT_TYPES
    }


@register.inclusion_tag('msgs/tags/channel_log_link.haml')
def channel_log_link(msg_or_call):
    return {'log': msg_or_call.get_last_log()}
