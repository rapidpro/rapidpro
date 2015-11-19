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
    url(r'^$', api, name='api'),
    url(r'^/explorer/$', ApiExplorerView.as_view(), name='api.explorer'),
    url(r'^/authenticate$', AuthenticateEndpoint.as_view(), name='api.authenticate'),
    url(r'^/broadcasts$', BroadcastEndpoint.as_view(), name='api.broadcasts'),
    url(r'^/messages$', MessageEndpoint.as_view(), name='api.messages'),
    url(r'^/message_actions$', MessageBulkActionEndpoint.as_view(), name='api.message_actions'),
    url(r'^/sms$', MessageEndpoint.as_view(), name='api.sms'),  # deprecated
    url(r'^/labels$', LabelEndpoint.as_view(), name='api.labels'),
    url(r'^/flows$', FlowEndpoint.as_view(), name='api.flows'),
    url(r'^/flow_definition$', FlowDefinitionEndpoint.as_view(), name='api.flow_definition'),
    url(r'^/results$', FlowResultsEndpoint.as_view(), name='api.results'),
    url(r'^/runs$', FlowRunEndpoint.as_view(), name='api.runs'),
    url(r'^/steps$', FlowStepEndpoint.as_view(), name='api.steps'),
    url(r'^/calls$', CallEndpoint.as_view(), name='api.calls'),
    url(r'^/contacts$', ContactEndpoint.as_view(), name='api.contacts'),
    url(r'^/contact_actions$', ContactBulkActionEndpoint.as_view(), name='api.contact_actions'),
    url(r'^/groups$', GroupEndpoint.as_view(), name='api.contactgroups'),
    url(r'^/fields$', FieldEndpoint.as_view(), name='api.contactfields'),
    url(r'^/relayers$', ChannelEndpoint.as_view(), name='api.channels'),
    url(r'^/campaigns$', CampaignEndpoint.as_view(), name='api.campaigns'),
    url(r'^/events$', CampaignEventEndpoint.as_view(), name='api.campaignevents'),
    url(r'^/boundaries$', BoundaryEndpoint.as_view(), name='api.boundaries'),
    url(r'^/org$', OrgEndpoint.as_view(), name='api.org'),
    url(r'^/assets$', AssetEndpoint.as_view(), name='api.assets')
]

urlpatterns = format_suffix_patterns(urlpatterns, allowed=['json', 'xml', 'api'])
