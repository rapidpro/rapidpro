from __future__ import absolute_import, unicode_literals

from django.conf.urls import url
from rest_framework.urlpatterns import format_suffix_patterns
from .views import api, ApiExplorerView, FlowRunEndpoint, MessageEndpoint


urlpatterns = [
    url(r'^$', api, name='api.v2'),
    url(r'^/explorer/$', ApiExplorerView.as_view(), name='api.v2.explorer'),
    url(r'^/messages$', MessageEndpoint.as_view(), name='api.v2.messages'),
    url(r'^/runs$', FlowRunEndpoint.as_view(), name='api.v2.runs'),
]

urlpatterns = format_suffix_patterns(urlpatterns, allowed=['json', 'api'])
