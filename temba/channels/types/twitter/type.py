# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import TWITTER_SCHEME, TWITTERID_SCHEME
from .views import ClaimView
from ...models import ChannelType
from ...tasks import MageStreamAction, notify_mage_task
from ...views import UpdateTwitterForm


class TwitterType(ChannelType):
    """
    A Twitter channel which uses Mage to stream DMs for a handle which has given access to a Twitter app configured for
    this deployment.
    """
    code = 'TT'
    category = ChannelType.Category.SOCIAL_MEDIA

    name = "Twitter"
    icon = 'icon-twitter'

    claim_blurb = _("""Add a <a href="http://twitter.com">Twitter</a> account to send messages as direct messages.""")
    claim_view = ClaimView

    update_form = UpdateTwitterForm

    schemes = [TWITTER_SCHEME, TWITTERID_SCHEME]
    max_length = 10000
    show_config_page = False
    free_sending = True
    quick_reply_text_size = 36

    FATAL_403S = ("messages to this user right now",  # handle is suspended
                  "users who are not following you")  # handle no longer follows us

    def activate(self, channel):
        # tell Mage to activate this channel
        notify_mage_task.delay(channel.uuid, MageStreamAction.activate.name)

    def deactivate(self, channel):
        # tell Mage to deactivate this channel
        notify_mage_task.delay(channel.uuid, MageStreamAction.deactivate.name)
