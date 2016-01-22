from __future__ import unicode_literals

from django.conf.urls import include, url
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_protect
from django.views.generic import RedirectView
from .views import WebHookEventListView, WebHookEventReadView, WebHookView, WebHookSimulatorView, WebHookTunnelView


urlpatterns = [
    url(r'^api/$', RedirectView.as_view(pattern_name='api.v1', permanent=False), name='api'),
    url(r'^api/v1', include('temba.api.v1.urls')),
    url(r'^api/v2', include('temba.api.v2.urls')),

    url(r'^webhooks', include([
        url(r'^/log/$', WebHookEventListView.as_view(), name='api.log'),
        url(r'^/log/(?P<pk>\d+)/$', WebHookEventReadView.as_view(), name='api.log_read'),
        url(r'^/webhook/$', WebHookView.as_view(), name='api.webhook'),
        url(r'^/webhook/simulator/$', WebHookSimulatorView.as_view(), name='api.webhook_simulator'),
        url(r'^/webhook/tunnel/$', login_required(csrf_protect(WebHookTunnelView.as_view())), name='api.webhook_tunnel')
    ])),
]
