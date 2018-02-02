# -*- coding: utf-8 -*-
from __future__ import unicode_literals, absolute_import

import six

from django.utils.translation import ugettext_lazy as _

from temba.channels.views import AuthenticatedExternalClaimView
from temba.contacts.models import TEL_SCHEME
from ...models import ChannelType


class MtargetType(ChannelType):
    """
    An Mtarget channel type (https://www.mtarget.fr/)
    """
    code = 'MT'
    category = ChannelType.Category.PHONE

    name = "Mtarget"
    icon = 'icon-channel-external'

    claim_blurb = _("""If you have an <a href="https://www.mtarget.fr/">Mtarget</a> number, you can quickly connect it using their APIs.""")
    claim_view = AuthenticatedExternalClaimView

    schemes = [TEL_SCHEME]
    max_length = 765
    attachment_support = False

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ["Africa/Kigali", "Africa/Yaoundé", "Africa/Kinshasa", "Europe/Paris"]

    def is_recommended_to(self, user):
        return self.is_available_to(user)

    def send(self, channel, msg, text):  # pragma: no cover
        raise Exception("Sending of mtarget messages only supported in courier")
