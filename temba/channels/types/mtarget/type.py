# -*- coding: utf-8 -*-
from __future__ import unicode_literals, absolute_import

import six

from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import TEL_SCHEME
from ...models import ChannelType
from .views import ClaimView


class MtargetType(ChannelType):
    """
    An Mtarget channel type (https://www.mtarget.fr/)
    """
    code = 'MT'
    category = ChannelType.Category.PHONE

    name = "Mtarget"
    icon = 'icon-mtarget'

    claim_view = ClaimView
    claim_blurb = _(
        """
        If you have an <a href="https://www.mtarget.fr/">Mtarget</a> account,
        you can quickly connect it using their APIs.
        """
    )

    configuration_blurb = _(
        """
        <h4>
        To finish connecting your channel, you need to have Mtarget configure the URLs below for your Service ID.
        </h4>
        <hr/>

        <h4>Receive URL</h4>
        <code>https://{{channel.callback_domain}}{% url 'courier.mt' channel.uuid 'receive' %}</code>
        <hr/>

        <h4>Status URL</h4>
        <code>https://{{channel.callback_domain}}{% url 'courier.mt' channel.uuid 'status' %}</code>
        <hr/>
        """
    )

    schemes = [TEL_SCHEME]
    max_length = 765
    attachment_support = False

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ["Africa/Kigali", "Africa/Yaoundé", "Africa/Kinshasa", "Europe/Paris"]

    def is_recommended_to(self, user):
        return self.is_available_to(user)
