from twilio.rest import TwilioRestClient
from temba.contacts.models import Contact, TEL_SCHEME
from temba.flows.models import Flow, FlowRun
from temba.ivr.models import IN_PROGRESS
from django.utils.http import urlencode

import requests

class TwilioClient(TwilioRestClient):
    def start_call(self, call, to, from_, status_callback):

        twilio_call = self.calls.create(to=to,
                                   from_=call.channel.address,
                                   url=status_callback,
                                   status_callback=status_callback)
        call.external_id = unicode(twilio_call.sid)
        call.save()

class VerboiceClient():
    def __init__(self):
        self.endpoint = 'https://verboice.instedd.org/api/call'

    def start_call(self, call, to, from_, status_callback):

        channel = call.channel
        contact = Contact.get_or_create(channel.created_by, channel.org, urns=[(TEL_SCHEME, to)])

        # Verboice differs from Twilio in that they expect the first block of twiml up front
        payload = unicode(Flow.handle_call(call, {}))

        # our config should have our http basic auth parameters and verboice channel
        config = channel.config_json()

        # now we can post that to verboice
        url = "%s?%s" % (self.endpoint, urlencode(dict(channel=config['channel'], address=to)))
        response = requests.post(url, data=payload, auth=(config['username'], config['password'])).json()

        # store the verboice call id in our IVRCall
        call.external_id = response['call_id']
        call.status = IN_PROGRESS
        call.save()
