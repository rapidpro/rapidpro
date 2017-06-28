from __future__ import unicode_literals, absolute_import

import json

from django.conf import settings
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _
from temba.contacts.models import TWITTER_SCHEME
from temba.utils.twitter import TembaTwython
from .views import ClaimView
from ...models import Channel, ChannelType


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

    scheme = TWITTER_SCHEME

    def is_available_to(self, user):
        return user.is_beta()

    def activate(self, channel):
        config = channel.config_json()
        client = TembaTwython(config['api_key'], config['api_secret'], config['access_token'], config['access_token_secret'])

        callback_url = 'https://%s%s' % (settings.HOSTNAME, reverse('handlers.twitter_handler', args=[channel.uuid]))
        webhook = client.register_webhook(callback_url)
        client.subscribe_to_webhook(webhook['id'])

        # save this webhook for later so we can delete it
        config['webhook_id'] = webhook['id']
        channel.config = json.dumps(config)
        channel.save(update_fields=('config',))

    def deactivate(self, channel):
        config = channel.config_json()
        client = TembaTwython(config['api_key'], config['api_secret'], config['access_token'], config['access_token_secret'])

        client.delete_webhook(config['webhook_id'])

    def send(self, channel, msg, text):  # pragma: no cover
        # use regular Twitter channel sending
        return Channel.get_type_for_code('TT').send(channel, msg, text)
