# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import six

from django.utils.translation import ugettext_lazy as _

from temba.channels.models import ChannelType
from temba.channels.types.messangi.views import ClaimView
from temba.contacts.models import TEL_SCHEME


class MessangiType(ChannelType):
    """
    An Messangi channel (http://messangi.com/)
    """

    CONFIG_PUBLIC_KEY = "public_key"
    CONFIG_PRIVATE_KEY = "private_key"
    CONFIG_CARRIER_ID = "carrier_id"
    CONFIG_INSTANCE_ID = "instance_id"

    courier_url = r"^mg/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    code = "MG"
    category = ChannelType.Category.PHONE

    name = "Messangi"

    claim_blurb = _(
        """If you are based in Jamaica, you can purchase a short code from <a href="http://www.messangi.com/">Messangi</a> and connect it in a few simple steps."""
    )
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 150

    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your Messangi connection you'll need to set the following callback URLs on your Messangi account.
        """
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.mg' channel.uuid 'receive' %}",
            description=_("To receive incoming messages, you need to set the receive URL for your Messangi account."),
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ["America/Jamaica"]

    def send(self, channel, msg, text):  # pragma: no cover
        raise Exception("Sending Messangi messages is only possible via Courier")
