# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import six
from django.utils.translation import ugettext_lazy as _

from temba.channels.types.dartmedia.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from ...models import ChannelType

# DartMedia is an aggregator in Indonesia, set this to the endpoint for your service
# and make sure you send from a whitelisted IP Address
DART_MEDIA_ENDPOINT = 'http://202.43.169.11/APIhttpU/receive2waysms.php'


class DartMediaType(ChannelType):
    """
    An DartMedia channel (http://dartmedia.biz/)
    """

    code = 'DA'
    category = ChannelType.Category.PHONE

    name = "DartMedia"

    claim_blurb = _("""Easily add a two way number you have configured with <a href="http://dartmedia.biz/">Dart Media</a> in Indonesia.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 160
    attachment_support = False

    show_public_addresses = True

    configuration_blurb = _(
        """
        To finish configuring your Dart Media connection you'll need to provide them with the following details.
        """
    )

    configuration_urls = (
        dict(
            label=_("Received URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.da' channel.uuid 'receive' %}",
            description=_(
                """
                This endpoint should be called by Dart Media when new messages are received to your number.
                You can set the receive URL on your Dart Media account by contacting your sales agent.
                """
            )
        ),
        dict(
            label=_("Delivered URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.da' channel.uuid 'delivered' %}",
            description=_(
                """
                This endpoint should be called by Dart Media when a message has been to the final recipient. (delivery reports)
                You can set the delivery callback URL on your Dart Media account by contacting your sales agent.
                """
            )
        ),

    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ["Asia/Jakarta"]

    def is_recommended_to(self, user):
        return self.is_available_to(user)
