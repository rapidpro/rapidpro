import time
import uuid

import jwt
import requests
from twilio.base.exceptions import TwilioRestException
from twilio.request_validator import RequestValidator
from twilio.rest.api import Api

from django.conf import settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import ChannelLog
from temba.channels.types.nexmo.client import Client
from temba.contacts.models import URN, Contact
from temba.flows.models import Flow
from temba.ivr.models import IVRCall
from temba.utils import json
from temba.utils.http import HttpEvent
from temba.utils.twilio import TembaTwilioRestClient


class IVRException(Exception):
    pass


class NexmoClient(Client):
    def __init__(self, api_key, api_secret, org):
        self.org = org

        super().__init__(api_key, api_secret)

    def validate(self, request):
        return True

    def start_call(self, call, to, from_, status_callback):
        if not settings.SEND_CALLS:
            raise ValueError("SEND_CALLS set to False, skipping call start")

        url = "https://%s%s" % (self.org.get_brand_domain(), reverse("ivr.ivrcall_handle", args=[call.pk]))

        params = dict()
        params["answer_url"] = [url]
        params["answer_method"] = "POST"
        params["from"] = dict(type="phone", number=from_.strip("+"))
        params["event_url"] = ["%s?has_event=1" % url]
        params["event_method"] = "POST"

        try:
            response = self.base.create_call(params=params)
            call_uuid = response.get("uuid", None)
            call.external_id = str(call_uuid)

            # the call was successfully sent to the IVR provider
            call.status = IVRCall.WIRED
            call.save()

        except Exception as e:
            event = HttpEvent("POST", "https://api.nexmo.com/v1/calls", json.dumps(params), response_body=str(e))
            ChannelLog.log_ivr_interaction(call, "Call start failed", event, is_error=True)

            call.status = IVRCall.FAILED
            call.save()

            raise IVRException(_("Nexmo call failed, with error %s") % str(e))

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
                time.sleep(0.250)

        content_type, downloaded = self.org.save_response_media(response)

        if content_type:
            # log that we downloaded it to our own url
            request = response.request
            event = HttpEvent(request.method, request.url, request.body, response.status_code, downloaded)
            ChannelLog.log_ivr_interaction(call, "Downloaded media", event)

            return "%s:%s" % (content_type, downloaded)

        return None

    def hangup(self, call):
        self.base.update_call(call.external_id, action="hangup", call_id=call.external_id)

    def download_recording(self, url, params=None, **kwargs):
        return requests.get(url, params=params, headers=self.gen_headers())

    def gen_headers(self):
        iat = int(time.time())

        payload = dict(self.base.auth_params)
        payload.setdefault("iat", iat)
        payload.setdefault("exp", iat + 60)
        payload.setdefault("jti", str(uuid.uuid4()))

        token = jwt.encode(payload, self.base.private_key, algorithm="RS256")

        return dict(self.base.headers, Authorization=b"Bearer " + force_bytes(token))


class TwilioClient(TembaTwilioRestClient):
    def __init__(self, account_sid, token, org, base=None, **kwargs):
        self.org = org
        super().__init__(account_sid, token, **kwargs)
        if base:
            custom_api = Api(self)
            custom_api.base_url = base
            self._api = custom_api

    def start_call(self, call, to, from_, status_callback):
        if not settings.SEND_CALLS:
            raise ValueError("SEND_CALLS set to False, skipping call start")

        params = dict(to=to, from_=call.channel.address, url=status_callback, status_callback=status_callback)

        try:
            twilio_call = self.api.calls.create(**params)
            call.external_id = str(twilio_call.sid)

            # the call was successfully sent to the IVR provider
            call.status = IVRCall.WIRED
            call.save()

            for event in self.events:
                ChannelLog.log_ivr_interaction(call, "Started call", event)

        except TwilioRestException as twilio_error:  # pragma: no cover
            message = "Twilio Error: %s" % twilio_error.msg
            if twilio_error.code == 20003:
                message = _("Could not authenticate with your Twilio account. Check your token and try again.")

            event = HttpEvent("POST", "https://api.nexmo.com/v1/calls", json.dumps(params), response_body=str(message))
            ChannelLog.log_ivr_interaction(call, "Call start failed", event, is_error=True)

            call.status = IVRCall.FAILED
            call.save()

            raise IVRException(message)

    def validate(self, request):  # pragma: needs cover
        validator = RequestValidator(self.auth[1])
        signature = request.META.get("HTTP_X_TWILIO_SIGNATURE", "")

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
        while attempts < 120:
            response = requests.get(media_url, stream=True, auth=self.auth)

            # in some cases Twilio isn't ready for us to fetch the recording URL yet, if we get a 404
            # sleep for a bit then try again for up to a minute
            if response.status_code == 200:
                break
            else:
                attempts += 1
                time.sleep(0.5)

        content_type, downloaded = self.org.save_response_media(response)
        if content_type:
            return "%s:%s" % (content_type, downloaded)

        return None  # pragma: needs cover

    def hangup(self, call):
        twilio_call = self.api.calls.get(call.external_id).update(status="completed")
        for event in self.events:
            ChannelLog.log_ivr_interaction(call, "Hung up call", event)
        return twilio_call


class VerboiceClient:  # pragma: needs cover
    def __init__(self, channel):
        self.endpoint = "https://verboice.instedd.org/api/call"

        config = channel.config
        self.auth = (config.get("username", None), config.get("password", None))

        # this is the verboice channel, not our channel
        self.verboice_channel = config.get("channel", None)

    def validate(self, request):
        # verboice isn't smart here
        return True

    def start_call(self, call, to, from_, status_callback):
        if not settings.SEND_CALLS:
            raise ValueError("SEND_CALLS set to False, skipping call start")

        channel = call.channel
        Contact.get_or_create(channel.org, URN.from_tel(to), channel)

        # Verboice differs from Twilio in that they expect the first block of twiml up front
        payload = str(Flow.handle_call(call))

        # now we can post that to verboice
        url = "%s?%s" % (self.endpoint, urlencode(dict(channel=self.verboice_channel, address=to)))
        response = requests.post(url, data=payload, auth=self.auth).json()

        if "call_id" not in response:
            call.status = IVRCall.FAILED
            call.save()

            raise IVRException(_("Verboice connection failed."))

        # store the verboice call id in our IVRCall
        call.external_id = response["call_id"]

        # the call was successfully sent to the IVR provider
        call.status = IVRCall.WIRED
        call.save()
