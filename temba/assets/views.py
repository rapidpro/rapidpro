from __future__ import absolute_import, unicode_literals

import urllib2

from django.conf import settings
from django.http import HttpResponse, HttpResponseNotFound, HttpResponseForbidden
from django.views.generic import View
from temba.contacts.models import ExportContactsTask
from temba.flows.models import ExportFlowResultsTask
from temba.msgs.models import Msg, ExportMessagesTask


class BaseAssetView(View):
    """
    Base class for asset views. Assumes that identifier is primary key of a db object with an associated asset.
    """
    model = None
    directory = None
    permission = None
    content_type = None
    extension = None

    def get(self, request, *args, **kwargs):
        identifier = kwargs.get('identifier')
        asset_org = self.derive_org(identifier)

        if not has_org_permission(asset_org, request.user, self.permission):
            return HttpResponseForbidden("Not allowed")

        asset_filename = self.derive_filename(asset_org, identifier)
        asset_url = self.derive_url(asset_org, self.directory, identifier)

        try:
            asset_file = urllib2.urlopen(asset_url)
        except urllib2.HTTPError:
            return HttpResponseNotFound("Object has no associated asset")

        response = HttpResponse(asset_file, content_type=self.content_type)
        response['Content-Disposition'] = 'attachment; filename="%s"' % asset_filename
        return response

    def derive_org(self, identifier):
        try:
            model_instance = self.model.objects.get(pk=identifier)
        except self.model.DoesNotExist:
            return HttpResponseNotFound("No such object in database")

        return model_instance.org

    def derive_filename(self, org, identifier):
        return '%s.%s' % (identifier, self.extension)

    def derive_url(self, org, type_name, identifier):
        asset_filename = self.derive_filename(org, identifier)
        return 'http://%s/orgs/%d/%s/%s' % (settings.AWS_STORAGE_BUCKET_NAME, org.pk, type_name, asset_filename)


class RecordingAssetView(BaseAssetView):
    model = Msg
    directory = 'recordings'
    permission = 'msgs.msg_recording_asset'
    content_type = 'audio/wav'
    extension = 'wav'


class ExportContactsAssetView(BaseAssetView):
    model = ExportContactsTask
    directory = 'contact-exports'
    permission = 'contacts.contact_export_asset'
    content_type = 'text/csv'
    extension = 'csv'


class ExportFlowResultsAssetView(BaseAssetView):
    model = ExportFlowResultsTask
    directory = 'flow-exports'
    permission = 'flows.flow_results_export_asset'
    content_type = 'text/csv'
    extension = 'csv'


class ExportMessagesAssetView(BaseAssetView):
    model = ExportMessagesTask
    directory = 'message-exports'
    permission = 'msgs.msg_export_asset'
    content_type = 'text/csv'
    extension = 'csv'


def has_org_permission(org, user, permission):
    """
    Determines if a user has the given permission in the given org
    """
    org_group = org.get_user_org_group(user)
    if not org_group:
        return False

    (app_label, codename) = permission.split(".")
    return org_group.permissions.filter(content_type__app_label=app_label, codename=codename).exists()
