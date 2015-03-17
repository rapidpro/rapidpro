from __future__ import absolute_import, unicode_literals

import mimetypes
import urllib2

from django.core.urlresolvers import reverse
from django.http import HttpResponse, HttpResponseNotFound, HttpResponseForbidden
from django.utils.translation import ugettext_lazy as _
from django.views.generic import View
from smartmin.views import SmartTemplateView
from . import AssetType
from .handlers import AssetEntityNotFound, AssetAccessDenied, AssetFileNotFound


def handle_asset_request(user, asset_type, identifier):
    """
    Request handler shared by the asset view and the asset API endpoint
    """
    try:
        url, filename = asset_type.handler.resolve(user, identifier)
        asset_type = mimetypes.guess_type(url)[0]

        if url.startswith('http'):
            asset_file = urllib2.urlopen(url)
        else:
            asset_file = open('.' + url, 'rb')

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
    Provides a landing page for an asset, e.g. {% url 'assets.download' 'recording' msg.pk %}
    """
    template_name = 'assets/asset_read.haml'

    def has_permission(self, request, *args, **kwargs):
        return self.request.user.is_authenticated()

    def get_context_data(self, **kwargs):
        context = super(AssetDownloadView, self).get_context_data(**kwargs)

        asset_type = AssetType[kwargs.pop('type')]
        identifier = kwargs.pop('identifier')

        try:
            asset_type.handler.resolve(self.request.user, identifier)
        except (AssetEntityNotFound, AssetFileNotFound):
            file_error = _("File not found")
        except AssetAccessDenied:
            file_error = _("You do not have permission to access this file")
        else:
            file_error = None

        download_url = reverse('assets.download', kwargs=dict(type=asset_type.name, identifier=identifier))

        context['file_error'] = file_error
        context['download_url'] = download_url

        return context


class AssetStreamView(View):
    """
    Provides a direct download stream to an asset, e.g. {% url 'assets.stream' 'recording' msg.pk %}
    """
    def get(self, request, *args, **kwargs):
        asset_type = AssetType[kwargs.pop('type')]
        identifier = kwargs.pop('identifier')

        return handle_asset_request(request.user, asset_type, identifier)