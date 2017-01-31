from __future__ import unicode_literals

import json
import six

from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View

from temba.ivr.models import IVRCall
from temba.flows.models import Flow, FlowRun


class CallHandler(View):

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super(CallHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):  # pragma: no cover
        return HttpResponse("ILLEGAL METHOD")

    def post(self, request, *args, **kwargs):
        call = IVRCall.objects.filter(pk=kwargs['pk']).first()

        if not call:
            return HttpResponse("Not found", status=404)

        client = call.channel.get_ivr_client()
        if request.POST.get('hangup', 0):
            if not request.user.is_anonymous():
                user_org = request.user.get_org()
                if user_org and user_org.pk == call.org.pk:
                    client.calls.hangup(call.external_id)
                    return HttpResponse(json.dumps(dict(status='Canceled')), content_type="application/json")
                else:  # pragma: no cover
                    return HttpResponse("Not found", status=404)

        if client.validate(request):
            status = request.POST.get('CallStatus', None)
            duration = request.POST.get('CallDuration', None)
            call.update_status(status, duration)
            call.save()

            # figure out if this is a callback due to an empty gather
            is_empty = '1' == request.GET.get('empty', '0')
            resume = request.GET.get('resume', 0)
            user_response = request.POST.copy()

            # if the user pressed pound, then record no digits as the input
            if is_empty:
                user_response['Digits'] = ''

            hangup = 'hangup' == user_response.get('Digits', None)

            if call.status not in IVRCall.DONE or hangup:
                response = Flow.handle_call(call, user_response, hangup=hangup, resume=resume)
                return HttpResponse(six.text_type(response))
            else:
                if call.status == IVRCall.COMPLETED:
                    # if our call is completed, hangup
                    run = FlowRun.objects.filter(session=call).first()
                    if run:
                        run.set_completed()
                return JsonResponse(dict(message="Updated call status",
                                         call=dict(status=call.get_status_display(), duration=call.duration)))

        else:  # pragma: no cover
            # raise an exception that things weren't properly signed
            raise ValidationError("Invalid request signature")

        return JsonResponse(dict(message="Unhandled"))  # pragma: no cover
