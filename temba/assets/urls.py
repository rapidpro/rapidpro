from __future__ import absolute_import, unicode_literals

from django.conf.urls import patterns, url
from .views import AssetView, ASSET_HANDLERS

urlpatterns = patterns('')

# add a URL for each asset type so each has it's own URL name, e.g. assets.recording
for type_name in ASSET_HANDLERS:
    url_regex = '^%s/(?P<identifier>\d+)/$' % type_name
    url_name = 'assets.%s' % type_name.replace('-', '_')
    urlpatterns += patterns('', url(url_regex, AssetView.as_view(type_name=type_name), name=url_name))
