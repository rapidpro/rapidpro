from __future__ import unicode_literals, absolute_import

import json
import six
import time

from django.conf import settings
from django.urls import reverse
from temba.utils.twitter import TembaTwython
from ...models import Channel, ChannelType, SendException
from ...tasks import MageStreamAction, notify_mage_task


class TwitterType(ChannelType):
    name = "Twitter"
    code = "TT"
    scheme = 'twitter'
    max_length = 10000
    show_config_page = False

    FATAL_403S = ("messages to this user right now",  # handle is suspended
                  "users who are not following you")  # handle no longer follows us

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

    def send(self, channel, msg, text):
        from temba.msgs.models import WIRED
        from temba.contacts.models import Contact

        twitter = TembaTwython.from_channel(channel)
        start = time.time()

        try:
            # TODO: wrap in such a way that we can get full request/response details
            dm = twitter.send_direct_message(screen_name=msg.urn_path, text=text)
        except Exception as e:
            error_code = getattr(e, 'error_code', 400)
            fatal = False

            if error_code == 404:  # handle doesn't exist
                fatal = True
            elif error_code == 403:
                for err in self.FATAL_403S:
                    if six.text_type(e).find(err) >= 0:
                        fatal = True
                        break

            # if message can never be sent, stop them contact
            if fatal:
                contact = Contact.objects.get(id=msg.contact)
                contact.stop(contact.modified_by)

            raise SendException(str(e), events=twitter.events, fatal=fatal, start=start)

        external_id = dm['id']
        Channel.success(channel, msg, WIRED, start, events=twitter.events, external_id=external_id)
