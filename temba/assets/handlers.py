from __future__ import absolute_import, unicode_literals

import os

from django.core.files.storage import default_storage
from temba.contacts.models import ExportContactsTask
from temba.flows.models import ExportFlowResultsTask
from temba.msgs.models import Msg, ExportMessagesTask


class AssetException(Exception):
    pass


class AssetAccessDenied(AssetException):
    pass


class AssetEntityNotFound(AssetException):
    pass


class AssetFileNotFound(AssetException):
    pass


class BaseAssetHandler(object):
    """
    Base class for asset handlers. Assumes that identifier is primary key of a db object with an associated asset.
    """
    model = None
    directory = None
    permission = None
    content_type = None
    extension = None

    def resolve_url(self, user, identifier):
        """
        Returns the complete URL of the identified asset
        """
        asset_org = self.derive_org(identifier)

        if not has_org_permission(asset_org, user, self.permission):
            raise AssetAccessDenied()

        asset_path = self.derive_path(asset_org, identifier)

        if not default_storage.exists(asset_path):
            raise AssetFileNotFound()

        return default_storage.url(asset_path)

    def save(self, user, identifier, file):
        """
        Saves a file asset
        """
        asset_org = self.derive_org(identifier)

        if not has_org_permission(asset_org, user, self.permission):
            raise AssetAccessDenied()

        asset_path = self.derive_path(asset_org, identifier)

        default_storage.save(asset_path, file)

    def derive_org(self, identifier):
        try:
            model_instance = self.model.objects.get(pk=identifier)
        except self.model.DoesNotExist:
            return AssetEntityNotFound()

        return model_instance.org

    def derive_filename(self, identifier):
        return '%s.%s' % (identifier, self.extension)

    def derive_path(self, org, identifier):
        return os.path.join('orgs', unicode(org.pk), self.directory, self.derive_filename(identifier))


class RecordingAssetHandler(BaseAssetHandler):
    model = Msg
    directory = 'recordings'
    permission = 'msgs.msg_recording_asset'
    content_type = 'audio/wav'
    extension = 'wav'


class ExportContactsAssetHandler(BaseAssetHandler):
    model = ExportContactsTask
    directory = 'contact_exports'
    permission = 'contacts.contact_export_asset'
    content_type = 'text/csv'
    extension = 'csv'


class ExportResultsAssetHandler(BaseAssetHandler):
    model = ExportFlowResultsTask
    directory = 'results_exports'
    permission = 'flows.flow_results_export_asset'
    content_type = 'text/csv'
    extension = 'csv'


class ExportMessagesAssetHandler(BaseAssetHandler):
    model = ExportMessagesTask
    directory = 'message_exports'
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
