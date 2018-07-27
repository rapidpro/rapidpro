# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import time
import requests
import six

from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _
from temba.channels.types.dartmedia.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import SENT
from temba.utils.http import HttpEvent, http_headers
from ...models import Channel, ChannelType, SendException


# Hub9 is an aggregator in Indonesia, set this to the endpoint for your service
# and make sure you send from a whitelisted IP Address
HUB9_ENDPOINT = 'http://175.103.48.29:28078/testing/smsmt.php'


class Hub9Type(ChannelType):
    """
    An DartMedia channel (http://dartmedia.biz/)
    """

    code = 'H9'
    category = ChannelType.Category.PHONE

    name = "Hub9"

    claim_blurb = _("""Easily add a two way number you have configured with Hub9 in Indonesia.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    show_public_addresses = True

    configuration_blurb = _(
        """
        To finish configuring your Hub9 connection you'll need to provide them with the following details.
        """
    )

    configuration_urls = (
        dict(
            label=_("Received URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.h9' channel.uuid 'receive' %}",
            description=_(
                """
                This endpoint should be called by Hub9 when new messages are received to your number.
                You can set the receive URL on your Hub9 account by contacting your sales agent.
                """
            ),
        ),
        dict(
            label=_("Delivered URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.h9' channel.uuid 'delivered' %}",
            description=_(
                """
                This endpoint should be called by Hub9 when a message has been to the final recipient. (delivery reports)
                You can set the delivery callback URL on your Hub9 account by contacting your sales agent.
                """
            ),
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ["Asia/Jakarta"]

    def is_recommended_to(self, user):
        return self.is_available_to(user)

    def send(self, channel, msg, text):

        # http://175.103.48.29:28078/testing/smsmt.php?
        #   userid=xxx
        #   &password=xxxx
        #   &original=6282881134567
        #   &sendto=628159152565
        #   &messagetype=0
        #   &messageid=1897869768
        #   &message=Test+Normal+Single+Message&dcs=0
        #   &udhl=0&charset=utf-8
        #
        url = HUB9_ENDPOINT
        payload = dict(userid=channel.config['username'], password=channel.config['password'],
                       original=channel.address.lstrip('+'), sendto=msg.urn_path.lstrip('+'),
                       messageid=msg.id, message=text, dcs=0, udhl=0)

        # build up our querystring and send it as a get
        send_url = "%s?%s" % (url, urlencode(payload))
        payload['password'] = 'x' * len(payload['password'])
        masked_url = "%s?%s" % (url, urlencode(payload))

        event = HttpEvent('GET', masked_url)

        start = time.time()

        try:
            response = requests.get(send_url, headers=http_headers(), timeout=15)
            event.status_code = response.status_code
            event.response_body = response.text
            if not response:  # pragma: no cover
                raise SendException("Unable to send message",
                                    event=event, start=start)

            if response.status_code != 200 and response.status_code != 201:
                raise SendException("Received non 200 status: %d" % response.status_code,
                                    event=event, start=start)

            # if it wasn't successfully delivered, throw
            if response.text != "000":  # pragma: no cover
                error = "Unknown error"
                if response.text == "001":
                    error = "Error 001: Authentication Error"
                elif response.text == "101":
                    error = "Error 101: Account expired or invalid parameters"

                raise SendException(error, event=event, start=start)

            Channel.success(channel, msg, SENT, start, event=event)

        except SendException as e:
            raise e
        except Exception as e:  # pragma: no cover
            reason = "Unknown error"
            try:
                if e.message and e.message.reason:
                    reason = e.message.reason
            except Exception:
                pass
            raise SendException(u"Unable to send message: %s" % six.text_type(reason)[:64], event=event, start=start)
