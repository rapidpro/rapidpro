from __future__ import absolute_import, unicode_literals

import mimetypes
import urllib2

from django.core.urlresolvers import reverse
from django.http import HttpResponse, HttpResponseNotFound, HttpResponseForbidden
from django.utils.translation import ugettext_lazy as _
from django.views.generic import View
from smartmin.views import SmartTemplateView
from .models import AssetType, AssetEntityNotFound, AssetAccessDenied, AssetFileNotFound


def get_asset_url(asset_type, identifier, direct=False):
    view_name = 'assets.stream' if direct else 'assets.download'
    return reverse(view_name, kwargs=dict(type=asset_type.name, identifier=identifier))


def handle_asset_request(user, asset_type, identifier):
    """
    Request handler shared by the asset view and the asset API endpoint
    """
    try:
        asset_org, location, filename = asset_type.store.resolve(user, identifier)
        asset_type = mimetypes.guess_type(location)[0]

        if location.startswith('http'):
            asset_file = urllib2.urlopen(location)
        else:
            asset_file = open('.' + location, 'rb')

        response = HttpResponse(asset_file, content_type=asset_type)
        response['Content-Disposition'] = 'attachment; filename=%s' % filename
        return response
    except AssetEntityNotFound:
        return HttpResponseNotFound("No such object in database")
    except AssetAccessDenied:
        return HttpResponseForbidden("Not allowed")
    except AssetFileNotFound:
        return HttpResponseNotFound("Object has no associated asset")


class AssetDownloadView(SmartTemplateView):
    """
    Provides a landing page for an asset, e.g. /assets/download/contact_export/123/
    """
    template_name = 'assets/asset_read.haml'

    def has_permission(self, request, *args, **kwargs):
        return self.request.user.is_authenticated()

    def get_context_data(self, **kwargs):
        context = super(AssetDownloadView, self).get_context_data(**kwargs)

        asset_type = AssetType[kwargs.pop('type')]
        identifier = kwargs.pop('identifier')

        try:
            asset_org, location, filename = asset_type.store.resolve(self.request.user, identifier)
        except (AssetEntityNotFound, AssetFileNotFound):
            file_error = _("File not found")
        except AssetAccessDenied:
            file_error = _("You do not have permission to access this file")
        else:
            file_error = None
            self.request.user.set_org(asset_org)

        download_url = get_asset_url(asset_type, identifier, direct=True)

        context['file_error'] = file_error
        context['download_url'] = download_url

        return context


class AssetStreamView(View):
    """
    Provides a direct download stream to an asset, e.g. /assets/stream/contact_export/123/
    """
    def get(self, request, *args, **kwargs):
        asset_type = AssetType[kwargs.pop('type')]
        identifier = kwargs.pop('identifier')

        return handle_asset_request(request.user, asset_type, identifier)