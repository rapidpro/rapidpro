from __future__ import absolute_import, unicode_literals

from django.conf.urls import patterns, url
from . import AssetType
from .views import AssetView

urlpatterns = patterns('')

# add a URL for each asset type so each has it's own URL name, e.g. assets.recording
for asset_type in AssetType.__members__.values():
    url_regex = '^%s/(?P<identifier>\d+)/$' % asset_type.name
    url_name = 'assets.%s' % asset_type.name
    urlpatterns += patterns('', url(url_regex, AssetView.as_view(asset_type=asset_type), name=url_name))
