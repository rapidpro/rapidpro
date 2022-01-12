from django.conf.urls import include
from django.urls import re_path
from django.views.generic import RedirectView

from .views import RefreshAPITokenView

urlpatterns = [
    re_path(r"^api/$", RedirectView.as_view(pattern_name="api.v2", permanent=False), name="api"),
    re_path(r"^api/v2/", include("temba.api.v2.urls")),
    re_path(r"^api/apitoken/refresh/$", RefreshAPITokenView.as_view(), name="api.apitoken_refresh"),
]
