# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import requests
import six
import time

from django.utils.translation import ugettext_lazy as _
from temba.contacts.models import Contact, ContactURN, URN, FACEBOOK_SCHEME
from temba.msgs.models import Attachment, WIRED
from temba.orgs.models import Org
from temba.triggers.models import Trigger
from temba.utils.http import HttpEvent
from .views import ClaimView
from ...models import Channel, ChannelType, SendException


class FacebookType(ChannelType):
    """
    A Facebook channel
    """
    code = 'FB'
    category = ChannelType.Category.SOCIAL_MEDIA

    name = "Facebook"
    icon = 'icon-facebook-official'

    claim_blurb = _("""Add a <a href="http://facebook.com">Facebook</a> bot to send and receive messages on behalf
    of one of your Facebook pages for free. You will need to create a Facebook application on their
    <a href="http://developers.facebook.com">developers</a> site first.""")
    claim_view = ClaimView

    schemes = [FACEBOOK_SCHEME]
    max_length = 320
    attachment_support = True
    free_sending = True

    def deactivate(self, channel):
        config = channel.config
        requests.delete('https://graph.facebook.com/v2.5/me/subscribed_apps', params={
            'access_token': config[Channel.CONFIG_AUTH_TOKEN]
        })

    def activate_trigger(self, trigger):
        # if this is new conversation trigger, register for the FB callback
        if trigger.trigger_type == Trigger.TYPE_NEW_CONVERSATION:
            self._set_call_to_action(trigger.channel, 'get_started')

    def deactivate_trigger(self, trigger):
        # for any new conversation triggers, clear out the call to action payload
        if trigger.trigger_type == Trigger.TYPE_NEW_CONVERSATION:
            self._set_call_to_action(trigger.channel, None)

    def send(self, channel, msg, text):
        # build our payload
        payload = {'message': {'text': text}}

        metadata = msg.metadata if hasattr(msg, 'metadata') else {}
        quick_replies = metadata.get('quick_replies', [])
        formatted_replies = [dict(title=item[:self.quick_reply_text_size], payload=item[:self.quick_reply_text_size],
                                  content_type='text') for item in quick_replies]

        if quick_replies:
            payload['message']['quick_replies'] = formatted_replies

        # this is a ref facebook id, temporary just for this message
        if URN.is_path_fb_ref(msg.urn_path):
            payload['recipient'] = dict(user_ref=URN.fb_ref_from_path(msg.urn_path))
        else:
            payload['recipient'] = dict(id=msg.urn_path)

        url = "https://graph.facebook.com/v2.5/me/messages"
        params = {'access_token': channel.config[Channel.CONFIG_AUTH_TOKEN]}
        headers = {'Content-Type': 'application/json'}
        start = time.time()

        payload = json.dumps(payload)
        event = HttpEvent('POST', url, json.dumps(payload))

        try:
            response = requests.post(url, payload, params=params, headers=headers, timeout=15)
            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        # for now we only support sending one attachment per message but this could change in future
        attachments = Attachment.parse_all(msg.attachments)
        attachment = attachments[0] if attachments else None

        if attachment:
            category = attachment.content_type.split('/')[0]

            payload = json.loads(payload)
            payload['message'] = {'attachment': {'type': category, 'payload': {'url': attachment.url}}}
            payload = json.dumps(payload)

            event = HttpEvent('POST', url, payload)

            try:
                response = requests.post(url, payload, params=params, headers=headers, timeout=15)
                event.status_code = response.status_code
                event.response_body = response.text
            except Exception as e:
                raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200:  # pragma: no cover
            raise SendException("Got non-200 response [%d] from Facebook" % response.status_code,
                                event=event, start=start)

        # grab our external id out, Facebook response is in format:
        # "{"recipient_id":"997011467086879","message_id":"mid.1459532331848:2534ddacc3993a4b78"}"
        external_id = None
        try:
            external_id = response.json()['message_id']
        except Exception as e:  # pragma: no cover
            # if we can't pull out our message id, that's ok, we still sent
            pass

        # if we sent Facebook a user_ref, look up the real Facebook id for this contact, should be in 'recipient_id'
        if URN.is_path_fb_ref(msg.urn_path):
            contact_obj = Contact.objects.get(id=msg.contact)
            org_obj = Org.objects.get(id=channel.org)
            channel_obj = Channel.objects.get(id=channel.id)

            try:
                real_fb_id = response.json()['recipient_id']

                # associate this contact with our real FB id
                ContactURN.get_or_create(org_obj, contact_obj, URN.from_facebook(real_fb_id), channel=channel_obj)

                # save our ref_id as an external URN on this contact
                ContactURN.get_or_create(org_obj, contact_obj, URN.from_external(URN.fb_ref_from_path(msg.urn_path)))

                # finally, disassociate our temp ref URN with this contact
                ContactURN.objects.filter(id=msg.contact_urn).update(contact=None)

            except Exception as e:  # pragma: no cover
                # if we can't pull out the recipient id, that's ok, msg was sent
                pass

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)

    @staticmethod
    def _set_call_to_action(channel, payload):
        # register for get_started events
        url = 'https://graph.facebook.com/v2.6/%s/thread_settings' % channel.address
        body = {'setting_type': 'call_to_actions', 'thread_state': 'new_thread', 'call_to_actions': []}

        # if we have a payload, set it, otherwise, clear it
        if payload:
            body['call_to_actions'].append({'payload': payload})

        access_token = channel.config[Channel.CONFIG_AUTH_TOKEN]

        response = requests.post(url, json=body, params={'access_token': access_token},
                                 headers={'Content-Type': 'application/json'})

        if response.status_code != 200:  # pragma: no cover
            raise Exception(_("Unable to update call to action: %s" % response.text))
