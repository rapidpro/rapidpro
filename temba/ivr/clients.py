# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import requests
import six
import time

from django.conf import settings
from django.core.urlresolvers import reverse
from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _
from temba.channels.models import ChannelLog
from temba.contacts.models import Contact, URN
from temba.flows.models import Flow
from temba.ivr.models import IVRCall
from temba.utils.http import HttpEvent
from temba.utils.nexmo import NexmoClient as NexmoCli
from temba.utils.twilio import TembaTwilioRestClient
from twilio import TwilioRestException
from twilio.util import RequestValidator
from nexmo import AuthenticationError, ClientError, ServerError


class IVRException(Exception):
    pass


class NexmoClient(NexmoCli):

    def __init__(self, api_key, api_secret, app_id, app_private_key, org):
        self.org = org
        NexmoCli.__init__(self, api_key, api_secret, app_id, app_private_key)
        self.events = []

    def validate(self, request):
        return True

    def parse(self, host, response):  # pragma: no cover

        # save an http event for logging later
        request = response.request
        self.events.append(HttpEvent(request.method, request.url, request.body, response.status_code, response.text))

        # Nexmo client doesn't extend object, so can't call super
        if response.status_code == 401:
            raise AuthenticationError
        elif response.status_code == 204:  # pragma: no cover
            return None
        elif 200 <= response.status_code < 300:
            return response.json()
        elif 400 <= response.status_code < 500:  # pragma: no cover
            message = "{code} response from {host}".format(code=response.status_code, host=host)
            raise ClientError(message)
        elif 500 <= response.status_code < 600:  # pragma: no cover
            message = "{code} response from {host}".format(code=response.status_code, host=host)
            raise ServerError(message)

    def start_call(self, call, to, from_, status_callback):
        url = 'https://%s%s' % (self.org.get_brand_domain(), reverse('ivr.ivrcall_handle', args=[call.pk]))

        params = dict()
        params['answer_url'] = [url]
        params['answer_method'] = 'POST'
        params['to'] = [dict(type='phone', number=to.strip('+'))]
        params['from'] = dict(type='phone', number=from_.strip('+'))
        params['event_url'] = ["%s?has_event=1" % url]
        params['event_method'] = "POST"

        try:
            response = self.create_call(params=params)
            call_uuid = response.get('uuid', None)
            call.external_id = six.text_type(call_uuid)
            call.save()
            for event in self.events:
                ChannelLog.log_ivr_interaction(call, 'Started call', event)

        except Exception as e:
            event = HttpEvent('POST', 'https://api.nexmo.com/v1/calls', json.dumps(params), response_body=six.text_type(e))
            ChannelLog.log_ivr_interaction(call, 'Call start failed', event, is_error=True)

            call.status = IVRCall.FAILED
            call.save()
            raise IVRException(_("Nexmo call failed, with error %s") % six.text_type(e))

    def download_media(self, call, media_url):
        """
        Fetches the recording and stores it with the provided recording_id
        :param media_url: the url where the media lives
        :return: the url for our downloaded media with full content type prefix
        """
        attempts = 0
        response = None
        while attempts < 4:
            response = self.download_recording(media_url)

            # in some cases Twilio isn't ready for us to fetch the recording URL yet, if we get a 404
            # sleep for a bit then try again up to 4 times
            if response.status_code == 200:
                break
            else:
                attempts += 1
                time.sleep(.250)

        content_type, downloaded = self.org.save_response_media(response)

        if content_type:
            # log that we downloaded it to our own url
            request = response.request
            event = HttpEvent(request.method, request.url, request.body, response.status_code, downloaded)
            ChannelLog.log_ivr_interaction(call, "Downloaded media", event)

            return '%s:%s' % (content_type, downloaded)

        return None

    def hangup(self, call):
        self.update_call(call.external_id, action='hangup', call_id=call.external_id)
        for event in self.events:
            ChannelLog.log_ivr_interaction(call, 'Hung up call', event)


class TwilioClient(TembaTwilioRestClient):

    def __init__(self, account, token, org, **kwargs):
        self.org = org
        super(TwilioClient, self).__init__(account=account, token=token, **kwargs)

    def start_call(self, call, to, from_, status_callback):
        if not settings.SEND_CALLS:
            raise IVRException("SEND_CALLS set to False, skipping call start")

        try:
            twilio_call = self.calls.create(to=to,
                                            from_=call.channel.address,
                                            url=status_callback,
                                            status_callback=status_callback)
            call.external_id = six.text_type(twilio_call.sid)
            call.save()

            for event in self.calls.events:
                ChannelLog.log_ivr_interaction(call, 'Started call', event)

        except TwilioRestException as twilio_error:
            message = 'Twilio Error: %s' % twilio_error.msg
            if twilio_error.code == 20003:
                message = _('Could not authenticate with your Twilio account. Check your token and try again.')

            raise IVRException(message)

    def validate(self, request):  # pragma: needs cover
        validator = RequestValidator(self.auth[1])
        signature = request.META.get('HTTP_X_TWILIO_SIGNATURE', '')

        url = "https://%s%s" % (request.get_host(), request.get_full_path())
        return validator.validate(url, request.POST, signature)

    def download_media(self, media_url):
        """
        Fetches the recording and stores it with the provided recording_id
        :param media_url: the url where the media lives
        :return: the url for our downloaded media with full content type prefix
        """
        response = None
        attempts = 0
        while attempts < 4:
            response = requests.get(media_url, stream=True, auth=self.auth)

            # in some cases Twilio isn't ready for us to fetch the recording URL yet, if we get a 404
            # sleep for a bit then try again up to 4 times
            if response.status_code == 200:
                break
            else:
                attempts += 1
                time.sleep(.250)

        content_type, downloaded = self.org.save_response_media(response)
        if content_type:
            return '%s:%s' % (content_type, downloaded)

        return None  # pragma: needs cover

    def hangup(self, call):
        response = self.calls.hangup(call.external_id)
        for event in self.calls.events:
            ChannelLog.log_ivr_interaction(call, 'Hung up call', event)
        return response


class VerboiceClient:  # pragma: needs cover

    def __init__(self, channel):
        self.endpoint = 'https://verboice.instedd.org/api/call'

        config = channel.config
        self.auth = (config.get('username', None), config.get('password', None))

        # this is the verboice channel, not our channel
        self.verboice_channel = config.get('channel', None)

    def validate(self, request):
        # verboice isn't smart here
        return True

    def start_call(self, call, to, from_, status_callback):
        if not settings.SEND_CALLS:
            raise IVRException("SEND_CALLS set to False, skipping call start")

        channel = call.channel
        Contact.get_or_create(channel.org, URN.from_tel(to), channel)

        # Verboice differs from Twilio in that they expect the first block of twiml up front
        payload = six.text_type(Flow.handle_call(call))

        # now we can post that to verboice
        url = "%s?%s" % (self.endpoint, urlencode(dict(channel=self.verboice_channel, address=to)))
        response = requests.post(url, data=payload, auth=self.auth).json()

        if 'call_id' not in response:
            raise IVRException(_('Verboice connection failed.'))

        # store the verboice call id in our IVRCall
        call.external_id = response['call_id']
        call.status = IVRCall.IN_PROGRESS
        call.save()
