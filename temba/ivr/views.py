from __future__ import unicode_literals

import json
import mimetypes

from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.temp import NamedTemporaryFile
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View
from temba.channels.models import Channel
from temba.utils import build_json_response
from temba.flows.models import Flow, FlowRun
from .models import IVRCall, IN_PROGRESS, COMPLETED, RINGING


class CallHandler(View):

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super(CallHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        call = IVRCall.objects.filter(pk=kwargs['pk']).first()

        if not call:
            return HttpResponse("Not found", status=404)

        channel = call.channel
        channel_type = channel.channel_type
        client = channel.get_ivr_client()

        if channel_type in [Channel.TYPE_TWILIO, Channel.TYPE_VERBOICE] and request.REQUEST.get('hangup', 0):
            if not request.user.is_anonymous():
                user_org = request.user.get_org()
                if user_org and user_org.pk == call.org.pk:
                    client.calls.hangup(call.external_id)
                    return HttpResponse(json.dumps(dict(status='Canceled')), content_type="application/json")
                else:
                    return HttpResponse("Not found", status=404)

        if client.validate(request):
            status = None
            duration = None
            if channel_type in [Channel.TYPE_TWILIO, Channel.TYPE_VERBOICE]:
                status = request.POST.get('CallStatus', None)
                duration = request.POST.get('CallDuration', None)
            elif channel_type in [Channel.TYPE_NEXMO]:
                status = request.POST.get('status', None)
                duration = request.POST.get('call-duration', None)

            call.update_status(status, duration, channel_type)

            # update any calls we have spawned with the same
            for child in call.child_calls.all():
                child.update_status(status, duration, channel_type)
                child.save()

            call.save()

            user_response = request.POST.copy()

            hangup = False
            saved_media_url = None

            # figure out if this is a callback due to an empty gather
            is_empty = '1' == request.GET.get('empty', '0')

            # if the user pressed pound, then record no digits as the input
            if is_empty:
                user_response['Digits'] = ''

            if channel_type in [Channel.TYPE_TWILIO, Channel.TYPE_VERBOICE]:

                hangup = 'hangup' == user_response.get('Digits', None)

                media_url = user_response.get('RecordingUrl', None)
                # if we've been sent a recording, go grab it
                if media_url:
                    saved_media_url = client.download_media(media_url)

            elif channel_type in [Channel.TYPE_NEXMO]:
                user_recording = request.FILES.get('UserRecording', None)
                if user_recording is not None:
                    content_type = user_recording.content_type
                    extension = mimetypes.guess_extension(content_type)
                    extension = extension.strip('.')

                    temp = NamedTemporaryFile(delete=True)
                    for chunk in user_recording.chunks():
                        temp.write(chunk)
                    temp.flush()

                    saved_media_url = '%s:%s' % (content_type, client.org.save_media(File(temp), extension))

            # parse the user response
            text = user_response.get('Digits', None)

            if call.status in [IN_PROGRESS, RINGING] or hangup:
                if call.is_flow():
                    response = Flow.handle_call(call, text=text, saved_media_url=saved_media_url, hangup=hangup)
                    return HttpResponse(unicode(response))
            else:
                if call.status == COMPLETED:
                    # if our call is completed, hangup
                    run = FlowRun.objects.filter(call=call).first()
                    if run:
                        run.set_completed()
                return build_json_response(dict(message="Updated call status"))

        else:  # pragma: no cover
            # raise an exception that things weren't properly signed
            raise ValidationError("Invalid request signature")

        return build_json_response(dict(message="Unhandled"))
