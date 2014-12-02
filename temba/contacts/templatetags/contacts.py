from __future__ import unicode_literals

from django import template
from temba.contacts.models import Contact, ContactURN, FACEBOOK_SCHEME, TEL_SCHEME, TWITTER_SCHEME, TWILIO_SCHEME, URN_ANON_MASK

register = template.Library()

URN_SCHEME_ICONS = {TEL_SCHEME: 'icon-mobile-2',
                    TWITTER_SCHEME: 'icon-twitter',
                    TWILIO_SCHEME: 'icon-twilio_original',
                    FACEBOOK_SCHEME: 'icon-facebook'}

@register.filter
def contact_field(contact, arg):
    value = contact.get_field_display(arg)
    if value:
        return value
    else:
        return '--'

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
    else:
        raise ValueError('Must be a URN or contact')

@register.filter
def urn_icon(urn):
    return URN_SCHEME_ICONS.get(urn.scheme, '')
