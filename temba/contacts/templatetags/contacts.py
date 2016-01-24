from __future__ import unicode_literals

from datetime import datetime

import pytz
from django import template
from django.utils import timezone
from django.utils.safestring import mark_safe
from temba.contacts.models import Contact, ContactURN, FACEBOOK_SCHEME, TEL_SCHEME, TWITTER_SCHEME, TWILIO_SCHEME, URN_ANON_MASK
from django.utils.translation import ugettext_lazy as _

register = template.Library()

URN_SCHEME_ICONS = {TEL_SCHEME: 'icon-mobile-2',
                    TWITTER_SCHEME: 'icon-twitter',
                    TWILIO_SCHEME: 'icon-twilio_original',
                    FACEBOOK_SCHEME: 'icon-facebook'}

ACTIVITY_ICONS = {
    'EventFire': 'icon-clock',
    'FlowRun': 'icon-tree-2',
    'Broadcast': 'icon-bullhorn',
    'Incoming': 'icon-bubble-user',
    'Outgoing': 'icon-bubble-right',
    'Failed': 'icon-bubble-notification',
    'Delivered': 'icon-bubble-check',
    'Call': 'icon-phone',
    'IVRCall': 'icon-phone',
    'DTMF': 'icon-phone'
}



@register.filter
def contact_field(contact, arg):
    value = contact.get_field_display(arg)
    if value:
        return value
    else:  # pragma: no cover
        return None

@register.filter
def tel(contact, org):
    return contact.get_urn_display(org=org, scheme=TEL_SCHEME)

@register.filter
def short_name(contact, org):
    return contact.get_display(org, short=True)

@register.filter
def name_or_urn(contact, org):
    return contact.get_display(org)

@register.filter
def format_urn(urn_or_contact, org):
    if isinstance(urn_or_contact, ContactURN):
        urn_val = urn_or_contact.get_display(org=org)
        return urn_val if urn_val != URN_ANON_MASK else '\u2022' * 8  # replace *'s with prettier HTML entity
    elif isinstance(urn_or_contact, Contact):
        # will render contact's highest priority URN
        return urn_or_contact.get_urn_display(org=org)
    else:  # pragma: no cover
        raise ValueError('Must be a URN or contact')

@register.filter
def urn_icon(urn):
    return URN_SCHEME_ICONS.get(urn.scheme, '')


@register.filter
def activity_icon(item):
    name = type(item).__name__
    if name == 'Msg':
        if item.broadcast and item.broadcast.recipient_count > 1:
            name = 'Broadcast'
        elif item.msg_type == 'V':
            if item.direction == 'I':
                name = 'DTMF'
            else:
                name = 'IVRCall'
        elif item.direction == 'I':
            name = 'Incoming'
        else:
            name = 'Outgoing'
            if hasattr(item, 'status'):
                if item.status in ('F', 'E'):
                    name = 'Failed'
                elif item.status == 'D':
                    name = 'Delivered'

    return mark_safe('<span class="glyph %s"></span>' % (ACTIVITY_ICONS.get(name, '')))

@register.filter
def event_time(event):

    unit = event.unit
    if abs(event.offset) == 1:
        if event.unit == 'D':
            unit = _('day')
        elif event.unit == 'M':
            unit = _('minute')
        elif event.unit == 'H':
            unit = _('hour')
    else:
        if event.unit == 'D':
            unit = _('days')
        elif event.unit == 'M':
            unit = _('minutes')
        elif event.unit == 'H':
            unit = _('hours')

    direction = 'after'
    if event.offset < 0:
        direction = 'before'

    return "%d %s %s %s" % (abs(event.offset), unit, direction, event.relative_to.label)

