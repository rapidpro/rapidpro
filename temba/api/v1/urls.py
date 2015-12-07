from __future__ import absolute_import, unicode_literals

from django.conf.urls import url
from rest_framework.urlpatterns import format_suffix_patterns
from .views import api, ApiExplorerView, AuthenticateEndpoint, OrgEndpoint
from .views import BroadcastEndpoint, MessageEndpoint, MessageBulkActionEndpoint, LabelEndpoint
from .views import CallEndpoint, ContactEndpoint, ContactBulkActionEndpoint
from .views import FlowEndpoint, FlowResultsEndpoint, FlowRunEndpoint, FlowDefinitionEndpoint, FlowStepEndpoint
from .views import GroupEndpoint, FieldEndpoint
from .views import ChannelEndpoint, CampaignEndpoint, CampaignEventEndpoint, BoundaryEndpoint, AssetEndpoint


urlpatterns = [
    url(r'^$', api, name='api.v1'),
    url(r'^/explorer/$', ApiExplorerView.as_view(), name='api.v1.explorer'),
    url(r'^/authenticate$', AuthenticateEndpoint.as_view(), name='api.v1.authenticate'),
    url(r'^/broadcasts$', BroadcastEndpoint.as_view(), name='api.v1.broadcasts'),
    url(r'^/messages$', MessageEndpoint.as_view(), name='api.v1.messages'),
    url(r'^/message_actions$', MessageBulkActionEndpoint.as_view(), name='api.v1.message_actions'),
    url(r'^/sms$', MessageEndpoint.as_view(), name='api.v1.sms'),  # deprecated
    url(r'^/labels$', LabelEndpoint.as_view(), name='api.v1.labels'),
    url(r'^/flows$', FlowEndpoint.as_view(), name='api.v1.flows'),
    url(r'^/flow_definition$', FlowDefinitionEndpoint.as_view(), name='api.v1.flow_definition'),
    url(r'^/results$', FlowResultsEndpoint.as_view(), name='api.v1.results'),
    url(r'^/runs$', FlowRunEndpoint.as_view(), name='api.v1.runs'),
    url(r'^/steps$', FlowStepEndpoint.as_view(), name='api.v1.steps'),
    url(r'^/calls$', CallEndpoint.as_view(), name='api.v1.calls'),
    url(r'^/contacts$', ContactEndpoint.as_view(), name='api.v1.contacts'),
    url(r'^/contact_actions$', ContactBulkActionEndpoint.as_view(), name='api.v1.contact_actions'),
    url(r'^/groups$', GroupEndpoint.as_view(), name='api.v1.contactgroups'),
    url(r'^/fields$', FieldEndpoint.as_view(), name='api.v1.contactfields'),
    url(r'^/relayers$', ChannelEndpoint.as_view(), name='api.v1.channels'),
    url(r'^/campaigns$', CampaignEndpoint.as_view(), name='api.v1.campaigns'),
    url(r'^/events$', CampaignEventEndpoint.as_view(), name='api.v1.campaignevents'),
    url(r'^/boundaries$', BoundaryEndpoint.as_view(), name='api.v1.boundaries'),
    url(r'^/org$', OrgEndpoint.as_view(), name='api.v1.org'),
    url(r'^/assets$', AssetEndpoint.as_view(), name='api.v1.assets')
]

urlpatterns = format_suffix_patterns(urlpatterns, allowed=['json', 'xml', 'api'])
