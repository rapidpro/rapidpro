from django.conf.urls import include
from django.urls import re_path
from django.views.generic import RedirectView

from .views import APITokenCRUDL

urlpatterns = APITokenCRUDL().as_urlpatterns()
urlpatterns += [
    re_path(r"^api/$", RedirectView.as_view(pattern_name="api.v2.root", permanent=False), name="api"),
    re_path(r"^api/internal/", include("temba.api.internal.urls")),
    re_path(r"^api/v2/", include("temba.api.v2.urls")),
]
