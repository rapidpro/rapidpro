from __future__ import absolute_import, unicode_literals

from enum import Enum
from .handlers import RecordingAssetHandler, ContactExportAssetHandler, ResultsExportAssetHandler, MessageExportAssetHandler


class AssetType(Enum):
    recording = (RecordingAssetHandler,)
    contact_export = (ContactExportAssetHandler,)
    results_export = (ResultsExportAssetHandler,)
    message_export = (MessageExportAssetHandler,)

    def __init__(self, handler_class):
        self.handler = handler_class(self)
