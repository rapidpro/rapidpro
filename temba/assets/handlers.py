from __future__ import absolute_import, unicode_literals

import os

from django.conf import settings
from django.core.files.storage import default_storage
from temba.contacts.models import ExportContactsTask
from temba.flows.models import ExportFlowResultsTask
from temba.msgs.models import Msg, ExportMessagesTask


class AssetException(Exception):
    pass


class AssetAccessDenied(AssetException):
    """
    User does not have permission to access the given asset
    """
    pass


class AssetEntityNotFound(AssetException):
    """
    Database entity associated with the asset could not be found
    """
    pass


class AssetFileNotFound(AssetException):
    """
    Asset file could not be found
    """
    pass


class BaseAssetHandler(object):
    """
    Base class for asset handlers. Assumes that identifier is primary key of a db object with an associated asset.
    """
    model = None
    directory = None
    permission = None
    extensions = None

    def __init__(self, asset_type):
        self.asset_type = asset_type

    def resolve(self, user, identifier):
        """
        Returns a tuple of the org, location and download filename of the identified asset. If user does not have access
        to the asset, an exception is raised.
        """
        asset_org = self.derive_org(identifier)

        if not has_org_permission(asset_org, user, self.permission):
            raise AssetAccessDenied()

        path = self.derive_path(asset_org, identifier)

        if not default_storage.exists(path):
            raise AssetFileNotFound()

        # create a more friendly download filename
        remainder, extension = path.rsplit('.', 1)
        filename = '%s.%s' % (self.asset_type.name, extension)

        return asset_org, default_storage.url(path), filename

    def save(self, identifier, _file, extension):
        """
        Saves a file asset
        """
        if extension not in self.extensions:
            raise ValueError("Extension %s not supported by handler" % extension)

        asset_org = self.derive_org(identifier)

        path = self.derive_path(asset_org, identifier, extension)

        default_storage.save(path, _file)

    def derive_org(self, identifier):
        """
        Derives the owning org of an asset
        """
        try:
            model_instance = self.model.objects.get(pk=identifier)
        except self.model.DoesNotExist:
            raise AssetEntityNotFound()

        return model_instance.org

    def derive_path(self, org, identifier, extension=None):
        """
        Derives the storage path of an asset, e.g. 'orgs/1/recordings/123.wav'
        """
        base_name = unicode(identifier)
        directory = os.path.join(settings.STORAGE_ROOT_DIR, unicode(org.pk), self.directory)

        if extension:
            return '%s/%s.%s' % (directory, base_name, extension)

        # no explicit extension so look for one with an existing file
        for ext in extension or self.extensions:
            path = '%s/%s.%s' % (directory, base_name, ext)
            if default_storage.exists(path):
                return path

        raise AssetFileNotFound()


class RecordingAssetHandler(BaseAssetHandler):
    model = Msg
    directory = 'recordings'
    permission = 'msgs.msg_recording_asset'
    extensions = ('wav',)


class ContactExportAssetHandler(BaseAssetHandler):
    model = ExportContactsTask
    directory = 'contact_exports'
    permission = 'contacts.contact_export_asset'
    extensions = ('xls', 'csv')


class ResultsExportAssetHandler(BaseAssetHandler):
    model = ExportFlowResultsTask
    directory = 'results_exports'
    permission = 'flows.flow_results_export_asset'
    extensions = ('xls',)


class MessageExportAssetHandler(BaseAssetHandler):
    model = ExportMessagesTask
    directory = 'message_exports'
    permission = 'msgs.msg_export_asset'
    extensions = ('xls',)


def has_org_permission(org, user, permission):
    """
    Determines if a user has the given permission in the given org
    """
    org_group = org.get_user_org_group(user)
    if not org_group:
        return False

    (app_label, codename) = permission.split(".")
    return org_group.permissions.filter(content_type__app_label=app_label, codename=codename).exists()
