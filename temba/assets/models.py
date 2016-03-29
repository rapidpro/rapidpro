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
    Base class for asset handlers. Assumes that pk is primary key of a db object with an associated asset.
    """
    model = None
    directory = None
    permission = None
    extensions = None

    def __init__(self, asset_type):
        self.asset_type = asset_type

    def resolve(self, user, pk):
        """
        Returns a tuple of the org, location and download filename of the identified asset. If user does not have access
        to the asset, an exception is raised.
        """
        asset = self.derive_asset(pk)

        if not user.has_org_perm(asset.org, self.permission):
            raise AssetAccessDenied()

        if not asset.uuid:
            raise AssetFileNotFound()

        path = self.derive_path(asset.org, asset.uuid)

        if not default_storage.exists(path):
            raise AssetFileNotFound()

        # create a more friendly download filename
        remainder, extension = path.rsplit('.', 1)
        filename = '%s_%s.%s' % (self.asset_type.name, pk, extension)

        # if our storage backend is S3
        if settings.DEFAULT_FILE_STORAGE == 'storages.backends.s3boto.S3BotoStorage':
            # generate our URL manually so that we can force the download name for the user
            url = default_storage.connection.generate_url(default_storage.querystring_expire,
                                                          method='GET', bucket=default_storage.bucket.name,
                                                          key=default_storage._encode_name(path),
                                                          query_auth=default_storage.querystring_auth,
                                                          force_http=not default_storage.secure_urls,
                                                          response_headers={'response-content-disposition':
                                                                            'attachment;filename=%s' % filename})

        # otherwise, let the backend generate the URL
        else:
            url = default_storage.url(path)

        return asset.org, url, filename

    def save(self, pk, _file, extension):
        """
        Saves a file asset
        """
        if extension not in self.extensions:
            raise ValueError("Extension %s not supported by handler" % extension)

        asset = self.derive_asset(pk)

        path = self.derive_path(asset.org, asset.uuid, extension)

        default_storage.save(path, _file)

    def derive_asset(self, pk):
        """
        Derives the export given a PK
        """
        try:
            model_instance = self.model.objects.get(pk=pk)
        except self.model.DoesNotExist:
            raise AssetEntityNotFound()

        return model_instance

    def derive_path(self, org, uuid, extension=None):
        """
        Derives the storage path of an asset, e.g. 'orgs/1/recordings/asdf-asdf-asdf-asdf-asdf-asdf.wav'
        """
        base_name = unicode(uuid)
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
