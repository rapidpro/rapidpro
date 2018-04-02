# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import logging

import six
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import TWITTER_SCHEME, TWITTERID_SCHEME
from temba.utils.twitter import TembaTwython
from .views import ClaimView
from ...models import ChannelType
from ...views import UpdateTwitterForm

logger = logging.getLogger(__name__)


class TwitterActivityType(ChannelType):
    """
    A Twitter channel which uses Twitter's new Activity API (currently in beta) to stream DMs.
    """
    code = 'TWT'
    category = ChannelType.Category.SOCIAL_MEDIA

    name = "Twitter Activity API"
    icon = 'icon-twitter'

    claim_blurb = _("""If you have access to the new <a href="https://dev.twitter.com/webhooks/account-activity">Twitter
    Activity API</a> which is currently in beta, you can add a Twitter channel for that here.""")
    claim_view = ClaimView

    update_form = UpdateTwitterForm

    schemes = [TWITTER_SCHEME, TWITTERID_SCHEME]
    show_config_page = False
    free_sending = True

    def is_available_to(self, user):
        return user.is_beta()

    def activate(self, channel):
        config = channel.config
        client = TembaTwython(config['api_key'], config['api_secret'], config['access_token'], config['access_token_secret'])

        callback_url = 'https://%s%s' % (channel.callback_domain, reverse('courier.twt', args=[channel.uuid]))
        try:
            client.register_webhook(config['env_name'], callback_url)
            client.subscribe_to_webhook(config['env_name'])
        except Exception as e:  # pragma: no cover
            logger.exception(six.text_type(e))

    def deactivate(self, channel):
        config = channel.config
        client = TembaTwython(config['api_key'], config['api_secret'], config['access_token'], config['access_token_secret'])
        client.delete_webhook(config['env_name'])
