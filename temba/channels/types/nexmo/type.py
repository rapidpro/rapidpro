# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import time
import regex

from time import sleep

from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import ChannelType, Channel, SendException
from temba.channels.views import UpdateNexmoForm
from temba.channels.types.nexmo.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import SENT
from temba.utils.nexmo import NexmoClient
from temba.utils.timezones import timezone_to_country_code


class NexmoType(ChannelType):
    """
    An Nexmo channel
    """

    code = 'NX'
    category = ChannelType.Category.PHONE

    name = "Nexmo"
    icon = "icon-channel-nexmo"

    claim_blurb = _("""Easily add a two way number you have configured with <a href="https://www.nexmo.com/">Nexmo</a> using their APIs.""")
    claim_view = ClaimView

    update_form = UpdateNexmoForm

    schemes = [TEL_SCHEME]
    max_length = 1600
    max_tps = 1

    ivr_protocol = ChannelType.IVRProtocol.IVR_PROTOCOL_NCCO

    configuration_blurb = _(
        """
        Your Nexmo configuration URLs are as follows. These should have been set up automatically when claiming your number, but if not you can set them from your Nexmo dashboard.
        """
    )

    configuration_urls = (
        dict(
            label=_("Callback URL for Inbound Messages"),
            url="https://{{ channel.callback_domain }}{% url 'courier.nx' channel.uuid 'receive' %}",
            description=_("The callback URL is called by Nexmo when you receive new incoming messages."),
        ),
        dict(
            label=_("Callback URL for Delivery Receipt"),
            url="https://{{ channel.callback_domain }}{% url 'courier.nx' channel.uuid 'status' %}",
            description=_("The delivery URL is called by Nexmo when a message is successfully delivered to a recipient.")
        ),
        dict(
            label=_("Callback URL for Incoming Call"),
            url="https://{{ channel.callback_domain }}{% url 'handlers.nexmo_call_handler' 'answer' channel.uuid %}",
            description=_("The callback URL is called by Nexmo when you receive an incoming call.")
        ),

    )

    def is_recommended_to(self, user):
        NEXMO_RECOMMENDED_COUNTRIES = ['US', 'CA', 'GB', 'AU', 'AT', 'FI', 'DE', 'HK', 'HU',
                                       'LT', 'NL', 'NO', 'PL', 'SE', 'CH', 'BE', 'ES', 'ZA']
        org = user.get_org()
        countrycode = timezone_to_country_code(org.timezone)
        return countrycode in NEXMO_RECOMMENDED_COUNTRIES

    def send(self, channel, msg, text):

        config = channel.config

        client = NexmoClient(config[Channel.CONFIG_NEXMO_API_KEY], config[Channel.CONFIG_NEXMO_API_SECRET],
                             config[Channel.CONFIG_NEXMO_APP_ID], config[Channel.CONFIG_NEXMO_APP_PRIVATE_KEY])
        start = time.time()

        callback_url = "https://" + channel.callback_domain + reverse('courier.nx', args=[channel.uuid, 'receive'])

        event = None
        attempts = 0
        while not event:
            try:
                (message_id, event) = client.send_message_via_nexmo(channel.address, msg.urn_path, text, callback_url)
            except SendException as e:
                match = regex.match(r'.*Throughput Rate Exceeded - please wait \[ (\d+) \] and retry.*', e.events[0].response_body)

                # this is a throughput failure, attempt to wait up to three times
                if match and attempts < 3:
                    sleep(float(match.group(1)) / 1000)
                    attempts += 1
                else:
                    raise e

        Channel.success(channel, msg, SENT, start, event=event, external_id=message_id)
