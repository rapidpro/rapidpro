from twilio import TwilioRestException
from twilio.rest import TwilioRestClient
from temba.contacts.models import Contact, TEL_SCHEME
from temba.flows.models import Flow, FlowRun
from temba.ivr.models import IN_PROGRESS
from django.utils.http import urlencode
from django.conf import settings
import json
from django.utils.translation import ugettext_lazy as _

import requests
from twilio.util import RequestValidator


class IVRException(Exception):
    pass


class TwilioClient(TwilioRestClient):
    def start_call(self, call, to, from_, status_callback):

        try:
            twilio_call = self.calls.create(to=to,
                                            from_=call.channel.address,
                                            url=status_callback,
                                            status_callback=status_callback)
            call.external_id = unicode(twilio_call.sid)
            call.save()
        except TwilioRestException as twilio:
            message = 'Twilio Error: %s' % twilio.msg
            if twilio.code == 20003:
                message = _('Could not authenticate with your Twilio account. Check your token and try again.')

            raise IVRException(message)

    def validate(self, request):
        validator = RequestValidator(self.auth[1])
        signature = request.META.get('HTTP_X_TWILIO_SIGNATURE', '')

        base_url = settings.TEMBA_HOST
        url = "https://%s%s" % (base_url, request.get_full_path())
        return validator.validate(url, request.POST, signature)


class VerboiceClient:

    def __init__(self, channel):
        self.endpoint = 'https://verboice.instedd.org/api/call'

        config = json.loads(channel.config)
        self.auth = (config.get('username', None), config.get('password', None))

        # this is the verboice channel, not our channel
        self.verboice_channel = config.get('channel', None)

    def validate(self, request):
        # verboice isn't smart here
        return True

    def start_call(self, call, to, from_, status_callback):

        channel = call.channel
        Contact.get_or_create(channel.org, channel.created_by, urns=[(TEL_SCHEME, to)])

        # Verboice differs from Twilio in that they expect the first block of twiml up front
        payload = unicode(Flow.handle_call(call, {}))

        # now we can post that to verboice
        url = "%s?%s" % (self.endpoint, urlencode(dict(channel=self.verboice_channel, address=to)))
        response = requests.post(url, data=payload, auth=self.auth).json()

        if 'call_id' not in response:
            raise IVRException(_('Verboice connection failed.'))

        # store the verboice call id in our IVRCall
        call.external_id = response['call_id']
        call.status = IN_PROGRESS
        call.save()