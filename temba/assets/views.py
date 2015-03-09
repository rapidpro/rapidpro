from __future__ import absolute_import, unicode_literals

import urllib2

from django.http import HttpResponse, HttpResponseNotFound, HttpResponseForbidden
from django.views.generic import View
from .handlers import AssetEntityNotFound, AssetAccessDenied, AssetFileNotFound


def handle_asset_request(user, asset_type, identifier):
    """
    Request handler shared by the asset view and the asset API endpoint
    """
    try:
        handler = asset_type.get_handler()
        asset_filename = handler.derive_filename(identifier)
        asset_url = handler.resolve_url(user, identifier)
        asset_stream = urllib2.urlopen(asset_url)

        response = HttpResponse(asset_stream, content_type=handler.content_type)
        response['Content-Disposition'] = 'attachment; filename="%s"' % asset_filename
        return response
    except AssetEntityNotFound:
        return HttpResponseNotFound("No such object in database")
    except AssetAccessDenied:
        return HttpResponseForbidden("Not allowed")
    except AssetFileNotFound:
        return HttpResponseNotFound("Object has no associated asset")


class AssetView(View):
    """
    Provides in-app access to assets via a view for each asset type, e.g. {% url 'assets.recording' msg.pk %}
    """
    asset_type = None

    def __init__(self, **kwargs):
        self.asset_type = kwargs.pop('asset_type')
        super(AssetView, self).__init__(**kwargs)

    def get(self, request, *args, **kwargs):
        return handle_asset_request(request.user, self.asset_type, kwargs.get('identifier'))
