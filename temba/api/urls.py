from django.conf.urls import include, url
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_protect
from django.views.generic import RedirectView

from .views import (
    RefreshAPITokenView,
    ResthookList,
    WebHookResultCRUDL,
    WebHookSimulatorView,
    WebHookTunnelView,
    WebHookView,
)

urlpatterns = [
    url(r"^api/$", RedirectView.as_view(pattern_name="api.v2", permanent=False), name="api"),
    url(r"^api/v2/", include("temba.api.v2.urls")),
    url(r"^api/apitoken/refresh/$", RefreshAPITokenView.as_view(), name="api.apitoken_refresh"),
    url(
        r"^webhooks/",
        include(
            [
                url(r"^webhook/$", WebHookView.as_view(), name="api.webhook"),
                url(r"^webhook/simulator/$", WebHookSimulatorView.as_view(), name="api.webhook_simulator"),
                url(
                    r"^webhook/tunnel/$",
                    login_required(csrf_protect(WebHookTunnelView.as_view())),
                    name="api.webhook_tunnel",
                ),
            ]
        ),
    ),
    url(r"^api/resthooks/", include([url(r"^$", ResthookList.as_view(), name="api.resthook_list")])),
]

urlpatterns += WebHookResultCRUDL().as_urlpatterns()
