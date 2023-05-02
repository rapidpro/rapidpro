from rest_framework.urlpatterns import format_suffix_patterns

from django.urls import re_path

from .views import NotificationsEndpoint

urlpatterns = [
    # ========== endpoints A-Z ===========
    re_path(r"^notifications$", NotificationsEndpoint.as_view(), name="api.internal.notifications"),
]

urlpatterns = format_suffix_patterns(urlpatterns, allowed=["json"])
