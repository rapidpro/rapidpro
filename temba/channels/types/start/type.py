# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import time
import requests
import six

from django.utils.translation import ugettext_lazy as _
from xml.sax.saxutils import quoteattr, escape

from temba.channels.views import AuthenticatedExternalClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent, http_headers
from ...models import Channel, ChannelType, SendException


class StartType(ChannelType):
    """
    An Start Mobile channel (https://bulk.startmobile.ua/)
    """

    code = 'ST'
    category = ChannelType.Category.PHONE

    name = "Start Mobile"

    claim_blurb = _("""Easily add a two way number you have configured with <a href="https://bulk.startmobile.ua/">Start Mobile</a> using their APIs.""")
    claim_view = AuthenticatedExternalClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600

    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your Start connection you'll need to notify Start of the following receiving URL.
        """
    )

    configuration_urls = (
        dict(
            label=_("Inbound URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.st' channel.uuid 'receive' %}",
            description=_("This endpoint should be called by Start when new messages are received to your number."),
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ["Europe/Kiev"]

    def send(self, channel, msg, text):

        url = 'http://bulk.startmobile.com.ua/clients.php'
        post_body = u"""
          <message>
            <service id="single" source=$$FROM$$ validity=$$VALIDITY$$/>
            <to>$$TO$$</to>
            <body content-type="plain/text" encoding="plain">$$BODY$$</body>
          </message>
        """
        post_body = post_body.replace("$$FROM$$", quoteattr(channel.address))

        # tell Start to attempt to deliver this message for up to 12 hours
        post_body = post_body.replace("$$VALIDITY$$", quoteattr("+12 hours"))
        post_body = post_body.replace("$$TO$$", escape(msg.urn_path))
        post_body = post_body.replace("$$BODY$$", escape(text))
        event = HttpEvent('POST', url, post_body)
        post_body = post_body.encode('utf8')

        start = time.time()
        try:
            headers = http_headers(extra={'Content-Type': 'application/xml; charset=utf8'})

            response = requests.post(url,
                                     data=post_body,
                                     headers=headers,
                                     auth=(channel.config[Channel.CONFIG_USERNAME], channel.config[Channel.CONFIG_PASSWORD]),
                                     timeout=30)

            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if (response.status_code != 200 and response.status_code != 201) or response.text.find("error") >= 0:
            raise SendException("Error Sending Message", event=event, start=start)

        # parse out our id, this is XML but we only care about the id
        external_id = None
        start_idx = response.text.find("<id>")
        end_idx = response.text.find("</id>")
        if end_idx > start_idx > 0:
            external_id = response.text[start_idx + 4:end_idx]

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)
