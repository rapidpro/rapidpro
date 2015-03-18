from __future__ import absolute_import, unicode_literals

import os

from django.conf import settings
from django.core.files.storage import default_storage
from enum import Enum
from temba.contacts.models import ExportContactsTask
from temba.flows.models import ExportFlowResultsTask
from temba.msgs.models import ExportMessagesTask


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


class BaseAssetStore(object):
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


class ContactExportAssetStore(BaseAssetStore):
    model = ExportContactsTask
    directory = 'contact_exports'
    permission = 'contacts.contact_export'
    extensions = ('xls', 'csv')


class ResultsExportAssetStore(BaseAssetStore):
    model = ExportFlowResultsTask
    directory = 'results_exports'
    permission = 'flows.flow_export_results'
    extensions = ('xls',)


class MessageExportAssetStore(BaseAssetStore):
    model = ExportMessagesTask
    directory = 'message_exports'
    permission = 'msgs.msg_export'
    extensions = ('xls',)


class AssetType(Enum):
    contact_export = (ContactExportAssetStore,)
    results_export = (ResultsExportAssetStore,)
    message_export = (MessageExportAssetStore,)

    def __init__(self, store_class):
        self.store = store_class(self)


def has_org_permission(org, user, permission):
    """
    Determines if a user has the given permission in the given org
    """
    if user.is_superuser:
        return True

    if user.is_anonymous():
        return False

    org_group = org.get_user_org_group(user)

    if not org_group:
        return False

    (app_label, codename) = permission.split(".")

    return org_group.permissions.filter(content_type__app_label=app_label, codename=codename).exists()
