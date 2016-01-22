from __future__ import absolute_import, unicode_literals

import json
import pytz
import xml.etree.ElementTree as ET

from datetime import datetime
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.generic import View
from redis_cache import get_redis_connection
from temba.api.models import WebHookEvent, SMS_RECEIVED
from temba.channels.models import Channel, PLIVO, SHAQODOON, YO
from temba.contacts.models import Contact, ContactURN, TEL_SCHEME
from temba.flows.models import Flow, FlowRun
from temba.orgs.models import NEXMO_UUID
from temba.msgs.models import Msg, HANDLE_EVENT_TASK, HANDLER_QUEUE, MSG_EVENT
from temba.triggers.models import Trigger
from temba.utils import JsonResponse, json_date_to_datetime
from temba.utils.middleware import disable_middleware
from temba.utils.queues import push_task
from twilio import twiml


class TwilioHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(TwilioHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return HttpResponse("ILLEGAL METHOD")

    def post(self, request, *args, **kwargs):
        from twilio.util import RequestValidator
        from temba.msgs.models import Msg, SENT, DELIVERED

        signature = request.META.get('HTTP_X_TWILIO_SIGNATURE', '')
        url = "https://" + settings.TEMBA_HOST + "%s" % request.get_full_path()

        call_sid = request.REQUEST.get('CallSid', None)
        direction = request.REQUEST.get('Direction', None)
        status = request.REQUEST.get('CallStatus', None)
        to_number = request.REQUEST.get('To', None)
        to_country = request.REQUEST.get('ToCountry', None)
        from_number = request.REQUEST.get('From', None)

        # Twilio sometimes sends un-normalized numbers
        if not to_number.startswith('+') and to_country:
            to_number, valid = ContactURN.normalize_number(to_number, to_country)

        # see if it's a twilio call being initiated
        if to_number and call_sid and direction == 'inbound' and status == 'ringing':

            # find a channel that knows how to answer twilio calls
            channel = Channel.objects.filter(address=to_number, channel_type='T', role__contains='A', is_active=True).exclude(org=None).first()
            if not channel:
                raise Exception("No active answering channel found for number: %s" % to_number)

            client = channel.org.get_twilio_client()
            validator = RequestValidator(client.auth[1])
            signature = request.META.get('HTTP_X_TWILIO_SIGNATURE', '')

            base_url = settings.TEMBA_HOST
            url = "https://%s%s" % (base_url, request.get_full_path())

            if validator.validate(url, request.POST, signature):
                from temba.ivr.models import IVRCall

                # find a contact for the one initiating us
                contact_urn = ContactURN.get_or_create(channel.org, TEL_SCHEME, from_number, channel)
                contact = Contact.get_or_create(channel.org, channel.created_by, urns=[(TEL_SCHEME, from_number)])
                flow = Trigger.find_flow_for_inbound_call(contact)

                call = IVRCall.create_incoming(channel, contact, contact_urn, flow, channel.created_by)
                call.update_status(request.POST.get('CallStatus', None),
                                   request.POST.get('CallDuration', None))
                call.save()

                if flow:
                    FlowRun.create(flow, contact, call=call)
                    response = Flow.handle_call(call, {})
                    return HttpResponse(unicode(response))
                else:

                    # we don't have an inbound trigger to deal with this call.
                    response = twiml.Response()

                    # say nothing and hangup, this is a little rude, but if we reject the call, then
                    # they'll get a non-working number error. We send 'busy' when our server is down
                    # so we don't want to use that here either.
                    response.say('')
                    response.hangup()

                    # if they have a missed call trigger, fire that off
                    Trigger.catch_triggers(contact, Trigger.TYPE_MISSED_CALL, channel)

                    # either way, we need to hangup now
                    return HttpResponse(unicode(response))

        action = request.GET.get('action', 'received')
        # this is a callback for a message we sent
        if action == 'callback':
            smsId = request.GET.get('id', None)
            status = request.POST.get('SmsStatus', None)

            # get the SMS
            sms = Msg.all_messages.select_related('channel').get(id=smsId)

            # validate this request is coming from twilio
            org = sms.org
            client = org.get_twilio_client()
            validator = RequestValidator(client.auth[1])

            if not validator.validate(url, request.POST, signature):
                # raise an exception that things weren't properly signed
                raise ValidationError("Invalid request signature")

            # queued, sending, sent, failed, or received.
            if status == 'sent':
                sms.status_sent()
            elif status == 'delivered':
                sms.status_delivered()
            elif status == 'failed':
                sms.fail()

            sms.broadcast.update()

            return HttpResponse("", status=200)

        # this is an incoming message that is being received by Twilio
        elif action == 'received':
            channel = Channel.objects.filter(address=to_number, is_active=True).exclude(org=None).first()
            if not channel:
                raise Exception("No active channel found for number: %s" % to_number)

            # validate this request is coming from twilio
            org = channel.org
            client = org.get_twilio_client()
            validator = RequestValidator(client.auth[1])

            if not validator.validate(url, request.POST, signature):
                # raise an exception that things weren't properly signed
                raise ValidationError("Invalid request signature")

            Msg.create_incoming(channel, (TEL_SCHEME, request.POST['From']), request.POST['Body'])

            return HttpResponse("", status=201)

        return HttpResponse("Not Handled, unknown action", status=400)


class AfricasTalkingHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(AfricasTalkingHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return HttpResponse("ILLEGAL METHOD", status=400)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED
        from temba.channels.models import AFRICAS_TALKING

        action = kwargs['action']
        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=AFRICAS_TALKING).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        # this is a callback for a message we sent
        if action == 'delivery':
            if not 'status' in request.POST or not 'id' in request.POST:
                return HttpResponse("Missing status or id parameters", status=400)

            status = request.POST['status']
            external_id = request.POST['id']

            # look up the message
            sms = Msg.current_messages.filter(channel=channel, external_id=external_id).select_related('channel').first()
            if not sms:
                return HttpResponse("No SMS message with id: %s" % external_id, status=404)

            if status == 'Success':
                sms.status_delivered()
            elif status == 'Sent' or status == 'Buffered':
                sms.status_sent()
            elif status == 'Rejected' or status == 'Failed':
                sms.fail()

            sms.broadcast.update()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'callback':
            if not 'from' in request.POST or not 'text' in request.POST:
                return HttpResponse("Missing from or text parameters", status=400)

            sms = Msg.create_incoming(channel, (TEL_SCHEME, request.POST['from']), request.POST['text'])

            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:
            return HttpResponse("Not handled", status=400)


class ZenviaHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(ZenviaHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED
        from temba.channels.models import ZENVIA

        request.encoding = "ISO-8859-1"

        action = kwargs['action']
        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=ZENVIA).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        # this is a callback for a message we sent
        if action == 'status':
            if not 'status' in request.REQUEST or not 'id' in request.REQUEST:
                return HttpResponse("Missing parameters, requires 'status' and 'id'", status=400)

            status = int(request.REQUEST['status'])
            sms_id = request.REQUEST['id']

            # look up the message
            sms = Msg.current_messages.filter(channel=channel, pk=sms_id).select_related('channel').first()
            if not sms:
                return HttpResponse("No SMS message with id: %s" % sms_id, status=404)

            # delivered
            if status == 120:
                sms.status_delivered()
            elif status == 111:
                sms.status_sent()
            else:
                sms.fail()

            # update our broadcast status
            sms.broadcast.update()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            import pytz

            if not 'date' in request.REQUEST or not 'from' in request.REQUEST or not 'msg' in request.REQUEST:
                return HttpResponse("Missing parameters, requires 'from', 'date' and 'msg'", status=400)

            # dates come in the format 31/07/2013 14:45:00
            sms_date = datetime.strptime(request.REQUEST['date'], "%d/%m/%Y %H:%M:%S")
            brazil_date = pytz.timezone('America/Sao_Paulo').localize(sms_date)

            sms = Msg.create_incoming(channel,
                                      (TEL_SCHEME, request.REQUEST['from']),
                                      request.REQUEST['msg'],
                                      date=brazil_date)

            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:
            return HttpResponse("Not handled", status=400)


class ExternalHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(ExternalHandler, self).dispatch(*args, **kwargs)

    def get_channel_type(self):
        from temba.channels.models import EXTERNAL
        return EXTERNAL

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED

        action = kwargs['action'].lower()

        # some external channels that have been added as bulk relayers had UUID set to their phone number
        uuid_or_address = kwargs['uuid']
        if len(uuid_or_address) == 36:
            channel_q = Q(uuid=uuid_or_address)
        else:
            channel_q = Q(address=uuid_or_address) | Q(address=('+' + uuid_or_address))

        channel = Channel.objects.filter(channel_q).filter(is_active=True, channel_type=self.get_channel_type()).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid or address %s not found." % uuid_or_address, status=400)

        # this is a callback for a message we sent
        if action == 'delivered' or action == 'failed' or action == 'sent':
            if not 'id' in request.REQUEST:
                return HttpResponse("Missing 'id' parameter, invalid call.", status=400)

            sms_pk = request.REQUEST['id']

            # look up the message
            sms = Msg.current_messages.filter(channel=channel, pk=sms_pk).select_related('channel').first()
            if not sms:
                return HttpResponse("No SMS message with id: %s" % sms_pk, status=400)

            if action == 'delivered':
                sms.status_delivered()
            elif action == 'sent':
                sms.status_sent()
            elif action == 'failed':
                sms.fail()

            sms.broadcast.update()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'received':
            sender = request.REQUEST.get('from', request.REQUEST.get('sender', None))
            if not sender:
                return HttpResponse("Missing 'from' or 'sender' parameter, invalid call.", status=400)

            text = request.REQUEST.get('text', request.REQUEST.get('message', None))
            if text is None:
                return HttpResponse("Missing 'text' or 'message' parameter, invalid call.", status=400)

            # handlers can optionally specify the date/time of the message (as 'date' or 'time') in ECMA format
            date = request.REQUEST.get('date', request.REQUEST.get('time', None))
            if date:
                date = json_date_to_datetime(date)

            sms = Msg.create_incoming(channel, (channel.scheme, sender), text, date=date)

            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:
            return HttpResponse("Not handled", status=400)


class ShaqodoonHandler(ExternalHandler):
    """
    Overloaded external channel for accepting Shaqodoon messages
    """
    def get_channel_type(self):
        return SHAQODOON


class YoHandler(ExternalHandler):
    """
    Overloaded external channel for accepting Yo! Messages.
    """
    def get_channel_type(self):
        return YO

class InfobipHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(InfobipHandler, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED
        from temba.channels.models import INFOBIP

        action = kwargs['action'].lower()
        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=INFOBIP).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        # parse our raw body, it should be XML that looks something like:
        # <DeliveryReport>
        #   <message id="254021015120766124"
        #    sentdate="2014/02/10 16:12:07"
        #    donedate="2014/02/10 16:13:00"
        #    status="DELIVERED"
        #    gsmerror="0"
        #    price="0.65" />
        # </DeliveryReport>
        root = ET.fromstring(request.body)

        message = root.find('message')
        external_id = message.get('id')
        status = message.get('status')

        # look up the message
        sms = Msg.current_messages.filter(channel=channel, external_id=external_id).select_related('channel').first()
        if not sms:
            return HttpResponse("No SMS message with external id: %s" % external_id, status=404)

        if status == 'DELIVERED':
            sms.status_delivered()
        elif status == 'SENT':
            sms.status_sent()
        elif status in ['NOT_SENT', 'NOT_ALLOWED', 'INVALID_DESTINATION_ADDRESS',
                        'INVALID_SOURCE_ADDRESS', 'ROUTE_NOT_AVAILABLE', 'NOT_ENOUGH_CREDITS',
                        'REJECTED', 'INVALID_MESSAGE_FORMAT']:
            sms.fail()

        if sms.broadcast:
            sms.broadcast.update()

        return HttpResponse("SMS Status Updated")

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED
        from temba.channels.models import INFOBIP

        action = kwargs['action'].lower()
        channel_uuid = kwargs['uuid']

        # validate all the appropriate fields are there
        if 'sender' not in request.REQUEST or 'text' not in request.REQUEST or 'receiver' not in request.REQUEST:
            return HttpResponse("Missing parameters, must have 'sender', 'text' and 'receiver'", status=400)

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=INFOBIP).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        # validate this is not a delivery report, those must be POSTs
        if action == 'delivered':
            return HttpResponse("Illegal method, delivery reports must be POSTs", status=401)

        # make sure the channel number matches the receiver
        if channel.address != '+' + request.REQUEST['receiver']:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        sms = Msg.create_incoming(channel, (TEL_SCHEME, request.REQUEST['sender']), request.REQUEST['text'])

        return HttpResponse("SMS Accepted: %d" % sms.id)


class Hub9Handler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(Hub9Handler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED
        from temba.channels.models import HUB9

        channel_uuid = kwargs['uuid']
        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=HUB9).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        # They send everythign as a simple GET
        # userid=testusr&password=test&original=555555555555&sendto=666666666666
        # &messageid=99123635&message=Test+sending+sms

        action = kwargs['action'].lower()
        message = request.REQUEST.get('message', None)
        external_id = request.REQUEST.get('messageid', None)
        status = int(request.REQUEST.get('status', -1))
        from_number = request.REQUEST.get('original', None)
        to_number = request.REQUEST.get('sendto', None)

        # delivery reports
        if action == 'delivered':
            # look up the message
            sms = Msg.current_messages.filter(channel=channel, pk=external_id).select_related('channel').first()
            if not sms:
                return HttpResponse("No SMS message with external id: %s" % external_id, status=404)

            if 10 <= status <= 12:
                sms.status_delivered()
            elif status > 20:
                sms.fail()
            elif status != -1:
                sms.status_sent()

            sms.broadcast.update()
            return HttpResponse("000")

        # An MO message
        if action == 'received':
            # make sure the channel number matches the receiver
            if channel.address != '+' + to_number:
                return HttpResponse("Channel with number '%s' not found." % to_number, status=404)

            from_number = '+' + from_number
            Msg.create_incoming(channel, (TEL_SCHEME, from_number), message)
            return HttpResponse("000")

        return HttpResponse("Unreconized action: %s" % action, status=404)


class HighConnectionHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(HighConnectionHandler, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg
        from temba.channels.models import HIGH_CONNECTION

        channel_uuid = kwargs['uuid']
        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=HIGH_CONNECTION).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=400)

        action = kwargs['action'].lower()

        # Update on the status of a sent message
        if action == 'status':
            msg_id = request.REQUEST.get('ret_id', None)
            status = int(request.REQUEST.get('status', 0))

            # look up the message
            sms = Msg.current_messages.filter(channel=channel, pk=msg_id).select_related('channel').first()
            if not sms:
                return HttpResponse("No SMS message with id: %s" % msg_id, status=400)

            if status == 4:
                sms.status_sent()
            elif status == 6:
                sms.status_delivered()
            elif status in [2, 11, 12, 13, 14, 15, 16]:
                sms.fail()

            sms.broadcast.update()
            return HttpResponse(json.dumps(dict(msg="Status Updated")))

        # An MO message
        elif action == 'receive':
            to_number = request.REQUEST.get('TO', None)
            from_number = request.REQUEST.get('FROM', None)
            message = request.REQUEST.get('MESSAGE', None)
            received = request.REQUEST.get('RECEPTION_DATE', None)

            # dateformat for reception date is 2015-04-02T14:26:06 in UTC
            if received is None:
                received = timezone.now()
            else:
                raw_date = datetime.strptime(received, "%Y-%m-%dT%H:%M:%S")
                received = raw_date.replace(tzinfo=pytz.utc)

            if to_number is None or from_number is None or message is None:
                return HttpResponse("Missing TO, FROM or MESSAGE parameters", status=400)

            msg = Msg.create_incoming(channel, (TEL_SCHEME, from_number), message, date=received)
            return HttpResponse(json.dumps(dict(msg="Msg received", id=msg.id)))

        return HttpResponse("Unrecognized action: %s" % action, status=400)


class BlackmynaHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(BlackmynaHandler, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg
        from temba.channels.models import BLACKMYNA

        channel_uuid = kwargs['uuid']
        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=BLACKMYNA).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=400)

        action = kwargs['action'].lower()

        # Update on the status of a sent message
        if action == 'status':
            msg_id = request.REQUEST.get('id', None)
            status = int(request.REQUEST.get('status', 0))

            # look up the message
            sms = Msg.current_messages.filter(channel=channel, external_id=msg_id).select_related('channel').first()
            if not sms:
                return HttpResponse("No SMS message with id: %s" % msg_id, status=400)

            if status == 8:
                sms.status_sent()
            elif status == 1:
                sms.status_delivered()
            elif status in [2, 16]:
                sms.fail()

            sms.broadcast.update()
            return HttpResponse("")

        # An MO message
        elif action == 'receive':
            to_number = request.REQUEST.get('to', None)
            from_number = request.REQUEST.get('from', None)
            message = request.REQUEST.get('text', None)
            smsc = request.REQUEST.get('smsc', None)

            if to_number is None or from_number is None or message is None:
                return HttpResponse("Missing to, from or text parameters", status=400)

            if channel.address != to_number:
                return HttpResponse("Invalid to number [%s], expecting [%s]" % (to_number, channel.address), status=400)

            msg = Msg.create_incoming(channel, (TEL_SCHEME, from_number), message)
            return HttpResponse("")

        return HttpResponse("Unrecognized action: %s" % action, status=400)


class SMSCentralHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(SMSCentralHandler, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg
        from temba.channels.models import SMSCENTRAL

        channel_uuid = kwargs['uuid']
        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=SMSCENTRAL).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=400)

        action = kwargs['action'].lower()

        # An MO message
        if action == 'receive':
            from_number = request.REQUEST.get('mobile', None)
            message = request.REQUEST.get('message', None)

            if from_number is None or message is None:
                return HttpResponse("Missing mobile or message parameters", status=400)

            Msg.create_incoming(channel, (TEL_SCHEME, from_number), message)
            return HttpResponse("")

        return HttpResponse("Unrecognized action: %s" % action, status=400)


class M3TechHandler(ExternalHandler):
    """
    Exposes our API for handling and receiving messages, same as external handlers.
    """
    def get_channel_type(self):
        from temba.channels.models import M3TECH
        return M3TECH


class NexmoHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(NexmoHandler, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED
        from temba.channels.models import NEXMO

        action = kwargs['action'].lower()

        # nexmo fires a test request at our URL with no arguments, return 200 so they take our URL as valid
        if (action == 'receive' and not request.REQUEST.get('to', None)) or (action == 'status' and not request.REQUEST.get('messageId', None)):
            return HttpResponse("No to parameter, ignoring")

        request_uuid = kwargs['uuid']

        # crazy enough, for nexmo 'to' is the channel number for both delivery reports and new messages
        channel_number = request.REQUEST['to']

        # look up the channel
        address_q = Q(address=channel_number) | Q(address=('+' + channel_number))
        channel = Channel.objects.filter(address_q).filter(is_active=True, channel_type=NEXMO).exclude(org=None).first()

        # make sure we got one, and that it matches the key for our org
        org_uuid = None
        if channel:
            org_uuid = channel.org.config_json().get(NEXMO_UUID, None)

        if not channel or org_uuid != request_uuid:
            return HttpResponse("Channel not found for number: %s" % channel_number, status=404)

        # this is a callback for a message we sent
        if action == 'status':
            external_id = request.REQUEST['messageId']

            # look up the message
            sms = Msg.current_messages.filter(channel=channel, external_id=external_id).select_related('channel').first()
            if not sms:
                return HttpResponse("No SMS message with external id: %s" % external_id, status=200)

            status = request.REQUEST['status']

            if status == 'delivered':
                sms.status_delivered()
            elif status == 'accepted' or status == 'buffered':
                sms.status_sent()
            elif status == 'expired' or status == 'failed':
                sms.fail()

            sms.broadcast.update()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            number = '+%s' % request.REQUEST['msisdn']
            sms = Msg.create_incoming(channel, (TEL_SCHEME, number), request.REQUEST['text'])
            sms.external_id = request.REQUEST['messageId']
            sms.save(update_fields=['external_id'])
            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:
            return HttpResponse("Not handled", status=400)

class VerboiceHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(VerboiceHandler, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        return HttpResponse("Illegal method, must be GET", status=405)

    def get(self, request, *args, **kwargs):

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        from temba.channels.models import VERBOICE
        channel = Channel.objects.filter(uuid__iexact=request_uuid, is_active=True, channel_type=VERBOICE).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=404)

        if action == 'status':

            to = self.request.REQUEST.get('From', None)
            call_sid = self.request.REQUEST.get('CallSid', None)
            call_status = self.request.REQUEST.get('CallStatus', None)

            if not to or not call_sid or not call_status:
                return HttpResponse("Missing From or CallSid or CallStatus, ignoring message", status=400)

            from temba.ivr.models import IVRCall
            call = IVRCall.objects.filter(external_id=call_sid).first()
            if call:
                call.update_status(call_status, None)
                call.save()
                return HttpResponse("Call Status Updated")

        return HttpResponse("Not handled", status=400)

class VumiHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(VumiHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return HttpResponse("Illegal method, must be POST", status=405)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, PENDING, QUEUED, WIRED, SENT, DELIVERED, FAILED, ERRORED
        from temba.channels.models import VUMI

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type=VUMI).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=404)

        # parse our JSON
        try:
            body = json.loads(request.body)
        except Exception as e:
            return HttpResponse("Invalid JSON: %s" % unicode(e), status=400)

        # this is a callback for a message we sent
        if action == 'event':
            if not 'event_type' in body and not 'user_message_id' in body:
                return HttpResponse("Missing event_type or user_message_id, ignoring message", status=400)

            external_id = body['user_message_id']
            status = body['event_type']

            # look up the message
            sms = Msg.current_messages.filter(channel=channel, external_id=external_id).select_related('channel')

            if not sms:
                return HttpResponse("Message with external id of '%s' not found" % external_id, status=404)

            if not status in ['ack', 'delivery_report']:
                return HttpResponse("Unknown status '%s', ignoring", status=200)

            # only update to SENT status if still in WIRED state
            if status == 'ack':
                sms.filter(status__in=[PENDING, QUEUED, WIRED]).update(status=SENT)
            elif status == 'delivery_report':
                sms = sms.first()
                if sms:
                    delivery_status = body.get('delivery_status', 'success')
                    if delivery_status == 'failed':

                        # we can get multiple reports from vumi if they multi-part the message for us
                        if sms.status in (WIRED, DELIVERED):
                            print "!! [%d] marking %s message as error" % (sms.pk, sms.get_status_display())
                            Msg.mark_error(get_redis_connection(), channel, sms)
                    else:

                        # we should only mark it as delivered if it's in a wired state, we want to hold on to our
                        # delivery failures if any part of the message comes back as failed
                        if sms.status == WIRED:
                            sms.status_delivered()

            # disabled for performance reasons
            # sms.first().broadcast.update()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            if not 'timestamp' in body or not 'from_addr' in body or not 'content' in body or not 'message_id' in body:
                return HttpResponse("Missing one of timestamp, from_addr, content or message_id, ignoring message", status=400)

            # dates come in the format "2014-04-18 03:54:20.570618" GMT
            sms_date = datetime.strptime(body['timestamp'], "%Y-%m-%d %H:%M:%S.%f")
            gmt_date = pytz.timezone('GMT').localize(sms_date)

            sms = Msg.create_incoming(channel,
                                      (TEL_SCHEME, body['from_addr']),
                                      body['content'],
                                      date=gmt_date)

            # use an update so there is no race with our handling
            Msg.all_messages.filter(pk=sms.id).update(external_id=body['message_id'])
            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:
            return HttpResponse("Not handled", status=400)

class KannelHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(KannelHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, DELIVERED, FAILED, WIRED, PENDING, QUEUED
        from temba.channels.models import KANNEL

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type=KANNEL).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=400)

        # kannel is telling us this message got delivered
        if action == 'status':
            if not all(k in request.REQUEST for k in ['id', 'status']):
                return HttpResponse("Missing one of 'id' or 'status' in request parameters.", status=400)

            sms_id = self.request.REQUEST['id']

            # look up the message
            sms = Msg.current_messages.filter(channel=channel, id=sms_id).select_related('channel')
            if not sms:
                return HttpResponse("Message with external id of '%s' not found" % sms_id, status=400)

            # possible status codes kannel will send us
            STATUS_CHOICES = {'1': DELIVERED,
                              '2': FAILED,
                              '4': SENT,
                              '8': SENT,
                              '16': FAILED}

            # check our status
            status_code = self.request.REQUEST['status']
            status = STATUS_CHOICES.get(status_code, None)

            # we don't recognize this status code
            if not status:
                return HttpResponse("Unrecognized status code: '%s', ignoring message." % status_code, status=401)

            # only update to SENT status if still in WIRED state
            if status == SENT:
                for sms_obj in sms.filter(status__in=[PENDING, QUEUED, WIRED]):
                    sms_obj.status_sent()
            elif status == DELIVERED:
                for sms_obj in sms:
                    sms_obj.status_delivered()
            elif status == FAILED:
                for sms_obj in sms:
                    sms_obj.fail()

            # disabled for performance reasons
            # sms.first().broadcast.update()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            if not all(k in request.REQUEST for k in ['message', 'sender', 'ts', 'id']):
                return HttpResponse("Missing one of 'message', 'sender', 'id' or 'ts' in request parameters.", status=400)

            # dates come in the format "2014-04-18 03:54:20.570618" GMT
            sms_date = datetime.utcfromtimestamp(int(request.REQUEST['ts']))
            gmt_date = pytz.timezone('GMT').localize(sms_date)

            sms = Msg.create_incoming(channel,
                                      (TEL_SCHEME, request.REQUEST['sender']),
                                      request.REQUEST['message'],
                                      date=gmt_date)

            Msg.all_messages.filter(pk=sms.id).update(external_id=request.REQUEST['id'])
            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:
            return HttpResponse("Not handled", status=400)


class ClickatellHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(ClickatellHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, DELIVERED, FAILED, WIRED, PENDING, QUEUED
        from temba.channels.models import CLICKATELL, API_ID

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type=CLICKATELL).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=400)

        # make sure the API id matches if it is included (pings from clickatell don't include them)
        if 'api_id' in self.request.REQUEST and channel.config_json()[API_ID] != self.request.REQUEST['api_id']:
            return HttpResponse("Invalid API id for message delivery: %s" % self.request.REQUEST['api_id'], status=400)

        # Clickatell is telling us a message status changed
        if action == 'status':
            if not all(k in request.REQUEST for k in ['apiMsgId', 'status']):
                # return 200 as clickatell pings our endpoint during configuration
                return HttpResponse("Missing one of 'apiMsgId' or 'status' in request parameters.", status=200)

            sms_id = self.request.REQUEST['apiMsgId']

            # look up the message
            sms = Msg.current_messages.filter(channel=channel, external_id=sms_id).select_related('channel')
            if not sms:
                return HttpResponse("Message with external id of '%s' not found" % sms_id, status=400)

            # possible status codes Clickatell will send us
            STATUS_CHOICES = {'001': FAILED,      # incorrect msg id
                              '002': WIRED,       # queued
                              '003': SENT,        # delivered to upstream gateway
                              '004': DELIVERED,   # received by handset
                              '005': FAILED,      # error in message
                              '006': FAILED,      # terminated by user
                              '007': FAILED,      # error delivering
                              '008': WIRED,       # msg received
                              '009': FAILED,      # error routing
                              '010': FAILED,      # expired
                              '011': WIRED,       # delayed but queued
                              '012': FAILED,      # out of credit
                              '014': FAILED}      # too long

            # check our status
            status_code = self.request.REQUEST['status']
            status = STATUS_CHOICES.get(status_code, None)

            # we don't recognize this status code
            if not status:
                return HttpResponse("Unrecognized status code: '%s', ignoring message." % status_code, status=401)

            # only update to SENT status if still in WIRED state
            if status == SENT:
                for sms_obj in sms.filter(status__in=[PENDING, QUEUED, WIRED]):
                    sms_obj.status_sent()
            elif status == DELIVERED:
                for sms_obj in sms:
                    sms_obj.status_delivered()
            elif status == FAILED:
                for sms_obj in sms:
                    sms_obj.fail()
                    Channel.track_status(sms_obj.channel, "Failed")
            else:
                # ignore wired, we are wired by default
                pass

            # update the broadcast status
            bcast = sms.first().broadcast
            if bcast:
                bcast.update()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            if not all(k in request.REQUEST for k in ['from', 'text', 'moMsgId', 'timestamp']):
                # return 200 as clickatell pings our endpoint during configuration
                return HttpResponse("Missing one of 'from', 'text', 'moMsgId' or 'timestamp' in request parameters.", status=200)

            # dates come in the format "2014-04-18 03:54:20" GMT+2
            sms_date = parse_datetime(request.REQUEST['timestamp'])

            # Posix makes this timezone name back-asswards:
            # http://stackoverflow.com/questions/4008960/pytz-and-etc-gmt-5
            gmt_date = pytz.timezone('Etc/GMT-2').localize(sms_date, is_dst=None)
            text = request.REQUEST['text']

            # clickatell will sometimes send us UTF-16BE encoded data which is double encoded, we need to turn
            # this into utf-8 through the insane process below, Python is retarded about encodings
            if request.REQUEST.get('charset', 'utf-8') == 'UTF-16BE':
                text_bytes = bytearray()
                for text_byte in text:
                    text_bytes.append(ord(text_byte))

                # now encode back into utf-8
                text = text_bytes.decode('utf-16be').encode('utf-8')

            sms = Msg.create_incoming(channel,
                                      (TEL_SCHEME, request.REQUEST['from']),
                                      text,
                                      date=gmt_date)

            Msg.all_messages.filter(pk=sms.id).update(external_id=request.REQUEST['moMsgId'])
            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:
            return HttpResponse("Not handled", status=400)


class PlivoHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(PlivoHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, DELIVERED, FAILED, WIRED, PENDING, QUEUED

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        if not all(k in request.REQUEST for k in ['From', 'To', 'MessageUUID']):
                return HttpResponse("Missing one of 'From', 'To', or 'MessageUUID' in request parameters.",
                                    status=400)

        channel = Channel.objects.filter(is_active=True, uuid=request_uuid, channel_type=PLIVO).first()

        if action == 'status':
            plivo_channel_address = request.REQUEST['From']

            if not 'Status' in request.REQUEST:
                return HttpResponse("Missing 'Status' in request parameters.", status=400)

            if not channel:
                return HttpResponse("Channel not found for number: %s" % plivo_channel_address, status=400)

            channel_address = plivo_channel_address
            if channel_address[0] != '+':
                channel_address = '+' + channel_address

            if channel.address != channel_address:
                return HttpResponse("Channel not found for number: %s" % plivo_channel_address, status=400)

            sms_id = request.REQUEST['MessageUUID']

            if 'ParentMessageUUID' in request.REQUEST:
                sms_id = request.REQUEST['ParentMessageUUID']

            # look up the message
            sms = Msg.current_messages.filter(channel=channel, external_id=sms_id).select_related('channel')
            if not sms:
                return HttpResponse("Message with external id of '%s' not found" % sms_id, status=400)

            STATUS_CHOICES = {'queued': WIRED,
                              'sent': SENT,
                              'delivered': DELIVERED,
                              'undelivered': SENT,
                              'rejected': FAILED}

            plivo_status = request.REQUEST['Status']
            status = STATUS_CHOICES.get(plivo_status, None)

            if not status:
                return HttpResponse("Unrecognized status: '%s', ignoring message." % plivo_status, status=401)

            # only update to SENT status if still in WIRED state
            if status == SENT:
                for sms_obj in sms.filter(status__in=[PENDING, QUEUED, WIRED]):
                    sms_obj.status_sent()
            elif status == DELIVERED:
                for sms_obj in sms:
                    sms_obj.status_delivered()
            elif status == FAILED:
                for sms_obj in sms:
                    sms_obj.fail()
                    Channel.track_status(sms_obj.channel, "Failed")
            else:
                # ignore wired, we are wired by default
                pass

            # update the broadcast status
            bcast = sms.first().broadcast
            if bcast:
                bcast.update()

            return HttpResponse("Status Updated")

        elif action == 'receive':
            if not 'Text' in request.REQUEST:
                return HttpResponse("Missing 'Text' in request parameters.", status=400)

            plivo_channel_address = request.REQUEST['To']

            if not channel:
                return HttpResponse("Channel not found for number: %s" % plivo_channel_address, status=400)

            channel_address = plivo_channel_address
            if channel_address[0] != '+':
                channel_address = '+' + channel_address

            if channel.address != channel_address:
                return HttpResponse("Channel not found for number: %s" % plivo_channel_address, status=400)

            sms = Msg.create_incoming(channel,
                                      (TEL_SCHEME, request.REQUEST['From']),
                                      request.REQUEST['Text'])

            Msg.all_messages.filter(pk=sms.id).update(external_id=request.REQUEST['MessageUUID'])

            return HttpResponse("SMS accepted: %d" % sms.id)
        else:
            return HttpResponse("Not handled", status=400)


class MageHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(MageHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return JsonResponse(dict(error="Illegal method, must be POST"), status=405)

    def post(self, request, *args, **kwargs):
        from temba.triggers.tasks import fire_follow_triggers

        authorization = request.META.get('HTTP_AUTHORIZATION', '').split(' ')

        if len(authorization) != 2 or authorization[0] != 'Token' or authorization[1] != settings.MAGE_AUTH_TOKEN:
            return JsonResponse(dict(error="Incorrect authentication token"), status=401)

        action = kwargs['action'].lower()
        new_contact = request.POST.get('new_contact', '').lower() in ('true', '1')

        if action == 'handle_message':
            try:
                msg_id = int(request.POST.get('message_id', ''))
            except ValueError:
                return JsonResponse(dict(error="Invalid message_id"), status=400)

            msg = Msg.all_messages.select_related('org').get(pk=msg_id)

            push_task(msg.org, HANDLER_QUEUE, HANDLE_EVENT_TASK,
                      dict(type=MSG_EVENT, id=msg.id, from_mage=True, new_contact=new_contact))

            # fire an event off for this message
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, msg, msg.created_on)
        elif action == 'follow_notification':
            try:
                channel_id = int(request.POST.get('channel_id', ''))
                contact_urn_id = int(request.POST.get('contact_urn_id', ''))
            except ValueError:
                return JsonResponse(dict(error="Invalid channel or contact URN id"), status=400)

            fire_follow_triggers.apply_async(args=(channel_id, contact_urn_id, new_contact), queue='handler')

        return JsonResponse(dict(error=None))


class StartHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(StartHandler, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED
        from temba.channels.models import START

        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=START).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=400)

        # Parse our raw body, it should be XML that looks something like:
        # <message>
        #   <service type="sms" timestamp="1450450974" auth="AAAFFF" request_id="15"/>
        #   <from>+12788123123</from>
        #   <to>1515</to>
        #   <body content-type="content-type" encoding="encoding">hello world</body>
        # </message>
        try:
            message = ET.fromstring(request.body)
        except ET.ParseError as e:
            message = None

        service = message.find('service') if message is not None else None
        external_id = service.get('request_id') if service is not None else None
        sender_el = message.find('from') if message is not None else None
        text_el = message.find('body') if message is not None else None

        # validate all the appropriate fields are there
        if external_id is None or sender_el is None or text_el is None:
            return HttpResponse("Missing parameters, must have 'request_id', 'to' and 'body'", status=400)

        Msg.create_incoming(channel, (TEL_SCHEME, sender_el.text), text_el.text)

        # Start expects an XML response
        xml_response = """<answer type="async"><state>Accepted</state></answer>"""
        return HttpResponse(xml_response)
