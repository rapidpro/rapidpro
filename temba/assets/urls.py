from __future__ import absolute_import, unicode_literals

from django.conf.urls import url
from .views import AssetDownloadView, AssetStreamView


urlpatterns = [
    url(r'download/(?P<type>\w+)/(?P<pk>\d+)/$', AssetDownloadView.as_view(), name='assets.download'),
    url(r'stream/(?P<type>\w+)/(?P<pk>\d+)/$', AssetStreamView.as_view(), name='assets.stream'),
]
