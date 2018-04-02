# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.utils.translation import ugettext_lazy as _
from twilio import TwilioRestException

from temba.channels.types.twilio.views import ClaimView
from temba.channels.views import TWILIO_SUPPORTED_COUNTRIES_CONFIG
from temba.contacts.models import TEL_SCHEME
from temba.utils.timezones import timezone_to_country_code
from ...models import ChannelType


class TwilioType(ChannelType):
    """
    An Twilio channel
    """

    code = 'T'
    category = ChannelType.Category.PHONE

    name = "Twilio"
    icon = "icon-channel-twilio"

    claim_blurb = _("""Easily add a two way number you have configured with <a href="https://www.twilio.com/">Twilio</a> using their APIs.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600

    ivr_protocol = ChannelType.IVRProtocol.IVR_PROTOCOL_TWIML

    def is_recommended_to(self, user):
        org = user.get_org()
        countrycode = timezone_to_country_code(org.timezone)
        return countrycode in TWILIO_SUPPORTED_COUNTRIES_CONFIG

    def has_attachment_support(self, channel):
        return channel.country in ('US', 'CA')

    def deactivate(self, channel):
        config = channel.config
        client = channel.org.get_twilio_client()
        number_update_args = dict()

        if not channel.is_delegate_sender():
            number_update_args['sms_application_sid'] = ""

        if channel.supports_ivr():
            number_update_args['voice_application_sid'] = ""

        try:
            number_sid = channel.bod or channel.config['number_sid']
            client.phone_numbers.update(number_sid, **number_update_args)
        except Exception:
            if client:
                matching = client.phone_numbers.list(phone_number=channel.address)
                if matching:
                    client.phone_numbers.update(matching[0].sid, **number_update_args)

        if 'application_sid' in config:
            try:
                client.applications.delete(sid=config['application_sid'])
            except TwilioRestException:  # pragma: no cover
                pass
