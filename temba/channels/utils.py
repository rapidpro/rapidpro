from __future__ import absolute_import, unicode_literals
from django.http.response import HttpResponse
from twilio.util import RequestValidator
from temba import settings
from .models import Channel
from temba.contacts.models import URN, Contact
from temba.flows.models import FlowRun, Flow
from temba.ivr.models import IVRCall
from temba.msgs.models import Msg
from temba.triggers.models import Trigger
from twilio import twiml
from django.core.exceptions import ValidationError

__author__ = 'teehamaral'


class TwilioPostHandler(object):

    def __init__(self, request):
        self.request = request
        self.call_sid = request.REQUEST.get('CallSid', None)
        self.direction = request.REQUEST.get('Direction', None)
        self.call_status = request.REQUEST.get('CallStatus', None)
        self.to_number = request.REQUEST.get('To', None)
        self.to_country = request.REQUEST.get('ToCountry', None)
        self.from_number = request.REQUEST.get('From', None)
        self.action = request.GET.get('action', 'received')
        self.sms_id = request.GET.get('id', None)
        self.sms_status = request.POST.get('SmsStatus', None)
        self.media = request.POST.get('NumMedia', 0)
        self.body = request.POST.get('Body')
        self.channel = None

    def get_signature(self):
        return self.request.META.get('HTTP_X_TWILIO_SIGNATURE', '')

    def get_url(self):
        return "https://%s%s" % (settings.TEMBA_HOST, self.request.get_full_path())

    def normalize_urn(self):
        to_number = self.to_number
        to_country = self.to_country

        if not to_number.startswith('+') and to_country:
            to_number, valid = URN.normalize_number(to_number, to_country)

        return to_number

    def check_is_inbound_call(self):
        if self.to_number and self.call_sid and self.direction == 'inbound' and self.call_status == 'ringing':
            return True
        return False

    def check_answer_channel(self, type):
        address = self.normalize_urn()
        return Channel.objects.filter(address=address, channel_type=type, role__contains='A', is_active=True).exclude(org=None).first()

    def set_channel(self, type, channel_uuid=None):
        self.channel = self.get_channel(type, channel_uuid)
        return self.channel

    def get_channel(self, type, channel_uuid=None):
        assert self.to_number or channel_uuid, ValidationError("number or channel_uuid is required")

        channel = Channel.objects.filter(is_active=True, channel_type=type)

        if channel_uuid:
            channel = channel.filter(uuid=channel_uuid)

        if self.to_number:
            address = self.normalize_urn()
            channel = channel.filter(address=address)

        return channel.first()

    def get_validator(self, client):
        return RequestValidator(client.auth[1])

    def check_validator(self, client):
        validator = self.get_validator(client)
        return validator.validate(self.get_url(), self.request.POST, self.get_signature())

    def get_number_normalized(self):
        return URN.from_tel(self.from_number)

    def get_or_create_contact(self):
        channel = self.channel
        return Contact.get_or_create(channel.org, channel.created_by, urns=[self.get_number_normalized()], channel=channel)

    def get_urn_obj(self):
        contact = self.get_or_create_contact()
        return contact.urn_objects[self.get_number_normalized()]

    def get_call_flow(self):
        return Trigger.find_flow_for_inbound_call(self.get_or_create_contact())

    def create_incoming_ivr(self):
        channel = self.channel
        contact = self.get_or_create_contact()
        urn_obj = self.get_urn_obj()
        flow = self.get_call_flow()

        call = IVRCall.create_incoming(channel, contact, urn_obj, flow, channel.created_by)
        call.update_status(self.call_status, self.request.POST.get('CallDuration', None))
        return call.save()

    def start_flow_run(self):
        flow = self.get_call_flow()
        contact = self.get_or_create_contact()
        call = self.create_incoming_ivr()

        FlowRun.create(flow, contact.pk, call=call)
        response = Flow.handle_call(call, {})
        return HttpResponse(unicode(response))

    def set_missed_call(self):
        channel = self.channel
        # we don't have an inbound trigger to deal with this call.
        response = twiml.Response()

        # say nothing and hangup, this is a little rude, but if we reject the call, then
        # they'll get a non-working number error. We send 'busy' when our server is down
        # so we don't want to use that here either.
        response.say('')
        response.hangup()

        contact = self.get_or_create_contact()

        # if they have a missed call trigger, fire that off
        Trigger.catch_triggers(contact, Trigger.TYPE_MISSED_CALL, channel)

        # either way, we need to hangup now
        return HttpResponse(unicode(response))

    def create_incoming_msg(self, client, urn):
        channel = self.channel

        # process any attached media
        for i in range(int(self.media)):
            media_url = client.download_media(self.request.POST['MediaUrl%d' % i])
            path = media_url.partition(':')[2]
            Msg.create_incoming(channel, urn, path, media=media_url)

        if self.body:
            Msg.create_incoming(channel, urn, self.body)

    def get_sms(self):
        return Msg.all_messages.select_related('channel').get(id=self.sms_id)

    def send_sms_status(self):
        sms = self.get_sms()

        if self.sms_status == 'sent':
            sms.status_sent()
        elif self.sms_status == 'delivered':
            sms.status_delivered()
        elif self.sms_status == 'failed':
            sms.fail()

    def execute(self, client):
        channel = self.channel

        to_number = self.normalize_urn()

        if not client:
            raise ValidationError("Invalid client")

        if not channel:
            raise ValidationError("Channel not found")

        # see if it's a twilio call being initiated
        if self.check_is_inbound_call():

            answer_channel = self.check_answer_channel(type=channel.channel_type)

            if not answer_channel:
                raise Exception("No active answering channel found for number: %s" % to_number)

            if not client:
                client = channel.org.get_twiml_client()

            if self.check_validator(client):

                flow = self.get_call_flow()

                if flow:
                    self.start_flow_run()
                else:
                    self.set_missed_call()

        action = self.action

        # this is a callback for a message we sent
        if action == 'callback':

            if not self.check_validator(client):
                # raise an exception that things weren't properly signed
                raise ValidationError("Invalid request signature")

            # queued, sending, sent, failed, or received.
            self.send_sms_status()

            return HttpResponse("", status=200)

        elif action == 'received':
            if not channel:
                return HttpResponse("Channel not found.", status=404)

            if not self.check_validator(client):
                # raise an exception that things weren't properly signed
                raise ValidationError("Invalid request signature")

            urn = self.get_number_normalized()

            # process any attached media
            self.create_incoming_msg(client=client, urn=urn)

            return HttpResponse("", status=201)

        return HttpResponse("Not Handled, unknown action", status=400)
