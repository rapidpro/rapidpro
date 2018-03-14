# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import six

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.utils.encoding import force_text
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View
from temba.channels.models import Channel, ChannelLog, ChannelType
from temba.ivr.models import IVRCall
from temba.utils.http import HttpEvent
from temba.flows.models import Flow, FlowRun, FlowStep


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

        if not(channel.is_active and channel.org):
            return HttpResponse("No channel found", status=400)

        channel_type = channel.channel_type
        ivr_protocol = Channel.get_type_from_code(channel_type).ivr_protocol
        client = channel.get_ivr_client()

        request_body = force_text(request.body)
        request_method = request.method
        request_path = request.get_full_path()

        if ivr_protocol == ChannelType.IVRProtocol.IVR_PROTOCOL_TWIML and request.POST.get('hangup', 0):
            if not request.user.is_anonymous():
                user_org = request.user.get_org()
                if user_org and user_org.pk == call.org.pk:
                    client.hangup(call)
                    return HttpResponse(json.dumps(dict(status='Canceled')), content_type="application/json")
                else:  # pragma: no cover
                    return HttpResponse("Not found", status=404)

        input_redirect = '1' == request.GET.get('input_redirect', '0')
        if client.validate(request):
            status = None
            duration = None
            if ivr_protocol == ChannelType.IVRProtocol.IVR_PROTOCOL_TWIML:
                status = request.POST.get('CallStatus', None)
                duration = request.POST.get('CallDuration', None)
            elif ivr_protocol == ChannelType.IVRProtocol.IVR_PROTOCOL_NCCO:
                if request_body:
                    body_json = json.loads(request_body)
                    status = body_json.get('status', None)
                    duration = body_json.get('duration', None)

                # force in progress call status for fake (input) redirects
                if input_redirect:
                    status = 'answered'

            call.update_status(status, duration, channel_type)  # update any calls we have spawned with the same
            call.save()

            resume = request.GET.get('resume', 0)
            user_response = request.POST.copy()

            hangup = False
            saved_media_url = None
            text = None
            media_url = None
            has_event = False

            if ivr_protocol == ChannelType.IVRProtocol.IVR_PROTOCOL_TWIML:

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

            elif ivr_protocol == ChannelType.IVRProtocol.IVR_PROTOCOL_NCCO:
                if request_body:
                    body_json = json.loads(request_body)
                    media_url = body_json.get('recording_url', None)

                    if media_url:
                        cache.set('last_call:media_url:%d' % call.pk, media_url, None)

                    media_url = cache.get('last_call:media_url:%d' % call.pk, None)
                    text = body_json.get('dtmf', None)
                    if input_redirect:
                        text = None

                has_event = '1' == request.GET.get('has_event', '0')
                save_media = '1' == request.GET.get('save_media', '0')
                if media_url:
                    if save_media:
                        saved_media_url = client.download_media(call, media_url)
                        cache.delete('last_call:media_url:%d' % call.pk)
                    else:
                        response_msg = 'Saved media url'
                        response = dict(message=response_msg)

                        event = HttpEvent(request_method, request_path, request_body, 200, json.dumps(response))
                        ChannelLog.log_ivr_interaction(call, response_msg, event)
                        return JsonResponse(response)

            if not has_event and call.status not in IVRCall.DONE or hangup:
                if call.is_ivr():
                    response = Flow.handle_call(call, text=text, saved_media_url=saved_media_url, hangup=hangup, resume=resume)
                    event = HttpEvent(request_method, request_path, request_body, 200, six.text_type(response))
                    if ivr_protocol == ChannelType.IVRProtocol.IVR_PROTOCOL_NCCO:
                        ChannelLog.log_ivr_interaction(call, "Incoming request for call", event)

                        # TODO: what's special here that this needs to be different?
                        return JsonResponse(json.loads(six.text_type(response)), safe=False)

                    ChannelLog.log_ivr_interaction(call, "Incoming request for call", event)
                    return HttpResponse(six.text_type(response), content_type="text/xml; charset=utf-8")
            else:

                if call.status == IVRCall.COMPLETED:
                    # if our call is completed, hangup
                    runs = FlowRun.objects.filter(connection=call)
                    for run in runs:
                        if not run.is_completed():
                            final_step = FlowStep.objects.filter(run=run).order_by('-arrived_on').first()
                            run.set_completed(final_step=final_step)

                response = dict(description="Updated call status",
                                call=dict(status=call.get_status_display(), duration=call.duration))

                event = HttpEvent(request_method, request_path, request_body, 200, json.dumps(response))
                ChannelLog.log_ivr_interaction(call, "Updated call status", event)
                return JsonResponse(response)

        else:  # pragma: no cover

            error = 'Invalid request signature'
            event = HttpEvent(request_method, request_path, request_body, 200, error)
            ChannelLog.log_ivr_interaction(call, error, event, is_error=True)
            # raise an exception that things weren't properly signed
            raise ValidationError(error)

        return JsonResponse(dict(message="Unhandled"))  # pragma: no cover
