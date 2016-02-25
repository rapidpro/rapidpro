from __future__ import absolute_import, unicode_literals

from django.conf.urls import url
from rest_framework.urlpatterns import format_suffix_patterns
from .views import api, ApiExplorerView, BroadcastEndpoint, ContactsEndpoint, FieldsEndpoint, GroupsEndpoint
from .views import LabelsEndpoint, MessagesEndpoint, OrgEndpoint, RunsEndpoint


urlpatterns = [
    url(r'^$', api, name='api.v2'),
    url(r'^/explorer/$', ApiExplorerView.as_view(), name='api.v2.explorer'),

    url(r'^/broadcasts$', BroadcastEndpoint.as_view(), name='api.v2.broadcasts'),
    url(r'^/contacts$', ContactsEndpoint.as_view(), name='api.v2.contacts'),
    url(r'^/fields$', FieldsEndpoint.as_view(), name='api.v2.fields'),
    url(r'^/groups$', GroupsEndpoint.as_view(), name='api.v2.groups'),
    url(r'^/labels$', LabelsEndpoint.as_view(), name='api.v2.labels'),
    url(r'^/messages$', MessagesEndpoint.as_view(), name='api.v2.messages'),
    url(r'^/org$', OrgEndpoint.as_view(), name='api.v2.org'),
    url(r'^/runs$', RunsEndpoint.as_view(), name='api.v2.runs'),
]

urlpatterns = format_suffix_patterns(urlpatterns, allowed=['json', 'api'])
