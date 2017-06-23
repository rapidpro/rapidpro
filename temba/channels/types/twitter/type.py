from __future__ import unicode_literals, absolute_import

import json

from django.conf import settings
from django.urls import reverse
from temba.utils.twitter import TembaTwython
from ...models import ChannelType
from ...tasks import MageStreamAction, notify_mage_task


class TwitterType(ChannelType):
    name = "Twitter"
    code = "TT"

    def activate(self, channel):
        config = channel.config_json()
        new_style = 'api_key' in config

        if new_style:
            client = TembaTwython(config['api_key'], config['api_secret'], config['access_token'], config['access_token_secret'])

            callback_url = 'https://%s%s' % (settings.HOSTNAME, reverse('handlers.twitter_handler', args=[channel.uuid]))
            webhook = client.register_webhook(callback_url)
            client.subscribe_to_webhook(webhook['id'])

            # save this webhook for later so we can delete it
            config['webhook_id'] = webhook['id']
            channel.config = json.dumps(config)
            channel.save(update_fields=('config',))
        else:
            # notify Mage so that it activates this channel
            notify_mage_task.delay(channel.uuid, MageStreamAction.activate.name)

    def deactivate(self, channel):
        config = channel.config_json()
        new_style = 'api_key' in config

        if new_style:
            # if this is a new-style Twitter channel, disable the webhook
            client = TembaTwython(config['api_key'], config['api_secret'], config['access_token'], config['access_token_secret'])
            client.delete_webhook(config['webhook_id'])
        else:
            # if this is an old-style Twitter channel, notify Mage
            notify_mage_task.delay(channel.uuid, MageStreamAction.deactivate.name)
