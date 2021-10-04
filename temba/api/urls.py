from django.conf.urls import include, url
from django.views.generic import RedirectView

from .views import RefreshAPITokenView

urlpatterns = [
    url(r"^api/$", RedirectView.as_view(pattern_name="api.v2", permanent=False), name="api"),
    url(r"^api/v2/", include("temba.api.v2.urls")),
    url(r"^api/apitoken/refresh/$", RefreshAPITokenView.as_view(), name="api.apitoken_refresh"),
]
