from __future__ import absolute_import, unicode_literals

from django.conf.urls import patterns, url
from .views import RecordingAssetView, ExportContactsAssetView, ExportFlowResultsAssetView, ExportMessagesAssetView

urlpatterns = patterns('',
                       url(r'^recording/(?P<identifier>\d+)/$', RecordingAssetView.as_view(), name='assets.recording'),
                       url(r'^contacts-export/(?P<identifier>\d+)/$', ExportContactsAssetView.as_view(), name='assets.contacts_export'),
                       url(r'^results-export/(?P<identifier>\d+)/$', ExportFlowResultsAssetView.as_view(), name='assets.results_export'),
                       url(r'^messages-export/(?P<identifier>\d+)/$', ExportMessagesAssetView.as_view(), name='assets.messages_export'))