# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import six
import time

from django.utils.translation import ugettext_lazy as _
from temba.contacts.models import Contact, TWITTER_SCHEME, TWITTERID_SCHEME, URN
from temba.msgs.models import WIRED
from temba.utils.twitter import TembaTwython
from .views import ClaimView
from ...models import Channel, ChannelType, SendException
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

    def send(self, channel, msg, text):
        twitter = TembaTwython.from_channel(channel)
        start = time.time()

        try:
            urn = getattr(msg, 'urn', URN.from_twitter(msg.urn_path))
            (scheme, path, display) = URN.to_parts(urn)

            # this is a legacy URN (no display), the path is our screen name
            if scheme == TWITTER_SCHEME:
                dm = twitter.send_direct_message(screen_name=path, text=text)
                external_id = dm['id']

            # this is a new twitterid URN, our path is our user id
            else:
                metadata = msg.metadata if hasattr(msg, 'metadata') else {}
                quick_replies = metadata.get('quick_replies', [])
                formatted_replies = [dict(label=item[:self.quick_reply_text_size]) for item in quick_replies]

                if quick_replies:
                    params = {
                        'event': {
                            'type': 'message_create',
                            'message_create': {
                                'target': {'recipient_id': path},
                                'message_data': {
                                    'text': text,
                                    'quick_reply': {'type': 'options', 'options': formatted_replies}
                                }
                            }
                        }
                    }
                    dm = twitter.post('direct_messages/events/new', params=params)
                    external_id = dm['event']['id']
                else:
                    dm = twitter.send_direct_message(user_id=path, text=text)
                    external_id = dm['id']

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

        Channel.success(channel, msg, WIRED, start, events=twitter.events, external_id=external_id)
