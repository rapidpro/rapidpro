# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.utils.translation import gettext_lazy as _

from temba.channels.models import ChannelType, ConfigUI
from temba.channels.types.messangi.views import ClaimView
from temba.contacts.models import URN


class MessangiType(ChannelType):
    """
    An Messangi channel (http://messangi.com/)
    """

    CONFIG_PUBLIC_KEY = "public_key"
    CONFIG_PRIVATE_KEY = "private_key"
    CONFIG_CARRIER_ID = "carrier_id"
    CONFIG_INSTANCE_ID = "instance_id"

    code = "MG"
    name = "Messangi"
    category = ChannelType.Category.PHONE

    courier_url = r"^mg/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = ["America/Jamaica"]

    claim_blurb = _(
        "If you are based in Jamaica, you can purchase a short code from %(link)s and connect it in a few simple steps."
    ) % {"link": '<a target="_blank" href="http://www.messangi.com/">Messangi</a>'}
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll need to set the following callback URLs on your Messangi account."
        ),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Receive URL"),
                help=_("To receive incoming messages, you need to set the receive URL for your Messangi account."),
            ),
        ],
    )
