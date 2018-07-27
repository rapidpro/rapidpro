# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import os
import six

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.urlresolvers import reverse


ASSET_STORES_BY_KEY = {}
ASSET_STORES_BY_MODEL = {}


def register_asset_store(store_class):
    store = store_class()
    ASSET_STORES_BY_KEY[store.key] = store
    ASSET_STORES_BY_MODEL[store.model] = store


def get_asset_store(key=None, model=None):
    return ASSET_STORES_BY_KEY.get(key) if key else ASSET_STORES_BY_MODEL.get(model)


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
    key = None
    directory = None
    permission = None
    extensions = None

    def resolve(self, user, pk):
        """
        Returns a tuple of the org, location and download filename of the identified asset. If user does not have access
        to the asset, an exception is raised.
        """
        asset = self.derive_asset(pk)

        if not user.has_org_perm(asset.org, self.permission):  # pragma: needs cover
            raise AssetAccessDenied()

        if not self.is_asset_ready(asset):
            raise AssetFileNotFound()

        path = self.derive_path(asset.org, asset.uuid)

        if not default_storage.exists(path):  # pragma: needs cover
            raise AssetFileNotFound()

        # create a more friendly download filename
        remainder, extension = path.rsplit('.', 1)
        filename = '%s_%s.%s' % (self.key, pk, extension)

        # if our storage backend is S3
        if settings.DEFAULT_FILE_STORAGE == 'storages.backends.s3boto.S3BotoStorage':  # pragma: needs cover
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
        if extension not in self.extensions:  # pragma: needs cover
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
        base_name = six.text_type(uuid)
        directory = os.path.join(settings.STORAGE_ROOT_DIR, six.text_type(org.pk), self.directory)

        if extension:
            return '%s/%s.%s' % (directory, base_name, extension)

        # no explicit extension so look for one with an existing file
        for ext in extension or self.extensions:
            path = '%s/%s.%s' % (directory, base_name, ext)
            if default_storage.exists(path):
                return path

        raise AssetFileNotFound()  # pragma: needs cover

    def is_asset_ready(self, asset):  # pragma: no cover
        return True

    def get_asset_url(self, pk, direct=False):
        view_name = 'assets.stream' if direct else 'assets.download'
        return reverse(view_name, kwargs=dict(type=self.key, pk=pk))
