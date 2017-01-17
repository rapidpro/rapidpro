from __future__ import unicode_literals

from django import template
from django.utils.safestring import mark_safe
from temba.channels.models import ChannelEvent

register = template.Library()


@register.filter
def as_icon(contact_event):

    icon = 'icon-bubble-dots-2 green'
    direction = getattr(contact_event, 'direction', 'O')
    msg_type = getattr(contact_event, 'msg_type', 'I')
    media_type = getattr(contact_event, 'media', None)

    if media_type and ':' in media_type:
        media_type = media_type.split(':', 1)[0].split('/', 1)[0]

    if hasattr(contact_event, 'status'):
        status = contact_event.status
    elif isinstance(contact_event, ChannelEvent):
        status = contact_event.event_type
    else:
        status = None

    if media_type == 'image':  # pragma: needs cover
        icon = 'icon-photo_camera primary boost'
    elif msg_type == 'V':
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
