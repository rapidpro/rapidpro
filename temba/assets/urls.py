from __future__ import absolute_import, unicode_literals

from django.conf.urls import patterns, url
from .views import AssetDownloadView, AssetStreamView


urlpatterns = patterns('', url(r'download/(?P<type>\w+)/(?P<pk>\d+)/$', AssetDownloadView.as_view(),
                               name='assets.download'))
urlpatterns += patterns('', url(r'stream/(?P<type>\w+)/(?P<pk>\d+)/$', AssetStreamView.as_view(),
                                name='assets.stream'))
