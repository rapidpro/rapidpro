from django.urls import re_path

from .views import AssetDownloadView, AssetStreamView

urlpatterns = [
    re_path(r"download/(?P<type>\w+)/(?P<pk>\d+)/$", AssetDownloadView.as_view(), name="assets.download"),
    re_path(r"stream/(?P<type>\w+)/(?P<pk>\d+)/$", AssetStreamView.as_view(), name="assets.stream"),
]
