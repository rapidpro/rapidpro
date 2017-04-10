from __future__ import absolute_import, unicode_literals

from django.conf.urls import url
from rest_framework.urlpatterns import format_suffix_patterns
from .views import AuthenticateEndpoint, OrgEndpoint, ContactEndpoint, FlowEndpoint, FlowDefinitionEndpoint
from .views import BoundaryEndpoint, FlowStepEndpoint, FieldEndpoint


urlpatterns = [
    url(r'^authenticate$', AuthenticateEndpoint.as_view(), name='api.v1.authenticate'),
    url(r'^boundaries$', BoundaryEndpoint.as_view(), name='api.v1.boundaries'),
    url(r'^contacts$', ContactEndpoint.as_view(), name='api.v1.contacts'),
    url(r'^fields$', FieldEndpoint.as_view(), name='api.v1.contactfields'),
    url(r'^flows$', FlowEndpoint.as_view(), name='api.v1.flows'),
    url(r'^flow_definition$', FlowDefinitionEndpoint.as_view(), name='api.v1.flow_definition'),
    url(r'^org$', OrgEndpoint.as_view(), name='api.v1.org'),
    url(r'^steps$', FlowStepEndpoint.as_view(), name='api.v1.steps'),
]

urlpatterns = format_suffix_patterns(urlpatterns, allowed=['json'])
