from __future__ import absolute_import, unicode_literals

import six

from django.http import HttpResponse, JsonResponse, Http404
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
        except:
            raise Http404()  

        obj.ensure_current_version()
        languages = [lang.as_json() for lang in obj.org.languages.all().order_by('orgs')]
        try:
            channel_countries = obj.org.get_channel_countries()
        except Exception:  
            channel_countries = []
        channels = [dict(uuid=chan.uuid, name=u"%s: %s" % (chan.get_channel_type_display(), chan.get_address_display())) for chan in obj.org.channels.filter(is_active=True)]
        response = {
            'flow': obj.as_json(expand_contacts=True),
            'languages': languages,
            'channel_countries': channel_countries,
            'channels': channels,
        }
        return JsonResponse(response, json_dumps_params=params)
            
                
            
            
