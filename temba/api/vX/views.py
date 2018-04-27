from __future__ import absolute_import, unicode_literals

import six
import json

from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse, Http404
from django.views import View
from temba.flows.models import Flow

class FlowEndpoint(View):

    def get(self, request):
        obj = None
        params = {}
        try:
            uuid = request.GET.get('uuid')
            if uuid:
                obj = Flow.objects.get(uuid=uuid)
            if 'pretty' in request.GET:
                params['indent'] = 2
            assert(obj)
            obj.ensure_current_version()
        except:
            raise Http404()  

        flow = obj.as_json(expand_contacts=True)
        flow['id'] = obj.id
        response = { 'flow': flow }
        return JsonResponse(response, json_dumps_params=params)
    
    def post(self, request):               
        obj = None
        org = request.user.get_org()
        user = request.user

        params = {}
        if 'pretty' in request.GET:
            params['indent'] = 2

        try:
            data = json.loads(request.body)
            flow = data['flow']
        except:
            return HttpResponseBadRequest('Could not load flow from request')

        flow_type = flow.get('flow_type')
        flow_uuid = flow.get('metadata', {}).get('uuid')
        flow_name = flow.get('metadata', {}).get('name')
        expires_after_minutes = flow.get('metadata', {}).get('expires')
        if flow_uuid:
            try:
                obj = Flow.objects.get(uuid=flow_uuid)
            except:
                pass
        if obj:
            obj.name = flow_name
            obj.expires_after_minutes = expires_after_minutes
            obj.save()
        else:
            obj = Flow.create(
                org,
                user,
                flow_name,
                flow_type=flow_type,    
                expires_after_minutes=expires_after_minutes
            )
        obj.update(flow)
            
        flow = obj.as_json(expand_contacts=True)
        flow['id'] = obj.id
        response = { 'flow': flow }
        return JsonResponse(response, json_dumps_params=params)
