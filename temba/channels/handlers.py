# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import hmac
import hashlib
import iso8601
import json
import pytz
import requests
import six
import magic
import xml.etree.ElementTree as ET
import logging

from datetime import datetime
from django.conf import settings
from django.core.files import File
from django.core.files.temp import NamedTemporaryFile
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.encoding import force_text, force_bytes
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View
from django_redis import get_redis_connection
from requests import Request

from temba.api.models import WebHookEvent
from temba.channels.models import Channel, ChannelLog, ChannelEvent
from temba.contacts.models import Contact, URN
from temba.flows.models import Flow, FlowRun, FlowStep
from temba.orgs.models import NEXMO_UUID
from temba.msgs.models import Msg, HANDLE_EVENT_TASK, HANDLER_QUEUE, MSG_EVENT, OUTGOING
from temba.triggers.models import Trigger
from temba.ussd.models import USSDSession
from temba.utils import on_transaction_commit
from temba.utils.dates import json_date_to_datetime, ms_to_datetime
from temba.utils.queues import push_task
from temba.utils.http import HttpEvent
from temba.utils.jiochat import JiochatClient
from temba.utils.text import decode_base64
from temba.utils.twitter import generate_twitter_signature
from twilio import twiml
from .tasks import fb_channel_subscribe, refresh_jiochat_access_tokens

logger = logging.getLogger(__name__)


class BaseChannelHandler(View):
    """
    Base class for all channel handlers
    """

    # the url pattern for this handler on courier
    courier_url = None
    courier_name = None

    # the url pattern for this handler on rapidpro (legacy)
    handler_url = None
    handler_name = None

    @csrf_exempt
    def dispatch(self, request, *args, **kwargs):
        return super(BaseChannelHandler, self).dispatch(request, *args, **kwargs)

    @classmethod
    def get_courier_url(cls):
        return cls.courier_url, cls.courier_name

    @classmethod
    def get_handler_url(cls):
        return cls.handler_url, cls.handler_name

    def get_param(self, name, default=None):
        """
        Utility for handlers that were written to use request.REQUEST which was removed in Django 1.9
        """
        try:
            return self.request.GET[name]
        except KeyError:
            try:
                return self.request.POST[name]
            except KeyError:
                return default


def get_channel_handlers():
    """
    Gets all known channel handler classes, i.e. subclasses of BaseChannelHandler
    """
    def all_subclasses(cls):
        return cls.__subclasses__() + [g for s in cls.__subclasses__() for g in all_subclasses(s)]

    return all_subclasses(BaseChannelHandler)


class CourierHandler(BaseChannelHandler):
    channel_name = None

    def get(self, request, *args, **kwargs):  # pragma: no cover
        if self.__class__.channel_name is None:
            raise Exception("CourierHandler subclasses must specify handler name")
        return HttpResponse("%s handling only implemented in Courier" % self.__class__.channel_name, status_code=500)

    def post(self):  # pragma: no cover
        if self.__class__.channel_name is None:
            raise Exception("CourierHandler subclasses must specify handler name")
        return HttpResponse("%s handling only implemented in Courier" % self.__class__.channel_name, status_code=500)


class DMarkHandler(CourierHandler):
    channel_name = "DMark"
    courier_url = r'^dk/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$'
    courier_name = 'courier.dk'


class WhatsApp(CourierHandler):
    channel_name = "WhatsApp"
    courier_url = r'^wa/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$'
    courier_name = 'courier.wa'


class ClickatellHandler(CourierHandler):
    channel_name = "Clickatell"
    courier_url = r'^ct/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$'
    courier_name = 'courier.ct'


class ZenviaHandler(CourierHandler):
    channel_name = "Zenvia"
    courier_url = r'^zv/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$'
    courier_name = 'courier.zv'


class MtargetHandler(CourierHandler):
    channel_name = "Mtarget"
    courier_url = r'^mt/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$'
    courier_name = 'courier.mt'


class TwimlAPIHandler(BaseChannelHandler):

    courier_url = r'^tw/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$'
    courier_name = 'courier.tw'

    handler_url = r'^twiml_api/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.twiml_api_handler'

    def get(self, request, *args, **kwargs):  # pragma: no cover
        return HttpResponse("ILLEGAL METHOD")

    def post(self, request, *args, **kwargs):
        from twilio.util import RequestValidator
        from temba.flows.models import FlowSession
        from temba.msgs.models import Msg

        signature = request.META.get('HTTP_X_TWILIO_SIGNATURE', '')
        url = "https://" + request.get_host() + "%s" % request.get_full_path()

        channel_uuid = kwargs.get('uuid')
        call_sid = self.get_param('CallSid')
        direction = self.get_param('Direction')
        status = self.get_param('CallStatus')
        to_number = self.get_param('To')
        to_country = self.get_param('ToCountry')
        from_number = self.get_param('From')

        # Twilio sometimes sends un-normalized numbers
        if to_number and not to_number.startswith('+') and to_country:
            to_number, valid = URN.normalize_number(to_number, to_country)

        # see if it's a twilio call being initiated
        if to_number and call_sid and direction == 'inbound' and status == 'ringing':

            # find a channel that knows how to answer twilio calls
            channel = self.get_ringing_channel(uuid=channel_uuid)
            if not channel:
                response = twiml.Response()
                response.say('Sorry, there is no channel configured to take this call. Goodbye.')
                response.hangup()
                return HttpResponse(six.text_type(response))

            org = channel.org

            if self.get_channel_type() == 'T' and not org.is_connected_to_twilio():
                return HttpResponse("No Twilio account is connected", status=400)

            client = self.get_client(channel=channel)
            validator = RequestValidator(client.auth[1])
            signature = request.META.get('HTTP_X_TWILIO_SIGNATURE', '')

            url = "https://%s%s" % (request.get_host(), request.get_full_path())

            if validator.validate(url, request.POST, signature):
                from temba.ivr.models import IVRCall
                # find a contact for the one initiating us
                urn = URN.from_tel(from_number)
                contact, urn_obj = Contact.get_or_create(channel.org, urn, channel)

                flow = Trigger.find_flow_for_inbound_call(contact)

                if flow:
                    call = IVRCall.create_incoming(channel, contact, urn_obj, channel.created_by, call_sid)
                    session = FlowSession.create(contact, connection=call)

                    call.update_status(request.POST.get('CallStatus', None),
                                       request.POST.get('CallDuration', None),
                                       'T')
                    call.save()

                    FlowRun.create(flow, contact, session=session, connection=call)
                    response = Flow.handle_call(call)
                    return HttpResponse(six.text_type(response))

                else:

                    # we don't have an inbound trigger to deal with this call.
                    response = channel.generate_ivr_response()

                    # say nothing and hangup, this is a little rude, but if we reject the call, then
                    # they'll get a non-working number error. We send 'busy' when our server is down
                    # so we don't want to use that here either.
                    response.say('')
                    response.hangup()

                    # if they have a missed call trigger, fire that off
                    Trigger.catch_triggers(contact, Trigger.TYPE_MISSED_CALL, channel)

                    # either way, we need to hangup now
                    return HttpResponse(six.text_type(response))

        action = request.GET.get('action', 'received')

        # check for call progress events, these include post-call hangup notifications
        if request.POST.get('CallbackSource', None) == 'call-progress-events':
            if call_sid:
                from temba.ivr.models import IVRCall
                call = IVRCall.objects.filter(external_id=call_sid).first()
                if call:
                    call.update_status(request.POST.get('CallStatus', None), request.POST.get('CallDuration', None),
                                       'TW')
                    call.save()
                    return HttpResponse("Call status updated")
            return HttpResponse("No call found")

        # this is a callback for a message we sent
        elif action == 'callback':
            smsId = request.GET.get('id', None)
            status = request.POST.get('SmsStatus', None)

            # get the SMS
            sms = Msg.objects.select_related('channel').filter(id=smsId).first()
            if sms is None:
                return HttpResponse("No message found with id: %s" % smsId, status=400)

            # validate this request is coming from twilio
            org = sms.org

            if not org.is_connected_to_twilio():
                return HttpResponse("No Twilio account is connected", status=400)

            channel = sms.channel
            if not channel:  # pragma: needs cover
                channel = org.channels.filter(channel_type=self.get_channel_type()).first()

            client = self.get_client(channel=channel)
            validator = RequestValidator(client.auth[1])

            if not validator.validate(url, request.POST, signature):  # pragma: needs cover
                return HttpResponse("Invalid request signature.", status=400)

            # queued, sending, sent, failed, or received.
            if status == 'sent':
                sms.status_sent()
            elif status == 'delivered':
                sms.status_delivered()
            elif status == 'failed':
                sms.status_fail()

            return HttpResponse("", status=200)

        elif action == 'received':
            if not to_number:
                return HttpResponse("Must provide To number for received messages", status=400)

            channel = self.get_receive_channel(uuid=channel_uuid)
            if not channel:
                return HttpResponse("No active channel found for number: %s" % to_number, status=400)

            org = channel.org

            if self.get_channel_type() == 'T' and not org.is_connected_to_twilio():
                return HttpResponse("No Twilio account is connected", status=400)

            client = self.get_client(channel=channel)
            validator = RequestValidator(client.auth[1])

            if not validator.validate(url, request.POST, signature):
                return HttpResponse("Invalid request signature.", status=400)

            # Twilio sometimes sends concat sms as base64 encoded MMS
            body = decode_base64(request.POST['Body'])
            urn = URN.from_tel(request.POST['From'])
            attachments = []

            # download any attached media
            for i in range(int(request.POST.get('NumMedia', 0))):
                attachments.append(client.download_media(request.POST['MediaUrl%d' % i]))

            Msg.create_incoming(channel, urn, body, attachments=attachments)

            return HttpResponse("", status=201)

        return HttpResponse("Not Handled, unknown action", status=400)  # pragma: no cover

    def get_ringing_channel(self, uuid):
        return Channel.objects.filter(uuid=uuid, channel_type=self.get_channel_type(), role__contains='A', is_active=True).first()

    def get_receive_channel(self, uuid=None):
        return Channel.objects.filter(uuid=uuid, is_active=True, channel_type=self.get_channel_type()).first()

    def get_client(self, channel):
        return channel.get_ivr_client()

    def get_channel_type(self):
        return 'TW'


class TwilioHandler(TwimlAPIHandler):

    courier_url = r'^t/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status|voice)$'
    courier_name = 'courier.t'

    handler_url = r'^twilio/(?P<action>receive|status|voice)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.twilio_handler'

    def get_channel_type(self):
        return 'T'


class TwilioMessagingServiceHandler(BaseChannelHandler):

    courier_url = r'^tms/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$'
    courier_name = 'courier.tms'

    handler_url = r'^twilio_messaging_service/(?P<action>receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.twilio_messaging_service_handler'

    def get(self, request, *args, **kwargs):  # pragma: no cover
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from twilio.util import RequestValidator
        from temba.msgs.models import Msg

        signature = request.META.get('HTTP_X_TWILIO_SIGNATURE', '')
        url = "https://" + request.get_host() + "%s" % request.get_full_path()

        action = kwargs['action']
        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type='TMS').first()
        if not channel:  # pragma: needs cover
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        if action == 'receive':

            org = channel.org

            if not org.is_connected_to_twilio():
                return HttpResponse("No Twilio account is connected", status=400)

            client = org.get_twilio_client()
            validator = RequestValidator(client.auth[1])

            if not validator.validate(url, request.POST, signature):
                return HttpResponse("Invalid request signature.", status=400)

            Msg.create_incoming(channel, URN.from_tel(request.POST['From']), request.POST['Body'])

            return HttpResponse("", status=201)

        if action == 'status':
            smsId = request.GET.get('id', None)
            status = request.POST.get('SmsStatus', None)

            # get the SMS
            sms = Msg.objects.select_related('channel').filter(id=smsId).first()
            if sms is None:
                return HttpResponse("No message found with id: %s" % smsId, status=400)

            # validate this request is coming from twilio
            org = sms.org

            if not org.is_connected_to_twilio():
                return HttpResponse("No Twilio account is connected", status=400)

            channel = sms.channel
            if not channel:
                channel = org.channels.filter(channel_type=self.get_channel_type()).first()

            client = self.get_client(channel=channel)
            validator = RequestValidator(client.auth[1])

            if not validator.validate(url, request.POST, signature):  # pragma: needs cover
                return HttpResponse("Invalid request signature.", status=400)

            # queued, sending, sent, failed, or received.
            if status == 'sent':
                sms.status_sent()
            elif status == 'delivered':
                sms.status_delivered()
            elif status == 'failed':
                sms.status_fail()

            return HttpResponse("", status=200)

        return HttpResponse("Not Handled, unknown action", status=400)  # pragma: no cover

    def get_channel_type(self):
        return 'TMS'

    def get_client(self, channel):
        return channel.org.get_twilio_client()


class AfricasTalkingHandler(BaseChannelHandler):

    courier_url = r'^at/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|delivery|callback|status)$'
    courier_name = 'courier.at'

    handler_url = r'^africastalking/(?P<action>receive|delivery|callback|status)/(?P<uuid>[a-z0-9\-]+)/$'
    handler_name = 'handlers.africas_talking_handler'

    def get(self, request, *args, **kwargs):
        return HttpResponse("ILLEGAL METHOD", status=400)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        action = kwargs['action']
        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type='AT').first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        # this is a callback for a message we sent
        if action in ['delivery', 'status']:
            if 'status' not in request.POST or 'id' not in request.POST:
                return HttpResponse("Missing status or id parameters", status=400)

            r = get_redis_connection()

            status = request.POST['status']
            external_id = request.POST['id']

            # look up the message
            sms = Msg.objects.filter(channel=channel, external_id=external_id).select_related('channel').first()
            if not sms:
                return HttpResponse("No SMS message with id: %s" % external_id, status=404)

            if status == 'Success':
                sms.status_delivered()
            elif status == 'Sent' or status == 'Buffered':
                sms.status_sent()
            elif status == 'Rejected' or status == 'Failed':
                Msg.mark_error(r, channel, sms)

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action in ['receive', 'callback']:
            if 'from' not in request.POST or 'text' not in request.POST:
                return HttpResponse("Missing from or text parameters", status=400)

            sms = Msg.create_incoming(channel, URN.from_tel(request.POST['from']), request.POST['text'])

            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:  # pragma: no cover
            return HttpResponse("Not handled", status=400)


class ExternalHandler(BaseChannelHandler):

    courier_url = r'^ex/(?P<uuid>[a-z0-9\-]+)/(?P<action>sent|delivered|failed|received|receive)$'
    courier_name = 'courier.ex'

    handler_url = r'^external/(?P<action>sent|delivered|failed|received|receive)/(?P<uuid>[a-z0-9\-]+)/$'
    handler_name = 'handlers.external_handler'

    def get_channel_type(self):
        return 'EX'

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

        channel = Channel.objects.filter(channel_q).filter(is_active=True, channel_type=self.get_channel_type()).first()
        if not channel:
            return HttpResponse("Channel with uuid or address %s not found." % uuid_or_address, status=400)

        # this is a callback for a message we sent
        if action == 'delivered' or action == 'failed' or action == 'sent':
            sms_id = self.get_param('id')

            if sms_id is None:
                return HttpResponse("Missing 'id' parameter, invalid call.", status=400)

            # look up the message
            sms = Msg.objects.filter(channel=channel, direction=OUTGOING, id=sms_id).select_related('channel').first()
            if not sms:
                return HttpResponse("No outgoing message with id: %s" % sms_id, status=400)

            if action == 'delivered':
                sms.status_delivered()
            elif action == 'sent':
                sms.status_sent()
            elif action == 'failed':
                sms.status_fail()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action in ['received', 'receive']:
            sender = self.get_param('from', self.get_param('sender'))
            if not sender:
                return HttpResponse("Missing 'from' or 'sender' parameter, invalid call.", status=400)

            text = self.get_param('text', self.get_param('message'))
            if text is None:
                return HttpResponse("Missing 'text' or 'message' parameter, invalid call.", status=400)

            # handlers can optionally specify the date/time of the message (as 'date' or 'time') in ECMA format
            date = self.get_param('date', self.get_param('time'))
            if date:
                try:
                    date = json_date_to_datetime(date)
                except ValueError as e:
                    return HttpResponse("Bad parameter error: %s" % six.text_type(e), status=400)
            urn = URN.from_parts(channel.schemes[0], sender)
            sms = Msg.create_incoming(channel, urn, text, date=date)

            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:  # pragma: needs cover
            return HttpResponse("Not handled", status=400)


class ShaqodoonHandler(ExternalHandler):
    """
    Overloaded external channel for accepting Shaqodoon messages
    """
    courier_url = r'^sq/(?P<uuid>[a-z0-9\-]+)/(?P<action>sent|delivered|failed|received|receive)$'
    courier_name = 'courier.sq'

    handler_url = r'^shaqodoon/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/$'
    handler_name = 'handlers.shaqodoon_handler'

    def get_channel_type(self):
        return 'SQ'


class YoHandler(ExternalHandler):
    """
    Overloaded external channel for accepting Yo! Messages.
    """
    courier_url = r'^yo/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$'
    courier_name = 'courier.yo'

    handler_url = r'^yo/(?P<action>received)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.yo_handler'

    def get_channel_type(self):
        return 'YO'


class TelegramHandler(BaseChannelHandler):
    courier_url = r'^tg/(?P<uuid>[a-z0-9\-]+)/receive$'
    courier_name = 'courier.tg'

    handler_url = r'^telegram/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.telegram_handler'

    @classmethod
    def download_file(cls, channel, file_id):
        """
        Fetches a file from Telegram's server based on their file id
        """
        auth_token = channel.config[Channel.CONFIG_AUTH_TOKEN]
        url = 'https://api.telegram.org/bot%s/getFile' % auth_token
        response = requests.post(url, {'file_id': file_id})

        if response.status_code == 200:
            if json:
                response_json = response.json()
                if response_json['ok']:
                    url = 'https://api.telegram.org/file/bot%s/%s' % (auth_token, response_json['result']['file_path'])
                    extension = url.rpartition('.')[2]
                    response = requests.get(url)

                    # attempt to determine our content type using magic bytes
                    content_type = None
                    try:
                        m = magic.Magic(mime=True)
                        content_type = m.from_buffer(response.content)
                    except Exception:  # pragma: no cover
                        pass

                    # fallback on the content type in our response header
                    if not content_type or content_type == 'application/octet-stream':  # pragma: no cover
                        content_type = response.headers['Content-Type']

                    temp = NamedTemporaryFile(delete=True)
                    temp.write(response.content)
                    temp.flush()

                    return '%s:%s' % (content_type, channel.org.save_media(File(temp), extension))

    def post(self, request, *args, **kwargs):

        request_body = request.body
        request_method = request.method
        request_path = request.get_full_path()

        def make_response(description, msg=None, status_code=None):
            response_body = dict(description=description)
            if msg:
                log(msg, 'Incoming message', json.dumps(response_body))

            if not status_code:
                status_code = 201
            return JsonResponse(response_body, status=status_code)

        def log(msg, description, response_body):
            event = HttpEvent(request_method, request_path, request_body, 200, response_body)
            ChannelLog.log_message(msg, description, event)

        channel_uuid = kwargs['uuid']
        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type='TG').first()

        if not channel:  # pragma: needs cover
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        body = json.loads(force_text(request.body))

        if 'message' not in body:
            return make_response('No "message" found in payload', status_code=400)

        # look up the contact
        telegram_id = str(body['message']['from']['id'])
        urn = URN.from_telegram(telegram_id)
        contact = Contact.from_urn(channel.org, urn)

        # if the contact doesn't exist, try to create one
        if not contact:
            # "from": {
            # "id": 25028612,
            # "first_name": "Eric",
            # "last_name": "Newcomer",
            # "username": "ericn" }
            name = " ".join((body['message']['from'].get('first_name', ''), body['message']['from'].get('last_name', '')))
            name = name.strip()

            username = body['message']['from'].get('username', '')
            if not name and username:  # pragma: needs cover
                name = username

            if channel.org.is_anon:
                name = None

            Contact.get_or_create(channel.org, urn, channel, name)

        text = ""
        attachments = []
        msg_date = datetime.utcfromtimestamp(body['message']['date']).replace(tzinfo=pytz.utc)

        def fetch_attachment(media_type):
            attachment = body['message'][media_type]
            if isinstance(attachment, list):
                attachment = attachment[-1]
                if isinstance(attachment, list):  # pragma: needs cover
                    attachment = attachment[0]

            # if we got a media URL for this attachment, save it away
            media_url = TelegramHandler.download_file(channel, attachment['file_id'])
            if media_url:
                attachments.append(media_url)

        if 'text' in body['message']:
            text = body['message']['text']
        elif 'caption' in body['message']:
            text = body['message']['caption']
        elif 'contact' in body['message']:  # pragma: needs cover
            contact_block = body['message']['contact']

            if 'first_name' in contact_block and 'phone_number' in contact_block:
                text = '%(first_name)s (%(phone_number)s)' % contact_block
            elif 'first_name' in contact_block:
                text = '%(first_name)s' % contact_block
            elif 'phone_number' in contact_block:
                text = '%(phone_number)s' % contact_block
        elif 'venue' in body['message']:
            if 'title' in body['message']['venue']:
                text = body['message']['venue']['title']

        for msg_type in ('sticker', 'video', 'voice', 'document', 'photo'):
            if msg_type in body['message']:
                fetch_attachment(msg_type)

        if 'location' in body['message']:
            location = body['message']['location']
            attachments.append('geo:%s,%s' % (location['latitude'], location['longitude']))

        if text.strip() == "/start":
            event = ChannelEvent.create(channel, urn, ChannelEvent.TYPE_NEW_CONVERSATION, timezone.now())
            event.handle()
            return make_response("Conversation started")

        if text or attachments:
            msg = Msg.create_incoming(channel, urn, text, attachments=attachments, date=msg_date)
            log(msg, 'Inbound message', json.dumps(dict(description='Message accepted')))
            return make_response('Message accepted', msg)
        else:
            return make_response("Ignored, nothing provided in payload to create a message")


class InfobipHandler(BaseChannelHandler):

    courier_url = r'^ib/(?P<uuid>[a-z0-9\-]+)/(?P<action>delivered|receive)$'
    courier_name = 'courier.ib'

    handler_url = r'^infobip/(?P<action>delivered|receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.infobip_handler'

    def get_channel_type(self):
        return 'IB'

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        request_body = request.body
        request_method = request.method
        request_path = request.get_full_path()

        def make_response(description, status_code=None):
            if not status_code:
                status_code = 201
            return HttpResponse(description, status=status_code)

        def log(msg, description, response_body):
            event = HttpEvent(request_method, request_path, request_body, 200, response_body)
            ChannelLog.log_message(msg, description, event)

        channel_uuid = kwargs['uuid']
        action = kwargs['action']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=self.get_channel_type()).first()
        if not channel:  # pragma: needs cover
            return make_response("Channel with uuid: %s not found." % channel_uuid, status_code=404)

        try:
            body = json.loads(request.body)
        except Exception as e:
            return make_response("Invalid JSON in POST body: %s" % str(e), status_code=400)

        # ignore if the JSON does not have results key
        if 'results' not in body:
            return make_response('Missing "results" in payload', status_code=400)

        if action == 'receive':

            msgs = []

            messageCount = body.get('messageCount')
            for i in range(messageCount):
                message_dict = body.get('results')[i]

                external_id = message_dict.get('messageId')
                text = message_dict.get('text')
                receiver = message_dict.get('to')
                sender = message_dict.get('from')
                try:
                    receivedAt = message_dict.get('receivedAt')
                    msg_date = iso8601.parse_date(receivedAt, default_timezone=None)
                except iso8601.ParseError:
                    msg_date = None

                if not text:
                    continue

                if channel.address.lstrip('+') == receiver.lstrip('+'):
                    urn = URN.from_tel(sender)
                    sms = Msg.create_incoming(channel, urn, text, date=msg_date, external_id=external_id)
                    msgs.append(sms)
                    log(sms, "Incoming message", "SMS Accepted: %s" % sms.id)

            if msgs:
                return make_response("SMS Accepted: %s" % ",".join([str(msg.id) for msg in msgs]))
            else:
                return make_response("No message for channel with uuid: %s" % channel_uuid, status_code=404)

        elif action == 'delivered':

            msg_reports = body.get('results')

            for report in msg_reports:
                msg_id = report.get('messageId')

                # look up the message
                sms = Msg.objects.filter(channel=channel, id=msg_id).select_related('channel').first()
                status = report.get('status', dict()).get('groupName')

                if status == 'DELIVERED':
                    sms.status_delivered()
                elif status in ['REJECTED', 'UNDELIVERABLE']:
                    sms.status_fail()
                log(sms, 'Status Updated', "SMS Status Updated")

            return make_response("SMS Status Updated", status_code=200)

        return make_response("Unreconized action: %s" % action, status_status=404)  # pragma: needs cover

    def get(self, request, *args, **kwargs):
        return HttpResponse("Illegal method, must be POST", status=405)


class Hub9Handler(BaseChannelHandler):
    courier_url = r'^h9/(?P<uuid>[a-z0-9\-]+)/(?P<action>sent|delivered|failed|receive|received)$'
    courier_name = 'courier.h9'

    handler_url = r'^hub9/(?P<action>sent|delivered|failed|receive|received)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.hub9_handler'

    def get_channel_type(self):
        return 'H9'

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        channel_uuid = kwargs['uuid']
        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=self.get_channel_type()).first()
        if not channel:  # pragma: needs cover
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        # They send everythign as a simple GET
        # userid=testusr&password=test&original=555555555555&sendto=666666666666
        # &messageid=99123635&message=Test+sending+sms

        action = kwargs['action'].lower()
        message = self.get_param('message')
        external_id = self.get_param('messageid')
        status = int(self.get_param('status', -1))
        from_number = self.get_param('original')
        to_number = self.get_param('sendto')

        # delivery reports
        if action == 'delivered':  # pragma: needs cover

            if external_id is None or status is None:
                return HttpResponse("Parameters messageid and status should not be null.", status=401)

            # look up the message
            sms = Msg.objects.filter(channel=channel, pk=external_id).select_related('channel').first()
            if not sms:
                return HttpResponse("No SMS message with external id: %s" % external_id, status=404)

            if 10 <= status <= 12:
                sms.status_delivered()
            elif status > 20:
                sms.status_fail()
            elif status != -1:
                sms.status_sent()

            return HttpResponse("000")

        # An MO message
        if action in ['received', 'receive']:

            if message is None or from_number is None or to_number is None:
                return HttpResponse("Parameters message, original and sendto should not be null.",
                                    status=401)

            # make sure the channel number matches the receiver
            if channel.address not in ['+' + to_number, to_number]:
                return HttpResponse("Channel with number '%s' not found." % to_number, status=404)

            Msg.create_incoming(channel, URN.from_tel('+' + from_number), message)
            return HttpResponse("000")

        return HttpResponse("Unreconized action: %s" % action, status=404)  # pragma: needs cover


class DartMediaHandler(Hub9Handler):
    courier_url = r'^da/(?P<uuid>[a-z0-9\-]+)/(?P<action>delivered|received|receive)$'
    courier_name = 'courier.da'

    handler_url = r'^dartmedia/(?P<action>delivered|received|receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.dartmedia_handler'

    def get_channel_type(self):
        return 'DA'


class HighConnectionHandler(BaseChannelHandler):
    courier_url = r'^hx/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$'
    courier_name = 'courier.hx'

    handler_url = r'^hcnx/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.hcnx_handler'

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        channel_uuid = kwargs['uuid']
        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type='HX').first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=400)

        action = kwargs['action'].lower()

        # Update on the status of a sent message
        if action == 'status':
            msg_id = self.get_param('ret_id')
            status = int(self.get_param('status', 0))

            # look up the message
            sms = Msg.objects.filter(channel=channel, pk=msg_id).select_related('channel').first()
            if not sms:  # pragma: needs cover
                return HttpResponse("No SMS message with id: %s" % msg_id, status=400)

            if status == 4:  # pragma: needs cover
                sms.status_sent()
            elif status == 6:
                sms.status_delivered()
            elif status in [2, 11, 12, 13, 14, 15, 16]:  # pragma: needs cover
                sms.status_fail()

            return HttpResponse(json.dumps(dict(msg="Status Updated")))

        # An MO message
        elif action == 'receive':
            to_number = self.get_param('TO')
            from_number = self.get_param('FROM')
            message = self.get_param('MESSAGE')
            received = self.get_param('RECEPTION_DATE')

            # dateformat for reception date is 2015-04-02T14:26:06 in UTC
            if received is None:  # pragma: needs cover
                received = timezone.now()
            else:
                raw_date = datetime.strptime(received, "%Y-%m-%dT%H:%M:%S")
                received = raw_date.replace(tzinfo=pytz.utc)

            if to_number is None or from_number is None or message is None:  # pragma: needs cover
                return HttpResponse("Missing TO, FROM or MESSAGE parameters", status=400)

            msg = Msg.create_incoming(channel, URN.from_tel(from_number), message, date=received)
            return HttpResponse(json.dumps(dict(msg="Msg received", id=msg.id)))

        return HttpResponse("Unrecognized action: %s" % action, status=400)  # pragma: needs cover


class BlackmynaHandler(BaseChannelHandler):
    courier_url = r'^bm/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$'
    courier_name = 'courier.bm'

    handler_url = r'^blackmyna/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.blackmyna_handler'

    def post(self, request, *args, **kwargs):  # pragma: needs cover
        return self.get(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        channel_uuid = kwargs['uuid']
        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type='BM').first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=400)

        action = kwargs['action'].lower()

        # Update on the status of a sent message
        if action == 'status':
            msg_id = self.get_param('id')
            status = int(self.get_param('status', 0))

            # look up the message
            sms = Msg.objects.filter(channel=channel, external_id=msg_id).select_related('channel').first()
            if not sms:
                return HttpResponse("No SMS message with id: %s" % msg_id, status=400)

            if status == 8:
                sms.status_sent()
            elif status == 1:
                sms.status_delivered()
            elif status in [2, 16]:
                sms.status_fail()

            return HttpResponse("")

        # An MO message
        elif action == 'receive':
            to_number = self.get_param('to')
            from_number = self.get_param('from')
            message = self.get_param('text')
            # smsc = self.get_param('smsc', None)

            if to_number is None or from_number is None or message is None:  # pragma: needs cover
                return HttpResponse("Missing to, from or text parameters", status=400)

            if channel.address != to_number:
                return HttpResponse("Invalid to number [%s], expecting [%s]" % (to_number, channel.address), status=400)

            Msg.create_incoming(channel, URN.from_tel(from_number), message)
            return HttpResponse("")

        return HttpResponse("Unrecognized action: %s" % action, status=400)  # pragma: needs cover


class SMSCentralHandler(BaseChannelHandler):
    courier_url = r'^sc/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$'
    courier_name = 'courier.sc'

    handler_url = r'^smscentral/(?P<action>receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.smscentral_handler'

    def post(self, request, *args, **kwargs):  # pragma: needs cover
        return self.get(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        channel_uuid = kwargs['uuid']
        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type='SC').first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=400)

        action = kwargs['action'].lower()

        # An MO message
        if action == 'receive':
            from_number = self.get_param('mobile')
            message = self.get_param('message')

            if from_number is None or message is None:  # pragma: needs cover
                return HttpResponse("Missing mobile or message parameters", status=400)

            Msg.create_incoming(channel, URN.from_tel(from_number), message)
            return HttpResponse("")

        return HttpResponse("Unrecognized action: %s" % action, status=400)  # pragma: needs cover


class MacroKioskHandler(BaseChannelHandler):
    courier_url = r'^mk/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$'
    courier_name = 'courier.mk'

    handler_url = r'^macrokiosk/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.macrokiosk_handler'

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        channel_uuid = kwargs['uuid']
        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type='MK').first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=400)

        action = kwargs['action'].lower()

        # Update on the status of a sent message
        if action == 'status':
            msg_id = self.get_param('msgid')
            status = self.get_param('status')

            # look up the message
            sms = Msg.objects.filter(channel=channel, external_id=msg_id).select_related('channel').first()
            if not sms:
                return HttpResponse("No SMS message with id: %s" % msg_id, status=400)

            if status == 'ACCEPTED':
                sms.status_sent()
            elif status == 'DELIVERED':
                sms.status_delivered()
            elif status == 'UNDELIVERED':
                sms.status_fail()

            return HttpResponse("")

        # An MO message
        elif action == 'receive':

            external_id = self.get_param('msgid')
            message_date = datetime.strptime(self.get_param('time'), "%Y-%m-%d%H:%M:%S")
            local_date = pytz.timezone('Asia/Kuala_Lumpur').localize(message_date)
            gmt_date = local_date.astimezone(pytz.utc)

            text = self.get_param('text')
            if self.get_param('shortcode'):
                from_number = self.get_param('from')
                to_number = self.get_param('shortcode')
            else:
                from_number = self.get_param('msisdn')
                to_number = self.get_param('longcode')

            if to_number is None or from_number is None or text is None:
                return HttpResponse("Missing shortcode, longcode, from, msisdn or text parameters", status=400)

            if channel.address != to_number:
                return HttpResponse("Invalid to number [%s], expecting [%s]" % (to_number, channel.address), status=400)

            Msg.create_incoming(channel, URN.from_tel(from_number), text, date=gmt_date, external_id=external_id)
            return HttpResponse("-1")

        return HttpResponse("Unrecognized action: %s" % action, status=400)  # pragma: needs cover


class M3TechHandler(ExternalHandler):
    """
    Exposes our API for handling and receiving messages, same as external handlers.
    """
    courier_url = r'^m3/(?P<uuid>[a-z0-9\-]+)/(?P<action>sent|delivered|failed|received|receive)$'
    courier_name = 'courier.m3'

    handler_url = r'^m3tech/(?P<action>sent|delivered|failed|received|receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.m3tech_handler'

    def get_channel_type(self):
        return 'M3'


class NexmoCallHandler(BaseChannelHandler):

    handler_url = r'^nexmo/(?P<action>answer|event)/(?P<uuid>[a-z0-9\-]+)/$'
    handler_name = 'handlers.nexmo_call_handler'

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.flows.models import FlowSession
        from temba.ivr.models import IVRCall

        action = kwargs['action'].lower()

        request_body = force_text(request.body)
        request_path = request.get_full_path()
        request_method = request.method

        request_uuid = kwargs['uuid']

        if action == 'event':
            if not request_body:
                return HttpResponse('')

            body_json = json.loads(request_body)
            status = body_json.get('status', None)
            duration = body_json.get('duration', None)
            call_uuid = body_json.get('uuid', None)
            conversation_uuid = body_json.get('conversation_uuid', None)

            if call_uuid is None:
                return HttpResponse("Missing uuid parameter, ignoring")

            call = IVRCall.objects.filter(external_id=call_uuid).first()
            if not call:
                # try looking up by the conversation uuid (inbound calls start with that)
                call = IVRCall.objects.filter(external_id=conversation_uuid).first()
                if call:
                    call.external_id = call_uuid
                    call.save()
                else:
                    response = dict(message="Call not found for %s" % call_uuid)
                    return JsonResponse(response)

            channel = call.channel
            channel_type = channel.channel_type
            call.update_status(status, duration, channel_type)
            call.save()

            response = dict(description="Updated call status",
                            call=dict(status=call.get_status_display(), duration=call.duration))

            event = HttpEvent(request_method, request_path, request_body, 200, json.dumps(response))
            ChannelLog.log_ivr_interaction(call, "Updated call status", event)

            if call.status == IVRCall.COMPLETED:
                # if our call is completed, hangup
                runs = FlowRun.objects.filter(connection=call)
                for run in runs:
                    if not run.is_completed():
                        final_step = FlowStep.objects.filter(run=run).order_by('-arrived_on').first()
                        run.set_completed(final_step=final_step)

            return JsonResponse(response)

        if action == 'answer':
            if not request_body:
                return HttpResponse('')

            body_json = json.loads(request_body)
            from_number = body_json.get('from', None)
            channel_number = body_json.get('to', None)
            external_id = body_json.get('conversation_uuid', None)

            if not from_number or not channel_number or not external_id:
                return HttpResponse("Missing parameters, Ignoring")

            # look up the channel
            address_q = Q(address=channel_number) | Q(address=('+' + channel_number))
            channel = Channel.objects.filter(address_q).filter(is_active=True, channel_type='NX').first()

            # make sure we got one, and that it matches the key for our org
            org_uuid = None
            if channel:
                org_uuid = channel.org.config.get(NEXMO_UUID, None)

            if not channel or org_uuid != request_uuid:
                return HttpResponse("Channel not found for number: %s" % channel_number, status=404)

            urn = URN.from_tel(from_number)
            contact, urn_obj = Contact.get_or_create(channel.org, urn, channel)

            flow = Trigger.find_flow_for_inbound_call(contact)

            if flow:
                call = IVRCall.create_incoming(channel, contact, urn_obj, channel.created_by, external_id)
                session = FlowSession.create(contact, connection=call)

                FlowRun.create(flow, contact, session=session, connection=call)
                response = Flow.handle_call(call)

                event = HttpEvent(request_method, request_path, request_body, 200, six.text_type(response))
                ChannelLog.log_ivr_interaction(call, "Incoming request for call", event)
                return JsonResponse(json.loads(six.text_type(response)), safe=False)
            else:
                # we don't have an inbound trigger to deal with this call.
                response = channel.generate_ivr_response()

                # say nothing and hangup, this is a little rude, but if we reject the call, then
                # they'll get a non-working number error. We send 'busy' when our server is down
                # so we don't want to use that here either.
                response.say('')
                response.hangup()

                # if they have a missed call trigger, fire that off
                Trigger.catch_triggers(contact, Trigger.TYPE_MISSED_CALL, channel)

                # either way, we need to hangup now
                return JsonResponse(json.loads(six.text_type(response)), safe=False)


class NexmoHandler(BaseChannelHandler):
    courier_url = r'^nx/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$'
    courier_name = 'courier.nx'

    handler_url = r'^nexmo/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/$'
    handler_name = 'handlers.nexmo_handler'

    def post(self, request, *args, **kwargs):  # pragma: needs cover
        return self.get(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        action = kwargs['action'].lower()

        # crazy enough, for nexmo 'to' is the channel number for both delivery reports and new messages
        channel_number = self.get_param('to')
        external_id = self.get_param('messageId')

        # nexmo fires a test request at our URL with no arguments, return 200 so they take our URL as valid
        if (action == 'receive' and channel_number is None) or (action == 'status' and external_id is None):  # pragma: needs cover
            return HttpResponse("No to parameter, ignoring")

        # look up the channel
        channel = Channel.objects.filter(is_active=True, channel_type='NX', uuid=kwargs['uuid']).first()

        if not channel:
            return HttpResponse("Channel not found for number: %s" % channel_number, status=404)

        # this is a callback for a message we sent
        if action == 'status':

            # look up the message
            sms = Msg.objects.filter(channel=channel, external_id=external_id).select_related('channel').first()
            if not sms:
                return HttpResponse("No SMS message with external id: %s" % external_id, status=200)

            status = self.get_param('status')

            if status == 'delivered':
                sms.status_delivered()
            elif status == 'accepted' or status == 'buffered':
                sms.status_sent()
            elif status == 'expired' or status == 'failed':
                sms.status_fail()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            urn = URN.from_tel('+%s' % self.get_param('msisdn'))
            sms = Msg.create_incoming(channel, urn, self.get_param('text'), external_id=external_id)
            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:  # pragma: needs cover
            return HttpResponse("Not handled", status=400)


class VerboiceHandler(BaseChannelHandler):
    courier_url = r'^vb/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$'
    courier_name = 'courier.vb'

    handler_url = r'^verboice/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.verboice_handler'

    def post(self, request, *args, **kwargs):
        return HttpResponse("Illegal method, must be GET", status=405)

    def get(self, request, *args, **kwargs):

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid__iexact=request_uuid, is_active=True,
                                         channel_type='VB').first()
        if not channel:  # pragma: needs cover
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=404)

        if action == 'status':
            to = self.get_param('From')
            call_sid = self.get_param('CallSid')
            call_status = self.get_param('CallStatus')

            if not to or not call_sid or not call_status:
                return HttpResponse("Missing From or CallSid or CallStatus, ignoring message", status=400)

            from temba.ivr.models import IVRCall
            call = IVRCall.objects.filter(external_id=call_sid).first()
            if call:
                call.update_status(call_status, None, 'VB')
                call.save()
                return HttpResponse("Call Status Updated")

        return HttpResponse("Not handled", status=400)


class KannelHandler(BaseChannelHandler):
    courier_url = r'^kn/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$'
    courier_name = 'courier.kn'

    handler_url = r'^kannel/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.kannel_handler'

    def get(self, request, *args, **kwargs):  # pragma: needs cover
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, DELIVERED, FAILED, WIRED, PENDING, QUEUED

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type='KN').first()
        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=400)

        # kannel is telling us this message got delivered
        if action == 'status':
            sms_id = self.get_param('id')
            status_code = self.get_param('status')

            r = get_redis_connection()

            if not sms_id and not status_code:  # pragma: needs cover
                return HttpResponse("Missing one of 'id' or 'status' in request parameters.", status=400)

            # look up the message
            sms = Msg.objects.filter(channel=channel, id=sms_id).select_related('channel')
            if not sms:
                return HttpResponse("Message with external id of '%s' not found" % sms_id, status=400)

            # possible status codes kannel will send us
            STATUS_CHOICES = {'1': DELIVERED,
                              '2': FAILED,
                              '4': SENT,
                              '8': SENT,
                              '16': FAILED}

            # check our status
            status = STATUS_CHOICES.get(status_code, None)

            # we don't recognize this status code
            if not status:  # pragma: needs cover
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
                    Msg.mark_error(r, channel, sms_obj)

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            sms_id = self.get_param('id')
            sms_ts = self.get_param('ts')
            sms_message = self.get_param('message')
            sms_sender = self.get_param('sender')

            if sms_id is None or sms_ts is None or sms_message is None or sms_sender is None:  # pragma: needs cover
                return HttpResponse("Missing one of 'message', 'sender', 'id' or 'ts' in request parameters.", status=400)

            # dates come in the format of a timestamp
            sms_date = datetime.utcfromtimestamp(int(sms_ts))
            gmt_date = pytz.timezone('GMT').localize(sms_date)

            urn = URN.from_tel(sms_sender)
            sms = Msg.create_incoming(channel, urn, sms_message, date=gmt_date)

            Msg.objects.filter(pk=sms.id).update(external_id=sms_id)
            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:  # pragma: needs cover
            return HttpResponse("Not handled", status=400)


class PlivoHandler(BaseChannelHandler):
    courier_url = r'^pl/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$'
    courier_name = 'courier.pl'

    handler_url = r'^plivo/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.plivo_handler'

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, DELIVERED, FAILED, WIRED, PENDING, QUEUED

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        sms_from = self.get_param('From')
        sms_to = self.get_param('To')
        sms_id = self.get_param('MessageUUID')

        if sms_from is None or sms_to is None or sms_id is None:
            return HttpResponse("Missing one of 'From', 'To', or 'MessageUUID' in request parameters.", status=400)

        channel = Channel.objects.filter(is_active=True, uuid=request_uuid, channel_type='PL').first()

        if action == 'status':
            plivo_channel_address = sms_from
            plivo_status = self.get_param('Status')

            if plivo_status is None:  # pragma: needs cover
                return HttpResponse("Missing 'Status' in request parameters.", status=400)

            if not channel:
                return HttpResponse("Channel not found for number: %s" % plivo_channel_address, status=400)

            channel_address = plivo_channel_address
            if channel_address[0] != '+':
                channel_address = '+' + channel_address

            if channel.address != channel_address:  # pragma: needs cover
                return HttpResponse("Channel not found for number: %s" % plivo_channel_address, status=400)

            parent_id = self.get_param('ParentMessageUUID')
            if parent_id is not None:  # pragma: needs cover
                sms_id = parent_id

            # look up the message
            sms = Msg.objects.filter(channel=channel, external_id=sms_id).select_related('channel')
            if not sms:
                return HttpResponse("Message with external id of '%s' not found" % sms_id, status=400)

            STATUS_CHOICES = {'queued': WIRED,
                              'sent': SENT,
                              'delivered': DELIVERED,
                              'undelivered': SENT,
                              'rejected': FAILED}

            status = STATUS_CHOICES.get(plivo_status)

            if not status:  # pragma: needs cover
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
                    sms_obj.status_fail()
                    Channel.track_status(sms_obj.channel, "Failed")
            else:
                # ignore wired, we are wired by default
                pass

            # update the broadcast status
            bcast = sms.first().broadcast
            if bcast:  # pragma: needs cover
                bcast.update()

            return HttpResponse("Status Updated")

        elif action == 'receive':
            sms_text = self.get_param('Text')
            plivo_channel_address = sms_to

            if sms_text is None:  # pragma: needs cover
                return HttpResponse("Missing 'Text' in request parameters.", status=400)

            if not channel:  # pragma: needs cover
                return HttpResponse("Channel not found for number: %s" % plivo_channel_address, status=400)

            channel_address = plivo_channel_address
            if channel_address[0] != '+':
                channel_address = '+' + channel_address

            if channel.address != channel_address:
                return HttpResponse("Channel not found for number: %s" % plivo_channel_address, status=400)

            sms = Msg.create_incoming(channel, URN.from_tel(sms_from), sms_text)

            Msg.objects.filter(pk=sms.id).update(external_id=sms_id)

            return HttpResponse("SMS accepted: %d" % sms.id)
        else:  # pragma: needs cover
            return HttpResponse("Not handled", status=400)


class MageHandler(BaseChannelHandler):

    handler_url = r'^mage/(?P<action>handle_message|follow_notification|stop_contact)$'
    handler_name = 'handlers.mage_handler'

    def get(self, request, *args, **kwargs):
        return JsonResponse(dict(error="Illegal method, must be POST"), status=405)

    def post(self, request, *args, **kwargs):
        from temba.triggers.tasks import fire_follow_triggers

        if not settings.MAGE_AUTH_TOKEN:  # pragma: no cover
            return JsonResponse(dict(error="Authentication not configured"), status=401)

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

            msg = Msg.objects.select_related('org').get(pk=msg_id)

            push_task(msg.org, HANDLER_QUEUE, HANDLE_EVENT_TASK,
                      dict(type=MSG_EVENT, id=msg.id, from_mage=True, new_contact=new_contact))

            # fire an event off for this message
            WebHookEvent.trigger_sms_event(WebHookEvent.TYPE_SMS_RECEIVED, msg, msg.created_on)

        elif action == 'follow_notification':
            try:
                channel_id = int(request.POST.get('channel_id', ''))
                contact_urn_id = int(request.POST.get('contact_urn_id', ''))
            except ValueError:  # pragma: needs cover
                return JsonResponse(dict(error="Invalid channel or contact URN id"), status=400)

            on_transaction_commit(lambda: fire_follow_triggers.apply_async(args=(channel_id, contact_urn_id, new_contact),
                                                                           queue='handler'))

        elif action == 'stop_contact':
            contact = Contact.objects.filter(is_active=True, id=request.POST.get('contact_id', '-1')).first()
            if not contact:
                return JsonResponse(dict(error="Invalid contact_id"), status=400)

            contact.stop(contact.modified_by)

        return JsonResponse(dict(error=None))


class StartHandler(BaseChannelHandler):
    courier_url = r'^st/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$'
    courier_name = 'courier.st'

    handler_url = r'^start/(?P<action>receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.start_handler'

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type='ST').first()
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


class ChikkaHandler(BaseChannelHandler):
    courier_url = r'^ck/(?P<uuid>[a-z0-9\-]+)/receive$'
    courier_name = 'courier.ck'

    handler_url = r'^chikka/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.chikka_handler'

    def get(self, request, *args, **kwargs):  # pragma: needs cover
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, WIRED, PENDING, QUEUED

        request_uuid = kwargs['uuid']
        action = self.get_param('message_type').lower()

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type='CK').first()
        if not channel:
            return HttpResponse("Error, channel not found for id: %s" % request_uuid, status=400)

        # if this is the status of an outgoing message
        if action == 'outgoing':
            sms_id = self.get_param('message_id')
            status_code = self.get_param('status')

            if sms_id is None or status_code is None:  # pragma: needs cover
                return HttpResponse("Error, missing one of 'message_id' or 'status' in request parameters.", status=400)

            # look up the message
            sms = Msg.objects.filter(channel=channel, id=sms_id).select_related('channel')
            if not sms:
                return HttpResponse("Error, message with external id of '%s' not found" % sms_id, status=400)

            # possible status codes Chikka will send us
            status_choices = {'SENT': SENT, 'FAILED': FAILED}

            # check our status
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
                    sms_obj.status_fail()

            return HttpResponse("Accepted. SMS Status Updated")

        # this is a new incoming message
        elif action == 'incoming':
            sms_id = self.get_param('request_id')
            sms_from = self.get_param('mobile_number')
            sms_text = self.get_param('message')
            sms_timestamp = self.get_param('timestamp')

            if sms_id is None or sms_from is None or sms_text is None or sms_timestamp is None:  # pragma: needs cover
                return HttpResponse("Error, missing one of 'mobile_number', 'request_id', "
                                    "'message' or 'timestamp' in request parameters.", status=400)

            # dates come as timestamps
            sms_date = datetime.utcfromtimestamp(float(sms_timestamp))
            gmt_date = pytz.timezone('GMT').localize(sms_date)

            urn = URN.from_tel(sms_from)
            sms = Msg.create_incoming(channel, urn, sms_text, date=gmt_date)

            # save our request id in case of replies
            Msg.objects.filter(pk=sms.id).update(external_id=sms_id)
            return HttpResponse("Accepted: %d" % sms.id)

        else:  # pragma: needs cover
            return HttpResponse("Error, unknown message type", status=400)


class JasminHandler(BaseChannelHandler):
    courier_url = r'^js/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$'
    courier_name = 'courier.js'

    handler_url = r'^jasmin/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.jasmin_handler'

    def get(self, request, *args, **kwargs):  # pragma: needs cover
        return HttpResponse("Must be called as a POST", status=400)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg
        from temba.utils import gsm7

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type='JS').first()
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
            sms = Msg.objects.filter(channel=channel, external_id=sms_id).select_related('channel')
            if not sms:  # pragma: needs cover
                return HttpResponse("Message with external id of '%s' not found" % sms_id, status=400)

            if dlvrd == '1':
                for sms_obj in sms:
                    sms_obj.status_delivered()
            elif err == '1':
                for sms_obj in sms:
                    sms_obj.status_fail()

            # tell Jasmin we handled this
            return HttpResponse('ACK/Jasmin')

        # this is a new incoming message
        elif action == 'receive':
            if not all(k in request.POST for k in ['content', 'coding', 'from', 'to', 'id']):  # pragma: needs cover
                return HttpResponse("Missing one of 'content', 'coding', 'from', 'to' or 'id' in request parameters.",
                                    status=400)

            # if we are GSM7 coded, decode it
            content = request.POST['content']
            if request.POST['coding'] == '0':
                content = gsm7.decode(request.POST['content'], 'replace')[0]

            sms = Msg.create_incoming(channel, URN.from_tel(request.POST['from']), content)
            Msg.objects.filter(pk=sms.id).update(external_id=request.POST['id'])
            return HttpResponse('ACK/Jasmin')

        else:  # pragma: needs cover
            return HttpResponse("Not handled, unknown action", status=400)


class JunebugHandler(BaseChannelHandler):
    courier_url = r'^jn/(?P<uuid>[a-z0-9\-]+)/(?P<action>event|inbound)$'
    courier_name = 'courier.jn'

    handler_url = r'^junebug/(?P<action>event|inbound)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.junebug_handler'
    ACK = 'ack'
    NACK = 'nack'

    def get(self, request, *args, **kwargs):
        return HttpResponse("Must be called as a POST", status=400)

    def is_ussd_message(self, msg):
        return 'session_event' in msg.get('channel_data', {})

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        request_body = request.body
        request_method = request.method
        request_path = request.get_full_path()

        def log_channel(channel, description, event, is_error=False):
            return ChannelLog.objects.create(channel_id=channel.pk, is_error=is_error, request=event.request_body,
                                             response=event.response_body, url=event.url, method=event.method,
                                             response_status=event.status_code, description=description)

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        data = json.loads(force_text(request_body))
        is_ussd = self.is_ussd_message(data)
        channel_data = data.get('channel_data', {})
        channel_types = ('JNU', 'JN')

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type__in=channel_types).first()

        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=400)

        auth = request.META.get('HTTP_AUTHORIZATION', '').split(' ')
        secret = channel.config.get(Channel.CONFIG_SECRET)
        if secret is not None and (len(auth) != 2 or auth[0] != 'Token' or auth[1] != secret):
            return JsonResponse(dict(error="Incorrect authentication token"), status=401)

        # Junebug is sending an event
        if action == 'event':
            expected_keys = ["event_type", "message_id", "timestamp"]
            if not set(expected_keys).issubset(data.keys()):
                status = 400
                response_body = "Missing one of %s in request parameters." % (', '.join(expected_keys))
                event = HttpEvent(request_method, request_path, request_body, status, response_body)
                log_channel(channel, 'Failed to handle event.', event, is_error=True)
                return HttpResponse(response_body, status=status)

            message_id = data['message_id']
            event_type = data["event_type"]

            # look up the message
            message = Msg.objects.filter(channel=channel, external_id=message_id).select_related('channel')
            if not message:
                status = 400
                response_body = "Message with external id of '%s' not found" % (message_id,)
                event = HttpEvent(request_method, request_path, request_body, status, response_body)
                log_channel(channel, 'Failed to handle %s event_type.' % (event_type), event)
                return HttpResponse(response_body, status=status)

            if event_type == 'submitted':
                for message_obj in message:
                    message_obj.status_sent()
            if event_type == 'delivery_succeeded':
                for message_obj in message:
                    message_obj.status_delivered()
            elif event_type in ['delivery_failed', 'rejected']:
                for message_obj in message:
                    message_obj.status_fail()

            response_body = {
                'status': self.ACK,
                'message_ids': [message_obj.pk for message_obj in message]
            }
            event = HttpEvent(request_method, request_path, request_body, 200, json.dumps(response_body))
            log_channel(channel, 'Handled %s event_type.' % (event_type), event)
            # Let Junebug know we're happy
            return JsonResponse(response_body)

        # Handle an inbound message
        elif action == 'inbound':
            expected_keys = [
                'channel_data',
                'from',
                'channel_id',
                'timestamp',
                'content',
                'to',
                'reply_to',
                'message_id',
            ]
            if not set(expected_keys).issubset(data.keys()):
                status = 400
                response_body = "Missing one of %s in request parameters." % (', '.join(expected_keys))
                event = HttpEvent(request_method, request_path, request_body, status, response_body)
                log_channel(channel, 'Failed to handle message.', event, is_error=True)
                return HttpResponse(response_body, status=status)

            if is_ussd:
                status = {
                    'close': USSDSession.INTERRUPTED,
                    'new': USSDSession.TRIGGERED,
                }.get(channel_data.get('session_event'), USSDSession.IN_PROGRESS)

                message_date = datetime.strptime(data['timestamp'], "%Y-%m-%d %H:%M:%S.%f")
                gmt_date = pytz.timezone('GMT').localize(message_date)
                # Use a session id if provided, otherwise fall back to using the `from` address as the identifier
                session_id = channel_data.get('session_id') or data['from']

                connection = USSDSession.handle_incoming(channel=channel, urn=data['from'], content=data['content'],
                                                         status=status, date=gmt_date, external_id=session_id,
                                                         message_id=data['message_id'], starcode=data['to'])

                if connection:
                    status = 200
                    response_body = {
                        'status': self.ACK,
                        'session_id': connection.pk,
                    }
                    event = HttpEvent(request_method, request_path, request_body, status, json.dumps(response_body))
                    log_channel(channel, 'Handled USSD message of %s session_event' % (
                        channel_data['session_event'],), event)
                    return JsonResponse(response_body, status=status)
                else:
                    status = 400
                    response_body = {
                        'status': self.NACK,
                        'reason': 'No suitable session found for this message.'
                    }
                    event = HttpEvent(request_method, request_path, request_body, status, json.dumps(response_body))
                    log_channel(channel, 'Failed to handle USSD message of %s session_event' % (
                        channel_data['session_event'],), event)
                    return JsonResponse(response_body, status=status)
            else:
                content = data['content']
                message = Msg.create_incoming(channel, URN.from_tel(data['from']), content)
                status = 200
                response_body = {
                    'status': self.ACK,
                    'message_id': message.pk,
                }
                Msg.objects.filter(pk=message.id).update(external_id=data['message_id'])
                event = HttpEvent(request_method, request_path, request_body, status, json.dumps(response_body))
                ChannelLog.log_message(message, 'Handled inbound message.', event)
                return JsonResponse(response_body, status=status)


class MbloxHandler(BaseChannelHandler):
    courier_url = r'^mb/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$'
    courier_name = 'courier.mb'

    handler_url = r'^mblox/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.mblox_handler'

    def get(self, request, *args, **kwargs):  # pragma: needs cover
        return HttpResponse("Must be called as a POST", status=400)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        request_uuid = kwargs['uuid']

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type='MB').first()
        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=400)

        # parse our response
        try:
            body = json.loads(request.body)
        except Exception as e:  # pragma: needs cover
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
            msgs = Msg.objects.filter(channel=channel, external_id=msg_id).select_related('channel')
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
                    msg.status_fail()

            # tell Mblox we've handled this
            return HttpResponse('SMS Updated: %s' % ",".join([str(msg.id) for msg in msgs]))

        # this is a new incoming message
        elif body['type'] == 'mo_text':
            if not all(k in body for k in ['id', 'from', 'to', 'body', 'received_at']):  # pragma: needs cover
                return HttpResponse("Missing one of 'id', 'from', 'to', 'body' or 'received_at' in request body.",
                                    status=400)

            msg_date = parse_datetime(body['received_at'])
            msg = Msg.create_incoming(channel, URN.from_tel(body['from']), body['body'], date=msg_date)
            Msg.objects.filter(pk=msg.id).update(external_id=body['id'])
            return HttpResponse("SMS Accepted: %d" % msg.id)

        else:  # pragma: needs cover
            return HttpResponse("Not handled, unknown type: %s" % body['type'], status=400)


class JioChatHandler(BaseChannelHandler):

    # Jiochat expected the URL to receive message on CONFIG_URL/rcv/msg/message
    # and for event message on CONFIG_URL/rcv/event/menu
    # and for follow event on CONFIG_URL/rcv/event/follow
    courier_url = r'^jc/(?P<uuid>[a-z0-9\-]+)(/rcv/msg/message|/rcv/event/menu|/rcv/event/follow)?/?$'
    courier_name = 'courier.jc'

    handler_url = r'^jiochat/(?P<uuid>[a-z0-9\-]+)(/rcv/msg/message|/rcv/event/menu|/rcv/event/follow)?/?$'
    handler_name = 'handlers.jiochat_handler'

    def lookup_channel(self, kwargs):
        # look up the channel
        return Channel.objects.filter(uuid=kwargs['uuid'], is_active=True, channel_type='JC').first()

    def get(self, request, *args, **kwargs):
        channel = self.lookup_channel(kwargs)
        if not channel:
            return HttpResponse("Channel not found for id: %s" % kwargs['uuid'], status=400)

        client = JiochatClient.from_channel(channel)
        if client:
            verified, echostr = client.verify_request(request, channel.config[Channel.CONFIG_SECRET])

            if verified:
                refresh_jiochat_access_tokens.delay(channel.id)
                return HttpResponse(echostr)

        return JsonResponse(dict(error="Unknown request"), status=400)

    def post(self, request, *args, **kwargs):
        channel = self.lookup_channel(kwargs)
        if not channel:
            return HttpResponse("Channel not found for id: %s" % kwargs['uuid'], status=400)

        try:
            body = json.loads(request.body)
        except Exception as e:  # pragma: needs cover
            return HttpResponse("Invalid JSON in POST body: %s" % str(e), status=400)

        if 'FromUserName' not in body or 'MsgType' not in body or ('MsgId' not in body and 'Event' not in body):
            return HttpResponse("Missing parameters", status=400)

        client = JiochatClient.from_channel(channel)

        sender_id = body.get('FromUserName')
        create_time = body.get('CreateTime', None)
        msg_date = None
        if create_time:
            msg_date = datetime.utcfromtimestamp(float(six.text_type(create_time)[:10])).replace(tzinfo=pytz.utc)
        msg_type = body.get('MsgType')
        external_id = body.get('MsgId', None)

        urn = URN.from_jiochat(sender_id)
        contact = Contact.from_urn(channel.org, urn)
        if not contact:
            contact_name = None
            if not channel.org.is_anon:
                contact_detail = client.get_user_detail(sender_id, channel.id)
                contact_name = contact_detail.get('nickname')

            contact, urn_obj = Contact.get_or_create(channel.org, urn, channel, contact_name)

        if msg_type == 'event':
            event = body.get('Event')
            if event == 'subscribe':
                channel_event = ChannelEvent.create(channel, urn, ChannelEvent.TYPE_FOLLOW, timezone.now())
                channel_event.handle()

                return HttpResponse("New follow handled: %s" % sender_id)

        text = ""
        attachments = []

        if msg_type == 'text':
            text = body.get('Content', "")
        elif msg_type in ['image', 'video', 'voice']:
            media_response = client.request_media(body.get('MediaId'), channel.id)
            content_type, downloaded_url = channel.org.save_response_media(media_response)
            attachments.append('%s:%s' % (content_type, downloaded_url))

        if text or attachments:
            msg = Msg.create_incoming(channel, urn, text, date=msg_date, contact=contact, attachments=attachments,
                                      external_id=external_id)
            return HttpResponse("Msgs Accepted: %s" % msg.id)

        return HttpResponse("Not handled", status=400)


class FacebookHandler(BaseChannelHandler):
    courier_url = r'^fb/(?P<uuid>[a-z0-9\-]+)/receive'
    courier_name = 'courier.fb'

    handler_url = r'^facebook/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.facebook_handler'

    def lookup_channel(self, kwargs):
        # look up the channel
        channel = Channel.objects.filter(uuid=kwargs['uuid'], is_active=True, channel_type='FB').first()
        return channel

    def get(self, request, *args, **kwargs):
        channel = self.lookup_channel(kwargs)
        if not channel:
            return HttpResponse("Channel not found for id: %s" % kwargs['uuid'], status=400)

        # this is a verification of a webhook
        if request.GET.get('hub.mode') == 'subscribe':
            # verify the token against our secret, if the same return the challenge FB sent us
            if channel.config[Channel.CONFIG_SECRET] == request.GET.get('hub.verify_token'):
                # fire off a subscription for facebook events, we have a bit of a delay here so that FB can react to
                # this webhook result
                on_transaction_commit(lambda: fb_channel_subscribe.apply_async([channel.id], delay=5))

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
        except Exception as e:  # pragma: needs cover
            return HttpResponse("Invalid JSON in POST body: %s" % str(e), status=400)

        if 'entry' not in body:
            return HttpResponse("Missing entry array", status=400)

        # iterate through our entries, handling them
        for entry in body.get('entry'):
            # this is a messaging notification
            if 'messaging' in entry:
                status = []

                for envelope in entry['messaging']:
                    if 'optin' in envelope or 'referral' in envelope:
                        # check that the recipient is correct for this channel
                        channel_address = str(envelope['recipient']['id'])
                        if channel_address != channel.address:  # pragma: needs cover
                            return HttpResponse("Msg Ignored for recipient id: %s" % channel_address, status=200)

                        referrer_id = None
                        trigger_extra = None

                        if 'optin' in envelope:
                            referrer_id = envelope['optin'].get('ref')
                            trigger_extra = envelope['optin']
                            trigger_extra[ChannelEvent.EXTRA_REFERRER_ID] = referrer_id

                        elif 'referral' in envelope:
                            referrer_id = envelope['referral'].get('ref')
                            trigger_extra = envelope['referral']
                            trigger_extra[ChannelEvent.EXTRA_REFERRER_ID] = referrer_id

                        # This is a standard opt in, we know the sender id:
                        #   https://developers.facebook.com/docs/messenger-platform/webhook-reference/optins
                        # {
                        #   "sender": { "id": "USER_ID" },
                        #   "recipient": { "id": "PAGE_ID" },
                        #   "timestamp": 1234567890,
                        #   "optin": {
                        #     "ref": "PASS_THROUGH_PARAM"
                        #   }
                        # }
                        if 'sender' in envelope:
                            # grab our contact
                            urn = URN.from_facebook(envelope['sender']['id'])
                            contact = Contact.from_urn(channel.org, urn)

                        # This is a checkbox-plugin:
                        #   https://developers.facebook.com/docs/messenger-platform/plugin-reference/checkbox-plugin
                        # {
                        #   "recipient": { "id": "PAGE_ID" },
                        #   "timestamp": 1234567890,
                        #   "optin": {
                        #      "ref": "PASS_THROUGH_PARAM",
                        #      "user_ref": "UNIQUE_REF_PARAM"
                        #   }
                        # }
                        elif 'user_ref' in envelope['optin']:
                            urn = URN.from_facebook(URN.path_from_fb_ref(envelope['optin']['user_ref']))
                            contact = Contact.from_urn(channel.org, urn)

                        # no idea what this is, ignore
                        else:
                            status.append("Ignored opt-in, no user_ref or sender")
                            continue

                        if not contact:
                            contact, urn_obj = Contact.get_or_create(channel.org, urn, channel)

                        event = ChannelEvent.create(channel, urn, ChannelEvent.TYPE_REFERRAL, timezone.now(),
                                                    extra=trigger_extra)
                        caught = event.handle()

                        if caught:
                            status.append("Triggered flow for ref: %s" % referrer_id)
                        else:
                            status.append("Ignored opt-in, no trigger for ref: %s" % referrer_id)

                    elif 'message' in envelope or 'postback' in envelope:
                        # ignore echos
                        if 'message' in envelope and envelope['message'].get('is_echo'):
                            status.append("Echo Ignored")
                            continue

                        # check that the recipient is correct for this channel
                        channel_address = str(envelope['recipient']['id'])
                        if channel_address != channel.address:  # pragma: needs cover
                            return HttpResponse("Msg Ignored for recipient id: %s" % channel.address, status=200)

                        content = None
                        postback = None
                        referrer_id = None
                        trigger_extra = None

                        if 'message' in envelope:
                            if 'text' in envelope['message']:
                                content = envelope['message']['text']
                            elif 'attachments' in envelope['message']:
                                urls = []
                                for attachment in envelope['message']['attachments']:
                                    if attachment['payload'] and 'url' in attachment['payload']:
                                        urls.append(attachment['payload']['url'])
                                    elif 'url' in attachment and attachment['url']:
                                        if 'title' in attachment and attachment['title']:
                                            urls.append(attachment['title'])
                                        urls.append(attachment['url'])

                                content = '\n'.join(urls)

                        elif 'postback' in envelope:
                            postback = envelope['postback']['payload']
                            if 'referral' in envelope['postback']:
                                trigger_extra = envelope['postback']['referral']
                                referrer_id = trigger_extra['ref']
                                trigger_extra[ChannelEvent.EXTRA_REFERRER_ID] = referrer_id

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
                                        response = requests.get('https://graph.facebook.com/v2.5/' + six.text_type(sender_id),
                                                                params=dict(fields='first_name,last_name',
                                                                            access_token=channel.config[Channel.CONFIG_AUTH_TOKEN]))

                                        if response.status_code == 200:
                                            user_stats = response.json()
                                            name = ' '.join([user_stats.get('first_name', ''), user_stats.get('last_name', '')])

                                    except Exception as e:
                                        # something went wrong trying to look up the user's attributes, oh well, move on
                                        import traceback
                                        traceback.print_exc()

                                    contact, urn_obj = Contact.get_or_create(channel.org, urn, channel, name)

                        # we received a new message, create and handle it
                        if content:
                            msg_date = datetime.fromtimestamp(envelope['timestamp'] / 1000.0).replace(tzinfo=pytz.utc)
                            msg = Msg.create_incoming(channel, urn, content, date=msg_date, contact=contact)
                            Msg.objects.filter(pk=msg.id).update(external_id=envelope['message']['mid'])
                            status.append("Msg %d accepted." % msg.id)

                        # conversation started with a referrer_id that catches a trigger
                        elif referrer_id:
                            event = ChannelEvent.create(channel, urn, ChannelEvent.TYPE_REFERRAL, timezone.now(),
                                                        extra=trigger_extra)
                            event.handle()

                            status.append("Referral posted with referral id: %s" % referrer_id)

                        # a contact pressed "Get Started", trigger any new conversation triggers
                        elif postback == 'get_started':
                            event = ChannelEvent.create(channel, urn, ChannelEvent.TYPE_NEW_CONVERSATION, timezone.now())
                            event.handle()
                            status.append("Postback handled.")

                        else:
                            status.append("Ignored, content unavailable")

                    elif 'delivery' in envelope and 'mids' in envelope['delivery']:
                        for external_id in envelope['delivery']['mids']:
                            msg = Msg.objects.filter(channel=channel, external_id=external_id, direction=OUTGOING).first()
                            if msg:
                                msg.status_delivered()
                                status.append("Msg %d updated." % msg.id)

                    else:
                        status.append("Messaging entry Ignored")

                return JsonResponse(dict(status=status))

        return JsonResponse(dict(status=["Ignored, unknown msg"]))


class GlobeHandler(BaseChannelHandler):
    courier_url = r'^gl/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$'
    courier_name = 'courier.gl'

    handler_url = r'^globe/(?P<action>receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.globe_handler'

    def get(self, request, *args, **kwargs):
        return HttpResponse("Illegal method, must be POST", status=405)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        # Sample request body for incoming message
        # {
        #     "inboundSMSMessageList":{
        #         "inboundSMSMessage": [
        #             {
        #                 "dateTime": "Fri Nov 22 2013 12:12:13 GMT+0000 (UTC)",
        #                 "destinationAddress": "tel:21581234",
        #                 "messageId": null,
        #                 "message": "Hello",
        #                 "resourceURL": null,
        #                 "senderAddress": "tel:+9171234567"
        #             }
        #         ],
        #         "numberOfMessagesInThisBatch": 1,
        #         "resourceURL": null,
        #         "totalNumberOfPendingMessages": null
        #     }
        #
        # }
        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type='GL').first()
        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=400)

        # parse our JSON
        try:
            body = json.loads(request.body)
        except Exception as e:
            return HttpResponse("Invalid JSON: %s" % six.text_type(e), status=400)

        # needs to contain our message list and inboundSMS message
        if 'inboundSMSMessageList' not in body or 'inboundSMSMessage' not in body['inboundSMSMessageList']:
            return HttpResponse("Invalid request, missing inboundSMSMessageList or inboundSMSMessage", status=400)

        # this is a callback for a message we sent
        if action == 'receive':
            msgs = []
            for inbound_msg in body['inboundSMSMessageList']['inboundSMSMessage']:
                if not all(field in inbound_msg for field in ('dateTime', 'senderAddress', 'message', 'messageId', 'destinationAddress')):
                    return HttpResponse("Missing one of dateTime, senderAddress, message, messageId or destinationAddress in message", status=400)

                try:
                    scheme, destination, display = URN.to_parts(inbound_msg['destinationAddress'])
                except ValueError as v:
                    return HttpResponse("Error parsing destination address: " + str(v), status=400)

                # dates come in the format "2014-04-18 03:54:20.570618" GMT
                sms_date = datetime.strptime(inbound_msg['dateTime'], "%a %b %d %Y %H:%M:%S GMT+0000 (UTC)")
                gmt_date = pytz.timezone('GMT').localize(sms_date)

                # parse our sender address out, it is a URN looking thing
                try:
                    scheme, sender_tel, display = URN.to_parts(inbound_msg['senderAddress'])
                except ValueError as v:
                    return HttpResponse("Error parsing sender address: " + str(v), status=400)

                msg = Msg.create_incoming(channel, URN.from_tel(sender_tel), inbound_msg['message'], date=gmt_date)

                # use an update so there is no race with our handling
                Msg.objects.filter(pk=msg.id).update(external_id=inbound_msg['messageId'])
                msgs.append(msg)

            return HttpResponse("Msgs Accepted: %s" % ", ".join([str(m.id) for m in msgs]))
        else:  # pragma: no cover
            return HttpResponse("Not handled", status=400)


class LineHandler(BaseChannelHandler):
    courier_url = r'^ln/(?P<uuid>[a-z0-9\-]+)/receive$'
    courier_name = 'courier.ln'

    handler_url = r'^line/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.line_handler'

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type='LN').first()
        if not channel:  # pragma: needs cover
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        try:
            data = json.loads(request.body)
            events = data.get('events')

            for item in events:

                if 'source' not in item or 'message' not in item or 'type' not in item.get('message'):
                    return HttpResponse("Missing message, source or type in the event", status=400)

                source = item.get('source')
                message = item.get('message')

                if message.get('type') == 'text':
                    text = message.get('text')
                    user_id = source.get('userId')
                    date = ms_to_datetime(item.get('timestamp'))
                    Msg.create_incoming(channel=channel, urn=URN.from_line(user_id), text=text, date=date)
                    return HttpResponse("Msg Accepted")
                else:  # pragma: needs cover
                    return HttpResponse("Msg Ignored")

        except Exception as e:
            return HttpResponse("Not handled. Error: %s" % e.args, status=400)


class ViberPublicHandler(BaseChannelHandler):
    courier_url = r'^vp/(?P<uuid>[a-z0-9\-]+)/receive$'
    courier_name = 'courier.vp'

    handler_url = r'^viber_public/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.viber_public_handler'

    @classmethod
    def calculate_sig(cls, request_body, auth_token):
        return hmac.new(force_bytes(auth_token.encode('ascii')),
                        msg=request_body, digestmod=hashlib.sha256).hexdigest()

    def get(self, request, *args, **kwargs):
        return HttpResponse("Must be called as a POST", status=405)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg
        request_uuid = kwargs['uuid']

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type='VP').first()
        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=200)

        # parse our response
        try:
            body = json.loads(request.body)
        except Exception as e:
            return HttpResponse("Invalid JSON in POST body: %s" % str(e), status=400)

        # calculate our signature
        signature = ViberPublicHandler.calculate_sig(request.body,
                                                     channel.config[Channel.CONFIG_AUTH_TOKEN])

        # check it against the Viber header
        if signature != request.META.get('HTTP_X_VIBER_CONTENT_SIGNATURE'):
            return HttpResponse("Invalid signature, ignoring request.", status=400)

        if 'event' not in body:
            return HttpResponse("Missing 'event' in request body.", status=400)

        event = body['event']

        # callback from Viber checking this webhook is valid
        if event == 'webhook':
            # {
            #    "event": "webhook",
            #    "timestamp": 4987034606158369000,
            #     "message_token": 1481059480858
            # }
            return HttpResponse("Webhook valid.")

        # user subscribed to this user, create the contact and trigger any associated triggers
        elif event == 'subscribed':
            # {
            #    "event": "subscribed",
            #    "timestamp": 1457764197627,
            #    "user": {
            #        "id": "01234567890A=",
            #        "name": "yarden",
            #        "avatar": "http://avatar_url",
            #        "country": "IL",
            #        "language": "en",
            #        "api_version": 1
            #    },
            #    "message_token": 4912661846655238145
            # }
            viber_id = body['user']['id']
            contact_name = None if channel.org.is_anon else body['user'].get('name')
            urn = URN.from_viber(viber_id)
            Contact.get_or_create(channel.org, urn, channel, contact_name)
            event = ChannelEvent.create(channel, urn, ChannelEvent.TYPE_NEW_CONVERSATION, timezone.now())
            event.handle()
            return HttpResponse("Subscription for contact: %s handled" % viber_id)

        # we currently ignore conversation started events
        elif event == 'conversation_started':
            # {
            #    "event": "conversation_started",
            #    "timestamp": 1457764197627,
            #    "message_token": 4912661846655238145,
            #    "type": "open",
            #    "context": "context information",
            #    "user": {
            #        "id": "01234567890A=",
            #        "name": "yarden",
            #        "avatar": "http://avatar_url",
            #        "country": "IL",
            #        "language": "en",
            #        "api_version": 1
            #    }
            # }
            return HttpResponse("Ignored conversation start")

        # user unsubscribed, we can block them
        elif event == 'unsubscribed':
            # {
            #    "event": "unsubscribed",
            #    "timestamp": 1457764197627,
            #    "user_id": "01234567890A=",
            #    "message_token": 4912661846655238145
            # }
            viber_id = body['user_id']
            contact = Contact.from_urn(channel.org, URN.from_viber(viber_id))
            if not contact:
                return HttpResponse("Unknown contact: %s, ignoring." % viber_id)
            else:
                contact.stop(channel.created_by)
                return HttpResponse("Stopped contact: %s" % viber_id)

        # msg was delivered to the user
        elif event in ['delivered', 'failed']:  # pragma: needs cover
            # {
            #    "event": "delivered",
            #    "timestamp": 1457764197627,
            #    "message_token": 4912661846655238145,
            #    "user_id": "01234567890A="
            # }
            msg = Msg.objects.filter(channel=channel, direction=OUTGOING, external_id=body['message_token']).select_related('channel').first()
            if not msg:
                return HttpResponse("Message with external id of '%s' not found" % body['message_token'])

            if event == 'delivered':
                msg.status_delivered()
            else:
                msg.status_failed()

            # tell Viber we handled this
            return HttpResponse('Msg %d updated' % msg.id)

        # this is a new incoming message
        elif event == 'message':
            # {
            #    "event": "message",
            #    "timestamp": 1457764197627,
            #    "message_token": 4912661846655238145,
            #    "sender": {
            #        "id": "01234567890A=",
            #        "name": "yarden",
            #        "avatar": "http://avatar_url"
            #    },
            #    "message": {
            #        "type": "text",
            #        "text": "a message to the service",
            #        "media": "http://download_url",
            #        "location": {"lat": 50.76891, "lon": 6.11499},
            #        "tracking_data": "tracking data"
            #    }
            # }
            if not all(k in body for k in ['timestamp', 'message_token', 'sender', 'message']):  # pragma: needs cover
                return HttpResponse("Missing one of 'timestamp', 'message_token', 'sender' or 'message' in request body.",
                                    status=400)

            message = body['message']
            msg_date = datetime.utcfromtimestamp(body['timestamp'] / 1000).replace(tzinfo=pytz.utc)
            text = message.get('text', "")
            attachments = []

            # convert different messages types to the right thing
            message_type = message['type']

            if message_type == 'text':
                pass

            elif message_type == 'picture':
                # "media": "http://www.images.com/img.jpg"
                if message.get('media', None):  # pragma: needs cover
                    downloaded_url = channel.org.download_and_save_media(Request('GET', message['media']))
                    attachments.append('%s:%s' % (Msg.MEDIA_IMAGE, downloaded_url))

            elif message_type == 'video':  # pragma: needs cover
                # "media": "http://www.images.com/video.mp4"
                if message.get('media', None):  # pragma: needs cover
                    downloaded_url = channel.org.download_and_save_media(Request('GET', message['media']))
                    attachments.append('%s:%s' % (Msg.MEDIA_VIDEO, downloaded_url))

            elif message_type == 'contact':
                # "contact": {
                #     "name": "Alex",
                #     "phone_number": "+972511123123"
                # }
                text = "%s: %s" % (message['contact']['name'], message['contact']['phone_number'])

            elif message_type == 'url':
                # "media": "http://www.website.com/go_here"
                text = message['media']

            elif message_type == 'location':
                # "location": {"lat": "37.7898", "lon": "-122.3942"}
                attachments.append('%s:%s,%s' % (Msg.MEDIA_GPS, message['location']['lat'], message['location']['lon']))

            else:  # pragma: needs cover
                return HttpResponse("Unknown message type: %s" % message_type, status=400)

            if not text and not attachments:
                return HttpResponse("Missing text or media in message in request body.", status=400)

            # get or create our contact with any name sent in
            urn = URN.from_viber(body['sender']['id'])

            contact_name = None if channel.org.is_anon else body['sender'].get('name')
            contact, urn_obj = Contact.get_or_create(channel.org, urn, channel, contact_name)

            msg = Msg.create_incoming(channel, urn, text, contact=contact, date=msg_date,
                                      external_id=body['message_token'], attachments=attachments)
            return HttpResponse('Msg Accepted: %d' % msg.id)

        else:  # pragma: no cover
            return HttpResponse("Not handled, unknown event: %s" % event, status=400)


class FCMHandler(BaseChannelHandler):
    handler_url = r'^fcm/(?P<action>register|receive)/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.fcm_handler'

    def get(self, request, *args, **kwargs):
        return HttpResponse("Must be called as a POST", status=405)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg

        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type='FCM').first()

        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        action = kwargs['action']

        try:
            if action == 'receive':
                if not self.get_param('from') or not self.get_param('msg') or not self.get_param('fcm_token'):
                    return HttpResponse("Missing parameters, requires 'from', 'msg' and 'fcm_token'", status=400)

                date = self.get_param('date', self.get_param('time', None))
                if date:
                    date = json_date_to_datetime(date)

                fcm_urn = URN.from_fcm(self.get_param('from'))
                fcm_token = self.get_param('fcm_token')
                name = self.get_param('name', None)
                contact, urn_obj = Contact.get_or_create(channel.org, fcm_urn, channel, name, fcm_token)

                sms = Msg.create_incoming(channel, fcm_urn, self.get_param('msg'), date=date, contact=contact)
                return HttpResponse("Msg Accepted: %d" % sms.id)

            elif action == 'register':
                if not self.get_param('urn') or not self.get_param('fcm_token'):
                    return HttpResponse("Missing parameters, requires 'urn' and 'fcm_token'", status=400)

                fcm_urn = URN.from_fcm(self.get_param('urn'))
                fcm_token = self.get_param('fcm_token')
                name = self.get_param('name', None)

                contact, urn_obj = Contact.get_or_create(channel.org, fcm_urn, channel, name, fcm_token)

                return HttpResponse(json.dumps({'contact_uuid': contact.uuid}), content_type='application/json')

            else:  # pragma: no cover
                return HttpResponse("Not handled, unknown action", status=400)

        except Exception as e:  # pragma: no cover
            return HttpResponse(e.args, status=400)


class TwitterHandler(BaseChannelHandler):
    courier_url = r'^twt/(?P<uuid>[a-z0-9\-]+)/receive$'
    courier_name = 'courier.twt'

    handler_url = r'^twitter/(?P<uuid>[a-z0-9\-]+)/?$'
    handler_name = 'handlers.twitter_handler'

    def get(self, request, *args, **kwargs):
        crc_token = request.GET['crc_token']

        channel = Channel.objects.filter(uuid=kwargs['uuid'], is_active=True, channel_type='TWT').first()
        if not channel:
            return HttpResponse("No such Twitter channel", status=400)

        consumer_secret = channel.config['api_secret']
        resp_token = generate_twitter_signature(crc_token, consumer_secret)

        return JsonResponse({'response_token': resp_token}, status=200)

    def post(self, request, *args, **kwargs):
        channel = Channel.objects.filter(uuid=kwargs['uuid'], is_active=True, channel_type='TWT').first()
        if not channel:
            return HttpResponse("No such Twitter channel", status=400)

        channel_config = channel.config

        # validate that request has come from Twitter
        expected_signature = request.META['HTTP_X_TWITTER_WEBHOOKS_SIGNATURE']
        calculated_signature = generate_twitter_signature(request.body, channel_config['api_secret'])

        if not constant_time_compare(expected_signature, calculated_signature):
            return HttpResponse("Invalid request signature", status=400)

        body = json.loads(request.body)
        dm_events = body.get('direct_message_events', [])
        users = body.get('users', {})

        msgs = []
        for dm_event in dm_events:
            if dm_event['type'] == 'message_create':
                sender_id = dm_event['message_create']['sender_id']

                # check that this isn't a message that we sent
                if int(sender_id) == channel_config['handle_id']:
                    continue

                urn = URN.from_twitterid(users[sender_id]['id'], users[sender_id]['screen_name'])
                name = None if channel.org.is_anon else users[sender_id]['name']
                contact, urn_obj = Contact.get_or_create(channel.org, urn, channel, name)

                external_id = dm_event['id']
                created_on = ms_to_datetime(int(dm_event['created_timestamp']))
                text = dm_event['message_create']['message_data']['text']

                msgs.append(Msg.create_incoming(channel, urn, text, date=created_on, contact=contact, external_id=external_id))

        return HttpResponse("Accepted %d messages" % len(msgs), status=200)
