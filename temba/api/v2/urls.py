# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.conf.urls import url
from rest_framework.urlpatterns import format_suffix_patterns
from .views import RootView, ExplorerView, AuthenticateView, BroadcastsEndpoint, ChannelsEndpoint, ChannelEventsEndpoint
from .views import CampaignsEndpoint, CampaignEventsEndpoint, ContactsEndpoint, DefinitionsEndpoint, FlowsEndpoint
from .views import FieldsEndpoint, FlowStartsEndpoint, GroupsEndpoint, LabelsEndpoint, MediaEndpoint, MessagesEndpoint
from .views import OrgEndpoint, ResthooksEndpoint, ResthookEventsEndpoint, ResthookSubscribersEndpoint, RunsEndpoint
from .views import BoundariesEndpoint, ContactActionsEndpoint, MessageActionsEndpoint


urlpatterns = [
    url(r'^$', RootView.as_view(), name='api.v2'),
    url(r'^explorer/$', ExplorerView.as_view(), name='api.v2.explorer'),
    url(r'^authenticate$', AuthenticateView.as_view(), name='api.v2.authenticate'),

    # ========== endpoints A-Z ===========
    url(r'^boundaries$', BoundariesEndpoint.as_view(), name='api.v2.boundaries'),
    url(r'^broadcasts$', BroadcastsEndpoint.as_view(), name='api.v2.broadcasts'),
    url(r'^campaigns$', CampaignsEndpoint.as_view(), name='api.v2.campaigns'),
    url(r'^campaign_events$', CampaignEventsEndpoint.as_view(), name='api.v2.campaign_events'),
    url(r'^channels$', ChannelsEndpoint.as_view(), name='api.v2.channels'),
    url(r'^channel_events$', ChannelEventsEndpoint.as_view(), name='api.v2.channel_events'),
    url(r'^contacts$', ContactsEndpoint.as_view(), name='api.v2.contacts'),
    url(r'^contact_actions$', ContactActionsEndpoint.as_view(), name='api.v2.contact_actions'),
    url(r'^definitions$', DefinitionsEndpoint.as_view(), name='api.v2.definitions'),
    url(r'^fields$', FieldsEndpoint.as_view(), name='api.v2.fields'),
    url(r'^flow_starts$', FlowStartsEndpoint.as_view(), name='api.v2.flow_starts'),
    url(r'^flows$', FlowsEndpoint.as_view(), name='api.v2.flows'),
    url(r'^groups$', GroupsEndpoint.as_view(), name='api.v2.groups'),
    url(r'^labels$', LabelsEndpoint.as_view(), name='api.v2.labels'),
    url(r'^media$', MediaEndpoint.as_view(), name='api.v2.media'),
    url(r'^messages$', MessagesEndpoint.as_view(), name='api.v2.messages'),
    url(r'^message_actions$', MessageActionsEndpoint.as_view(), name='api.v2.message_actions'),
    url(r'^org$', OrgEndpoint.as_view(), name='api.v2.org'),
    url(r'^resthooks$', ResthooksEndpoint.as_view(), name='api.v2.resthooks'),
    url(r'^resthook_events$', ResthookEventsEndpoint.as_view(), name='api.v2.resthook_events'),
    url(r'^resthook_subscribers$', ResthookSubscribersEndpoint.as_view(), name='api.v2.resthook_subscribers'),
    url(r'^runs$', RunsEndpoint.as_view(), name='api.v2.runs'),
]

urlpatterns = format_suffix_patterns(urlpatterns, allowed=['json', 'api'])
