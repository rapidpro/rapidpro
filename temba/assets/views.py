import mimetypes

from smartmin.views import SmartTemplateView, SmartView

from django.http import HttpResponse, HttpResponseForbidden, HttpResponseNotFound, HttpResponseRedirect
from django.utils.translation import ugettext_lazy as _
from django.views.generic import View

from temba.notifications.views import NotificationTargetMixin

from .models import AssetAccessDenied, AssetEntityNotFound, AssetFileNotFound, get_asset_store


def handle_asset_request(user, asset_store, pk):
    """
    Request handler shared by the asset view and the asset API endpoint
    """
    try:
        asset, location, filename = asset_store.resolve(user, pk)
        mime_type = mimetypes.guess_type(filename)[0]

        if location.startswith("http"):  # pragma: needs cover
            # return an HTTP Redirect to the source
            response = HttpResponseRedirect(location)
        else:
            asset_file = open("." + location, "rb")
            response = HttpResponse(asset_file, content_type=mime_type)
            response["Content-Disposition"] = "attachment; filename=%s" % filename

        return response
    except AssetEntityNotFound:
        return HttpResponseNotFound("No such object in database")
    except AssetAccessDenied:  # pragma: needs cover
        return HttpResponseForbidden("Not allowed")
    except AssetFileNotFound:
        return HttpResponseNotFound("Object has no associated asset")


class AssetDownloadView(NotificationTargetMixin, SmartTemplateView):
    """
    Provides a landing page for an asset, e.g. /assets/download/contact_export/123/
    """

    template_name = "assets/asset_read.haml"

    def has_permission(self, request, *args, **kwargs):
        return self.request.user.is_authenticated

    def get_notification_scope(self) -> tuple:
        try:
            scope = self.get_asset().get_notification_scope()
        except Exception:
            scope = None

        return "export:finished", scope

    @property
    def asset_store(self):
        return get_asset_store(self.kwargs["type"])

    def get_asset(self):
        asset, _, _ = self.asset_store.resolve(self.request.user, self.kwargs["pk"])
        return asset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        download_url = None

        try:
            asset = self.get_asset()
            download_url = self.asset_store.get_asset_url(asset.id, direct=True)
        except (AssetEntityNotFound, AssetFileNotFound):
            file_error = _("File not found")
        except AssetAccessDenied:  # pragma: needs cover
            file_error = _("You do not have permission to access this file")
        else:
            file_error = None
            self.request.user.set_org(asset.org)

        context["file_error"] = file_error
        context["download_url"] = download_url

        return context


class AssetStreamView(SmartView, View):
    """
    Provides a direct download stream to an asset, e.g. /assets/stream/contact_export/123/
    """

    def has_permission(self, request, *args, **kwargs):
        return self.request.user.is_authenticated

    def get(self, request, *args, **kwargs):
        asset_store = get_asset_store(kwargs.pop("type"))
        pk = kwargs.pop("pk")

        return handle_asset_request(request.user, asset_store, pk)
