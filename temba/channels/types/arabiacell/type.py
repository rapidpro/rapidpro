# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import TEL_SCHEME
from ...models import ChannelType
from .views import ClaimView


class ArabiaCellType(ChannelType):
    """
    An ArabiaCell channel type (http://arabiacell.com)
    """
    code = 'AC'
    name = "ArabiaCell"
    available_timezones = ["Asia/Amman"]
    recommended_timezones = ["Asia/Amman"]
    category = ChannelType.Category.PHONE
    schemes = [TEL_SCHEME]
    max_length = 1530
    attachment_support = False

    claim_view = ClaimView
    claim_blurb = _(
        """
        If you have an <a href="https://www.arabiacell.com/">ArabiaCell</a> number,
        you can quickly connect it using their APIs.
        """
    )

    configuration_blurb = _(
        """
        To finish connecting your channel, you need to have ArabiaCell configure the URL below for your shortcode.
        """
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{channel.callback_domain}}/c/ac/{{channel.uuid}}/receive",
            description=_("This URL should be called by ArabiaCell when new messages are received."),
        ),
    )
