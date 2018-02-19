# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import time
import six

from django.utils.translation import ugettext_lazy as _
from twilio import TwilioRestException

from temba.channels.types.twilio.views import ClaimView
from temba.channels.views import TWILIO_SUPPORTED_COUNTRIES_CONFIG
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import WIRED, Attachment
from temba.utils.timezones import timezone_to_country_code
from temba.utils.twilio import TembaTwilioRestClient
from ...models import Channel, ChannelType, SendException


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

    def send(self, channel, msg, text):
        callback_url = Channel.build_twilio_callback_url(channel.callback_domain, channel.channel_type, channel.uuid, msg.id)

        start = time.time()
        media_urls = []

        if msg.attachments:
            # for now we only support sending one attachment per message but this could change in future
            attachment = Attachment.parse_all(msg.attachments)[0]
            media_urls = [attachment.url]

        if channel.channel_type == 'TW':  # pragma: no cover
            config = channel.config
            client = TembaTwilioRestClient(config.get(Channel.CONFIG_ACCOUNT_SID), config.get(Channel.CONFIG_AUTH_TOKEN),
                                           base=config.get(Channel.CONFIG_SEND_URL))
        else:
            config = channel.config
            client = TembaTwilioRestClient(config.get(Channel.CONFIG_ACCOUNT_SID), config.get(Channel.CONFIG_AUTH_TOKEN))

        try:
            if channel.channel_type == 'TMS':
                messaging_service_sid = channel.config['messaging_service_sid']
                client.messages.create(to=msg.urn_path,
                                       messaging_service_sid=messaging_service_sid,
                                       body=text,
                                       media_url=media_urls,
                                       status_callback=callback_url)
            else:
                client.messages.create(to=msg.urn_path,
                                       from_=channel.address,
                                       body=text,
                                       media_url=media_urls,
                                       status_callback=callback_url)

            Channel.success(channel, msg, WIRED, start, events=client.messages.events)

        except TwilioRestException as e:
            fatal = False

            # user has blacklisted us, stop the contact
            if e.code == 21610:
                from temba.contacts.models import Contact
                fatal = True
                contact = Contact.objects.get(id=msg.contact)
                contact.stop(contact.modified_by)

            raise SendException(e.msg, events=client.messages.events, fatal=fatal)

        except Exception as e:
            raise SendException(six.text_type(e), events=client.messages.events)
