from __future__ import unicode_literals, absolute_import

import json
import requests
import six
import time

from django.core.validators import URLValidator
from django.utils.translation import ugettext_lazy as _
from temba.channels.models import ChannelType, Channel, SendException
from temba.channels.types.vumi.views import ClaimView
from temba.contacts.models import TEL_SCHEME, Contact
from temba.msgs.models import Msg, WIRED
from temba.ussd.models import USSDSession
from temba.utils.http import HttpEvent, http_headers


class VumiType(ChannelType):
    """
    A Vumi channel
    """

    code = 'VM'
    category = ChannelType.Category.PHONE

    name = "Vumi"

    claim_blurb = _("""Easily connect your <a href="http://vumi.com/">Vumi</a> account to take advantage of two way texting across different mediums.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ["Africa/Lagos"]

    def send(self, channel, msg, text):

        is_ussd = Channel.get_type_from_code(channel.channel_type).category == ChannelType.Category.USSD
        channel.config['transport_name'] = 'ussd_transport' if is_ussd else 'mtech_ng_smpp_transport'

        session = None
        session_event = None
        in_reply_to = None

        if is_ussd:
            session = USSDSession.objects.get_with_status_only(msg.connection_id)
            if session and session.should_end:
                session_event = "close"
            else:
                session_event = "resume"

        if msg.response_to_id:
            in_reply_to = Msg.objects.values_list('external_id', flat=True).filter(pk=msg.response_to_id).first()

        payload = dict(message_id=msg.id,
                       in_reply_to=in_reply_to,
                       session_event=session_event,
                       to_addr=msg.urn_path,
                       from_addr=channel.address,
                       content=text,
                       transport_name=channel.config['transport_name'],
                       transport_type='ussd' if is_ussd else 'sms',
                       transport_metadata={},
                       helper_metadata={})

        payload = json.dumps(payload)

        headers = http_headers(extra={'Content-Type': 'application/json'})

        api_url_base = channel.config.get('api_url', Channel.VUMI_GO_API_URL)

        url = "%s/%s/messages.json" % (api_url_base, channel.config['conversation_key'])

        event = HttpEvent('PUT', url, json.dumps(payload))

        start = time.time()

        validator = URLValidator()
        validator(url)

        try:
            response = requests.put(url,
                                    data=payload,
                                    headers=headers,
                                    timeout=30,
                                    auth=(channel.config['account_key'], channel.config['access_token']))

            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code not in (200, 201):
            # this is a fatal failure, don't retry
            fatal = response.status_code == 400

            # if this is fatal due to the user opting out, stop them
            if response.text and response.text.find('has opted out') >= 0:
                contact = Contact.objects.get(id=msg.contact)
                contact.stop(contact.modified_by)
                fatal = True

            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, fatal=fatal, start=start)

        # parse our response
        body = response.json()
        external_id = body.get('message_id', '')

        if is_ussd and session and session.should_end:
            session.close()

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)
