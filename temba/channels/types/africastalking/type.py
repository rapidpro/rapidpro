# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import requests
import six
import time


from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _

from temba.channels.types.africastalking.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import SENT
from temba.utils.http import HttpEvent, http_headers
from ...models import Channel, ChannelType, SendException


class AfricasTalkingType(ChannelType):
    """
    An Africa's Talking channel (https://africastalking.com/)
    """
    code = 'AT'
    category = ChannelType.Category.PHONE

    name = "Africa's Talking"
    icon = 'icon-channel-external'

    claim_blurb = _("""If you are based in Kenya, Uganda or Malawi you can purchase a short
    code from <a href="http://africastalking.com">Africa's Talking</a> and connect it
    in a few simple steps.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 160
    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your Africa's Talking connection you'll need to set the following callback URLs
        on the Africa's Talking website under your account.
        """
    )

    configuration_urls = (
        dict(
            label=_("Callback URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.at' channel.uuid 'receive' %}",
            description=_(
                """
                You can set the callback URL on your Africa's Talking account by visiting the SMS Dashboard page, then clicking on
                <a href="http://www.africastalking.com/account/sms/smscallback" target="africastalking">Callback URL</a>.
                """
            ),
        ),
        dict(
            label=_("Delivery URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.at' channel.uuid 'status' %}",
            description=_(
                """
                You can set the delivery URL on your Africa's Talking account by visiting the SMS Dashboard page, then clicking on
                <a href="http://www.africastalking.com/account/sms/dlrcallback" target="africastalking">Delivery Reports</a>.
                """
            )
        ),

    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ["Africa/Nairobi", "Africa/Kampala", "Africa/Lilongwe"]

    def is_recommended_to(self, user):
        return self.is_available_to(user)

    def send(self, channel, msg, text):

        payload = dict(username=channel.config['username'],
                       to=msg.urn_path,
                       message=text)

        # if this isn't a shared shortcode, send the from address
        if not channel.config.get('is_shared', False):
            payload['from'] = channel.address

        headers = http_headers(dict(Accept='application/json', apikey=channel.config['api_key']))
        api_url = "https://api.africastalking.com/version1/messaging"
        event = HttpEvent('POST', api_url, urlencode(payload))
        start = time.time()

        try:
            response = requests.post(api_url,
                                     data=payload, headers=headers, timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:
            raise SendException(u"Unable to send message: %s" % six.text_type(e),
                                event=event, start=start)

        if response.status_code != 200 and response.status_code != 201:
            raise SendException("Got non-200 response from API: %d" % response.status_code,
                                event=event, start=start)

        response_data = response.json()

        # grab the status out of our response
        status = response_data['SMSMessageData']['Recipients'][0]['status']
        if status != 'Success':
            raise SendException("Got non success status from API: %s" % status,
                                event=event, start=start)

        # set our external id so we know when it is actually sent, this is missing in cases where
        # it wasn't sent, in which case we'll become an errored message
        external_id = response_data['SMSMessageData']['Recipients'][0]['messageId']

        Channel.success(channel, msg, SENT, start, event=event, external_id=external_id)
