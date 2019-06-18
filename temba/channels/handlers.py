import logging

from twilio.twiml.voice_response import VoiceResponse

from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils.encoding import force_text
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View

from temba.channels.models import Channel, ChannelLog
from temba.contacts.models import URN, Contact
from temba.flows.models import Flow, FlowRun
from temba.orgs.models import NEXMO_UUID
from temba.triggers.models import Trigger
from temba.utils import json
from temba.utils.http import HttpEvent

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
        return super().dispatch(request, *args, **kwargs)

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


class TWIMLCallHandler(BaseChannelHandler):
    handler_url = r"^twiml_api/(?P<uuid>[a-z0-9\-]+)/?$"
    handler_name = "handlers.twiml_api_handler"

    def get(self, request, *args, **kwargs):  # pragma: no cover
        return HttpResponse("ILLEGAL METHOD")

    def post(self, request, *args, **kwargs):
        from twilio.request_validator import RequestValidator
        from temba.flows.models import FlowSession

        signature = request.META.get("HTTP_X_TWILIO_SIGNATURE", "")
        url = "https://" + request.get_host() + "%s" % request.get_full_path()

        channel_uuid = kwargs.get("uuid")
        call_sid = self.get_param("CallSid")
        direction = self.get_param("Direction")
        status = self.get_param("CallStatus")
        to_number = self.get_param("To")
        to_country = self.get_param("ToCountry")
        from_number = self.get_param("From")

        # Twilio sometimes sends un-normalized numbers
        if to_number and not to_number.startswith("+") and to_country:  # pragma: no cover
            to_number, valid = URN.normalize_number(to_number, to_country)

        # see if it's a twilio call being initiated
        if to_number and call_sid and direction == "inbound" and status == "ringing":

            # find a channel that knows how to answer twilio calls
            channel = self.get_ringing_channel(uuid=channel_uuid)
            if not channel:
                response = VoiceResponse()
                response.say("Sorry, there is no channel configured to take this call. Goodbye.")
                response.hangup()
                return HttpResponse(str(response))

            org = channel.org

            if self.get_channel_type() == "T" and not org.is_connected_to_twilio():
                return HttpResponse("No Twilio account is connected", status=400)

            client = self.get_client(channel=channel)
            validator = RequestValidator(client.auth[1])
            signature = request.META.get("HTTP_X_TWILIO_SIGNATURE", "")

            url = "https://%s%s" % (request.get_host(), request.get_full_path())

            if validator.validate(url, request.POST, signature):
                from temba.ivr.models import IVRCall

                # find a contact for the one initiating us
                urn = URN.from_tel(from_number)
                contact, urn_obj = Contact.get_or_create(channel.org, urn, channel)

                flow = Trigger.find_flow_for_inbound_call(contact)

                if flow:
                    call = IVRCall.create_incoming(channel, contact, urn_obj, call_sid)
                    session = FlowSession.create(contact, connection=call)

                    call.update_status(
                        request.POST.get("CallStatus", None), request.POST.get("CallDuration", None), "T"
                    )
                    call.save()

                    FlowRun.create(flow, contact, session=session, connection=call)
                    response = Flow.handle_call(call)
                    return HttpResponse(str(response))

                else:

                    # we don't have an inbound trigger to deal with this call.
                    response = channel.generate_ivr_response()

                    # say nothing and hangup, this is a little rude, but if we reject the call, then
                    # they'll get a non-working number error. We send 'busy' when our server is down
                    # so we don't want to use that here either.
                    response.say("")
                    response.hangup()

                    # if they have a missed call trigger, fire that off
                    Trigger.catch_triggers(contact, Trigger.TYPE_MISSED_CALL, channel)

                    # either way, we need to hangup now
                    return HttpResponse(str(response))

        # check for call progress events, these include post-call hangup notifications
        if request.POST.get("CallbackSource", None) == "call-progress-events":
            if call_sid:
                from temba.ivr.models import IVRCall

                call = IVRCall.objects.filter(external_id=call_sid).first()
                if call:
                    call.update_status(
                        request.POST.get("CallStatus", None), request.POST.get("CallDuration", None), "TW"
                    )
                    call.save()
                    return HttpResponse("Call status updated")
            return HttpResponse("No call found")

        return HttpResponse("Not Handled, unknown action", status=400)  # pragma: no cover

    def get_ringing_channel(self, uuid):
        return Channel.objects.filter(
            uuid=uuid, channel_type=self.get_channel_type(), role__contains="A", is_active=True
        ).first()

    def get_receive_channel(self, uuid=None):  # pragma: no cover
        return Channel.objects.filter(uuid=uuid, is_active=True, channel_type=self.get_channel_type()).first()

    def get_client(self, channel):
        return channel.get_ivr_client()

    def get_channel_type(self):  # pragma: no cover
        return "TW"


class TwilioCallHandler(TWIMLCallHandler):

    handler_url = r"^twilio/(?P<action>receive|status|voice)/(?P<uuid>[a-z0-9\-]+)/?$"
    handler_name = "handlers.twilio_handler"

    def get_channel_type(self):
        return "T"


class NexmoCallHandler(BaseChannelHandler):

    handler_url = r"^nexmo/(?P<action>answer|event)/(?P<uuid>[a-z0-9\-]+)/$"
    handler_name = "handlers.nexmo_call_handler"

    @csrf_exempt
    def dispatch(self, request, *args, **kwargs):
        return super(BaseChannelHandler, self).dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.flows.models import FlowSession
        from temba.ivr.models import IVRCall

        action = kwargs["action"].lower()

        request_body = force_text(request.body)
        request_path = request.get_full_path()
        request_method = request.method

        request_uuid = kwargs["uuid"]

        if action == "event":
            if not request_body:
                return HttpResponse("")

            body_json = json.loads(request_body)
            status = body_json.get("status", None)
            duration = body_json.get("duration", None)
            call_uuid = body_json.get("uuid", None)
            conversation_uuid = body_json.get("conversation_uuid", None)

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

            response = dict(
                description="Updated call status", call=dict(status=call.get_status_display(), duration=call.duration)
            )

            event = HttpEvent(request_method, request_path, request_body, 200, json.dumps(response))
            ChannelLog.log_ivr_interaction(call, "Updated call status", event)

            if call.status == IVRCall.COMPLETED:
                # if our call is completed, hangup
                runs = FlowRun.objects.filter(connection=call)
                for run in runs:
                    if not run.is_completed():
                        run.set_completed(exit_uuid=None)

            return JsonResponse(response)

        if action == "answer":
            if not request_body:
                return HttpResponse("")

            body_json = json.loads(request_body)
            from_number = body_json.get("from", None)
            channel_number = body_json.get("to", None)
            external_id = body_json.get("conversation_uuid", None)

            if not from_number or not channel_number or not external_id:
                return HttpResponse("Missing parameters, Ignoring")

            # look up the channel
            address_q = Q(address=channel_number) | Q(address=("+" + channel_number))
            channel = Channel.objects.filter(address_q).filter(is_active=True, channel_type="NX").first()

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
                call = IVRCall.create_incoming(channel, contact, urn_obj, external_id)
                session = FlowSession.create(contact, connection=call)

                FlowRun.create(flow, contact, session=session, connection=call)
                response = Flow.handle_call(call)
                channel_type = channel.channel_type
                call.update_status("answered", None, channel_type)

                event = HttpEvent(request_method, request_path, request_body, 200, str(response))
                ChannelLog.log_ivr_interaction(call, "Incoming request for call", event)
                return JsonResponse(json.loads(str(response)), safe=False)
            else:
                # we don't have an inbound trigger to deal with this call.
                response = channel.generate_ivr_response()

                # say nothing and hangup, this is a little rude, but if we reject the call, then
                # they'll get a non-working number error. We send 'busy' when our server is down
                # so we don't want to use that here either.
                response.say("")
                response.hangup()

                # if they have a missed call trigger, fire that off
                Trigger.catch_triggers(contact, Trigger.TYPE_MISSED_CALL, channel)

                # either way, we need to hangup now
                return JsonResponse(json.loads(str(response)), safe=False)


class CourierHandler(View):
    channel_name = None

    def get(self, request, *args, **kwargs):  # pragma: no cover
        logger.error(
            "%s courier handler called in RapidPro with URL: %s" % (self.channel_name, request.get_full_path())
        )
        return HttpResponse("%s handling only implemented in Courier" % self.channel_name, status=404)

    def post(self, request, *args, **kwargs):  # pragma: no cover
        logger.error(
            "%s courier handler called in RapidPro with URL: %s" % (self.channel_name, request.get_full_path())
        )
        return HttpResponse("%s handling only implemented in Courier" % self.channel_name, status=404)
