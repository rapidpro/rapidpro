from __future__ import unicode_literals

from django import template
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _
from temba.contacts.models import ContactURN, EMAIL_SCHEME, EXTERNAL_SCHEME, FACEBOOK_SCHEME
from temba.contacts.models import TELEGRAM_SCHEME, TEL_SCHEME, TWITTER_SCHEME, TWILIO_SCHEME

register = template.Library()

URN_SCHEME_ICONS = {
    TEL_SCHEME: 'icon-mobile-2',
    TWITTER_SCHEME: 'icon-twitter',
    TWILIO_SCHEME: 'icon-twilio_original',
    EMAIL_SCHEME: 'icon-envelop',
    FACEBOOK_SCHEME: 'icon-facebook',
    TELEGRAM_SCHEME: 'icon-telegram',
    EXTERNAL_SCHEME: 'icon-channel-external'
}

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
    'DTMF': 'icon-phone',
    'Expired': 'icon-clock',
    'Interrupted': 'icon-warning',
    'Completed': 'icon-checkmark'
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
def name(contact, org):
    if contact.name:
        return contact.name
    elif org.is_anon:
        return contact.anon_identifier
    else:
        return "--"


@register.filter
def format_urn(urn, org):
    urn_val = urn.get_display(org=org, international=True)
    if urn_val == ContactURN.ANON_MASK:
        return ContactURN.ANON_MASK_HTML
    return urn_val


@register.filter
def urn(contact, org):
    urn = contact.get_urn()
    if urn:
        return format_urn(urn, org)
    else:
        return ""


@register.filter
def format_contact(contact, org):
    return contact.get_display(org=org)


@register.filter
def urn_icon(urn):
    return URN_SCHEME_ICONS.get(urn.scheme, '')


@register.filter
def osm_link(geo_url):
    (media_type, delim, location) = geo_url.partition(':')
    coords = location.split(',')
    if len(coords) == 2:
        (lat, lng) = coords
        return 'http://www.openstreetmap.org/?mlat=%(lat)s&mlon=%(lng)s#map=18/%(lat)s/%(lng)s' % {"lat": lat, "lng": lng}


@register.filter
def location(geo_url):
    (media_type, delim, location) = geo_url.partition(':')
    if len(location.split(',')) == 2:
        return location


@register.filter
def media_url(media):
    if media:
        # TODO: remove after migration msgs.0053
        if media.startswith('http'):  # pragma: needs cover
            return media
        return media.partition(':')[2]


@register.filter
def media_content_type(media):
    if media:
        # TODO: remove after migration msgs.0053
        if media.startswith('http'):  # pragma: needs cover
            return 'audio/x-wav'
        return media.partition(':')[0]


@register.filter
def media_type(media):
    type = media_content_type(media)
    if type == 'application/octet-stream' and media.endswith('.oga'):  # pragma: needs cover
        return 'audio'
    if type and '/' in type:  # pragma: needs cover
        type = type.split('/')[0]
    return type


@register.filter
def is_supported_audio(content_type):  # pragma: needs cover
    return content_type in ['audio/wav', 'audio/x-wav', 'audio/vnd.wav', 'application/octet-stream']


@register.filter
def is_document(media_url):
    type = media_type(media_url)
    return type in ['application', 'text']


@register.filter
def extension(url):  # pragma: needs cover
    return url.rpartition('.')[2]


@register.filter
def activity_icon(item):
    name = type(item).__name__

    if name == 'Broadcast':
        if item.purged_status in ('E', 'F'):
            name = 'Failed'
    elif name == 'Msg':
        if item.broadcast and item.broadcast.recipient_count > 1:
            name = 'Broadcast'
            if item.status in ('E', 'F'):
                name = 'Failed'
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
    elif name == 'FlowRun':
        if hasattr(item, 'run_event_type'):
            if item.exit_type == 'C':
                name = 'Completed'
            elif item.exit_type == 'I':
                name = 'Interrupted'
            elif item.exit_type == 'E':
                name = 'Expired'

    return mark_safe('<span class="glyph %s"></span>' % (ACTIVITY_ICONS.get(name, '')))


@register.filter
def history_class(item):
    css = ''
    from temba.msgs.models import Msg
    if isinstance(item, Msg):
        if item.media and item.media[:6] == 'video:':
            css = '%s %s' % (css, 'video')
        if item.direction or item.recipient_count:
            css = '%s %s' % (css, 'msg')
    else:
        css = '%s %s' % (css, 'non-msg')

    return css


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
