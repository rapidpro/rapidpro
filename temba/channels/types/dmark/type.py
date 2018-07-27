# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import six

from django.utils.translation import ugettext_lazy as _

from temba.channels.types.dmark.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from ...models import ChannelType


class DMarkType(ChannelType):
    """
    A DMark Channel Type http://smsapi1.dmarkmobile.com/
    """
    code = 'DK'
    category = ChannelType.Category.PHONE

    name = "DMark"
    icon = 'icon-channel-external'

    claim_blurb = _("""If you are based in Uganda or DRC you can purchase a short
    code from <a href="http://dmarkmobile.com/">DMark Mobile</a> and connect it
    in a few simple steps.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 459
    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your DMark channel you need to set DMark to send MO messages to the URL below.
        """
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.dk' channel.uuid 'receive' %}",
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ["Africa/Kampala", "Africa/Kinshasa"]

    def send(self, channel, msg, text):  # pragma: no cover
        raise Exception("Sending DMark messages is only possible via Courier")
