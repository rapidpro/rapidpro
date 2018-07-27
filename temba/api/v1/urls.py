# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.conf.urls import url
from django.core.urlresolvers import reverse
from django.http import HttpResponseGone
from django.views.generic.base import RedirectView
from rest_framework.urlpatterns import format_suffix_patterns
from .views import AuthenticateEndpoint, OrgEndpoint, ContactEndpoint, FlowEndpoint, FlowDefinitionEndpoint
from .views import BoundaryEndpoint, FlowStepEndpoint, FieldEndpoint, RootView


def v1_gone(request, *args, **kwargs):
    v2_root = request.build_absolute_uri(reverse('api.v2'))
    return HttpResponseGone(content="API v1 no longer exists. Please migrate to API v2. See %s." % v2_root)


urlpatterns = [
    # this HTML view redirects to its v2 equivalent
    url(r'^explorer/$', RedirectView.as_view(pattern_name='api.v2.explorer', permanent=True)),

    # these endpoints are retained for Android Surveyor clients
    url(r'^$', RootView.as_view()),
    url(r'^authenticate$', AuthenticateEndpoint.as_view(), name='api.v1.authenticate'),
    url(r'^boundaries$', BoundaryEndpoint.as_view(), name='api.v1.boundaries'),
    url(r'^contacts$', ContactEndpoint.as_view(), name='api.v1.contacts'),
    url(r'^fields$', FieldEndpoint.as_view(), name='api.v1.contactfields'),
    url(r'^flows$', FlowEndpoint.as_view(), name='api.v1.flows'),
    url(r'^flow_definition$', FlowDefinitionEndpoint.as_view(), name='api.v1.flow_definition'),
    url(r'^org$', OrgEndpoint.as_view(), name='api.v1.org'),
    url(r'^steps$', FlowStepEndpoint.as_view(), name='api.v1.steps'),

    # these endpoints return 410 (Gone) with an error message
    url(r'^broadcasts$', v1_gone),
    url(r'^messages$', v1_gone),
    url(r'^message_actions$', v1_gone),
    url(r'^sms$', v1_gone),
    url(r'^labels$', v1_gone),
    url(r'^runs$', v1_gone),
    url(r'^calls$', v1_gone),
    url(r'^contact_actions$', v1_gone),
    url(r'^groups$', v1_gone),
    url(r'^relayers$', v1_gone),
    url(r'^campaigns$', v1_gone),
    url(r'^events$', v1_gone),
    url(r'^assets$', v1_gone)
]

urlpatterns = format_suffix_patterns(urlpatterns, allowed=['json'])
