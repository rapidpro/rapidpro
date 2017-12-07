from __future__ import unicode_literals, absolute_import

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
