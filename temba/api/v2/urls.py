from __future__ import absolute_import, unicode_literals

from django.conf.urls import url
from rest_framework.urlpatterns import format_suffix_patterns
from .views import FlowRunEndpoint


urlpatterns = [
    url(r'^/runs$', FlowRunEndpoint.as_view(), name='api.runs'),
]

urlpatterns = format_suffix_patterns(urlpatterns, allowed=['json', 'api'])
