from __future__ import unicode_literals

import ttag

from datetime import timedelta
from django import template
from django.utils import timezone
from django.utils.safestring import mark_safe
from ttag.helpers import AsTag


register = template.Library()


@register.filter
def as_icon(contact_event):
    icon = 'icon-bubble-dots-2 green'
    five_minutes_ago = timezone.now() - timedelta(minutes=5)
    direction = getattr(contact_event, 'direction', 'O')
    msg_type = getattr(contact_event, 'msg_type', 'I')

    if hasattr(contact_event, 'status'):
        status = contact_event.status
    else:
        status = contact_event.call_type

    if msg_type == 'V':
        icon = 'icon-phone'
    elif direction == 'I':
        icon = 'icon-bubble-user green'
    elif status in ['P', 'Q']:
        icon = 'icon-bubble-dots-2 green'
    elif status == 'D':
        icon = 'icon-bubble-check green'
    elif status in ['W', 'S']:
        icon = 'icon-bubble-right green'
    elif status in ['E', 'F']:
        icon = 'icon-bubble-notification red'
    elif status == 'mo_call':
        icon = 'icon-call-incoming green'
    elif status == 'mo_miss':
        icon = 'icon-call-incoming red'
    elif status == 'mt_call':
        icon = 'icon-call-outgoing green'
    elif status == 'mt_miss':
        icon = 'icon-call-outgoing red'
    return mark_safe('<span class="glyph %s"></span>' % icon)


class Render(AsTag):
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

    By default, the tag strips the output of leading and trailing white space.
    To avoid this, use the ``no_strip`` argument::

        {% render no_strip as target %} ... {% endrender %}
    """
    no_strip = ttag.BooleanArg()

    class Meta:
        block = True

    def as_value(self, data, context):
        output = self.nodelist.render(context)
        if 'no_strip' not in data:
            output = output.strip()
        return mark_safe(output)


register.tag(Render)
