# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import six

from django.utils.translation import ugettext_lazy as _

from temba.channels.models import ChannelType
from temba.channels.types.novo.views import ClaimView
from temba.contacts.models import TEL_SCHEME


class NovoType(ChannelType):
    """
    A Novo channel (http://www.novotechnologyinc.com/)
    """

    CONFIG_MERCHANT_ID = "merchant_id"
    CONFIG_MERCHANT_SECRET = "merchant_secret"

    courier_url = r"^nv/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    code = "NV"
    category = ChannelType.Category.PHONE

    name = "Novo"

    claim_blurb = _(
        """If you are based in Trinidad & Tobago, you can purchase a short code from <a href="http://www.novotechnologyinc.com/">Novo</a> and connect it in a few simple steps."""
    )
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 160

    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your Novo connection you'll need to set the following callback URLs on your Novo account.
        """
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.nv' channel.uuid 'receive' %}",
            description=_("To receive incoming messages, you need to set the receive URL for your Novo account."),
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ["America/Port_of_Spain"]

    def send(self, channel, msg, text):  # pragma: no cover
        raise Exception("Sending Novo messages is only possible via Courier")
