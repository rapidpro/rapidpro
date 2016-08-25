# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import json
import pytz
import requests
import xml.etree.ElementTree as ET

from datetime import datetime
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.temp import NamedTemporaryFile
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.generic import View
from temba.api.models import WebHookEvent, SMS_RECEIVED
from temba.channels.models import Channel, PLIVO, SHAQODOON, YO, TWILIO_MESSAGING_SERVICE, AUTH_TOKEN, TELEGRAM, TWIML_API, TWILIO
from temba.channels.utils import TwilioPostHandler
from temba.contacts.models import Contact, URN
from temba.orgs.models import NEXMO_UUID
from temba.msgs.models import Msg, HANDLE_EVENT_TASK, HANDLER_QUEUE, MSG_EVENT
from temba.triggers.models import Trigger
from temba.utils import json_date_to_datetime
from temba.utils.middleware import disable_middleware
from temba.utils.queues import push_task
from .tasks import fb_channel_subscribe


class TwilioHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(TwilioHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return HttpResponse("ILLEGAL METHOD")

    def post(self, request, *args, **kwargs):
        twilio_post = TwilioPostHandler(request)
        channel = twilio_post.set_channel(type=TWILIO)
        client = channel.org.get_twilio_client()
        return twilio_post.execute(client=client)


class TwilioMessagingServiceHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(TwilioMessagingServiceHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from twilio.util import RequestValidator
        from temba.msgs.models import Msg

        signature = request.META.get('HTTP_X_TWILIO_SIGNATURE', '')
        url = "https://" + settings.HOSTNAME + "%s" % request.get_full_path()

        action = kwargs['action']
        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=TWILIO_MESSAGING_SERVICE).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        if action == 'receive':

            org = channel.org
            client = org.get_twilio_client()
            validator = RequestValidator(client.auth[1])

            if not validator.validate(url, request.POST, signature):
                # raise an exception that things weren't properly signed
                raise ValidationError("Invalid request signature")

            Msg.create_incoming(channel, URN.from_tel(request.POST['From']), request.POST['Body'])

            return HttpResponse("", status=201)

        return HttpResponse("Not Handled, unknown action", status=400)


class TwimlAPIHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(TwimlAPIHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        twilio_post = TwilioPostHandler(request)
        channel = twilio_post.set_channel(type=TWIML_API, channel_uuid=kwargs['uuid'])
        client = channel.org.get_twiml_client()
        return twilio_post.execute(client=client)


class AfricasTalkingHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(AfricasTalkingHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return HttpResponse("ILLEGAL METHOD", status=400)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg
        from temba.channels.models import AFRICAS_TALKING

        action = kwargs['action']
        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=AFRICAS_TALKING).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        # this is a callback for a message we sent
        if action == 'delivery':
            if 'status' not in request.POST or 'id' not in request.POST:
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

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'callback':
            if 'from' not in request.POST or 'text' not in request.POST:
                return HttpResponse("Missing from or text parameters", status=400)

            sms = Msg.create_incoming(channel, URN.from_tel(request.POST['from']), request.POST['text'])

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
        from temba.msgs.models import Msg
        from temba.channels.models import ZENVIA

        request.encoding = "ISO-8859-1"

        action = kwargs['action']
        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=ZENVIA).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        # this is a callback for a message we sent
        if action == 'status':
            if 'status' not in request.REQUEST or 'id' not in request.REQUEST:
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

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            import pytz

            if 'date' not in request.REQUEST or 'from' not in request.REQUEST or 'msg' not in request.REQUEST:
                return HttpResponse("Missing parameters, requires 'from', 'date' and 'msg'", status=400)

            # dates come in the format 31/07/2013 14:45:00
            sms_date = datetime.strptime(request.REQUEST['date'], "%d/%m/%Y %H:%M:%S")
            brazil_date = pytz.timezone('America/Sao_Paulo').localize(sms_date)

            urn = URN.from_tel(request.REQUEST['from'])
            sms = Msg.create_incoming(channel, urn, request.REQUEST['msg'], date=brazil_date)

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
        from temba.msgs.models import Msg

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
            if 'id' not in request.REQUEST:
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

            urn = URN.from_parts(channel.scheme, sender)
            sms = Msg.create_incoming(channel, urn, text, date=date)

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


class TelegramHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(TelegramHandler, self).dispatch(*args, **kwargs)

    @classmethod
    def download_file(cls, channel, file_id):
        """
        Fetches a file from Telegram's server based on their file id
        """
        auth_token = channel.config_json()[AUTH_TOKEN]
        url = 'https://api.telegram.org/bot%s/getFile' % auth_token
        response = requests.post(url, {'file_id': file_id})

        if response.status_code == 200:
            if json:
                response_json = response.json()
                if response_json['ok']:
                    url = 'https://api.telegram.org/file/bot%s/%s' % (auth_token, response_json['result']['file_path'])
                    extension = url.rpartition('.')[2]
                    response = requests.get(url)
                    content_type = response.headers['Content-Type']

                    temp = NamedTemporaryFile(delete=True)
                    temp.write(response.content)
                    temp.flush()

                    return '%s:%s' % (content_type, channel.org.save_media(File(temp), extension))

    def post(self, request, *args, **kwargs):
        channel_uuid = kwargs['uuid']
        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=TELEGRAM).exclude(org=None).first()

        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        body = json.loads(request.body)

        # look up the contact
        telegram_id = str(body['message']['from']['id'])
        urn = URN.from_telegram(telegram_id)
        existing_contact = Contact.from_urn(channel.org, urn)

        # if the contact doesn't exist, try to create one
        if not existing_contact and not channel.org.is_anon:
            # "from": {
            # "id": 25028612,
            # "first_name": "Eric",
            # "last_name": "Newcomer",
            # "username": "ericn" }
            name = " ".join((body['message']['from'].get('first_name', ''), body['message']['from'].get('last_name', '')))
            name = name.strip()

            username = body['message']['from'].get('username', '')
            if not name and username:
                name = username

            if name:
                Contact.get_or_create(channel.org, channel.created_by, name, urns=[urn])

        msg_date = datetime.utcfromtimestamp(body['message']['date']).replace(tzinfo=pytz.utc)

        def create_media_message(file_id):
            media_url = TelegramHandler.download_file(channel, file_id)
            url = media_url.partition(':')[2]
            msg = Msg.create_incoming(channel, urn, url, date=msg_date, media=media_url)
            return HttpResponse("Message Accepted: %d" % msg.id)

        if 'sticker' in body['message']:
            return create_media_message(body['message']['sticker']['file_id'])

        if 'video' in body['message']:
            return create_media_message(body['message']['video']['file_id'])

        if 'voice' in body['message']:
            return create_media_message(body['message']['voice']['file_id'])

        if 'document' in body['message']:
            return create_media_message(body['message']['document']['file_id'])

        if 'location' in body['message']:
            location = body['message']['location']
            location = '%s,%s' % (location['latitude'], location['longitude'])

            msg_text = location
            if 'venue' in body['message']:
                if 'title' in body['message']['venue']:
                    msg_text = '%s (%s)' % (msg_text, body['message']['venue']['title'])
            media_url = 'geo:%s' % location
            msg = Msg.create_incoming(channel, urn, msg_text, date=msg_date, media=media_url)
            return HttpResponse("Message Accepted: %d" % msg.id)

        if 'photo' in body['message']:
            photos = body['message']['photo']
            if len(photos):
                # grab the last (largest) photo in the list
                return create_media_message(photos[-1:][0]['file_id'])

        if 'contact' in body['message']:
            contact = body['message']['contact']

            if 'first_name' in contact and 'phone_number' in contact:
                body['message']['text'] = '%(first_name)s (%(phone_number)s)' % contact

            elif 'first_name' in contact:
                body['message']['text'] = '%(first_name)s' % contact

            elif 'phone_number' in contact:
                body['message']['text'] = '%(phone_number)s' % contact

        # skip if there is no message block (could be a sticker or voice)
        if 'text' in body['message']:
            msg = Msg.create_incoming(channel, urn, body['message']['text'], date=msg_date)
            return HttpResponse("Message Accepted: %d" % msg.id)

        return HttpResponse("No message, ignored.")


class InfobipHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(InfobipHandler, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg
        from temba.channels.models import INFOBIP

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

        return HttpResponse("SMS Status Updated")

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg
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

        sms = Msg.create_incoming(channel, URN.from_tel(request.REQUEST['sender']), request.REQUEST['text'])

        return HttpResponse("SMS Accepted: %d" % sms.id)


class Hub9Handler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(Hub9Handler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg
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

            return HttpResponse("000")

        # An MO message
        if action == 'received':
            # make sure the channel number matches the receiver
            if channel.address != '+' + to_number:
                return HttpResponse("Channel with number '%s' not found." % to_number, status=404)

            Msg.create_incoming(channel, URN.from_tel('+' + from_number), message)
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

            msg = Msg.create_incoming(channel, URN.from_tel(from_number), message, date=received)
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

            return HttpResponse("")

        # An MO message
        elif action == 'receive':
            to_number = request.REQUEST.get('to', None)
            from_number = request.REQUEST.get('from', None)
            message = request.REQUEST.get('text', None)
            # smsc = request.REQUEST.get('smsc', None)

            if to_number is None or from_number is None or message is None:
                return HttpResponse("Missing to, from or text parameters", status=400)

            if channel.address != to_number:
                return HttpResponse("Invalid to number [%s], expecting [%s]" % (to_number, channel.address), status=400)

            Msg.create_incoming(channel, URN.from_tel(from_number), message)
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

            Msg.create_incoming(channel, URN.from_tel(from_number), message)
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
        from temba.msgs.models import Msg
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

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            urn = URN.from_tel('+%s' % request.REQUEST['msisdn'])
            sms = Msg.create_incoming(channel, urn, request.REQUEST['text'])
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
        from temba.msgs.models import Msg, PENDING, QUEUED, WIRED, SENT
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
            if 'event_type' not in body and 'user_message_id' not in body:
                return HttpResponse("Missing event_type or user_message_id, ignoring message", status=400)

            external_id = body['user_message_id']
            status = body['event_type']

            # look up the message
            sms = Msg.current_messages.filter(channel=channel, external_id=external_id).select_related('channel')

            if not sms:
                return HttpResponse("Message with external id of '%s' not found" % external_id, status=404)

            if status not in ('ack', 'delivery_report'):
                return HttpResponse("Unknown status '%s', ignoring", status=200)

            # only update to SENT status if still in WIRED state
            if status == 'ack':
                sms.filter(status__in=[PENDING, QUEUED, WIRED]).update(status=SENT)
            elif status == 'delivery_report':
                sms = sms.first()
                if sms:
                    delivery_status = body.get('delivery_status', 'success')
                    if delivery_status == 'failed':
                        # Vumi and M-Tech disagree on what 'failed' means in a DLR, so for now, ignore these
                        # cases.
                        #
                        # we can get multiple reports from vumi if they multi-part the message for us
                        # if sms.status in (WIRED, DELIVERED):
                        #    print "!! [%d] marking %s message as error" % (sms.pk, sms.get_status_display())
                        #    Msg.mark_error(get_redis_connection(), channel, sms)
                        pass
                    else:

                        # we should only mark it as delivered if it's in a wired state, we want to hold on to our
                        # delivery failures if any part of the message comes back as failed
                        if sms.status == WIRED:
                            sms.status_delivered()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            if 'timestamp' not in body or 'from_addr' not in body or 'content' not in body or 'message_id' not in body:
                return HttpResponse("Missing one of timestamp, from_addr, content or message_id, ignoring message", status=400)

            # dates come in the format "2014-04-18 03:54:20.570618" GMT
            sms_date = datetime.strptime(body['timestamp'], "%Y-%m-%d %H:%M:%S.%f")
            gmt_date = pytz.timezone('GMT').localize(sms_date)

            sms = Msg.create_incoming(channel, URN.from_tel(body['from_addr']), body['content'], date=gmt_date)

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

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            if not all(k in request.REQUEST for k in ['message', 'sender', 'ts', 'id']):
                return HttpResponse("Missing one of 'message', 'sender', 'id' or 'ts' in request parameters.", status=400)

            # dates come in the format of a timestamp
            sms_date = datetime.utcfromtimestamp(int(request.REQUEST['ts']))
            gmt_date = pytz.timezone('GMT').localize(sms_date)

            urn = URN.from_tel(request.REQUEST['sender'])
            sms = Msg.create_incoming(channel, urn, request.REQUEST['message'], date=gmt_date)

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
            charset = request.REQUEST.get('charset', 'utf-8')

            # clickatell will sometimes send us UTF-16BE encoded data which is double encoded, we need to turn
            # this into utf-8 through the insane process below, Python is retarded about encodings
            if charset == 'UTF-16BE':
                text_bytes = bytearray()
                for text_byte in text:
                    text_bytes.append(ord(text_byte))

                # now encode back into utf-8
                text = text_bytes.decode('utf-16be').encode('utf-8')
            elif charset == 'ISO-8859-1':
                text = text.encode('iso-8859-1', 'ignore').decode('iso-8859-1').encode('utf-8')

            sms = Msg.create_incoming(channel, URN.from_tel(request.REQUEST['from']), text, date=gmt_date)

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

            if 'Status' not in request.REQUEST:
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
            if 'Text' not in request.REQUEST:
                return HttpResponse("Missing 'Text' in request parameters.", status=400)

            plivo_channel_address = request.REQUEST['To']

            if not channel:
                return HttpResponse("Channel not found for number: %s" % plivo_channel_address, status=400)

            channel_address = plivo_channel_address
            if channel_address[0] != '+':
                channel_address = '+' + channel_address

            if channel.address != channel_address:
                return HttpResponse("Channel not found for number: %s" % plivo_channel_address, status=400)

            sms = Msg.create_incoming(channel, URN.from_tel(request.REQUEST['From']), request.REQUEST['Text'])

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
        from temba.msgs.models import Msg
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
        except ET.ParseError:
            message = None

        service = message.find('service') if message is not None else None
        external_id = service.get('request_id') if service is not None else None
        sender_el = message.find('from') if message is not None else None
        text_el = message.find('body') if message is not None else None

        # validate all the appropriate fields are there
        if external_id is None or sender_el is None or text_el is None:
            return HttpResponse("Missing parameters, must have 'request_id', 'to' and 'body'", status=400)

        text = text_el.text
        if text is None:
            text = ""

        Msg.create_incoming(channel, URN.from_tel(sender_el.text), text)

        # Start expects an XML response
        xml_response = """<answer type="async"><state>Accepted</state></answer>"""
        return HttpResponse(xml_response)


class ChikkaHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(ChikkaHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, WIRED, PENDING, QUEUED
        from temba.channels.models import CHIKKA

        request_uuid = kwargs['uuid']
        action = request.REQUEST['message_type'].lower()

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type=CHIKKA).exclude(org=None).first()
        if not channel:
            return HttpResponse("Error, channel not found for id: %s" % request_uuid, status=400)

        # if this is the status of an outgoing message
        if action == 'outgoing':
            if not all(k in request.REQUEST for k in ['message_id', 'status']):
                return HttpResponse("Error, missing one of 'message_id' or 'status' in request parameters.", status=400)

            sms_id = self.request.REQUEST['message_id']

            # look up the message
            sms = Msg.current_messages.filter(channel=channel, id=sms_id).select_related('channel')
            if not sms:
                return HttpResponse("Error, message with external id of '%s' not found" % sms_id, status=400)

            # possible status codes Chikka will send us
            status_choices = {'SENT': SENT, 'FAILED': FAILED}

            # check our status
            status_code = self.request.REQUEST['status']
            status = status_choices.get(status_code, None)

            # we don't recognize this status code
            if not status:
                return HttpResponse("Error, unrecognized status: '%s', ignoring message." % status_code, status=400)

            # only update to SENT status if still in WIRED state
            if status == SENT:
                for sms_obj in sms.filter(status__in=[PENDING, QUEUED, WIRED]):
                    sms_obj.status_sent()
            elif status == FAILED:
                for sms_obj in sms:
                    sms_obj.fail()

            return HttpResponse("Accepted. SMS Status Updated")

        # this is a new incoming message
        elif action == 'incoming':
            if not all(k in request.REQUEST for k in ['mobile_number', 'request_id', 'message', 'timestamp']):
                return HttpResponse("Error, missing one of 'mobile_number', 'request_id', "
                                    "'message' or 'timestamp' in request parameters.", status=400)

            # dates come as timestamps
            sms_date = datetime.utcfromtimestamp(float(request.REQUEST['timestamp']))
            gmt_date = pytz.timezone('GMT').localize(sms_date)

            urn = URN.from_tel(request.REQUEST['mobile_number'])
            sms = Msg.create_incoming(channel, urn, request.REQUEST['message'], date=gmt_date)

            # save our request id in case of replies
            Msg.all_messages.filter(pk=sms.id).update(external_id=request.REQUEST['request_id'])
            return HttpResponse("Accepted: %d" % sms.id)

        else:
            return HttpResponse("Error, unknown message type", status=400)


class JasminHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(JasminHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return HttpResponse("Must be called as a POST", status=400)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg
        from temba.channels.models import JASMIN
        from temba.utils import gsm7

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type=JASMIN).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=400)

        # Jasmin is updating the delivery status for a message
        if action == 'status':
            if not all(k in request.POST for k in ['id', 'dlvrd', 'err']):
                return HttpResponse("Missing one of 'id' or 'dlvrd' or 'err' in request parameters.", status=400)

            sms_id = request.POST['id']
            dlvrd = request.POST['dlvrd']
            err = request.POST['err']

            # look up the message
            sms = Msg.current_messages.filter(channel=channel, external_id=sms_id).select_related('channel')
            if not sms:
                return HttpResponse("Message with external id of '%s' not found" % sms_id, status=400)

            if dlvrd == '1':
                for sms_obj in sms:
                    sms_obj.status_delivered()
            elif err == '1':
                for sms_obj in sms:
                    sms_obj.fail()

            # tell Jasmin we handled this
            return HttpResponse('ACK/Jasmin')

        # this is a new incoming message
        elif action == 'receive':
            if not all(k in request.POST for k in ['content', 'coding', 'from', 'to', 'id']):
                return HttpResponse("Missing one of 'content', 'coding', 'from', 'to' or 'id' in request parameters.",
                                    status=400)

            # if we are GSM7 coded, decode it
            content = request.POST['content']
            if request.POST['coding'] == '0':
                content = gsm7.decode(request.POST['content'], 'replace')[0]

            sms = Msg.create_incoming(channel, URN.from_tel(request.POST['from']), content)
            Msg.all_messages.filter(pk=sms.id).update(external_id=request.POST['id'])
            return HttpResponse('ACK/Jasmin')

        else:
            return HttpResponse("Not handled, unknown action", status=400)


class MbloxHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(MbloxHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return HttpResponse("Must be called as a POST", status=400)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg
        from temba.channels.models import MBLOX

        request_uuid = kwargs['uuid']

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True,
                                         channel_type=MBLOX).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=400)

        # parse our response
        try:
            body = json.loads(request.body)
        except Exception as e:
            return HttpResponse("Invalid JSON in POST body: %s" % str(e), status=400)

        if 'type' not in body:
            return HttpResponse("Missing 'type' in request body.", status=400)

        # two possible actions we care about: mo_text and recipient_deliveryreport_sms
        if body['type'] == 'recipient_delivery_report_sms':
            if not all(k in body for k in ['batch_id', 'status']):
                return HttpResponse("Missing one of 'batch_id' or 'status' in request body.", status=400)

            msg_id = body['batch_id']
            status = body['status']

            # look up the message
            msgs = Msg.current_messages.filter(channel=channel, external_id=msg_id).select_related('channel')
            if not msgs:
                return HttpResponse("Message with external id of '%s' not found" % msg_id, status=400)

            if status == 'Delivered':
                for msg in msgs:
                    msg.status_delivered()
            if status == 'Dispatched':
                for msg in msgs:
                    msg.status_sent()
            elif status in ['Aborted', 'Rejected', 'Failed', 'Expired']:
                for msg in msgs:
                    msg.fail()

            # tell Mblox we've handled this
            return HttpResponse('SMS Updated: %s' % ",".join([str(msg.id) for msg in msgs]))

        # this is a new incoming message
        elif body['type'] == 'mo_text':
            if not all(k in body for k in ['id', 'from', 'to', 'body', 'received_at']):
                return HttpResponse("Missing one of 'id', 'from', 'to', 'body' or 'received_at' in request body.",
                                    status=400)

            msg_date = parse_datetime(body['received_at'])
            msg = Msg.create_incoming(channel, URN.from_tel(body['from']), body['body'], date=msg_date)
            Msg.all_messages.filter(pk=msg.id).update(external_id=body['id'])
            return HttpResponse("SMS Accepted: %d" % msg.id)

        else:
            return HttpResponse("Not handled, unknown type: %s" % body['type'], status=400)


class FacebookHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(FacebookHandler, self).dispatch(*args, **kwargs)

    def lookup_channel(self, kwargs):
        from temba.channels.models import FACEBOOK

        # look up the channel
        channel = Channel.objects.filter(uuid=kwargs['uuid'], is_active=True,
                                         channel_type=FACEBOOK).exclude(org=None).first()
        return channel

    def get(self, request, *args, **kwargs):
        channel = self.lookup_channel(kwargs)
        if not channel:
            return HttpResponse("Channel not found for id: %s" % kwargs['uuid'], status=400)

        # this is a verification of a webhook
        if request.GET.get('hub.mode') == 'subscribe':
            # verify the token against our secret, if the same return the challenge FB sent us
            if channel.secret == request.GET.get('hub.verify_token'):
                # fire off a subscription for facebook events, we have a bit of a delay here so that FB can react to this webhook result
                fb_channel_subscribe.apply_async([channel.id], delay=5)

                return HttpResponse(request.GET.get('hub.challenge'))

        return JsonResponse(dict(error="Unknown request"), status=400)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        channel = self.lookup_channel(kwargs)
        if not channel:
            return HttpResponse("Channel not found for id: %s" % kwargs['uuid'], status=400)

        # parse our response
        try:
            body = json.loads(request.body)
        except Exception as e:
            return HttpResponse("Invalid JSON in POST body: %s" % str(e), status=400)

        if 'entry' not in body:
            return HttpResponse("Missing entry array", status=400)

        # iterate through our entries, handling them
        for entry in body.get('entry'):
            # this is a messaging notification
            if 'messaging' in entry:
                status = []

                for envelope in entry['messaging']:
                    if 'message' in envelope or 'postback' in envelope:
                        # ignore echos
                        if 'message' in envelope and envelope['message'].get('is_echo'):
                            status.append("Echo Ignored")
                            continue

                        # check that the recipient is correct for this channel
                        channel_address = str(envelope['recipient']['id'])
                        if channel_address != channel.address:
                            return HttpResponse("Msg Ignored for recipient id: %s" % channel.address, status=200)

                        content = None
                        postback = None

                        if 'message' in envelope:
                            if 'text' in envelope['message']:
                                content = envelope['message']['text']
                            elif 'attachments' in envelope['message']:
                                urls = []
                                for attachment in envelope['message']['attachments']:
                                    if attachment['payload'] and 'url' in attachment['payload']:
                                        urls.append(attachment['payload']['url'])
                                    elif 'url' in attachment:
                                        if 'title' in attachment:
                                            urls.append(attachment['title'])
                                        urls.append(attachment['url'])

                                content = '\n'.join(urls)

                        elif 'postback' in envelope:
                            postback = envelope['postback']['payload']

                        # if we have some content, load the contact
                        if content or postback:
                            # does this contact already exist?
                            sender_id = envelope['sender']['id']
                            urn = URN.from_facebook(sender_id)
                            contact = Contact.from_urn(channel.org, urn)

                            # if not, let's go create it
                            if not contact:
                                name = None

                                # if this isn't an anonymous org, look up their name from the Facebook API
                                if not channel.org.is_anon:
                                    try:
                                        response = requests.get('https://graph.facebook.com/v2.5/' + unicode(sender_id),
                                                                params=dict(fields='first_name,last_name',
                                                                            access_token=channel.config_json()[AUTH_TOKEN]))

                                        if response.status_code == 200:
                                            user_stats = response.json()
                                            name = ' '.join([user_stats.get('first_name', ''), user_stats.get('last_name', '')])

                                    except Exception as e:
                                        # something went wrong trying to look up the user's attributes, oh well, move on
                                        import traceback
                                        traceback.print_exc()

                                contact = Contact.get_or_create(channel.org, channel.created_by,
                                                                name=name, urns=[urn], channel=channel)

                        # we received a new message, create and handle it
                        if content:
                            msg_date = datetime.fromtimestamp(envelope['timestamp'] / 1000.0).replace(tzinfo=pytz.utc)
                            msg = Msg.create_incoming(channel, urn, content, date=msg_date, contact=contact)
                            Msg.all_messages.filter(pk=msg.id).update(external_id=envelope['message']['mid'])
                            status.append("Msg %d accepted." % msg.id)

                        # a contact pressed "Get Started", trigger any new conversation triggers
                        elif postback == Channel.GET_STARTED:
                            Trigger.catch_triggers(contact, Trigger.TYPE_NEW_CONVERSATION, channel)
                            status.append("Postback handled.")

                    elif 'delivery' in envelope and 'mids' in envelope['delivery']:
                        for external_id in envelope['delivery']['mids']:
                            msg = Msg.all_messages.filter(channel=channel, external_id=external_id).first()
                            if msg:
                                msg.status_delivered()
                                status.append("Msg %d updated." % msg.id)

                    else:
                        status.append("Messaging entry Ignored")

                return JsonResponse(dict(status=status))

        return JsonResponse(dict(status=["Ignored, unknown msg"]))
