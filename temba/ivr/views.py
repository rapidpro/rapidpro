from __future__ import unicode_literals

import json

from django.core.cache import cache
from django.core.exceptions import ValidationError
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

        print "====="
        print request.body
        print "====="
        print request.REQUEST
        print "====="

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

        input_redirect = '1' == request.GET.get('input_redirect', '0')
        if client.validate(request):
            status = None
            duration = None
            if channel_type in [Channel.TYPE_TWILIO, Channel.TYPE_VERBOICE]:
                status = request.POST.get('CallStatus', None)
                duration = request.POST.get('CallDuration', None)
            elif channel_type in [Channel.TYPE_NEXMO]:
                if request.body:
                    body_json = json.loads(request.body)
                    status = body_json.get('status', None)
                    duration = body_json.get('duration', None)

                # force in progress call status for fake (input) redirects
                if input_redirect:
                    status = 'answered'

            call.update_status(status, duration, channel_type)

            # update any calls we have spawned with the same
            for child in call.child_calls.all():
                child.update_status(status, duration, channel_type)
                child.save()

            call.save()

            user_response = request.POST.copy()

            hangup = False
            saved_media_url = None
            text = None
            media_url = None

            if channel_type in [Channel.TYPE_TWILIO, Channel.TYPE_VERBOICE]:

                # figure out if this is a callback due to an empty gather
                is_empty = '1' == request.GET.get('empty', '0')

                # if the user pressed pound, then record no digits as the input
                if is_empty:
                    user_response['Digits'] = ''

                hangup = 'hangup' == user_response.get('Digits', None)

                media_url = user_response.get('RecordingUrl', None)
                # if we've been sent a recording, go grab it
                if media_url:
                    saved_media_url = client.download_media(media_url)

                # parse the user response
                text = user_response.get('Digits', None)

            elif channel_type in [Channel.TYPE_NEXMO]:
                if request.body:
                    body_json = json.loads(request.body)
                    media_url = body_json.get('recording_url', None)

                    if media_url:
                        cache.set('last_call:media_url:%d' % call.pk, media_url, None)

                    media_url = cache.get('last_call:media_url:%d' % call.pk, None)
                    text = body_json.get('dtmf', None)
                    if input_redirect:
                        text = None

                has_event = '1' == request.GET.get('has_event', '0')
                if has_event:
                    print 'event======'
                    return HttpResponse(unicode(''))

                save_media = '1' == request.GET.get('save_media', '0')
                if media_url:
                    if save_media:
                        saved_media_url = client.download_media(media_url)
                        cache.delete('last_call:media_url:%d' % call.pk)
                    else:
                        return HttpResponse(unicode('media URL saved'))

            if call.status in [IN_PROGRESS, RINGING] or hangup:
                if call.is_flow():
                    response = Flow.handle_call(call, text=text, saved_media_url=saved_media_url, hangup=hangup)
                    if channel_type in [Channel.TYPE_NEXMO]:
                        print "++++"
                        print unicode(response)
                        print "++++"
                        return build_json_response(json.loads(unicode(response)))

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
